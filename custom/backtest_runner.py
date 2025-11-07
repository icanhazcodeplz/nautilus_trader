#!/usr/bin/env python3
import random

import pandas as pd

from custom import BACKTEST_SYMBOL
from custom.catalog_options import CATALOG_OPTIONS
from custom.utils.load_catalog_data import VENUE, get_catalog_data
from custom.nt_extensions.limit_fill_model import LimitFillModel
from custom.utils.orders_to_trades import orders_to_trades
from custom.strategies.momo import MomoStrategyConfig, MomoStrategy
from custom.app_utils.viz import CreateMarkers
from custom.strategies.random import RandomConfig, Random
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from custom.statistics.trade_avg import AvgTrade
from custom.statistics.trade_avg_scaled import PnlPer100, TotalBought
from custom.statistics.trade_counts import Winners, Losers, NumTrades
from custom.statistics.win_loss_ratio import WinLossRatio
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LoggingConfig
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
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.enums import AccountType, BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

measured_latency_model = LatencyModel(
    base_latency_nanos=30 * 1e6,
    insert_latency_nanos=34 * 1e6,
    update_latency_nanos=25 * 1e6,
    cancel_latency_nanos=25 * 1e6,
)


def run_single_backtest(
    dataset_name, strategy_name, params, save_artifacts=False, return_engine=False, log_level="ERROR"
):
    params_copy = params.copy()
    random_seed = params_copy.pop("random_seed", None)
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("M-1"),
            logging=LoggingConfig(
                log_level=log_level,
                log_level_file=None,
                log_directory="logs",
                log_file_name=f"{log_level}.log",
                log_component_levels=dict(
                    RiskEngine="WARNING",
                    Portfolio="WARNING",
                ),
                use_pyo3=False,
            ),
            cache=CacheConfig(tick_capacity=10_000, bar_capacity=1000),
        )
    )

    engine.add_venue(
        venue=Venue(VENUE),
        # book_type=BookType.L3_MBO,
        book_type=BookType.L1_MBP,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(100000.0, USD)],
        fill_model=LimitFillModel(prob_fill_on_limit=0.8, random_seed=random_seed),
        reject_stop_orders=False,
        # trade_execution=True,
        # latency_model=LatencyModel(),
        latency_model=measured_latency_model,
    )

    dataset_params = CATALOG_OPTIONS[dataset_name.lower()]
    symbol = dataset_params["symbol"]

    test_instrument = TestInstrumentProvider.equity(symbol=symbol, venue=VENUE)
    engine.add_instrument(test_instrument)

    engine.add_data(get_catalog_data(symbol, dataset_params["start"], dataset_params["end"], data_cls=TradeTick))
    engine.add_data(get_catalog_data(symbol, dataset_params["start"], dataset_params["end"], data_cls=QuoteTick))

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

    for stat_class in [NumTrades, Winners, Losers, WinLossRatio, AvgTrade, TotalBought]:  # Scratches
        engine.portfolio.analyzer.register_statistic(stat_class())

    engine.portfolio.analyzer.register_statistic(avg_trade_scaled)

    if strategy_name == "random":
        config = RandomConfig(instrument_id=test_instrument.id, **params_copy)
        strategy = Random(config=config)
    elif strategy_name == "momo":
        config = MomoStrategyConfig(instrument_id=test_instrument.id, **params_copy)
        strategy = MomoStrategy(config=config)

    strategy.save_artifacts = save_artifacts

    engine.add_strategy(strategy=strategy)
    random.seed(random_seed)
    engine.run()
    signals = strategy.buy_sell_signals
    num_buy_sells = len(signals)
    if num_buy_sells > 0:
        signals_df = pd.DataFrame(signals)
        signals_df["duration_ms"] = (signals_df["win_time"] - signals_df["time"]) / 1e6
        signals_df["delay_duration_ms"] = (signals_df["win_delay_time"] - signals_df["time"]) / 1e6
        wins = len(signals_df[signals_df["win"]])
        long_wins = len(signals_df[signals_df["win"] & signals_df["win_delay"]])
        print(f"\n BuySignals:  {num_buy_sells}   {wins}/{num_buy_sells - wins} = {round(wins / num_buy_sells, 2)}")
        print(
            f"LongWins__:  {num_buy_sells}   {long_wins}/{num_buy_sells - long_wins} = {round(long_wins / num_buy_sells, 2)}"
        )

    if return_engine:
        return engine
    performance_stats = {
        **engine.portfolio.analyzer.get_performance_stats_pnls(),
        **engine.portfolio.analyzer.get_performance_stats_returns(),
        **engine.portfolio.analyzer.get_performance_stats_general(),
    }
    return performance_stats


def run_multiple_backtests(dataset_names, strategy_name, params, log_level="ERROR"):
    performance_stats = []
    for dataset_name in dataset_names:
        p_stats = run_single_backtest(
            dataset_name, strategy_name, params, save_artifacts=False, return_engine=False, log_level=log_level
        )
        performance_stats.append({"name": dataset_name, **p_stats})
    stats_df = pd.DataFrame(performance_stats).round(3)
    return stats_df


if __name__ == "__main__":
    log_level = "INFO"
    # log_level = "DEBUG"
    # log_level = "ERROR"

    strategy_name = "momo"

    params = dict(
        trade_size=5,
        max_position_multiplier=1,
        stop_loss=0.10,
        take_profit=0.10,
        take_ratio=0.8,
        vwap_window=100,
        variance_window_ratio=1.5,
        upper_lower_scaler=0.10,
        use_bracket_orders=True,
        trailing_stop=False,
        simple_take=True,
        allow_trades=True,
        random_seed=4,
    )
    datasets = ["zooz"]
    run_BACKTEST_SYMBOL = True

    all_stats = []
    if not run_BACKTEST_SYMBOL:
        for random_seed in [1]:
            params["random_seed"] = random_seed
            stats = run_multiple_backtests(datasets, strategy_name, params, log_level=log_level)
            all_stats.append(stats)
        stats = pd.concat(all_stats)
        with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
            print(stats)
            pass
    else:
        engine = run_single_backtest(
            BACKTEST_SYMBOL, strategy_name, params, save_artifacts=True, return_engine=True, log_level=log_level
        )
        order_fills_report = engine.trader.generate_order_fills_report()
        orders_report = engine.trader.generate_orders_report()
        fills_report = engine.trader.generate_fills_report()
        positions_report = engine.trader.generate_positions_report()

        trades = pd.DataFrame()
        sell_legs = []
        if not positions_report.empty:
            positions = positions_report[
                ["peak_qty", "ts_opened", "ts_closed", "avg_px_open", "avg_px_close", "realized_pnl"]
            ]

            orders_report = orders_report[orders_report["filled_qty"].astype(int) > 0]
            orders = orders_report[
                ["side", "quantity", "filled_qty", "price", "trigger_price", "avg_px", "tags", "ts_init", "ts_last"]
            ].copy()
            t = orders.copy()
            t["ts_init"] = pd.to_datetime(orders["ts_init"], unit="ns")
            t["ts_last"] = pd.to_datetime(orders["ts_last"], unit="ns")

            trades, sell_legs = orders_to_trades(orders_report)
        CreateMarkers().create_and_save_markers(trades, sell_legs)

        print()
        # engine.reset()
        # engine.dispose()
