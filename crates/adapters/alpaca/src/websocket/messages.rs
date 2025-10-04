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

//! Data structures modelling Alpaca WebSocket request and response payloads.

use nautilus_model::{
    data::{Data, OrderBookDeltas, QuoteTick, TradeTick},
    events::{AccountState, OrderCancelRejected, OrderModifyRejected, OrderRejected},
    instruments::InstrumentAny,
    reports::{FillReport, OrderStatusReport},
};
use serde::{Deserialize, Serialize};
use ustr::Ustr;

use crate::common::enums::{AlpacaOrderStatus, AlpacaSide};

/// Represents the various message types that can be received from Alpaca WebSocket.
#[derive(Debug, Clone)]
pub enum NautilusWsMessage {
    /// Market data updates (trades, quotes, bars).
    Data(Vec<Data>),
    /// Order book deltas.
    Deltas(OrderBookDeltas),
    /// Instrument definition.
    Instrument(Box<InstrumentAny>),
    /// Account state update.
    AccountUpdate(AccountState),
    /// Order rejection.
    OrderRejected(OrderRejected),
    /// Order cancel rejection.
    OrderCancelRejected(OrderCancelRejected),
    /// Order modify rejection.
    OrderModifyRejected(OrderModifyRejected),
    /// Execution reports (order updates and fills).
    ExecutionReports(Vec<ExecutionReport>),
    /// Error from Alpaca.
    Error(AlpacaWebSocketError),
    /// Raw unhandled message.
    Raw(serde_json::Value),
    /// Reconnected signal.
    Reconnected,
}

/// Represents an Alpaca WebSocket error.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "python", pyo3::pyclass)]
pub struct AlpacaWebSocketError {
    /// Error code from Alpaca.
    pub code: String,
    /// Error message from Alpaca.
    pub message: String,
    /// Timestamp when the error occurred.
    pub timestamp: u64,
}

/// Execution report can be either an order status update or a fill.
#[derive(Debug, Clone)]
#[allow(clippy::large_enum_variant)]
pub enum ExecutionReport {
    Order(OrderStatusReport),
    Fill(FillReport),
}

// =============================================================================
// Authentication
// =============================================================================

/// Alpaca WebSocket authentication message.
///
/// # References
///
/// <https://docs.alpaca.markets/docs/websocket-streaming#authentication>
#[derive(Debug, Serialize)]
pub struct AlpacaAuthentication {
    pub action: &'static str,
    pub key: String,
    pub secret: String,
}

// =============================================================================
// Subscription
// =============================================================================

/// Alpaca WebSocket subscription request.
#[derive(Debug, Serialize)]
pub struct AlpacaSubscription {
    pub action: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trades: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quotes: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bars: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dailybars: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub statuses: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lulds: Option<Vec<String>>,
}

// =============================================================================
// WebSocket Events
// =============================================================================

/// Represents the various event types received from Alpaca WebSocket.
#[derive(Debug, Deserialize)]
#[serde(tag = "T")]
pub enum AlpacaWebSocketEvent {
    /// Successful connection.
    #[serde(rename = "success")]
    Success {
        msg: String,
    },
    /// Subscription confirmation.
    #[serde(rename = "subscription")]
    Subscription {
        trades: Option<Vec<String>>,
        quotes: Option<Vec<String>>,
        bars: Option<Vec<String>>,
    },
    /// Error event.
    #[serde(rename = "error")]
    Error {
        code: u32,
        msg: String,
    },
    /// Trade update.
    #[serde(rename = "t")]
    Trade(AlpacaTradeMsg),
    /// Quote update.
    #[serde(rename = "q")]
    Quote(AlpacaQuoteMsg),
    /// Bar (candlestick) update.
    #[serde(rename = "b")]
    Bar(AlpacaBarMsg),
    /// Trading status update.
    #[serde(rename = "s")]
    Status(AlpacaStatusMsg),
    /// Limit up/limit down update.
    #[serde(rename = "l")]
    Luld(AlpacaLuldMsg),
    /// Trading update (for trading WebSocket).
    #[serde(rename = "trading_status")]
    TradingUpdate(AlpacaTradingUpdateMsg),
}

