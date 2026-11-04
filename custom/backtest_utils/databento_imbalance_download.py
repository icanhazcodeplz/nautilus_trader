#!/usr/bin/env python3
"""
Download a symbol's Nasdaq auction imbalance (NOII) messages from Databento into ``data/imbalance/``.

Databento's `XNAS.ITCH` `imbalance` schema is Nasdaq TotalView's Net Order Imbalance Indicator,
normalized: from 09:25 ET for the opening cross (every 10s, every 1s after 09:28) and 15:50 for the
closing cross. Historical data is billed per request (AMZN 2025-01 to 2026-10 was $0.40), so the raw
CSV is saved first as ``<SYMBOL>_<START>_<END>.csv`` and never re-requested while it exists. It is
then parsed into ``<SYMBOL>.parquet`` with every downloaded range, one row per message, times in UTC.

Reads `DATABENTO_API_KEY` from ``.env``. Check the cost first with `metadata.get_cost` (free).
Edit the values under ``if __name__ == "__main__"`` and run from the repo root:
    python custom/backtest_utils/databento_imbalance_download.py
"""

import os
import re
import sys


sys.path.append(os.getcwd())
from pathlib import Path

import pandas as pd
import requests

from custom.utils.paths import data_subdir
from custom.utils.paths import repo_path


HIST_URL = "https://hist.databento.com/v0"
DATASET = "XNAS.ITCH"


def _api_key() -> str:
    match = re.search(r"^DATABENTO_API_KEY\s*=\s*['\"]?([^'\"\s]+)", Path(repo_path(".env")).read_text(), re.M)
    if not match:
        raise SystemExit("No DATABENTO_API_KEY in .env")
    return match.group(1)


def _params(symbol: str, start: str, end: str) -> dict:
    return {"dataset": DATASET, "symbols": symbol, "schema": "imbalance", "start": start, "end": end, "stype_in": "raw_symbol"}


def download_raw(symbol: str, start: str, end: str, out: Path) -> Path:
    """The raw CSV of [start, end), downloaded (and billed) only if not already on disk."""
    path = out / f"{symbol}_{start}_{end}.csv"
    if path.exists():
        return path
    key = _api_key()
    cost = requests.get(f"{HIST_URL}/metadata.get_cost", params=_params(symbol, start, end), auth=(key, ""), timeout=60)
    cost.raise_for_status()
    print(f"Downloading {symbol} {DATASET} imbalance {start} to {end} (${float(cost.text):.2f})...")
    params = _params(symbol, start, end) | {"encoding": "csv", "compression": "none", "pretty_px": "true", "pretty_ts": "true", "map_symbols": "true"}
    tmp = path.with_suffix(".part")
    with requests.post(f"{HIST_URL}/timeseries.get_range", data=params, auth=(key, ""), stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(path)
    return path


def main(symbol, start, end):
    out = Path(data_subdir("imbalance"))
    out.mkdir(parents=True, exist_ok=True)
    download_raw(symbol, start, end, out)
    frames = [pd.read_csv(p) for p in sorted(out.glob(f"{symbol}_*.csv"))]
    df = pd.concat(frames, ignore_index=True)
    for col in ("ts_recv", "ts_event", "auction_time"):
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    df = df.drop_duplicates().sort_values("ts_recv").reset_index(drop=True)
    df.to_parquet(out / f"{symbol}.parquet", index=False)
    print(f"{len(df)} {symbol} imbalance messages, {df['ts_recv'].min()} to {df['ts_recv'].max()}, in {out / f'{symbol}.parquet'}")


if __name__ == "__main__":
    SYMBOL = "AMZN"
    START = "2025-01-01"
    END = "2026-11-02"

    main(SYMBOL, START, END)
