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

//! Alpaca WebSocket client for market data and trading streams.

use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};

use nautilus_common::runtime::get_runtime;
use nautilus_core::consts::NAUTILUS_USER_AGENT;
use nautilus_network::websocket::{WebSocketClient, WebSocketConfig, channel_message_handler};
use serde_json::Value;
use tokio::sync::RwLock;
use tokio_tungstenite::tungstenite::Message;

use crate::{
    error::{AlpacaError, AlpacaResult},
    websocket::messages::{
        AlpacaAuthRequest, AlpacaSubscriptionRequest, AlpacaWebSocketMessage,
    },
};

const DEFAULT_HEARTBEAT_SECS: u64 = 20;

/// Alpaca WebSocket client for streaming market data or trading updates.
pub struct AlpacaWebSocketClient {
    url: String,
    api_key: Option<String>,
    api_secret: Option<String>,
    heartbeat: Option<u64>,
    inner: Arc<RwLock<Option<WebSocketClient>>>,
    rx: Option<tokio::sync::mpsc::UnboundedReceiver<AlpacaWebSocketMessage>>,
    signal: Arc<AtomicBool>,
    task_handle: Option<tokio::task::JoinHandle<()>>,
    is_authenticated: Arc<AtomicBool>,
}

impl std::fmt::Debug for AlpacaWebSocketClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AlpacaWebSocketClient")
            .field("url", &self.url)
            .field("has_credentials", &self.api_key.is_some())
            .field("heartbeat", &self.heartbeat)
            .finish()
    }
}

