import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest

from custom.utils.load_catalog_data import get_catalog_data
from nautilus_trader.adapters.alpaca.utils import get_alpaca_key_and_secret
from nautilus_trader.model import TradeTick

"""
Alpaca exchange codes
"""

ALPACA_EXCHANGE_CODES = {
    "A": "NYSE American (AMEX)",
    "B": "NASDAQ OMX BX",
    "C": "National Stock Exchange",
    "D": "FINRA ADF",
    "E": "Market Independent",
    "H": "MIAX",
    "I": "International Securities Exchange",
    "J": "Cboe EDGA",
    "K": "Cboe EDGX",
    "L": "Long Term Stock Exchange",
    "M": "Chicago Stock Exchange",
    "N": "New York Stock Exchange",
    "P": "NYSE Arca",
    "Q": "NASDAQ OMX",
    "S": "NASDAQ Small Cap",
    "T": "NASDAQ Int",
    "U": "Members Exchange",
    "V": "IEX",
    "W": "CBOE",
    "X": "NASDAQ OMX PSX",
    "Y": "Cboe BYX",
    "Z": "Cboe BZX",
}

symbol = "AAPL"
start_dt_str = "2025-11-03"
end_dt_str = "2025-11-04"

api_key, api_secret = get_alpaca_key_and_secret(paper=True)

# Create the Alpaca historical data client
client = StockHistoricalDataClient(api_key, api_secret, raw_data=False)

# Create the request for tick (trade) data
request = StockTradesRequest(
    feed="sip",
    symbol_or_symbols=symbol,
    start=start_dt_str,
    end=end_dt_str,
    limit=1000,
)

# Fetch the tick data
trades = client.get_stock_trades(request)
df = trades.df
df = df.reset_index()

# Print first 20 lines with full terminal width
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)
print("\nAlpaca trade data (first 20 rows):")
print(df.head(20))
print()


data = get_catalog_data(symbol, f"{start_dt_str}T00:00:00Z", f"{end_dt_str}T00:00:00Z", data_cls=TradeTick)

# Convert TradeTick objects to DataFrame
data_records = []
for tick in data:
    data_records.append({
        'price': float(tick.price),
        'size': float(tick.size),
        'aggressor_side': str(tick.aggressor_side),
        'trade_id': str(tick.trade_id),
        'ts_event': tick.ts_event,
        'ts_init': tick.ts_init,
    })

data_df = pd.DataFrame(data_records)


# Set timestamp as index
data_df['ts_event'] = pd.to_datetime(data_df['ts_event'], unit='ns')

df["bento_event"] = data_df["ts_event"].head(len(df)).dt.tz_localize(tz="UTC")
df["diff"] = (df["bento_event"] - df["timestamp"]).dt.total_seconds() * 1e3
print()
