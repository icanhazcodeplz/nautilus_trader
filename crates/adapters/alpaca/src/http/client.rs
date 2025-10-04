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

//! Provides an ergonomic wrapper around the **ALPACA v5 REST API** –
//! <https://www.alpaca.com/docs-v5/en/>.
//!
//! The core type exported by this module is [`ALPACAHttpClient`].  It offers an
//! interface to all exchange endpoints currently required by NautilusTrader.
//!
//! Key responsibilities handled internally:
//! • Request signing and header composition for private routes (HMAC-SHA256).
//! • Rate-limiting based on the public ALPACA specification.
//! • Zero-copy deserialization of large JSON payloads into domain models.
//! • Conversion of raw exchange errors into the rich [`ALPACAHttpError`] enum.
//!
//! # Quick links to official docs
//! | Domain                               | ALPACA reference                                          |
//! |--------------------------------------|--------------------------------------------------------|
//! | Market data                          | <https://www.alpaca.com/docs-v5/en/#rest-api-market-data> |
//! | Account & positions                  | <https://www.alpaca.com/docs-v5/en/#rest-api-account>     |
//! | Funding & asset balances             | <https://www.alpaca.com/docs-v5/en/#rest-api-funding>     |

use std::{
    collections::HashMap,
    fmt::Debug,
    num::NonZeroU32,
    sync::{Arc, LazyLock, Mutex},
};

use ahash::AHashSet;
use chrono::{DateTime, Utc};
use nautilus_core::{
    UnixNanos, consts::NAUTILUS_USER_AGENT, env::get_env_var, time::get_atomic_clock_realtime,
};
use nautilus_model::{
    data::{Bar, BarType, IndexPriceUpdate, MarkPriceUpdate, TradeTick},
    enums::{AggregationSource, BarAggregation, OrderSide, OrderType, TriggerType},
    events::AccountState,
    identifiers::{AccountId, ClientOrderId, InstrumentId},
    instruments::{Instrument, InstrumentAny},
    reports::{FillReport, OrderStatusReport, PositionStatusReport},
    types::{Price, Quantity},
};
use nautilus_network::{
    http::HttpClient,
    ratelimiter::quota::Quota,
    retry::{RetryConfig, RetryManager},
};
use reqwest::{Method, StatusCode, header::USER_AGENT};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use tokio_util::sync::CancellationToken;
use ustr::Ustr;

use super::{
    error::ALPACAHttpError,
    models::{
        ALPACAAccount, ALPACAAsset, ALPACABar, ALPACABarsResponse, ALPACACancelAlgoOrderRequest,
        ALPACACancelAlgoOrderResponse, ALPACACalendar, ALPACAClock, ALPACALatestQuote,
        ALPACALatestTrade, ALPACAOrder, ALPACAOrderRequest, ALPACAPlaceAlgoOrderRequest,
        ALPACAPlaceAlgoOrderResponse, ALPACAPosition, ALPACAQuote, ALPACAQuotesResponse,
        ALPACASnapshot, ALPACATrade, ALPACATradesResponse,
    },
};
use crate::{
    common::{
        consts::{ALPACA_HTTP_URL, ALPACA_NAUTILUS_BROKER_ID, should_retry_error_code},
        credential::Credential,
        enums::{
            ALPACAInstrumentType, ALPACAPositionMode, ALPACASide, ALPACATradeMode,
        },
        models::ALPACAInstrument,
        parse::{
            alpaca_instrument_type, parse_account_state, parse_candlestick, parse_fill_report,
            parse_index_price_update, parse_instrument_any, parse_mark_price_update,
            parse_order_status_report, parse_position_status_report, parse_trade_tick,
        },
    },
    http::models::{ALPACABar as ALPACACandlestick},
};

/// Default Alpaca REST API rate limit.
///
/// Alpaca has a rate limit of 200 requests per minute for most endpoints.
/// We use a conservative 150 requests per minute to stay well within limits.
pub static ALPACA_REST_QUOTA: LazyLock<Quota> =
    LazyLock::new(|| Quota::per_minute(NonZeroU32::new(150).unwrap()));

/// Provides a HTTP client for connecting to the [ALPACA](https://alpaca.com) REST API.
///
/// This client wraps the underlying [`HttpClient`] to handle functionality
/// specific to ALPACA, such as request signing (for authenticated endpoints),
/// forming request URLs, and deserializing responses into specific data models.
pub struct ALPACAHttpInnerClient {
    base_url: String,
    client: HttpClient,
    credential: Option<Credential>,
    retry_manager: RetryManager<ALPACAHttpError>,
    cancellation_token: CancellationToken,
}

impl Default for ALPACAHttpInnerClient {
    fn default() -> Self {
        Self::new(None, Some(60), None, None, None)
            .expect("Failed to create default ALPACAHttpInnerClient")
    }
}

impl Debug for ALPACAHttpInnerClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let credential = self.credential.as_ref().map(|_| "<redacted>");
        f.debug_struct(stringify!(ALPACAHttpInnerClient))
            .field("base_url", &self.base_url)
            .field("credential", &credential)
            .finish_non_exhaustive()
    }
}

