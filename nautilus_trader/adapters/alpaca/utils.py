from typing import Tuple

from nautilus_trader import ENV


def get_alpaca_key_and_secret(paper:bool=True) -> Tuple[str, str]:
    if paper:
        return ENV.ALPACA_PAPER_KEY, ENV.ALPACA_PAPER_SECRET
    else:
        return ENV.ALPACA_KEY, ENV.ALPACA_SECRET
