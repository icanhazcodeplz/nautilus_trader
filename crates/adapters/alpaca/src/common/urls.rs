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

//! URL helpers and endpoint metadata for ALPACA services.

use nautilus_core::env::get_env_var;

/// ALPACA endpoint types for determining URL and authentication requirements.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "python", pyo3::pyclass)]
pub enum ALPACAEndpointType {
    Public,
    Private,
    Business,
}

/// Checks if endpoint requires authentication.
pub fn requires_authentication(endpoint_type: ALPACAEndpointType) -> bool {
    matches!(
        endpoint_type,
        ALPACAEndpointType::Private | ALPACAEndpointType::Business
    )
}

/// Gets the HTTP base URL.
pub fn get_http_base_url() -> String {
    get_env_var("ALPACA_BASE_URL_HTTP").unwrap_or_else(|_| "https://www.alpaca.com".to_string())
}

/// Gets the WebSocket base URL for public data (market data).
pub fn get_ws_base_url_public(is_demo: bool) -> String {
    if is_demo {
        get_env_var("ALPACA_DEMO_BASE_URL_WS_PUBLIC")
            .unwrap_or_else(|_| "wss://wspap.alpaca.com:8443/ws/v5/public".to_string())
    } else {
        get_env_var("ALPACA_BASE_URL_WS_PUBLIC")
            .unwrap_or_else(|_| "wss://ws.alpaca.com:8443/ws/v5/public".to_string())
    }
}

/// Gets the WebSocket base URL for private data (account/order management).
pub fn get_ws_base_url_private(is_demo: bool) -> String {
    if is_demo {
        get_env_var("ALPACA_DEMO_BASE_URL_WS_PRIVATE")
            .unwrap_or_else(|_| "wss://wspap.alpaca.com:8443/ws/v5/private".to_string())
    } else {
        get_env_var("ALPACA_BASE_URL_WS_PRIVATE")
            .unwrap_or_else(|_| "wss://ws.alpaca.com:8443/ws/v5/private".to_string())
    }
}

/// Gets the WebSocket base URL for business data (bars/candlesticks).
pub fn get_ws_base_url_business(is_demo: bool) -> String {
    if is_demo {
        get_env_var("ALPACA_DEMO_BASE_URL_WS_BUSINESS")
            .unwrap_or_else(|_| "wss://wspap.alpaca.com:8443/ws/v5/business".to_string())
    } else {
        get_env_var("ALPACA_BASE_URL_WS_BUSINESS")
            .unwrap_or_else(|_| "wss://ws.alpaca.com:8443/ws/v5/business".to_string())
    }
}

/// Gets WebSocket URL by endpoint type.
pub fn get_ws_url(endpoint_type: ALPACAEndpointType, is_demo: bool) -> String {
    match endpoint_type {
        ALPACAEndpointType::Public => get_ws_base_url_public(is_demo),
        ALPACAEndpointType::Private => get_ws_base_url_private(is_demo),
        ALPACAEndpointType::Business => get_ws_base_url_business(is_demo),
    }
}

/// Gets the WebSocket base URL (backward compatibility - defaults to private).
///
/// .. deprecated::
///     Use get_ws_base_url_public() or get_ws_base_url_private() instead.
pub fn get_ws_base_url(is_demo: bool) -> String {
    get_ws_base_url_private(is_demo)
}

#[cfg(test)]
mod tests {
    use rstest::rstest;

    use super::*;

    #[rstest]
    fn test_endpoint_authentication() {
        assert!(!requires_authentication(ALPACAEndpointType::Public));
        assert!(requires_authentication(ALPACAEndpointType::Private));
        assert!(requires_authentication(ALPACAEndpointType::Business));
    }

    #[rstest]
    fn test_http_base_url() {
        assert_eq!(get_http_base_url(), "https://www.alpaca.com");
    }

    #[rstest]
    fn test_ws_urls_production() {
        assert_eq!(
            get_ws_base_url_public(false),
            "wss://ws.alpaca.com:8443/ws/v5/public"
        );
        assert_eq!(
            get_ws_base_url_private(false),
            "wss://ws.alpaca.com:8443/ws/v5/private"
        );
        assert_eq!(
            get_ws_base_url_business(false),
            "wss://ws.alpaca.com:8443/ws/v5/business"
        );
    }

    #[rstest]
    fn test_ws_urls_demo() {
        assert_eq!(
            get_ws_base_url_public(true),
            "wss://wspap.alpaca.com:8443/ws/v5/public"
        );
        assert_eq!(
            get_ws_base_url_private(true),
            "wss://wspap.alpaca.com:8443/ws/v5/private"
        );
        assert_eq!(
            get_ws_base_url_business(true),
            "wss://wspap.alpaca.com:8443/ws/v5/business"
        );
    }

    #[rstest]
    fn test_get_ws_url_by_type() {
        assert_eq!(
            get_ws_url(ALPACAEndpointType::Public, false),
            get_ws_base_url_public(false)
        );
        assert_eq!(
            get_ws_url(ALPACAEndpointType::Private, false),
            get_ws_base_url_private(false)
        );
        assert_eq!(
            get_ws_url(ALPACAEndpointType::Business, false),
            get_ws_base_url_business(false)
        );
    }
}
