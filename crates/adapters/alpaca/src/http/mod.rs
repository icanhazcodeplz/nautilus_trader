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

//! HTTP client for the Alpaca REST API.

use std::{collections::HashMap, sync::Arc};

use nautilus_core::consts::NAUTILUS_USER_AGENT;
use nautilus_network::{
    http::HttpClient,
    retry::{RetryConfig, RetryManager},
};
use reqwest::{header::USER_AGENT, Method};
use serde::{de::DeserializeOwned, Serialize};
use tokio_util::sync::CancellationToken;

use crate::{
    common::consts::{HEADER_API_KEY_ID, HEADER_API_SECRET},
    error::{AlpacaError, AlpacaResult},
};

/// Inner HTTP client implementation for Alpaca.
pub struct AlpacaHttpInnerClient {
    base_url: String,
    client: HttpClient,
    api_key: Option<String>,
    api_secret: Option<String>,
    retry_manager: RetryManager<AlpacaError>,
    cancellation_token: CancellationToken,
}

impl std::fmt::Debug for AlpacaHttpInnerClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AlpacaHttpInnerClient")
            .field("base_url", &self.base_url)
            .field("has_credentials", &self.api_key.is_some())
            .finish()
    }
}

impl AlpacaHttpInnerClient {
    /// Creates a new Alpaca HTTP client.
    ///
    /// # Errors
    ///
    /// Returns an error if the retry manager cannot be created.
    pub fn new(
        base_url: String,
        timeout_secs: Option<u64>,
        max_retries: Option<u32>,
        retry_delay_ms: Option<u64>,
        retry_delay_max_ms: Option<u64>,
    ) -> AlpacaResult<Self> {
        let retry_config = RetryConfig {
            max_retries: max_retries.unwrap_or(3),
            initial_delay_ms: retry_delay_ms.unwrap_or(1000),
            max_delay_ms: retry_delay_max_ms.unwrap_or(10_000),
            backoff_factor: 2.0,
            jitter_ms: 1000,
            operation_timeout_ms: Some(60_000),
            immediate_first: false,
            max_elapsed_ms: Some(180_000),
        };

        let retry_manager = RetryManager::new(retry_config)
            .map_err(|e| AlpacaError::Generic(format!("Failed to create retry manager: {e}")))?;

        Ok(Self {
            base_url,
            client: HttpClient::new(
                Self::default_headers(),
                vec![],
                vec![],
                None,
                timeout_secs,
            ),
            api_key: None,
            api_secret: None,
            retry_manager,
            cancellation_token: CancellationToken::new(),
        })
    }

    /// Creates a new Alpaca HTTP client with credentials.
    ///
    /// # Errors
    ///
    /// Returns an error if the retry manager cannot be created.
    pub fn with_credentials(
        api_key: String,
        api_secret: String,
        base_url: String,
        timeout_secs: Option<u64>,
        max_retries: Option<u32>,
        retry_delay_ms: Option<u64>,
        retry_delay_max_ms: Option<u64>,
    ) -> AlpacaResult<Self> {
        let mut client = Self::new(
            base_url,
            timeout_secs,
            max_retries,
            retry_delay_ms,
            retry_delay_max_ms,
        )?;
        client.api_key = Some(api_key);
        client.api_secret = Some(api_secret);
        Ok(client)
    }

    fn default_headers() -> HashMap<String, String> {
        HashMap::from([(USER_AGENT.to_string(), NAUTILUS_USER_AGENT.to_string())])
    }

    fn auth_headers(&self) -> AlpacaResult<HashMap<String, String>> {
        let api_key = self
            .api_key
            .as_ref()
            .ok_or_else(|| AlpacaError::Authentication("API key not configured".into()))?;
        let api_secret = self
            .api_secret
            .as_ref()
            .ok_or_else(|| AlpacaError::Authentication("API secret not configured".into()))?;

        let mut headers = HashMap::new();
        headers.insert(HEADER_API_KEY_ID.to_string(), api_key.clone());
        headers.insert(HEADER_API_SECRET.to_string(), api_secret.clone());
        Ok(headers)
    }

