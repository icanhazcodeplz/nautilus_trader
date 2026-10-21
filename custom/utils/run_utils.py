import traceback

from custom.artifacts import LiveArtifactsIO, BacktestArtifactsIO
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.live.node import TradingNode
from nautilus_trader.common.component import Logger

log = Logger(name="run_utils")


def run_strategy(strategy, node_or_engine, run_config, artifacts_io=None, paper=True, raise_errors=False):
    """
    Run `strategy` on a live node or backtest engine and return its performance stats.

    An exception during the run is logged and swallowed, so a live node still cleans up and
    reports. `raise_errors` re-raises it after cleanup instead, for callers that must not treat a
    failed run's stats as a result (and whose logging may be bypassed, hiding the log line).
    """
    if isinstance(node_or_engine, TradingNode):
        symbol = strategy.config.instrument_id.symbol.value
        alpaca_helper = AlpacaTraderHelper(paper=paper)
        alpaca_helper.cancel_orders_and_flatten_position_with_retry(symbol)
        strategy.initialize(artifacts_io=artifacts_io, trader_helper=alpaca_helper)
        node_or_engine.trader.add_strategy(strategy=strategy)
        node_or_engine.build()
        strategy._exec_engine = node_or_engine.kernel.exec_engine
    elif isinstance(node_or_engine, BacktestEngine):
        strategy.initialize(artifacts_io=artifacts_io, trader_helper=None)
        node_or_engine.add_strategy(strategy=strategy)

    try:
        # FIXME: Need to run in a separate process so can still close position if rust panick: exit code 134 (interrupted by signal 6:SIGABRT)
        node_or_engine.run()
    except Exception as e:
        log.error(f"Exception during run with params {run_config}: {e}")
        log.error(f"Traceback:\n{traceback.format_exc()}")
        if raise_errors:
            raise
    finally:
        if isinstance(node_or_engine, TradingNode):
            try:
                alpaca_helper.cancel_orders_and_flatten_position_with_retry(symbol)
            except Exception as e:
                log.error(f"Unable to flatten position during cleanup: {e}")
                # FIXME: Add text notification!
        orders_report = node_or_engine.trader.generate_orders_report()
        performance_stats = {
            **node_or_engine.portfolio.analyzer.get_performance_stats_pnls(),
            **node_or_engine.portfolio.analyzer.get_performance_stats_returns(),
            **node_or_engine.portfolio.analyzer.get_performance_stats_general(),
        }
        if artifacts_io is not None:
            artifacts_io.save_orders_report(orders_report)
            artifacts_io.save_performance_metrics(performance_stats)
            artifacts_io.save_config(run_config)

        node_or_engine.dispose()

    return performance_stats
