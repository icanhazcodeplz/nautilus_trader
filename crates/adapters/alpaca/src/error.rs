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

//! Error types for the Alpaca adapter.

use thiserror::Error;

/// Represents errors that can occur when using the Alpaca adapter.
#[derive(Error, Debug, Clone)]
pub enum AlpacaError {
    /// HTTP request error.
    #[error("HTTP request error: {0}")]
    HttpRequest(String),

    /// HTTP response error with status code.
    #[error("HTTP response error {status}: {message}")]
    HttpResponse { status: u16, message: String },

    /// WebSocket connection error.
    #[error("WebSocket connection error: {0}")]
    WebSocketConnection(String),

    /// WebSocket message error.
    #[error("WebSocket message error: {0}")]
    WebSocketMessage(String),

    /// JSON serialization/deserialization error.
    #[error("JSON error: {0}")]
    JsonParse(String),

    /// Authentication error.
    #[error("Authentication error: {0}")]
    Authentication(String),

    /// Invalid configuration error.
    #[error("Invalid configuration: {0}")]
    InvalidConfiguration(String),

    /// Invalid data error.
    #[error("Invalid data: {0}")]
    InvalidData(String),

    /// Invalid order error.
    #[error("Invalid order: {0}")]
    InvalidOrder(String),

    /// Rate limit exceeded.
    #[error("Rate limit exceeded: {0}")]
    RateLimit(String),

    /// API error with code.
    #[error("API error {code}: {message}")]
    Api { code: String, message: String },

    /// Generic error with message.
    #[error("{0}")]
    Generic(String),
}

impl AlpacaError {
    /// Creates a new HTTP response error.
    #[must_use]
    pub fn http_response(status: u16, message: impl Into<String>) -> Self {
        Self::HttpResponse {
            status,
            message: message.into(),
        }
    }

    /// Creates a new API error.
    #[must_use]
    pub fn api(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::Api {
            code: code.into(),
            message: message.into(),
        }
    }
}

/// Result type for Alpaca operations.
pub type AlpacaResult<T> = Result<T, AlpacaError>;

impl From<serde_json::Error> for AlpacaError {
    fn from(err: serde_json::Error) -> Self {
        Self::JsonParse(err.to_string())
    }
}

impl From<reqwest::Error> for AlpacaError {
    fn from(err: reqwest::Error) -> Self {
        if let Some(status) = err.status() {
            Self::HttpResponse {
                status: status.as_u16(),
                message: err.to_string(),
            }
        } else {
            Self::HttpRequest(err.to_string())
        }
    }
}

impl From<tokio_tungstenite::tungstenite::Error> for AlpacaError {
    fn from(err: tokio_tungstenite::tungstenite::Error) -> Self {
        Self::WebSocketConnection(err.to_string())
    }
}
