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
from typing import TYPE_CHECKING, Any

from nautilus_trader.adapters.alpaca.constants import ALPACA_VENUE
from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.adapters.alpaca.providers import AlpacaInstrumentProvider
from nautilus_trader.adapters.alpaca.utils import alpaca_date_str_to_nanos
from nautilus_trader.adapters.alpaca.websocket import AlpacaMarketDataWebSocketClient
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.enums import LogColor
from nautilus_trader.adapters.alpaca.utils import dt_to_iso_8601

from nautilus_trader.live.config import LiveDataClientConfig
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarAggregation
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


if TYPE_CHECKING:
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus
    from nautilus_trader.data.messages import RequestTradeTicks, RequestBars
    from nautilus_trader.data.messages import SubscribeInstrument
    from nautilus_trader.data.messages import SubscribeInstruments
    from nautilus_trader.data.messages import SubscribeOrderBook
    from nautilus_trader.data.messages import SubscribeQuoteTicks
    from nautilus_trader.data.messages import SubscribeTradeTicks
    from nautilus_trader.data.messages import SubscribeBars
    from nautilus_trader.data.messages import UnsubscribeInstrument
    from nautilus_trader.data.messages import UnsubscribeInstruments
    from nautilus_trader.data.messages import UnsubscribeOrderBook
    from nautilus_trader.data.messages import UnsubscribeQuoteTicks
    from nautilus_trader.data.messages import UnsubscribeTradeTicks
    from nautilus_trader.data.messages import UnsubscribeBars


class AlpacaDataClientConfig(LiveDataClientConfig, frozen=True):
    """
    Configuration for ``AlpacaDataClient`` instances.

    Parameters
    ----------
    feed : str, default "iex"
        The market data feed: "iex" or "sip".
    http_timeout : PositiveInt, default 30
        The timeout (seconds) for HTTP requests.
    update_instruments_interval_mins : PositiveInt or None, default 60
        The interval (minutes) between reloading instruments from the venue.

    """

    paper: bool = True
    feed: str = "iex"
    http_timeout: PositiveInt = 30
    update_instruments_interval_mins: PositiveInt | None = 60


