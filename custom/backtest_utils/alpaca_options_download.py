#!/usr/bin/env python3
"""
Download a symbol's historical options data from Alpaca into ``data/options/<SYMBOL>/``.

Alpaca has options bars and trades from 2024 on, and lists expired contracts, but has no historical
quotes, open interest or greeks. Saved, each skipped if already on disk:

    contracts.parquet          every contract expiring in [START, END + 120 days], active or expired
    stock_minute_bars.parquet  the underlying's 1-minute SIP bars, 04:00-20:00 ET
    daily_bars/<expiry>.parquet  daily bars of every contract of that expiry, over the
                               `DAILY_LOOKBACK_DAYS` days before it (most volume trades then)
    minute_bars/<day>.parquet  1-minute RTH bars of the contracts of the `N_EXPIRIES` nearest
                               expiries from that day on, with strikes within `STRIKE_BAND` of the
                               day's price range

The trading days are the catalog days in ``data/analysis/<SYMBOL>_opening_range_stats.parquet``
(written by `opening_range_features`).

Edit the values under ``if __name__ == "__main__"`` and run from the repo root:
    python custom/backtest_utils/alpaca_options_download.py
"""

import os
import sys


sys.path.append(os.getcwd())
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from custom.utils.paths import data_subdir
from nautilus_trader.adapters.alpaca.utils import get_alpaca_key_and_secret


DATA_URL = "https://data.alpaca.markets"
TRADING_URL = "https://paper-api.alpaca.markets"
NY_TZ = "America/New_York"
# Symbols per bars request, kept well under Alpaca's URL length limit
SYMBOLS_PER_REQUEST = 100
N_THREADS = 16

_session = requests.Session()
_key, _secret = get_alpaca_key_and_secret(paper=True)
_session.headers.update({"APCA-API-KEY-ID": _key, "APCA-API-SECRET-KEY": _secret})


def _get(url: str, params: dict) -> dict:
    """GET with retries on rate limits and server errors."""
    for attempt in range(8):
        r = _session.get(url, params=params, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2**attempt)
            continue
        raise RuntimeError(f"{r.status_code} from {url} {params}: {r.text[:500]}")
    raise RuntimeError(f"Gave up on {url} {params}")


def _paged(url: str, params: dict, key: str):
    """Every item of `key` across the pages of a request."""
    params = dict(params)
    while True:
        page = _get(url, params)
        yield page[key]
        if not page.get("next_page_token"):
            return
        params["page_token"] = page["next_page_token"]


def _bars(url: str, params: dict) -> pd.DataFrame:
    """Every page of a multi-symbol bars request as one frame, with a `symbol` column."""
    rows = [
        {"symbol": symbol, **bar}
        for bars in _paged(url, params | {"limit": 10000}, "bars")
        for symbol, symbol_bars in bars.items()
        for bar in symbol_bars
    ]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "n": "trades", "vw": "vwap"})
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def download_contracts(symbol: str, start: str, end: str, out: Path) -> pd.DataFrame:
    path = out / "contracts.parquet"
    if path.exists():
        return pd.read_parquet(path)
    rows = []
    for status in ("inactive", "active"):
        params = {
            "underlying_symbols": symbol,
            "status": status,
            "expiration_date_gte": start,
            "expiration_date_lte": end,
            "limit": 10000,
        }
        for page in _paged(f"{TRADING_URL}/v2/options/contracts", params, "option_contracts"):
            rows += page
    df = pd.DataFrame(rows)[["symbol", "expiration_date", "strike_price", "type", "root_symbol", "style"]]
    df = df.drop_duplicates("symbol").sort_values(["expiration_date", "type", "strike_price"])
    df["strike_price"] = df["strike_price"].astype(float)
    df.to_parquet(path, index=False)
    print(f"{len(df)} contracts over {df['expiration_date'].nunique()} expiries")
    return df


def download_stock_minute_bars(symbol: str, start: str, end: str, out: Path) -> pd.DataFrame:
    path = out / "stock_minute_bars.parquet"
    if path.exists():
        return pd.read_parquet(path)
    params = {"symbols": symbol, "timeframe": "1Min", "start": start, "end": end, "feed": "sip", "adjustment": "raw"}
    df = _bars(f"{DATA_URL}/v2/stocks/bars", params).drop(columns="symbol")
    df.to_parquet(path, index=False)
    print(f"{len(df)} {symbol} minute bars")
    return df


