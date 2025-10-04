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

//! Data transfer objects for deserializing Alpaca HTTP API payloads.

use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use ustr::Ustr;

// =============================================================================
// Market Data Models
// =============================================================================

/// Represents an asset (tradable symbol) from the GET /v2/assets endpoint.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaAsset {
    /// Asset ID (UUID).
    pub id: String,
    /// Symbol name.
    pub symbol: String,
    /// Exchange where the asset is traded.
    pub exchange: String,
    /// Asset class (us_equity, crypto).
    pub class: String,
    /// Whether the asset is tradable.
    pub tradable: bool,
    /// Whether the asset is marginable.
    pub marginable: bool,
    /// Whether the asset is shortable.
    pub shortable: bool,
    /// Whether the asset is easy to borrow for shorting.
    pub easy_to_borrow: bool,
    /// Whether the asset is fractionable.
    pub fractionable: bool,
    /// Minimum order size.
    #[serde(default)]
    pub min_order_size: Option<String>,
    /// Minimum trade increment.
    #[serde(default)]
    pub min_trade_increment: Option<String>,
    /// Price increment.
    #[serde(default)]
    pub price_increment: Option<String>,
    /// Status of the asset.
    pub status: String,
}

/// Represents a bar/candlestick from the GET /v2/stocks/{symbol}/bars endpoint.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaBar {
    /// Timestamp (RFC3339).
    pub t: String,
    /// Open price.
    pub o: Decimal,
    /// High price.
    pub h: Decimal,
    /// Low price.
    pub l: Decimal,
    /// Close price.
    pub c: Decimal,
    /// Volume.
    pub v: u64,
    /// Number of trades.
    #[serde(default)]
    pub n: Option<u64>,
    /// Volume weighted average price.
    #[serde(default)]
    pub vw: Option<Decimal>,
}

/// Response wrapper for bars endpoint.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaBarsResponse {
    /// Map of symbol to bars.
    pub bars: std::collections::HashMap<String, Vec<AlpacaBar>>,
    /// Symbol queried.
    pub symbol: String,
    /// Next page token.
    #[serde(default)]
    pub next_page_token: Option<String>,
}

/// Represents a trade from the GET /v2/stocks/{symbol}/trades endpoint.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaTrade {
    /// Timestamp (RFC3339).
    pub t: String,
    /// Exchange where the trade occurred.
    pub x: String,
    /// Price.
    pub p: Decimal,
    /// Size.
    pub s: u64,
    /// Trade conditions.
    #[serde(default)]
    pub c: Option<Vec<String>>,
    /// Trade ID.
    pub i: u64,
    /// Tape.
    #[serde(default)]
    pub z: Option<String>,
}

/// Response wrapper for trades endpoint.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaTradesResponse {
    /// List of trades.
    pub trades: Vec<AlpacaTrade>,
    /// Symbol queried.
    pub symbol: String,
    /// Next page token.
    #[serde(default)]
    pub next_page_token: Option<String>,
}

/// Represents a quote from the GET /v2/stocks/{symbol}/quotes endpoint.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaQuote {
    /// Timestamp (RFC3339).
    pub t: String,
    /// Ask exchange.
    pub ax: String,
    /// Ask price.
    pub ap: Decimal,
    /// Ask size.
    pub as_: u64,
    /// Bid exchange.
    pub bx: String,
    /// Bid price.
    pub bp: Decimal,
    /// Bid size.
    pub bs: u64,
    /// Quote conditions.
    #[serde(default)]
    pub c: Option<Vec<String>>,
    /// Tape.
    #[serde(default)]
    pub z: Option<String>,
}

/// Response wrapper for quotes endpoint.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaQuotesResponse {
    /// List of quotes.
    pub quotes: Vec<AlpacaQuote>,
    /// Symbol queried.
    pub symbol: String,
    /// Next page token.
    #[serde(default)]
    pub next_page_token: Option<String>,
}