impl ALPACAHttpInnerClient {
    /// Cancel all pending HTTP requests.
    pub fn cancel_all_requests(&self) {
        self.cancellation_token.cancel();
    }

    /// Get the cancellation token for this client.
    pub fn cancellation_token(&self) -> &CancellationToken {
        &self.cancellation_token
    }

    /// Creates a new [`ALPACAHttpClient`] using the default ALPACA HTTP URL,
    /// optionally overridden with a custom base URL.
    ///
    /// This version of the client has **no credentials**, so it can only
    /// call publicly accessible endpoints.
    ///
    /// # Errors
    ///
    /// Returns an error if the retry manager cannot be created.
    pub fn new(
        base_url: Option<String>,
        timeout_secs: Option<u64>,
        max_retries: Option<u32>,
        retry_delay_ms: Option<u64>,
        retry_delay_max_ms: Option<u64>,
    ) -> Result<Self, ALPACAHttpError> {
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

        let retry_manager = RetryManager::new(retry_config).map_err(|e| {
            ALPACAHttpError::ValidationError(format!("Failed to create retry manager: {e}"))
        })?;

        Ok(Self {
            base_url: base_url.unwrap_or(ALPACA_HTTP_URL.to_string()),
            client: HttpClient::new(
                Self::default_headers(),
                vec![],
                vec![],
                Some(*ALPACA_REST_QUOTA),
                timeout_secs,
            ),
            credential: None,
            retry_manager,
            cancellation_token: CancellationToken::new(),
        })
    }

    /// Creates a new [`ALPACAHttpClient`] configured with credentials
    /// for authenticated requests, optionally using a custom base URL.
    ///
    /// # Errors
    ///
    /// Returns an error if the retry manager cannot be created.
    #[allow(clippy::too_many_arguments)]
    pub fn with_credentials(
        api_key: String,
        api_secret: String,
        base_url: String,
        timeout_secs: Option<u64>,
        max_retries: Option<u32>,
        retry_delay_ms: Option<u64>,
        retry_delay_max_ms: Option<u64>,
    ) -> Result<Self, ALPACAHttpError> {
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

        let retry_manager = RetryManager::new(retry_config).map_err(|e| {
            ALPACAHttpError::ValidationError(format!("Failed to create retry manager: {e}"))
        })?;

        Ok(Self {
            base_url,
            client: HttpClient::new(
                Self::default_headers(),
                vec![],
                vec![],
                Some(*ALPACA_REST_QUOTA),
                timeout_secs,
            ),
            credential: Some(Credential::new(api_key, api_secret)),
            retry_manager,
            cancellation_token: CancellationToken::new(),
        })
    }

    /// Builds the default headers to include with each request (e.g., `User-Agent`).
    fn default_headers() -> HashMap<String, String> {
        HashMap::from([(USER_AGENT.to_string(), NAUTILUS_USER_AGENT.to_string())])
    }

    /// Combine a base path with a `serde_urlencoded` query string if one exists.
    ///
    /// # Errors
    ///
    /// Returns an error if the query string serialization fails.
    fn build_path<S: Serialize>(base: &str, params: &S) -> Result<String, ALPACAHttpError> {
        let query = serde_urlencoded::to_string(params)
            .map_err(|e| ALPACAHttpError::JsonError(e.to_string()))?;
        if query.is_empty() {
            Ok(base.to_owned())
        } else {
            Ok(format!("{base}?{query}"))
        }
    }

    /// Adds Alpaca authentication headers to the request.
    ///
    /// Alpaca uses simple API key/secret authentication via headers:
    /// - APCA-API-KEY-ID: The API key
    /// - APCA-API-SECRET-KEY: The API secret
    ///
    /// # Errors
    ///
    /// Returns [`ALPACAHttpError::MissingCredentials`] if no credentials are set
    /// but the request requires authentication.
    fn sign_request(
        &self,
        _method: &Method,
        _path: &str,
        _body: Option<&[u8]>,
    ) -> Result<HashMap<String, String>, ALPACAHttpError> {
        let credential = match self.credential.as_ref() {
            Some(c) => c,
            None => return Err(ALPACAHttpError::MissingCredentials),
        };

        let mut headers = HashMap::new();
        headers.insert("APCA-API-KEY-ID".to_string(), credential.api_key().to_string());
        headers.insert("APCA-API-SECRET-KEY".to_string(), credential.api_secret().to_string());

        Ok(headers)
    }

