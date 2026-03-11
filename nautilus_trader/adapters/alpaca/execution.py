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

import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd

from nautilus_trader.model.orders import StopLimitOrder

from custom.utils.paths import run_artifacts_subdir
from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.adapters.alpaca.constants import ALPACA_VENUE
from nautilus_trader.adapters.alpaca.utils import alpaca_date_str_to_nanos, ns_to_iso_8601, dt_to_iso_8601
from nautilus_trader.adapters.alpaca.enums import AlpacaOrderType, AlpacaTimeInForce
from nautilus_trader.adapters.alpaca.parsing import AlpacaEnumParser, client_id_is_real
from nautilus_trader.adapters.alpaca.websocket import AlpacaWebSocketClient
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.events.order import OrderFilled
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.core.uuid import UUID4

from nautilus_trader.live.config import LiveExecClientConfig
from nautilus_trader.model.enums import LiquiditySide, OrderSide, PositionSide
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.execution.reports import PositionStatusReport
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OrderStatus
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.orders.limit import LimitOrder
from nautilus_trader.model.objects import Money, AccountBalance, MarginBalance, Currency


if TYPE_CHECKING:
    import asyncio

    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus
    from nautilus_trader.execution.messages import CancelAllOrders, QueryOrder
    from nautilus_trader.execution.messages import CancelOrder
    from nautilus_trader.execution.messages import GenerateFillReports
    from nautilus_trader.execution.messages import GenerateOrderStatusReport
    from nautilus_trader.execution.messages import GenerateOrderStatusReports
    from nautilus_trader.execution.messages import GeneratePositionStatusReports
    from nautilus_trader.execution.messages import ModifyOrder
    from nautilus_trader.execution.messages import QueryAccount
    from nautilus_trader.execution.messages import SubmitOrder


