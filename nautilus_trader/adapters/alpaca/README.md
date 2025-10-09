# Alpaca Adapter

Python adapter for integrating Alpaca Markets with NautilusTrader.

## Overview

The Alpaca adapter provides integration with [Alpaca Markets](https://alpaca.markets/), a commission-free trading platform for stocks and cryptocurrencies.

## Components

### Configuration

- **AlpacaDataClientConfig**: Configuration for market data client
- **AlpacaExecClientConfig**: Configuration for execution client

### Execution Client

- **AlpacaExecutionClient**: Live execution client for order management

## Features

### ✅ Implemented

- **Execution Client**: Full order management and lifecycle
- **HTTP Client**: REST API integration for orders, positions, and account
- **WebSocket Client**: Real-time trade updates and order events
- **Order Operations**:
  - Submit orders (Market, Limit, Stop-Limit, Trailing Stop)
  - Cancel individual orders
  - Cancel all orders
  - Order type conversion (NautilusTrader ↔ Alpaca)
- **Account Management**:
  - Connect/disconnect lifecycle
  - Account state updates with balances and margins
  - Credential verification
- **Reporting**:
  - Order status reports (single & bulk)
  - Support for open-only and all orders queries
  - Proper event generation (submitted, accepted, canceled, rejected, filled)
- **Configuration**: Both data and execution client configs
- **Factories**: Client factory for easy instantiation
- **Testing**: Unit tests for parsing, enums, and configuration

### 🚧 In Development

- Fill report generation from trade history
- Position status report generation
- Data client for historical and streaming market data
- Order modification support

## Rust Core

The adapter includes a Rust core implementation (see `crates/adapters/alpaca/`) with:

- HTTP client for REST API
- WebSocket client for market data streaming
- Core data models and types
- Error handling
- Configuration management

## Usage

```python
from nautilus_trader.adapters.alpaca import AlpacaExecClientConfig, AlpacaExecutionClient

# Configure the execution client
config = AlpacaExecClientConfig(
    api_key="your_api_key",
    api_secret="your_api_secret",
    environment="paper",  # or "live"
)

# Create the execution client
client = AlpacaExecutionClient(
    loop=loop,
    msgbus=msgbus,
    cache=cache,
    clock=clock,
    config=config,
)
```

## Environment Variables

The adapter can source API credentials from environment variables:

- `ALPACA_API_KEY`: Your Alpaca API key
- `ALPACA_API_SECRET`: Your Alpaca API secret

## Configuration Options

### Data Client

- `environment`: Trading environment ("paper" or "live")
- `feed`: Market data feed ("iex" or "sip")
- `http_timeout`: Timeout for HTTP requests (seconds)
- `update_instruments_interval_mins`: Interval for reloading instruments

### Execution Client

- `environment`: Trading environment ("paper" or "live")
- `http_timeout`: Timeout for HTTP requests (seconds)
- `max_retries`: Maximum retry attempts for failed requests
- `retry_delay_initial_ms`: Initial delay between retries
- `retry_delay_max_ms`: Maximum delay between retries

## Development Status

This adapter is under active development. The core structure is complete, but many API integration points are marked with TODO comments and require implementation.

## References

- [Alpaca API Documentation](https://docs.alpaca.markets/)
- [Alpaca Trading API](https://docs.alpaca.markets/docs/trading-api)
- [Alpaca Market Data API](https://docs.alpaca.markets/docs/market-data-api)