    /// Sends an HTTP request to Alpaca and parses the response into `T`.
    ///
    /// Alpaca API returns direct JSON responses (not wrapped), so this method
    /// deserializes responses directly into the expected type.
    ///
    /// # Errors
    ///
    /// This function will return an error if:
    /// - The HTTP request fails.
    /// - Authentication is required but credentials are missing.
    /// - The response cannot be deserialized into the expected type.
    /// - The Alpaca API returns an error response.
    async fn send_request<T: DeserializeOwned>(
        &self,
        method: Method,
        path: &str,
        body: Option<Vec<u8>>,
        authenticate: bool,
    ) -> Result<T, ALPACAHttpError> {
        let url = format!("{}{path}", self.base_url);
        let endpoint = path;
        let method_clone = method.clone();
        let body_clone = body.clone();

        let operation = || {
            let url = url.clone();
            let method = method_clone.clone();
            let body = body_clone.clone();

            async move {
                let mut headers = if authenticate {
                    self.sign_request(&method, endpoint, body.as_deref())?
                } else {
                    HashMap::new()
                };

                // Always set Content-Type header when body is present
                if body.is_some() {
                    headers.insert("Content-Type".to_string(), "application/json".to_string());
                }

                let resp = self
                    .client
                    .request(method.clone(), url, Some(headers), body, None, None)
                    .await?;

                tracing::trace!("Response: {resp:?}");

                if resp.status.is_success() {
                    // Alpaca returns direct JSON responses (no wrapping)
                    let result: T = serde_json::from_slice(&resp.body).map_err(|e| {
                        tracing::error!("Failed to deserialize Alpaca response: {e}");
                        tracing::error!("Response body: {}", String::from_utf8_lossy(&resp.body));
                        ALPACAHttpError::JsonError(e.to_string())
                    })?;

                    Ok(result)
                } else {
                    let error_body = String::from_utf8_lossy(&resp.body);
                    tracing::error!(
                        "HTTP error {} with body: {error_body}",
                        resp.status.as_str()
                    );

                    // Try to parse Alpaca error response
                    #[derive(Deserialize)]
                    struct AlpacaError {
                        #[serde(default)]
                        code: Option<u32>,
                        #[serde(default)]
                        message: Option<String>,
                    }

                    if let Ok(parsed_error) = serde_json::from_slice::<AlpacaError>(&resp.body) {
                        return Err(ALPACAHttpError::AlpacaError {
                            error_code: parsed_error.code.unwrap_or(0).to_string(),
                            message: parsed_error.message.unwrap_or_else(|| error_body.to_string()),
                        });
                    }

                    Err(ALPACAHttpError::UnexpectedStatus {
                        status: StatusCode::from_u16(resp.status.as_u16()).unwrap(),
                        body: error_body.to_string(),
                    })
                }
            }
        };

        // Retry strategy: retry on network errors and HTTP 5xx/429
        let should_retry = |error: &ALPACAHttpError| -> bool {
            match error {
                ALPACAHttpError::HttpClientError(_) => true,
                ALPACAHttpError::UnexpectedStatus { status, .. } => {
                    status.as_u16() >= 500 || status.as_u16() == 429
                }
                ALPACAHttpError::AlpacaError { error_code, .. } => should_retry_error_code(error_code),
                _ => false,
            }
        };

        let create_error = |msg: String| -> ALPACAHttpError {
            if msg == "canceled" {
                ALPACAHttpError::ValidationError("Request canceled".to_string())
            } else {
                ALPACAHttpError::ValidationError(msg)
            }
        };

        self.retry_manager
            .execute_with_retry_with_cancel(
                endpoint,
                operation,
                should_retry,
                create_error,
                &self.cancellation_token,
            )
            .await
    }

    // =============================================================================
    // Market Data Endpoints
    // =============================================================================

    /// Requests a list of assets from Alpaca.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/get-v2-assets>
    pub async fn http_get_assets(
        &self,
        status: Option<&str>,
        asset_class: Option<&str>,
    ) -> Result<Vec<ALPACAAsset>, ALPACAHttpError> {
        let mut path = "/v2/assets".to_string();
        let mut params = vec![];

        if let Some(s) = status {
            params.push(format!("status={}", s));
        }
        if let Some(ac) = asset_class {
            params.push(format!("asset_class={}", ac));
        }

        if !params.is_empty() {
            path.push('?');
            path.push_str(&params.join("&"));
        }

        self.send_request(Method::GET, &path, None, false).await
    }

    /// Requests bars (candlesticks) for a symbol.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/stockbars-1>
    pub async fn http_get_bars(
        &self,
        symbol: &str,
        timeframe: &str,
        start: Option<&str>,
        end: Option<&str>,
        limit: Option<u32>,
    ) -> Result<ALPACABarsResponse, ALPACAHttpError> {
        let mut path = format!("/v2/stocks/{}/bars", symbol);
        let mut params = vec![format!("timeframe={}", timeframe)];

        if let Some(s) = start {
            params.push(format!("start={}", s));
        }
        if let Some(e) = end {
            params.push(format!("end={}", e));
        }
        if let Some(l) = limit {
            params.push(format!("limit={}", l));
        }

        path.push('?');
        path.push_str(&params.join("&"));

        self.send_request(Method::GET, &path, None, false).await
    }

    /// Requests trades for a symbol.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/stocktrades>
    pub async fn http_get_trades(
        &self,
        symbol: &str,
        start: Option<&str>,
        end: Option<&str>,
        limit: Option<u32>,
    ) -> Result<ALPACATradesResponse, ALPACAHttpError> {
        let mut path = format!("/v2/stocks/{}/trades", symbol);
        let mut params = vec![];

        if let Some(s) = start {
            params.push(format!("start={}", s));
        }
        if let Some(e) = end {
            params.push(format!("end={}", e));
        }
        if let Some(l) = limit {
            params.push(format!("limit={}", l));
        }

        if !params.is_empty() {
            path.push('?');
            path.push_str(&params.join("&"));
        }

        self.send_request(Method::GET, &path, None, false).await
    }

