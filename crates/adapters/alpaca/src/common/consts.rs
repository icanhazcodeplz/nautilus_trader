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

//! Core constants shared across the ALPACA adapter components.

use std::sync::LazyLock;

use ahash::AHashSet;
use nautilus_model::{
    enums::{OrderType, TimeInForce},
    identifiers::Venue,
};
use ustr::Ustr;

pub const ALPACA: &str = "ALPACA";
pub static ALPACA_VENUE: LazyLock<Venue> = LazyLock::new(|| Venue::new(Ustr::from(ALPACA)));

/// Alpaca broker ID for NautilusTrader
pub const ALPACA_NAUTILUS_BROKER_ID: &str = "nautilus";

// Alpaca API URLs
// Live trading URLs
pub const ALPACA_HTTP_URL: &str = "https://api.alpaca.markets";
pub const ALPACA_DATA_HTTP_URL: &str = "https://data.alpaca.markets";
pub const ALPACA_WS_TRADING_URL: &str = "wss://api.alpaca.markets/stream";
pub const ALPACA_WS_DATA_URL: &str = "wss://stream.data.alpaca.markets";

// Paper trading URLs
pub const ALPACA_PAPER_HTTP_URL: &str = "https://paper-api.alpaca.markets";
pub const ALPACA_PAPER_DATA_HTTP_URL: &str = "https://data.alpaca.markets"; // Same for paper
pub const ALPACA_PAPER_WS_TRADING_URL: &str = "wss://paper-api.alpaca.markets/stream";
pub const ALPACA_PAPER_WS_DATA_URL: &str = "wss://stream.data.alpaca.markets"; // Same for paper

// WebSocket URLs (aliases for compatibility)
pub const ALPACA_WS_PUBLIC_URL: &str = ALPACA_WS_DATA_URL;

/// ALPACA supported order time in force for market orders.
///
/// # Notes
///
/// - ALPACA implements IOC and FOK as order types rather than separate time-in-force parameters.
/// - GTD is supported via expire_time parameter.
pub const ALPACA_SUPPORTED_TIME_IN_FORCE: &[TimeInForce] = &[
    TimeInForce::Gtc, // Good Till Cancel (default)
    TimeInForce::Ioc, // Immediate or Cancel (mapped to ALPACAOrderType::Ioc)
    TimeInForce::Fok, // Fill or Kill (mapped to ALPACAOrderType::Fok)
];

/// ALPACA supported order types.
///
/// # Notes
///
/// - PostOnly is supported as a flag on limit orders.
/// - Conditional orders (stop/trigger) are supported via algo orders.
pub const ALPACA_SUPPORTED_ORDER_TYPES: &[OrderType] = &[
    OrderType::Market,
    OrderType::Limit,
    OrderType::MarketToLimit,   // Mapped to IOC when no price is specified
    OrderType::StopMarket,      // Supported via algo order API
    OrderType::StopLimit,       // Supported via algo order API
    OrderType::MarketIfTouched, // Supported via algo order API
    OrderType::LimitIfTouched,  // Supported via algo order API
];

/// Conditional order types that require the ALPACA algo order API.
pub const ALPACA_CONDITIONAL_ORDER_TYPES: &[OrderType] = &[
    OrderType::StopMarket,
    OrderType::StopLimit,
    OrderType::MarketIfTouched,
    OrderType::LimitIfTouched,
];

/// Alpaca HTTP status codes that should trigger retries.
///
/// Only retry on temporary network/system issues.
///
/// # References
///
/// Based on Alpaca API documentation: <https://docs.alpaca.markets/docs/api-error-codes>
pub static ALPACA_RETRY_ERROR_CODES: LazyLock<AHashSet<&'static str>> = LazyLock::new(|| {
    let mut codes = AHashSet::new();

    // Rate limiting
    codes.insert("429"); // Too Many Requests - rate limited

    // Server errors (5xx)
    codes.insert("500"); // Internal Server Error
    codes.insert("502"); // Bad Gateway
    codes.insert("503"); // Service Unavailable
    codes.insert("504"); // Gateway Timeout

    codes
});

/// Determines if an Alpaca error code should trigger a retry.
pub fn should_retry_error_code(error_code: &str) -> bool {
    ALPACA_RETRY_ERROR_CODES.contains(error_code)
}

/// Alpaca error code for insufficient buying power.
pub const ALPACA_INSUFFICIENT_BUYING_POWER: i32 = 40310000;

/// Alpaca error code for order not found.
pub const ALPACA_ORDER_NOT_FOUND: i32 = 40410000;

/// Post-only order error code (Alpaca doesn't specifically use this, but included for compatibility).
pub const ALPACA_POST_ONLY_ERROR_CODE: &str = "40010001";

/// Post-only order cancel source.
pub const ALPACA_POST_ONLY_CANCEL_SOURCE: &str = "post_only_reject";

/// Post-only order cancel reason.
pub const ALPACA_POST_ONLY_CANCEL_REASON: &str = "Order would immediately match and trade as a taker order";
