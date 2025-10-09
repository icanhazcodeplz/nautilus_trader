// -------------------------------------------------------------------------------------------------
//  Copyright (C) 2015-2025 Nautech Systems Pty Ltd. All rights reserved.
//  https://nautechsystems.io
//
//  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
//  You may not use this file except in compliance with the License.
//  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// -------------------------------------------------------------------------------------------------

//! Configuration structures for the Alpaca adapter.

use nautilus_model::identifiers::AccountId;

use crate::common::{
    consts::{
        DEFAULT_HEARTBEAT_INTERVAL_SECS, DEFAULT_HTTP_TIMEOUT_SECS, DEFAULT_MAX_RETRIES,
        DEFAULT_RETRY_DELAY_INITIAL_MS, DEFAULT_RETRY_DELAY_MAX_MS,
    },
    enums::{AlpacaAssetClass, AlpacaEnvironment, AlpacaFeed},
    urls::{alpaca_data_base_url, alpaca_http_base_url, alpaca_ws_market_data_url, alpaca_ws_trading_url},
};

/// Configuration for the Alpaca live data client.
#[derive(Clone, Debug)]
pub struct AlpacaDataClientConfig {
    /// API key for authenticated requests.
    pub api_key: Option<String>,
    /// API secret for authenticated requests.
    pub api_secret: Option<String>,
    /// Asset classes to subscribe to (e.g., UsEquity, Crypto, UsOption).
    pub asset_classes: Vec<AlpacaAssetClass>,
    /// Environment selection (Paper, Live).
    pub environment: AlpacaEnvironment,
    /// Market data feed (Iex, Sip).
    pub feed: AlpacaFeed,
    /// Optional override for the REST base URL.
    pub base_url_http: Option<String>,
    /// Optional override for the market data REST URL.
    pub base_url_data: Option<String>,
    /// Optional override for the market data WebSocket URL.
    pub base_url_ws_data: Option<String>,
    /// Optional REST timeout in seconds.
    pub http_timeout_secs: Option<u64>,
    /// Optional maximum retry attempts for REST requests.
    pub max_retries: Option<u32>,
    /// Optional initial retry backoff in milliseconds.
    pub retry_delay_initial_ms: Option<u64>,
    /// Optional maximum retry backoff in milliseconds.
    pub retry_delay_max_ms: Option<u64>,
    /// Optional heartbeat interval (seconds) for WebSocket clients.
    pub heartbeat_interval_secs: Option<u64>,
    /// Optional interval (minutes) for instrument refresh from REST.
    pub update_instruments_interval_mins: Option<u64>,
}

impl Default for AlpacaDataClientConfig {
    fn default() -> Self {
        Self {
            api_key: None,
            api_secret: None,
            asset_classes: vec![AlpacaAssetClass::UsEquity],
            environment: AlpacaEnvironment::Paper,
            feed: AlpacaFeed::Iex,
            base_url_http: None,
            base_url_data: None,
            base_url_ws_data: None,
            http_timeout_secs: Some(DEFAULT_HTTP_TIMEOUT_SECS),
            max_retries: Some(DEFAULT_MAX_RETRIES),
            retry_delay_initial_ms: Some(DEFAULT_RETRY_DELAY_INITIAL_MS),
            retry_delay_max_ms: Some(DEFAULT_RETRY_DELAY_MAX_MS),
            heartbeat_interval_secs: Some(DEFAULT_HEARTBEAT_INTERVAL_SECS),
            update_instruments_interval_mins: Some(60),
        }
    }
}

impl AlpacaDataClientConfig {
    /// Creates a configuration with default values.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Returns `true` if both API key and secret are available.
    #[must_use]
    pub fn has_api_credentials(&self) -> bool {
        self.api_key.is_some() && self.api_secret.is_some()
    }

