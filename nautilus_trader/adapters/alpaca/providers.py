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

"""Instrument provider for Alpaca adapter."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import TYPE_CHECKING

from nautilus_trader.adapters.alpaca.execution import ALPACA_VENUE
from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


if TYPE_CHECKING:
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.config import InstrumentProviderConfig


class AlpacaInstrumentProvider(InstrumentProvider):
    """
    Provides Nautilus instrument definitions from Alpaca Markets.

    Parameters
    ----------
    client : AlpacaHttpClient
        The Alpaca HTTP client.
    clock : LiveClock
        The clock instance.
    config : InstrumentProviderConfig, optional
        The instrument provider configuration.

    """

    def __init__(
        self,
        client: AlpacaHttpClient,
        clock: LiveClock,
        config: InstrumentProviderConfig | None = None,
    ) -> None:
        super().__init__(config=config)
        self._client = client
        self._clock = clock
        self._log_warnings = config.log_warnings if config else True

        # Add USD currency
        self.add_currency(Currency.from_str("USD"))


    async def load_all_async(self, filters: dict | None = None) -> None:
        """
        Load all tradeable instruments from Alpaca.

        Parameters
        ----------
        filters : dict, optional
            Filters to apply when loading instruments.
            Supported filters:
            - asset_class: "us_equity", "crypto", or "us_option"
            - status: "active" (default), "inactive"
            - tradable: True (default), False

        """
        filters_str = "..." if not filters else f" with filters {filters}..."
        self._log.info(f"Loading all instruments{filters_str}")

        # Get tradeable assets from Alpaca
        try:
            assets = await self._client.get_assets()

            # Apply filters if provided
            if filters:
                status = filters.get("status", "active")
                tradable = filters.get("tradable", True)
                asset_class = filters.get("asset_class")

                filtered_assets = []
                for asset in assets:
                    if status and asset.get("status") != status:
                        continue
                    if tradable is not None and asset.get("tradable") != tradable:
                        continue
                    if asset_class and asset.get("class") != asset_class:
                        continue
                    filtered_assets.append(asset)

                assets = filtered_assets

            # Parse each asset into a Nautilus instrument
            for asset_data in assets:
                try:
                    self._parse_asset(asset_data)
                except Exception as e:
                    if self._log_warnings:
                        self._log.warning(
                            f"Failed to parse asset {asset_data.get('symbol')}: {e}",
                        )

            self._log.info(f"Loaded {len(self._instruments)} instruments")

        except Exception as e:
            self._log.error(f"Failed to load instruments: {e}")
            raise

    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        """
        Load specific instruments by their IDs.

        Parameters
        ----------
        instrument_ids : list[InstrumentId]
            The instrument IDs to load.
        filters : dict, optional
            Not used for Alpaca (all filters applied at load_all level).

        """
        if not instrument_ids:
            self._log.warning("No instrument IDs given for loading")
            return

        # Check all instrument IDs are valid for Alpaca
        for instrument_id in instrument_ids:
            PyCondition.equal(
                instrument_id.venue,
                ALPACA_VENUE,
                "instrument_id.venue",
                "ALPACA",
            )

        # Load all instruments first
        await self.load_all_async(filters)

        # Filter to only requested instruments
        for instrument_id in instrument_ids:
            if instrument_id not in self._instruments:
                self._log.warning(f"Instrument {instrument_id} not found")

    async def load_async(
        self,
        instrument_id: InstrumentId,
        filters: dict | None = None,
    ) -> None:
        """
        Load a single instrument by ID.

        Parameters
        ----------
        instrument_id : InstrumentId
            The instrument ID to load.
        filters : dict, optional
            Not used for Alpaca.

        """
        PyCondition.not_none(instrument_id, "instrument_id")
        await self.load_ids_async([instrument_id], filters)

    def _parse_asset(self, asset_data: dict) -> None:
        """
        Parse an Alpaca asset into a Nautilus Equity instrument.

        Parameters
        ----------
        asset_data : dict
            The asset data from Alpaca API.

        """
        symbol_str = asset_data["symbol"]
        name = asset_data.get("name", symbol_str)

        # Create instrument ID
        symbol = Symbol(symbol_str)
        instrument_id = InstrumentId(symbol=symbol, venue=ALPACA_VENUE)

        # Get price/size precision (Alpaca doesn't provide this directly)
        # US stocks typically have 2 decimal places for price, 0 for quantity
        price_precision = 2
        size_precision = 0

        # Calculate increments
        price_increment = Price(Decimal(f"0.{'0' * (price_precision - 1)}1"), price_precision)
        size_increment = Quantity(Decimal("1"), size_precision)

        # Alpaca stocks are fractionable (can trade fractional shares)
        fractionable = asset_data.get("fractionable", False)
        if fractionable:
            size_precision = 9  # Allow up to 9 decimal places for fractional shares
            size_increment = Quantity(Decimal("0.000000001"), size_precision)

        # Minimum order size
        min_quantity = Quantity.from_str("1") if not fractionable else Quantity.from_str("0.000000001")

        # Create margin parameters (Alpaca uses simple margin)
        margin_init = Decimal("0.50")  # 50% initial margin (2x leverage)
        margin_maint = Decimal("0.25")  # 25% maintenance margin

        # Get USD currency
        currency = self.currency("USD")

        # Create timestamps
        ts_event = self._clock.timestamp_ns()
        ts_init = self._clock.timestamp_ns()

        # Create Equity instrument
        instrument = Equity(
            instrument_id=instrument_id,
            raw_symbol=Symbol(symbol_str),
            currency=currency,
            price_precision=price_precision,
            price_increment=price_increment,
            # size_precision=size_precision,
            # size_increment=size_increment,
            # multiplier=Quantity.from_int(1),
            lot_size=min_quantity,
            isin=asset_data.get("isin"),
            margin_init=margin_init,
            margin_maint=margin_maint,
            maker_fee=Decimal("0"),  # Alpaca is commission-free
            taker_fee=Decimal("0"),  # Alpaca is commission-free
            ts_event=ts_event,
            ts_init=ts_init,
            info={"name": name, "class": asset_data.get("class"), "exchange": asset_data.get("exchange")},
        )

        self.add(instrument)

    async def get_assets(self) -> list[dict]:
        """
        Fetch all assets from Alpaca API.

        Returns
        -------
        list[dict]
            List of asset dictionaries from Alpaca.

        """
        # Use the HTTP client get_assets endpoint
        # This wraps the /v2/assets endpoint
        return await self._client.get_assets()


@lru_cache(1)
def get_alpaca_instrument_provider(
    client: AlpacaHttpClient,
    clock: LiveClock,
    config: InstrumentProviderConfig,
) -> AlpacaInstrumentProvider:
    return AlpacaInstrumentProvider(
        client=client,
        clock=clock,
        config=config,
    )
