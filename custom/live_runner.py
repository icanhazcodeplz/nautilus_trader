#!/usr/bin/env python3
import json

from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils import run_artifacts_subdir
from nautilus_trader.adapters.alpaca import ALPACA, AlpacaExecClientConfig, AlpacaDataClientConfig
from nautilus_trader.adapters.alpaca import AlpacaLiveDataClientFactory
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId


symbol = "QLGN"
instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA}")
paper = True
dry_run = False  # Set this to False to enable actual trading

instrument_provider_config = InstrumentProviderConfig(
    load_ids=frozenset([instrument_id]),
    # FIXME: BRENT - figure out why all instruments are loaded when load_all=False
    load_all=False,
)
config_node = TradingNodeConfig(
    trader_id=TraderId("TESTER-001"),
    logging=LoggingConfig(log_level="DEBUG", use_pyo3=True),
    exec_engine=LiveExecEngineConfig(reconciliation=False),
    cache=CacheConfig(
        # database=DatabaseConfig(),
        encoding="msgpack",
        timestamps_as_iso8601=True,
        buffer_interval_ms=100,
    ),
    data_clients={
        ALPACA: AlpacaDataClientConfig(
            paper=paper,
            feed="iex",  # 'iex' or 'sip' (SIP requires paid subscription)
            instrument_provider=instrument_provider_config,
        ),
    },
    exec_clients={
        ALPACA: AlpacaExecClientConfig(
            paper=paper,
            instrument_provider=instrument_provider_config,
        ),
    },
    timeout_connection=60.0,
    timeout_reconciliation=20.0,
    timeout_portfolio=10.0,
    timeout_disconnection=5.0,
    timeout_post_stop=5.0,
)

node = TradingNode(config=config_node)

# strategy = CustomExecTester(config=CustomExecTesterConfig(
#     instrument_id=instrument_id,
#     external_order_claims=[instrument_id],
#     order_qty=1,
#     tob_offset_ticks=1,
#     subscribe_quotes=False,
#     subscribe_trades=False,
#     use_post_only=False,  # Alpaca doesn't have a post-only flag
#     close_positions_time_in_force=TimeInForce.DAY,  # Use DAY for Alpaca
#     close_positions_on_stop=True,
#     open_position_on_start_qty=1,
#     open_position_time_in_force=TimeInForce.DAY,
#     dry_run=dry_run,
#     log_data=True,
# ))
strategy_config = MomoStrategyConfig(
    instrument_id=instrument_id,
    external_order_claims=[instrument_id],
    trade_size=1,
    max_position_multiplier=1,
    stop_loss=0.00,
    take_profit=0.01,
    take_ratio=0.5,
    vwap_window=50,
    vwap_buy_threshold=0.01,
    trailing_stop=True,
)
strategy = MomoStrategy(config=strategy_config)

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

        node.run()
    finally:
        # orders_report = node.trader.generate_orders_report()
        node.dispose()