// =============================================================================
// Market Data Messages
// =============================================================================

/// Trade message from Alpaca WebSocket.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaTradeMsg {
    /// Symbol.
    #[serde(rename = "S")]
    pub symbol: Ustr,
    /// Trade ID.
    #[serde(rename = "i")]
    pub id: u64,
    /// Exchange code.
    #[serde(rename = "x")]
    pub exchange: String,
    /// Trade price.
    #[serde(rename = "p")]
    pub price: String,
    /// Trade size.
    #[serde(rename = "s")]
    pub size: u64,
    /// Trade timestamp (RFC3339).
    #[serde(rename = "t")]
    pub timestamp: String,
    /// Trade conditions.
    #[serde(default, rename = "c")]
    pub conditions: Vec<String>,
    /// Trade tape.
    #[serde(default, rename = "z")]
    pub tape: Option<String>,
}

/// Quote message from Alpaca WebSocket.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaQuoteMsg {
    /// Symbol.
    #[serde(rename = "S")]
    pub symbol: Ustr,
    /// Ask exchange.
    #[serde(rename = "ax")]
    pub ask_exchange: String,
    /// Ask price.
    #[serde(rename = "ap")]
    pub ask_price: String,
    /// Ask size.
    #[serde(rename = "as")]
    pub ask_size: u64,
    /// Bid exchange.
    #[serde(rename = "bx")]
    pub bid_exchange: String,
    /// Bid price.
    #[serde(rename = "bp")]
    pub bid_price: String,
    /// Bid size.
    #[serde(rename = "bs")]
    pub bid_size: u64,
    /// Quote timestamp (RFC3339).
    #[serde(rename = "t")]
    pub timestamp: String,
    /// Quote conditions.
    #[serde(default, rename = "c")]
    pub conditions: Vec<String>,
    /// Quote tape.
    #[serde(default, rename = "z")]
    pub tape: Option<String>,
}

/// Bar (candlestick) message from Alpaca WebSocket.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaBarMsg {
    /// Symbol.
    #[serde(rename = "S")]
    pub symbol: Ustr,
    /// Open price.
    #[serde(rename = "o")]
    pub open: String,
    /// High price.
    #[serde(rename = "h")]
    pub high: String,
    /// Low price.
    #[serde(rename = "l")]
    pub low: String,
    /// Close price.
    #[serde(rename = "c")]
    pub close: String,
    /// Volume.
    #[serde(rename = "v")]
    pub volume: u64,
    /// Bar timestamp (RFC3339).
    #[serde(rename = "t")]
    pub timestamp: String,
    /// Number of trades (optional).
    #[serde(default, rename = "n")]
    pub trade_count: Option<u64>,
    /// Volume weighted average price (optional).
    #[serde(default, rename = "vw")]
    pub vwap: Option<String>,
}

/// Trading status message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaStatusMsg {
    /// Symbol.
    #[serde(rename = "S")]
    pub symbol: Ustr,
    /// Status code.
    #[serde(rename = "sc")]
    pub status_code: String,
    /// Status message.
    #[serde(rename = "sm")]
    pub status_msg: String,
    /// Reason code.
    #[serde(rename = "rc")]
    pub reason_code: String,
    /// Reason message.
    #[serde(rename = "rm")]
    pub reason_msg: String,
    /// Timestamp (RFC3339).
    #[serde(rename = "t")]
    pub timestamp: String,
    /// Tape.
    #[serde(rename = "z")]
    pub tape: String,
}

