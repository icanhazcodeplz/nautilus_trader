import json
import multiprocessing
import threading
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockQuotesRequest
from alpaca.data.requests import StockTradesRequest

from custom.artifacts import LiveArtifactsIO
from custom.backtest_utils.load_catalog_data import BACKTESTING_CATALOG
from custom.catalog_options import write_json_single_line_entries
from custom.utils.paths import repo_path
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.adapters.alpaca.constants import has_excluded_condition
from nautilus_trader.adapters.alpaca.utils import get_alpaca_key_and_secret
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model import TradeTick
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.test_kit.providers import TestInstrumentProvider


pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)
pd.set_option("display.max_colwidth", None)

NY_TZ = "America/New_York"


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

# Alpaca pages through a request one cursor at a time, and trades pages can take seconds each, so a day is
# fetched as `SLICE_MINUTES` windows in parallel. Worker processes each run a thread pool of fetches;
# `DAYS_IN_FLIGHT` bounds how many days per process are held in memory (~1GB each) before being written.
N_PROCESSES = 2
N_THREADS_PER_PROCESS = 8
SLICE_MINUTES = 30
DAYS_IN_FLIGHT = 4

_thread_local = threading.local()


def _client():
    """Return this thread's client, since its `requests.Session` shouldn't be shared across threads."""
    if not hasattr(_thread_local, "client"):
        _thread_local.client = StockHistoricalDataClient(api_key, api_secret, raw_data=False)
    return _thread_local.client


def _price_precision(price):
    if price < 1.0:
        return 4
    return 2


def _download_range(day_in_question: pd.Timestamp, start_time=None, end_time=None):
    """
    Return the (start, end) ISO strings to download for `day_in_question`.

    `start_time` and `end_time` are New York wall-clock "HH:MM[:SS]" strings, so the window follows
    EST/EDT. Either one left as `None` falls back to that day's New York midnight.
    """
    date_str = day_in_question.strftime("%Y-%m-%d")
    day_start = pd.Timestamp(date_str, tz=NY_TZ)
    start = day_start if start_time is None else pd.Timestamp(f"{date_str} {start_time}", tz=NY_TZ)
    end = day_start + pd.Timedelta(days=1) if end_time is None else pd.Timestamp(f"{date_str} {end_time}", tz=NY_TZ)
    assert start < end, f"start_time {start_time} must be before end_time {end_time}"
    return start.isoformat(), end.isoformat()


def _data_exists(instrument_id, day_in_question, data_type):
    intervals = BACKTESTING_CATALOG.get_intervals(data_type, str(instrument_id))
    intervals_days = {pd.Timestamp(tp[0]).date() for tp in intervals}
    if day_in_question.date() in intervals_days:
        print(f"{data_type.__name__}s exist for {instrument_id} on {day_in_question}")
        return True
    return False


def _delete_range(instrument_id, start_dt_str, end_dt_str, data_type):
    print(f"Deleting {instrument_id} from {start_dt_str} to {end_dt_str}")
    BACKTESTING_CATALOG.delete_data_range(
        data_cls=data_type, identifier=str(instrument_id), start=start_dt_str, end=end_dt_str
    )


def _slices(start_dt_str, end_dt_str):
    """Split the download range into `SLICE_MINUTES` windows, returned as (start, end, is_last) tuples."""
    bounds = list(pd.date_range(start_dt_str, end_dt_str, freq=f"{SLICE_MINUTES}min"))
    if bounds[-1] != pd.Timestamp(end_dt_str):
        bounds.append(pd.Timestamp(end_dt_str))
    return [(s, e, e == bounds[-1]) for s, e in pairwise(bounds)]


def _fetch_slice(data_type, symbol, start, end, is_last):
    """Fetch one window of trades or quotes as alpaca models."""
    client = _client()
    if data_type is TradeTick:
        get, request_cls = client.get_stock_trades, StockTradesRequest
    else:
        get, request_cls = client.get_stock_quotes, StockQuotesRequest
    request = request_cls(feed="sip", symbol_or_symbols=symbol, start=start.isoformat(), end=end.isoformat())
    rows = get(request).data.get(symbol, [])
    # The next window starts at `end`, so drop anything on the boundary in case Alpaca's `end` is inclusive
    return rows if is_last else [row for row in rows if row.timestamp < end]


def _start_download_if_needed(pool, data_type, symbol, day_in_question, force, start_time, end_time):
    """
    Submit the fetches for one day's `data_type` to `pool`.

    Returns (instrument_id, data_type, futures), or `None` if it's already in the catalog and `force` is off.
    """
    start_dt_str, end_dt_str = _download_range(day_in_question, start_time, end_time)
    instrument_id = TestInstrumentProvider.equity(symbol=symbol, venue=ALPACA).id

    # Check if data exists for the range in BACKTESTING_CATALOG (reads filenames only, no data loaded)
    if _data_exists(instrument_id, day_in_question, data_type):
        if force:
            _delete_range(instrument_id, start_dt_str, end_dt_str, data_type)
        else:
            print("\tForce is off, skipping")
            return None

    print(f"\tGetting {data_type.__name__}s for {instrument_id} on {day_in_question.date()}")
    futures = [
        pool.submit(_fetch_slice, data_type, symbol, start, end, is_last)
        for start, end, is_last in _slices(start_dt_str, end_dt_str)
    ]
    return instrument_id, data_type, futures