    /// Returns the trading REST base URL, considering overrides and environment.
    #[must_use]
    pub fn http_base_url(&self) -> String {
        self.base_url_http
            .clone()
            .unwrap_or_else(|| alpaca_http_base_url(self.environment).to_string())
    }

    /// Returns the market data REST base URL.
    #[must_use]
    pub fn data_base_url(&self) -> String {
        self.base_url_data
            .clone()
            .unwrap_or_else(|| alpaca_data_base_url().to_string())
    }

    /// Returns the market data WebSocket URL.
    #[must_use]
    pub fn ws_data_url(&self) -> String {
        self.base_url_ws_data.clone().unwrap_or_else(|| {
            let feed = match self.feed {
                AlpacaFeed::Iex => "iex",
                AlpacaFeed::Sip => "sip",
            };
            alpaca_ws_market_data_url(feed, "v2")
        })
    }
}

/// Configuration for the Alpaca live execution client.
#[derive(Clone, Debug)]
pub struct AlpacaExecClientConfig {
    /// API key for authenticated requests.
    pub api_key: Option<String>,
    /// API secret for authenticated requests.
    pub api_secret: Option<String>,
    /// Asset classes to support (e.g., UsEquity, Crypto, UsOption).
    pub asset_classes: Vec<AlpacaAssetClass>,
    /// Environment selection (Paper, Live).
    pub environment: AlpacaEnvironment,
    /// Optional override for the REST base URL.
    pub base_url_http: Option<String>,
    /// Optional override for the trading WebSocket URL.
    pub base_url_ws_trading: Option<String>,
    /// Optional REST timeout in seconds.
    pub http_timeout_secs: Option<u64>,
    /// Optional maximum retry attempts for REST requests.
    pub max_retries: Option<u32>,
    /// Optional initial retry backoff in milliseconds.
    pub retry_delay_initial_ms: Option<u64>,
    /// Optional maximum retry backoff in milliseconds.
    pub retry_delay_max_ms: Option<u64>,
    /// Optional heartbeat interval (seconds) for WebSocket clients.
    pub heartbeat_interval_secs: Option<u64>,
    /// Optional account identifier to associate with the execution client.
    pub account_id: Option<AccountId>,
}

impl Default for AlpacaExecClientConfig {
    fn default() -> Self {
        Self {
            api_key: None,
            api_secret: None,
            asset_classes: vec![AlpacaAssetClass::UsEquity],
            environment: AlpacaEnvironment::Paper,
            base_url_http: None,
            base_url_ws_trading: None,
            http_timeout_secs: Some(DEFAULT_HTTP_TIMEOUT_SECS),
            max_retries: Some(DEFAULT_MAX_RETRIES),
            retry_delay_initial_ms: Some(DEFAULT_RETRY_DELAY_INITIAL_MS),
            retry_delay_max_ms: Some(DEFAULT_RETRY_DELAY_MAX_MS),
            heartbeat_interval_secs: Some(DEFAULT_HEARTBEAT_INTERVAL_SECS),
            account_id: None,
        }
    }
}

impl AlpacaExecClientConfig {
    /// Creates a configuration with default values.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Returns `true` if both API key and secret are available.
    #[must_use]
    pub fn has_api_credentials(&self) -> bool {
        self.api_key.is_some() && self.api_secret.is_some()
    }

    /// Returns the REST base URL, considering overrides and environment.
    #[must_use]
    pub fn http_base_url(&self) -> String {
        self.base_url_http
            .clone()
            .unwrap_or_else(|| alpaca_http_base_url(self.environment).to_string())
    }

    /// Returns the trading WebSocket URL, considering overrides and environment.
    #[must_use]
    pub fn ws_trading_url(&self) -> String {
        self.base_url_ws_trading
            .clone()
            .unwrap_or_else(|| alpaca_ws_trading_url(self.environment))
    }
}

////////////////////////////////////////////////////////////////////////////////
// Tests
////////////////////////////////////////////////////////////////////////////////

