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

//! WebSocket client example for the Alpaca adapter.
//!
//! This example demonstrates how to connect to Alpaca's market data WebSocket
//! and subscribe to trades, quotes, and bars for specific symbols.
//!
//! # Usage
//!
//! Set your Alpaca API credentials as environment variables:
//! ```bash
//! export ALPACA_API_KEY="your_api_key"
//! export ALPACA_API_SECRET="your_api_secret"
//! ```
//!
//! Then run the example:
//! ```bash
//! cargo run -p nautilus-alpaca --bin alpaca-ws-data
//! ```

use std::env;

use futures_util::StreamExt;
use nautilus_alpaca::websocket::{AlpacaWebSocketClient, AlpacaWebSocketMessage};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Initialize logging
    nautilus_common::logging::ensure_logging_initialized();

    println!("Alpaca WebSocket Client Example");
    println!("=================================\n");

    // Get credentials from environment
    let api_key = env::var("ALPACA_API_KEY").ok();
    let api_secret = env::var("ALPACA_API_SECRET").ok();

    // WebSocket URL for market data (IEX feed)
    let ws_url = "wss://stream.data.alpaca.markets/v2/iex".to_string();

    // Create client
    let mut client = if let (Some(key), Some(secret)) = (api_key, api_secret) {
        println!("Creating authenticated client...");
        AlpacaWebSocketClient::new_authenticated(ws_url, key, secret, None)
    } else {
        println!("No credentials found, creating unauthenticated client (limited access)...");
        AlpacaWebSocketClient::new_market_data(ws_url, None)
    };

    // Connect to WebSocket
    println!("Connecting to Alpaca WebSocket...");
    client.connect().await?;
    println!("Connected successfully!\n");

    // Subscribe to trades and quotes for AAPL
    println!("Subscribing to AAPL trades and quotes...");
    client
        .subscribe(
            Some(vec!["AAPL".to_string()]),
            Some(vec!["AAPL".to_string()]),
            None,
        )
        .await?;

    // Get message stream
    let stream = client.stream();

    println!("\nReceiving messages (press Ctrl+C to stop):\n");

    // Pin the stream for polling
    tokio::pin!(stream);

    // Process messages
    let mut msg_count = 0;
    while let Some(message) = stream.next().await {
        msg_count += 1;

        match message {
            AlpacaWebSocketMessage::Connected(resp) => {
                println!("✓ Connected: {}", resp.msg);
            }
            AlpacaWebSocketMessage::Authenticated => {
                println!("✓ Authenticated successfully");
            }
            AlpacaWebSocketMessage::Subscription(sub) => {
                println!("✓ Subscription: {}", sub.msg);
            }
            AlpacaWebSocketMessage::Trade(trade) => {
                println!(
                    "→ TRADE: {} @ ${} (size: {})",
                    trade.symbol, trade.price, trade.size
                );
            }
            AlpacaWebSocketMessage::Quote(quote) => {
                println!(
                    "→ QUOTE: {} bid: ${} ({}) ask: ${} ({})",
                    quote.symbol, quote.bid_price, quote.bid_size, quote.ask_price, quote.ask_size
                );
            }
            AlpacaWebSocketMessage::Bar(bar) => {
                println!(
                    "→ BAR: {} O: ${} H: ${} L: ${} C: ${} V: {}",
                    bar.symbol, bar.open, bar.high, bar.low, bar.close, bar.volume
                );
            }
            AlpacaWebSocketMessage::Error(err) => {
                eprintln!("✗ Error: {} (code: {})", err.msg, err.code);
            }
            AlpacaWebSocketMessage::Pong => {
                log::debug!("Received pong");
            }
            AlpacaWebSocketMessage::Raw(value) => {
                log::debug!("Raw message: {:?}", value);
            }
            _ => {}
        }

        // Stop after 50 messages for demo purposes
        if msg_count >= 50 {
            println!("\nReceived {} messages, stopping...", msg_count);
            break;
        }
    }

    // Disconnect
    client.close().await?;
    println!("\nDisconnected successfully!");

    Ok(())
}
