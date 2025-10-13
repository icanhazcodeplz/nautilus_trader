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

"""Factories for Alpaca adapter clients."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from nautilus_trader.adapters.alpaca.data import AlpacaDataClient
from nautilus_trader.adapters.alpaca.execution import AlpacaExecutionClient
from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.adapters.alpaca.providers import AlpacaInstrumentProvider
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.live.factories import LiveExecClientFactory


if TYPE_CHECKING:
    import asyncio

    from nautilus_trader.adapters.alpaca.config import AlpacaDataClientConfig
    from nautilus_trader.adapters.alpaca.config import AlpacaExecClientConfig
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus
    from nautilus_trader.config import InstrumentProviderConfig


@lru_cache(1)
def get_alpaca_http_client(
    base_url: str,
    api_key: str,
    api_secret: str,
    timeout: int,
    clock: LiveClock,
    data_base_url: str | None = None,
) -> AlpacaHttpClient:
    """
    Cache and return an Alpaca HTTP client with the given parameters.

    Parameters
    ----------
    base_url : str
        The base URL for the API.
    api_key : str
        The API key.
    api_secret : str
        The API secret.
    timeout : int
        The timeout for HTTP requests (seconds).
    clock : LiveClock
        The clock instance.
    data_base_url : str, optional
        The base URL for market data API requests.

    Returns
    -------
    AlpacaHttpClient

    """
    # Create a logger for the HTTP client
    from nautilus_trader.common.component import Logger

    logger = Logger(name="AlpacaHttpClient")

    return AlpacaHttpClient(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        timeout=timeout,
        logger=logger,
        data_base_url=data_base_url,
    )


@lru_cache(1)
def get_alpaca_instrument_provider(
    client: AlpacaHttpClient,
    clock: LiveClock,
    config: InstrumentProviderConfig,
) -> AlpacaInstrumentProvider:
    """
    Cache and return an Alpaca instrument provider.

    Parameters
    ----------
    client : AlpacaHttpClient
        The HTTP client.
    clock : LiveClock
        The clock instance.
    config : InstrumentProviderConfig
        The instrument provider configuration.

    Returns
    -------
    AlpacaInstrumentProvider

    """
    return AlpacaInstrumentProvider(
        client=client,
        clock=clock,
        config=config,
    )


class AlpacaLiveExecClientFactory(LiveExecClientFactory):
    """
    Provides an Alpaca live execution client factory.
    """

    @staticmethod
    def create(  # type: ignore
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: AlpacaExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> AlpacaExecutionClient:
        """
        Create a new Alpaca execution client.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            The event loop for the client.
        name : str
            The custom client ID.
        config : AlpacaExecClientConfig
            The client configuration.
        msgbus : MessageBus
            The message bus for the client.
        cache : Cache
            The cache for the client.
        clock : LiveClock
            The clock for the client.

        Returns
        -------
        AlpacaExecutionClient

        """
        import os

        # Get API credentials
        api_key = config.api_key or os.getenv("ALPACA_API_KEY")
        api_secret = config.api_secret or os.getenv("ALPACA_API_SECRET")

        if not api_key or not api_secret:
            raise ValueError("Alpaca API credentials not provided")

        # Determine base URL
        if config.http_base_url:
            http_base_url = config.http_base_url
        elif config.environment == "live":
            http_base_url = "https://api.alpaca.markets"
        else:
            http_base_url = "https://paper-api.alpaca.markets"

        # Create HTTP client
        http_client = get_alpaca_http_client(
            base_url=http_base_url,
            api_key=api_key,
            api_secret=api_secret,
            timeout=config.http_timeout,
            clock=clock,
        )

        # Create instrument provider if configured
        instrument_provider = None
        if config.instrument_provider:
            instrument_provider = get_alpaca_instrument_provider(
                client=http_client,
                clock=clock,
                config=config.instrument_provider,
            )

        # Create execution client
        client = AlpacaExecutionClient(
            loop=loop,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
            name=name,
            instrument_provider=instrument_provider
        )

        return client


class AlpacaLiveDataClientFactory(LiveDataClientFactory):
    """
    Provides an Alpaca live data client factory.
    """

    @staticmethod
    def create(  # type: ignore
        loop: asyncio.AbstractEventLoop,
        name: str | None,
        config: AlpacaDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> AlpacaDataClient:
        """
        Create a new Alpaca data client.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            The event loop for the client.
        name : str, optional
            The custom client ID.
        config : AlpacaDataClientConfig
            The client configuration.
        msgbus : MessageBus
            The message bus for the client.
        cache : Cache
            The cache for the client.
        clock : LiveClock
            The clock for the client.

        Returns
        -------
        AlpacaDataClient

        """
        import os

        # Get API credentials
        api_key = config.api_key or os.getenv("ALPACA_API_KEY")
        api_secret = config.api_secret or os.getenv("ALPACA_API_SECRET")

        if not api_key or not api_secret:
            raise ValueError("Alpaca API credentials not provided")

        # Determine HTTP base URL
        if config.http_base_url:
            http_base_url = config.http_base_url
        elif config.environment == "live":
            http_base_url = "https://api.alpaca.markets"
        else:
            http_base_url = "https://paper-api.alpaca.markets"

        # Determine WebSocket base URL (for data streaming)
        # if config.ws_base_url:
        #     ws_base_url = config.ws_base_url
        # Paper trading uses same data feed as live
        if config.feed == "sip":
            ws_base_url = "wss://stream.data.alpaca.markets/v2/sip"
        else:
            ws_base_url = "wss://stream.data.alpaca.markets/v2/iex"

        # Determine market data base URL
        data_base_url = config.data_base_url or "https://data.alpaca.markets"

        # Create HTTP client
        http_client = get_alpaca_http_client(
            base_url=http_base_url,
            api_key=api_key,
            api_secret=api_secret,
            timeout=config.http_timeout,
            clock=clock,
            data_base_url=data_base_url,
        )

        # Create instrument provider if configured
        instrument_provider = None
        if config.instrument_provider:
            instrument_provider = get_alpaca_instrument_provider(
                client=http_client,
                clock=clock,
                config=config.instrument_provider,
            )

        # Create data client
        return AlpacaDataClient(
            loop=loop,
            http_client=http_client,
            ws_base_url=ws_base_url,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
            name=name,
        )
