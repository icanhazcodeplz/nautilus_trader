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

//! Functions translating raw Alpaca WebSocket frames into Nautilus data types.

use anyhow::Result;
use nautilus_core::nanos::UnixNanos;
use nautilus_model::{
    data::{Bar, BarType, Data, QuoteTick, TradeTick},
    identifiers::{AccountId, InstrumentId},
    reports::{FillReport, OrderStatusReport},
};

use super::messages::{
    AlpacaBarMsg, AlpacaOrderUpdateMsg, AlpacaQuoteMsg, AlpacaTradeMsg, AlpacaWebSocketEvent,
    ExecutionReport, NautilusWsMessage,
};
use crate::common::{
    models::ALPACAInstrument,
    parse::{parse_fill_report, parse_order_status_report, parse_ws_bar, parse_ws_quote_tick, parse_ws_trade_tick},
};

// =============================================================================
// Market Data Parsing
// =============================================================================

/// Parses an Alpaca trade WebSocket message.
pub fn parse_trade_msg(
    msg: &AlpacaTradeMsg,
    instrument_id: InstrumentId,
    price_precision: u8,
    size_precision: u8,
    ts_init: UnixNanos,
) -> Result<TradeTick> {
    parse_ws_trade_tick(msg, instrument_id, price_precision, size_precision, ts_init)
}

/// Parses a vector of Alpaca trade messages.
pub fn parse_trade_msg_vec(
    msgs: Vec<AlpacaTradeMsg>,
    instrument_id: InstrumentId,
    price_precision: u8,
    size_precision: u8,
    ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    msgs.into_iter()
        .map(|msg| {
            parse_trade_msg(&msg, instrument_id, price_precision, size_precision, ts_init)
                .map(Data::from)
        })
        .collect()
}

/// Parses an Alpaca quote WebSocket message.
pub fn parse_quote_msg(
    msg: &AlpacaQuoteMsg,
    instrument_id: InstrumentId,
    price_precision: u8,
    size_precision: u8,
    ts_init: UnixNanos,
) -> Result<QuoteTick> {
    parse_ws_quote_tick(msg, instrument_id, price_precision, size_precision, ts_init)
}

/// Parses a vector of Alpaca quote messages.
pub fn parse_quote_msg_vec(
    msgs: Vec<AlpacaQuoteMsg>,
    instrument_id: InstrumentId,
    price_precision: u8,
    size_precision: u8,
    ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    msgs.into_iter()
        .map(|msg| {
            parse_quote_msg(&msg, instrument_id, price_precision, size_precision, ts_init)
                .map(Data::from)
        })
        .collect()
}

/// Parses an Alpaca bar (candlestick) WebSocket message.
pub fn parse_bar_msg(
    msg: &AlpacaBarMsg,
    bar_type: BarType,
    ts_init: UnixNanos,
) -> Result<Bar> {
    parse_ws_bar(msg, bar_type, ts_init)
}

/// Parses a vector of Alpaca bar messages.
pub fn parse_bar_msg_vec(
    msgs: Vec<AlpacaBarMsg>,
    bar_type: BarType,
    ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    msgs.into_iter()
        .map(|msg| parse_bar_msg(&msg, bar_type, ts_init).map(Data::from))
        .collect()
}

// =============================================================================
// Order/Execution Parsing (Placeholders)
// =============================================================================

/// Parses order messages (placeholder - implement when needed).
pub fn parse_order_msg_vec(
    _msgs: Vec<AlpacaOrderUpdateMsg>,
    _account_id: AccountId,
    _instrument_id: InstrumentId,
    _ts_init: UnixNanos,
) -> Result<Vec<ExecutionReport>> {
    Ok(vec![])
}

/// Parses a fill report (placeholder - implement when needed).
pub fn parse_fill_report_from_ws(
    _msg: &AlpacaOrderUpdateMsg,
    _account_id: AccountId,
    _instrument_id: InstrumentId,
    _ts_init: UnixNanos,
) -> Result<FillReport> {
    anyhow::bail!("WebSocket fill parsing not yet implemented")
}

/// Parses an order status report (placeholder - implement when needed).
pub fn parse_order_status_report_from_ws(
    _msg: &AlpacaOrderUpdateMsg,
    _account_id: AccountId,
    _instrument_id: InstrumentId,
    _ts_init: UnixNanos,
) -> Result<OrderStatusReport> {
    anyhow::bail!("WebSocket order status parsing not yet implemented")
}

// =============================================================================
// WebSocket Message Dispatcher
// =============================================================================

