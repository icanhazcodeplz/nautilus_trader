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

//! Parsing utilities that convert Alpaca API payloads into Nautilus domain models.

use std::str::FromStr;

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use nautilus_core::{UnixNanos, UUID4, datetime::NANOSECONDS_IN_MILLISECOND};
use nautilus_model::{
    currencies::CURRENCY_MAP,
    data::{Bar, BarSpecification, BarType, QuoteTick, TradeTick},
    enums::{
        AccountType, AggressorSide, BarAggregation, LiquiditySide,
        OrderSide, OrderStatus, OrderType, PositionSide, PriceType, TimeInForce,
    },
    events::AccountState,
    identifiers::{AccountId, ClientOrderId, InstrumentId, Symbol, TradeId, Venue, VenueOrderId},
    instruments::InstrumentAny,
    reports::{FillReport, OrderStatusReport, PositionStatusReport},
    types::{AccountBalance, Currency, Money, Price, Quantity},
};
use rust_decimal::Decimal;
use serde::{Deserialize, Deserializer};
use ustr::Ustr;

use super::enums::AlpacaInstrumentType;
use crate::{
    common::consts::ALPACA_VENUE,
    http::models::{AlpacaAccount, AlpacaBar, AlpacaOrder, AlpacaPosition, AlpacaTrade},
    websocket::{
        enums::AlpacaWsChannel,
        messages::{AlpacaBarMsg, AlpacaQuoteMsg, AlpacaTradeMsg},
    },
};

// =============================================================================
// Deserialization Helpers
// =============================================================================

/// Deserializes an empty string into [`None`].
pub fn deserialize_empty_string_as_none<'de, D>(
    deserializer: D,
) -> Result<Option<String>, D::Error>
where
    D: Deserializer<'de>,
{
    let opt = Option::<String>::deserialize(deserializer)?;
    Ok(opt.filter(|s| !s.is_empty()))
}

/// Deserializes an empty string into [`None`] for Ustr.
pub fn deserialize_empty_ustr_as_none<'de, D>(deserializer: D) -> Result<Option<Ustr>, D::Error>
where
    D: Deserializer<'de>,
{
    let opt = Option::<String>::deserialize(deserializer)?;
    Ok(opt.filter(|s| !s.is_empty()).map(|s| Ustr::from(&s)))
}

// =============================================================================
// Type Conversions
// =============================================================================

/// Returns the Alpaca instrument type for the given Nautilus instrument.
pub fn alpaca_instrument_type(instrument: &InstrumentAny) -> Result<AlpacaInstrumentType> {
    match instrument {
        InstrumentAny::Equity(_) => Ok(AlpacaInstrumentType::Stock),
        InstrumentAny::CurrencyPair(_) => Ok(AlpacaInstrumentType::Crypto),
        _ => anyhow::bail!("Unsupported instrument type for Alpaca: {instrument:?}"),
    }
}

/// Parses an instrument ID from an Alpaca symbol.
pub fn parse_instrument_id(symbol: Ustr) -> InstrumentId {
    InstrumentId::new(Symbol::new(symbol), Venue::new(*ALPACA_VENUE))
}

/// Parses a client order ID, returning None if the string is not a valid UUID.
pub fn parse_client_order_id(value: &str) -> Option<ClientOrderId> {
    ClientOrderId::from_str(value).ok()
}

// =============================================================================
// Timestamp Parsing
// =============================================================================

/// Parses a millisecond timestamp into UnixNanos.
pub fn parse_millisecond_timestamp(timestamp_ms: u64) -> UnixNanos {
    UnixNanos::from(timestamp_ms * NANOSECONDS_IN_MILLISECOND)
}

/// Parses an RFC3339 timestamp string into UnixNanos.
pub fn parse_rfc3339_timestamp(timestamp: &str) -> Result<UnixNanos> {
    let dt = DateTime::parse_from_rfc3339(timestamp)
        .context("Failed to parse RFC3339 timestamp")?;
    Ok(UnixNanos::from(dt.timestamp_nanos_opt().unwrap() as u64))
}

// =============================================================================
// Price/Quantity Parsing
// =============================================================================

/// Parses a price string with the given precision.
pub fn parse_price(value: &str, precision: u8) -> Result<Price> {
    let decimal = Decimal::from_str(value).context("Failed to parse price")?;
    Ok(Price::from_raw(
        (decimal * Decimal::from(10_i64.pow(precision as u32)))
            .to_i64()
            .context("Price conversion overflow")?,
        precision,
    ))
}

