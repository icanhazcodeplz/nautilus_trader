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

//! Enumerations mapping Alpaca concepts onto idiomatic Nautilus variants.

use nautilus_model::enums::{AggressorSide, LiquiditySide, OrderSide, OrderStatus, OrderType};
use serde::{Deserialize, Serialize};
use strum::{AsRefStr, Display, EnumIter, EnumString};

/// Represents the side of an order or trade (Buy/Sell).
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ALPACASide {
    /// Buy side of a trade or order.
    Buy,
    /// Sell side of a trade or order.
    Sell,
}

impl From<OrderSide> for ALPACASide {
    fn from(value: OrderSide) -> Self {
        match value {
            OrderSide::Buy => Self::Buy,
            OrderSide::Sell => Self::Sell,
            _ => panic!("Invalid `OrderSide`"),
        }
    }
}

impl From<ALPACASide> for OrderSide {
    fn from(side: ALPACASide) -> Self {
        match side {
            ALPACASide::Buy => Self::Buy,
            ALPACASide::Sell => Self::Sell,
        }
    }
}

impl From<ALPACASide> for AggressorSide {
    fn from(value: ALPACASide) -> Self {
        match value {
            ALPACASide::Buy => Self::Buyer,
            ALPACASide::Sell => Self::Seller,
        }
    }
}

/// Represents the available order types on Alpaca.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ALPACAOrderType {
    /// Market order, executed immediately at current market price.
    Market,
    /// Limit order, executed only at specified price or better.
    Limit,
    /// Stop order (stop-loss), triggers a market order when stop price is reached.
    Stop,
    /// Stop-limit order, triggers a limit order when stop price is reached.
    StopLimit,
    /// Trailing stop order with percentage or dollar amount.
    TrailingStop,
}

impl From<OrderType> for ALPACAOrderType {
    fn from(value: OrderType) -> Self {
        match value {
            OrderType::Market => Self::Market,
            OrderType::Limit => Self::Limit,
            OrderType::StopMarket => Self::Stop,
            OrderType::StopLimit => Self::StopLimit,
            OrderType::TrailingStopMarket => Self::TrailingStop,
            _ => panic!("Unsupported `OrderType` for Alpaca: {value:?}"),
        }
    }
}

impl From<ALPACAOrderType> for OrderType {
    fn from(ord_type: ALPACAOrderType) -> Self {
        match ord_type {
            ALPACAOrderType::Market => Self::Market,
            ALPACAOrderType::Limit => Self::Limit,
            ALPACAOrderType::Stop => Self::StopMarket,
            ALPACAOrderType::StopLimit => Self::StopLimit,
            ALPACAOrderType::TrailingStop => Self::TrailingStopMarket,
        }
    }
}

/// Represents the possible states of an order throughout its lifecycle.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ALPACAOrderStatus {
    /// Order has been received by Alpaca and is being processed.
    New,
    /// Order has been partially filled.
    PartiallyFilled,
    /// Order has been completely filled.
    Filled,
    /// Order is waiting to be filled but can be cancelled.
    DoneForDay,
    /// Order has been cancelled.
    Canceled,
    /// Order has been replaced by another order.
    Replaced,
    /// Order has been received by Alpaca but not yet acknowledged.
    PendingCancel,
    /// Order has been stopped.
    Stopped,
    /// Order has been rejected.
    Rejected,
    /// Order is suspended.
    Suspended,
    /// Order is pending replacement.
    PendingNew,
    /// Order has been accepted for execution.
    Accepted,
    /// Order has been received by Alpaca's systems but not routed to execution yet.
    PendingReplace,
    /// Order is being processed.
    Calculated,
    /// Order has expired.
    Expired,
    /// Order has been accepted for bidding.
    AcceptedForBidding,
}

impl From<ALPACAOrderStatus> for OrderStatus {
    fn from(status: ALPACAOrderStatus) -> Self {
        match status {
            ALPACAOrderStatus::New
            | ALPACAOrderStatus::Accepted
            | ALPACAOrderStatus::PendingNew => Self::Accepted,
            ALPACAOrderStatus::PartiallyFilled => Self::PartiallyFilled,
            ALPACAOrderStatus::Filled => Self::Filled,
            ALPACAOrderStatus::Canceled
            | ALPACAOrderStatus::PendingCancel => Self::Canceled,
            ALPACAOrderStatus::Rejected => Self::Rejected,
            ALPACAOrderStatus::Expired => Self::Expired,
            ALPACAOrderStatus::Replaced
            | ALPACAOrderStatus::PendingReplace
            | ALPACAOrderStatus::Stopped
            | ALPACAOrderStatus::Suspended
            | ALPACAOrderStatus::DoneForDay
            | ALPACAOrderStatus::Calculated
            | ALPACAOrderStatus::AcceptedForBidding => Self::Accepted,
        }
    }
}

impl From<OrderStatus> for ALPACAOrderStatus {
    fn from(value: OrderStatus) -> Self {
        match value {
            OrderStatus::Accepted | OrderStatus::Submitted => Self::Accepted,
            OrderStatus::PartiallyFilled => Self::PartiallyFilled,
            OrderStatus::Filled => Self::Filled,
            OrderStatus::Canceled => Self::Canceled,
            OrderStatus::Rejected => Self::Rejected,
            OrderStatus::Expired => Self::Expired,
            _ => Self::New,
        }
    }
}

/// Represents time-in-force for Alpaca orders.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ALPACATimeInForce {
    /// Day order - valid until market close.
    Day,
    /// Good-till-canceled - valid until explicitly canceled.
    Gtc,
    /// At the open - execute at market open or cancel.
    Opg,
    /// At the close - execute at market close.
    Cls,
    /// Immediate-or-cancel - execute immediately or cancel.
    Ioc,
    /// Fill-or-kill - fill entire order immediately or cancel.
    Fok,
}

