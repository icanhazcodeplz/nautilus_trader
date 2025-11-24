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

"""Parsing utilities for Alpaca adapter."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.adapters.alpaca.enums import AlpacaOrderStatus
from nautilus_trader.adapters.alpaca.enums import AlpacaOrderType
from nautilus_trader.adapters.alpaca.enums import AlpacaTimeInForce
from nautilus_trader.core.uuid import UUID4

from nautilus_trader.adapters.alpaca.utils import alpaca_date_str_to_nanos
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.model.enums import LiquiditySide
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


class AlpacaEnumParser:
    """
    Parser for converting between Alpaca and NautilusTrader enumerations.
    """

    @staticmethod
    def parse_alpaca_order_side(side: str) -> OrderSide:
        if side == "buy":
            return OrderSide.BUY
        elif side == "sell":
            return OrderSide.SELL
        else:
            raise ValueError(f"Unknown Alpaca order side: {side}")

    @staticmethod
    def parse_nautilus_order_side(side: OrderSide) -> str:
        if side == OrderSide.BUY:
            return "buy"
        elif side == OrderSide.SELL:
            return "sell"
        else:
            raise ValueError(f"Unknown Nautilus order side: {side}")

    @staticmethod
    def parse_alpaca_order_type(order_type: str) -> OrderType:
        if order_type == AlpacaOrderType.MARKET:
            return OrderType.MARKET
        elif order_type == AlpacaOrderType.LIMIT:
            return OrderType.LIMIT
        elif order_type == AlpacaOrderType.STOP_LIMIT:
            return OrderType.STOP_LIMIT
        elif order_type == AlpacaOrderType.TRAILING_STOP:
            return OrderType.TRAILING_STOP_MARKET
        else:
            raise ValueError(f"Unknown Alpaca order type: {order_type}")

    @staticmethod
    def parse_nautilus_order_type(order_type: OrderType) -> str:
        if order_type == OrderType.MARKET:
            return AlpacaOrderType.MARKET
        elif order_type == OrderType.LIMIT:
            return AlpacaOrderType.LIMIT
        elif order_type == OrderType.STOP_LIMIT:
            return AlpacaOrderType.STOP_LIMIT
        elif order_type == OrderType.TRAILING_STOP_MARKET:
            return AlpacaOrderType.TRAILING_STOP
        else:
            raise ValueError(f"Unsupported order type for Alpaca: {order_type}")

    @staticmethod
    def parse_alpaca_time_in_force(tif: str) -> TimeInForce:
        if tif == AlpacaTimeInForce.DAY:
            return TimeInForce.DAY
        elif tif == AlpacaTimeInForce.GTC:
            return TimeInForce.GTC
        elif tif == AlpacaTimeInForce.IOC:
            return TimeInForce.IOC
        elif tif == AlpacaTimeInForce.FOK:
            return TimeInForce.FOK
        elif tif == AlpacaTimeInForce.GTD:
            return TimeInForce.GTD
        elif tif == AlpacaTimeInForce.OPG:
            return TimeInForce.AT_THE_OPEN
        elif tif == AlpacaTimeInForce.CLS:
            return TimeInForce.AT_THE_CLOSE
        else:
            raise ValueError(f"Unknown Alpaca time in force: {tif}")

    @staticmethod
    def parse_nautilus_time_in_force(tif: TimeInForce) -> str:
        if tif == TimeInForce.DAY:
            return AlpacaTimeInForce.DAY
        elif tif == TimeInForce.GTC:
            return AlpacaTimeInForce.GTC
        elif tif == TimeInForce.IOC:
            return AlpacaTimeInForce.IOC
        elif tif == TimeInForce.FOK:
            return AlpacaTimeInForce.FOK
        elif tif == TimeInForce.GTD:
            return AlpacaTimeInForce.GTD
        elif tif == TimeInForce.AT_THE_OPEN:
            return AlpacaTimeInForce.OPG
        elif tif == TimeInForce.AT_THE_CLOSE:
            return AlpacaTimeInForce.CLS
        else:
            raise ValueError(f"Unsupported time in force for Alpaca: {tif}")

    @staticmethod
    def parse_alpaca_order_status(status: str) -> OrderStatus:
        if status in (AlpacaOrderStatus.NEW, AlpacaOrderStatus.ACCEPTED, AlpacaOrderStatus.PENDING_NEW):
            return OrderStatus.ACCEPTED
        elif status == AlpacaOrderStatus.PARTIALLY_FILLED:
            return OrderStatus.PARTIALLY_FILLED
        elif status == AlpacaOrderStatus.FILLED:
            return OrderStatus.FILLED
        elif status == AlpacaOrderStatus.PENDING_CANCEL:
            return OrderStatus.PENDING_CANCEL
        elif status == AlpacaOrderStatus.CANCELED:
            return OrderStatus.CANCELED
        elif status in (AlpacaOrderStatus.EXPIRED, AlpacaOrderStatus.DONE_FOR_DAY):
            return OrderStatus.EXPIRED
        elif status in (
            AlpacaOrderStatus.REJECTED,
            AlpacaOrderStatus.CANCEL_REJECTED,
            AlpacaOrderStatus.REPLACE_REJECTED,
        ):
            return OrderStatus.REJECTED
        elif status == AlpacaOrderStatus.PENDING_REPLACE:
            return OrderStatus.PENDING_UPDATE
        elif status == AlpacaOrderStatus.REPLACED:
            return OrderStatus.ACCEPTED  # Replaced order becomes accepted
        elif status in (AlpacaOrderStatus.STOPPED, AlpacaOrderStatus.SUSPENDED):
            return OrderStatus.CANCELED  # Map stopped/suspended to canceled
        else:
            raise ValueError(f"Unknown Alpaca order status: {status}")


def parse_order_status_report(
    alpaca_order: dict[str, Any],
    account_id: AccountId,
    instrument_id: InstrumentId,
    ts_init: int,
) -> OrderStatusReport:
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
    parser = AlpacaEnumParser()

    venue_order_id = VenueOrderId(alpaca_order["id"])
    client_order_id = ClientOrderId(alpaca_order.get("client_order_id", alpaca_order["id"]))

    order_status = parser.parse_alpaca_order_status(alpaca_order["status"])
    order_side = parser.parse_alpaca_order_side(alpaca_order["side"])
    order_type = parser.parse_alpaca_order_type(alpaca_order["order_type"])
    time_in_force = parser.parse_alpaca_time_in_force(alpaca_order["time_in_force"])

    # Parse quantities
    quantity = Quantity.from_str(str(alpaca_order["qty"]))
    filled_qty = Quantity.from_str(str(alpaca_order.get("filled_qty", "0")))
    leaves_qty = quantity - filled_qty

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
        account_id=account_id,
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


def parse_fill_report(
    alpaca_fill: dict[str, Any],
    account_id: AccountId,
    instrument_id: InstrumentId,
    venue_order_id: VenueOrderId,
    client_order_id: ClientOrderId | None,
    ts_init: int,
) -> FillReport:
    """
    Parse an Alpaca trade/fill response into a FillReport.

    Parameters
    ----------
    alpaca_fill : dict[str, Any]
        The Alpaca trade/fill response dictionary.
    account_id : AccountId
        The account ID.
    instrument_id : InstrumentId
        The instrument ID.
    venue_order_id : VenueOrderId
        The venue order ID.
    client_order_id : ClientOrderId or None
        The client order ID (if available).
    ts_init : int
        The initialization timestamp (nanoseconds).

    Returns
    -------
    FillReport

    """
    parser = AlpacaEnumParser()

    # Parse trade ID
    trade_id = TradeId(alpaca_fill["id"])

    # Parse side
    order_side = parser.parse_alpaca_order_side(alpaca_fill["side"])

    # Parse quantity and price
    last_qty = Quantity.from_str(str(alpaca_fill["qty"]))
    last_px = Price.from_str(str(alpaca_fill["price"]))

    # Determine liquidity side (Alpaca doesn't provide this directly)
    # Default to TAKER as most fills are taker
    liquidity_side = LiquiditySide.TAKER

    # Commission is 0 for Alpaca (commission-free trading)
    commission = Money(0, instrument_id.symbol.value.split("/")[1])  # Quote currency

    # Parse timestamp
    # Alpaca timestamps are in RFC3339 format, need to convert to nanoseconds
    # For now, use ts_init as placeholder
    ts_event = ts_init

    return FillReport(
        account_id=account_id,
        instrument_id=instrument_id,
        venue_order_id=venue_order_id,
        trade_id=trade_id,
        order_side=order_side,
        last_qty=last_qty,
        last_px=last_px,
        commission=commission,
        liquidity_side=liquidity_side,
        report_id=UUID4(),
        ts_event=ts_event,
        ts_init=ts_init,
        client_order_id=client_order_id,
    )
