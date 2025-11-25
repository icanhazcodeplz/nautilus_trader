#!/usr/bin/env python3
import random

import pandas as pd

from custom.backtest_utils import BACKTEST_SYMBOL
from custom.catalog_options import CATALOG_OPTIONS
from custom.backtest_utils.load_catalog_data import get_catalog_data
from custom.nt_extensions.limit_fill_model import LimitFillModel
from custom.strategies.momo import MomoStrategyConfig, MomoStrategy
from custom.artifacts import ArtifactsIO, VIZ_ARTIFACTS_PATH
from custom.strategies.random import RandomConfig, Random
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from custom.statistics.trade_avg import AvgTrade
from custom.statistics.trade_avg_scaled import PnlPer100, TotalBought
from custom.statistics.trade_counts import Winners, Losers, NumTrades
from custom.statistics.win_loss_ratio import WinLossRatio
from custom.utils.orders_to_trades import orders_to_trades
from custom.utils.run_utils import run_strategy
from nautilus_trader.adapters.alpaca import ALPACA
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

latency_model = LatencyModel(
    base_latency_nanos=30 * 1e6,
    insert_latency_nanos=34 * 1e6,
    update_latency_nanos=25 * 1e6,
    cancel_latency_nanos=25 * 1e6,
)
# latency_model=LatencyModel()

# DATA_VENUE = DATABENTO
DATA_VENUE = ALPACA


def _calculate_oco_win_ratio_DEPRECATED(orders_report):
    """Calculate win/loss ratio based on each buy order."""
    if "trigger_price" in orders_report:
        cols = ["side", "quantity", "filled_qty", "price", "trigger_price", "avg_px", "tags", "ts_init", "ts_last"]
    else:
        cols = ["side", "quantity", "filled_qty", "price", "avg_px", "tags", "ts_init", "ts_last"]

    # FOR DEBUGGING
    # for col in ["ts_init", "ts_last"]:
    #     orders_report[col] = orders_report[col].apply(lambda x: pd.Timestamp(x))

    orders = orders_report[cols].copy()
    # Calculate win/loss ratio based on each buy order, instead of each trade.
    orders["order_num"] = orders["tags"].apply(lambda t: t[0])

    orders["position_change"] = orders["filled_qty"].astype(int)
    orders.loc[orders["side"] == "SELL", "position_change"] = -orders.loc[orders["side"] == "SELL", "position_change"]
    orders["position_cumsum"] = orders["position_change"].cumsum()

    def pnl(group):
        group["trade_value"] = group["price"] * group["filled_qty"]
        group_buy_value = group[group["side"] == "BUY"]["trade_value"].sum()
        group_sell_value = group[group["side"] == "SELL"]["trade_value"].sum()
        return group_sell_value - group_buy_value

    orders["price"] = orders["price"].astype(float)
    orders["filled_qty"] = orders["filled_qty"].astype(int)
    pnl_for_each_buy = orders.groupby("order_num")[["side", "filled_qty", "price"]].apply(pnl)
    trade_count = len(pnl_for_each_buy)
    if trade_count == 0:
        return 0, 0
    wins = len(pnl_for_each_buy[pnl_for_each_buy > 0])
    losses = len(pnl_for_each_buy[pnl_for_each_buy < 0])

    win_ratio = round(wins / trade_count, 3)

    print(f"{trade_count} buys. W/L {wins}|{losses} = {win_ratio}\n")

    return wins, trade_count


def buy_signal_stats(signals):
    num_buy_sells = len(signals)
    if num_buy_sells > 0:
        signals_df = pd.DataFrame(signals).dropna()
        wins = len(signals_df[signals_df["win"]])
        long_wins = len(signals_df[signals_df["win"] & signals_df["win_delay"]])
        wins_ratio = round(wins / num_buy_sells, 2)
        long_wins_ratio = round(long_wins / num_buy_sells, 2)
        # print(f"\nBuySignals:  {num_buy_sells}   {wins}/{num_buy_sells - wins} = {wins_ratio}")
        print(f"LongWins__:  {num_buy_sells}   {long_wins}/{num_buy_sells - long_wins} = {long_wins_ratio}")
    else:
        long_wins = 0
    return num_buy_sells, long_wins


def run_single_backtest(dataset_name, strategy_name, params, artifacts_location=None, log_level="ERROR"):
    params_copy = params.copy()
    random_seed = params_copy.pop("random_seed", None)
    random.seed(random_seed)

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
            cache=CacheConfig(tick_capacity=1000, bar_capacity=1000),
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
        fill_model=LimitFillModel(prob_fill_on_limit=0.8, random_seed=random_seed),
        reject_stop_orders=False,
        # trade_execution=True,
        latency_model=latency_model,
    )

    dataset_params = CATALOG_OPTIONS[dataset_name.lower()]
    symbol = dataset_params["symbol"]

    test_instrument = TestInstrumentProvider.equity(symbol=symbol, venue=DATA_VENUE)
    engine.add_instrument(test_instrument)

    engine.add_data(
        get_catalog_data(symbol, dataset_params["start"], dataset_params["end"], data_cls=TradeTick, venue=DATA_VENUE)
    )
    engine.add_data(
        get_catalog_data(symbol, dataset_params["start"], dataset_params["end"], data_cls=QuoteTick, venue=DATA_VENUE)
    )

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

    run_config = {"symbol": symbol, "strategy": config.dict()}
    performance_stats = run_strategy(strategy, engine, artifacts_location, run_config=run_config)
    return performance_stats


def run_multiple_backtests(dataset_names, strategy_name, params, log_level="ERROR"):
    performance_stats = []
    for dataset_name in dataset_names:
        p_stats = run_single_backtest(dataset_name, strategy_name, params, artifacts_location=None, log_level=log_level)
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


if __name__ == "__main__":
    log_level = "INFO"
    # log_level = "DEBUG"
    log_level = "ERROR"
    # log_level = "WARNING"

    strategy_name = "momo"

    params = dict(
        trade_size=5,
        max_position_multiplier=1,
        stop_loss=0.10,
        take_profit=None,
        take_ratio=0.8,
        vwap_window=100,
        variance_window_ratio=1.5,
        lower_scalar=0.10,
        upper_scalar=0.05,
        trailing_buy_order=False,
        use_bracket_orders=False,
        use_oco_sell_orders=False,
        simple_take=False,
        trailing_take=True,
        allow_trades=True,
        random_buy=True,
        random_seed=11,
    )
    datasets = [
        "aapl1103",
        "aapl1104",
        "aapl1105",
        "aapl1106",
        "aapl1107",
    ]
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
        run_single_backtest(
            BACKTEST_SYMBOL,
            strategy_name,
            params,
            artifacts_location=VIZ_ARTIFACTS_PATH,
            log_level=log_level,
        )

        artifacts_io = ArtifactsIO(VIZ_ARTIFACTS_PATH)
        num_buy_sells, long_wins = buy_signal_stats(artifacts_io.load_signals())

        orders_report = artifacts_io.load_orders_report()
        df = orders_report.copy()
        df = df[df["filled_qty"].astype(int) > 0]

        # win_ratio = _calculate_oco_win_ratio_DEPRECATED(df)
        trades, sell_legs = orders_to_trades(df)
        analyze_trades(trades, print_report=True)

        buys = df[df["side"] == "BUY"]
        print()