def _expiry_daily_bars(expiry: str, symbols: list[str], start: str, path: Path):
    end = (pd.Timestamp(expiry) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    frames = [
        _bars(f"{DATA_URL}/v1beta1/options/bars", {"symbols": ",".join(chunk), "timeframe": "1Day", "start": start, "end": end})
        for chunk in (symbols[i : i + SYMBOLS_PER_REQUEST] for i in range(0, len(symbols), SYMBOLS_PER_REQUEST))
    ]
    pd.concat(frames, ignore_index=True).to_parquet(path, index=False)


def download_daily_bars(contracts: pd.DataFrame, start: str, last_day: str, lookback_days: int, out: Path):
    folder = out / "daily_bars"
    folder.mkdir(parents=True, exist_ok=True)
    jobs = []
    for expiry, group in contracts.groupby("expiration_date"):
        path = folder / f"{expiry}.parquet"
        first = max(pd.Timestamp(start), pd.Timestamp(expiry) - pd.Timedelta(days=lookback_days))
        # An expiry still to come is re-downloaded, since it has days left to trade
        if (path.exists() and expiry <= last_day) or first > pd.Timestamp(last_day):
            continue
        jobs.append((expiry, list(group["symbol"]), first.strftime("%Y-%m-%d"), path))
    print(f"Downloading daily bars of {len(jobs)} expiries...")
    with ThreadPoolExecutor(N_THREADS) as pool:
        list(pool.map(lambda job: _expiry_daily_bars(*job), jobs))


def _day_minute_bars(day: str, symbols: list[str], path: Path):
    start = pd.Timestamp(f"{day} 09:30", tz=NY_TZ).tz_convert("UTC").isoformat()
    end = pd.Timestamp(f"{day} 16:15", tz=NY_TZ).tz_convert("UTC").isoformat()
    frames = [
        _bars(f"{DATA_URL}/v1beta1/options/bars", {"symbols": ",".join(chunk), "timeframe": "1Min", "start": start, "end": end})
        for chunk in (symbols[i : i + SYMBOLS_PER_REQUEST] for i in range(0, len(symbols), SYMBOLS_PER_REQUEST))
    ]
    pd.concat(frames, ignore_index=True).to_parquet(path, index=False)


def download_minute_bars(contracts: pd.DataFrame, stock: pd.DataFrame, days: list[str], n_expiries: int, strike_band: float, out: Path):
    folder = out / "minute_bars"
    folder.mkdir(parents=True, exist_ok=True)
    stock_day = stock["ts"].dt.tz_convert(NY_TZ).dt.strftime("%Y-%m-%d")
    lows, highs = stock.groupby(stock_day)["low"].min(), stock.groupby(stock_day)["high"].max()
    jobs = []
    for day in days:
        path = folder / f"{day}.parquet"
        if path.exists() or day not in lows.index:
            continue
        expiries = sorted(e for e in contracts["expiration_date"].unique() if e >= day)[:n_expiries]
        chosen = contracts[
            contracts["expiration_date"].isin(expiries)
            & contracts["strike_price"].between(lows[day] * (1 - strike_band), highs[day] * (1 + strike_band))
        ]
        jobs.append((day, list(chosen["symbol"]), path))
    print(f"Downloading minute bars of {len(jobs)} days...")
    with ThreadPoolExecutor(N_THREADS) as pool:
        for i, _ in enumerate(pool.map(lambda job: _day_minute_bars(*job), jobs)):
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(jobs)} days", flush=True)


def main(symbol, start, end, n_expiries, strike_band, lookback_days):
    out = data_subdir("options", symbol)
    days = sorted(pd.read_parquet(Path(data_subdir("analysis")) / f"{symbol}_opening_range_stats.parquet", columns=["day"])["day"])
    days = [d for d in days if start <= d <= end]
    contracts_end = (pd.Timestamp(end) + pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    contracts = download_contracts(symbol, start, contracts_end, out)
    stock_end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    stock = download_stock_minute_bars(symbol, start, stock_end, out)
    # Adjusted contracts (after a corporate action, named like 1AMZN...) aren't accepted by the bars endpoint
    contracts = contracts[contracts["symbol"].str.fullmatch(rf"{symbol}\d{{6}}[CP]\d{{8}}")]
    download_daily_bars(contracts, start, days[-1], lookback_days, out)
    download_minute_bars(contracts, stock, days, n_expiries, strike_band, out)


if __name__ == "__main__":
    SYMBOL = "AMZN"
    START = "2025-01-01"
    END = "2026-10-30"
    # Minute bars are kept for the contracts of this many nearest expiries each day
    N_EXPIRIES = 4
    # ... with strikes from this fraction below the day's low to this fraction above its high
    STRIKE_BAND = 0.06
    # Daily bars are kept for this many calendar days before each expiry
    DAILY_LOOKBACK_DAYS = 70

    main(SYMBOL, START, END, N_EXPIRIES, STRIKE_BAND, DAILY_LOOKBACK_DAYS)
