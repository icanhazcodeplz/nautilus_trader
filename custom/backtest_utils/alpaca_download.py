import json

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest, StockQuotesRequest

from custom.backtest_utils.load_catalog_data import BACKTESTING_CATALOG
from custom.catalog_options import write_json_single_line_entries
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.adapters.alpaca.utils import get_alpaca_key_and_secret
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model import TradeTick
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider


pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)
pd.set_option("display.max_colwidth", None)


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

api_key, api_secret = get_alpaca_key_and_secret(paper=True)
client = StockHistoricalDataClient(api_key, api_secret, raw_data=False)


def get_trades_and_save_to_catalog_if_needed(symbol, start_dt_str, end_dt_str, force=False):
    """Download trades from Alpaca and save to catalog."""
    instrument_id = TestInstrumentProvider.equity(symbol=symbol, venue=ALPACA).id

    # Check if trades exist for the range in BACKTESTING_CATALOG
    if not force:
        existing_trades = BACKTESTING_CATALOG.query(
            data_cls=TradeTick,
            identifiers=[str(instrument_id)],
            start=start_dt_str,
            end=end_dt_str,
        )
        if existing_trades:
            print(f"Trades exist for {symbol} from {start_dt_str} to {end_dt_str}, skipping")
            return

    request = StockTradesRequest(
        feed="sip", symbol_or_symbols=symbol, start=start_dt_str, end=end_dt_str
    )

    # Fetch the tick data
    alpaca_response = client.get_stock_trades(request)
    trades = alpaca_response.data[symbol]

    any_penny = any(trade.price < 1 for trade in trades)
    if any_penny:
        raise ValueError(f"Penny stocks not yet supported")

    trade_ticks = [
        TradeTick(
            instrument_id=instrument_id,
            price=Price(trade.price, precision=2),
            size=Quantity.from_int(int(trade.size)),
            aggressor_side=AggressorSide.NO_AGGRESSOR,
            trade_id=TradeId(str(i)),
            ts_event=(ts := dt_to_unix_nanos(trade.timestamp)),  # Look at me! I'm a WAAAAAAALLLrus
            ts_init=ts,
        )
        for i, trade in enumerate(trades)
        if trade.exchange != "D" and trade.size != 0
    ]

    BACKTESTING_CATALOG.write_data(trade_ticks)


def get_quotes_and_save_to_catalog(symbol, start_dt_str, end_dt_str, force=False):
    """Download quote data from Alpaca and save as QuoteTicks to catalog."""
    instrument_id = TestInstrumentProvider.equity(symbol=symbol, venue=ALPACA).id

    # Check if quotes exist for the range in BACKTESTING_CATALOG
    if not force:
        existing_quotes = BACKTESTING_CATALOG.query(
            data_cls=QuoteTick,
            identifiers=[str(instrument_id)],
            start=start_dt_str,
            end=end_dt_str,
        )
        if existing_quotes:
            print(f"Quotes exist for {symbol} from {start_dt_str} to {end_dt_str}, skipping")
            return

    request = StockQuotesRequest(
        feed="sip", symbol_or_symbols=symbol, start=start_dt_str, end=end_dt_str
    )

    alpaca_response = client.get_stock_quotes(request)
    quotes = alpaca_response.data[symbol]

    quote_ticks = [
        QuoteTick(
            instrument_id=instrument_id,
            bid_price=Price(quote.bid_price, precision=2),
            ask_price=Price(quote.ask_price, precision=2),
            bid_size=Quantity.from_int(int(quote.bid_size)),
            ask_size=Quantity.from_int(int(quote.ask_size)),
            ts_event=(ts := dt_to_unix_nanos(quote.timestamp)),
            ts_init=ts,
        )
        for quote in quotes
    ]

    BACKTESTING_CATALOG.write_data(quote_ticks)


def prepare_alpaca_data(symbol, start_dt):
    end_dt = start_dt.replace(hour=0, minute=0) + pd.Timedelta(days=1)
    start_dt_str = start_dt.isoformat()
    end_dt_str = end_dt.isoformat()
    get_trades_and_save_to_catalog_if_needed(symbol, start_dt_str, end_dt_str)
    get_quotes_and_save_to_catalog(symbol, start_dt_str, end_dt_str)


