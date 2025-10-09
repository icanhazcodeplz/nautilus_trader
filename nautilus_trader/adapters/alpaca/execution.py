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

from nautilus_trader.common.enums import LogColor
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.execution.reports import PositionStatusReport
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue


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
            instrument_provider=None,  # Will be set separately if needed
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

        # TODO: Initialize HTTP client (could use Rust client via PyO3 or Python requests)
        # TODO: Initialize WebSocket client for order updates

    async def _connect(self) -> None:
        """Connect to Alpaca API."""
        self._log.info("Connecting to Alpaca...")

        # TODO: Verify credentials by making a test API call
        # TODO: Subscribe to order update WebSocket stream
        # TODO: Update account state

        self._log.info("Connected to Alpaca", LogColor.GREEN)

    async def _disconnect(self) -> None:
        """Disconnect from Alpaca API."""
        self._log.info("Disconnecting from Alpaca...")

        # TODO: Close WebSocket connections
        # TODO: Cancel any pending requests

        self._log.info("Disconnected from Alpaca")

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

        # TODO: Query Alpaca API for order status
        # TODO: Parse response into OrderStatusReport
        # TODO: Return the report

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

        # TODO: Query Alpaca API for orders
        # TODO: Parse responses into OrderStatusReport objects
        # TODO: Return the reports

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

        # TODO: Convert NautilusTrader order to Alpaca order format
        # TODO: Submit order via HTTP API
        # TODO: Handle response and generate appropriate events
        #       (OrderAccepted, OrderRejected, etc.)

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

        # TODO: Get order from cache
        # TODO: Cancel order via HTTP API
        # TODO: Handle response and generate appropriate events
        #       (OrderCanceled, OrderCancelRejected, etc.)

    async def _cancel_all_orders(self, command: CancelAllOrders) -> None:
        """
        Cancel all orders for an instrument on Alpaca.

        Parameters
        ----------
        command : CancelAllOrders
            The command to cancel all orders.

        """
        self._log.info(f"Canceling all orders for {command.instrument_id}")

        # TODO: Cancel all orders via HTTP API
        # TODO: Handle responses and generate appropriate events

    async def _query_account(self, command: QueryAccount) -> None:
        """
        Query account information from Alpaca.

        Parameters
        ----------
        command : QueryAccount
            The command to query the account.

        """
        self._log.debug("Querying account...")

        # TODO: Query account via HTTP API
        # TODO: Update account state