#[cfg(test)]
mod tests {
    use rstest::rstest;

    use super::*;

    #[rstest]
    fn test_data_config_default() {
        let config = AlpacaDataClientConfig::default();

        assert!(!config.has_api_credentials());
        assert_eq!(config.asset_classes, vec![AlpacaAssetClass::UsEquity]);
        assert_eq!(config.http_timeout_secs, Some(DEFAULT_HTTP_TIMEOUT_SECS));
        assert_eq!(
            config.heartbeat_interval_secs,
            Some(DEFAULT_HEARTBEAT_INTERVAL_SECS)
        );
    }

    #[rstest]
    fn test_data_config_with_credentials() {
        let config = AlpacaDataClientConfig {
            api_key: Some("test_key".to_string()),
            api_secret: Some("test_secret".to_string()),
            ..Default::default()
        };

        assert!(config.has_api_credentials());
    }

    #[rstest]
    fn test_data_config_http_url_paper() {
        let config = AlpacaDataClientConfig {
            environment: AlpacaEnvironment::Paper,
            ..Default::default()
        };

        assert_eq!(config.http_base_url(), "https://paper-api.alpaca.markets");
    }

    #[rstest]
    fn test_data_config_http_url_live() {
        let config = AlpacaDataClientConfig {
            environment: AlpacaEnvironment::Live,
            ..Default::default()
        };

        assert_eq!(config.http_base_url(), "https://api.alpaca.markets");
    }

    #[rstest]
    fn test_data_config_data_url() {
        let config = AlpacaDataClientConfig::default();

        assert_eq!(config.data_base_url(), "https://data.alpaca.markets");
    }

    #[rstest]
    fn test_data_config_ws_data_url_iex() {
        let config = AlpacaDataClientConfig {
            feed: AlpacaFeed::Iex,
            ..Default::default()
        };

        assert_eq!(
            config.ws_data_url(),
            "wss://stream.data.alpaca.markets/v2/iex"
        );
    }

    #[rstest]
    fn test_data_config_ws_data_url_sip() {
        let config = AlpacaDataClientConfig {
            feed: AlpacaFeed::Sip,
            ..Default::default()
        };

        assert_eq!(
            config.ws_data_url(),
            "wss://stream.data.alpaca.markets/v2/sip"
        );
    }

    #[rstest]
    fn test_exec_config_default() {
        let config = AlpacaExecClientConfig::default();

        assert!(!config.has_api_credentials());
        assert_eq!(config.asset_classes, vec![AlpacaAssetClass::UsEquity]);
        assert_eq!(config.http_timeout_secs, Some(DEFAULT_HTTP_TIMEOUT_SECS));
        assert_eq!(
            config.heartbeat_interval_secs,
            Some(DEFAULT_HEARTBEAT_INTERVAL_SECS)
        );
    }

    #[rstest]
    fn test_exec_config_with_credentials() {
        let config = AlpacaExecClientConfig {
            api_key: Some("test_key".to_string()),
            api_secret: Some("test_secret".to_string()),
            ..Default::default()
        };

        assert!(config.has_api_credentials());
    }

    #[rstest]
    fn test_exec_config_urls_paper() {
        let config = AlpacaExecClientConfig {
            environment: AlpacaEnvironment::Paper,
            ..Default::default()
        };

        assert_eq!(config.http_base_url(), "https://paper-api.alpaca.markets");
        assert_eq!(
            config.ws_trading_url(),
            "wss://paper-api.alpaca.markets/stream"
        );
    }

    #[rstest]
    fn test_exec_config_urls_live() {
        let config = AlpacaExecClientConfig {
            environment: AlpacaEnvironment::Live,
            ..Default::default()
        };

        assert_eq!(config.http_base_url(), "https://api.alpaca.markets");
        assert_eq!(
            config.ws_trading_url(),
            "wss://api.alpaca.markets/stream"
        );
    }
}
