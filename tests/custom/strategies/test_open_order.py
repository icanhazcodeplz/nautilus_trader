"""
Tests for OpenOrder's modify-gating.

Alpaca implements modify as cancel-and-replace: a replacement order starts with its own fresh
filled_qty. A fill landing right as we send a modify can race the replace and let it stack a
fresh fill on top of shares already bought (the 2026-09-23 AMZN overfill: 53 filled on the
original order, then another 100 filled on its replacement, for 153 against a 100-share cap).
These tests pin the guard added for that: refuse to modify within BLOCK_MODIFY_AFTER_FILL_NS of
any fill, so the fill has time to reach us before we reprice again.
"""

from unittest.mock import MagicMock

from custom.strategies._open_order import (
    BLOCK_MODIFY_AFTER_FILL_NS,
    ONLY_MODIFY_EVERY_NS,
    OpenOrder,
)
from nautilus_trader.model import Price, Quantity
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.events import OrderFilled


def _fill(ts_event):
    event = MagicMock(spec=OrderFilled)
    event.ts_event = ts_event
    return event


def _order(status=OrderStatus.ACCEPTED, quantity=100, filled_qty=0, events=None):
    order = MagicMock()
    order.status = status
    order.quantity = Quantity.from_int(quantity)
    order.filled_qty = Quantity.from_int(filled_qty)
    order.events = events or []
    order.price = Price.from_str("10.00")
    order.client_order_id = "O-1"
    return order


# Past both the modify-frequency cooldown and the after-fill block, for tests not exercising them.
FAR_FUTURE_NS = 10_000_000_000


# ---------------------------------------------------------------------------
# last_fill_ns
# ---------------------------------------------------------------------------


def test_last_fill_ns_is_none_without_any_fills():
    open_order = OpenOrder(_order(events=[]))
    assert open_order.last_fill_ns is None


def test_last_fill_ns_is_the_most_recent_fill_regardless_of_event_order():
    open_order = OpenOrder(_order(events=[_fill(100), _fill(300), _fill(200)]))
    assert open_order.last_fill_ns == 300


# ---------------------------------------------------------------------------
# The after-fill modify guard
# ---------------------------------------------------------------------------


def test_modify_blocked_shortly_after_a_fill():
    fill_ns = 1_000_000_000
    open_order = OpenOrder(_order(filled_qty=53, events=[_fill(fill_ns)]))

    now_ns = fill_ns + int(BLOCK_MODIFY_AFTER_FILL_NS) - 1
    allowed = open_order.update_last_modify_if_allowed(Quantity.from_int(100), Price.from_str("10.01"), now_ns)

    assert allowed is False


def test_modify_allowed_once_the_after_fill_window_has_passed():
    fill_ns = 1_000_000_000
    open_order = OpenOrder(_order(filled_qty=53, events=[_fill(fill_ns)]))

    now_ns = fill_ns + int(BLOCK_MODIFY_AFTER_FILL_NS) + 1
    allowed = open_order.update_last_modify_if_allowed(Quantity.from_int(100), Price.from_str("10.01"), now_ns)

    assert allowed is True


def test_modify_allowed_with_no_fills_once_past_the_frequency_cooldown():
    open_order = OpenOrder(_order(events=[]))

    allowed = open_order.update_last_modify_if_allowed(
        Quantity.from_int(100), Price.from_str("10.01"), FAR_FUTURE_NS
    )

    assert allowed is True


def test_after_fill_guard_does_not_relax_the_ordinary_frequency_cooldown():
    """A fill long ago must not shorten the normal between-modifies cooldown."""
    open_order = OpenOrder(_order(filled_qty=10, events=[_fill(1)]))
    open_order.update_last_modify_if_allowed(Quantity.from_int(100), Price.from_str("10.01"), FAR_FUTURE_NS)

    too_soon = FAR_FUTURE_NS + int(ONLY_MODIFY_EVERY_NS) - 1
    allowed = open_order.update_last_modify_if_allowed(Quantity.from_int(100), Price.from_str("10.02"), too_soon)

    assert allowed is False