# Flatten msg into a single dictionary
def flatten_dict(d: dict, parent_key: str = "", sep: str = "_") -> dict:
    """Recursively flatten a nested dictionary."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key and k in items else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


class AlpacaExecClientConfig(LiveExecClientConfig, frozen=True):
    """
    Configuration for ``AlpacaExecutionClient`` instances.

    Parameters
    ----------
    http_timeout : PositiveInt, default 30
        The timeout (seconds) for HTTP requests.
    max_retries : PositiveInt or None, default 3
        The maximum number of times a submit, cancel or modify order request will be retried.
    retry_delay_initial_ms : PositiveInt or None, default 1000
        The initial delay (milliseconds) between retries.
    retry_delay_max_ms : PositiveInt or None, default 10000
        The maximum delay (milliseconds) between retries.

    Warnings
    --------
    A short `retry_delay` with frequent retries may result in account bans or rate limiting.

    """

    paper: bool = True
    record_orders: bool = False
    http_timeout: PositiveInt = 30
    max_retries: PositiveInt | None = 3
    retry_delay_initial_ms: PositiveInt | None = 1_000
    retry_delay_max_ms: PositiveInt | None = 10_000


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
        self._http_timeout = config.http_timeout
        self._max_retries = config.max_retries or 3

        # Set account ID
        if config.paper:
            account_id = AccountId(f"{name or ALPACA_VENUE.value}-PAPER")
        else:
            account_id = AccountId(f"{name or ALPACA_VENUE.value}-LIVE")

        self._set_account_id(account_id)
        self._http_client = AlpacaHttpClient(
            paper=config.paper,
            timeout=config.http_timeout,
            record_orders=config.record_orders,
        )
        self._enum_parser = AlpacaEnumParser()

        # Initialize WebSocket client for order updates
        self._ws_client = AlpacaWebSocketClient(
            paper=config.paper,
            handler=self._handle_ws_message,
            logger=self._log,
        )

        self.order_previous_qty_and_value = dict()

        # Setup output directory and file for trade updates
        # TODO: Pull trade_updates from the logs instead to reduce overhead?
        self._trade_updates_output_file_path = run_artifacts_subdir("alpaca_trade_updates.json")
        self._trade_updates_data = []

        # Keep track of the associated client_order_id with the venue_order_id as orders are replaced/modified
        self._venue_id__client_id_map = {}

        # Store pending modify params so the "replaced" WS handler uses the correct qty/price
        # (the "replaced" event only contains OLD order data, not the new replacement order's data)
        self._pending_modify_params: dict[ClientOrderId, tuple[Quantity | None, Price | None]] = {}

        # Track processed execution IDs to deduplicate fills from websocket vs reconciliation
        self._processed_execution_ids: set[str] = set()

        # Track the latest WS-reported filled_qty per venue order ID (string -> string).
        # Used to correct REST API eventual consistency lag in order status reports.
        self._ws_filled_qty: dict[str, str] = {}

        # Track accumulated fills from previous venue orders per client_order_id.
        # Alpaca's replacement model creates a NEW venue order with fresh fill history,
        # Track ghost replacement order IDs that we've already attempted to cancel
        # and failed with a terminal-state error (rejected/filled/canceled).
        # Prevents infinite retry loops when Alpaca returns these in open orders queries.
        self._handled_ghost_ids: set[str] = set()

        # Capture start time for querying fills during reconciliation
        self._start_ns: int = self._clock.timestamp_ns()
        self._last_fills_report_ns: int = self._clock.timestamp_ns()

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
        await self._ws_client.connect()
        await self._ws_client.subscribe_trade_updates()

    async def _disconnect(self) -> None:
        """Disconnect from Alpaca API."""
        self._log.info("Disconnecting from Alpaca...")

        # Close WebSocket client
        await self._ws_client.disconnect()

        # Close HTTP client
        await self._http_client.close()

        # Flush trade updates before disconnecting
        self._save_trade_updates()

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

        self._log.debug(f"Updated account state: Equity=${equity}", LogColor.BLUE)
        # except Exception as e:
        #     self._log.error(f"Failed to update account state: {e}")

    # -- EXECUTION REPORTS --------------------------------------------------------------------

    def _parse_order_status_report(self, alpaca_order: dict[str, Any], ts_init: int) -> OrderStatusReport:
        """
        Parse an Alpaca order response into an OrderStatusReport.

        Parameters
        ----------
        alpaca_order : dict[str, Any]
            The Alpaca order response dictionary.
        account_id : AccountId
            The account ID.
        instrument_id : InstrumentId
            The instrument ID.
        ts_init : int
            The initialization timestamp (nanoseconds).

        Returns
        -------
        OrderStatusReport

        """

        instrument_id = InstrumentId.from_str(f"{alpaca_order['symbol']}.{ALPACA_VENUE}")
        venue_order_id = VenueOrderId(alpaca_order["id"])
        client_order_id = ClientOrderId(alpaca_order.get("client_order_id", alpaca_order["id"]))

        order_status = self._enum_parser.parse_alpaca_order_status(alpaca_order["status"])
        order_side = self._enum_parser.parse_alpaca_order_side(alpaca_order["side"])
        order_type = self._enum_parser.parse_alpaca_order_type(alpaca_order["order_type"])
        time_in_force = self._enum_parser.parse_alpaca_time_in_force(alpaca_order["time_in_force"])

        # Parse quantities
        quantity = Quantity.from_str(alpaca_order["qty"])
        rest_filled_str = alpaca_order["filled_qty"]
        # Correct for REST API eventual consistency lag: use the higher of
        # REST-reported filled_qty and WS-tracked filled_qty
        ws_filled_str = self._ws_filled_qty.get(alpaca_order["id"])
        if ws_filled_str and int(ws_filled_str) > int(rest_filled_str):
            self._log.debug(
                f"REST filled_qty ({rest_filled_str}) < WS filled_qty ({ws_filled_str}) for {alpaca_order['id']}, "
                "using WS value"
            )
            filled_qty_int = int(ws_filled_str)
        else:
            filled_qty_int = int(rest_filled_str)

        # NOTE: Alpaca's REST API only reports fills for the CURRENT venue order,
        # but NT's cache accumulates fills across all venue orders in a replacement
        # chain. This means report.filled_qty < cache.filled_qty for replaced orders.
        # We intentionally do NOT inflate the report here because during the transient
        # window after a replace, REST can be ahead of WS for the new venue order's
        # fills, causing spurious inferred fills that corrupt the position.
        # The reconciliation engine handles the mismatch: on first encounter it
        # force-closes the order (if venue says FILLED), and on subsequent cycles
        # it silently accepts the mismatch for already-closed orders.

        filled_qty = Quantity(filled_qty_int, precision=0)

        # Parse price (if applicable)
        price = None
        if "limit_price" in alpaca_order and alpaca_order["limit_price"]:
            price = Price.from_str(str(alpaca_order["limit_price"]))

        # Parse stop price (if applicable)
        trigger_price = None
        if "stop_price" in alpaca_order and alpaca_order["stop_price"]:
            trigger_price = Price.from_str(str(alpaca_order["stop_price"]))

        # Parse average fill price
        avg_px = None
        if "filled_avg_price" in alpaca_order and alpaca_order["filled_avg_price"]:
            avg_px = Decimal(alpaca_order["filled_avg_price"])

        # Parse timestamps
        submitted_at = alpaca_order["submitted_at"]
        ts_accepted = alpaca_date_str_to_nanos(submitted_at)
        ts_last = alpaca_date_str_to_nanos(alpaca_order["updated_at"])

        return OrderStatusReport(
            account_id=self.account_id,
            instrument_id=instrument_id,
            venue_order_id=venue_order_id,
            client_order_id=client_order_id,
            order_side=order_side,
            order_type=order_type,
            time_in_force=time_in_force,
            order_status=order_status,
            quantity=quantity,
            filled_qty=filled_qty,
            price=price,
            trigger_price=trigger_price,
            avg_px=avg_px,
            post_only=False,
            reduce_only=False,
            report_id=UUID4(),
            ts_accepted=ts_accepted,
            ts_last=ts_last,
            ts_init=ts_init,
        )

    async def generate_order_status_report(self, command: GenerateOrderStatusReport) -> OrderStatusReport | None:
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

            # Query Alpaca API for order status, following the replacement chain if the order has been replaced
            alpaca_order = await self._http_client.get_order(venue_order_id.value)
            while alpaca_order.get("replaced_by"):
                replaced_by = alpaca_order["replaced_by"]
                self._log.debug(f"Order {venue_order_id.value} was replaced by {replaced_by}, following chain")
                # Register the old venue_id mapping before following the chain
                old_id = alpaca_order["id"]
                client_order_id_str = alpaca_order.get("client_order_id")
                if client_order_id_str and client_id_is_real(client_order_id_str):
                    self._venue_id__client_id_map[old_id] = client_order_id_str
                alpaca_order = await self._http_client.get_order(replaced_by)

            # Parse response into OrderStatusReport
            filtered_list = self.filter_replaced_and_incomplete_orders([alpaca_order])
            if filtered_list is not None and len(filtered_list) > 0:
                alpaca_order = filtered_list[0]
                report = self._parse_order_status_report(alpaca_order=alpaca_order, ts_init=self._clock.timestamp_ns())
                self._log.debug(f"Generated single order report {report}")
                return report
            self._log.error(f"Single order report {alpaca_order} filtered out")

        except Exception as e:
            self._log.error(f"Failed to generate OrderStatusReport: {e}")
            return None

    def filter_replaced_and_incomplete_orders(self, orders_list: list[dict[str, str]]) -> list[dict[str, str]]:
        filtered_and_modified_orders_list = []

        # Build a lookup from venue order ID to order data for chain walking
        orders_by_venue_id = {order["id"]: order for order in orders_list}

        for order in reversed(orders_list):
            client_order_id = order["client_order_id"]
            alpaca_venue_order_id = order["id"]
            if client_id_is_real(client_order_id):
                self._venue_id__client_id_map[alpaca_venue_order_id] = client_order_id
            elif order["replaces"] in self._venue_id__client_id_map.keys():
                self._venue_id__client_id_map[alpaca_venue_order_id] = self._venue_id__client_id_map[order["replaces"]]

            # Walk the replaces chain backward and map all intermediate venue
            # order IDs to the same client_order_id. This ensures FillReports
            # referencing predecessor orders can be resolved.
            if alpaca_venue_order_id in self._venue_id__client_id_map:
                resolved_client_id = self._venue_id__client_id_map[alpaca_venue_order_id]
                predecessor_id = order["replaces"]
                while predecessor_id and predecessor_id not in self._venue_id__client_id_map:
                    self._venue_id__client_id_map[predecessor_id] = resolved_client_id
                    predecessor_order = orders_by_venue_id.get(predecessor_id)
                    if predecessor_order:
                        predecessor_id = predecessor_order["replaces"]
                    else:
                        break

            if order["replaced_by"] is None:
                if client_id_is_real(client_order_id):
                    filtered_and_modified_orders_list.append(order)
                else:
                    try:
                        actual_client_order_id = self._venue_id__client_id_map[alpaca_venue_order_id]
                        order["client_order_id"] = actual_client_order_id
                        filtered_and_modified_orders_list.append(order)
                    except KeyError:
                        self._log.debug(f"No client_order_id for {order}, skipping")

        # Deduplicate orders with the same client_order_id, keeping only the one with the highest updated_at timestamp
        deduplicated_orders = {}
        for order in filtered_and_modified_orders_list:
            client_order_id = order["client_order_id"]
            if client_order_id not in deduplicated_orders:
                deduplicated_orders[client_order_id] = order
            else:
                # Compare timestamps and keep the order with the most recent updated_at
                existing_order = deduplicated_orders[client_order_id]
                self._log.debug(
                    f"Two orders with the same client_order_id {client_order_id}:\n{order}\n and\n{existing_order}\nComparing timestamps and keeping latest"
                )
                if pd.Timestamp(order["updated_at"]) > pd.Timestamp(existing_order["updated_at"]):
                    deduplicated_orders[client_order_id] = order

        return list(deduplicated_orders.values())

    async def generate_order_status_reports(self, command: GenerateOrderStatusReports) -> list[OrderStatusReport]:
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

        NOTE: THIS GETS RUN DURING RECONCILIATION via `generate_mass_status`
        """
        reports: list[OrderStatusReport] = []

        try:
            # Determine status filter
            status = "open" if command.open_only else "all"

            # Query Alpaca API for orders
            after = dt_to_iso_8601(command.start) if command.start is not None else None
            alpaca_orders = await self._http_client.get_orders(status=status, after=after)
            alpaca_orders = self.filter_replaced_and_incomplete_orders(alpaca_orders)
            # Parse responses into OrderStatusReport objects
            for alpaca_order in alpaca_orders:
                try:
                    # NOTE: This ghost order accounting is actually run in paper mode
                    # Detect ghost replacement orders from modify-fill races.
                    # If the cached order is already closed with a DIFFERENT venue_order_id,
                    # this Alpaca order is a ghost that should be canceled, not reconciled.
                    alpaca_order_id = alpaca_order["id"]
                    alpaca_client_order_id_str = alpaca_order["client_order_id"]
                    cached_order = None
                    cached_order = self._cache.order(ClientOrderId(alpaca_client_order_id_str))
                    if cached_order and cached_order.is_closed:
                        cached_venue_id = cached_order.venue_order_id.value if cached_order.venue_order_id else None
                        if cached_venue_id and cached_venue_id != alpaca_order_id:
                            if alpaca_order_id in self._handled_ghost_ids:
                                self._log.debug(f"Skipping already-handled ghost {alpaca_order_id}")
                                continue
                            self._log.warning(
                                f"Ghost replacement detected: Alpaca order {alpaca_order_id} is open "
                                f"but cached order {alpaca_client_order_id_str} is {cached_order.status_string()} "
                                f"with venue_order_id {cached_venue_id}. Canceling ghost.",
                            )
                            try:
                                await self._http_client.cancel_order(alpaca_order_id)
                                # Mark as handled after successful cancel so we don't
                                # re-detect it if Alpaca's order list API returns stale
                                # data on the next reconciliation cycle.
                                self._log.info(f"Ghost {alpaca_order_id} successfully canceled")
                                self._handled_ghost_ids.add(alpaca_order_id)
                            except Exception as cancel_err:
                                err_str = str(cancel_err)
                                # If Alpaca says the order is already in a terminal state,
                                # mark it as handled so we don't retry on every reconciliation cycle
                                if "already in" in err_str and any(
                                    state in err_str for state in ("rejected", "filled", "canceled", "expired")
                                ):
                                    self._handled_ghost_ids.add(alpaca_order_id)
                                    self._log.warning(
                                        f"Ghost {alpaca_order_id} is in terminal state at Alpaca, suppressing future cancel retries",
                                    )
                                else:
                                    self._log.error(f"Failed to cancel ghost {alpaca_order_id}: {cancel_err}")
                            continue

                    report = self._parse_order_status_report(
                        alpaca_order=alpaca_order, ts_init=self._clock.timestamp_ns()
                    )
                    reports.append(report)
                    self._log.debug(f"Generated {report}")

                except Exception as e:
                    self._log.error(f"Failed to parse order {alpaca_order.get('id')}: {e}")

        except Exception as e:
            self._log.error(f"Failed to generate OrderStatusReports: {e}")

        return reports

    def _alpaca_fill_report_to_nt_report(self, alpaca_fill: dict) -> FillReport | None:
        """
        Parse an Alpaca fill activity into a FillReport.

        Parameters
        ----------
        alpaca_fill : dict[str, Any]
            The Alpaca fill activity response dictionary (from GET /v2/account/activities/FILL).

        Returns
        -------
        FillReport

        """
        instrument_id = InstrumentId.from_str(f"{alpaca_fill['symbol']}.{ALPACA_VENUE}")
        # Alpaca fill id format: "20260213040457999::b8b24055-bcb1-428d-8ba0-e28cd2c930bb"
        # TradeId max length is 36, so extract the UUID portion after "::"
        fill_id = alpaca_fill["id"]
        trade_id = TradeId(fill_id.split("::")[-1])

        venue_order_id = VenueOrderId(alpaca_fill["order_id"])
        order_side = self._enum_parser.parse_alpaca_order_side(alpaca_fill["side"])
        last_qty = Quantity.from_str(alpaca_fill["qty"])
        last_px = Price.from_str(alpaca_fill["price"])
        ts_event = alpaca_date_str_to_nanos(alpaca_fill["transaction_time"])

        client_order_id_str = self._venue_id__client_id_map.get(alpaca_fill["order_id"])
        if client_order_id_str is not None:
            client_order_id = ClientOrderId(client_order_id_str)
        else:
            # FIXME: This code has never been run or tested!
            # Fallback: try to resolve from cache (handles orders submitted but not yet
            # in _venue_id__client_id_map, e.g. if order status reports haven't been processed yet)
            client_order_id = self._cache.client_order_id(venue_order_id)
            if client_order_id:
                self._venue_id__client_id_map[alpaca_fill["order_id"]] = str(client_order_id)
                self._log.debug(
                    f"Resolved venue order id {alpaca_fill['order_id']} from cache (client_order_id={client_order_id})"
                )
            else:
                self._log.warning(
                    f"Venue order id {alpaca_fill['order_id']} not found in _venue_id__client_id_map "
                    f"or cache, skipping fill report (trade_id={trade_id})"
                )
                return None

        return FillReport(
            account_id=self.account_id,
            instrument_id=instrument_id,
            venue_order_id=venue_order_id,
            trade_id=trade_id,
            order_side=order_side,
            last_qty=last_qty,
            last_px=last_px,
            commission=Money(0, USD),
            liquidity_side=LiquiditySide.TAKER,
            report_id=UUID4(),
            ts_event=ts_event,
            ts_init=self._clock.timestamp_ns(),
            client_order_id=client_order_id,
        )

    async def generate_fill_reports(
        self,
        command: GenerateFillReports,
    ) -> list[FillReport]:
        """
        Generate fill reports.

        NOTE: THIS GETS RUN DURING RECONCILIATION via `generate_mass_status`

        Parameters
        ----------
        command : GenerateFillReports
            The command to generate reports.

        Returns
        -------
        list[FillReport]
            The fill reports.
        """
        self._log.debug("Generating Alpaca FillReports.")

        request_sent_dt = self._clock.timestamp_ns()

        # The GenerateFillReports command has a "start_time" in it, but I think it's better to keep
        # track here within the client itself with self._last_fills_report_ns
        after = ns_to_iso_8601(self._last_fills_report_ns)
        fill_orders = await self._http_client.get_fills(after=after)
        self._last_fills_report_ns = request_sent_dt

        reports: list[FillReport] = []
        for fill_order in fill_orders:
            # Need to parse report first because "trade_id" has a datetime prefix from alpaca
            fill_report = self._alpaca_fill_report_to_nt_report(fill_order)
            if fill_report is None:
                continue
            if fill_report.trade_id.value in self._processed_execution_ids:
                self._log.debug(f"Skipping report {fill_report.trade_id}, already processed")
            else:
                self._processed_execution_ids.add(fill_report.trade_id.value)
                reports.append(fill_report)
        return reports

    async def generate_position_status_reports(
        self,
        command: GeneratePositionStatusReports,
    ) -> list[PositionStatusReport]:
        """
        Generate position status reports.

        NOTE: THIS GETS RUN DURING RECONCILIATION via `generate_mass_status`

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

        from decimal import Decimal
        from nautilus_trader.core.uuid import UUID4

        reports: list[PositionStatusReport] = []
        ts_init = self._clock.timestamp_ns()

        try:
            positions = await self._http_client.get_positions()
        except Exception as e:
            self._log.error(f"Failed to fetch positions from Alpaca: {e}")
            return reports

        found_instrument_ids: set[InstrumentId] = set()

        for pos_data in positions:
            symbol = pos_data["symbol"]
            instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA_VENUE}")
            found_instrument_ids.add(instrument_id)
            qty = int(pos_data["qty"])
            side_str = pos_data["side"]
            avg_entry_price = pos_data["avg_entry_price"]

            if qty == 0:
                position_side = PositionSide.FLAT
            elif side_str == "long":
                position_side = PositionSide.LONG
            else:
                position_side = PositionSide.SHORT

            report = PositionStatusReport(
                account_id=self.account_id,
                instrument_id=instrument_id,
                position_side=position_side,
                quantity=Quantity.from_int(abs(qty)),
                report_id=UUID4(),
                ts_last=ts_init,
                ts_init=ts_init,
                avg_px_open=Decimal(str(avg_entry_price)) if avg_entry_price else None,
            )
            self._log.debug(f"Generated {report}")
            reports.append(report)

        # If the command requests a specific instrument that Alpaca doesn't have a position for,
        # generate a FLAT report so the exec engine can reconcile the cache position down to zero
        if command.instrument_id is not None and command.instrument_id not in found_instrument_ids:
            instrument = self._cache.instrument(command.instrument_id)
            size_precision = instrument.size_precision if instrument else 0
            flat_report = PositionStatusReport.create_flat(
                account_id=self.account_id,
                instrument_id=command.instrument_id,
                size_precision=size_precision,
                ts_init=ts_init,
            )
            self._log.debug(f"Generated FLAT report for {command.instrument_id} (no Alpaca position)")
            reports.append(flat_report)

        return reports

    # -- COMMAND HANDLERS ---------------------------------------------------------------------

    async def _submit_order_list(self, command: SubmitOrder) -> None:
        # Bracket order docs: https://docs.alpaca.markets/docs/orders-at-alpaca
        orders = command.order_list.orders
        if len(orders) == 2:
            order_type = "oco"
            buy_order = None
            stop_order, take_order = orders
        elif len(orders) == 3:
            order_type = "bracket"
            buy_order, stop_order, take_order = orders
            assert isinstance(buy_order, LimitOrder)
            assert buy_order.side == OrderSide.BUY
        else:
            raise Exception(f"Invalid number of orders: {len(orders)}")

        assert isinstance(stop_order, StopLimitOrder)
        assert isinstance(take_order, LimitOrder)
        assert stop_order.side == OrderSide.SELL
        assert take_order.side == OrderSide.SELL

        strategy_id = take_order.strategy_id
        instrument_id = take_order.instrument_id
        symbol = instrument_id.symbol.value.split(".")[0]

        order_request = {
            "order_class": order_type,
            "type": "limit",
            "symbol": symbol,
            "time_in_force": "day",
            "take_profit": {
                "limit_price": str(take_order.price),
            },
            "stop_loss": {
                "stop_price": str(stop_order.trigger_price),
                # "limit_price": str(stop_order.price) # TODO: Add this if want limit order, otherwise it'll be market
            },
        }
        if order_type == "bracket":
            order_request = {
                **order_request,
                "side": "buy",
                "qty": str(buy_order.quantity),
                "limit_price": str(buy_order.price),
            }
        elif order_type == "oco":
            order_request = {
                **order_request,
                "side": "sell",
                "qty": str(take_order.quantity),
            }
        else:
            raise Exception(f"Invalid order type: {order_type}")

        # Generate order submitted events
        submitted_time = self._clock.timestamp_ns()
        for order in orders:
            self.generate_order_submitted(
                strategy_id=strategy_id,
                instrument_id=instrument_id,
                client_order_id=order.client_order_id,
                ts_event=submitted_time,
            )

        order_response = await self._http_client.submit_order(order_request)
        accepted_time = self._clock.timestamp_ns()

        if order_type == "bracket":
            alpaca_take_order = None
            alpaca_stop_order = None
            for leg in order_response["legs"]:
                if leg["type"] == "limit":
                    alpaca_take_order = leg
                elif leg["type"] == "stop":
                    alpaca_stop_order = leg
                else:
                    raise Exception(f"Unknown leg type: {leg['type']}. Leg {leg}")

            if alpaca_stop_order is None or alpaca_take_order is None:
                raise Exception(f"Stop order or take order are not present. order response: {order_response}")
            # Order accepted for BUY order
            self.generate_order_accepted(
                strategy_id=strategy_id,
                instrument_id=instrument_id,
                client_order_id=buy_order.client_order_id,
                venue_order_id=VenueOrderId(order_response["id"]),
                ts_event=accepted_time,
            )

        elif order_type == "oco":
            alpaca_take_order = order_response
            alpaca_stop_order = order_response["legs"][0]

        # Order accepted for TAKE order
        self.generate_order_accepted(
            strategy_id=strategy_id,
            instrument_id=instrument_id,
            client_order_id=take_order.client_order_id,
            venue_order_id=VenueOrderId(alpaca_take_order["id"]),
            ts_event=accepted_time,
        )

        # Order accepted for STOP order
        self.generate_order_accepted(
            strategy_id=strategy_id,
            instrument_id=instrument_id,
            client_order_id=stop_order.client_order_id,
            venue_order_id=VenueOrderId(alpaca_stop_order["id"]),
            ts_event=accepted_time,
        )

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

        self._log.debug(f"Submitting order: {order}")

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
            # Register venue_id mapping so reconciliation fill reports can resolve it
            self._venue_id__client_id_map[alpaca_order["id"]] = str(order.client_order_id)
            self.generate_order_accepted(
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=VenueOrderId(alpaca_order["id"]),
                ts_event=self._clock.timestamp_ns(),
            )

        except Exception as e:
            self._log.debug(f"Failed to submit order: {e}")
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

        # FIXME: BRENT should we prevent any market orders?
        extended_hours = order_type == AlpacaOrderType.LIMIT and time_in_force == AlpacaTimeInForce.DAY
        # Build base request
        request = {
            "symbol": symbol,
            "qty": str(order.quantity),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "client_order_id": order.client_order_id.value,
            "extended_hours": extended_hours,
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
        self._log.debug(f"Modifying order: {command.client_order_id}")

        try:
            # Get order from cache
            order = self._cache.order(command.client_order_id)
            if not order:
                self._log.error(f"Order {command.client_order_id} not found in cache")
                self.generate_order_modify_rejected(
                    strategy_id=command.strategy_id,
                    instrument_id=command.instrument_id,
                    client_order_id=command.client_order_id,
                    venue_order_id=command.venue_order_id,
                    reason="Order not found in cache",
                    ts_event=self._clock.timestamp_ns(),
                )
                return

            if order.is_closed:
                self._log.warning(f"Order {command.client_order_id} is already closed")
                self.generate_order_modify_rejected(
                    strategy_id=command.strategy_id,
                    instrument_id=command.instrument_id,
                    client_order_id=command.client_order_id,
                    venue_order_id=command.venue_order_id,
                    reason="Order is already closed",
                    ts_event=self._clock.timestamp_ns(),
                )
                return

            # Get venue order ID
            venue_order_id = command.venue_order_id or self._cache.venue_order_id(command.client_order_id)
            if not venue_order_id:
                self._log.error(f"No venue_order_id found for {command.client_order_id}")
                self.generate_order_modify_rejected(
                    strategy_id=command.strategy_id,
                    instrument_id=command.instrument_id,
                    client_order_id=command.client_order_id,
                    venue_order_id=None,
                    reason="No venue_order_id found",
                    ts_event=self._clock.timestamp_ns(),
                )
                return

            # Build replacement parameters
            qty = str(command.quantity) if command.quantity else None
            limit_price = str(command.price) if command.price else None
            stop_price = str(command.trigger_price) if command.trigger_price else None

            # Store pending params so the WS "replaced" handler uses the correct values
            # (the "replaced" event only contains the OLD order's data)
            self._pending_modify_params[command.client_order_id] = (command.quantity, command.price)

            # Modify order via HTTP API (Alpaca uses PATCH for replace)
            try:
                response = await self._http_client.replace_order(
                    order_id=venue_order_id.value,
                    # client_order_id=command.client_order_id.value,
                    qty=qty,
                    limit_price=limit_price,
                    stop_price=stop_price,
                )
                # Register the new replacement venue order ID immediately so that
                # fill lookups during reconciliation can resolve it (the WebSocket
                # "replaced" event may arrive later than REST fill queries)
                new_venue_order_id = response["id"]
                self._venue_id__client_id_map[new_venue_order_id] = str(command.client_order_id)

                # Also update the cache index immediately. Without this, any
                # reconciliation that runs before the WS "replaced" event arrives
                # will fail to resolve this venue_order_id from the cache,
                # producing "Venue order ID X not indexed in cache" warnings.
                self._cache.add_venue_order_id(
                    command.client_order_id, VenueOrderId(new_venue_order_id), overwrite=True
                )

                # Check if the order filled during the PATCH round-trip.
                # If so, Alpaca still created a ghost replacement order that we must cancel.
                order = self._cache.order(command.client_order_id)
                if order and order.is_closed:
                    # FIXME: This code has never been hit or tested!
                    ghost_order_id = response["id"]
                    self._log.warning(
                        f"Order {command.client_order_id} filled during modify, canceling ghost replacement {ghost_order_id}",
                    )
                    self._pending_modify_params.pop(command.client_order_id, None)
                    try:
                        await self._http_client.cancel_order(ghost_order_id)
                    except Exception as cancel_err:
                        self._log.error(f"Failed to cancel ghost replacement {ghost_order_id}: {cancel_err}")

            except Exception as e:
                string = e.args[0]
                start = string.find("{")
                json_text = string[start:]
                data = json.loads(json_text)
                msg = data["message"]
                if msg == "order already replaced":
                    self._log.info(f"Order {command.client_order_id} is already pending replacement, skipping")
                    new_order = await self._http_client.get_order(venue_order_id.value)
                    # Register the replacement's venue order ID if available
                    replaced_by_id = new_order.get("replaced_by")
                    if replaced_by_id:
                        self._venue_id__client_id_map[replaced_by_id] = str(command.client_order_id)
                    if float(new_order["limit_price"]) != float(limit_price):
                        self._log.warning(f"Order already replaced, but limit price has changed. {venue_order_id}")
                elif msg == "order parameters are not changed" or "insufficient qty available for order" in msg:
                    self._log.info(f"Order {command.client_order_id} modify rejected: {msg}")
                    order = self._cache.order(command.client_order_id)
                    current_venue_order_id = order.venue_order_id if order else command.venue_order_id
                    self.generate_order_modify_rejected(
                        strategy_id=command.strategy_id,
                        instrument_id=command.instrument_id,
                        client_order_id=command.client_order_id,
                        venue_order_id=current_venue_order_id,
                        reason=msg,
                        ts_event=self._clock.timestamp_ns(),
                    )
                else:
                    raise Exception(json_text) from e

        except Exception as e:
            self.generate_order_modify_rejected(
                strategy_id=command.strategy_id,
                instrument_id=command.instrument_id,
                client_order_id=command.client_order_id,
                venue_order_id=command.venue_order_id,
                reason=str(e),
                ts_event=self._clock.timestamp_ns(),
            )

    async def _cancel_order(self, command: CancelOrder) -> None:
        """
        Cancel an order on Alpaca.

        Parameters
        ----------
        command : CancelOrder
            The command to cancel the order.

        """
        self._log.debug(f"Canceling order: {command.client_order_id}")

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

        except Exception as e:
            self._log.error(f"Failed to cancel order: {e}")
            if order:
                # Use the order's CURRENT venue_order_id from cache, not the
                # (possibly stale) one from the cancel command. After a replace,
                # the cache order already has the new venue_order_id, and the
                # engine will reject the event if there's a mismatch.
                current_venue_order_id = order.venue_order_id or venue_order_id
                self.generate_order_cancel_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=order.instrument_id,
                    client_order_id=order.client_order_id,
                    venue_order_id=current_venue_order_id,
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
            open_orders = await self._http_client.get_orders(
                status="open", symbols=command.instrument_id.symbol.value, priority=True
            )

            for open_order in open_orders:
                try:
                    venue_order_id = VenueOrderId(open_order["id"])
                    client_order_id_str = open_order.get("client_order_id")
                    cancelation_response = await self._http_client.cancel_order(open_order["id"])
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
                    self._log.error(f"Failed to generate cancel event for order {open_order.get('id')}: {e}")

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

    async def _query_order(self, command: QueryOrder) -> None:
        order_status_report = await self.generate_order_status_report(command)
        if order_status_report is not None:
            self._send_order_status_report(order_status_report)

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
            msg_received_dt = pd.Timestamp.utcnow()

            # Record the raw message immediately, before any early returns
            flattened_msg = flatten_dict(msg)
            self._trade_updates_data.append({"msg_received_dt": str(msg_received_dt), **flattened_msg})

            msg_data = msg["data"]
            event = msg_data.get("event")
            order_data = msg_data.get("order", {})

            if not event or not order_data:
                self._log.warning(f"Invalid trade update message: {msg}")
                return

            # Extract order identifiers
            venue_order_id_str = order_data.get("id")
            client_order_id_str = order_data.get("client_order_id")

            venue_order_id = VenueOrderId(venue_order_id_str)
            client_order_id = ClientOrderId(client_order_id_str) if client_order_id_str else None

            # Try to get client_order_id from cache if not in message
            if not client_order_id or not client_id_is_real(client_order_id_str):
                client_order_id = self._cache.client_order_id(venue_order_id)

            if not client_order_id:
                self._log.debug(f"Cannot process trade update: no client_order_id for {venue_order_id}")
                return

            # Get order from cache
            order = self._cache.order(client_order_id)
            if not order:
                self._log.warning(f"Order {client_order_id} not found in cache")
                return

            instrument_id = order.instrument_id
            ts_event = alpaca_date_str_to_nanos(msg_data["timestamp"])

            # Handle different event types
            if event == "new":
                if order.is_closed:
                    # FIXME: This code has not been hit yet. It is VERY unlikely to occur. Would have
                    #  to be closed and then resolved in reconciliation before the websocket msg
                    self._log.warning(
                        f"Order {client_order_id} already {order.status_string()}, skipping late 'new' event (likely ghost replacement from modify-fill race)",
                    )
                    return
                if order.status == OrderStatus.ACCEPTED:
                    self._log.debug(f"Order {client_order_id} already accepted, skipping duplicate")
                    return
                self.generate_order_accepted(
                    strategy_id=order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=venue_order_id,
                    ts_event=ts_event,
                )

            elif event in ["fill", "partial_fill"]:
                if order_data["asset_class"] != "us_equity":
                    raise NotImplementedError(
                        f"fill event for asset_class {order_data['asset_class']} not yet implemented"
                    )

                this_fill_qty = msg_data["qty"]
                this_fill_price = msg_data["price"]
                # "execution_id" is what we want, it's the id for the action taken at the exchange.
                alpaca_execution_id = msg_data["execution_id"]

                # Track WS-reported filled_qty to correct REST API lag in order status reports
                self._ws_filled_qty[venue_order_id_str] = order_data["filled_qty"]

                # Dedup: skip fill if this execution_id was already processed.
                # NOTE: We rely solely on execution_id for dedup, NOT on filled_qty comparison.
                # Alpaca's replacement model creates fresh fill histories on the new order,
                # so the new order's filled_qty starts from 0 even though the cache already
                # has fills from the old (replaced) order.
                if alpaca_execution_id in self._processed_execution_ids:
                    self._log.warning(f"Skipping duplicate execution_id: {alpaca_execution_id}")
                else:
                    self._processed_execution_ids.add(alpaca_execution_id)
                    currency = Currency.from_str("USD")
                    self.generate_order_filled(
                        strategy_id=order.strategy_id,
                        instrument_id=instrument_id,
                        client_order_id=client_order_id,
                        venue_order_id=venue_order_id,
                        venue_position_id=None,
                        trade_id=TradeId(alpaca_execution_id),
                        order_side=order.side,
                        order_type=order.order_type,
                        last_qty=Quantity.from_str(this_fill_qty),
                        last_px=Price.from_str(this_fill_price),
                        quote_currency=currency,
                        commission=Money(0, currency),  # Commission is 0 for Alpaca
                        liquidity_side=LiquiditySide.TAKER,
                        ts_event=ts_event,
                    )

            elif event == "canceled":
                self.generate_order_canceled(
                    strategy_id=order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=venue_order_id,
                    ts_event=ts_event,
                )

            elif event == "replaced":  # AKA modified
                self._log.debug(f"Order {client_order_id} replaced")

                replaced_by = msg_data["order"]["replaced_by"]

                # Register the new replacement venue order ID in the mapping so that fill lookups during reconciliation
                # can resolve it
                self._venue_id__client_id_map[replaced_by] = str(client_order_id)

                # Proactively update the cache index so reconciliation can resolve
                # this venue order ID before generate_order_updated propagates
                self._cache.add_venue_order_id(
                    client_order_id,
                    VenueOrderId(replaced_by),
                    overwrite=True,
                )

                # If the order filled during the modify round-trip, the "replaced" event is stale. Cancel the ghost
                # replacement order on Alpaca.
                if order.is_closed:
                    self._log.warning(
                        f"Order {client_order_id} already {order.status_string()}, skipping 'replaced' event and canceling ghost {replaced_by}",
                    )
                    self._pending_modify_params.pop(client_order_id, None)
                    try:
                        self._loop.create_task(self._http_client.cancel_order(replaced_by))
                    except Exception as e:
                        self._log.error(f"Failed to cancel ghost replacement {replaced_by}: {e}")
                    return

                # Use pending modify params if available (from our _modify_order call). The "replaced" event's order
                # data contains the OLD order's qty/price, not the new replacement order's values.
                pending = self._pending_modify_params.pop(client_order_id, None)
                if pending:
                    pending_qty, pending_price = pending
                    quantity = pending_qty if pending_qty is not None else order.quantity
                    price = pending_price if pending_price is not None else order.price
                else:
                    # External replacement or no pending params - fall back to old order data
                    quantity = Quantity.from_str(msg_data["order"]["qty"])
                    price = Price(float(msg_data["order"]["limit_price"]), precision=order.price.precision)
                    raise RuntimeError("Should not fall here")

                self.generate_order_updated(
                    strategy_id=order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=VenueOrderId(replaced_by),
                    quantity=quantity,
                    price=price,
                    trigger_price=order.trigger_price if order.has_trigger_price else None,
                    ts_event=ts_event,
                    venue_order_id_modified=True,
                )

            elif event == "order_replace_rejected":
                reason = msg_data["reason"]
                self._pending_modify_params.pop(client_order_id, None)
                self.generate_order_modify_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=client_order_id,
                    venue_order_id=venue_order_id,
                    reason=reason,
                    ts_event=ts_event,
                )
            elif event == "rejected":
                # FIXME: This is dead code on paper trading, remove?
                reason = "Unknown"
                self.generate_order_rejected(
                    strategy_id=order.strategy_id,
                    instrument_id=instrument_id,
                    client_order_id=client_order_id,
                    reason=reason,
                    ts_event=ts_event,
                )

            elif event in ["pending_new", "accepted", "held"]:
                self._log.debug(f"Unhandled trade update event: {event}")
            else:
                self._log.warning(f"Unrecognized trade update event: {event}")

        except Exception as e:
            self._log.error(f"Error handling WebSocket message: {e}")

    def _save_trade_updates(self) -> None:
        # TODO: save these every so often? Or wait until the end?
        try:
            with open(self._trade_updates_output_file_path, "w") as f:
                json.dump(self._trade_updates_data, f, default=str)
        except Exception as e:
            self._log.error(f"Failed to save trade updates to file: {e}")
