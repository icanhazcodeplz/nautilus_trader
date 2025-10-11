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

"""Tests for Alpaca instrument provider."""

from decimal import Decimal
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from nautilus_trader.adapters.alpaca.providers import AlpacaInstrumentProvider
from nautilus_trader.model.identifiers import InstrumentId


class TestAlpacaInstrumentProvider:
    """Tests for AlpacaInstrumentProvider."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock HTTP client
        self.mock_client = MagicMock()
        self.mock_client.get_assets = AsyncMock()

        # Create mock clock
        self.mock_clock = MagicMock()
        self.mock_clock.timestamp_ns.return_value = 1234567890000000000

        # Create provider
        self.provider = AlpacaInstrumentProvider(
            client=self.mock_client,
            clock=self.mock_clock,
            config=None,
        )

    @pytest.mark.asyncio
    async def test_load_all_async_with_mock_assets(self):
        """Test loading all instruments with mock asset data."""
        # Arrange
        mock_assets = [
            {
                "id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "status": "active",
                "tradable": True,
                "marginable": True,
                "shortable": True,
                "easy_to_borrow": True,
                "fractionable": True,
            },
            {
                "id": "c0c7ee9e-9c9c-59b0-cb57-c0e65017f526",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "symbol": "TSLA",
                "name": "Tesla Inc.",
                "status": "active",
                "tradable": True,
                "marginable": True,
                "shortable": True,
                "easy_to_borrow": False,
                "fractionable": True,
            },
        ]

        self.mock_client.get_assets.return_value = mock_assets

        # Act
        await self.provider.load_all_async()

        # Assert
        assert len(self.provider.list_all()) == 2
        assert self.mock_client.get_assets.called

        # Check instruments were created
        aapl = self.provider.find(InstrumentId.from_str("AAPL.ALPACA"))
        assert aapl is not None
        assert aapl.id.symbol.value == "AAPL"
        assert str(aapl.price_precision) == "2"
        assert aapl.maker_fee == Decimal("0")
        assert aapl.taker_fee == Decimal("0")

        tsla = self.provider.find(InstrumentId.from_str("TSLA.ALPACA"))
        assert tsla is not None
        assert tsla.id.symbol.value == "TSLA"

    @pytest.mark.asyncio
    async def test_load_all_async_with_filters(self):
        """Test loading instruments with filters."""
        # Arrange
        mock_assets = [
            {
                "id": "1",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "status": "active",
                "tradable": True,
                "fractionable": True,
            },
            {
                "id": "2",
                "class": "us_equity",
                "exchange": "NYSE",
                "symbol": "INACTIVE",
                "name": "Inactive Stock",
                "status": "inactive",
                "tradable": False,
                "fractionable": False,
            },
        ]

        self.mock_client.get_assets.return_value = mock_assets

        # Act
        await self.provider.load_all_async(filters={"status": "active", "tradable": True})

        # Assert
        # Only AAPL should be loaded (INACTIVE is filtered out)
        instruments = self.provider.list_all()
        assert len(instruments) == 1
        assert instruments[0].id.symbol.value == "AAPL"

    @pytest.mark.asyncio
    async def test_parse_asset_fractionable(self):
        """Test parsing a fractionable asset."""
        # Arrange
        asset_data = {
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "fractionable": True,
        }

        # Act
        self.provider._parse_asset(asset_data)

        # Assert
        instrument = self.provider.find(InstrumentId.from_str("AAPL.ALPACA"))
        assert instrument is not None
        # Fractionable stocks have higher size precision
        assert instrument.size_precision == 9

    @pytest.mark.asyncio
    async def test_parse_asset_non_fractionable(self):
        """Test parsing a non-fractionable asset."""
        # Arrange
        asset_data = {
            "symbol": "TSLA",
            "name": "Tesla Inc.",
            "class": "us_equity",
            "exchange": "NASDAQ",
            "fractionable": False,
        }

        # Act
        self.provider._parse_asset(asset_data)

        # Assert
        instrument = self.provider.find(InstrumentId.from_str("TSLA.ALPACA"))
        assert instrument is not None
        # Non-fractionable stocks have 0 size precision (whole shares only)
        assert instrument.size_precision == 0