def _finish_download(instrument_id, data_type, futures):
    """Wait for one day's fetches, then convert them in order and write them to the catalog."""
    rows = [row for future in futures for row in future.result()]
    to_ticks = _trade_ticks if data_type is TradeTick else _quote_ticks
    BACKTESTING_CATALOG.write_data(to_ticks(instrument_id, rows))


def _download_jobs(jobs):
    """
    Download (symbol, day, force, start_time, end_time) `jobs` in this process.

    Up to `DAYS_IN_FLIGHT` days keep fetching while the oldest is converted and written.
    """
    in_flight = deque()
    with ThreadPoolExecutor(N_THREADS_PER_PROCESS) as pool:
        for symbol, day, force, start_time, end_time in jobs:
            window = f"{start_time or '00:00'}-{end_time or '24:00'} ET"
            print(f"\nGetting data for {symbol} on {day.date()} {window}, force={force}")
            for data_type in (TradeTick, QuoteTick):
                started = _start_download_if_needed(pool, data_type, symbol, day, force, start_time, end_time)
                if started is not None:
                    in_flight.append(started)
            while len(in_flight) > 2 * DAYS_IN_FLIGHT:
                _finish_download(*in_flight.popleft())
        while in_flight:
            _finish_download(*in_flight.popleft())


def _download_jobs_in_worker(catalog_path, jobs):
    global BACKTESTING_CATALOG  # noqa: PLW0603 - so a worker writes where its parent would
    BACKTESTING_CATALOG = ParquetDataCatalog(catalog_path)
    _download_jobs(jobs)


def download_alpaca_data(symbols, days, force=False, start_time=None, end_time=None):
    """
    Download trades and quotes for every symbol and day, spread over `N_PROCESSES` processes.

    `start_time`/`end_time` are as in `prepare_alpaca_data`.
    """
    jobs = [(symbol, day, force, start_time, end_time) for symbol in symbols for day in days]
    if N_PROCESSES == 1:
        _download_jobs(jobs)
        return

    # Every Nth job goes to each worker so they share the load evenly. Workers use the parent's catalog
    chunks = [jobs[i::N_PROCESSES] for i in range(N_PROCESSES)]
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(N_PROCESSES, mp_context=ctx) as executor:
        futures = [executor.submit(_download_jobs_in_worker, BACKTESTING_CATALOG.path, chunk) for chunk in chunks]
        for future in futures:
            future.result()


def _trade_ticks(instrument_id, trades):
    return [
        TradeTick(
            instrument_id=instrument_id,
            price=Price(trade.price, precision=_price_precision(trade.price)),
            size=Quantity.from_int(int(trade.size)),
            aggressor_side=AggressorSide.NO_AGGRESSOR,
            trade_id=TradeId(str(i)),
            ts_event=(ts := dt_to_unix_nanos(trade.timestamp)),  # Look at me! I'm a WAAAAAAALLLrus
            ts_init=ts,
        )
        for i, trade in enumerate(trades)
        if trade.exchange != "D"
        and trade.size != 0
        and not has_excluded_condition(trade.conditions)
    ]


def _quote_ticks(instrument_id, quotes):
    return [
        QuoteTick(
            instrument_id=instrument_id,
            bid_price=Price(quote.bid_price, precision=_price_precision(quote.bid_price)),
            ask_price=Price(quote.ask_price, precision=_price_precision(quote.bid_price)),
            bid_size=Quantity.from_int(int(quote.bid_size)),
            ask_size=Quantity.from_int(int(quote.ask_size)),
            ts_event=(ts := dt_to_unix_nanos(quote.timestamp)),
            ts_init=ts,
        )
        for quote in quotes
    ]


def prepare_alpaca_data(symbol, day_in_question: pd.Timestamp, force=False, start_time=None, end_time=None):
    """
    Download one day's trades and quotes. `start_time`/`end_time` are New York wall-clock "HH:MM"
    strings (EST or EDT, whichever applies that day); `None` means the whole day.
    """
    _download_jobs([(symbol, day_in_question, force, start_time, end_time)])


def add_entry_to_catalog_options(key, symbol, data_start_dt, data_end_dt, notes=None):
    catalog_json_path = repo_path("custom", "catalog_options.json")
    with open(catalog_json_path) as f:
        catalog_options = json.load(f)

    # Check if the key is already
    if key in catalog_options.keys():
        print(f"{key} already in catalog_options. Skipping.")
        return

    catalog_options[key] = {
        "symbol": symbol.upper(),
        "start": data_start_dt.strftime("%Y-%m-%d %H:%M"),
        "end": data_end_dt.strftime("%Y-%m-%d %H:%M"),
        "notes": "" if notes is None else notes,
    }

    write_json_single_line_entries(catalog_options, catalog_json_path)


