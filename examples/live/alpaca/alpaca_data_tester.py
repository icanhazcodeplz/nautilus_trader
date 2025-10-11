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
Alpaca data tester example.

This example demonstrates using the DataTester actor for testing
market data functionality with Alpaca Markets.

NOTE: The Alpaca data client is not yet fully implemented.
This script provides the structure and configuration that will work
once the data client implementation is complete.

Requirements:
1. Set environment variables:
   - ALPACA_API_KEY: Your Alpaca API key
   - ALPACA_API_SECRET: Your Alpaca API secret

2. Run the script:
   python examples/live/alpaca/alpaca_data_tester.py

"""
import pandas as pd

from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.adapters.alpaca import AlpacaDataClientConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.test_kit.strategies.tester_data import DataTester
from nautilus_trader.test_kit.strategies.tester_data import DataTesterConfig


# *** THIS IS A TEST STRATEGY WITH NO ALPHA ADVANTAGE WHATSOEVER. ***
# *** IT IS NOT INTENDED TO BE USED TO TRADE LIVE WITH REAL MONEY. ***

# Example symbols for different Alpaca products
# US Stocks: AAPL (Apple), TSLA (Tesla), SPY (S&P 500 ETF)
# Crypto: BTC/USD, ETH/USD (if using crypto trading)

environment = "paper"  # Use "paper" for testing, "live" for production
symbol = "AAPL"  # Apple stock
# symbol = "TSLA"  # Tesla stock
# symbol = "SPY"  # S&P 500 ETF
# symbol = "BTC/USD"  # Bitcoin (if crypto enabled)

# Configure the trading node
config_node = TradingNodeConfig(
    trader_id=TraderId("TESTER-001"),
    logging=LoggingConfig(log_level="INFO", use_pyo3=True),
    exec_engine=LiveExecEngineConfig(
        reconciliation=False,  # Not applicable for data-only testing
    ),
    data_clients={
        "ALPACA": AlpacaDataClientConfig(
            api_key=None,  # 'ALPACA_API_KEY' env var
            api_secret=None,  # 'ALPACA_API_SECRET' env var
            environment=environment,
            feed="iex",  # 'iex' or 'sip' (SIP requires paid subscription)
            http_base_url=None,  # Override with custom endpoint
            # ws_base_url=None,  # Override with custom endpoint
            instrument_provider=InstrumentProviderConfig(load_all=True),
        ),
    },
    timeout_connection=10.0,
    timeout_reconciliation=10.0,
    timeout_disconnection=2.0,
    timeout_post_stop=1.0,
)

# Configure the data tester actor
config_tester = DataTesterConfig(
    instrument_ids=[InstrumentId.from_str(f"{symbol}.{ALPACA}")],
    bar_types=[BarType.from_str(f"{symbol}.{ALPACA}-1-MINUTE-LAST-EXTERNAL")],
    subscribe_instrument=True,
    # subscribe_quotes=True,  # Real-time quote data
    # subscribe_trades=True,  # Real-time trade data
    # subscribe_bars=True,  # Real-time bar aggregation
    # subscribe_book_deltas=True,  # Order book updates
    # subscribe_book_depth=True,  # Full order book snapshots
    # subscribe_book_at_interval=True,
    # book_type=BookType.L2_MBP,  # Level 2 Market By Price
    # book_depth=10,  # Top 10 levels
    # book_interval_ms=100,  # Sample book every 100ms
    request_trades=True,  # Historical trade data
    # request_bars=True,  # Historical bar data
    requests_start_delta=pd.Timedelta("22 hours"),
)

# Setup the trading node
node = TradingNode(config=config_node)

# Add the data tester actor to the node
node.trader.add_actor(DataTester(config=config_tester))

# Register the data client factory
from nautilus_trader.adapters.alpaca.factories import AlpacaLiveDataClientFactory
node.add_data_client_factory("ALPACA", AlpacaLiveDataClientFactory)
node.build()

# Run the node
if __name__ == "__main__":
    try:
        print("=" * 80)
        print("Alpaca Data Tester")
        print("=" * 80)
        print(f"Environment: {environment}")
        print(f"Symbol: {symbol}")
        print("=" * 80)
        print()
        print("NOTE: WebSocket market data streaming is not yet fully implemented.")
        print("The script will connect and load instruments, but real-time data")
        print("subscriptions (quotes, trades, order book) are not yet available.")
        print()
        print("Press CTRL+C to stop...")
        print()

        node.run()
    except KeyboardInterrupt:
        node.stop()
    finally:
        node.dispose()
