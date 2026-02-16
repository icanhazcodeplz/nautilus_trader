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

import uuid
from decimal import Decimal
from typing import Any

from custom.utils.paths import DT_STR
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


def client_id_is_real(client_order_id: str) -> bool:
    # FIXME: This requires that DT_STR is part of the trader_id in TradingNodeConfig()
    return DT_STR in str(client_order_id)


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
        match tif:
            case TimeInForce.DAY:
                return AlpacaTimeInForce.DAY
            case TimeInForce.GTC:
                return AlpacaTimeInForce.GTC
            case TimeInForce.IOC:
                return AlpacaTimeInForce.IOC
            case TimeInForce.FOK:
                return AlpacaTimeInForce.FOK
            case TimeInForce.GTD:
                return AlpacaTimeInForce.GTD
            case TimeInForce.AT_THE_OPEN:
                return AlpacaTimeInForce.OPG
            case TimeInForce.AT_THE_CLOSE:
                return AlpacaTimeInForce.CLS
            case _:
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
            raise NotImplementedError(f"Trying to avoid every passing forward replaced orders")
            return OrderStatus.CANCELED  # Replaced order becomes canceled
        elif status in (AlpacaOrderStatus.STOPPED, AlpacaOrderStatus.SUSPENDED):
            return OrderStatus.CANCELED  # Map stopped/suspended to canceled
        else:
            raise ValueError(f"Unknown Alpaca order status: {status}")
