from datetime import datetime
from typing import Tuple

from nautilus_trader import ENV
from nautilus_trader.core.datetime import dt_to_unix_nanos


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