class AlpacaDataClient(LiveMarketDataClient):
    """
    Provides a data client for Alpaca Markets.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    http_client : AlpacaHttpClient
        The Alpaca HTTP client.
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

        self._config = config

        # http_client used to request historical data
        self._http_client = http_client

        # Map symbol -> BarType for WS bar subscriptions
        self._bar_type_by_symbol: dict[str, BarType] = {}

        # Initialize WebSocket client for market data streaming
        self._ws_client = AlpacaMarketDataWebSocketClient(
            paper=config.paper,
            feed=config.feed,
            handler=self._handle_ws_message,
            logger=self._log,
        )

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

        # Connect WebSocket client for market data streaming
        if self._ws_client:
            try:
                await self._ws_client.connect()
                self._log.info("WebSocket market data streaming connected", LogColor.GREEN)
            except Exception as e:
                self._log.error(f"Failed to connect WebSocket client: {e}", LogColor.RED)
                self._log.warning(
                    "WebSocket streaming unavailable, only historical data requests will work",
                    LogColor.YELLOW,
                )
        else:
            self._log.warning(
                "WebSocket client not initialized. Only historical data requests will work.",
                LogColor.YELLOW,
            )

        self._log.info("Connected to Alpaca data API", LogColor.GREEN)

    async def _disconnect(self) -> None:
        """Disconnect from Alpaca data streams."""
        self._log.info("Disconnecting from Alpaca data API...")

        # Disconnect WebSocket client
        if self._ws_client:
            try:
                await self._ws_client.disconnect()
                self._log.info("WebSocket client disconnected", LogColor.GREEN)
            except Exception as e:
                self._log.error(f"Error disconnecting WebSocket client: {e}", LogColor.RED)

        # Close HTTP client
        await self._http_client.close()

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

    def _handle_ws_message(self, msg: dict[str, Any]) -> None:
        """
        Handle an incoming WebSocket message.

        Parameters
        ----------
        msg : dict
            The market data message.

        """
        msg_type = msg.get("T")

        try:
            if msg_type == "t":
                # Trade message
                self._handle_trade_message(msg)
            elif msg_type == "q":
                # Quote message
                self._handle_quote_message(msg)
            elif msg_type == "b":
                # Bar message
                self._handle_bar_message(msg)
            else:
                self._log.warning(f"Unknown market data message type: {msg_type}")
        except Exception as e:
            self._log.error(f"Error handling market data message: {e}")

    def _trade_msg_to_tradetick(self, instrument_id: InstrumentId, msg: dict[str, Any]) -> TradeTick | None:
        """Convert an Alpaca trade message to a TradeTick."""
        # Skip FINRA market data messages
        if msg["x"] == "D":
            return

        # Skip zero qty trades
        qty_obj = Quantity.from_str(str(msg["s"]))
        if qty_obj == 0:
            return

        ts_event = alpaca_date_str_to_nanos(msg["t"])
        ts_init = self._clock.timestamp_ns()

        # Create TradeTick
        trade = TradeTick(
            instrument_id=instrument_id,
            price=Price.from_str(str(msg["p"])),
            size=qty_obj,
            aggressor_side=AggressorSide.NO_AGGRESSOR,  # Alpaca doesn't provide this
            trade_id=TradeId(str(msg["i"])),
            ts_event=ts_event,
            ts_init=ts_init,
        )
        return trade

    def _handle_trade_message(self, msg: dict[str, Any]) -> None:
        """Parse and handle a trade message."""
        try:
            # Create instrument ID from symbol
            symbol = msg["S"]
            instrument_id = InstrumentId.from_str(f"{symbol}.ALPACA")
            trade = self._trade_msg_to_tradetick(instrument_id, msg)

            # Send to data engine
            if trade is not None:
                self._handle_data(trade)

        except Exception as e:
            self._log.error(f"Error parsing trade message: {e}")

    def _handle_quote_message(self, msg: dict[str, Any]) -> None:
        """Parse and handle a quote message."""
        try:
            # Extract symbol and create instrument ID
            symbol = msg["S"]
            instrument_id = InstrumentId.from_str(f"{symbol}.ALPACA")

            # Get instrument for validation
            instrument = self._cache.instrument(instrument_id)
            if instrument is None:
                self._log.warning(f"Received quote for unknown instrument: {instrument_id}")
                return

            ts_event = alpaca_date_str_to_nanos(msg["t"])
            ts_init = self._clock.timestamp_ns()

            # Create QuoteTick

            quote = QuoteTick(
                instrument_id=instrument_id,
                bid_price=instrument.make_price(msg["bp"]),
                ask_price=instrument.make_price(msg["ap"]),
                bid_size=instrument.make_qty(msg["bs"]),
                ask_size=instrument.make_qty(msg["as"]),
                ts_event=ts_event,
                ts_init=ts_init,
            )

            # Send to data engine
            self._handle_data(quote)

        except Exception as e:
            self._log.error(f"Error parsing quote message: {e}")

    def _handle_bar_message(self, msg: dict[str, Any]) -> None:
        """Parse and handle a bar message."""
        try:
            symbol = msg["S"]
            bar_type = self._bar_type_by_symbol.get(symbol)
            if bar_type is None:
                self._log.warning(f"Received bar for untracked symbol: {symbol}")
                return

            ts_event = alpaca_date_str_to_nanos(msg["t"])

            bar = Bar(
                bar_type=bar_type,
                open=Price.from_str(str(msg["o"])),
                high=Price.from_str(str(msg["h"])),
                low=Price.from_str(str(msg["l"])),
                close=Price.from_str(str(msg["c"])),
                volume=Quantity.from_str(str(msg["v"])),
                ts_event=ts_event,
                ts_init=self._clock.timestamp_ns(),
            )

            self._handle_data(bar)

        except Exception as e:
            self._log.error(f"Error parsing bar message: {e}")

    # Subscription methods

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
        if not self._ws_client or not self._ws_client.is_connected:
            self._log.error(
                f"Cannot subscribe to quote ticks for {command.instrument_id}: WebSocket not connected",
                LogColor.RED,
            )
            return

        # Extract symbol from instrument_id (format: SYMBOL.ALPACA)
        symbol = command.instrument_id.symbol.value

        try:
            await self._ws_client.subscribe(quotes=[symbol])
            self._log.info(
                f"Subscribed to quote ticks for {command.instrument_id}",
                LogColor.GREEN,
            )
        except Exception as e:
            self._log.error(
                f"Failed to subscribe to quote ticks for {command.instrument_id}: {e}",
                LogColor.RED,
            )

    async def _subscribe_trade_ticks(self, command: SubscribeTradeTicks) -> None:
        """Subscribe to trade ticks."""
        if not self._ws_client or not self._ws_client.is_connected:
            self._log.error(
                f"Cannot subscribe to trade ticks for {command.instrument_id}: WebSocket not connected",
                LogColor.RED,
            )
            return

        # Extract symbol from instrument_id (format: SYMBOL.ALPACA)
        symbol = command.instrument_id.symbol.value

        try:
            await self._ws_client.subscribe(trades=[symbol])
            self._log.info(
                f"Subscribed to trade ticks for {command.instrument_id}",
                LogColor.GREEN,
            )
        except Exception as e:
            self._log.error(
                f"Failed to subscribe to trade ticks for {command.instrument_id}: {e}",
                LogColor.RED,
            )

    # Unsubscription methods

    async def _unsubscribe_instruments(self, command: UnsubscribeInstruments) -> None:
        """Unsubscribe from instrument updates."""
        self._log.debug("Unsubscribe instruments requested (not implemented)")

    async def _unsubscribe_instrument(self, command: UnsubscribeInstrument) -> None:
        """Unsubscribe from a specific instrument update."""
        self._log.debug(f"Unsubscribe instrument {command.instrument_id} requested (not implemented)")

    async def _unsubscribe_order_book_deltas(self, command: UnsubscribeOrderBook) -> None:
        """Unsubscribe from order book deltas."""
        self._log.debug(f"Unsubscribe order book deltas {command.instrument_id} requested (not implemented)")

    async def _unsubscribe_order_book_snapshots(self, command: UnsubscribeOrderBook) -> None:
        """Unsubscribe from order book snapshots."""
        self._log.debug(f"Unsubscribe order book snapshots {command.instrument_id} requested (not implemented)")

    async def _unsubscribe_quote_ticks(self, command: UnsubscribeQuoteTicks) -> None:
        """Unsubscribe from quote ticks."""
        if not self._ws_client or not self._ws_client.is_connected:
            self._log.debug(f"Cannot unsubscribe from quote ticks for {command.instrument_id}: WebSocket not connected")
            return

        # Extract symbol from instrument_id (format: SYMBOL.ALPACA)
        symbol = command.instrument_id.symbol.value

        try:
            await self._ws_client.unsubscribe(quotes=[symbol])
            self._log.info(f"Unsubscribed from quote ticks for {command.instrument_id}")
        except Exception as e:
            self._log.error(f"Failed to unsubscribe from quote ticks for {command.instrument_id}: {e}")

    async def _unsubscribe_trade_ticks(self, command: UnsubscribeTradeTicks) -> None:
        """Unsubscribe from trade ticks."""
        if not self._ws_client or not self._ws_client.is_connected:
            self._log.debug(f"Cannot unsubscribe from trade ticks for {command.instrument_id}: WebSocket not connected")
            return

        # Extract symbol from instrument_id (format: SYMBOL.ALPACA)
        symbol = command.instrument_id.symbol.value

        try:
            await self._ws_client.unsubscribe(trades=[symbol])
            self._log.info(f"Unsubscribed from trade ticks for {command.instrument_id}")
        except Exception as e:
            self._log.error(f"Failed to unsubscribe from trade ticks for {command.instrument_id}: {e}")

    async def _subscribe_bars(self, command: SubscribeBars) -> None:
        """Subscribe to bars via WebSocket."""
        if not self._ws_client or not self._ws_client.is_connected:
            self._log.error(
                f"Cannot subscribe to bars for {command.bar_type}: WebSocket not connected",
                LogColor.RED,
            )
            return

        symbol = command.bar_type.instrument_id.symbol.value

        # Store the bar type so the WS handler can construct Bar objects
        self._bar_type_by_symbol[symbol] = command.bar_type

        try:
            await self._ws_client.subscribe(bars=[symbol])
            self._log.info(
                f"Subscribed to bars for {command.bar_type}",
                LogColor.GREEN,
            )
        except Exception as e:
            self._log.error(
                f"Failed to subscribe to bars for {command.bar_type}: {e}",
                LogColor.RED,
            )

    async def _unsubscribe_bars(self, command: UnsubscribeBars) -> None:
        """Unsubscribe from bars."""
        if not self._ws_client or not self._ws_client.is_connected:
            self._log.debug(f"Cannot unsubscribe from bars for {command.bar_type}: WebSocket not connected")
            return

        symbol = command.bar_type.instrument_id.symbol.value

        try:
            await self._ws_client.unsubscribe(bars=[symbol])
            self._bar_type_by_symbol.pop(symbol, None)
            self._log.info(f"Unsubscribed from bars for {command.bar_type}")
        except Exception as e:
            self._log.error(f"Failed to unsubscribe from bars for {command.bar_type}: {e}")

    async def _request_trade_ticks(self, request: RequestTradeTicks) -> None:
        """Request historical trade ticks."""
        # Extract symbol from instrument_id (format: SYMBOL.ALPACA)
        symbol = request.instrument_id.symbol.value

        # Get the instrument for precision
        instrument = self._cache.instrument(request.instrument_id)
        if instrument is None:
            self._log.error(
                f"Cannot request trades for unknown instrument {request.instrument_id}",
            )
            return

        # Prepare request parameters
        limit = request.limit

        # Because we filter out finra trades, need to request some larger number and then ensure we still have enough
        # after filtering is complete.
        if limit is not None:
            if limit > 10000:
                raise NotImplementedError(
                    f"Have not implemented pagination for trade ticks yet, limit {limit} exceeds maximum of 10000"
                )
            # WARNING, setting to 10000 to ensure we get enough ticks after filtering for finra trades
            request_limit = 10000
        else:
            request_limit = None

        start_str = dt_to_iso_8601(request.start) if request.start else None
        end_str = dt_to_iso_8601(request.end) if request.end else None

        # Request trades from Alpaca API
        # Tried to make this `sort` var more intelligent, for example, checking if start or end were None, but Start is
        # required by NT to be non-None, and end is set to current time if None in NT code! Hoping 'desc' is okay
        sort = "desc"  # vs 'asc'
        try:
            response = await self._http_client.get_trades(
                symbol=symbol,
                start=start_str,
                end=end_str,
                limit=request_limit,
                feed=self._config.feed,
                sort=sort,
            )
        except Exception as exc:
            self._log.exception(f"Failed to request trades for {request.instrument_id}", exc)
            return

        # Parse trades from response
        trades_data = response.get("trades", [])
        if not trades_data:
            self._log.info(
                f"No trades returned for {request.instrument_id}",
            )
            # Still call handler with empty list
            self._handle_trade_ticks(
                request.instrument_id,
                [],
                request.id,
                request.start,
                request.end,
                request.params,
            )
            return
        if sort == "desc":
            trades_data.reverse()
        # Convert to TradeTick objects
        trades = []
        for trade_data in trades_data:
            try:
                tradetick = self._trade_msg_to_tradetick(request.instrument_id, trade_data)
                if tradetick is not None:
                    trades.append(tradetick)
            except Exception as exc:
                self._log.warning(f"Failed to parse trade data: {trade_data}", exc)
                continue
        if len(trades) < limit:
            raise ValueError(
                f"Expected at least {limit} trades, got {len(trades)} after filtering out zero qty and finra trades"
            )
        trades = trades[-limit:]

        self._log.info(f"Received {len(trades)} trades for {request.instrument_id}")

        # Send trades to data engine
        self._handle_trade_ticks(request.instrument_id, trades, request.id, request.start, request.end, request.params)

    # -- Bar aggregation to Alpaca timeframe mapping ---

    _AGGREGATION_TO_ALPACA_UNIT: dict = {
        BarAggregation.MINUTE: "Min",
        BarAggregation.HOUR: "Hour",
        BarAggregation.DAY: "Day",
        BarAggregation.WEEK: "Week",
        BarAggregation.MONTH: "Month",
    }

    async def _request_bars(self, request: RequestBars) -> None:
        """Request historical bars."""
        bar_type = request.bar_type

        # Validate aggregation source
        if bar_type.is_internally_aggregated():
            self._log.error(
                f"Cannot request internally aggregated bars from Alpaca: {bar_type}",
            )
            return

        # Validate price type
        if bar_type.spec.price_type != PriceType.LAST:
            self._log.error(
                f"Only LAST price type available from Alpaca, got {bar_type.spec.price_type}",
            )
            return

        # Validate time-based aggregation
        if not bar_type.spec.is_time_aggregated():
            self._log.error(
                f"Only time-based bar aggregations supported from Alpaca, got {bar_type.spec}",
            )
            return

        # Map aggregation to Alpaca timeframe string
        unit = self._AGGREGATION_TO_ALPACA_UNIT.get(bar_type.spec.aggregation)
        if unit is None:
            self._log.error(
                f"Unsupported bar aggregation for Alpaca: {bar_type.spec.aggregation}",
            )
            return

        timeframe = f"{bar_type.spec.step}{unit}"

        symbol = bar_type.instrument_id.symbol.value
        instrument = self._cache.instrument(bar_type.instrument_id)
        if instrument is None:
            self._log.error(f"Cannot request bars for unknown instrument {bar_type.instrument_id}")
            return

        start_str = dt_to_iso_8601(request.start) if request.start else None
        end_str = dt_to_iso_8601(request.end) if request.end else None
        limit = request.limit

        try:
            response = await self._http_client.get_bars(
                symbol=symbol,
                timeframe=timeframe,
                start=start_str,
                end=end_str,
                limit=limit,
                feed=self._config.feed,
                sort="asc",
            )
        except Exception as exc:
            self._log.exception(f"Failed to request bars for {bar_type}", exc)
            return

        bars_data = response.get("bars", [])
        if not bars_data:
            self._log.info(f"No bars returned for {bar_type}")
            self._handle_bars(bar_type, [], request.id, request.start, request.end, request.params)
            return

        bars = []
        for raw in bars_data:
            try:
                ts_event = alpaca_date_str_to_nanos(raw["t"])
                bar = Bar(
                    bar_type=bar_type,
                    open=Price.from_str(str(raw["o"])),
                    high=Price.from_str(str(raw["h"])),
                    low=Price.from_str(str(raw["l"])),
                    close=Price.from_str(str(raw["c"])),
                    volume=Quantity.from_str(str(raw["v"])),
                    ts_event=ts_event,
                    ts_init=ts_event,
                )
                bars.append(bar)
            except Exception as exc:
                self._log.warning(f"Failed to parse bar data: {raw}", exc)
                continue

        self._log.info(f"Received {len(bars)} bars for {bar_type}")

        self._handle_bars(bar_type, bars, request.id, request.start, request.end, request.params)