/// Represents the latest quote for a symbol.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaLatestQuote {
    /// Symbol.
    pub symbol: String,
    /// Ask price.
    pub ap: Decimal,
    /// Ask size.
    pub as_: u64,
    /// Ask exchange.
    pub ax: String,
    /// Bid price.
    pub bp: Decimal,
    /// Bid size.
    pub bs: u64,
    /// Bid exchange.
    pub bx: String,
    /// Timestamp.
    pub t: String,
}

/// Represents the latest trade for a symbol.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaLatestTrade {
    /// Symbol.
    pub symbol: String,
    /// Price.
    pub p: Decimal,
    /// Size.
    pub s: u64,
    /// Exchange.
    pub x: String,
    /// Timestamp.
    pub t: String,
}

/// Represents a snapshot of current market data.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaSnapshot {
    /// Symbol.
    pub symbol: String,
    /// Latest trade.
    #[serde(default)]
    pub latest_trade: Option<AlpacaLatestTrade>,
    /// Latest quote.
    #[serde(default)]
    pub latest_quote: Option<AlpacaLatestQuote>,
    /// Minute bar.
    #[serde(default)]
    pub minute_bar: Option<AlpacaBar>,
    /// Daily bar.
    #[serde(default)]
    pub daily_bar: Option<AlpacaBar>,
    /// Previous daily bar.
    #[serde(default)]
    pub prev_daily_bar: Option<AlpacaBar>,
}

// =============================================================================
// Account Models
// =============================================================================

/// Represents account information from GET /v2/account.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaAccount {
    /// Account ID.
    pub id: String,
    /// Account number.
    pub account_number: String,
    /// Account status.
    pub status: String,
    /// Currency (USD).
    pub currency: String,
    /// Cash balance.
    pub cash: Decimal,
    /// Portfolio value.
    pub portfolio_value: Decimal,
    /// Whether pattern day trader.
    pub pattern_day_trader: bool,
    /// Whether trading is blocked.
    pub trading_blocked: bool,
    /// Whether transfers are blocked.
    pub transfers_blocked: bool,
    /// Whether account is blocked.
    pub account_blocked: bool,
    /// Account created timestamp.
    pub created_at: String,
    /// Whether shorting is enabled.
    pub shorting_enabled: bool,
    /// Long market value.
    pub long_market_value: Decimal,
    /// Short market value.
    pub short_market_value: Decimal,
    /// Equity.
    pub equity: Decimal,
    /// Last equity.
    pub last_equity: Decimal,
    /// Multiplier (1 for cash, 2 or 4 for margin).
    pub multiplier: Decimal,
    /// Buying power.
    pub buying_power: Decimal,
    /// Initial margin.
    pub initial_margin: Decimal,
    /// Maintenance margin.
    pub maintenance_margin: Decimal,
    /// SMA (Special Memorandum Account).
    #[serde(default)]
    pub sma: Option<Decimal>,
    /// Day trade count in last 5 trading days.
    pub daytrade_count: i32,
    /// Last maintenance margin.
    pub last_maintenance_margin: Decimal,
    /// Daytrading buying power.
    pub daytrading_buying_power: Decimal,
    /// Regt buying power (for cash accounts).
    pub regt_buying_power: Decimal,
}

// =============================================================================
// Order Models
// =============================================================================

/// Represents an order from GET /v2/orders or POST /v2/orders.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaOrder {
    /// Order ID.
    pub id: String,
    /// Client order ID.
    pub client_order_id: String,
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
    /// Replaced timestamp.
    #[serde(default)]
    pub replaced_at: Option<String>,
    /// Order that replaced this order.
    #[serde(default)]
    pub replaced_by: Option<String>,
    /// Order that this order replaces.
    #[serde(default)]
    pub replaces: Option<String>,
    /// Asset ID.
    pub asset_id: String,
    /// Symbol.
    pub symbol: String,
    /// Asset class.
    pub asset_class: String,
    /// Notional amount (for fractional orders).
    #[serde(default)]
    pub notional: Option<Decimal>,
    /// Order quantity.
    #[serde(default)]
    pub qty: Option<Decimal>,
    /// Filled quantity.
    pub filled_qty: Decimal,
    /// Filled average price.
    #[serde(default)]
    pub filled_avg_price: Option<Decimal>,
    /// Order type: market, limit, stop, stop_limit, trailing_stop.
    pub order_type: String,
    /// Side: buy or sell.
    pub side: String,
    /// Time in force: day, gtc, opg, cls, ioc, fok.
    pub time_in_force: String,
    /// Limit price.
    #[serde(default)]
    pub limit_price: Option<Decimal>,
    /// Stop price.
    #[serde(default)]
    pub stop_price: Option<Decimal>,
    /// Order status.
    pub status: String,
    /// Extended hours.
    pub extended_hours: bool,
    /// Legs (for complex orders).
    #[serde(default)]
    pub legs: Option<Vec<AlpacaOrder>>,
    /// Trail percent.
    #[serde(default)]
    pub trail_percent: Option<Decimal>,
    /// Trail price.
    #[serde(default)]
    pub trail_price: Option<Decimal>,
    /// HWMQ (high water mark qty).
    #[serde(default)]
    pub hwm: Option<Decimal>,
}