if __name__ == "__main__":
    import os
    from pathlib import Path

    top_gainers_dir = "/Users/brent/code/trading_dev/data/top_gainers"
    parquet_files = sorted([f for f in os.listdir(top_gainers_dir) if f.endswith(".parquet")])[-4:]

    price_min = 2.0
    price_max = 20.0
    vol_min = 100_000
    check_hours = [5, 7, 10, 13, 17]
    must_be_in_top = 5
    min_perc_gain = 40

    for parquet_file in parquet_files:
        top_gainers_df = pd.read_parquet(Path(top_gainers_dir) / parquet_file)
        print(parquet_file)
        for hr in check_hours:
            start_dt = top_gainers_df.timestamp[0].replace(hour=hr, minute=0)
            candidates = top_gainers_df[top_gainers_df["timestamp"] == start_dt].head(
                must_be_in_top
            )
            candidates = candidates[
                (candidates["price"] > price_min)
                & (candidates["price"] < price_max)
                & (candidates["volume"] > vol_min)
                & (candidates["perc_gain"] > min_perc_gain)
            ]
            print(f"{candidates}\n")
            for symbol in candidates["symbol"].unique():
                data_start_dt = start_dt - pd.Timedelta(hours=1)
                prepare_alpaca_data(symbol, data_start_dt)

                # Add entry to catalog_options.json
                catalog_json_path = "/Users/brent/code/nautilus_trader/custom/catalog_options.json"
                with open(catalog_json_path) as f:
                    catalog_options = json.load(f)

                data_end_dt = data_start_dt.replace(hour=0, minute=0) + pd.Timedelta(days=1)
                date_str = data_start_dt.strftime("%m%d")
                key = f"{date_str}_{symbol.lower()}"
                # Check if the key is already
                if key in catalog_options.keys():
                    print(f"{key} already in catalog_options. Skipping.")

                catalog_options[key] = {
                    "symbol": symbol.upper(),
                    "start": data_start_dt.strftime("%Y-%m-%d %H:%M"),
                    "end": data_end_dt.strftime("%Y-%m-%d %H:%M"),
                    "notes": "",
                }

                write_json_single_line_entries(catalog_options, catalog_json_path)

    if False:
        end_dt_str = pd.Timestamp(start_dt_str) + pd.Timedelta(days=1)
        end_dt_str = end_dt_str.strftime("%Y-%m-%d")

        # COMPARE DATABENTO AND ALPACAA
        # Convert TradeTick objects to DataFrame
        def trade_ticks_to_df(data):
            data_records = []
            for tick in data:
                data_records.append(
                    {
                        "price": float(tick.price),
                        "size": float(tick.size),
                        "aggressor_side": str(tick.aggressor_side),
                        "trade_id": str(tick.trade_id),
                        "ts_event": pd.Timestamp(tick.ts_event, unit="ns"),
                        "ts_init": pd.Timestamp(tick.ts_init, unit="ns"),
                    }
                )

            return pd.DataFrame(data_records)

        # Convert TradeTick objects to DataFrame
        def quote_ticks_to_df(data):
            data_records = []
            for tick in data:
                bid_size = int(tick.bid_size)
                ask_size = int(tick.ask_size)
                if bid_size == 0 and ask_size == 0:
                    continue
                data_records.append(
                    {
                        "bid_size": bid_size,
                        "bid_price": float(tick.bid_price),
                        "ask_size": ask_size,
                        "ask_price": float(tick.ask_price),
                        "ts_event": pd.Timestamp(tick.ts_event, unit="ns"),
                        "ts_init": pd.Timestamp(tick.ts_init, unit="ns"),
                    }
                )

            return pd.DataFrame(data_records)

        # Print first 20 lines with full terminal width
        data_cls = TradeTick
        # data_cls = QuoteTick
        a_data = get_catalog_data(
            symbol,
            f"{start_dt_str}T00:00:00Z",
            f"{end_dt_str}T00:00:00Z",
            data_cls=data_cls,
            venue=ALPACA,
        )
        b_data = get_catalog_data(
            symbol,
            f"{start_dt_str}T00:00:00Z",
            f"{end_dt_str}T00:00:00Z",
            data_cls=data_cls,
            venue=DATABENTO,
        )

        if data_cls == TradeTick:
            a_df = trade_ticks_to_df(a_data)
            b_df = trade_ticks_to_df(b_data)
        elif data_cls == QuoteTick:
            a_df = quote_ticks_to_df(a_data)
            b_df = quote_ticks_to_df(b_data)

        print()

        print(a_df.head(20))
        print()
        print(b_df.head(10))
        print()
        print()
        print(a_df.tail(20))
        print()
        print(b_df.tail(10))
        # Set timestamp as index

        print()
