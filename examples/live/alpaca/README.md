# Alpaca Examples

This directory contains example scripts for using the Alpaca adapter with NautilusTrader.

## Prerequisites

1. **Alpaca Account**: Sign up for a free Alpaca paper trading account at [alpaca.markets](https://alpaca.markets)

2. **API Credentials**: Get your API key and secret from the Alpaca dashboard

3. **Environment Variables**: Set your credentials as environment variables:
   ```bash
   export ALPACA_API_KEY="your_api_key_here"
   export ALPACA_API_SECRET="your_api_secret_here"
   ```

4. **NautilusTrader**: Ensure NautilusTrader is installed with the Alpaca adapter

## Examples

### `alpaca_exec_tester.py`

An execution testing script that demonstrates:
- Connecting to Alpaca paper trading account
- Placing limit orders at an offset from the market
- Managing order lifecycle (submit, cancel, fills)
- Account state reconciliation

**Configuration Options:**

```python
# Instrument to trade
instrument_id = InstrumentId.from_str("AAPL.ALPACA")

# Order offset from market (in ticks)
offset_ticks = 5

# Trade size in shares
trade_size = Decimal("1")

# Environment: "paper" or "live"
environment = "paper"

# Dry run mode (prevents actual trading)
dry_run = True
```

**Running the Example:**

```bash
# From the repository root
python examples/live/alpaca/alpaca_exec_tester.py
```

**What to Expect:**

1. The script connects to your Alpaca paper trading account
2. Verifies your credentials and account status
3. Subscribes to trade updates via WebSocket
4. The ExecTester strategy will:
   - Monitor market conditions (simulated for Alpaca)
   - Place limit orders at the configured offset
   - Automatically manage order lifecycle
   - Log all order events

**Safety Features:**

- `dry_run=True` by default - no actual orders are placed
- Uses paper trading environment by default
- All operations are logged with detailed information
- Graceful shutdown with CTRL+C

## Important Notes

### Alpaca-Specific Behavior

1. **Commission-Free**: Alpaca offers commission-free trading for US stocks
2. **Cash Account**: The adapter uses a CASH account type for stock trading
3. **Market Hours**: Alpaca only allows trading during market hours (9:30 AM - 4:00 PM ET)
4. **Paper Trading**: Always test with paper trading before using live credentials
5. **Rate Limits**: Alpaca has rate limits on API requests (200 requests per minute)

### Supported Order Types

- Market orders
- Limit orders
- Stop-Limit orders
- Trailing Stop orders

### Supported Time in Force

- DAY: Valid for the trading day
- GTC: Good till canceled
- IOC: Immediate or cancel
- FOK: Fill or kill

### WebSocket Updates

The adapter automatically receives real-time updates for:
- Order acceptance
- Order fills (full and partial)
- Order cancellations
- Order rejections

## Troubleshooting

### "Authentication error"
- Verify your API credentials are correct
- Check that environment variables are set: `echo $ALPACA_API_KEY`
- Ensure your API keys are for the correct environment (paper vs live)

### "Instrument not found"
- Verify the symbol exists on Alpaca
- Use the format: `SYMBOL.ALPACA` (e.g., "AAPL.ALPACA")
- Check that the symbol is tradeable (not halted or delisted)

### "Order rejected"
- Ensure you have sufficient buying power
- Check that markets are open (or use extended hours if enabled)
- Verify order parameters (quantity, price) are valid

### "WebSocket connection failed"
- Check your internet connection
- The adapter will fall back to HTTP polling if WebSocket fails
- Check Alpaca status page for any outages

## Additional Resources

- [Alpaca Documentation](https://docs.alpaca.markets/)
- [Alpaca Trading API](https://docs.alpaca.markets/docs/trading-api)
- [NautilusTrader Documentation](https://nautilustrader.io/)
- [NautilusTrader Alpaca Adapter](../../nautilus_trader/adapters/alpaca/)

## Support

For issues specific to:
- **Alpaca API**: Contact Alpaca support
- **NautilusTrader**: Open an issue on [GitHub](https://github.com/nautechsystems/nautilus_trader)
- **This Adapter**: Open an issue on the NautilusTrader repository with the "adapter" label
