#!/usr/bin/env python3
import json

from custom.strategies.latency_test import LatencyTestStrategyConfig, LatencyTestStrategy
from custom.utils import run_artifacts_subdir
from nautilus_trader.adapters.alpaca import AlpacaExecClientConfig, AlpacaDataClientConfig
from nautilus_trader.adapters.alpaca import AlpacaLiveDataClientFactory
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId


symbol = "TSLA"
paper = True
buy_on_tick = True
order_count = 50

instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA}")
instrument_provider_config = InstrumentProviderConfig(load_ids=frozenset([instrument_id]), load_all=False)
config_node = TradingNodeConfig(
    trader_id=TraderId("TESTER-001"),
    logging=LoggingConfig(log_level="DEBUG", use_pyo3=True),
    exec_engine=LiveExecEngineConfig(reconciliation=False),
    cache=CacheConfig(
        encoding="msgpack",
        timestamps_as_iso8601=True,
        buffer_interval_ms=100,
    ),
    data_clients={
        ALPACA: AlpacaDataClientConfig(
            paper=paper,
            feed="iex",
            instrument_provider=instrument_provider_config,
        ),
    },
    exec_clients={
        ALPACA: AlpacaExecClientConfig(
            paper=paper,
            instrument_provider=instrument_provider_config,
            record_orders=True,
        ),
    },
    timeout_post_stop=4.0,
)

node = TradingNode(config=config_node)

strategy_config = LatencyTestStrategyConfig(
    instrument_id=instrument_id,
    buy_on_tick=buy_on_tick,
    order_count=order_count,
)
strategy = LatencyTestStrategy(config=strategy_config)

node.trader.add_strategy(strategy)
node.add_data_client_factory(ALPACA, AlpacaLiveDataClientFactory)
node.add_exec_client_factory(ALPACA, AlpacaLiveExecClientFactory)
node.build()


if __name__ == "__main__":
    try:
        run_details = {"paper": paper, "symbol": symbol, "strategy": strategy_config.dict()}
        details_filepath = run_artifacts_subdir("config.json")
        with open(details_filepath, "w") as f:
            json.dump(run_details, f, indent=2, default=str)

        node.run(raise_exception=True)
    finally:
        # orders_report = node.trader.generate_orders_report()
        node.dispose()
