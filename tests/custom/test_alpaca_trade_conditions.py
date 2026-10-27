from unittest.mock import MagicMock

import pytest

from nautilus_trader.adapters.alpaca.data import AlpacaDataClient
from nautilus_trader.model.identifiers import InstrumentId


INSTRUMENT_ID = InstrumentId.from_str("AMZN.ALPACA")


def _msg(conditions, exchange="Q", size=100):
    return {
        "T": "t",
        "S": "AMZN",
        "i": 5078,
        "x": exchange,
        "p": 245.8,
        "s": size,
        "c": conditions,
        "t": "2026-09-24T13:30:01.3528Z",
        "z": "C",
    }


def _parse(msg):
    client = MagicMock()
    client._clock.timestamp_ns.return_value = 0
    return AlpacaDataClient._trade_msg_to_tradetick(client, INSTRUMENT_ID, msg)


@pytest.mark.parametrize(
    "conditions",
    [
        ["@", "O", "X"],  # opening cross print (2026-09-24 AMZN, 550,554 @ 245.80)
        ["@", "Q"],  # its official-open re-report, same size and price
        ["@", "M"],
    ],
)
def test_trade_with_excluded_condition_is_skipped(conditions):
    assert _parse(_msg(conditions)) is None


@pytest.mark.parametrize(
    "conditions",
    [["@"], ["@", "F"], ["@", "I"], ["@", "T"], ["@", "F", "T", "I"], [], None],
)
def test_regular_trade_is_kept(conditions):
    trade = _parse(_msg(conditions))

    assert trade is not None
    assert trade.size == 100


def test_trade_without_condition_field_is_kept():
    msg = _msg(["@"])
    del msg["c"]

    assert _parse(msg) is not None


def test_finra_and_zero_size_trades_are_still_skipped():
    assert _parse(_msg(["@"], exchange="D")) is None
    assert _parse(_msg(["@"], size=0)) is None