/// Request to create an order via POST /v2/orders.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaOrderRequest {
    /// Symbol to trade.
    pub symbol: String,
    /// Quantity.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub qty: Option<Decimal>,
    /// Notional amount (for fractional/dollar-based orders).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub notional: Option<Decimal>,
    /// Side: buy or sell.
    pub side: String,
    /// Order type.
    #[serde(rename = "type")]
    pub order_type: String,
    /// Time in force.
    pub time_in_force: String,
    /// Limit price.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit_price: Option<Decimal>,
    /// Stop price.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stop_price: Option<Decimal>,
    /// Trail price.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trail_price: Option<Decimal>,
    /// Trail percent.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trail_percent: Option<Decimal>,
    /// Extended hours.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub extended_hours: Option<bool>,
    /// Client order ID.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub client_order_id: Option<String>,
    /// Order class: simple, bracket, oco, oto.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub order_class: Option<String>,
    /// Take profit leg.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub take_profit: Option<TakeProfitSpec>,
    /// Stop loss leg.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stop_loss: Option<StopLossSpec>,
}

/// Take profit specification for bracket orders.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TakeProfitSpec {
    /// Limit price for take profit.
    pub limit_price: Decimal,
}

/// Stop loss specification for bracket orders.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StopLossSpec {
    /// Stop price for stop loss.
    pub stop_price: Decimal,
    /// Limit price (for stop-limit orders).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit_price: Option<Decimal>,
}

// =============================================================================
// Position Models
// =============================================================================

/// Represents a position from GET /v2/positions.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaPosition {
    /// Asset ID.
    pub asset_id: String,
    /// Symbol.
    pub symbol: String,
    /// Exchange.
    pub exchange: String,
    /// Asset class.
    pub asset_class: String,
    /// Average entry price.
    pub avg_entry_price: Decimal,
    /// Quantity (negative for short).
    pub qty: Decimal,
    /// Side: long or short.
    pub side: String,
    /// Market value.
    pub market_value: Decimal,
    /// Cost basis.
    pub cost_basis: Decimal,
    /// Unrealized P&L.
    pub unrealized_pl: Decimal,
    /// Unrealized P&L percent.
    pub unrealized_plpc: Decimal,
    /// Unrealized intraday P&L.
    pub unrealized_intraday_pl: Decimal,
    /// Unrealized intraday P&L percent.
    pub unrealized_intraday_plpc: Decimal,
    /// Current price.
    pub current_price: Decimal,
    /// Last day price.
    pub lastday_price: Decimal,
    /// Change today.
    pub change_today: Decimal,
}

// =============================================================================
// Clock Models
// =============================================================================

/// Represents market clock from GET /v2/clock.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaClock {
    /// Current timestamp.
    pub timestamp: String,
    /// Whether market is open.
    pub is_open: bool,
    /// Next open timestamp.
    pub next_open: String,
    /// Next close timestamp.
    pub next_close: String,
}

// =============================================================================
// Calendar Models
// =============================================================================

/// Represents a calendar day from GET /v2/calendar.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AlpacaCalendar {
    /// Date.
    pub date: String,
    /// Market open time.
    pub open: String,
    /// Market close time.
    pub close: String,
}
