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
Simple Alpaca order example.

This example demonstrates the most basic order submission and cancellation
with Alpaca Markets.

Requirements:
1. Set environment variables ALPACA_API_KEY and ALPACA_API_SECRET
2. Use paper trading account for testing

"""

import asyncio
import time

from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.adapters.alpaca import AlpacaExecClientConfig
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs


async def main():
    """Run the simple order example."""
    # =====================================================================================
    # Configuration
    # =====================================================================================

    print("=" * 80)
    print("Alpaca Simple Order Example")
    print("=" * 80)

    # Configure the trading node
    config_node = TradingNodeConfig(
        trader_id=TraderId("TESTER-SIMPLE"),
        logging=LoggingConfig(log_level="INFO", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(reconciliation=True),
        cache=CacheConfig(encoding="msgpack"),
        exec_clients={
            "ALPACA": AlpacaExecClientConfig(
                environment="paper",  # Use paper trading for testing
            ),
        },
        timeout_connection=60.0,
        timeout_reconciliation=20.0,
        timeout_disconnection=5.0,
    )

    # Create and build the node
    node = TradingNode(config=config_node)
    node.add_exec_client_factory("ALPACA", AlpacaLiveExecClientFactory)
    node.build()

    # =====================================================================================
    # Start the node
    # =====================================================================================

    print("\nStarting node...")
    await node.run_async()
    print("✓ Node started")

    # Wait for connection
    await asyncio.sleep(3)

    # =====================================================================================
    # Submit a limit order
    # =====================================================================================

    print("\n" + "=" * 80)
    print("Submitting Limit Order")
    print("=" * 80)

    instrument_id = InstrumentId.from_str("AAPL.ALPACA")
    strategy_id = TestIdStubs.strategy_id()

    # Create a limit order (buy 1 share of AAPL at $100)
    order = LimitOrder(
        trader_id=node.trader_id,
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        client_order_id=TestIdStubs.client_order_id(),
        order_side=OrderSide.BUY,
        quantity=Quantity.from_int(1),
        price=Price.from_str("100.00"),  # Well below market to avoid fill
        time_in_force=TimeInForce.DAY,
        init_id=TestIdStubs.uuid(),
        ts_init=time.time_ns(),
    )

    print(f"\nOrder Details:")
    print(f"  Instrument: {order.instrument_id}")
    print(f"  Side: {order.side}")
    print(f"  Quantity: {order.quantity}")
    print(f"  Price: {order.price}")
    print(f"  Time in Force: {order.time_in_force}")
    print(f"  Client Order ID: {order.client_order_id}")

    # Submit the order
    print("\nSubmitting order...")
    node.trader.submit_order(order)

    # Wait for order to be submitted and accepted
    await asyncio.sleep(3)

    # Check order status
    cached_order = node.cache.order(order.client_order_id)
    if cached_order:
        print(f"\n✓ Order submitted successfully")
        print(f"  Status: {cached_order.status}")
        if cached_order.venue_order_id:
            print(f"  Venue Order ID: {cached_order.venue_order_id}")

    # =====================================================================================
    # Cancel the order
    # =====================================================================================

    print("\n" + "=" * 80)
    print("Canceling Order")
    print("=" * 80)

    await asyncio.sleep(2)

    if cached_order and not cached_order.is_closed:
        print(f"\nCanceling order {order.client_order_id}...")
        node.trader.cancel_order(cached_order)

        # Wait for cancellation
        await asyncio.sleep(3)

        # Check final status
        final_order = node.cache.order(order.client_order_id)
        if final_order:
            print(f"\n✓ Order status: {final_order.status}")
    else:
        print("\nOrder already closed, no need to cancel")

    # =====================================================================================
    # Stop the node
    # =====================================================================================

    print("\n" + "=" * 80)
    print("Shutting Down")
    print("=" * 80)

    await asyncio.sleep(1)
    print("\nStopping node...")
    await node.stop_async()
    print("✓ Node stopped")

    node.dispose()
    print("✓ Node disposed")
    print("\nExample completed successfully!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
