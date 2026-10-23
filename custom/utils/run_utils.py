from pathlib import Path

from custom.statistics.trade_avg import AvgTrade
from custom.statistics.trade_avg_scaled import AverageEntryPrice
from custom.statistics.trade_avg_scaled import PnlPer100
from custom.statistics.trade_avg_scaled import TotalEntered
from custom.statistics.trade_counts import Losers
from custom.statistics.trade_counts import NumTrades
from custom.statistics.trade_counts import Winners
from custom.statistics.win_loss_ratio import WinLossRatio
from custom.utils.paths import RUN_START_TIME_AS_STR
from custom.utils.paths import run_trader_id
from nautilus_trader import ENV
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.adapters.alpaca import AlpacaDataClientConfig
from nautilus_trader.adapters.alpaca import AlpacaExecClientConfig
from nautilus_trader.analysis.analyzer import PortfolioAnalyzer
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.common.component import Logger
from nautilus_trader.common.config import DatabaseConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import Expectancy
from nautilus_trader.core.nautilus_pyo3 import LongRatio
from nautilus_trader.core.nautilus_pyo3 import MinLoser
from nautilus_trader.core.nautilus_pyo3 import MinWinner
from nautilus_trader.core.nautilus_pyo3 import ProfitFactor
from nautilus_trader.core.nautilus_pyo3 import ReturnsAverage
from nautilus_trader.core.nautilus_pyo3 import ReturnsAverageLoss
from nautilus_trader.core.nautilus_pyo3 import ReturnsAverageWin
from nautilus_trader.core.nautilus_pyo3 import ReturnsVolatility
from nautilus_trader.core.nautilus_pyo3 import RiskReturnRatio
from nautilus_trader.core.nautilus_pyo3 import SharpeRatio
from nautilus_trader.core.nautilus_pyo3 import SortinoRatio
from nautilus_trader.live.config import LiveDataEngineConfig
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue


log = Logger(name="run_utils")


def performance_stats(analyzer: PortfolioAnalyzer) -> dict:
    return {
        **analyzer.get_performance_stats_pnls(),
        **analyzer.get_performance_stats_returns(),
        **analyzer.get_performance_stats_general(),
    }


def live_performance_stats(analyzer: PortfolioAnalyzer, cache, instrument_id: InstrumentId) -> dict:
    """
    Return the performance stats for one symbol of a live run.

    Nothing on a live node calls `calculate_statistics` (only `BacktestEngine` does at the end of a
    run), so without this every stat is zero or NaN. `calculate_statistics` resets the analyzer, so
    the node's own analyzer can be reused for one symbol after another. `PnL%` is relative to the
    whole account, since every symbol trades from the same one.
    """
    account = cache.account_for_venue(Venue(ALPACA))
    if account is None:
        log.error(f"No {ALPACA} account in the cache; cannot calculate stats for {instrument_id}")
        return {}
    # NETTING reuses one position ID per instrument, so every cycle before the current one lives on
    # as a snapshot. BacktestEngine passes them in too.
    snapshots = [s for s in cache.position_snapshots() if s.instrument_id == instrument_id]
    analyzer.calculate_statistics(account, cache.positions(instrument_id=instrument_id) + snapshots)
    return performance_stats(analyzer)


def register_custom_statistics(analyzer):
    """Swap the analyzer's default statistics for ours. Backtests and live runs both use this."""
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
        analyzer.deregister_statistic(stat_class())

    for stat_class in [NumTrades, Winners, Losers, WinLossRatio, AvgTrade, TotalEntered, AverageEntryPrice, PnlPer100]:
        analyzer.register_statistic(stat_class())


def live_node_config(
    instrument_ids: list[InstrumentId],
    run_dir: Path,
    log_level: str,
    file_log_level: str,
) -> TradingNodeConfig:
    instrument_provider_config = InstrumentProviderConfig(load_ids=frozenset(instrument_ids), load_all=False)
    return TradingNodeConfig(
        trader_id=run_trader_id(RUN_START_TIME_AS_STR),
        logging=LoggingConfig(
            log_level=log_level,
            log_level_file=file_log_level,
            log_directory=str(run_dir),
            log_file_name=file_log_level,
            use_pyo3=True,
            log_file_max_size=int(10e6),
            log_file_max_backup_count=50,
            log_component_levels={
                "nautilus_infrastructure::redis::cache": "INFO",
            },
        ),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=10,
            reconciliation_instrument_ids=list(instrument_ids),
            inflight_check_interval_ms=5000,
            reconciliation_startup_delay_secs=3.0,
            open_check_interval_secs=10,
            # purge_closed_orders_interval_mins=None,
            # purge_closed_positions_interval_mins=None,
            open_check_open_only=True,
            open_check_lookback_mins=5,  # TODO: Reduce this?
            open_check_threshold_ms=3000,
            graceful_shutdown_on_exception=True,
            allow_overfills=True,  # FIXME: Do we want this?
        ),
        cache=CacheConfig(
            database=DatabaseConfig(),
            encoding="msgpack",
            timestamps_as_iso8601=True,
            buffer_interval_ms=None,
            flush_on_start=False,
        ),
        data_clients={
            ALPACA: AlpacaDataClientConfig(
                paper=ENV.PAPER,
                feed="sip",  # 'iex' or 'sip'
                instrument_provider=instrument_provider_config,
            ),
        },
        exec_clients={
            ALPACA: AlpacaExecClientConfig(
                paper=ENV.PAPER,
                instrument_provider=instrument_provider_config,
            ),
        },
        data_engine=LiveDataEngineConfig(graceful_shutdown_on_exception=True),
        timeout_connection=60.0,
        timeout_reconciliation=20.0,
        timeout_portfolio=10.0,
        timeout_disconnection=5.0,
        timeout_post_stop=5.0,
    )
