#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2025 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

from decimal import Decimal

from custom.strategies.momo import Momo, MomoConfig
from custom.strategies.tester_exec import CustomExecTesterConfig, CustomExecTester
from nautilus_trader.adapters.alpaca import AlpacaDataClientConfig, AlpacaLiveDataClientFactory, ALPACA
from nautilus_trader.adapters.alpaca import AlpacaExecClientConfig
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId

symbol = "AAPL"
instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA}")
environment = "paper"  # "paper" or "live"
dry_run = False  # Set this to False to enable actual trading

instrument_provider_config = InstrumentProviderConfig(
    load_ids=frozenset([instrument_id]),
    # FIXME: BRENT - figure out why all instruments are loaded when load_all=False
    load_all=False,
)
config_node = TradingNodeConfig(
    trader_id=TraderId("TESTER-001"),
    logging=LoggingConfig(log_level="INFO", use_pyo3=True),
    exec_engine=LiveExecEngineConfig(reconciliation=False),
    cache=CacheConfig(
        # database=DatabaseConfig(),
        encoding="msgpack",
        timestamps_as_iso8601=True,
        buffer_interval_ms=100,
    ),
    data_clients={
        ALPACA: AlpacaDataClientConfig(
            environment=environment,
            feed="iex",  # 'iex' or 'sip' (SIP requires paid subscription)
            instrument_provider=instrument_provider_config,
        ),
    },
    exec_clients={
        ALPACA: AlpacaExecClientConfig(
            environment=environment,
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
strategy = Momo(config=MomoConfig(
    instrument_id=instrument_id,
    external_order_claims=[instrument_id],
    trade_size=10,
    max_position_multiplier=1,
    stop_loss=0.30,
    take_profit=0.30,
    take_ratio=0.5,
    vwap_window=50,
    vwap_buy_threshold=0.20,
    trailing_stop=True,
))

node.trader.add_strategy(strategy)

node.add_data_client_factory(ALPACA, AlpacaLiveDataClientFactory)
node.add_exec_client_factory(ALPACA, AlpacaLiveExecClientFactory)
node.build()


if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()
