from enum import StrEnum


class Side(StrEnum):
    """
    The direction a strategy trades in.

    A StrEnum so that `Side.LONG == "long"` and f-strings render the bare value, which keeps
    config files, logs and saved artifacts readable.
    """

    LONG = "long"
    SHORT = "short"