    /// Requests quotes for a symbol.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/stockquotes>
    pub async fn http_get_quotes(
        &self,
        symbol: &str,
        start: Option<&str>,
        end: Option<&str>,
        limit: Option<u32>,
    ) -> Result<ALPACAQuotesResponse, ALPACAHttpError> {
        let mut path = format!("/v2/stocks/{}/quotes", symbol);
        let mut params = vec![];

        if let Some(s) = start {
            params.push(format!("start={}", s));
        }
        if let Some(e) = end {
            params.push(format!("end={}", e));
        }
        if let Some(l) = limit {
            params.push(format!("limit={}", l));
        }

        if !params.is_empty() {
            path.push('?');
            path.push_str(&params.join("&"));
        }

        self.send_request(Method::GET, &path, None, false).await
    }

    /// Requests the latest quote for a symbol.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/stocklatestquote>
    pub async fn http_get_latest_quote(
        &self,
        symbol: &str,
    ) -> Result<ALPACALatestQuote, ALPACAHttpError> {
        let path = format!("/v2/stocks/{}/quotes/latest", symbol);
        self.send_request(Method::GET, &path, None, false).await
    }

    /// Requests the latest trade for a symbol.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/stocklatesttrade>
    pub async fn http_get_latest_trade(
        &self,
        symbol: &str,
    ) -> Result<ALPACALatestTrade, ALPACAHttpError> {
        let path = format!("/v2/stocks/{}/trades/latest", symbol);
        self.send_request(Method::GET, &path, None, false).await
    }

    /// Requests a snapshot of current market data for a symbol.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/stocksnapshot>
    pub async fn http_get_snapshot(
        &self,
        symbol: &str,
    ) -> Result<ALPACASnapshot, ALPACAHttpError> {
        let path = format!("/v2/stocks/{}/snapshot", symbol);
        self.send_request(Method::GET, &path, None, false).await
    }

    // =============================================================================
    // Account Endpoints
    // =============================================================================

    /// Requests account information.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/get-v2-account>
    pub async fn http_get_account(&self) -> Result<ALPACAAccount, ALPACAHttpError> {
        let path = "/v2/account";
        self.send_request(Method::GET, path, None, true).await
    }

    /// Requests current positions.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/get-v2-positions>
    pub async fn http_get_positions(&self) -> Result<Vec<ALPACAPosition>, ALPACAHttpError> {
        let path = "/v2/positions";
        self.send_request(Method::GET, path, None, true).await
    }

    /// Requests a specific position by symbol.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/get-v2-positions-symbol>
    pub async fn http_get_position(
        &self,
        symbol: &str,
    ) -> Result<ALPACAPosition, ALPACAHttpError> {
        let path = format!("/v2/positions/{}", symbol);
        self.send_request(Method::GET, &path, None, true).await
    }

    // =============================================================================
    // Trading Endpoints
    // =============================================================================

    /// Requests all orders.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/get-v2-orders>
    pub async fn http_get_orders(
        &self,
        status: Option<&str>,
        limit: Option<u32>,
        after: Option<&str>,
        until: Option<&str>,
    ) -> Result<Vec<ALPACAOrder>, ALPACAHttpError> {
        let mut path = "/v2/orders".to_string();
        let mut params = vec![];

        if let Some(s) = status {
            params.push(format!("status={}", s));
        }
        if let Some(l) = limit {
            params.push(format!("limit={}", l));
        }
        if let Some(a) = after {
            params.push(format!("after={}", a));
        }
        if let Some(u) = until {
            params.push(format!("until={}", u));
        }

        if !params.is_empty() {
            path.push('?');
            path.push_str(&params.join("&"));
        }

        self.send_request(Method::GET, &path, None, true).await
    }

    /// Requests a specific order by ID.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/get-v2-orders-order-id>
    pub async fn http_get_order(
        &self,
        order_id: &str,
    ) -> Result<ALPACAOrder, ALPACAHttpError> {
        let path = format!("/v2/orders/{}", order_id);
        self.send_request(Method::GET, &path, None, true).await
    }

    /// Places a new order.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/post-v2-orders>
    pub async fn http_place_order(
        &self,
        request: ALPACAOrderRequest,
    ) -> Result<ALPACAOrder, ALPACAHttpError> {
        let path = "/v2/orders";
        let body = serde_json::to_vec(&request)?;
        self.send_request(Method::POST, path, Some(body), true).await
    }

    /// Cancels an order by ID.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/delete-v2-orders-order-id>
    pub async fn http_cancel_order(
        &self,
        order_id: &str,
    ) -> Result<serde_json::Value, ALPACAHttpError> {
        let path = format!("/v2/orders/{}", order_id);
        self.send_request(Method::DELETE, &path, None, true).await
    }