/// Parses WebSocket message data into Nautilus messages.
pub fn parse_ws_message_data(
    events: Vec<AlpacaWebSocketEvent>,
    _instrument: &Option<ALPACAInstrument>,
    _account_id: Option<AccountId>,
    ts_init: UnixNanos,
) -> Result<Option<NautilusWsMessage>> {
    let mut all_data = Vec::new();

    for event in events {
        match event {
            AlpacaWebSocketEvent::Trade(msg) => {
                // For now, just acknowledge the trade was received
                // Full implementation would parse it into TradeTick
                tracing::debug!("Received trade for {}: ${}", msg.symbol, msg.price);
            }
            AlpacaWebSocketEvent::Quote(msg) => {
                tracing::debug!(
                    "Received quote for {}: bid ${} / ask ${}",
                    msg.symbol,
                    msg.bid_price,
                    msg.ask_price
                );
            }
            AlpacaWebSocketEvent::Bar(msg) => {
                tracing::debug!("Received bar for {}: close ${}", msg.symbol, msg.close);
            }
            AlpacaWebSocketEvent::Success { msg } => {
                tracing::info!("Alpaca WebSocket: {}", msg);
            }
            AlpacaWebSocketEvent::Subscription { trades, quotes, bars } => {
                tracing::info!(
                    "Subscribed to trades: {:?}, quotes: {:?}, bars: {:?}",
                    trades,
                    quotes,
                    bars
                );
            }
            AlpacaWebSocketEvent::Error { code, msg } => {
                tracing::error!("Alpaca WebSocket error {}: {}", code, msg);
                return Ok(Some(NautilusWsMessage::Error(
                    super::messages::ALPACAWebSocketError {
                        code: code.to_string(),
                        message: msg,
                        timestamp: ts_init.as_u64(),
                        conn_id: None,
                    },
                )));
            }
            AlpacaWebSocketEvent::Status(msg) => {
                tracing::info!(
                    "Status for {}: {} - {}",
                    msg.symbol,
                    msg.status_code,
                    msg.status_msg
                );
            }
            AlpacaWebSocketEvent::Luld(msg) => {
                tracing::info!(
                    "LULD for {}: up ${} / down ${}",
                    msg.symbol,
                    msg.limit_up_price,
                    msg.limit_down_price
                );
            }
            AlpacaWebSocketEvent::TradingUpdate(msg) => {
                tracing::info!("Trading update: {} - {:?}", msg.event, msg.order.id);
            }
        }
    }

    if all_data.is_empty() {
        Ok(None)
    } else {
        Ok(Some(NautilusWsMessage::Data(all_data)))
    }
}

// =============================================================================
// Placeholder Functions for Compatibility
// =============================================================================

/// Placeholder for book message parsing (not applicable to basic Alpaca).
pub fn parse_book_msg_vec(
    _data: Vec<serde_json::Value>,
    _instrument_id: &InstrumentId,
    _price_precision: u8,
    _size_precision: u8,
    _ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    Ok(vec![])
}

/// Placeholder for ticker message parsing.
pub fn parse_ticker_msg_vec(
    _data: serde_json::Value,
    _instrument_id: &InstrumentId,
    _price_precision: u8,
    _size_precision: u8,
    _ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    Ok(vec![])
}

/// Placeholder for mark price message parsing (not applicable to stocks).
pub fn parse_mark_price_msg_vec(
    _data: serde_json::Value,
    _instrument_id: &InstrumentId,
    _ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    Ok(vec![])
}

/// Placeholder for index price message parsing (not applicable to stocks).
pub fn parse_index_price_msg_vec(
    _data: serde_json::Value,
    _instrument_id: &InstrumentId,
    _ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    Ok(vec![])
}

/// Placeholder for funding rate message parsing (not applicable to stocks).
pub fn parse_funding_rate_msg_vec(
    _data: serde_json::Value,
    _instrument_id: &InstrumentId,
    _ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    Ok(vec![])
}

/// Placeholder for candle message parsing.
pub fn parse_candle_msg_vec(
    _data: serde_json::Value,
    _bar_type: BarType,
    _ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    Ok(vec![])
}

/// Placeholder for book10 message parsing (not applicable to basic Alpaca).
pub fn parse_book10_msg_vec(
    _data: serde_json::Value,
    _instrument_id: &InstrumentId,
    _price_precision: u8,
    _size_precision: u8,
    _ts_init: UnixNanos,
) -> Result<Vec<Data>> {
    Ok(vec![])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_ws_message_empty() {
        let events = vec![];
        let result = parse_ws_message_data(events, &None, None, UnixNanos::default()).unwrap();
        assert!(result.is_none());
    }
}
