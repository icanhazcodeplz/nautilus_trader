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

//! Common data models for the Alpaca adapter.

use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

use super::enums::{AlpacaAssetClass, AlpacaOrderSide, AlpacaOrderStatus, AlpacaOrderType, AlpacaTimeInForce};

/// Represents an Alpaca asset (stock, crypto, or option).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaAsset {
    /// Asset ID (UUID).
    pub id: String,
    /// Asset class (e.g., us_equity, crypto, us_option).
    #[serde(rename = "class")]
    pub asset_class: AlpacaAssetClass,
    /// Exchange where the asset is traded.
    pub exchange: String,
    /// Symbol name.
    pub symbol: String,
    /// Asset name.
    pub name: Option<String>,
    /// Trading status (active, inactive).
    pub status: String,
    /// Whether the asset is tradable.
    pub tradable: bool,
    /// Whether the asset is marginable.
    pub marginable: bool,
    /// Whether the asset is shortable.
    pub shortable: bool,
    /// Whether the asset is easy to borrow.
    pub easy_to_borrow: bool,
    /// Whether the asset is fractionable.
    pub fractionable: bool,
}

/// Represents an Alpaca order.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaOrder {
    /// Order ID (UUID).
    pub id: String,
    /// Client order ID.
    pub client_order_id: String,
    /// Creation timestamp.
    pub created_at: DateTime<Utc>,
    /// Last update timestamp.
    pub updated_at: Option<DateTime<Utc>>,
    /// Submission timestamp.
    pub submitted_at: Option<DateTime<Utc>>,
    /// Fill timestamp.
    pub filled_at: Option<DateTime<Utc>>,
    /// Expiration timestamp.
    pub expired_at: Option<DateTime<Utc>>,
    /// Cancellation timestamp.
    pub canceled_at: Option<DateTime<Utc>>,
    /// Failed timestamp.
    pub failed_at: Option<DateTime<Utc>>,
    /// Replaced by order ID.
    pub replaced_at: Option<DateTime<Utc>>,
    /// Order that replaced this order.
    pub replaced_by: Option<String>,
    /// Order that this order replaces.
    pub replaces: Option<String>,
    /// Asset class.
    pub asset_class: AlpacaAssetClass,
    /// Symbol.
    pub symbol: String,
    /// Asset ID.
    pub asset_id: String,
    /// Order quantity.
    pub qty: Option<Decimal>,
    /// Notional amount (for fractional orders).
    pub notional: Option<Decimal>,
    /// Filled quantity.
    pub filled_qty: Decimal,
    /// Filled average price.
    pub filled_avg_price: Option<Decimal>,
    /// Order type.
    pub order_type: AlpacaOrderType,
    /// Order side (buy/sell).
    pub side: AlpacaOrderSide,
    /// Time in force.
    pub time_in_force: AlpacaTimeInForce,
    /// Limit price (for limit orders).
    pub limit_price: Option<Decimal>,
    /// Stop price (for stop orders).
    pub stop_price: Option<Decimal>,
    /// Order status.
    pub status: AlpacaOrderStatus,
    /// Whether order is eligible for extended hours.
    pub extended_hours: bool,
}

/// Represents an Alpaca trade/fill.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaTrade {
    /// Trade ID.
    pub id: String,
    /// Order ID this trade belongs to.
    pub order_id: String,
    /// Symbol.
    pub symbol: String,
    /// Exchange code.
    pub exchange: String,
    /// Trade price.
    pub price: Decimal,
    /// Trade quantity.
    pub qty: Decimal,
    /// Trade side (buy/sell).
    pub side: AlpacaOrderSide,
    /// Trade timestamp.
    pub timestamp: DateTime<Utc>,
}

/// Represents a market data quote.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaQuote {
    /// Symbol.
    #[serde(rename = "S")]
    pub symbol: String,
    /// Bid price.
    #[serde(rename = "bp")]
    pub bid_price: Decimal,
    /// Bid size.
    #[serde(rename = "bs")]
    pub bid_size: u64,
    /// Ask price.
    #[serde(rename = "ap")]
    pub ask_price: Decimal,
    /// Ask size.
    #[serde(rename = "as")]
    pub ask_size: u64,
    /// Timestamp (nanoseconds since epoch).
    #[serde(rename = "t")]
    pub timestamp: DateTime<Utc>,
}

/// Represents a market data trade.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaMarketTrade {
    /// Symbol.
    #[serde(rename = "S")]
    pub symbol: String,
    /// Trade price.
    #[serde(rename = "p")]
    pub price: Decimal,
    /// Trade size.
    #[serde(rename = "s")]
    pub size: u64,
    /// Timestamp (nanoseconds since epoch).
    #[serde(rename = "t")]
    pub timestamp: DateTime<Utc>,
    /// Exchange code.
    #[serde(rename = "x")]
    pub exchange: String,
}

/// Represents a market data bar (candlestick).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AlpacaBar {
    /// Symbol.
    #[serde(rename = "S")]
    pub symbol: String,
    /// Open price.
    #[serde(rename = "o")]
    pub open: Decimal,
    /// High price.
    #[serde(rename = "h")]
    pub high: Decimal,
    /// Low price.
    #[serde(rename = "l")]
    pub low: Decimal,
    /// Close price.
    #[serde(rename = "c")]
    pub close: Decimal,
    /// Volume.
    #[serde(rename = "v")]
    pub volume: u64,
    /// Timestamp (start of bar).
    #[serde(rename = "t")]
    pub timestamp: DateTime<Utc>,
    /// Number of trades in bar.
    #[serde(rename = "n")]
    pub trade_count: Option<u64>,
    /// VWAP.
    #[serde(rename = "vw")]
    pub vwap: Option<Decimal>,
}
