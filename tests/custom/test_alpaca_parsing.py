import pytest

from nautilus_trader.adapters.alpaca.parsing import AlpacaEnumParser
from nautilus_trader.model.enums import OrderSide


@pytest.mark.parametrize(
    ("alpaca_side", "expected"),
    [("buy", OrderSide.BUY), ("sell", OrderSide.SELL), ("sell_short", OrderSide.SELL)],
)
def test_parse_alpaca_order_side(alpaca_side, expected):
    # The account activities (FILL) endpoint reports short sales as "sell_short"; failing to parse
    # it broke every reconciliation once the strategy had shorted (34 of 87 on 2026-09-24).
    assert AlpacaEnumParser.parse_alpaca_order_side(alpaca_side) == expected


def test_parse_alpaca_order_side_rejects_unknown_sides():
    with pytest.raises(ValueError):
        AlpacaEnumParser.parse_alpaca_order_side("short")
