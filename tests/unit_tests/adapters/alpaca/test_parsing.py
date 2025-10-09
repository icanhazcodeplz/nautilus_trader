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

"""Tests for Alpaca parsing utilities."""

import pytest

from nautilus_trader.adapters.alpaca.enums import AlpacaOrderSide
from nautilus_trader.adapters.alpaca.enums import AlpacaOrderStatus
from nautilus_trader.adapters.alpaca.enums import AlpacaOrderType
from nautilus_trader.adapters.alpaca.enums import AlpacaTimeInForce
from nautilus_trader.adapters.alpaca.parsing import AlpacaEnumParser
from nautilus_trader.adapters.alpaca.parsing import parse_order_status_report
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import InstrumentId


class TestAlpacaEnumParser:
    """Tests for AlpacaEnumParser."""

    def test_parse_alpaca_order_side_buy(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_side(AlpacaOrderSide.BUY)

        # Assert
        assert result == OrderSide.BUY

    def test_parse_alpaca_order_side_sell(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_side(AlpacaOrderSide.SELL)

        # Assert
        assert result == OrderSide.SELL

    def test_parse_nautilus_order_side_buy(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_nautilus_order_side(OrderSide.BUY)

        # Assert
        assert result == AlpacaOrderSide.BUY

    def test_parse_nautilus_order_side_sell(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_nautilus_order_side(OrderSide.SELL)

        # Assert
        assert result == AlpacaOrderSide.SELL

    def test_parse_alpaca_order_type_market(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_type(AlpacaOrderType.MARKET)

        # Assert
        assert result == OrderType.MARKET

    def test_parse_alpaca_order_type_limit(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_type(AlpacaOrderType.LIMIT)

        # Assert
        assert result == OrderType.LIMIT

    def test_parse_alpaca_order_type_stop_limit(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_type(AlpacaOrderType.STOP_LIMIT)

        # Assert
        assert result == OrderType.STOP_LIMIT

    def test_parse_nautilus_order_type_market(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_nautilus_order_type(OrderType.MARKET)

        # Assert
        assert result == AlpacaOrderType.MARKET

    def test_parse_nautilus_order_type_limit(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_nautilus_order_type(OrderType.LIMIT)

        # Assert
        assert result == AlpacaOrderType.LIMIT

    def test_parse_nautilus_order_type_stop_limit(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_nautilus_order_type(OrderType.STOP_LIMIT)

        # Assert
        assert result == AlpacaOrderType.STOP_LIMIT

    def test_parse_alpaca_time_in_force_day(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_time_in_force(AlpacaTimeInForce.DAY)

        # Assert
        assert result == TimeInForce.DAY

    def test_parse_alpaca_time_in_force_gtc(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_time_in_force(AlpacaTimeInForce.GTC)

        # Assert
        assert result == TimeInForce.GTC

    def test_parse_nautilus_time_in_force_day(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_nautilus_time_in_force(TimeInForce.DAY)

        # Assert
        assert result == AlpacaTimeInForce.DAY

    def test_parse_nautilus_time_in_force_gtc(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_nautilus_time_in_force(TimeInForce.GTC)

        # Assert
        assert result == AlpacaTimeInForce.GTC

    def test_parse_alpaca_order_status_new(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_status(AlpacaOrderStatus.NEW)

        # Assert
        assert result == OrderStatus.ACCEPTED

    def test_parse_alpaca_order_status_filled(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_status(AlpacaOrderStatus.FILLED)

        # Assert
        assert result == OrderStatus.FILLED

    def test_parse_alpaca_order_status_partially_filled(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_status(AlpacaOrderStatus.PARTIALLY_FILLED)

        # Assert
        assert result == OrderStatus.PARTIALLY_FILLED

    def test_parse_alpaca_order_status_canceled(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_status(AlpacaOrderStatus.CANCELED)

        # Assert
        assert result == OrderStatus.CANCELED

    def test_parse_alpaca_order_status_rejected(self):
        # Arrange, Act
        result = AlpacaEnumParser.parse_alpaca_order_status(AlpacaOrderStatus.REJECTED)

        # Assert
        assert result == OrderStatus.REJECTED


class TestParseOrderStatusReport:
    """Tests for parse_order_status_report function."""

    def test_parse_order_status_report_market_order(self):
        # Arrange
        alpaca_order = {
            "id": "test-order-123",
            "client_order_id": "client-order-456",
            "symbol": "AAPL",
            "side": "buy",
            "order_type": "market",
            "time_in_force": "day",
            "status": "filled",
            "qty": "100",
            "filled_qty": "100",
            "filled_avg_price": "150.50",
        }

        account_id = AccountId("ALPACA-001")
        instrument_id = InstrumentId.from_str("AAPL.ALPACA")
        ts_init = 1234567890000000000

        # Act
        report = parse_order_status_report(
            alpaca_order=alpaca_order,
            account_id=account_id,
            instrument_id=instrument_id,
            ts_init=ts_init,
        )

        # Assert
        assert report.account_id == account_id
        assert report.instrument_id == instrument_id
        assert report.venue_order_id.value == "test-order-123"
        assert report.client_order_id.value == "client-order-456"
        assert report.order_side == OrderSide.BUY
        assert report.order_type == OrderType.MARKET
        assert report.time_in_force == TimeInForce.DAY
        assert report.order_status == OrderStatus.FILLED
        assert str(report.quantity) == "100"
        assert str(report.filled_qty) == "100"

    def test_parse_order_status_report_limit_order(self):
        # Arrange
        alpaca_order = {
            "id": "test-order-789",
            "client_order_id": "client-order-101",
            "symbol": "TSLA",
            "side": "sell",
            "order_type": "limit",
            "limit_price": "800.00",
            "time_in_force": "gtc",
            "status": "new",
            "qty": "50",
            "filled_qty": "0",
        }

        account_id = AccountId("ALPACA-002")
        instrument_id = InstrumentId.from_str("TSLA.ALPACA")
        ts_init = 1234567890000000000

        # Act
        report = parse_order_status_report(
            alpaca_order=alpaca_order,
            account_id=account_id,
            instrument_id=instrument_id,
            ts_init=ts_init,
        )

        # Assert
        assert report.account_id == account_id
        assert report.instrument_id == instrument_id
        assert report.venue_order_id.value == "test-order-789"
        assert report.client_order_id.value == "client-order-101"
        assert report.order_side == OrderSide.SELL
        assert report.order_type == OrderType.LIMIT
        assert report.time_in_force == TimeInForce.GTC
        assert report.order_status == OrderStatus.ACCEPTED
        assert str(report.quantity) == "50"
        assert str(report.filled_qty) == "0"
        assert str(report.price) == "800.00"
