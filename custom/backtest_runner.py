#!/usr/bin/env python3
import glob
import os
import random
import shutil

import pandas as pd

from custom.backtest_utils.load_catalog_data import load_catalog_data_to_engine, CATALOG_TIME_STR_FMT
from custom.backtest_utils.prepare_top_gainers import parse_candidate_str, get_allow_buy_times_for_candidate
from custom.nt_extensions.limit_fill_model import LimitFillModel
from custom.strategies.momo import MomoStrategyConfig, MomoStrategy
from custom.artifacts import ArtifactsIO, BACKTEST_RUNS_PATH
from custom.strategies.random import RandomConfig, Random
from nautilus_trader.adapters.alpaca.utils import ns_to_iso_8601
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from custom.statistics.trade_avg import AvgTrade
from custom.statistics.trade_avg_scaled import PnlPer100, TotalBought, AverageBuyPrice
from custom.statistics.trade_counts import Winners, Losers, NumTrades
from custom.statistics.win_loss_ratio import WinLossRatio
from custom.utils.orders_to_trades import orders_to_trades
from custom.utils.run_utils import run_strategy
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.events import (
    OrderFilled,
    OrderAccepted,
    OrderInitialized,
    OrderCanceled,
    OrderExpired,
    OrderUpdated,
)
from nautilus_trader.persistence.config import StreamingConfig
from nautilus_trader.core.nautilus_pyo3 import (
    Expectancy,
    LongRatio,
    MinLoser,
    ProfitFactor,
    ReturnsAverage,
    ReturnsAverageLoss,
    ReturnsAverageWin,
    ReturnsVolatility,
    RiskReturnRatio,
    SharpeRatio,
    SortinoRatio,
    MinWinner,
)
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

latency_model = LatencyModel(
    base_latency_nanos=30 * 1e6,
    insert_latency_nanos=34 * 1e6,
    update_latency_nanos=25 * 1e6,
    cancel_latency_nanos=25 * 1e6,
)
# latency_model=LatencyModel()
prob_fill_on_limit = 0.5
DATA_VENUE = ALPACA


