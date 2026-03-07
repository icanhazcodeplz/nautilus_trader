from datetime import datetime
from typing import Tuple

import pandas as pd

from nautilus_trader import ENV
from nautilus_trader.core.datetime import dt_to_unix_nanos


def get_alpaca_key_and_secret(paper: bool = True) -> Tuple[str, str]:
    if paper:
        return ENV.ALPACA_PAPER_KEY, ENV.ALPACA_PAPER_SECRET
    else:
        return ENV.ALPACA_KEY, ENV.ALPACA_SECRET


def alpaca_date_str_to_nanos(alpaca_date_str: str) -> int:
    """
    Parse timestamp (RFC-3339 format) with nanosecond precision.
    """
    ts = pd.Timestamp(alpaca_date_str)
    return dt_to_unix_nanos(ts)


def dt_to_iso_8601(dt: datetime) -> str:
    """Convert a datetime-like object to an ISO 8601 string with nanosecond precision."""
    ts = pd.Timestamp(dt)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.isoformat()


def ns_to_iso_8601(ns: int) -> str:
    """Convert nanoseconds since epoch to ISO 8601 UTC string with nanosecond precision."""
    return pd.Timestamp(ns, unit="ns", tz="UTC").isoformat()
