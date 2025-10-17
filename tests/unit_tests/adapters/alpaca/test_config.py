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

"""Tests for Alpaca configuration."""

from nautilus_trader.adapters.alpaca.config import AlpacaDataClientConfig
from nautilus_trader.adapters.alpaca.config import AlpacaExecClientConfig


class TestAlpacaDataClientConfig:
    """Tests for AlpacaDataClientConfig."""

    def test_default_config(self):
        # Arrange, Act
        config = AlpacaDataClientConfig()

        # Assert
        assert config.api_key is None
        assert config.api_secret is None
        assert config.environment == "paper"
        assert config.feed == "iex"
        assert config.http_base_url is None
        assert config.data_base_url is None
        assert config.http_timeout == 30
        assert config.update_instruments_interval_mins == 60

    def test_config_with_credentials(self):
        # Arrange, Act
        config = AlpacaDataClientConfig(
            api_key="test_key",
            api_secret="test_secret",
            environment="live",
            feed="sip",
        )

        # Assert
        assert config.api_key == "test_key"
        assert config.api_secret == "test_secret"
        assert config.environment == "live"
        assert config.feed == "sip"


class TestAlpacaExecClientConfig:
    """Tests for AlpacaExecClientConfig."""

    def test_default_config(self):
        # Arrange, Act
        config = AlpacaExecClientConfig()

        # Assert
        assert config.api_key is None
        assert config.api_secret is None
        assert config.environment == "paper"
        assert config.http_timeout == 30
        assert config.max_retries == 3
        assert config.retry_delay_initial_ms == 1_000
        assert config.retry_delay_max_ms == 10_000

    def test_config_with_credentials(self):
        # Arrange, Act
        config = AlpacaExecClientConfig(
            api_key="test_key",
            api_secret="test_secret",
            environment="live",
            max_retries=5,
        )

        # Assert
        assert config.api_key == "test_key"
        assert config.api_secret == "test_secret"
        assert config.environment == "live"
        assert config.max_retries == 5

