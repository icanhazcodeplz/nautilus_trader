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

"""
Alpaca execution tester example.

This example demonstrates using the ExecTester strategy for testing
execution functionality with Alpaca Markets.
The strategy places limit orders at a specified offset from the market.

Requirements:
1. Set environment variables:
   - ALPACA_API_KEY: Your Alpaca API key
   - ALPACA_API_SECRET: Your Alpaca API secret

2. Make sure you have a funded Alpaca paper trading account.

3. Run the script:
   python examples/live/alpaca/alpaca_exec_tester.py

"""

from decimal import Decimal

from nautilus_trader.adapters.alpaca import ALPACA, AlpacaInstrumentProvider, AlpacaExecutionClient
from nautilus_trader.adapters.alpaca import AlpacaExecClientConfig
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.common.component import LiveClock
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.test_kit.strategies.tester_exec import ExecTester
from nautilus_trader.test_kit.strategies.tester_exec import ExecTesterConfig


# =====================================================================================
# Configuration
# =====================================================================================

# Instrument to trade (must be a valid Alpaca stock symbol)
instrument_id = InstrumentId.from_str("AAPL.ALPACA")

# Number of ticks to offset limit orders from the market
offset_ticks = 1

# Trade size in shares
trade_size = Decimal("1")

# Environment: "paper" or "live" (use paper for testing!)
environment = "paper"

# Dry run mode - if True, no actual orders will be placed
dry_run = False  # Set this to False to enable actual trading

# =====================================================================================
# Trading Node Configuration
# =====================================================================================

config = InstrumentProviderConfig(load_all=True)
config_node = TradingNodeConfig(
    trader_id=TraderId("TESTER-001"),
    logging=LoggingConfig(
        log_level="INFO",
        use_pyo3=True,
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,  # Reconcile state with exchange on startup
        # snapshot_orders=True,
        # snapshot_positions=True,
        # snapshot_positions_interval_secs=5.0,
    ),
    cache=CacheConfig(
        # database=DatabaseConfig(),
        encoding="msgpack",
        timestamps_as_iso8601=True,
        buffer_interval_ms=100,
    ),
    exec_clients={
        "ALPACA": AlpacaExecClientConfig(
            environment=environment,
            # api_key and api_secret will be sourced from environment variables:
            # ALPACA_API_KEY and ALPACA_API_SECRET
            instrument_provider=InstrumentProviderConfig(load_all=True),
        ),
    },
    timeout_connection=60.0,
    timeout_reconciliation=20.0,
    timeout_portfolio=10.0,
    timeout_disconnection=5.0,
    timeout_post_stop=5.0,
)

# Instantiate the node with a configuration
node = TradingNode(config=config_node)


# =====================================================================================
# Strategy Configuration
# =====================================================================================

config_tester = ExecTesterConfig(
    instrument_id=instrument_id,
    external_order_claims=[instrument_id],
    order_qty=trade_size,
    tob_offset_ticks=offset_ticks,
    subscribe_quotes=False,  # Alpaca doesn't require quote subscription for this test
    subscribe_trades=False,  # Alpaca doesn't require trade subscription for this test
    use_post_only=False,  # Alpaca doesn't have a post-only flag
    close_positions_time_in_force=TimeInForce.DAY,  # Use DAY for Alpaca
    open_position_on_start_qty=trade_size,
    dry_run=dry_run,
    log_data=True,
)

# Instantiate your strategy
strategy = ExecTester(config=config_tester)

# Add your strategies and modules
node.trader.add_strategy(strategy)

# Register your client factories with the node
node.add_exec_client_factory("ALPACA", AlpacaLiveExecClientFactory)
node.build()


# =====================================================================================
# Run the trading node
# =====================================================================================

if __name__ == "__main__":
    try:
        print("=" * 80)
        print("Alpaca Execution Tester")
        print("=" * 80)
        print(f"Environment: {environment}")
        print(f"Instrument: {instrument_id}")
        print(f"Trade Size: {trade_size} shares")
        print(f"Offset Ticks: {offset_ticks}")
        print(f"Dry Run: {dry_run}")
        print("=" * 80)
        print()

        if dry_run:
            print("⚠️  DRY RUN MODE - No actual orders will be placed")
        else:
            print("🔴 LIVE MODE - Real orders will be placed!")

        print()
        print("Press CTRL+C to stop...")
        print()

        node.run()
    finally:
        node.dispose()
