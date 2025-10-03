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

import asyncio
from functools import lru_cache

from nautilus_trader.adapters.alpaca.config import AlpacaDataClientConfig
from nautilus_trader.adapters.alpaca.config import AlpacaExecClientConfig
from nautilus_trader.adapters.alpaca.data import AlpacaDataClient
from nautilus_trader.adapters.alpaca.execution import AlpacaExecutionClient
from nautilus_trader.adapters.alpaca.providers import AlpacaInstrumentProvider
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.live.factories import LiveExecClientFactory


@lru_cache(1)
def get_cached_alpaca_http_client(
    api_key: str | None = None,
    api_secret: str | None = None,
    base_url: str | None = None,
    timeout_secs: int = 60,
    is_paper: bool = True,
) -> nautilus_pyo3.AlpacaHttpClient:
    """
    Cache and return an Alpaca HTTP client with the given key and secret.

    If a cached client with matching parameters already exists, the cached client will be returned.

    Parameters
    ----------
    api_key : str, optional
        The API key for the client.
    api_secret : str, optional
        The API secret for the client.
    base_url : str, optional
        The base URL for the API endpoints.
    timeout_secs : int, default 60
        The timeout (seconds) for HTTP requests to Alpaca.
    is_paper : bool, default True
        If the client is connecting to the paper trading API.

    Returns
    -------
    AlpacaHttpClient

    """
    return nautilus_pyo3.AlpacaHttpClient(
        api_key=api_key,
        api_secret=api_secret,
        base_url=base_url,
        timeout_secs=timeout_secs,
        is_paper=is_paper,
    )


@lru_cache(1)
def get_cached_alpaca_instrument_provider(
    client: nautilus_pyo3.AlpacaHttpClient,
    config: InstrumentProviderConfig | None = None,
) -> AlpacaInstrumentProvider:
    """
    Cache and return a Alpaca instrument provider.

    If a cached provider already exists, then that provider will be returned.

    Parameters
    ----------
    client : AlpacaHttpClient
        The Alpaca HTTP client.
    config : InstrumentProviderConfig, optional
        The instrument provider configuration, by default None.

    Returns
    -------
    AlpacaInstrumentProvider

    """
    return AlpacaInstrumentProvider(
        client=client,
        config=config,
    )


class AlpacaLiveDataClientFactory(LiveDataClientFactory):
    """
    Provides a Alpaca live data client factory.
    """

    @staticmethod
    def create(  # type: ignore
        loop: asyncio.AbstractEventLoop,
        name: str,
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
        name : str
            The custom client ID.
        config : AlpacaDataClientConfig
            The client configuration.
        msgbus : MessageBus
            The message bus for the client.
        cache : Cache
            The cache for the client.
        clock: LiveClock
            The clock for the instrument provider.

        Returns
        -------
        AlpacaDataClient

        """
        client: nautilus_pyo3.AlpacaHttpClient = get_cached_alpaca_http_client(
            api_key=config.api_key,
            api_secret=config.api_secret,
            base_url=config.base_url_http,
            is_paper=config.is_paper,
        )
        provider = get_cached_alpaca_instrument_provider(
            client=client,
            config=config.instrument_provider,
        )
        return AlpacaDataClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
        )


class AlpacaLiveExecClientFactory(LiveExecClientFactory):
    """
    Provides a Alpaca live execution client factory.
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
        client: nautilus_pyo3.AlpacaHttpClient = get_cached_alpaca_http_client(
            api_key=config.api_key,
            api_secret=config.api_secret,
            base_url=config.base_url_http,
            is_paper=config.is_paper,
        )
        provider = get_cached_alpaca_instrument_provider(
            client=client,
            config=config.instrument_provider,
        )
        return AlpacaExecutionClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
        )
