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

//! WebSocket message types for the Alpaca adapter.

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::common::models::{AlpacaBar, AlpacaMarketTrade, AlpacaQuote};

/// Authentication request message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaAuthRequest {
    /// Action type.
    pub action: String,
    /// API key.
    pub key: String,
    /// API secret.
    pub secret: String,
}

/// Subscription request message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaSubscriptionRequest {
    /// Action (subscribe/unsubscribe).
    pub action: String,
    /// List of trades symbols.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trades: Option<Vec<String>>,
    /// List of quotes symbols.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quotes: Option<Vec<String>>,
    /// List of bars symbols.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bars: Option<Vec<String>>,
}

/// Subscription status response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaSubscriptionResponse {
    /// Stream type.
    #[serde(rename = "T")]
    pub msg_type: String,
    /// Subscription message.
    pub msg: String,
}

/// Connection success response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaConnectionResponse {
    /// Stream type.
    #[serde(rename = "T")]
    pub msg_type: String,
    /// Connection message.
    pub msg: String,
}

/// Error response.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaErrorResponse {
    /// Stream type.
    #[serde(rename = "T")]
    pub msg_type: String,
    /// Error code.
    pub code: i32,
    /// Error message.
    pub msg: String,
}

/// Trade update message for trading stream.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaTradeUpdate {
    /// Stream type.
    #[serde(rename = "T")]
    pub msg_type: String,
    /// Event type (e.g., "fill", "canceled").
    pub event: String,
    /// Order details.
    pub order: Value,
    /// Timestamp.
    pub timestamp: String,
}

/// Enum representing all possible WebSocket messages from Alpaca.
#[derive(Debug, Clone)]
pub enum AlpacaWebSocketMessage {
    /// Connection established.
    Connected(AlpacaConnectionResponse),
    /// Authentication successful.
    Authenticated,
    /// Subscription confirmation.
    Subscription(AlpacaSubscriptionResponse),
    /// Trade message.
    Trade(AlpacaMarketTrade),
    /// Quote message.
    Quote(AlpacaQuote),
    /// Bar message.
    Bar(AlpacaBar),
    /// Trade update (for trading stream).
    TradeUpdate(AlpacaTradeUpdate),
    /// Error message.
    Error(AlpacaErrorResponse),
    /// Heartbeat/pong.
    Pong,
    /// Reconnected.
    Reconnected,
    /// Raw JSON message (for unhandled types).
    Raw(Value),
}
