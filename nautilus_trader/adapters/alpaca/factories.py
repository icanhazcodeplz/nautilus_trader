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

from typing import TYPE_CHECKING

from nautilus_trader.adapters.alpaca.data import AlpacaDataClient
from nautilus_trader.adapters.alpaca.execution import AlpacaExecutionClient
from nautilus_trader.adapters.alpaca.http import get_alpaca_http_client
from nautilus_trader.adapters.alpaca.providers import get_alpaca_instrument_provider
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.live.factories import LiveExecClientFactory

if TYPE_CHECKING:
    import asyncio

    from nautilus_trader.adapters.alpaca.config import AlpacaDataClientConfig
    from nautilus_trader.adapters.alpaca.config import AlpacaExecClientConfig
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus

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
        http_client = get_alpaca_http_client(
            paper=config.paper,
            timeout=config.http_timeout,
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

        http_client = get_alpaca_http_client(
            paper=config.paper,
            timeout=config.http_timeout,
        )

        # Create instrument provider if configured
        instrument_provider = None
        if config.instrument_provider:
            instrument_provider = get_alpaca_instrument_provider(
                client=http_client,
                clock=clock,
                config=config.instrument_provider,
            )

        return AlpacaDataClient(
            loop=loop,
            http_client=http_client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
            name=name,
        )