/// Represents asset class types on Alpaca.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    Default,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
#[cfg_attr(
    feature = "python",
    pyo3::pyclass(eq, eq_int, module = "nautilus_trader.core.nautilus_pyo3.alpaca")
)]
pub enum ALPACAAssetClass {
    /// US equity securities.
    #[default]
    UsEquity,
    /// Cryptocurrency.
    Crypto,
}

/// Represents an asset status on Alpaca.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ALPACAAssetStatus {
    /// Asset is active and tradable.
    Active,
    /// Asset is inactive and not tradable.
    Inactive,
}

/// Represents the type of execution that generated a trade.
#[derive(
    Copy,
    Clone,
    Debug,
    Default,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
pub enum ALPACAExecType {
    #[default]
    None,
    Taker,
    Maker,
}

impl From<LiquiditySide> for ALPACAExecType {
    fn from(value: LiquiditySide) -> Self {
        match value {
            LiquiditySide::NoLiquiditySide => Self::None,
            LiquiditySide::Taker => Self::Taker,
            LiquiditySide::Maker => Self::Maker,
        }
    }
}

impl From<ALPACAExecType> for LiquiditySide {
    fn from(exec: ALPACAExecType) -> Self {
        match exec {
            ALPACAExecType::Maker => Self::Maker,
            ALPACAExecType::Taker => Self::Taker,
            ALPACAExecType::None => Self::NoLiquiditySide,
        }
    }
}

/// Represents Alpaca bar/candlestick timeframes.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
pub enum ALPACABarTimeframe {
    #[serde(rename = "1Min")]
    Minute1,
    #[serde(rename = "5Min")]
    Minute5,
    #[serde(rename = "15Min")]
    Minute15,
    #[serde(rename = "1Hour")]
    Hour1,
    #[serde(rename = "1Day")]
    Day1,
}

/// Represents Alpaca order class types.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ALPACAOrderClass {
    /// Simple order.
    Simple,
    /// Bracket order (entry + take profit + stop loss).
    Bracket,
    /// One-cancels-other order.
    Oco,
    /// One-triggers-other order.
    Oto,
}

/// Represents the instrument type on Alpaca.
#[derive(
    Copy,
    Clone,
    Debug,
    Default,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "UPPERCASE")]
#[cfg_attr(
    feature = "python",
    pyo3::pyclass(eq, eq_int, module = "nautilus_trader.core.nautilus_pyo3.alpaca")
)]
pub enum ALPACAInstrumentType {
    #[default]
    /// Stock/Equity instrument.
    Stock,
    /// Cryptocurrency.
    Crypto,
    /// Spot products.
    Spot,
    /// Perpetual swap products.
    Swap,
    /// Futures products.
    Futures,
    /// Option products.
    Option,
}

/// Represents an instrument contract type on Alpaca.
#[derive(
    Copy,
    Clone,
    Default,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
#[cfg_attr(
    feature = "python",
    pyo3::pyclass(eq, eq_int, module = "nautilus_trader.core.nautilus_pyo3.alpaca")
)]
pub enum ALPACAContractType {
    #[serde(rename = "")]
    #[default]
    None,
    Linear,
    Inverse,
}

/// Represents position mode on Alpaca.
#[derive(
    Copy,
    Clone,
    Debug,
    Default,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[cfg_attr(
    feature = "python",
    pyo3::pyclass(eq, eq_int, module = "nautilus_trader.core.nautilus_pyo3.alpaca")
)]
pub enum ALPACAPositionMode {
    #[default]
    #[serde(rename = "net_mode")]
    NetMode,
    #[serde(rename = "long_short_mode")]
    LongShortMode,
}

/// Represents position side on Alpaca.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ALPACAPositionSide {
    #[serde(rename = "")]
    None,
    Net,
    Long,
    Short,
}

/// Represents order book channel types on Alpaca.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
pub enum ALPACABookChannel {
    /// Standard depth-first book channel.
    Book,
    /// Low-latency Level 2 time-based book channel.
    BookL2Tbt,
    /// Low-latency 50-depth Level 2 time-based book channel.
    Books50L2Tbt,
}

/// Represents trading mode on Alpaca.
#[derive(
    Copy,
    Clone,
    Debug,
    Default,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
pub enum ALPACATradeMode {
    #[default]
    Cash,
    Isolated,
    Cross,
}

/// Represents trigger type for conditional orders.
#[derive(
    Copy,
    Clone,
    Debug,
    Default,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum ALPACATriggerType {
    #[default]
    Last,
    Index,
    Mark,
}

/// Represents order book action type.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
#[serde(rename_all = "lowercase")]
pub enum ALPACABookAction {
    /// Incremental update.
    Update,
    /// Full snapshot.
    Snapshot,
}

/// Represents candle confirmation status.
#[derive(
    Copy,
    Clone,
    Debug,
    Display,
    PartialEq,
    Eq,
    Hash,
    AsRefStr,
    EnumIter,
    EnumString,
    Serialize,
    Deserialize,
)]
pub enum ALPACACandleConfirm {
    /// Candle is incomplete (partial).
    #[serde(rename = "0")]
    Partial,
    /// Candle is complete (closed).
    #[serde(rename = "1")]
    Closed,
}

/// Helper function to check if an order type is conditional (placeholder).
pub fn is_conditional_order(_order_type: &ALPACAOrderType) -> bool {
    false // Alpaca doesn't have conditional orders like OKX
}

/// Helper function to convert conditional order to algo type (placeholder).
pub fn conditional_order_to_algo_type(_order_type: &OrderType) -> Option<String> {
    None // Not applicable to Alpaca
}
