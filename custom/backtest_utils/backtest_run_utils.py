import glob
import os
import shutil

from custom.artifacts import ArtifactsIO, BACKTEST_RUNS_PATH
from custom.backtest_scripts.backtest_config import DATA_VENUE, prob_fill_on_limit, latency_model
from custom.nt_extensions.limit_fill_model import LimitFillModel
from custom.statistics.trade_avg import AvgTrade
from custom.statistics.trade_avg_scaled import PnlPer100, TotalBought, AverageBuyPrice
from custom.statistics.trade_counts import Winners, Losers, NumTrades
from custom.statistics.win_loss_ratio import WinLossRatio
from custom.utils.orders_to_trades import orders_to_trades
from custom.backtest_utils.load_catalog_data import BACKTESTING_CATALOG

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.core.nautilus_pyo3 import (
    Expectancy,
    LongRatio,
    MinLoser,
    MinWinner,
    ProfitFactor,
    ReturnsAverage,
    ReturnsAverageLoss,
    ReturnsAverageWin,
    ReturnsVolatility,
    RiskReturnRatio,
    SharpeRatio,
    SortinoRatio,
)
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, BookType, OmsType
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderExpired,
    OrderFilled,
    OrderInitialized,
    OrderUpdated,
)
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.config import StreamingConfig


def build_backtest_engine(artifacts_location, log_level, controller=None):
    """Build a BacktestEngine with standard logging, cache, and streaming config.

    Clears any existing *.log files in artifacts_location.
    """
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
            controller=controller,
        )
    )

    # Register the parquet catalog so request_trade_ticks / request_quote_ticks /
    # request_bars called from a strategy's on_start() actually serve data via
    # on_historical_data. Without this, BacktestMarketDataClient no-ops on requests.
    engine.kernel.data_engine.register_catalog(BACKTESTING_CATALOG)

    return engine


def add_default_venue(engine, random_seed):
    engine.add_venue(
        venue=Venue(DATA_VENUE),
        book_type=BookType.L1_MBP,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(100000.0, USD)],
        fill_model=LimitFillModel(prob_fill_on_limit=prob_fill_on_limit, random_seed=random_seed),
        reject_stop_orders=False,
        latency_model=latency_model,
    )


def register_custom_statistics(engine):
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

    for stat_class in [NumTrades, Winners, Losers, WinLossRatio, AvgTrade, TotalBought, AverageBuyPrice, PnlPer100]:
        engine.portfolio.analyzer.register_statistic(stat_class())


def save_backtest_order_updates(engine, artifacts_location):
    if artifacts_location is None:
        return
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


def analyze_backtest():
    artifacts_io = ArtifactsIO(BACKTEST_RUNS_PATH)

    p_mets = artifacts_io.load_performance_metrics()
    print("\n".join(f"{k}: {round(v, 2)}" for k, v in p_mets.items()))
    print()

    orders_report = artifacts_io.load_orders_report()
    trades, sell_legs = orders_to_trades(orders_report)
    analyze_trades(trades, print_report=True)
