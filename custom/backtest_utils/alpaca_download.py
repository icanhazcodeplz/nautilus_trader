import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockTradesRequest, StockQuotesRequest

from custom.backtest_utils.load_catalog_data import BACKTESTING_CATALOG
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


def get_trades_and_save_to_catalog(symbol, start_dt_str, end_dt_str):
    request = StockTradesRequest(
        feed="sip",
        symbol_or_symbols=symbol,
        start=start_dt_str,
        end=end_dt_str,
        limit=None,
    )

    instrument_id = TestInstrumentProvider.equity(symbol=symbol, venue=ALPACA).id
    # Fetch the tick data
    trades = client.get_stock_trades(request)
    df = trades.df
    df = df.reset_index()
    print(f"Fetched {len(df)} trades")
    df = df[df["exchange"] != "D"]
    print(f"Drop Finra {len(df)} trades")

    # Convert each row in df into a TradeTick object and then save to the catalog `BACKTESTING_CATALOG`
    trade_ticks = []
    for i, row in df.iterrows():
        ts_event = dt_to_unix_nanos(row["timestamp"])
        ts_init = ts_event  # Use same timestamp for initialization

        size_ = int(row["size"])
        if size_ == 0:
            continue
        # price_from_str = Price.from_str(str(row["price"]))

        precision = 2
        price_float = float(row["price"])
        if price_float < 1:
            raise ValueError(f"Penny stocks not yet supported")
        trade_tick = TradeTick(
            instrument_id=instrument_id,
            price=Price(price_float, precision=precision),
            size=Quantity.from_str(str(size_)),
            aggressor_side=AggressorSide.NO_AGGRESSOR,  # Alpaca doesn't provide aggressor side
            trade_id=TradeId(str(i)),
            ts_event=ts_event,
            ts_init=ts_init,
        )
        trade_ticks.append(trade_tick)

    BACKTESTING_CATALOG.write_data(trade_ticks)


def get_quotes_and_save_to_catalog(symbol, start_dt_str, end_dt_str):
    """Download quote data from Alpaca and save as QuoteTicks to catalog."""
    request = StockQuotesRequest(
        feed="sip",
        symbol_or_symbols=symbol,
        start=start_dt_str,
        end=end_dt_str,
        limit=None,
    )

    instrument_id = TestInstrumentProvider.equity(symbol=symbol, venue=ALPACA).id
    start_at = pd.Timestamp.now()
    quotes = client.get_stock_quotes(request)
    print(f"Downloading took {(pd.Timestamp.now() - start_at).total_seconds()} seconds")
    df = quotes.df
    print(f"Fetched {len(df)} quotes")
    df = df.reset_index()

    # Convert each row in df into a QuoteTick object and then save to the catalog
    start_at = pd.Timestamp.now()
    quote_ticks = []
    for _, row in df.iterrows():
        # Convert timestamp to Unix nanoseconds
        ts_event = dt_to_unix_nanos(row["timestamp"])
        ts_init = ts_event  # Use same timestamp for initialization

        # Skip quotes with zero bid or ask sizes
        bid_size = int(row["bid_size"])
        ask_size = int(row["ask_size"])
        # if bid_size == 0 or ask_size == 0:
        #     continue

        # Create QuoteTick
        bid_price = row["bid_price"]
        ask_price = row["ask_price"]
        bid_price_Price = Price.from_str(str(bid_price))
        ask_price_Price = Price.from_str(str(ask_price))
        precision = max(ask_price_Price.precision, bid_price_Price.precision, 2)
        # if precision > 2:
        #     print(f"WARNING: precision {precision} is greater than 2, setting to 2")
        precision = 2
        quote_tick = QuoteTick(
            instrument_id=instrument_id,
            bid_price=Price(float(bid_price), precision=precision),
            ask_price=Price(float(ask_price), precision=precision),
            bid_size=Quantity.from_str(str(bid_size)),
            ask_size=Quantity.from_str(str(ask_size)),
            ts_event=ts_event,
            ts_init=ts_init,
        )
        quote_ticks.append(quote_tick)
    print(f"Conversion took {(pd.Timestamp.now() - start_at).total_seconds()} seconds")
    print("Saving to catalog")
    # Write to catalog
    start_at = pd.Timestamp.now()
    BACKTESTING_CATALOG.write_data(quote_ticks)
    print(f"Writing to Catalog took {(pd.Timestamp.now() - start_at).total_seconds()} seconds")


def prepare_alpaca_data(symbol, start_dt_str):
    end_dt_str = pd.Timestamp(start_dt_str) + pd.Timedelta(days=1)
    end_dt_str = end_dt_str.strftime("%Y-%m-%d")
    print(f"Getting Alpaca data for {symbol} from {start_dt_str} to {end_dt_str}")
    get_trades_and_save_to_catalog(symbol, start_dt_str, end_dt_str)
    get_quotes_and_save_to_catalog(symbol, start_dt_str, end_dt_str)


if __name__ == "__main__":
    """
       Candidates

       10/13 - ELAB after hours
       10/13 - AQMS after hours
       10/13 - NDRA
       10/13 - STI
       10/13 - PMAX
       10/13 - GWH
       10/10 - GWH
       10/10 - SGBX
       10/9 - YDDL after hours
       10/9 - LFS after hours
       10/9 - BJDX
       10/9 - TTRX (ipo 10/8)
       10/9 - BAOS
       10/8 - AMBO (after hours)
       10/8 - XBIO
       10/8 - ACXB
       10/8 - XTLB
       10/8 - BIAF
       10/7 - BJDX
       10/7 - CISS
       10/7 - GLTO
       10/6 - SPRB
       10/6 - CRML
       10/6 - SOPA
       10/2 - IVDA
       10/2 - CIGL
       10/1 - AKAN
       10/1 - PALI
       10/1 - LAC
       9/30 - LAC (after hours)
       9/30 - SPRC
       9/29 - POAI
       9/25 - SPRC
       9/25 - EVAX
       9/24 - TNFA
       9/24 - SHFS
       9/23 - SHFS (after hours)
       9/23 - FLD
       9/29 - MSS

       9/19 - ZOOZ
       9/19 - AGMH
       
       pulled already
       10/13 - CHNR
       11/13 - SGBX
       10/14 - JDZG
       10/14 - GWAV
       10/14 - NVA
       12/01 - CNCK
       """

    symbol = "CNCK".upper()
    start_dt_str = "2025-12-01"

    prepare_alpaca_data(symbol, start_dt_str)

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
            symbol, f"{start_dt_str}T00:00:00Z", f"{end_dt_str}T00:00:00Z", data_cls=data_cls, venue=ALPACA
        )
        b_data = get_catalog_data(
            symbol, f"{start_dt_str}T00:00:00Z", f"{end_dt_str}T00:00:00Z", data_cls=data_cls, venue=DATABENTO
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
