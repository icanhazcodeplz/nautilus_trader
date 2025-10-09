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

//! Enumerations for the Alpaca adapter.

use serde::{Deserialize, Serialize};
use strum::{Display, EnumString};

/// Represents the Alpaca trading environment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Display, EnumString)]
#[serde(rename_all = "lowercase")]
#[strum(serialize_all = "lowercase")]
pub enum AlpacaEnvironment {
    /// Paper trading environment for testing.
    Paper,
    /// Live trading environment.
    Live,
}

impl Default for AlpacaEnvironment {
    fn default() -> Self {
        Self::Paper
    }
}

/// Represents asset classes supported by Alpaca.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Display, EnumString)]
#[serde(rename_all = "snake_case")]
#[strum(serialize_all = "snake_case")]
pub enum AlpacaAssetClass {
    /// US equity securities.
    #[serde(rename = "us_equity")]
    #[strum(serialize = "us_equity")]
    UsEquity,
    /// Cryptocurrency.
    Crypto,
    /// Options contracts.
    #[serde(rename = "us_option")]
    #[strum(serialize = "us_option")]
    UsOption,
}

/// Represents order types supported by Alpaca.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Display, EnumString)]
#[serde(rename_all = "snake_case")]
#[strum(serialize_all = "snake_case")]
pub enum AlpacaOrderType {
    /// Market order.
    Market,
    /// Limit order.
    Limit,
    /// Stop limit order.
    #[serde(rename = "stop_limit")]
    #[strum(serialize = "stop_limit")]
    StopLimit,
    /// Trailing stop order.
    #[serde(rename = "trailing_stop")]
    #[strum(serialize = "trailing_stop")]
    TrailingStop,
}

/// Represents order side (buy or sell).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Display, EnumString)]
#[serde(rename_all = "lowercase")]
#[strum(serialize_all = "lowercase")]
pub enum AlpacaOrderSide {
    /// Buy order.
    Buy,
    /// Sell order.
    Sell,
}

/// Represents time in force options for orders.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Display, EnumString)]
#[serde(rename_all = "lowercase")]
#[strum(serialize_all = "lowercase")]
pub enum AlpacaTimeInForce {
    /// Day order (valid until market close).
    Day,
    /// Good till canceled.
    Gtc,
    /// Immediate or cancel.
    Ioc,
    /// Fill or kill.
    Fok,
    /// Good till date.
    Gtd,
    /// Opening auction.
    Opg,
    /// Closing auction.
    Cls,
}

/// Represents order status.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Display, EnumString)]
#[serde(rename_all = "snake_case")]
#[strum(serialize_all = "snake_case")]
pub enum AlpacaOrderStatus {
    /// Order has been received but not yet accepted.
    New,
    /// Order has been accepted but not yet executed.
    Accepted,
    /// Order is being processed.
    #[serde(rename = "pending_new")]
    #[strum(serialize = "pending_new")]
    PendingNew,
    /// Order has been partially filled.
    #[serde(rename = "partially_filled")]
    #[strum(serialize = "partially_filled")]
    PartiallyFilled,
    /// Order has been completely filled.
    Filled,
    /// Order cancellation is being processed.
    #[serde(rename = "pending_cancel")]
    #[strum(serialize = "pending_cancel")]
    PendingCancel,
    /// Order has been canceled.
    Canceled,
    /// Order has expired.
    Expired,
    /// Order has been rejected.
    Rejected,
    /// Order has been stopped.
    Stopped,
    /// Order cancellation has been rejected.
    #[serde(rename = "cancel_rejected")]
    #[strum(serialize = "cancel_rejected")]
    CancelRejected,
    /// Order is being replaced.
    #[serde(rename = "pending_replace")]
    #[strum(serialize = "pending_replace")]
    PendingReplace,
    /// Order has been replaced.
    Replaced,
    /// Order replacement has been rejected.
    #[serde(rename = "replace_rejected")]
    #[strum(serialize = "replace_rejected")]
    ReplaceRejected,
    /// Order is suspended.
    Suspended,
    /// Order is done for day.
    #[serde(rename = "done_for_day")]
    #[strum(serialize = "done_for_day")]
    DoneForDay,
}

/// Represents market data feed types.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Display, EnumString)]
#[serde(rename_all = "lowercase")]
#[strum(serialize_all = "lowercase")]
pub enum AlpacaFeed {
    /// IEX feed (free for stocks).
    Iex,
    /// SIP feed (consolidated market data, requires subscription).
    Sip,
}

impl Default for AlpacaFeed {
    fn default() -> Self {
        Self::Iex
    }
}

/// Represents bar/candle timeframes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Display, EnumString)]
pub enum AlpacaTimeframe {
    /// 1 minute bars.
    #[serde(rename = "1Min")]
    #[strum(serialize = "1Min")]
    Min1,
    /// 5 minute bars.
    #[serde(rename = "5Min")]
    #[strum(serialize = "5Min")]
    Min5,
    /// 15 minute bars.
    #[serde(rename = "15Min")]
    #[strum(serialize = "15Min")]
    Min15,
    /// 1 hour bars.
    #[serde(rename = "1Hour")]
    #[strum(serialize = "1Hour")]
    Hour1,
    /// 1 day bars.
    #[serde(rename = "1Day")]
    #[strum(serialize = "1Day")]
    Day1,
}

////////////////////////////////////////////////////////////////////////////////
// Tests
////////////////////////////////////////////////////////////////////////////////

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_environment_default() {
        assert_eq!(AlpacaEnvironment::default(), AlpacaEnvironment::Paper);
    }

    #[test]
    fn test_feed_default() {
        assert_eq!(AlpacaFeed::default(), AlpacaFeed::Iex);
    }

    #[test]
    fn test_environment_serialization() {
        let env = AlpacaEnvironment::Paper;
        let json = serde_json::to_string(&env).unwrap();
        assert_eq!(json, r#""paper""#);

        let env = AlpacaEnvironment::Live;
        let json = serde_json::to_string(&env).unwrap();
        assert_eq!(json, r#""live""#);
    }

    #[test]
    fn test_order_side_serialization() {
        let side = AlpacaOrderSide::Buy;
        let json = serde_json::to_string(&side).unwrap();
        assert_eq!(json, r#""buy""#);

        let side = AlpacaOrderSide::Sell;
        let json = serde_json::to_string(&side).unwrap();
        assert_eq!(json, r#""sell""#);
    }

    #[test]
    fn test_order_type_serialization() {
        let order_type = AlpacaOrderType::Market;
        let json = serde_json::to_string(&order_type).unwrap();
        assert_eq!(json, r#""market""#);

        let order_type = AlpacaOrderType::StopLimit;
        let json = serde_json::to_string(&order_type).unwrap();
        assert_eq!(json, r#""stop_limit""#);
    }
}
