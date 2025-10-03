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

/// See <https://www.alpaca.com/docs-v5/en/#overview-broker-program> for further details.
pub const ALPACA_NAUTILUS_BROKER_ID: &str = "a535cbe8d0c8BCDE";

// Use the canonical host with www to avoid cross-domain redirects which may
// strip authentication headers in some HTTP clients and middleboxes.
pub const ALPACA_HTTP_URL: &str = "https://www.alpaca.com";
pub const ALPACA_WS_PUBLIC_URL: &str = "wss://ws.alpaca.com:8443/ws/v5/public";
pub const ALPACA_WS_PRIVATE_URL: &str = "wss://ws.alpaca.com:8443/ws/v5/private";
pub const ALPACA_WS_BUSINESS_URL: &str = "wss://ws.alpaca.com:8443/ws/v5/business";
pub const ALPACA_WS_DEMO_PUBLIC_URL: &str = "wss://wspap.alpaca.com:8443/ws/v5/public";
pub const ALPACA_WS_DEMO_PRIVATE_URL: &str = "wss://wspap.alpaca.com:8443/ws/v5/private";
pub const ALPACA_WS_DEMO_BUSINESS_URL: &str = "wss://wspap.alpaca.com:8443/ws/v5/business";

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

/// ALPACA error codes that should trigger retries.
///
/// Only retry on temporary network/system issues.
///
/// # References
///
/// Based on ALPACA API documentation: <https://www.alpaca.com/docs-v5/en/#error-codes>
pub static ALPACA_RETRY_ERROR_CODES: LazyLock<AHashSet<&'static str>> = LazyLock::new(|| {
    let mut codes = AHashSet::new();

    // Temporary system errors
    codes.insert("50001"); // Service temporarily unavailable
    codes.insert("50004"); // API endpoint request timeout (does not mean that the request was successful or failed, please check the request result)
    codes.insert("50005"); // API is offline or unavailable
    codes.insert("50013"); // System busy, please try again later
    codes.insert("50026"); // System error, please try again later

    // Rate limit errors (temporary)
    codes.insert("50011"); // Request too frequent
    codes.insert("50113"); // API requests exceed the limit

    // WebSocket connection issues (temporary)
    codes.insert("60001"); // OK not received in time
    codes.insert("60005"); // Connection closed as there was no data transmission in the last 30 seconds

    codes
});

/// Determines if an ALPACA error code should trigger a retry.
pub fn should_retry_error_code(error_code: &str) -> bool {
    ALPACA_RETRY_ERROR_CODES.contains(error_code)
}

/// ALPACA error code returned when a post-only order would immediately take liquidity.
pub const ALPACA_POST_ONLY_ERROR_CODE: &str = "51019";

/// ALPACA cancel source code used when a post-only order is auto-cancelled for taking liquidity.
pub const ALPACA_POST_ONLY_CANCEL_SOURCE: &str = "31";

/// Human-readable reason used when a post-only order is auto-cancelled for taking liquidity.
pub const ALPACA_POST_ONLY_CANCEL_REASON: &str = "POST_ONLY would take liquidity";