/// Limit up/limit down message.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaLuldMsg {
    /// Symbol.
    #[serde(rename = "S")]
    pub symbol: Ustr,
    /// Limit up price.
    #[serde(rename = "u")]
    pub limit_up_price: String,
    /// Limit down price.
    #[serde(rename = "d")]
    pub limit_down_price: String,
    /// Indicator.
    #[serde(rename = "i")]
    pub indicator: String,
    /// Timestamp (RFC3339).
    #[serde(rename = "t")]
    pub timestamp: String,
    /// Tape.
    #[serde(rename = "z")]
    pub tape: String,
}

// =============================================================================
// Trading Updates
// =============================================================================

/// Trading update message (order and fill updates).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaTradingUpdateMsg {
    /// Event type (new, fill, partial_fill, canceled, etc.).
    pub event: String,
    /// Order details.
    pub order: AlpacaOrderUpdateMsg,
    /// Timestamp (RFC3339).
    #[serde(default)]
    pub timestamp: Option<String>,
    /// Position quantity (for fill events).
    #[serde(default)]
    pub position_qty: Option<String>,
    /// Price (for fill events).
    #[serde(default)]
    pub price: Option<String>,
    /// Quantity filled (for fill events).
    #[serde(default)]
    pub qty: Option<String>,
}

/// Order update details within a trading update.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaOrderUpdateMsg {
    /// Order ID.
    pub id: String,
    /// Client order ID.
    pub client_order_id: String,
    /// Symbol.
    pub symbol: String,
    /// Side (buy/sell).
    pub side: AlpacaSide,
    /// Order type.
    pub order_type: String,
    /// Time in force.
    pub time_in_force: String,
    /// Quantity.
    pub qty: Option<String>,
    /// Filled quantity.
    pub filled_qty: String,
    /// Order status.
    pub status: AlpacaOrderStatus,
    /// Created timestamp.
    pub created_at: String,
    /// Updated timestamp.
    #[serde(default)]
    pub updated_at: Option<String>,
    /// Submitted timestamp.
    #[serde(default)]
    pub submitted_at: Option<String>,
    /// Filled timestamp.
    #[serde(default)]
    pub filled_at: Option<String>,
    /// Expired timestamp.
    #[serde(default)]
    pub expired_at: Option<String>,
    /// Canceled timestamp.
    #[serde(default)]
    pub canceled_at: Option<String>,
    /// Failed timestamp.
    #[serde(default)]
    pub failed_at: Option<String>,
    /// Filled average price.
    #[serde(default)]
    pub filled_avg_price: Option<String>,
    /// Limit price.
    #[serde(default)]
    pub limit_price: Option<String>,
    /// Stop price.
    #[serde(default)]
    pub stop_price: Option<String>,
    /// Extended hours.
    pub extended_hours: bool,
}

////////////////////////////////////////////////////////////////////////////////
// Tests
////////////////////////////////////////////////////////////////////////////////
#[cfg(test)]
mod tests {
    use rstest::rstest;

    use super::*;

    #[rstest]
    fn test_deserialize_success_event() {
        let json_str = r#"[{"T":"success","msg":"authenticated"}]"#;
        let result: Result<Vec<AlpacaWebSocketEvent>, _> = serde_json::from_str(json_str);

        match result {
            Ok(events) => {
                assert_eq!(events.len(), 1);
                match &events[0] {
                    AlpacaWebSocketEvent::Success { msg } => {
                        assert_eq!(msg, "authenticated");
                    }
                    _ => panic!("Expected Success event"),
                }
            }
            Err(e) => {
                panic!("Failed to deserialize: {e}");
            }
        }
    }

