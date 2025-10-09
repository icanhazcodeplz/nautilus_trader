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

### Supported

- Basic execution client structure
- Configuration management
- Support for both paper and live trading environments
- Account type configuration (CASH for stocks)

### In Development

The following features have TODO markers and require implementation:

- HTTP API integration for order submission/cancellation
- WebSocket streaming for order updates
- Order type conversion (NautilusTrader ↔ Alpaca)
- Order status report generation
- Fill report generation
- Position status report generation
- Account state management

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
