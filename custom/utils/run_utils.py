from custom.statistics.trade_avg import AvgTrade
from custom.statistics.trade_avg_scaled import TotalEntered, AverageEntryPrice, PnlPer100
from custom.statistics.trade_counts import NumTrades, Winners, Losers
from custom.statistics.win_loss_ratio import WinLossRatio

from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.analysis.analyzer import PortfolioAnalyzer
from nautilus_trader.common.component import Logger
from nautilus_trader.core.nautilus_pyo3 import (
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
)
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
