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

"""Data client for Alpaca adapter."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nautilus_trader.adapters.alpaca.execution import ALPACA_VENUE
from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.adapters.alpaca.providers import AlpacaInstrumentProvider
from nautilus_trader.common.enums import LogColor
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.identifiers import ClientId


if TYPE_CHECKING:
    from nautilus_trader.adapters.alpaca.config import AlpacaDataClientConfig
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus
    from nautilus_trader.data.messages import SubscribeInstrument, RequestTradeTicks
    from nautilus_trader.data.messages import SubscribeInstruments
    from nautilus_trader.data.messages import SubscribeOrderBook
    from nautilus_trader.data.messages import SubscribeQuoteTicks
    from nautilus_trader.data.messages import SubscribeTradeTicks
    from nautilus_trader.data.messages import UnsubscribeInstrument
    from nautilus_trader.data.messages import UnsubscribeInstruments
    from nautilus_trader.data.messages import UnsubscribeOrderBook
    from nautilus_trader.data.messages import UnsubscribeQuoteTicks
    from nautilus_trader.data.messages import UnsubscribeTradeTicks


class AlpacaDataClient(LiveMarketDataClient):
    """
    Provides a data client for Alpaca Markets.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    http_client : AlpacaHttpClient
        The Alpaca HTTP client.
    ws_base_url : str
        The WebSocket base URL for market data streaming.
    msgbus : MessageBus
        The message bus for the client.
    cache : Cache
        The cache for the client.
    clock : LiveClock
        The clock for the client.
    instrument_provider : AlpacaInstrumentProvider
        The instrument provider.
    config : AlpacaDataClientConfig
        The configuration for the client.
    name : str, optional
        The custom client ID.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        http_client: AlpacaHttpClient,
        ws_base_url: str,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: AlpacaInstrumentProvider,
        config: AlpacaDataClientConfig,
        name: str | None,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(name or ALPACA_VENUE.value),
            venue=ALPACA_VENUE,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
        )

        # Configuration
        self._config = config
        self._log.info(f"config.environment={config.environment}", LogColor.BLUE)
        self._log.info(f"config.feed={config.feed}", LogColor.BLUE)
        self._log.info(f"config.http_timeout={config.http_timeout}", LogColor.BLUE)

        # HTTP API
        self._http_client = http_client
        self._log.info(f"HTTP client initialized", LogColor.BLUE)

        # WebSocket API
        self._ws_base_url = ws_base_url
        self._log.info(f"WebSocket URL: {ws_base_url}", LogColor.BLUE)

        # WebSocket client will be initialized on connect
        # TODO: Implement WebSocket client for market data streaming
        self._ws_client = None

    @property
    def instrument_provider(self) -> AlpacaInstrumentProvider:
        return self._instrument_provider  # type: ignore

    async def _connect(self) -> None:
        """Connect to Alpaca data streams."""
        self._log.info("Connecting to Alpaca data API...")

        # Initialize instrument provider
        await self._instrument_provider.initialize()
        self._cache_instruments()
        self._send_all_instruments_to_data_engine()

        # TODO: Connect WebSocket client for market data streaming
        # The WebSocket implementation for market data is not yet complete
        self._log.warning(
            "WebSocket market data streaming not yet implemented. "
            "Only instrument data is available.",
            LogColor.YELLOW,
        )

        self._log.info("Connected to Alpaca data API", LogColor.GREEN)

    async def _disconnect(self) -> None:
        """Disconnect from Alpaca data streams."""
        self._log.info("Disconnecting from Alpaca data API...")

        # Close HTTP client
        await self._http_client.close()

        # TODO: Disconnect WebSocket client when implemented
        # if self._ws_client:
        #     await self._ws_client.disconnect()

        self._log.info("Disconnected from Alpaca data API", LogColor.GREEN)

    def _cache_instruments(self) -> None:
        """Cache instruments for parsing responses."""
        # Ensures instrument definitions are available for correct
        # price and size precisions when parsing responses
        instruments = list(self._instrument_provider.get_all().values())
        self._log.debug(f"Cached {len(instruments)} instruments", LogColor.MAGENTA)

    def _send_all_instruments_to_data_engine(self) -> None:
        """Send all instruments to the data engine."""
        for instrument in self._instrument_provider.get_all().values():
            self._handle_data(instrument)

        for currency in self._instrument_provider.currencies().values():
            self._cache.add_currency(currency)

        self._log.info(
            f"Sent {len(self._instrument_provider.get_all())} instruments to data engine",
            LogColor.BLUE,
        )

    # Subscription methods - TODO: Implement with WebSocket client

    async def _subscribe_instruments(self, command: SubscribeInstruments) -> None:
        """Subscribe to instrument updates."""
        self._log.warning(
            "Instrument subscriptions not yet implemented",
            LogColor.YELLOW,
        )

    async def _subscribe_instrument(self, command: SubscribeInstrument) -> None:
        """Subscribe to a specific instrument update."""
        self._log.warning(
            f"Instrument subscription for {command.instrument_id} not yet implemented",
            LogColor.YELLOW,
        )

    async def _subscribe_order_book_deltas(self, command: SubscribeOrderBook) -> None:
        """Subscribe to order book deltas."""
        self._log.warning(
            f"Order book delta subscription for {command.instrument_id} not yet implemented",
            LogColor.YELLOW,
        )

    async def _subscribe_order_book_snapshots(self, command: SubscribeOrderBook) -> None:
        """Subscribe to order book snapshots."""
        self._log.warning(
            f"Order book snapshot subscription for {command.instrument_id} not yet implemented",
            LogColor.YELLOW,
        )

    async def _subscribe_quote_ticks(self, command: SubscribeQuoteTicks) -> None:
        """Subscribe to quote ticks."""
        self._log.warning(
            f"Quote tick subscription for {command.instrument_id} not yet implemented",
            LogColor.YELLOW,
        )

    async def _subscribe_trade_ticks(self, command: SubscribeTradeTicks) -> None:
        """Subscribe to trade ticks."""
        self._log.warning(
            f"Trade tick subscription for {command.instrument_id} not yet implemented",
            LogColor.YELLOW,
        )

    # Unsubscription methods - TODO: Implement with WebSocket client

    async def _unsubscribe_instruments(self, command: UnsubscribeInstruments) -> None:
        """Unsubscribe from instrument updates."""
        self._log.debug("Unsubscribe instruments requested")

    async def _unsubscribe_instrument(self, command: UnsubscribeInstrument) -> None:
        """Unsubscribe from a specific instrument update."""
        self._log.debug(f"Unsubscribe instrument {command.instrument_id} requested")

    async def _unsubscribe_order_book_deltas(self, command: UnsubscribeOrderBook) -> None:
        """Unsubscribe from order book deltas."""
        self._log.debug(f"Unsubscribe order book deltas {command.instrument_id} requested")

    async def _unsubscribe_order_book_snapshots(self, command: UnsubscribeOrderBook) -> None:
        """Unsubscribe from order book snapshots."""
        self._log.debug(f"Unsubscribe order book snapshots {command.instrument_id} requested")

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        """Unsubscribe from quote ticks."""
        self._log.debug(f"Unsubscribe quote ticks {command.instrument_id} requested")

    async def _unsubscribe_trade_ticks(self, command: UnsubscribeTradeTicks) -> None:
        """Unsubscribe from trade ticks."""
        self._log.debug(f"Unsubscribe trade ticks {command.instrument_id} requested")

    async def _request_trade_ticks(self, request: RequestTradeTicks) -> None:

        print()
        self._http_client