def download_matching_data_from_live_run(artifacts_dir, symbol):
    artifacts_io = LiveArtifactsIO(artifacts_dir, symbol)
    ticks = artifacts_io.load_ticks_and_metrics_file()
    start = min(ticks.keys())
    symbol = artifacts_io.symbol

    data_start_dt = pd.Timestamp(start, unit="ns", tz="UTC").tz_convert("US/Eastern").normalize()
    data_end_dt = data_start_dt + pd.Timedelta(days=1)
    prepare_alpaca_data(symbol, data_start_dt)
    date_str = data_start_dt.strftime("%m%d")
    key = f"{date_str}_{symbol.lower()}"
    add_entry_to_catalog_options(key, symbol, data_start_dt, data_end_dt)


def _download_top_gainers():
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
            candidates = top_gainers_df[top_gainers_df["timestamp"] == start_dt].head(must_be_in_top)
            candidates = candidates[
                (candidates["price"] > price_min)
                & (candidates["price"] < price_max)
                & (candidates["volume"] > vol_min)
                & (candidates["perc_gain"] > min_perc_gain)
            ]
            print(f"{candidates}\n")
            for symbol in candidates["symbol"].unique():
                data_start_dt = start_dt - pd.Timedelta(hours=1)
                data_end_dt = data_start_dt.replace(hour=0, minute=0) + pd.Timedelta(days=1)
                prepare_alpaca_data(symbol, data_start_dt, data_end_dt)

            data_end_dt = data_start_dt.replace(hour=0, minute=0) + pd.Timedelta(days=1)
            date_str = data_start_dt.strftime("%m%d")
            key = f"{date_str}_{symbol.lower()}"
            add_entry_to_catalog_options(key, symbol, data_start_dt, data_end_dt)


if __name__ == "__main__":
    # --------- DOWNLOAD MATCHING DATA FROM LIVE RUN ------------
    # artifacts_dir = data_subdir("runs", "20260313_144138")
    # artifacts_dir = data_subdir("paper_runs", "20260311_104208")
    # download_matching_data_from_live_run(artifacts_dir)

    # EVTV 5/13 07:00. Received at 07:00:03
    # CANF 2026-05-13 07:00. Received 07:00:00.07 - positive phase 2a pancreatic cancer data
    # TSEM 2026-05-13 07:00. Received 07:00:01.2 - earnings
    # EOSE 2026-05-13 06:30. Received 06:30:03 - deploy american-made long duration energy storage at scale

    nasdaq_holidays = [
        pd.Timestamp("2024-01-01"),  # New Year's Day
        pd.Timestamp("2024-01-15"),  # MLK Day
        pd.Timestamp("2024-02-19"),  # Presidents' Day
        pd.Timestamp("2024-03-29"),  # Good Friday
        pd.Timestamp("2024-05-27"),  # Memorial Day
        pd.Timestamp("2024-06-19"),  # Juneteenth
        pd.Timestamp("2024-07-04"),  # Independence Day
        pd.Timestamp("2024-09-02"),  # Labor Day
        pd.Timestamp("2024-11-28"),  # Thanksgiving
        pd.Timestamp("2024-12-25"),  # Christmas
        pd.Timestamp("2025-01-01"),  # New Year's Day
        pd.Timestamp("2025-01-09"),  # National Day of Mourning (President Carter)
        pd.Timestamp("2025-01-20"),  # MLK Day
        pd.Timestamp("2025-02-17"),  # Presidents' Day
        pd.Timestamp("2025-04-18"),  # Good Friday
        pd.Timestamp("2025-05-26"),  # Memorial Day
        pd.Timestamp("2025-06-19"),  # Juneteenth
        pd.Timestamp("2025-07-04"),  # Independence Day
        pd.Timestamp("2025-09-01"),  # Labor Day
        pd.Timestamp("2025-11-27"),  # Thanksgiving
        pd.Timestamp("2025-12-25"),  # Christmas
        pd.Timestamp("2026-01-01"),  # New Year's Day
        pd.Timestamp("2026-01-19"),  # MLK Day
        pd.Timestamp("2026-02-16"),  # Presidents' Day
        pd.Timestamp("2026-04-03"),  # Good Friday
        pd.Timestamp("2026-05-25"),  # Memorial Day
        pd.Timestamp("2026-06-19"),  # Juneteenth
        pd.Timestamp("2026-07-03"),  # Independence Day (observed)
        pd.Timestamp("2026-09-07"),  # Labor Day
        pd.Timestamp("2026-11-26"),  # Thanksgiving
        pd.Timestamp("2026-12-25"),  # Christmas
    ]
    trading_days = pd.bdate_range("2026-1-01", "2026-11-10", freq="C", holidays=nasdaq_holidays)

    symbols = ["AMZN", "AAPL", "MSFT", "GOOG", "WMT", "META"]
    symbols = ["AMZN", "AAPL", "GOOG"]
    # New York wall-clock window to download each day. `None` for either end means midnight.
    start_time = None
    end_time = None
    download_alpaca_data(symbols, trading_days, force=False, start_time=start_time, end_time=end_time)

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