def run_single_backtest(
    symbol,
    start_str,
    end_str,
    strategy_name,
    params,
    artifacts_location=None,
    allow_buy_times=None,
    log_level="ERROR",
    analyze=False,
):
    start_time = pd.Timestamp.now()
    params_copy = params.copy()
    random_seed = params_copy.pop("random_seed", None)
    random.seed(random_seed)

    streaming = None
    if artifacts_location is not None:
        streaming = StreamingConfig(
            catalog_path=str(artifacts_location),
            include_types=[OrderInitialized, OrderFilled, OrderAccepted, OrderCanceled, OrderUpdated, OrderExpired],
            replace_existing=True,
        )
        for log_file in glob.glob(os.path.join(str(artifacts_location), "*.log")):
            os.remove(log_file)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("M-1"),
            logging=LoggingConfig(
                log_level=log_level,
                log_level_file="DEBUG" if artifacts_location is not None else None,
                log_directory=str(artifacts_location) if artifacts_location is not None else "logs",
                log_file_name="DEBUG",
                log_file_max_size=int(10e6),
                log_file_max_backup_count=50,
                log_component_levels=dict(
                    RiskEngine="WARNING",
                    Portfolio="WARNING",
                ),
                use_pyo3=False,
            ),
            cache=CacheConfig(tick_capacity=1000, bar_capacity=1000),
            streaming=streaming,
        )
    )

    engine.add_venue(
        venue=Venue(DATA_VENUE),
        # book_type=BookType.L3_MBO,
        book_type=BookType.L1_MBP,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(100000.0, USD)],
        fill_model=LimitFillModel(prob_fill_on_limit=prob_fill_on_limit, random_seed=random_seed),
        reject_stop_orders=False,
        # trade_execution=True,
        latency_model=latency_model,
    )

    test_instrument, engine = load_catalog_data_to_engine(engine, symbol, start_str, end_str, data_venue="ALPACA")

    avg_trade_scaled = PnlPer100()

    for stat_class in [
        ReturnsVolatility,
        SharpeRatio,
        SortinoRatio,
        LongRatio,
        ProfitFactor,
        RiskReturnRatio,
        ReturnsAverage,
        ReturnsAverageLoss,
        ReturnsAverageWin,
        MinWinner,
        MinLoser,
        Expectancy,
    ]:
        engine.portfolio.analyzer.deregister_statistic(stat_class())

    for stat_class in [NumTrades, Winners, Losers, WinLossRatio, AvgTrade, TotalBought, AverageBuyPrice]:
        engine.portfolio.analyzer.register_statistic(stat_class())

    engine.portfolio.analyzer.register_statistic(avg_trade_scaled)

    if strategy_name == "random":
        config = RandomConfig(instrument_id=test_instrument.id, **params_copy)
        strategy = Random(config=config)
    elif strategy_name == "momo":
        config = MomoStrategyConfig(instrument_id=test_instrument.id, **params_copy)
        strategy = MomoStrategy(config=config)

    if allow_buy_times is not None:
        strategy.allow_buy_times = allow_buy_times
        strategy.set_allow_buys(False)
    strategy.internal_bars = True

    performance_stats = run_strategy(strategy, engine, artifacts_location, run_config=config.dict())

    if artifacts_location is not None:
        # --- SAVE ORDER FILLS TO PKL ---
        from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

        catalog = ParquetDataCatalog(str(artifacts_location))
        relevant_order_updates = catalog.read_backtest(instance_id=str(engine.kernel.instance_id))
        # Deduplicate: OrderFilled events are published twice in engine.pyx
        # (once from _handle_order_fill, once from _handle_event)
        relevant_order_updates = list(set(f for f in relevant_order_updates))
        relevant_order_updates.sort(key=lambda e: e.ts_event)
        artifacts_io = ArtifactsIO(BACKTEST_RUNS_PATH)
        artifacts_io.save_backtest_order_updates_to_pkl(relevant_order_updates)
        shutil.rmtree(BACKTEST_RUNS_PATH / "backtest", ignore_errors=True)

    if artifacts_location is not None and analyze:
        analyze_backtest()
        print(f"\nTotal Runtime {pd.Timestamp.now() - start_time}")

    engine.dispose()
    return performance_stats


def run_single_backtest_from_top_gainers_candidate(
    candidate_str, strategy_name, params, artifacts_location=None, log_level="ERROR", analyze=False
):
    rank_max = params.pop("rank_max")
    vol_30min_min = params.pop("vol_30min_min")
    perc_gain_min = params.pop("perc_gain_min")
    price_min = params.pop("price_min")
    price_max = params.pop("price_max")

    symbol, day_str = parse_candidate_str(candidate_str)
    allow_buy_times = get_allow_buy_times_for_candidate(
        symbol, day_str, rank_max, vol_30min_min, perc_gain_min, price_min, price_max
    )

    start = allow_buy_times[0] - pd.Timedelta(minutes=30)
    end = allow_buy_times[-1] + pd.Timedelta(minutes=20)

    # start = allow_buy_times[0] + pd.Timedelta(minutes=2)
    # end = allow_buy_times[0] + pd.Timedelta(minutes=5)
    start_str = pd.Timestamp.strftime(start, CATALOG_TIME_STR_FMT)
    end_str = pd.Timestamp.strftime(end, CATALOG_TIME_STR_FMT)

    return run_single_backtest(
        symbol,
        start_str,
        end_str,
        strategy_name,
        params,
        allow_buy_times=allow_buy_times,
        artifacts_location=artifacts_location,
        log_level=log_level,
        analyze=analyze,
    )


def run_multiple_backtests(dataset_names, strategy_name, params, log_level="ERROR"):
    performance_stats = []
    for dataset_name in dataset_names:
        p_stats = run_single_backtest_from_top_gainers_candidate(
            dataset_name, strategy_name, params, artifacts_location=None, log_level=log_level
        )
        performance_stats.append({"name": dataset_name, **p_stats})
    stats_df = pd.DataFrame(performance_stats).round(3)
    return stats_df