impl AlpacaWebSocketClient {
    /// Creates a new Alpaca WebSocket client for market data.
    #[must_use]
    pub fn new_market_data(url: String, heartbeat: Option<u64>) -> Self {
        Self {
            url,
            api_key: None,
            api_secret: None,
            heartbeat: heartbeat.or(Some(DEFAULT_HEARTBEAT_SECS)),
            inner: Arc::new(RwLock::new(None)),
            rx: None,
            signal: Arc::new(AtomicBool::new(false)),
            task_handle: None,
            is_authenticated: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Creates a new Alpaca WebSocket client with authentication.
    #[must_use]
    pub fn new_authenticated(
        url: String,
        api_key: String,
        api_secret: String,
        heartbeat: Option<u64>,
    ) -> Self {
        Self {
            url,
            api_key: Some(api_key),
            api_secret: Some(api_secret),
            heartbeat: heartbeat.or(Some(DEFAULT_HEARTBEAT_SECS)),
            inner: Arc::new(RwLock::new(None)),
            rx: None,
            signal: Arc::new(AtomicBool::new(false)),
            task_handle: None,
            is_authenticated: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Connects to the WebSocket server.
    ///
    /// # Errors
    ///
    /// Returns an error if the connection fails.
    pub async fn connect(&mut self) -> AlpacaResult<()> {
        let (message_handler, mut message_rx) = channel_message_handler();

        let config = WebSocketConfig {
            url: self.url.clone(),
            headers: Self::default_headers(),
            message_handler: Some(message_handler),
            heartbeat: self.heartbeat,
            heartbeat_msg: None,
            ping_handler: None,
            reconnect_timeout_ms: Some(5_000),
            reconnect_delay_initial_ms: Some(500),
            reconnect_delay_max_ms: Some(5_000),
            reconnect_backoff_factor: Some(1.5),
            reconnect_jitter_ms: Some(250),
        };

        let client = WebSocketClient::connect(config, None, vec![], None)
            .await
            .map_err(|e| AlpacaError::WebSocketConnection(e.to_string()))?;

        {
            let mut guard = self.inner.write().await;
            *guard = Some(client);
        }

        let (event_tx, event_rx) = tokio::sync::mpsc::unbounded_channel::<AlpacaWebSocketMessage>();
        self.rx = Some(event_rx);
        self.signal.store(false, Ordering::Relaxed);

        let signal = Arc::clone(&self.signal);
        let is_authenticated = Arc::clone(&self.is_authenticated);

        let task_handle = get_runtime().spawn(async move {
            while let Some(message) = message_rx.recv().await {
                if signal.load(Ordering::Relaxed) {
                    break;
                }

                match Self::handle_message(message) {
                    Ok(Some(msg)) => {
                        // Update authentication status
                        if matches!(msg, AlpacaWebSocketMessage::Authenticated) {
                            is_authenticated.store(true, Ordering::Relaxed);
                        }

                        if event_tx.send(msg).is_err() {
                            break;
                        }
                    }
                    Ok(None) => {}
                    Err(err) => {
                        tracing::error!("Error handling Alpaca WebSocket message: {err}");
                    }
                }
            }
        });

        self.task_handle = Some(task_handle);

        // Authenticate if credentials provided
        if self.api_key.is_some() && self.api_secret.is_some() {
            self.authenticate().await?;
        }

        Ok(())
    }

    /// Disconnects the WebSocket client.
    pub async fn close(&mut self) -> AlpacaResult<()> {
        self.signal.store(true, Ordering::Relaxed);

        {
            let inner_guard = self.inner.read().await;
            if let Some(inner) = inner_guard.as_ref() {
                inner.disconnect().await;
            }
        }

        if let Some(handle) = self.task_handle.take() {
            let _ = handle.await;
        }

        self.rx = None;
        self.is_authenticated.store(false, Ordering::Relaxed);

        Ok(())
    }

    /// Authenticates with the WebSocket server.
    ///
    /// # Errors
    ///
    /// Returns an error if authentication fails or credentials are missing.
    pub async fn authenticate(&self) -> AlpacaResult<()> {
        let api_key = self.api_key.as_ref().ok_or_else(|| {
            AlpacaError::Authentication("API key not configured".into())
        })?;
        let api_secret = self.api_secret.as_ref().ok_or_else(|| {
            AlpacaError::Authentication("API secret not configured".into())
        })?;

        let auth = AlpacaAuthRequest {
            action: "auth".to_string(),
            key: api_key.clone(),
            secret: api_secret.clone(),
        };

        let payload = serde_json::to_string(&auth)
            .map_err(|e| AlpacaError::JsonParse(e.to_string()))?;

        self.send_text(&payload).await
    }

    /// Subscribes to market data streams.
    ///
    /// # Errors
    ///
    /// Returns an error if the subscription request fails.
    pub async fn subscribe(
        &self,
        trades: Option<Vec<String>>,
        quotes: Option<Vec<String>>,
        bars: Option<Vec<String>>,
    ) -> AlpacaResult<()> {
        let request = AlpacaSubscriptionRequest {
            action: "subscribe".to_string(),
            trades,
            quotes,
            bars,
        };

        let payload = serde_json::to_string(&request)
            .map_err(|e| AlpacaError::JsonParse(e.to_string()))?;

        self.send_text(&payload).await
    }

    /// Unsubscribes from market data streams.
    ///
    /// # Errors
    ///
    /// Returns an error if the unsubscription request fails.
    pub async fn unsubscribe(
        &self,
        trades: Option<Vec<String>>,
        quotes: Option<Vec<String>>,
        bars: Option<Vec<String>>,
    ) -> AlpacaResult<()> {
        let request = AlpacaSubscriptionRequest {
            action: "unsubscribe".to_string(),
            trades,
            quotes,
            bars,
        };

        let payload = serde_json::to_string(&request)
            .map_err(|e| AlpacaError::JsonParse(e.to_string()))?;

        self.send_text(&payload).await
    }

    /// Returns a stream of WebSocket messages.
    ///
    /// # Panics
    ///
    /// Panics if called before [`Self::connect`] or if the stream has already been taken.
    pub fn stream(
        &mut self,
    ) -> impl futures_util::Stream<Item = AlpacaWebSocketMessage> + Send + 'static {
        let rx = self
            .rx
            .take()
            .expect("Stream receiver already taken or client not connected");

        async_stream::stream! {
            let mut rx = rx;
            while let Some(event) = rx.recv().await {
                yield event;
            }
        }
    }

    /// Returns whether the client is authenticated.
    #[must_use]
    pub fn is_authenticated(&self) -> bool {
        self.is_authenticated.load(Ordering::Relaxed)
    }

    fn default_headers() -> Vec<(String, String)> {
        vec![
            ("Content-Type".to_string(), "application/json".to_string()),
            ("User-Agent".to_string(), NAUTILUS_USER_AGENT.to_string()),
        ]
    }

    async fn send_text(&self, text: &str) -> AlpacaResult<()> {
        let guard = self.inner.read().await;
        let client = guard
            .as_ref()
            .ok_or_else(|| AlpacaError::WebSocketConnection("Not connected".into()))?;

        client
            .send_text(text.to_string(), None)
            .await
            .map_err(|e| AlpacaError::WebSocketMessage(e.to_string()))
    }

    fn handle_message(message: Message) -> AlpacaResult<Option<AlpacaWebSocketMessage>> {
        match message {
            Message::Text(text) => {
                tracing::trace!("Alpaca WS message: {text}");

                let value: Value = serde_json::from_str(&text)?;

                // Handle array of messages
                if let Some(array) = value.as_array() {
                    // For simplicity, return the first message
                    if let Some(first) = array.first() {
                        return Self::classify_message(first);
                    }
                    return Ok(None);
                }

                Self::classify_message(&value)
            }
            Message::Ping(payload) => {
                tracing::trace!("Received ping ({} bytes)", payload.len());
                Ok(None)
            }
            Message::Pong(_) => Ok(Some(AlpacaWebSocketMessage::Pong)),
            Message::Binary(_) => Ok(None),
            Message::Close(_) => Ok(None),
            Message::Frame(_) => Ok(None),
        }
    }

    fn classify_message(value: &Value) -> AlpacaResult<Option<AlpacaWebSocketMessage>> {
        // Check message type field
        if let Some(msg_type) = value.get("T").and_then(Value::as_str) {
            match msg_type {
                "success" => {
                    if let Some(msg) = value.get("msg").and_then(Value::as_str) {
                        if msg.contains("authenticated") {
                            return Ok(Some(AlpacaWebSocketMessage::Authenticated));
                        }
                    }
                    let resp: crate::websocket::messages::AlpacaConnectionResponse =
                        serde_json::from_value(value.clone())?;
                    Ok(Some(AlpacaWebSocketMessage::Connected(resp)))
                }
                "subscription" => {
                    let resp: crate::websocket::messages::AlpacaSubscriptionResponse =
                        serde_json::from_value(value.clone())?;
                    Ok(Some(AlpacaWebSocketMessage::Subscription(resp)))
                }
                "error" => {
                    let resp: crate::websocket::messages::AlpacaErrorResponse =
                        serde_json::from_value(value.clone())?;
                    Ok(Some(AlpacaWebSocketMessage::Error(resp)))
                }
                "t" => {
                    // Trade message
                    let trade: crate::common::models::AlpacaMarketTrade =
                        serde_json::from_value(value.clone())?;
                    Ok(Some(AlpacaWebSocketMessage::Trade(trade)))
                }
                "q" => {
                    // Quote message
                    let quote: crate::common::models::AlpacaQuote =
                        serde_json::from_value(value.clone())?;
                    Ok(Some(AlpacaWebSocketMessage::Quote(quote)))
                }
                "b" => {
                    // Bar message
                    let bar: crate::common::models::AlpacaBar =
                        serde_json::from_value(value.clone())?;
                    Ok(Some(AlpacaWebSocketMessage::Bar(bar)))
                }
                "trade_updates" => {
                    // Trade update message
                    let update: crate::websocket::messages::AlpacaTradeUpdate =
                        serde_json::from_value(value.clone())?;
                    Ok(Some(AlpacaWebSocketMessage::TradeUpdate(update)))
                }
                _ => Ok(Some(AlpacaWebSocketMessage::Raw(value.clone()))),
            }
        } else {
            Ok(Some(AlpacaWebSocketMessage::Raw(value.clone())))
        }
    }
}
