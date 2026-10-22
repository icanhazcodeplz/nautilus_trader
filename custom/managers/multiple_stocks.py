import traceback
from pathlib import Path

import msgspec

from custom.artifacts import LiveArtifactsIO
from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper
from custom.utils.paths import RUN_START_TIME_AS_STR
from custom.utils.paths import current_live_run_artifacts_dir
from custom.utils.run_utils import live_performance_stats, register_custom_statistics
from nautilus_trader import ENV
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.adapters.alpaca import AlpacaDataClientConfig
from nautilus_trader.adapters.alpaca import AlpacaExecClientConfig
from nautilus_trader.adapters.alpaca import AlpacaLiveDataClientFactory
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.analysis import ReportProvider
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.common.component import Logger
from nautilus_trader.common.config import DatabaseConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.config import LiveDataEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId


log = Logger(name="MultipleStocks")



def _node_config(
    instrument_ids: list[InstrumentId],
    run_dir: Path,
    log_level: str,
    file_log_level: str,
) -> TradingNodeConfig:
    instrument_provider_config = InstrumentProviderConfig(load_ids=frozenset(instrument_ids), load_all=False)
    return TradingNodeConfig(
        trader_id=TraderId(f"T-{RUN_START_TIME_AS_STR}"),  # FIXME: DO NOT REMOVE DT_STR, Is there a more robust way?
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
        self.configs = self._fill_instrument_ids(configs)
        if not ENV.PAPER:
            self._confirm_live()

    @staticmethod
    def _fill_instrument_ids(configs: dict[str, MomoStrategyConfig]) -> dict[str, MomoStrategyConfig]:
        filled = {}
        for symbol, config in configs.items():
            symbol = symbol.upper()
            if symbol in filled:
                raise ValueError(f"{symbol} is in the configs more than once")
            instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA}")
            if config.instrument_id is not None and config.instrument_id != instrument_id:
                raise ValueError(f"Config for {symbol} has instrument_id={config.instrument_id}")
            filled[symbol] = msgspec.structs.replace(
                config,
                instrument_id=instrument_id,
                external_order_claims=[instrument_id],
            )
        return filled

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
                if key in ("instrument_id", "oms_type", "external_order_claims"):
                    continue
                print(f"    {key}: {value}")
        user_input = input("\nPress 'y' to continue, type any char to exit... ")
        if user_input.lower() != "y":
            raise SystemExit()

    def run(self) -> None:
        config_node = _node_config(
            [config.instrument_id for config in self.configs.values()],
            self.run_dir,
            self.log_level,
            self.file_log_level,
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
            strategy = MomoStrategy(config=config)
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
                    report = ReportProvider.generate_orders_report(
                        node.cache.orders(instrument_id=config.instrument_id)
                    )
                    artifacts_io.save_orders_report(report)
                    artifacts_io.save_performance_metrics(
                        live_performance_stats(analyzer, node.cache, config.instrument_id),
                    )
                    artifacts_io.save_config(config.dict())
                except Exception as e:
                    log.error(f"Unable to save {symbol} artifacts: {e}\n{traceback.format_exc()}")

            node.dispose()