/// Parses a quantity string with the given precision.
pub fn parse_quantity(value: &str, precision: u8) -> Result<Quantity> {
    let decimal = Decimal::from_str(value).context("Failed to parse quantity")?;
    Ok(Quantity::from_raw(
        (decimal * Decimal::from(10_i64.pow(precision as u32)))
            .to_u64()
            .context("Quantity conversion overflow")?,
        precision,
    ))
}

/// Parses a fee string into Money.
pub fn parse_fee(value: Option<&str>, currency: Currency) -> Result<Money> {
    match value {
        Some(v) => {
            let amount = Decimal::from_str(v).context("Failed to parse fee")?;
            Ok(Money::new(amount.abs().to_f64().unwrap_or(0.0), currency))
        }
        None => Ok(Money::new(0.0, currency)),
    }
}

// =============================================================================
// Market Data Parsing
// =============================================================================

/// Parses an Alpaca trade (from REST API) into a TradeTick.
pub fn parse_trade_tick(
    trade: &AlpacaTrade,
    instrument_id: InstrumentId,
    price_precision: u8,
    size_precision: u8,
    ts_init: UnixNanos,
) -> Result<TradeTick> {
    let price = parse_price(&trade.p.to_string(), price_precision)?;
    let size = Quantity::from_raw(trade.s, size_precision);
    let aggressor_side = AggressorSide::NoAggressor; // Alpaca doesn't provide this
    let trade_id = TradeId::new(&trade.i.to_string());
    let ts_event = parse_rfc3339_timestamp(&trade.t)?;

    Ok(TradeTick::new(
        instrument_id,
        price,
        size,
        aggressor_side,
        trade_id,
        ts_event,
        ts_init,
    ))
}

/// Parses an Alpaca bar (candlestick) into a Bar.
pub fn parse_candlestick(
    bar: &AlpacaBar,
    bar_type: BarType,
    ts_init: UnixNanos,
) -> Result<Bar> {
    let precision = bar_type.spec().price_precision();

    let open = parse_price(&bar.o.to_string(), precision)?;
    let high = parse_price(&bar.h.to_string(), precision)?;
    let low = parse_price(&bar.l.to_string(), precision)?;
    let close = parse_price(&bar.c.to_string(), precision)?;
    let volume = Quantity::from_raw(bar.v, 0);
    let ts_event = parse_rfc3339_timestamp(&bar.t)?;

    Ok(Bar::new(
        bar_type,
        open,
        high,
        low,
        close,
        volume,
        ts_event,
        ts_init,
    ))
}

/// Parses an Alpaca WebSocket trade message into a TradeTick.
pub fn parse_ws_trade_tick(
    msg: &AlpacaTradeMsg,
    instrument_id: InstrumentId,
    price_precision: u8,
    size_precision: u8,
    ts_init: UnixNanos,
) -> Result<TradeTick> {
    let price = parse_price(&msg.price, price_precision)?;
    let size = Quantity::from_raw(msg.size, size_precision);
    let aggressor_side = AggressorSide::NoAggressor;
    let trade_id = TradeId::new(&msg.id.to_string());
    let ts_event = parse_rfc3339_timestamp(&msg.timestamp)?;

    Ok(TradeTick::new(
        instrument_id,
        price,
        size,
        aggressor_side,
        trade_id,
        ts_event,
        ts_init,
    ))
}

/// Parses an Alpaca WebSocket quote message into a QuoteTick.
pub fn parse_ws_quote_tick(
    msg: &AlpacaQuoteMsg,
    instrument_id: InstrumentId,
    price_precision: u8,
    size_precision: u8,
    ts_init: UnixNanos,
) -> Result<QuoteTick> {
    let bid_price = parse_price(&msg.bid_price, price_precision)?;
    let ask_price = parse_price(&msg.ask_price, price_precision)?;
    let bid_size = Quantity::from_raw(msg.bid_size, size_precision);
    let ask_size = Quantity::from_raw(msg.ask_size, size_precision);
    let ts_event = parse_rfc3339_timestamp(&msg.timestamp)?;

    Ok(QuoteTick::new(
        instrument_id,
        bid_price,
        ask_price,
        bid_size,
        ask_size,
        ts_event,
        ts_init,
    ))
}

/// Parses an Alpaca WebSocket bar message into a Bar.
pub fn parse_ws_bar(
    msg: &AlpacaBarMsg,
    bar_type: BarType,
    ts_init: UnixNanos,
) -> Result<Bar> {
    let precision = bar_type.spec().price_precision();

    let open = parse_price(&msg.open, precision)?;
    let high = parse_price(&msg.high, precision)?;
    let low = parse_price(&msg.low, precision)?;
    let close = parse_price(&msg.close, precision)?;
    let volume = Quantity::from_raw(msg.volume, 0);
    let ts_event = parse_rfc3339_timestamp(&msg.timestamp)?;

    Ok(Bar::new(
        bar_type,
        open,
        high,
        low,
        close,
        volume,
        ts_event,
        ts_init,
    ))
}

