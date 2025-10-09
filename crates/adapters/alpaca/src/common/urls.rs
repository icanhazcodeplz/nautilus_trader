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

//! URL constants and helpers for the Alpaca adapter.

use super::enums::AlpacaEnvironment;

/// Returns the base URL for the Alpaca trading API based on the environment.
#[must_use]
pub fn alpaca_http_base_url(environment: AlpacaEnvironment) -> &'static str {
    match environment {
        AlpacaEnvironment::Paper => "https://paper-api.alpaca.markets",
        AlpacaEnvironment::Live => "https://api.alpaca.markets",
    }
}

/// Returns the base URL for the Alpaca market data API.
#[must_use]
pub const fn alpaca_data_base_url() -> &'static str {
    "https://data.alpaca.markets"
}

/// Returns the WebSocket URL for Alpaca trading stream based on the environment.
#[must_use]
pub fn alpaca_ws_trading_url(environment: AlpacaEnvironment) -> String {
    match environment {
        AlpacaEnvironment::Paper => "wss://paper-api.alpaca.markets/stream".to_string(),
        AlpacaEnvironment::Live => "wss://api.alpaca.markets/stream".to_string(),
    }
}

/// Returns the WebSocket URL for Alpaca market data stream.
///
/// # Arguments
///
/// * `feed` - The data feed (e.g., "iex", "sip").
/// * `version` - The API version (e.g., "v2").
#[must_use]
pub fn alpaca_ws_market_data_url(feed: &str, version: &str) -> String {
    format!("wss://stream.data.alpaca.markets/{version}/{feed}")
}

/// Returns the WebSocket URL for Alpaca crypto market data stream.
#[must_use]
pub fn alpaca_ws_crypto_url() -> String {
    "wss://stream.data.alpaca.markets/v1beta3/crypto/us".to_string()
}

////////////////////////////////////////////////////////////////////////////////
// Tests
////////////////////////////////////////////////////////////////////////////////

#[cfg(test)]
mod tests {
    use rstest::rstest;

    use super::*;

    #[rstest]
    fn test_http_base_url_paper() {
        let url = alpaca_http_base_url(AlpacaEnvironment::Paper);
        assert_eq!(url, "https://paper-api.alpaca.markets");
    }

    #[rstest]
    fn test_http_base_url_live() {
        let url = alpaca_http_base_url(AlpacaEnvironment::Live);
        assert_eq!(url, "https://api.alpaca.markets");
    }

    #[rstest]
    fn test_data_base_url() {
        let url = alpaca_data_base_url();
        assert_eq!(url, "https://data.alpaca.markets");
    }

    #[rstest]
    fn test_ws_trading_url_paper() {
        let url = alpaca_ws_trading_url(AlpacaEnvironment::Paper);
        assert_eq!(url, "wss://paper-api.alpaca.markets/stream");
    }

    #[rstest]
    fn test_ws_trading_url_live() {
        let url = alpaca_ws_trading_url(AlpacaEnvironment::Live);
        assert_eq!(url, "wss://api.alpaca.markets/stream");
    }

    #[rstest]
    fn test_ws_market_data_url() {
        let url = alpaca_ws_market_data_url("iex", "v2");
        assert_eq!(url, "wss://stream.data.alpaca.markets/v2/iex");
    }

    #[rstest]
    fn test_ws_crypto_url() {
        let url = alpaca_ws_crypto_url();
        assert_eq!(url, "wss://stream.data.alpaca.markets/v1beta3/crypto/us");
    }
}
