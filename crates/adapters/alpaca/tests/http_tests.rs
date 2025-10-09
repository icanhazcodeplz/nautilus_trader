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

//! Integration tests for Alpaca HTTP client.

use nautilus_alpaca::{
    config::{AlpacaDataClientConfig, AlpacaExecClientConfig},
    http::AlpacaHttpClient,
};

#[test]
fn test_http_client_creation() {
    let client = AlpacaHttpClient::new(
        "https://paper-api.alpaca.markets".to_string(),
        Some(30),
        None,
        None,
        None,
    );

    assert!(client.is_ok());
    let client = client.unwrap();
    assert_eq!(client.base_url(), "https://paper-api.alpaca.markets");
    assert!(!client.has_credentials());
}

#[test]
fn test_http_client_with_credentials() {
    let client = AlpacaHttpClient::with_credentials(
        "test_key".to_string(),
        "test_secret".to_string(),
        "https://paper-api.alpaca.markets".to_string(),
        Some(30),
        None,
        None,
        None,
    );

    assert!(client.is_ok());
    let client = client.unwrap();
    assert!(client.has_credentials());
}

#[test]
fn test_data_client_config_defaults() {
    let config = AlpacaDataClientConfig::default();

    assert!(!config.has_api_credentials());
    assert_eq!(config.http_base_url(), "https://paper-api.alpaca.markets");
    assert_eq!(config.data_base_url(), "https://data.alpaca.markets");
}

#[test]
fn test_exec_client_config_defaults() {
    let config = AlpacaExecClientConfig::default();

    assert!(!config.has_api_credentials());
    assert_eq!(config.http_base_url(), "https://paper-api.alpaca.markets");
}

#[test]
fn test_data_client_config_with_credentials() {
    let config = AlpacaDataClientConfig {
        api_key: Some("test_key".to_string()),
        api_secret: Some("test_secret".to_string()),
        ..Default::default()
    };

    assert!(config.has_api_credentials());
}