// =============================================================================
// Account/Position/Order Parsing
// =============================================================================

/// Parses an Alpaca account into an AccountState.
pub fn parse_account_state(
    account: &AlpacaAccount,
    account_id: AccountId,
    ts_init: UnixNanos,
) -> Result<AccountState> {
    let base_currency = Currency::USD(); // Alpaca accounts are USD-based

    let cash = account.cash.to_f64().unwrap_or(0.0);
    let equity = account.equity.to_f64().unwrap_or(0.0);

    let balances = vec![AccountBalance::new(
        Money::new(cash, base_currency),
        Money::new(0.0, base_currency),
        Money::new(equity, base_currency),
    )];

    Ok(AccountState::new(
        account_id,
        AccountType::Cash,
        balances,
        vec![], // Margins not applicable for cash accounts
        false,  // Not reported
        UUID4::new(),
        ts_init,
        ts_init,
    ))
}

/// Parses an Alpaca order into an OrderStatusReport.
pub fn parse_order_status_report(
    order: &AlpacaOrder,
    account_id: AccountId,
    instrument_id: InstrumentId,
    ts_init: UnixNanos,
) -> Result<OrderStatusReport> {
    let venue_order_id = VenueOrderId::new(&order.id);
    let client_order_id = parse_client_order_id(&order.client_order_id)
        .unwrap_or_else(|| ClientOrderId::new(&order.client_order_id));

    let order_side = match order.side.as_str() {
        "buy" => OrderSide::Buy,
        "sell" => OrderSide::Sell,
        _ => anyhow::bail!("Unknown order side: {}", order.side),
    };

    let order_type = match order.order_type.as_str() {
        "market" => OrderType::Market,
        "limit" => OrderType::Limit,
        "stop" => OrderType::StopMarket,
        "stop_limit" => OrderType::StopLimit,
        "trailing_stop" => OrderType::TrailingStopMarket,
        _ => anyhow::bail!("Unknown order type: {}", order.order_type),
    };

    let time_in_force = match order.time_in_force.as_str() {
        "day" => TimeInForce::Day,
        "gtc" => TimeInForce::Gtc,
        "ioc" => TimeInForce::Ioc,
        "fok" => TimeInForce::Fok,
        _ => TimeInForce::Gtc,
    };

    let order_status = match order.status.as_str() {
        "new" | "accepted" | "pending_new" => OrderStatus::Accepted,
        "partially_filled" => OrderStatus::PartiallyFilled,
        "filled" => OrderStatus::Filled,
        "canceled" | "pending_cancel" => OrderStatus::Canceled,
        "rejected" => OrderStatus::Rejected,
        "expired" => OrderStatus::Expired,
        _ => OrderStatus::Accepted,
    };

    let price_precision = 2; // Default precision for stocks
    let size_precision = 0;

    let quantity = order
        .qty
        .map(|q| Quantity::from_raw(q.to_u64().unwrap_or(0), size_precision));
    let filled_qty = Quantity::from_raw(order.filled_qty.to_u64().unwrap_or(0), size_precision);
    let avg_px = order
        .filled_avg_price
        .as_ref()
        .and_then(|p| parse_price(&p.to_string(), price_precision).ok());

    let ts_accepted = parse_rfc3339_timestamp(&order.created_at)?;
    let ts_last = order
        .updated_at
        .as_ref()
        .and_then(|t| parse_rfc3339_timestamp(t).ok())
        .unwrap_or(ts_accepted);

    Ok(OrderStatusReport::new(
        account_id,
        instrument_id,
        Some(client_order_id),
        venue_order_id,
        order_side,
        order_type,
        time_in_force,
        order_status,
        quantity,
        filled_qty,
        ts_accepted,
        ts_last,
        ts_init,
        None, // report_id
        avg_px,
        None, // post_only
        false, // reduce_only
        false, // quote_quantity
        None, // cancel_reason
    ))
}

/// Parses an Alpaca position into a PositionStatusReport.
pub fn parse_position_status_report(
    position: &AlpacaPosition,
    account_id: AccountId,
    instrument_id: InstrumentId,
    ts_init: UnixNanos,
) -> Result<PositionStatusReport> {
    let qty_decimal = position.qty.abs();
    let signed_qty = position.qty.to_f64().unwrap_or(0.0);

    let position_side = if signed_qty > 0.0 {
        PositionSide::Long
    } else if signed_qty < 0.0 {
        PositionSide::Short
    } else {
        PositionSide::Flat
    };

    let quantity = Quantity::from_raw(qty_decimal.to_u64().unwrap_or(0), 0);

    Ok(PositionStatusReport::new(
        account_id,
        instrument_id,
        position_side,
        quantity,
        ts_init,
        ts_init,
        None, // report_id
        None, // venue_position_id
        None, // avg_px_open
    ))
}