    /// Cancels all orders.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/delete-v2-orders>
    pub async fn http_cancel_all_orders(&self) -> Result<Vec<serde_json::Value>, ALPACAHttpError> {
        let path = "/v2/orders";
        self.send_request(Method::DELETE, path, None, true).await
    }

    // =============================================================================
    // Clock & Calendar Endpoints
    // =============================================================================

    /// Requests market clock information.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/get-v2-clock>
    pub async fn http_get_clock(&self) -> Result<ALPACAClock, ALPACAHttpError> {
        let path = "/v2/clock";
        self.send_request(Method::GET, path, None, false).await
    }

    /// Requests market calendar.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or the response cannot be deserialized.
    ///
    /// # References
    ///
    /// <https://docs.alpaca.markets/reference/get-v2-calendar>
    pub async fn http_get_calendar(
        &self,
        start: Option<&str>,
        end: Option<&str>,
    ) -> Result<Vec<ALPACACalendar>, ALPACAHttpError> {
        let mut path = "/v2/calendar".to_string();
        let mut params = vec![];

        if let Some(s) = start {
            params.push(format!("start={}", s));
        }
        if let Some(e) = end {
            params.push(format!("end={}", e));
        }

        if !params.is_empty() {
            path.push('?');
            path.push_str(&params.join("&"));
        }

        self.send_request(Method::GET, &path, None, false).await
    }
}

/// Provides a higher-level HTTP client for the [ALPACA](https://alpaca.com) REST API.
///
/// This client wraps the underlying `ALPACAHttpInnerClient` to handle conversions
/// into the Nautilus domain model.
#[derive(Clone, Debug)]
#[cfg_attr(
    feature = "python",
    pyo3::pyclass(module = "nautilus_trader.core.nautilus_pyo3.adapters")
)]
pub struct ALPACAHttpClient {
    pub(crate) inner: Arc<ALPACAHttpInnerClient>,
    pub(crate) instruments_cache: Arc<Mutex<HashMap<Ustr, InstrumentAny>>>,
    cache_initialized: bool,
}

impl Default for ALPACAHttpClient {
    fn default() -> Self {
        Self::new(None, Some(60), None, None, None).expect("Failed to create default ALPACAHttpClient")
    }
}

impl ALPACAHttpClient {
    /// Creates a new [`ALPACAHttpClient`] using the default ALPACA HTTP URL,
    /// optionally overridden with a custom base url.
    ///
    /// This version of the client has **no credentials**, so it can only
    /// call publicly accessible endpoints.
    ///
    /// # Errors
    ///
    /// Returns an error if the retry manager cannot be created.
    pub fn new(
        base_url: Option<String>,
        timeout_secs: Option<u64>,
        max_retries: Option<u32>,
        retry_delay_ms: Option<u64>,
        retry_delay_max_ms: Option<u64>,
    ) -> anyhow::Result<Self> {
        Ok(Self {
            inner: Arc::new(ALPACAHttpInnerClient::new(
                base_url,
                timeout_secs,
                max_retries,
                retry_delay_ms,
                retry_delay_max_ms,
            )?),
            instruments_cache: Arc::new(Mutex::new(HashMap::new())),
            cache_initialized: false,
        })
    }

    /// Creates a new authenticated [`ALPACAHttpClient`] using environment variables and
    /// the default ALPACA HTTP base url.
    pub fn from_env() -> anyhow::Result<Self> {
        Self::with_credentials(None, None, None, None, None, None, None, None)
    }

    /// Creates a new [`ALPACAHttpClient`] configured with credentials
    /// for authenticated requests, optionally using a custom base url.
    #[allow(clippy::too_many_arguments)]
    pub fn with_credentials(
        api_key: Option<String>,
        api_secret: Option<String>,
        api_passphrase: Option<String>,
        base_url: Option<String>,
        timeout_secs: Option<u64>,
        max_retries: Option<u32>,
        retry_delay_ms: Option<u64>,
        retry_delay_max_ms: Option<u64>,
    ) -> anyhow::Result<Self> {
        let api_key = api_key.unwrap_or(get_env_var("ALPACA_API_KEY")?);
        let api_secret = api_secret.unwrap_or(get_env_var("ALPACA_API_SECRET")?);
        let api_passphrase = api_passphrase.unwrap_or(get_env_var("ALPACA_API_PASSPHRASE")?);
        let base_url = base_url.unwrap_or(ALPACA_HTTP_URL.to_string());

        Ok(Self {
            inner: Arc::new(ALPACAHttpInnerClient::with_credentials(
                api_key,
                api_secret,
                api_passphrase,
                base_url,
                timeout_secs,
                max_retries,
                retry_delay_ms,
                retry_delay_max_ms,
            )?),
            instruments_cache: Arc::new(Mutex::new(HashMap::new())),
            cache_initialized: false,
        })
    }