    async fn send_request<T: DeserializeOwned>(
        &self,
        method: Method,
        endpoint: &str,
        body: Option<Vec<u8>>,
        authenticate: bool,
    ) -> AlpacaResult<T> {
        let url = format!("{}{endpoint}", self.base_url);
        let endpoint = endpoint.to_string();
        let method_clone = method.clone();
        let body_clone = body.clone();

        let operation = || {
            let url = url.clone();
            let method = method_clone.clone();
            let body = body_clone.clone();

            async move {
                let mut headers = Self::default_headers();

                if authenticate {
                    headers.extend(self.auth_headers()?);
                }

                if method == Method::POST || method == Method::PUT || method == Method::PATCH {
                    headers.insert("Content-Type".to_string(), "application/json".to_string());
                }

                let response = self
                    .client
                    .request(method, url, Some(headers), body, None, None)
                    .await
                    .map_err(|e| AlpacaError::HttpRequest(e.to_string()))?;

                if response.status.as_u16() >= 400 {
                    let body = String::from_utf8_lossy(&response.body).to_string();
                    return Err(AlpacaError::http_response(response.status.as_u16(), body));
                }

                let result: T = serde_json::from_slice(&response.body)?;
                Ok(result)
            }
        };

        let should_retry = |error: &AlpacaError| -> bool {
            match error {
                AlpacaError::HttpRequest(_) => true,
                AlpacaError::HttpResponse { status, .. } => *status >= 500,
                _ => false,
            }
        };

        let create_error = |msg: String| -> AlpacaError {
            if msg == "canceled" {
                AlpacaError::HttpRequest("Request canceled".to_string())
            } else {
                AlpacaError::Generic(msg)
            }
        };

        self.retry_manager
            .execute_with_retry_with_cancel(
                &endpoint,
                operation,
                should_retry,
                create_error,
                &self.cancellation_token,
            )
            .await
    }

    fn build_path<S: Serialize>(base: &str, params: &S) -> AlpacaResult<String> {
        let query = serde_urlencoded::to_string(params)
            .map_err(|e| AlpacaError::JsonParse(e.to_string()))?;
        if query.is_empty() {
            Ok(base.to_owned())
        } else {
            Ok(format!("{base}?{query}"))
        }
    }

    /// Returns the base URL.
    #[must_use]
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// Returns whether credentials are configured.
    #[must_use]
    pub fn has_credentials(&self) -> bool {
        self.api_key.is_some() && self.api_secret.is_some()
    }

    /// Cancel all pending HTTP requests.
    pub fn cancel_all_requests(&self) {
        self.cancellation_token.cancel();
    }

    /// Get the cancellation token for this client.
    pub fn cancellation_token(&self) -> &CancellationToken {
        &self.cancellation_token
    }

    // ==========================================================================
    // Public API methods
    // ==========================================================================

