# nautilus-alpaca

Alpaca adapter for NautilusTrader, providing both market data and execution capabilities.

## Overview

The `nautilus-alpaca` crate provides client bindings (HTTP & WebSocket), data models,
and helper utilities that wrap the official **Alpaca Trading API**.

The official Alpaca API reference can be found at <https://docs.alpaca.markets/>.

## Features

- Real-time market data streaming (stocks, crypto, options)
- Historical market data (bars, trades, quotes)
- Order execution (market, limit, stop limit orders)
- Paper and live trading support
- Multiple asset classes: stocks, crypto, options
- WebSocket streaming for both market data and trading updates

## API Capabilities

### Market Data
- Real-time trades, quotes, and bars
- Historical data access
- Multiple data feeds (IEX, SIP for stocks)
- Crypto and options market data

### Trading
- Order management (create, cancel, modify)
- Multiple order types: market, limit, stop limit
- Time in force options: DAY, GTC, IOC, FOK
- Real-time trade updates via WebSocket
- Paper trading environment for testing

## Authentication

Alpaca uses header-based authentication:
- `APCA-API-KEY-ID`: Your API key ID
- `APCA-API-SECRET-KEY`: Your API secret key

You can obtain API keys from the [Alpaca dashboard](https://app.alpaca.markets/).

## Environment

- **Paper Trading**: `https://paper-api.alpaca.markets`
- **Live Trading**: `https://api.alpaca.markets`
- **Market Data**: `https://data.alpaca.markets`

## License

Licensed under the GNU Lesser General Public License Version 3.0.