/// Parses an Alpaca fill (from order data) into a FillReport.
pub fn parse_fill_report(
    order: &AlpacaOrder,
    account_id: AccountId,
    instrument_id: InstrumentId,
    ts_init: UnixNanos,
) -> Result<FillReport> {
    let venue_order_id = VenueOrderId::new(&order.id);
    let client_order_id = parse_client_order_id(&order.client_order_id)
        .unwrap_or_else(|| ClientOrderId::new(&order.client_order_id));

    let order_side = match order.side.as_str() {
        "buy" => OrderSide::Buy,
        "sell" => OrderSide::Sell,
        _ => anyhow::bail!("Unknown order side: {}", order.side),
    };

    let price_precision = 2;
    let last_qty = Quantity::from_raw(order.filled_qty.to_u64().unwrap_or(0), 0);
    let last_px = order
        .filled_avg_price
        .as_ref()
        .map(|p| parse_price(&p.to_string(), price_precision))
        .transpose()?
        .unwrap_or_else(|| Price::from_raw(0, price_precision));

    let commission = Money::new(0.0, Currency::USD()); // Alpaca doesn't provide commission in basic API
    let liquidity_side = LiquiditySide::NoLiquiditySide;

    let ts_event = order
        .filled_at
        .as_ref()
        .and_then(|t| parse_rfc3339_timestamp(t).ok())
        .unwrap_or(ts_init);

    Ok(FillReport::new(
        account_id,
        instrument_id,
        venue_order_id,
        TradeId::new(&order.id), // Use order ID as trade ID
        order_side,
        last_qty,
        last_px,
        commission,
        liquidity_side,
        Some(client_order_id),
        None, // venue_position_id
        ts_event,
        ts_init,
        None, // report_id
    ))
}

// =============================================================================
// Instrument Parsing (placeholder - implement when needed)
// =============================================================================

/// Parses instrument data - placeholder for future implementation.
pub fn parse_instrument_any(
    _symbol: &str,
    _ts_init: UnixNanos,
) -> Result<InstrumentAny> {
    anyhow::bail!("Instrument parsing not yet implemented for Alpaca")
}

// Placeholder functions for compatibility
pub fn parse_mark_price_update(
    _data: &str,
    _instrument_id: InstrumentId,
    _ts_init: UnixNanos,
) -> Result<()> {
    Ok(())
}

pub fn parse_index_price_update(
    _data: &str,
    _instrument_id: InstrumentId,
    _ts_init: UnixNanos,
) -> Result<()> {
    Ok(())
}

// =============================================================================
// Bar Specification Utilities
// =============================================================================

/// Converts a BarSpecification to an Alpaca WebSocket channel.
pub fn bar_spec_to_alpaca_channel(bar_spec: &BarSpecification) -> Result<AlpacaWsChannel> {
    match (bar_spec.aggregation(), bar_spec.step()) {
        (BarAggregation::Minute, 1) => Ok(AlpacaWsChannel::Bars),
        (BarAggregation::Day, 1) => Ok(AlpacaWsChannel::DailyBars),
        _ => anyhow::bail!("Unsupported bar specification for Alpaca: {bar_spec}"),
    }
}

/// Converts an Alpaca timeframe string to a BarSpecification.
pub fn alpaca_timeframe_to_bar_spec(timeframe: &str) -> Result<BarSpecification> {
    match timeframe {
        "1Min" => Ok(BarSpecification::new(
            1,
            BarAggregation::Minute,
            PriceType::Last,
        )),
        "1Day" => Ok(BarSpecification::new(
            1,
            BarAggregation::Day,
            PriceType::Last,
        )),
        _ => anyhow::bail!("Unsupported Alpaca timeframe: {timeframe}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_rfc3339_timestamp() {
        let timestamp = "2023-01-01T12:00:00Z";
        let result = parse_rfc3339_timestamp(timestamp).unwrap();
        assert!(result.as_u64() > 0);
    }

    #[test]
    fn test_parse_price() {
        let price = parse_price("150.50", 2).unwrap();
        assert_eq!(price.raw, 15050);
        assert_eq!(price.precision, 2);
    }

    #[test]
    fn test_parse_quantity() {
        let qty = parse_quantity("100.5", 1).unwrap();
        assert_eq!(qty.raw, 1005);
        assert_eq!(qty.precision, 1);
    }
}