    /// Retrieves an instrument from the cache.
    ///
    /// # Errors
    ///
    /// Returns an error if the instrument is not found in the cache.
    fn get_instrument_from_cache(&self, symbol: Ustr) -> anyhow::Result<InstrumentAny> {
        self.instruments_cache
            .lock()
            .expect("`instruments_cache` lock poisoned")
            .get(&symbol)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("Instrument {symbol} not in cache"))
    }

    async fn instrument_or_fetch(&self, symbol: Ustr) -> anyhow::Result<InstrumentAny> {
        if let Ok(inst) = self.get_instrument_from_cache(symbol) {
            return Ok(inst);
        }

        for group in [
            ALPACAInstrumentType::Stock,
            ALPACAInstrumentType::Crypto,
            ALPACAInstrumentType::Spot,
        ] {
            if let Ok(instruments) = self.request_instruments(group).await {
                let mut guard = self.instruments_cache.lock().unwrap();
                for inst in instruments {
                    guard.insert(inst.raw_symbol().inner(), inst);
                }
                drop(guard);

                if let Ok(inst) = self.get_instrument_from_cache(symbol) {
                    return Ok(inst);
                }
            }
        }

        anyhow::bail!("Instrument {symbol} not in cache and fetch failed");
    }

    /// Cancel all pending HTTP requests.
    pub fn cancel_all_requests(&self) {
        self.inner.cancel_all_requests();
    }

    /// Get the cancellation token for this client.
    pub fn cancellation_token(&self) -> &CancellationToken {
        self.inner.cancellation_token()
    }

    /// Returns the base url being used by the client.
    pub fn base_url(&self) -> &str {
        self.inner.base_url.as_str()
    }

    /// Returns the public API key being used by the client.
    pub fn api_key(&self) -> Option<&str> {
        self.inner.credential.as_ref().map(|c| c.api_key.as_str())
    }

    /// Checks if the client is initialized.
    ///
    /// The client is considered initialized if any instruments have been cached from the venue.
    #[must_use]
    pub const fn is_initialized(&self) -> bool {
        self.cache_initialized
    }

    /// Generates a timestamp for initialization.
    fn generate_ts_init(&self) -> UnixNanos {
        get_atomic_clock_realtime().get_time_ns()
    }

    /// Returns the cached instrument symbols.
    #[must_use]
    /// Returns a snapshot of all instrument symbols currently held in the
    /// internal cache.
    ///
    /// # Panics
    ///
    /// Panics if the internal mutex guarding the instrument cache is poisoned
    /// (which would indicate a previous panic while the lock was held).
    pub fn get_cached_symbols(&self) -> Vec<String> {
        self.instruments_cache
            .lock()
            .unwrap()
            .keys()
            .map(std::string::ToString::to_string)
            .collect()
    }

    /// Adds the `instruments` to the clients instrument cache.
    ///
    /// Any existing instruments will be replaced.
    /// Inserts multiple instruments into the local cache.
    ///
    /// # Panics
    ///
    /// Panics if the instruments cache mutex is poisoned.
    pub fn add_instruments(&mut self, instruments: Vec<InstrumentAny>) {
        for inst in instruments {
            self.instruments_cache
                .lock()
                .unwrap()
                .insert(inst.raw_symbol().inner(), inst);
        }
        self.cache_initialized = true;
    }

    /// Adds the `instrument` to the clients instrument cache.
    ///
    /// Any existing instrument will be replaced.
    /// Inserts a single instrument into the local cache.
    ///
    /// # Panics
    ///
    /// Panics if the instruments cache mutex is poisoned.
    pub fn add_instrument(&mut self, instrument: InstrumentAny) {
        self.instruments_cache
            .lock()
            .unwrap()
            .insert(instrument.raw_symbol().inner(), instrument);
        self.cache_initialized = true;
    }

    /// Requests the account state for the `account_id` from ALPACA.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or no account state is returned.
    pub async fn request_account_state(
        &self,
        account_id: AccountId,
    ) -> anyhow::Result<AccountState> {
        let resp = self
            .inner
            .http_get_account()
            .await
            .map_err(|e| anyhow::anyhow!(e))?;

        let ts_init = self.generate_ts_init();
        let account_state = parse_account_state(&resp, account_id, ts_init, ts_init)?;

        Ok(account_state)
    }

    /// Sets the position mode for the account.
    ///
    /// # Errors
    ///
    /// Returns an error indicating this feature is not supported for Alpaca.
    ///
    /// # Note
    ///
    /// Alpaca does not support position mode settings. Stocks use net positions
    /// and crypto positions are always net. This is a no-op for compatibility.
    pub async fn set_position_mode(&self, _position_mode: ALPACAPositionMode) -> anyhow::Result<()> {
        anyhow::bail!("Position mode setting not supported for Alpaca")
    }

    /// Requests all instruments for the `instrument_type` from ALPACA.
    ///
    /// # Errors
    ///
    /// Returns an error indicating this feature is not yet implemented.
    pub async fn request_instruments(
        &self,
        _instrument_type: ALPACAInstrumentType,
    ) -> anyhow::Result<Vec<InstrumentAny>> {
        anyhow::bail!("Instrument requests not yet implemented for Alpaca")
    }

    /// Requests the latest mark price for the `instrument_type` from ALPACA.
    ///
    /// # Errors
    ///
    /// Returns an error indicating this feature is not applicable to Alpaca.
    ///
    /// # Note
    ///
    /// Mark prices are used in derivatives/futures markets. Alpaca primarily
    /// supports stocks and crypto which use spot prices.
    pub async fn request_mark_price(
        &self,
        _instrument_id: InstrumentId,
    ) -> anyhow::Result<MarkPriceUpdate> {
        anyhow::bail!("Mark price not applicable to Alpaca (stocks/crypto only)")
    }

    /// Requests the latest index price for the `instrument_id` from ALPACA.
    ///
    /// # Errors
    ///
    /// Returns an error indicating this feature is not applicable to Alpaca.
    ///
    /// # Note
    ///
    /// Index prices are used in derivatives/futures markets. Alpaca primarily
    /// supports stocks and crypto which use spot prices.
    pub async fn request_index_price(
        &self,
        _instrument_id: InstrumentId,
    ) -> anyhow::Result<IndexPriceUpdate> {
        anyhow::bail!("Index price not applicable to Alpaca (stocks/crypto only)")
    }

    /// Requests trades for the `instrument_id` and `start` -> `end` time range.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or trade parsing fails.
    pub async fn request_trades(
        &self,
        instrument_id: InstrumentId,
        start: Option<DateTime<Utc>>,
        end: Option<DateTime<Utc>>,
        limit: Option<u32>,
    ) -> anyhow::Result<Vec<TradeTick>> {
        let symbol = instrument_id.symbol.as_str();

        // Format dates as RFC3339 strings if provided
        let start_str = start.map(|s| s.to_rfc3339());
        let end_str = end.map(|e| e.to_rfc3339());

        // Fetch raw trades from Alpaca API
        let resp = self
            .inner
            .http_get_trades(
                symbol,
                start_str.as_deref(),
                end_str.as_deref(),
                limit,
            )
            .await
            .map_err(anyhow::Error::new)?;

        let ts_init = self.generate_ts_init();
        let inst = self
            .instrument_or_fetch(instrument_id.symbol.inner())
            .await?;

        let mut trades = Vec::with_capacity(resp.trades.len());
        for raw in &resp.trades {
            match parse_trade_tick(
                raw,
                instrument_id,
                inst.price_precision(),
                inst.size_precision(),
                ts_init,
            ) {
                Ok(trade) => trades.push(trade),
                Err(e) => tracing::error!("{e}"),
            }
        }

        Ok(trades)
    }

    /// Requests historical bars for the given bar type and time range.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or bar parsing fails.
    pub async fn request_bars(
        &self,
        bar_type: BarType,
        start: Option<DateTime<Utc>>,
        end: Option<DateTime<Utc>>,
        limit: Option<u32>,
    ) -> anyhow::Result<Vec<Bar>> {
        anyhow::ensure!(
            bar_type.aggregation_source() == AggregationSource::External,
            "Only EXTERNAL aggregation is supported"
        );

        let instrument_id = bar_type.instrument_id();
        let symbol = instrument_id.symbol.as_str();
        let spec = bar_type.spec();
        let step = spec.step.get();

        // Map bar aggregation to Alpaca timeframe format
        let timeframe = match spec.aggregation {
            BarAggregation::Minute => format!("{}Min", step),
            BarAggregation::Hour => format!("{}Hour", step),
            BarAggregation::Day => format!("{}Day", step),
            BarAggregation::Week => format!("{}Week", step),
            BarAggregation::Month => format!("{}Month", step),
            a => anyhow::bail!("Alpaca does not support {:?} aggregation", a),
        };

        // Format dates as RFC3339 strings if provided
        let start_str = start.map(|s| s.to_rfc3339());
        let end_str = end.map(|e| e.to_rfc3339());

        // Fetch bars from Alpaca API
        let resp = self
            .inner
            .http_get_bars(
                symbol,
                &timeframe,
                start_str.as_deref(),
                end_str.as_deref(),
                limit,
            )
            .await
            .map_err(anyhow::Error::new)?;

        let ts_init = self.generate_ts_init();

        let mut bars = Vec::new();

        // ALPACABarsResponse contains a HashMap<String, Vec<ALPACABar>> where key is symbol
        for (_symbol, symbol_bars) in &resp.bars {
            for raw in symbol_bars {
                match parse_candlestick(
                    raw,
                    bar_type,
                    ts_init,
                ) {
                    Ok(bar) => bars.push(bar),
                    Err(e) => tracing::error!("{e}"),
                }
            }
        }

        Ok(bars)
    }

    /// Requests historical order status reports for the given parameters.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or order parsing fails.
    #[allow(clippy::too_many_arguments)]
    pub async fn request_order_status_reports(
        &self,
        account_id: AccountId,
        _instrument_type: Option<ALPACAInstrumentType>,
        instrument_id: Option<InstrumentId>,
        start: Option<DateTime<Utc>>,
        end: Option<DateTime<Utc>>,
        open_only: bool,
        limit: Option<u32>,
    ) -> anyhow::Result<Vec<OrderStatusReport>> {
        // Determine status filter
        let status = if open_only { Some("open") } else { None };

        // Format dates as RFC3339 strings if provided
        let after_str = start.map(|s| s.to_rfc3339());
        let until_str = end.map(|e| e.to_rfc3339());

        // Fetch orders from Alpaca API
        let orders = self
            .inner
            .http_get_orders(
                status,
                limit,
                after_str.as_deref(),
                until_str.as_deref(),
            )
            .await
            .map_err(anyhow::Error::new)?;

        let ts_init = self.generate_ts_init();
        let mut reports = Vec::with_capacity(orders.len());

        for order in &orders {
            // Filter by instrument if specified
            if let Some(inst_id) = instrument_id {
                if order.symbol != inst_id.symbol.as_str() {
                    continue;
                }
            }

            let inst = self
                .instrument_or_fetch(Ustr::from(&order.symbol))
                .await?;

            let report = parse_order_status_report(
                order,
                account_id,
                inst.id(),
                ts_init,
            )?;

            reports.push(report);
        }

        Ok(reports)
    }

    /// Requests fill reports (transaction details) for the given parameters.
    ///
    /// # Errors
    ///
    /// Returns an error indicating this feature is not yet implemented.
    ///
    /// # Note
    ///
    /// Alpaca provides fill information as part of order updates. This method
    /// will be implemented to extract fill reports from order history.
    pub async fn request_fill_reports(
        &self,
        _account_id: AccountId,
        _instrument_type: Option<ALPACAInstrumentType>,
        _instrument_id: Option<InstrumentId>,
        _start: Option<DateTime<Utc>>,
        _end: Option<DateTime<Utc>>,
        _limit: Option<u32>,
    ) -> anyhow::Result<Vec<FillReport>> {
        anyhow::bail!("Fill reports not yet implemented for Alpaca")
    }

    /// Requests current position status reports for the given parameters.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP request fails or position parsing fails.
    pub async fn request_position_status_reports(
        &self,
        account_id: AccountId,
        _instrument_type: Option<ALPACAInstrumentType>,
        instrument_id: Option<InstrumentId>,
    ) -> anyhow::Result<Vec<PositionStatusReport>> {
        // Fetch positions from Alpaca API
        let positions = if let Some(inst_id) = instrument_id {
            // Get specific position
            vec![self
                .inner
                .http_get_position(inst_id.symbol.as_str())
                .await
                .map_err(anyhow::Error::new)?]
        } else {
            // Get all positions
            self.inner
                .http_get_positions()
                .await
                .map_err(anyhow::Error::new)?
        };

        let ts_init = self.generate_ts_init();
        let mut reports = Vec::with_capacity(positions.len());

        for position in &positions {
            let inst = self
                .instrument_or_fetch(Ustr::from(&position.symbol))
                .await?;

            let report = parse_position_status_report(
                position,
                account_id,
                inst.id(),
                ts_init,
            )?;
            reports.push(report);
        }

        Ok(reports)
    }

    /// Places an algo order via HTTP.
    ///
    /// # Errors
    ///
    /// Returns an error indicating algo orders are not supported for Alpaca.
    ///
    /// # Note
    ///
    /// Alpaca does not support algo orders. Use regular order types instead.
    pub async fn place_algo_order(
        &self,
        _request: ALPACAPlaceAlgoOrderRequest,
    ) -> Result<ALPACAPlaceAlgoOrderResponse, ALPACAHttpError> {
        Err(ALPACAHttpError::ValidationError(
            "Algo orders not supported for Alpaca".to_string(),
        ))
    }

    /// Cancels an algo order via HTTP.
    ///
    /// # Errors
    ///
    /// Returns an error indicating algo orders are not supported for Alpaca.
    pub async fn cancel_algo_order(
        &self,
        _request: ALPACACancelAlgoOrderRequest,
    ) -> Result<ALPACACancelAlgoOrderResponse, ALPACAHttpError> {
        Err(ALPACAHttpError::ValidationError(
            "Algo orders not supported for Alpaca".to_string(),
        ))
    }

    /// Places an algo order using domain types.
    ///
    /// # Errors
    ///
    /// Returns an error indicating algo orders are not supported for Alpaca.
    #[allow(clippy::too_many_arguments)]
    pub async fn place_algo_order_with_domain_types(
        &self,
        _instrument_id: InstrumentId,
        _td_mode: ALPACATradeMode,
        _client_order_id: ClientOrderId,
        _order_side: OrderSide,
        _order_type: OrderType,
        _quantity: Quantity,
        _trigger_price: Price,
        _trigger_type: Option<TriggerType>,
        _limit_price: Option<Price>,
        _reduce_only: Option<bool>,
    ) -> Result<ALPACAPlaceAlgoOrderResponse, ALPACAHttpError> {
        Err(ALPACAHttpError::ValidationError(
            "Algo orders not supported for Alpaca".to_string(),
        ))
    }

    /// Cancels an algo order using domain types.
    ///
    /// # Errors
    ///
    /// Returns an error indicating algo orders are not supported for Alpaca.
    pub async fn cancel_algo_order_with_domain_types(
        &self,
        _instrument_id: InstrumentId,
        _algo_id: String,
    ) -> Result<ALPACACancelAlgoOrderResponse, ALPACAHttpError> {
        Err(ALPACAHttpError::ValidationError(
            "Algo orders not supported for Alpaca".to_string(),
        ))
    }
}
