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

"""Configuration for Alpaca adapter."""

from __future__ import annotations

from nautilus_trader.config import LiveDataClientConfig
from nautilus_trader.config import LiveExecClientConfig
from nautilus_trader.config import PositiveInt


class AlpacaDataClientConfig(LiveDataClientConfig, frozen=True):
    """
    Configuration for ``AlpacaDataClient`` instances.

    Parameters
    ----------
    api_key : str, optional
        The Alpaca API key.
        If ``None`` then will source the `ALPACA_API_KEY` environment variable.
    api_secret : str, optional
        The Alpaca API secret.
        If ``None`` then will source the `ALPACA_API_SECRET` environment variable.
    environment : str, default "paper"
        The Alpaca environment: "paper" or "live".
    feed : str, default "iex"
        The market data feed: "iex" or "sip".
    http_base_url : str, optional
        Override base URL for HTTP requests.
        If None, will use environment-appropriate default.
    data_base_url : str, optional
        Override base URL for market data requests.
        If None, will use default data URL.
    http_timeout : PositiveInt, default 30
        The timeout (seconds) for HTTP requests.
    update_instruments_interval_mins : PositiveInt or None, default 60
        The interval (minutes) between reloading instruments from the venue.

    """

    api_key: str | None = None
    api_secret: str | None = None
    environment: str = "paper"
    feed: str = "iex"
    http_base_url: str | None = None
    data_base_url: str | None = None
    http_timeout: PositiveInt = 30
    update_instruments_interval_mins: PositiveInt | None = 60


class AlpacaExecClientConfig(LiveExecClientConfig, frozen=True):
    """
    Configuration for ``AlpacaExecutionClient`` instances.

    Parameters
    ----------
    api_key : str, optional
        The Alpaca API key.
        If ``None`` then will source the `ALPACA_API_KEY` environment variable.
    api_secret : str, optional
        The Alpaca API secret.
        If ``None`` then will source the `ALPACA_API_SECRET` environment variable.
    environment : str, default "paper"
        The Alpaca environment: "paper" or "live".
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

    api_key: str | None = None
    api_secret: str | None = None
    environment: str = "paper"
    http_timeout: PositiveInt = 30
    max_retries: PositiveInt | None = 3
    retry_delay_initial_ms: PositiveInt | None = 1_000
    retry_delay_max_ms: PositiveInt | None = 10_000
