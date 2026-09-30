from nautilus_trader.model.identifiers import Venue

ALPACA = "ALPACA"
ALPACA_VENUE = Venue("ALPACA")

# SIP trade condition codes whose trades are dropped from live and historical trade data. A trade
# carrying any one of them is skipped.
#   Q - Market Center Official Open: re-reports the opening cross, so keeping it double counts it
#   M - Market Center Official Close: the same, for the closing cross
#   O - Opening Prints: the opening auction print
#   X - Cross Trade: auction/cross executions
EXCLUDED_TRADE_CONDITIONS = frozenset({"Q", "M", "O", "X"})


def has_excluded_condition(conditions: list[str] | None) -> bool:
    """Whether a trade carries any of the `EXCLUDED_TRADE_CONDITIONS` (None means no conditions)."""
    return any(condition in EXCLUDED_TRADE_CONDITIONS for condition in conditions or [])
