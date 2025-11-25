import traceback

from custom.artifacts import ArtifactsIO
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.live.node import TradingNode
from nautilus_trader.common.component import Logger

log = Logger(name="run_utils")


def run_strategy(strategy, node_or_engine, artifacts_location=None, run_config=None, paper=True):
    strategy.initialize(artifacts_location=artifacts_location)
    if isinstance(node_or_engine, TradingNode):
        alpaca_helper = AlpacaTraderHelper(paper=paper)
        alpaca_helper.flatten_symbol(symbol=strategy.config.instrument_id.symbol.value)

        node_or_engine.trader.add_strategy(strategy=strategy)
        node_or_engine.build()
    elif isinstance(node_or_engine, BacktestEngine):
        node_or_engine.add_strategy(strategy=strategy)

    try:
        node_or_engine.run()
    except Exception as e:
        log.error(f"Exception during run: {e}")
        log.error(f"Traceback:\n{traceback.format_exc()}")
    finally:
        if isinstance(node_or_engine, TradingNode):
            alpaca_helper.flatten_symbol(symbol=strategy.config.instrument_id.symbol.value)

        orders_report = node_or_engine.trader.generate_orders_report()
        performance_stats = {
            **node_or_engine.portfolio.analyzer.get_performance_stats_pnls(),
            **node_or_engine.portfolio.analyzer.get_performance_stats_returns(),
            **node_or_engine.portfolio.analyzer.get_performance_stats_general(),
        }
        if artifacts_location is not None:
            artifacts_io = ArtifactsIO(artifacts_location)
            artifacts_io.save_orders_report(orders_report)
            artifacts_io.save_performance_metrics(performance_stats)
            if run_config is not None:
                artifacts_io.save_config(run_config)

            # "buy_signals": num_buy_sells,
            # "buy_signal_wins": long_wins,
            # "buys": total_buys,
            # "wins": wins,

        node_or_engine.dispose()
        return performance_stats