def analyze_trades(trades, print_report=False):
    if len(trades) == 0:
        return 0
    trades = trades.copy()
    trades = trades[trades["avg_sell_price"] > 0]
    wins = len(trades[trades["pnl"] > 0])
    losses = len(trades[trades["pnl"] < 0])
    scratches = len(trades[trades["pnl"] == 0])
    trade_count = len(trades)
    win_ratio = round(wins / trade_count, 3)
    if print_report:
        print(f"\nTrades:  {trade_count}   {wins}|{losses}|{scratches} = {win_ratio}")
        print(f"Total PnL: ${round(trades['pnl'].sum(), 2)} | Per trade: ${round(trades['pnl'].mean(), 3)}\n")
    return win_ratio


def replay_live_run(live_run_artifacts_dir, log_level="ERROR"):
    """Run a backtest with the same parameters as a live run."""
    artifacts_io = ArtifactsIO(live_run_artifacts_dir)
    config = artifacts_io.load_config()

    # Derive dataset name from run date and symbol
    ticks = artifacts_io.load_ticks_and_metrics_file()
    start_str = ns_to_iso_8601(min(ticks.keys()))
    end_str = ns_to_iso_8601(max(ticks.keys()))

    symbol = config.pop("instrument_id").split(".")[0]

    return run_single_backtest(symbol, start_str, end_str, "momo", config, log_level=log_level, analyze=True)


def analyze_backtest():
    artifacts_io = ArtifactsIO(BACKTEST_RUNS_PATH)

    p_mets = artifacts_io.load_performance_metrics()
    print("\n".join(f"{k}: {round(v, 2)}" for k, v in p_mets.items()))
    print()

    orders_report = artifacts_io.load_orders_report()
    trades, sell_legs = orders_to_trades(orders_report)
    analyze_trades(trades, print_report=True)


if __name__ == "__main__":
    # live_run_artifacts_dir = data_subdir("runs", "20260313_144138")
    # replay_live_run(live_run_artifacts_dir)

    log_level = "INFO"
    # log_level = "DEBUG"
    log_level = "ERROR"
    # log_level = "WARNING"

    strategy_name = "momo"
    lstm_buy = False
    params = dict(
        allow_trades=True,
        max_position_multiplier=1,
        trade_size=10,
        stop_loss=0.2,
        take_profit=None,
        upper_scalar_multiplier=0.5,
        lower_scalar_multiplier=1.5,
        vwap_window=150,
        variance_window=300,
        outer_band_multiplier=2.5,
        pressure_window=25,
        simple_take=True,
        only_buy_if_macd_positive=False,
        trailing_take=False,
        num_sell_tiers=3,
        trailing_buy_order=False,
        random_buy=not lstm_buy,
        lstm_buy=lstm_buy,
        random_seed=1,
        # --- TOP GAINERS PARAMS ----------------
        # price_min=0.8,
        # price_max=20.0,
        # vol_30min_min=100_000,
        # perc_gain_min=30,
        # rank_max=5,
    )

    # candidate_str = "2026-03-19_LNKS"
    #
    # run_single_backtest_from_top_gainers_candidate(
    #     candidate_str, strategy_name, params, artifacts_location=BACKTEST_RUNS_PATH, log_level="ERROR", analyze=True
    # )

    symbol = 'AAPL'
    start_str = "2026-02-03 08:30-04:00"
    # end_str = "2026-04-13 10:30-04:00"
    end_str = "2026-02-03 16:00-04:00"
    run_single_backtest(
        symbol,
        start_str,
        end_str,
        strategy_name,
        params,
        allow_buy_times=None,
        artifacts_location=BACKTEST_RUNS_PATH,
        log_level=log_level,
        analyze=True,
    )


    # datasets = ["0129_vivssm"]
    # all_stats = []
    # if len(datasets) > 1:
    #     for random_seed in [1]:
    #         params["random_seed"] = random_seed
    #         stats = run_multiple_backtests(datasets, strategy_name, params, log_level=log_level)
    #         all_stats.append(stats)
    #     stats = pd.concat(all_stats)
    #     with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
    #         print(stats)
    #         pass
    # else:
    #     symbol, start_str, end_str = extract_dataset_name_info(datasets[0])
    #     run_backtest_and_analyze(symbol, start_str, end_str, strategy_name, params, log_level=log_level)
