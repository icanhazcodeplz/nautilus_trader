# Alpaca Adapter

Python adapter for integrating Alpaca Markets with NautilusTrader.

## Overview

The Alpaca adapter provides integration with [Alpaca Markets](https://alpaca.markets/), a commission-free trading platform for stocks and cryptocurrencies.

## Components

### Configuration

- **AlpacaDataClientConfig**: Configuration for market data client
- **AlpacaExecClientConfig**: Configuration for execution client

### Providers

- **AlpacaInstrumentProvider**: Loads tradeable instruments from Alpaca
  - Fetches all US equities, crypto, and options
  - Converts to Nautilus `Equity` instruments
  - Supports fractionable shares
  - Handles margin requirements

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

### Basic Configuration

```python
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.adapters.alpaca import AlpacaExecClientConfig
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.config import TradingNodeConfig

# Configure the execution client
config = TradingNodeConfig(
    exec_clients={
        ALPACA: AlpacaExecClientConfig(
            environment="paper",  # or "live"
            # api_key and api_secret will be sourced from environment variables
        ),
    },
)
```

### Examples

See the `examples/live/alpaca/` directory for complete examples:

- **`alpaca_exec_tester.py`**: Full execution testing with the ExecTester strategy
- **`alpaca_simple_order.py`**: Simple order submission and cancellation example

Run an example:
```bash
# Set your credentials
export ALPACA_API_KEY="your_api_key"
export ALPACA_API_SECRET="your_api_secret"

# Run the execution tester
python examples/live/alpaca/alpaca_exec_tester.py
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
