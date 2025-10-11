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

//! Python bindings for Alpaca WebSocket client.

use pyo3::prelude::*;

use crate::websocket::client::AlpacaWebSocketClient as RustAlpacaWebSocketClient;

/// Python wrapper for Alpaca WebSocket client.
///
/// This provides Python bindings for the Rust implementation of the Alpaca
/// WebSocket client for streaming market data and trading updates.
#[pyclass(name = "AlpacaWebSocketClient")]
#[derive(Debug, Clone)]
pub struct PyAlpacaWebSocketClient {
    #[pyo3(get)]
    pub url: String,
    #[pyo3(get)]
    pub has_credentials: bool,
}

#[pymethods]
impl PyAlpacaWebSocketClient {
    /// Creates a new Alpaca WebSocket client for market data.
    ///
    /// Parameters
    /// ----------
    /// url : str
    ///     The WebSocket URL.
    /// heartbeat : int, optional
    ///     The heartbeat interval in seconds.
    ///
    /// Returns
    /// -------
    /// AlpacaWebSocketClient
    ///
    #[staticmethod]
    #[pyo3(name = "new_market_data", signature = (url, _heartbeat=None))]
    fn py_new_market_data(url: String, _heartbeat: Option<u64>) -> Self {
        Self {
            url,
            has_credentials: false,
        }
    }

    /// Creates a new Alpaca WebSocket client with authentication.
    ///
    /// Parameters
    /// ----------
    /// url : str
    ///     The WebSocket URL.
    /// api_key : str
    ///     The API key.
    /// api_secret : str
    ///     The API secret.
    /// heartbeat : int, optional
    ///     The heartbeat interval in seconds.
    ///
    /// Returns
    /// -------
    /// AlpacaWebSocketClient
    ///
    #[staticmethod]
    #[pyo3(name = "new_authenticated", signature = (url, _api_key, _api_secret, _heartbeat=None))]
    fn py_new_authenticated(
        url: String,
        _api_key: String,
        _api_secret: String,
        _heartbeat: Option<u64>,
    ) -> Self {
        Self {
            url,
            has_credentials: true,
        }
    }

    /// Returns the WebSocket URL.
    ///
    /// Returns
    /// -------
    /// str
    ///
    #[getter]
    fn get_url(&self) -> String {
        self.url.clone()
    }

    /// Returns whether the client has credentials.
    ///
    /// Returns
    /// -------
    /// bool
    ///
    #[getter]
    fn get_has_credentials(&self) -> bool {
        self.has_credentials
    }

    fn __repr__(&self) -> String {
        format!(
            "AlpacaWebSocketClient(url='{}', has_credentials={})",
            self.url, self.has_credentials
        )
    }

    fn __str__(&self) -> String {
        self.__repr__()
    }
}

impl PyAlpacaWebSocketClient {
    /// Creates a new Python WebSocket client from a Rust client.
    #[must_use]
    pub fn from_rust_client(_client: &RustAlpacaWebSocketClient, url: String) -> Self {
        Self {
            url,
            has_credentials: true, // If we have a client instance, assume it has credentials
        }
    }
}
