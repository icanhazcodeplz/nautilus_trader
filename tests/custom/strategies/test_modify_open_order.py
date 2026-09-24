"""
Tests for BaseStrategy.modify_open_order's venue-quantity clamp.

Alpaca implements modify as cancel-and-replace, and the replacement order starts with its own
fresh filled_qty. Resending the full target quantity on every reprice let a replacement stack a
fresh fill on top of whatever had already filled against the logical order -- the root cause of
the 2026-09-23 AMZN overfill (53 filled, then another 100 filled on the replacement, for 153
against a 100-share cap). These tests pin that only the leftover quantity (target - filled) is
ever sent to the venue.
"""

from unittest.mock import MagicMock

from custom.strategies._open_order import OpenOrder
from custom.strategies.base import BaseStrategy
from nautilus_trader.model import Price, Quantity
from nautilus_trader.model.enums import OrderStatus

# Past the modify-frequency cooldown and the after-fill block from a fresh OpenOrder's zeroed state.
FAR_FUTURE_NS = 10_000_000_000


def _order(quantity, filled_qty, status=OrderStatus.ACCEPTED):
    order = MagicMock()
    order.status = status
    order.quantity = Quantity.from_int(quantity)
    order.filled_qty = Quantity.from_int(filled_qty)
    order.events = []
    order.price = Price.from_str("10.00")
    order.client_order_id = "O-1"
    return order


class _ModifyHarness(BaseStrategy):
    """
    A BaseStrategy with just enough shadowed to exercise the real modify_open_order.

    `BaseStrategy.__init__` is bypassed (needs a fully wired nautilus Strategy); `modify_order`
    -- the underlying nautilus Strategy call -- is mocked so nothing hits the venue, but
    `modify_open_order` itself runs for real, which is what test_base_side.py's harness mocks
    away and so cannot exercise.
    """

    def __init__(self):
        self._log_mock = MagicMock()
        self._clock_mock = MagicMock()
        self._clock_mock.timestamp_ns.return_value = FAR_FUTURE_NS
        self.instrument = MagicMock()
        self.instrument.make_qty.side_effect = lambda q: Quantity.from_int(int(q))
        self.modify_order = MagicMock()

    log = property(lambda s: s._log_mock)
    clock = property(lambda s: s._clock_mock)

    def _on_trade_tick(self, tick):  # abstract
        pass

    def _on_order_filled(self, order):  # abstract
        pass


def test_sends_only_the_leftover_quantity_after_a_partial_fill():
    open_order = OpenOrder(_order(quantity=100, filled_qty=53))
    s = _ModifyHarness()

    result = s.modify_open_order(open_order, quantity=100, price=10.01)

    assert result is True
    sent_qty = s.modify_order.call_args.kwargs["quantity"]
    assert int(sent_qty) == 47, "must not re-request the 53 shares already filled"


def test_sends_the_full_quantity_when_nothing_has_filled():
    open_order = OpenOrder(_order(quantity=100, filled_qty=0))
    s = _ModifyHarness()

    s.modify_open_order(open_order, quantity=100, price=10.01)

    sent_qty = s.modify_order.call_args.kwargs["quantity"]
    assert int(sent_qty) == 100


def test_skips_the_modify_once_filled_qty_already_meets_the_target():
    open_order = OpenOrder(_order(quantity=100, filled_qty=100))
    s = _ModifyHarness()

    result = s.modify_open_order(open_order, quantity=100, price=10.01)

    assert result is False
    s.modify_order.assert_not_called()


def test_skips_the_modify_when_fills_have_already_exceeded_the_target():
    """The exact FILLED -> FILLED overfill scenario from the log: filled_qty > quantity."""
    open_order = OpenOrder(_order(quantity=100, filled_qty=127))
    s = _ModifyHarness()

    result = s.modify_open_order(open_order, quantity=100, price=10.01)

    assert result is False
    s.modify_order.assert_not_called()


def test_a_resize_is_clamped_against_the_new_target_not_the_stale_leaves_qty():
    """Exit-tier resizing passes a new target quantity that differs from the order's current one."""
    open_order = OpenOrder(_order(quantity=10, filled_qty=4))
    s = _ModifyHarness()

    s.modify_open_order(open_order, quantity=20, price=10.01)  # resized up to 20

    sent_qty = s.modify_order.call_args.kwargs["quantity"]
    assert int(sent_qty) == 16  # 20 - 4 already filled


def test_the_open_orders_own_target_bookkeeping_is_not_shrunk_to_the_venue_qty():
    """
    Regression guard: OpenOrder.quantity/leaves_qty must keep tracking the full target, not the
    leftover amount sent to the venue -- otherwise the next reprice double-subtracts filled_qty.
    """
    open_order = OpenOrder(_order(quantity=100, filled_qty=53))
    s = _ModifyHarness()

    s.modify_open_order(open_order, quantity=100, price=10.01)

    assert int(open_order.quantity) == 100
    assert int(open_order.leaves_qty) == 47


def test_a_modify_held_while_pending_new_is_retried_without_cooldown_or_reconciliation():
    from nautilus_trader.adapters.alpaca.execution import MODIFY_HELD_PENDING_NEW_REASON
    from nautilus_trader.model.events import OrderModifyRejected

    strategy = _ModifyHarness()
    open_order = OpenOrder(_order(quantity=100, filled_qty=0))
    strategy._buy_orders = {open_order}
    strategy._sell_orders = set()
    strategy._trigger_nt_reconciliation = MagicMock()
    event = MagicMock(spec=OrderModifyRejected)
    event.client_order_id = "O-1"
    event.reason = MODIFY_HELD_PENDING_NEW_REASON

    strategy.on_order_event(event)

    strategy._trigger_nt_reconciliation.assert_not_called()
    assert open_order._last_modify_ns == 0  # no cooldown pushed into the future
