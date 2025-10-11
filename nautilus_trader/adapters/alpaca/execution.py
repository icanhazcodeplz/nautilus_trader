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

"""Execution client for Alpaca adapter."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.adapters.alpaca.parsing import AlpacaEnumParser
from nautilus_trader.adapters.alpaca.parsing import parse_order_status_report
from nautilus_trader.adapters.alpaca.websocket import AlpacaWebSocketClient
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core.uuid import UUID4

from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.execution.reports import PositionStatusReport
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.identifiers import AccountId, Symbol
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import Money, AccountBalance, MarginBalance

if TYPE_CHECKING:
    import asyncio

    from nautilus_trader.adapters.alpaca.config import AlpacaExecClientConfig
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus
    from nautilus_trader.execution.messages import CancelAllOrders
    from nautilus_trader.execution.messages import CancelOrder
    from nautilus_trader.execution.messages import GenerateFillReports
    from nautilus_trader.execution.messages import GenerateOrderStatusReport
    from nautilus_trader.execution.messages import GenerateOrderStatusReports
    from nautilus_trader.execution.messages import GeneratePositionStatusReports
    from nautilus_trader.execution.messages import ModifyOrder
    from nautilus_trader.execution.messages import QueryAccount
    from nautilus_trader.execution.messages import SubmitOrder


ALPACA_VENUE = Venue("ALPACA")


class AlpacaExecutionClient(LiveExecutionClient):
    """
    Provides an execution client for the Alpaca broker.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    msgbus : MessageBus
        The message bus for the client.
    cache : Cache
        The cache for the client.
    clock : LiveClock
        The clock for the client.
    config : AlpacaExecClientConfig
        The configuration for the client.
    name : str, optional
        The custom client ID.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        config: AlpacaExecClientConfig,
        instrument_provider: InstrumentProvider | None = None,
        name: str | None = None,
    ) -> None:
        # Determine account type
        # Alpaca uses a cash account for stocks
        account_type = AccountType.CASH

        super().__init__(
            loop=loop,
            client_id=ClientId(name or ALPACA_VENUE.value),
            venue=ALPACA_VENUE,
            oms_type=OmsType.NETTING,
            instrument_provider=instrument_provider,  # Will be set separately if needed
            account_type=account_type,
            base_currency=None,  # Will be determined from account
            msgbus=msgbus,
            cache=cache,
            clock=clock,
        )

        # Configuration
        self._environment = config.environment
        self._http_timeout = config.http_timeout
        self._max_retries = config.max_retries or 3

        # API credentials
        self._api_key = config.api_key or os.getenv("ALPACA_API_KEY")
        self._api_secret = config.api_secret or os.getenv("ALPACA_API_SECRET")

        if not self._api_key or not self._api_secret:
            self._log.warning("Alpaca API credentials not provided")

        # URLs
        if config.http_base_url:
            self._http_base_url = config.http_base_url
        elif self._environment == "live":
            self._http_base_url = "https://api.alpaca.markets"
        else:
            self._http_base_url = "https://paper-api.alpaca.markets"

        # FIXME: BRENT: need to adjust for paper trading
        if config.ws_base_url:
            self._ws_base_url = config.ws_base_url
        else:
            self._ws_base_url = "wss://api.alpaca.markets/stream"

        # Set account ID
        account_id = AccountId(f"{name or ALPACA_VENUE.value}-{self._environment.upper()}")
        self._set_account_id(account_id)

        self._log.info(f"Account type: {account_type}", LogColor.BLUE)
        self._log.info(f"Environment: {self._environment}", LogColor.BLUE)
        self._log.info(f"HTTP base URL: {self._http_base_url}", LogColor.BLUE)
        self._log.info(f"WS base URL: {self._ws_base_url}", LogColor.BLUE)

        # Initialize HTTP client
        self._http_client = AlpacaHttpClient(
            base_url=self._http_base_url,
            api_key=self._api_key,
            api_secret=self._api_secret,
            timeout=self._http_timeout,
            logger=self._log,
        )

        # Initialize enum parser
        self._enum_parser = AlpacaEnumParser()

        # Initialize WebSocket client for order updates
        self._ws_client = AlpacaWebSocketClient(
            url=self._ws_base_url,
            api_key=self._api_key,
            api_secret=self._api_secret,
            handler=self._handle_ws_message,
            logger=self._log,
        )

        self._instrument_provider.load_all()
    #     BRENT. Instruments loaded here

    @property
    def instrument_provider(self):
        """
        Return the instrument provider for the client.

        Returns
        -------
        BetfairInstrumentProvider

        """
        return self._instrument_provider

    async def _connect(self) -> None:
        """Connect to Alpaca API."""
        self._log.info("Connecting to Alpaca...")

        # Verify credentials by making a test API call
        try:
            account_data = await self._http_client.get_account()
            self._log.info(f"Alpaca account: {account_data.get('account_number')}", LogColor.GREEN)
            self._log.info(f"Account status: {account_data.get('status')}", LogColor.BLUE)
        except Exception as e:
            self._log.error(f"Failed to connect to Alpaca: {e}")
            raise

        # Update account state
        await self._update_account_state()

        # Connect to WebSocket and subscribe to trade updates
        # try:
        await self._ws_client.connect()
        await self._ws_client.subscribe_trade_updates()
        self._log.info("Subscribed to trade updates", LogColor.GREEN)
        # except Exception as e:
        #     self._log.error(f"Failed to connect to WebSocket: {e}")
        #     # Don't fail completely if WebSocket fails, can still use HTTP polling
        #     self._log.warning("Continuing without WebSocket updates")

        self._log.info("Connected to Alpaca", LogColor.GREEN)

    async def _disconnect(self) -> None:
        """Disconnect from Alpaca API."""
        self._log.info("Disconnecting from Alpaca...")

        # Close WebSocket client
        await self._ws_client.disconnect()

        # Close HTTP client
        await self._http_client.close()

        self._log.info("Disconnected from Alpaca")

    async def _update_account_state(self) -> None:
        """Update account state from Alpaca API."""
        # try:
        account_data = await self._http_client.get_account()

        # Parse account balances
        cash = Money.from_str(f"{account_data['cash']} USD")
        # buying_power = Money.from_str(f"{account_data['buying_power']} USD")
        equity = Money.from_str(f"{account_data['equity']} USD")
        zero_usd = Money(0.00, USD)
        balances = [AccountBalance(total=cash, locked=zero_usd, free=cash)]
        margins = [MarginBalance(zero_usd, zero_usd)]

        # Generate account state
        self.generate_account_state(
            balances=balances,
            margins=margins,
            reported=True,
            ts_event=self._clock.timestamp_ns(),
        )

        self._log.info(f"Updated account state: Equity=${equity}", LogColor.BLUE)
        # except Exception as e:
        #     self._log.error(f"Failed to update account state: {e}")

    # -- EXECUTION REPORTS --------------------------------------------------------------------

    async def generate_order_status_report(
        self,
        command: GenerateOrderStatusReport,
    ) -> OrderStatusReport | None:
        """
        Generate an order status report for a specific order.

        Parameters
        ----------
        command : GenerateOrderStatusReport
            The command to generate the report.

        Returns
        -------
        OrderStatusReport or None
            The order status report, or None if the order is not found.

        """
        self._log.debug(f"Generating OrderStatusReport for {command.client_order_id}")

        try:
            # Try to get venue_order_id from cache
            venue_order_id = command.venue_order_id
            if not venue_order_id and command.client_order_id:
                venue_order_id = self._cache.venue_order_id(command.client_order_id)

            if not venue_order_id:
                self._log.warning(f"Cannot find venue_order_id for {command.client_order_id}")
                return None

            # Query Alpaca API for order status
            alpaca_order = await self._http_client.get_order(venue_order_id.value)

            # Parse response into OrderStatusReport
            report = parse_order_status_report(
                alpaca_order=alpaca_order,
                account_id=self.account_id,
                instrument_id=command.instrument_id,
                ts_init=self._clock.timestamp_ns(),
            )

            self._log.debug(f"Generated {report}")
            return report

        except Exception as e:
            self._log.error(f"Failed to generate OrderStatusReport: {e}")
            return None

    async def generate_order_status_reports(
        self,
        command: GenerateOrderStatusReports,
    ) -> list[OrderStatusReport]:
        """
        Generate order status reports.

        Parameters
        ----------
        command : GenerateOrderStatusReports
            The command to generate reports.

        Returns
        -------
        list[OrderStatusReport]
            The order status reports.

        """
        self._log.debug("Generating OrderStatusReports...")

        reports: list[OrderStatusReport] = []

        try:
            # Determine status filter
            status = "open" if command.open_only else "all"

            # Query Alpaca API for orders
            alpaca_orders = await self._http_client.get_orders(status=status, limit=500)

            # Parse responses into OrderStatusReport objects
            for alpaca_order in alpaca_orders:
                try:
                    # Get instrument ID from symbol
                    symbol = alpaca_order["symbol"]
                    instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA_VENUE}")

                    # Parse order
                    report = parse_order_status_report(
                        alpaca_order=alpaca_order,
                        account_id=self.account_id,
                        instrument_id=instrument_id,
                        ts_init=self._clock.timestamp_ns(),
                    )

                    reports.append(report)
                    self._log.debug(f"Generated {report}")

                except Exception as e:
                    self._log.error(f"Failed to parse order {alpaca_order.get('id')}: {e}")

            self._log.info(f"Generated {len(reports)} OrderStatusReports")

        except Exception as e:
            self._log.error(f"Failed to generate OrderStatusReports: {e}")

        return reports

    async def generate_fill_reports(
        self,
        command: GenerateFillReports,
    ) -> list[FillReport]:
        """
        Generate fill reports.

        Parameters
        ----------
        command : GenerateFillReports
            The command to generate reports.

        Returns
        -------
        list[FillReport]
            The fill reports.

        """
        self._log.debug("Generating FillReports...")

        reports: list[FillReport] = []

        # TODO: Query Alpaca API for trade history
        # TODO: Parse responses into FillReport objects
        # TODO: Return the reports

        return reports

    async def generate_position_status_reports(
        self,
        command: GeneratePositionStatusReports,
    ) -> list[PositionStatusReport]:
        """
        Generate position status reports.

        Parameters
        ----------
        command : GeneratePositionStatusReports
            The command to generate reports.

        Returns
        -------
        list[PositionStatusReport]
            The position status reports.

        """
        self._log.debug("Generating PositionStatusReports...")

        reports: list[PositionStatusReport] = []

        # TODO: Query Alpaca API for positions
        # TODO: Parse responses into PositionStatusReport objects
        # TODO: Return the reports

        return reports

    # -- COMMAND HANDLERS ---------------------------------------------------------------------

    async def _submit_order(self, command: SubmitOrder) -> None:
        """
        Submit an order to Alpaca.

        Parameters
        ----------
        command : SubmitOrder
            The command to submit the order.

        """
        order = command.order

        if order.is_closed:
            self._log.warning(f"Order {order} is already closed")
            return

        self._log.info(f"Submitting order: {order}")

        # Generate order submitted event
        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )

        try:
            # Convert NautilusTrader order to Alpaca order format
            order_request = self._build_order_request(order)

            # Submit order via HTTP API
            alpaca_order = await self._http_client.submit_order(order_request)

            # Generate order accepted event
            venue_order_id = VenueOrderId(alpaca_order["id"])
            self.generate_order_accepted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=venue_order_id,
                ts_event=self._clock.timestamp_ns(),
            )

            self._log.info(f"Order accepted: {venue_order_id}")

        except Exception as e:
            self._log.error(f"Failed to submit order: {e}")
            self.generate_order_rejected(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                reason=str(e),
                ts_event=self._clock.timestamp_ns(),
            )

    def _build_order_request(self, order) -> dict:
        """Build Alpaca order request from NautilusTrader order."""
        # Extract symbol (remove venue suffix)
        symbol = order.instrument_id.symbol.value.split(".")[0]

        # Convert order side
        side = self._enum_parser.parse_nautilus_order_side(order.side)

        # Convert order type
        order_type = self._enum_parser.parse_nautilus_order_type(order.order_type)

        # Convert time in force
        time_in_force = self._enum_parser.parse_nautilus_time_in_force(order.time_in_force)

        # Build base request
        request = {
            "symbol": symbol,
            "qty": str(order.quantity),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "client_order_id": order.client_order_id.value,
        }

        # Add limit price if applicable
        if order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            request["limit_price"] = str(order.price)

        # Add stop price if applicable
        if order.order_type == OrderType.STOP_LIMIT:
            request["stop_price"] = str(order.trigger_price)

        # Add trail for trailing stop orders
        if order.order_type == OrderType.TRAILING_STOP_MARKET:
            request["trail_percent"] = str(order.trailing_offset_pct)

        return request

    async def _modify_order(self, command: ModifyOrder) -> None:
        """
        Modify an order on Alpaca.

        Parameters
        ----------
        command : ModifyOrder
            The command to modify the order.

        """
        self._log.info(f"Modifying order: {command.client_order_id}")

        # TODO: Get order from cache
        # TODO: Modify order via HTTP API
        # TODO: Handle response and generate appropriate events
        #       (OrderUpdated, OrderModifyRejected, etc.)

    async def _cancel_order(self, command: CancelOrder) -> None:
        """
        Cancel an order on Alpaca.

        Parameters
        ----------
        command : CancelOrder
            The command to cancel the order.

        """
        self._log.info(f"Canceling order: {command.client_order_id}")

        try:
            # Get order from cache
            order = self._cache.order(command.client_order_id)
            if not order:
                self._log.error(f"Order {command.client_order_id} not found in cache")
                return

            if order.is_closed:
                self._log.warning(f"Order {command.client_order_id} is already closed")
                return

            # Get venue order ID
            venue_order_id = command.venue_order_id or self._cache.venue_order_id(command.client_order_id)
            if not venue_order_id:
                self._log.error(f"No venue_order_id found for {command.client_order_id}")
                return

            # Cancel order via HTTP API
            await self._http_client.cancel_order(venue_order_id.value)

            # Generate order canceled event
            self.generate_order_canceled(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=venue_order_id,
                ts_event=self._clock.timestamp_ns(),
            )

            self._log.info(f"Order canceled: {venue_order_id}")

        except Exception as e:
            self._log.error(f"Failed to cancel order: {e}")
            if order:
                self.generate_order_cancel_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    venue_order_id=venue_order_id if venue_order_id else None,
                    reason=str(e),
                    ts_event=self._clock.timestamp_ns(),
                )

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        """
        Cancel all orders for an instrument on Alpaca.

        Parameters
        ----------
        command : CancelAllOrders
            The command to cancel all orders.

        """
        self._log.info(f"Canceling all orders for {command.instrument_id}")

        try:
            # Cancel all orders via HTTP API
            # Note: Alpaca cancels ALL orders, not just for a specific instrument
            results = await self._http_client.cancel_all_orders()

            self._log.info(f"Canceled {len(results)} orders")

            # Generate events for each canceled order
            for result in results:
                try:
                    venue_order_id = VenueOrderId(result["id"])
                    client_order_id_str = result.get("client_order_id")

                    if client_order_id_str:
                        client_order_id = ClientOrderId(client_order_id_str)
                        order = self._cache.order(client_order_id)

                        if order:
                            self.generate_order_canceled(
                                strategy_id=order.strategy_id,
                                instrument_id=order.instrument_id,
                                client_order_id=client_order_id,
                                venue_order_id=venue_order_id,
                                ts_event=self._clock.timestamp_ns(),
                            )
                except Exception as e:
                    self._log.error(f"Failed to generate cancel event for order {result.get('id')}: {e}")

        except Exception as e:
            self._log.error(f"Failed to cancel all orders: {e}")

    async def _query_account(self, command: QueryAccount) -> None:
        """
        Query account information from Alpaca.

        Parameters
        ----------
        command : QueryAccount
            The command to query the account.

        """
        self._log.debug("Querying account...")

        # Query account via HTTP API and update account state
        await self._update_account_state()

    # -- WEBSOCKET HANDLERS -------------------------------------------------------------------

    def _handle_ws_message(self, msg: dict) -> None:
        """
        Handle an incoming WebSocket trade update message.

        Parameters
        ----------
        msg : dict
            The trade update message.

        """
        try:
            # Extract event type and order data
            event = msg.get("event")
            order_data = msg.get("order", {})

            if not event or not order_data:
                self._log.warning(f"Invalid trade update message: {msg}")
                return

            # Extract order identifiers
            venue_order_id_str = order_data.get("id")
            client_order_id_str = order_data.get("client_order_id")

            if not venue_order_id_str:
                self._log.warning("Trade update missing order ID")
                return

            venue_order_id = VenueOrderId(venue_order_id_str)
            client_order_id = ClientOrderId(client_order_id_str) if client_order_id_str else None

            # Try to get client_order_id from cache if not in message
            if not client_order_id:
                client_order_id = self._cache.client_order_id(venue_order_id)

            if not client_order_id:
                self._log.debug(f"Cannot process trade update: no client_order_id for {venue_order_id}")
                return

            # Get order from cache
            order = self._cache.order(client_order_id)
            if not order:
                self._log.warning(f"Order {client_order_id} not found in cache")
                return

            # Get instrument ID
            symbol = order_data.get("symbol")
            if symbol:
                instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA_VENUE}")
            else:
                instrument_id = order.instrument_id

            # Handle different event types
            ts_event = self._clock.timestamp_ns()

            if event == "new":
                # Order accepted
                self.generate_order_accepted(
                    strategy_id=order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=venue_order_id,
                    ts_event=ts_event,
                )

            elif event == "fill":
                # Order filled
                filled_qty_str = order_data.get("filled_qty", "0")
                filled_avg_price_str = order_data.get("filled_avg_price")

                if filled_avg_price_str:
                    # Generate fill event
                    from nautilus_trader.model.objects import Price, Quantity
                    from nautilus_trader.model.identifiers import TradeId
                    from nautilus_trader.model.enums import LiquiditySide

                    last_qty = Quantity.from_str(filled_qty_str)
                    last_px = Price.from_str(filled_avg_price_str)

                    # Commission is 0 for Alpaca
                    commission = Money(0, instrument_id.symbol.value.split(".")[0].split("/")[-1] if "/" in instrument_id.symbol.value else "USD")

                    self.generate_order_filled(
                        strategy_id=order.strategy_id,
                        instrument_id=instrument_id,
                        client_order_id=client_order_id,
                        venue_order_id=venue_order_id,
                        venue_position_id=None,
                        trade_id=TradeId(venue_order_id_str),  # Use order ID as trade ID
                        order_side=order.side,
                        order_type=order.order_type,
                        last_qty=last_qty,
                        last_px=last_px,
                        quote_currency=instrument_id.symbol.value.split(".")[ 0].split("/")[-1] if "/" in instrument_id.symbol.value else "USD",
                        commission=commission,
                        liquidity_side=LiquiditySide.TAKER,
                        ts_event=ts_event,
                    )

            elif event == "partial_fill":
                # Order partially filled
                self._log.info(f"Order {client_order_id} partially filled")
                # Could generate partial fill event here if needed

            elif event == "canceled":
                # Order canceled
                self.generate_order_canceled(
                    strategy_id=order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=venue_order_id,
                    ts_event=ts_event,
                )

            elif event == "rejected":
                # Order rejected
                reason = order_data.get("reject_reason", "Unknown")
                self.generate_order_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=client_order_id,
                    reason=reason,
                    ts_event=ts_event,
                )

            elif event == "replaced":
                # Order replaced (modified)
                self._log.info(f"Order {client_order_id} replaced")
                # Could generate order updated event here

            else:
                self._log.debug(f"Unhandled trade update event: {event}")

        except Exception as e:
            self._log.error(f"Error handling WebSocket message: {e}")
