from datetime import datetime, timezone
from typing import Tuple

from nautilus_trader import ENV
from nautilus_trader.core.datetime import dt_to_unix_nanos, ensure_pydatetime_utc


def get_alpaca_key_and_secret(paper: bool = True) -> Tuple[str, str]:
    if paper:
        return ENV.ALPACA_PAPER_KEY, ENV.ALPACA_PAPER_SECRET
    else:
        return ENV.ALPACA_KEY, ENV.ALPACA_SECRET


def alpaca_date_str_to_nanos(alpaca_date_str: str) -> int:
    """
    Parse timestamp (RFC-3339 format)
    """
    timestamp_dt = datetime.fromisoformat(alpaca_date_str.replace("Z", "+00:00"))
    return dt_to_unix_nanos(timestamp_dt)


def dt_to_iso_8601(dt: datetime) -> str:
    """Convert a datetime-like object to an ISO 8601 string via ensure_pydatetime_utc."""
    return ensure_pydatetime_utc(dt).isoformat()


def ns_to_iso_8601(ns: int) -> str:
    """Convert nanoseconds since epoch to ISO 8601 UTC string (e.g. '2026-02-13T05:04:00Z')."""
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