    /// Gets account information.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn get_account<T: DeserializeOwned>(&self) -> AlpacaResult<T> {
        self.send_request(Method::GET, "/v2/account", None, true)
            .await
    }

    /// Gets assets list.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn get_assets<T: DeserializeOwned>(&self) -> AlpacaResult<T> {
        self.send_request(Method::GET, "/v2/assets", None, false)
            .await
    }

    /// Gets a specific asset.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn get_asset<T: DeserializeOwned>(&self, symbol: &str) -> AlpacaResult<T> {
        let endpoint = format!("/v2/assets/{symbol}");
        self.send_request(Method::GET, &endpoint, None, false)
            .await
    }

    /// Submits a new order.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn submit_order<T: DeserializeOwned>(
        &self,
        order_request: &serde_json::Value,
    ) -> AlpacaResult<T> {
        let body = serde_json::to_vec(order_request)?;
        self.send_request(Method::POST, "/v2/orders", Some(body), true)
            .await
    }

    /// Gets all orders.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn get_orders<T: DeserializeOwned, S: Serialize>(
        &self,
        params: &S,
    ) -> AlpacaResult<T> {
        let path = Self::build_path("/v2/orders", params)?;
        self.send_request(Method::GET, &path, None, true).await
    }

    /// Gets a specific order by ID.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn get_order<T: DeserializeOwned>(&self, order_id: &str) -> AlpacaResult<T> {
        let endpoint = format!("/v2/orders/{order_id}");
        self.send_request(Method::GET, &endpoint, None, true)
            .await
    }

    /// Cancels an order.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn cancel_order<T: DeserializeOwned>(&self, order_id: &str) -> AlpacaResult<T> {
        let endpoint = format!("/v2/orders/{order_id}");
        self.send_request(Method::DELETE, &endpoint, None, true)
            .await
    }

    /// Cancels all orders.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn cancel_all_orders<T: DeserializeOwned>(&self) -> AlpacaResult<T> {
        self.send_request(Method::DELETE, "/v2/orders", None, true)
            .await
    }

    /// Gets positions.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn get_positions<T: DeserializeOwned>(&self) -> AlpacaResult<T> {
        self.send_request(Method::GET, "/v2/positions", None, true)
            .await
    }

    /// Gets a specific position.
    ///
    /// # Errors
    ///
    /// Returns an error if the request fails or response cannot be parsed.
    pub async fn get_position<T: DeserializeOwned>(&self, symbol: &str) -> AlpacaResult<T> {
        let endpoint = format!("/v2/positions/{symbol}");
        self.send_request(Method::GET, &endpoint, None, true)
            .await
    }
}

/// Provides an HTTP client for the Alpaca REST API.
#[derive(Clone)]
#[cfg_attr(
    feature = "python",
    pyo3::pyclass(module = "nautilus_trader.core.nautilus_pyo3.adapters")
)]
pub struct AlpacaHttpClient {
    pub(crate) inner: Arc<AlpacaHttpInnerClient>,
}

impl std::fmt::Debug for AlpacaHttpClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AlpacaHttpClient")
            .field("inner", &self.inner)
            .finish()
    }
}

impl AlpacaHttpClient {
    /// Creates a new Alpaca HTTP client.
    ///
    /// # Errors
    ///
    /// Returns an error if the client cannot be created.
    pub fn new(
        base_url: String,
        timeout_secs: Option<u64>,
        max_retries: Option<u32>,
        retry_delay_ms: Option<u64>,
        retry_delay_max_ms: Option<u64>,
    ) -> AlpacaResult<Self> {
        Ok(Self {
            inner: Arc::new(AlpacaHttpInnerClient::new(
                base_url,
                timeout_secs,
                max_retries,
                retry_delay_ms,
                retry_delay_max_ms,
            )?),
        })
    }

    /// Creates a new Alpaca HTTP client with credentials.
    ///
    /// # Errors
    ///
    /// Returns an error if the client cannot be created.
    pub fn with_credentials(
        api_key: String,
        api_secret: String,
        base_url: String,
        timeout_secs: Option<u64>,
        max_retries: Option<u32>,
        retry_delay_ms: Option<u64>,
        retry_delay_max_ms: Option<u64>,
    ) -> AlpacaResult<Self> {
        Ok(Self {
            inner: Arc::new(AlpacaHttpInnerClient::with_credentials(
                api_key,
                api_secret,
                base_url,
                timeout_secs,
                max_retries,
                retry_delay_ms,
                retry_delay_max_ms,
            )?),
        })
    }

    /// Returns the base URL.
    #[must_use]
    pub fn base_url(&self) -> &str {
        self.inner.base_url()
    }

    /// Returns whether credentials are configured.
    #[must_use]
    pub fn has_credentials(&self) -> bool {
        self.inner.has_credentials()
    }

    /// Cancel all pending HTTP requests.
    pub fn cancel_all_requests(&self) {
        self.inner.cancel_all_requests();
    }

    /// Get the cancellation token for this client.
    pub fn cancellation_token(&self) -> &CancellationToken {
        self.inner.cancellation_token()
    }
}