    #[rstest]
    fn test_deserialize_trade_event() {
        let json_str = r#"[{"T":"t","S":"AAPL","i":123,"x":"V","p":"150.50","s":100,"t":"2023-01-01T12:00:00Z","c":[],"z":"A"}]"#;
        let result: Result<Vec<AlpacaWebSocketEvent>, _> = serde_json::from_str(json_str);

        match result {
            Ok(events) => {
                assert_eq!(events.len(), 1);
                match &events[0] {
                    AlpacaWebSocketEvent::Trade(trade) => {
                        assert_eq!(trade.symbol, Ustr::from("AAPL"));
                        assert_eq!(trade.price, "150.50");
                        assert_eq!(trade.size, 100);
                    }
                    _ => panic!("Expected Trade event"),
                }
            }
            Err(e) => {
                panic!("Failed to deserialize: {e}");
            }
        }
    }

    #[rstest]
    fn test_deserialize_quote_event() {
        let json_str = r#"[{"T":"q","S":"AAPL","ax":"V","ap":"150.51","as":100,"bx":"V","bp":"150.49","bs":200,"t":"2023-01-01T12:00:00Z","c":[],"z":"A"}]"#;
        let result: Result<Vec<AlpacaWebSocketEvent>, _> = serde_json::from_str(json_str);

        match result {
            Ok(events) => {
                assert_eq!(events.len(), 1);
                match &events[0] {
                    AlpacaWebSocketEvent::Quote(quote) => {
                        assert_eq!(quote.symbol, Ustr::from("AAPL"));
                        assert_eq!(quote.ask_price, "150.51");
                        assert_eq!(quote.bid_price, "150.49");
                        assert_eq!(quote.ask_size, 100);
                        assert_eq!(quote.bid_size, 200);
                    }
                    _ => panic!("Expected Quote event"),
                }
            }
            Err(e) => {
                panic!("Failed to deserialize: {e}");
            }
        }
    }

    #[rstest]
    fn test_deserialize_bar_event() {
        let json_str = r#"[{"T":"b","S":"AAPL","o":"150.00","h":"151.00","l":"149.50","c":"150.50","v":10000,"t":"2023-01-01T12:00:00Z","n":100,"vw":"150.25"}]"#;
        let result: Result<Vec<AlpacaWebSocketEvent>, _> = serde_json::from_str(json_str);

        match result {
            Ok(events) => {
                assert_eq!(events.len(), 1);
                match &events[0] {
                    AlpacaWebSocketEvent::Bar(bar) => {
                        assert_eq!(bar.symbol, Ustr::from("AAPL"));
                        assert_eq!(bar.open, "150.00");
                        assert_eq!(bar.high, "151.00");
                        assert_eq!(bar.low, "149.50");
                        assert_eq!(bar.close, "150.50");
                        assert_eq!(bar.volume, 10000);
                    }
                    _ => panic!("Expected Bar event"),
                }
            }
            Err(e) => {
                panic!("Failed to deserialize: {e}");
            }
        }
    }

    #[rstest]
    fn test_serialize_subscription() {
        let subscription = AlpacaSubscription {
            action: "subscribe",
            trades: Some(vec!["AAPL".to_string(), "TSLA".to_string()]),
            quotes: Some(vec!["AAPL".to_string()]),
            bars: None,
            dailybars: None,
            statuses: None,
            lulds: None,
        };

        let serialized = serde_json::to_string(&subscription).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&serialized).unwrap();

        assert_eq!(parsed["action"], "subscribe");
        assert!(parsed["trades"].is_array());
        assert_eq!(parsed["trades"].as_array().unwrap().len(), 2);
        assert!(parsed["quotes"].is_array());
        assert_eq!(parsed["quotes"].as_array().unwrap().len(), 1);
    }

    #[rstest]
    fn test_serialize_authentication() {
        let auth = AlpacaAuthentication {
            action: "auth",
            key: "test_key".to_string(),
            secret: "test_secret".to_string(),
        };

        let serialized = serde_json::to_string(&auth).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&serialized).unwrap();

        assert_eq!(parsed["action"], "auth");
        assert_eq!(parsed["key"], "test_key");
        assert_eq!(parsed["secret"], "test_secret");
    }
}
