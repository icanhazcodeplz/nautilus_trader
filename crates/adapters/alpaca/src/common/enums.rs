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
pub enum AlpacaSide {
    /// Buy side of a trade or order.
    Buy,
    /// Sell side of a trade or order.
    Sell,
}

impl From<OrderSide> for AlpacaSide {
    fn from(value: OrderSide) -> Self {
        match value {
            OrderSide::Buy => Self::Buy,
            OrderSide::Sell => Self::Sell,
            _ => panic!("Invalid `OrderSide`"),
        }
    }
}

impl From<AlpacaSide> for OrderSide {
    fn from(side: AlpacaSide) -> Self {
        match side {
            AlpacaSide::Buy => Self::Buy,
            AlpacaSide::Sell => Self::Sell,
        }
    }
}

impl From<AlpacaSide> for AggressorSide {
    fn from(value: AlpacaSide) -> Self {
        match value {
            AlpacaSide::Buy => Self::Buyer,
            AlpacaSide::Sell => Self::Seller,
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
pub enum AlpacaOrderType {
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

impl From<OrderType> for AlpacaOrderType {
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

impl From<AlpacaOrderType> for OrderType {
    fn from(ord_type: AlpacaOrderType) -> Self {
        match ord_type {
            AlpacaOrderType::Market => Self::Market,
            AlpacaOrderType::Limit => Self::Limit,
            AlpacaOrderType::Stop => Self::StopMarket,
            AlpacaOrderType::StopLimit => Self::StopLimit,
            AlpacaOrderType::TrailingStop => Self::TrailingStopMarket,
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
pub enum AlpacaOrderStatus {
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

impl From<AlpacaOrderStatus> for OrderStatus {
    fn from(status: AlpacaOrderStatus) -> Self {
        match status {
            AlpacaOrderStatus::New
            | AlpacaOrderStatus::Accepted
            | AlpacaOrderStatus::PendingNew => Self::Accepted,
            AlpacaOrderStatus::PartiallyFilled => Self::PartiallyFilled,
            AlpacaOrderStatus::Filled => Self::Filled,
            AlpacaOrderStatus::Canceled
            | AlpacaOrderStatus::PendingCancel => Self::Canceled,
            AlpacaOrderStatus::Rejected => Self::Rejected,
            AlpacaOrderStatus::Expired => Self::Expired,
            AlpacaOrderStatus::Replaced
            | AlpacaOrderStatus::PendingReplace
            | AlpacaOrderStatus::Stopped
            | AlpacaOrderStatus::Suspended
            | AlpacaOrderStatus::DoneForDay
            | AlpacaOrderStatus::Calculated
            | AlpacaOrderStatus::AcceptedForBidding => Self::Accepted,
        }
    }
}

impl From<OrderStatus> for AlpacaOrderStatus {
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
pub enum AlpacaTimeInForce {
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
pub enum AlpacaAssetClass {
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
pub enum AlpacaAssetStatus {
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
pub enum AlpacaExecType {
    #[default]
    None,
    Taker,
    Maker,
}

impl From<LiquiditySide> for AlpacaExecType {
    fn from(value: LiquiditySide) -> Self {
        match value {
            LiquiditySide::NoLiquiditySide => Self::None,
            LiquiditySide::Taker => Self::Taker,
            LiquiditySide::Maker => Self::Maker,
        }
    }
}

impl From<AlpacaExecType> for LiquiditySide {
    fn from(exec: AlpacaExecType) -> Self {
        match exec {
            AlpacaExecType::Maker => Self::Maker,
            AlpacaExecType::Taker => Self::Taker,
            AlpacaExecType::None => Self::NoLiquiditySide,
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
pub enum AlpacaBarTimeframe {
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
pub enum AlpacaOrderClass {
    /// Simple order.
    Simple,
    /// Bracket order (entry + take profit + stop loss).
    Bracket,
    /// One-cancels-other order.
    Oco,
    /// One-triggers-other order.
    Oto,
}
