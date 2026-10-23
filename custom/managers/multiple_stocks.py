import traceback

from custom.artifacts import LiveArtifactsIO
from custom.strategies.base import alpaca_instrument_id
from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper
from custom.utils.paths import RUN_START_TIME_AS_STR
from custom.utils.paths import current_live_run_artifacts_dir
from custom.utils.run_utils import live_node_config
from custom.utils.run_utils import live_performance_stats
from custom.utils.run_utils import register_custom_statistics
from nautilus_trader import ENV
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.adapters.alpaca import AlpacaLiveDataClientFactory
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.analysis import ReportProvider
from nautilus_trader.common.component import Logger
from nautilus_trader.live.node import TradingNode


log = Logger(name="MultipleStocks")


def live_blocking_reasons(config: MomoStrategyConfig) -> list[str]:
    """Return why `config` can't trade a live account, or an empty list when it can."""
    blocking = []
    if config.trade_size > 4:
        blocking.append(f"trade_size={config.trade_size} (max 4)")
    if config.max_position_multiplier > 1:
        blocking.append(f"max_position_multiplier={config.max_position_multiplier} (max 1)")
    if config.entry_strategy == EntryStrategy.RANDOM:
        blocking.append(f"entry_strategy={EntryStrategy.RANDOM}")
    return blocking


class MultipleStocks:
    """
    Run one `MomoStrategy` per symbol in a single live `TradingNode`.

    Alpaca allows one market-data WebSocket per account, and every symbol's subscription is a
    message on the data client's one socket, so all symbols have to share a node.

    An exception in any strategy shuts the whole node down (`graceful_shutdown_on_exception`), and
    every symbol is then flattened.
    """

    def __init__(
        self,
        configs: dict[str, MomoStrategyConfig],
        log_level: str = "INFO",
        file_log_level: str = "DEBUG",
    ) -> None:
        if not configs:
            raise ValueError("No symbols to run")
        self.log_level = log_level
        self.file_log_level = file_log_level
        self.run_dir = current_live_run_artifacts_dir()
        self.configs = {symbol.upper(): config for symbol, config in configs.items()}
        if len(self.configs) != len(configs):
            raise ValueError(f"A symbol is in the configs more than once: {list(configs)}")
        if not ENV.PAPER:
            self._confirm_live()

    def _confirm_live(self) -> None:
        blocking = [
            f"{symbol}: {reason}" for symbol, config in self.configs.items() for reason in live_blocking_reasons(config)
        ]
        if blocking:
            raise ValueError(f"Can't run with these params live: {', '.join(blocking)}")
        print("\n⚠️  You are running LIVE with:")
        for symbol, config in self.configs.items():
            print(f"\n  {symbol}")
            for key, value in config.dict().items():
                if key in ("oms_type", "external_order_claims"):
                    continue
                print(f"    {key}: {value}")
        user_input = input("\nPress 'y' to continue, type any char to exit... ")
        if user_input.lower() != "y":
            raise SystemExit()

    def run(self) -> None:
        config_node = live_node_config(
            [alpaca_instrument_id(symbol) for symbol in self.configs],
            self.run_dir,
            self.log_level,
            self.file_log_level,
        )
        # Before anything can crash, so the run's orders can always be found in Redis afterwards
        LiveArtifactsIO.save_run_info(
            self.run_dir,
            trader_id=config_node.trader_id,
            run_start=RUN_START_TIME_AS_STR,
            paper=ENV.PAPER,
            symbols=list(self.configs),
        )
        node = TradingNode(config=config_node)
        node.add_data_client_factory(ALPACA, AlpacaLiveDataClientFactory)
        node.add_exec_client_factory(ALPACA, AlpacaLiveExecClientFactory)

        helper = AlpacaTraderHelper()
        for symbol in self.configs:
            helper.cancel_orders_and_flatten_position_with_retry(symbol)

        strategies: dict[str, MomoStrategy] = {}
        artifacts: dict[str, LiveArtifactsIO] = {}
        for symbol, config in self.configs.items():
            artifacts_io = LiveArtifactsIO(self.run_dir, symbol)
            artifacts_io.directory.mkdir(parents=True, exist_ok=True)
            strategy = MomoStrategy(config=config, symbol=symbol)
            strategy.initialize(artifacts_io=artifacts_io, trader_helper=helper)
            node.trader.add_strategy(strategy=strategy)
            strategies[symbol] = strategy
            artifacts[symbol] = artifacts_io

        node.build()
        for strategy in strategies.values():
            strategy._exec_engine = node.kernel.exec_engine

        try:
            # FIXME: Need to run in a separate process so can still close position if rust panick:
            #  exit code 134 (interrupted by signal 6:SIGABRT)
            node.run()
        except Exception as e:
            log.error(f"Exception during run of {list(self.configs)}: {e}")
            log.error(f"Traceback:\n{traceback.format_exc()}")
        finally:
            for symbol in self.configs:
                try:
                    helper.cancel_orders_and_flatten_position_with_retry(symbol)
                except Exception as e:
                    log.error(f"Unable to flatten {symbol} position during cleanup: {e}")
                    # FIXME: Add text notification!

            analyzer = node.portfolio.analyzer
            register_custom_statistics(analyzer)
            for symbol, config in self.configs.items():
                try:
                    artifacts_io = artifacts[symbol]
                    instrument_id = strategies[symbol].instrument_id
                    report = ReportProvider.generate_orders_report(node.cache.orders(instrument_id=instrument_id))
                    artifacts_io.save_orders_report(report)
                    artifacts_io.save_performance_metrics(
                        live_performance_stats(analyzer, node.cache, instrument_id),
                    )
                    artifacts_io.save_config(config.dict())
                except Exception as e:
                    log.error(f"Unable to save {symbol} artifacts: {e}\n{traceback.format_exc()}")

            node.dispose()
