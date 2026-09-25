"""
Tests for long/short side support in BaseStrategy.

The critical property is that `side == "long"` behaves exactly as the long-only implementation
did, so the old cap formulas are pinned here literally and compared against the new ones.
"""

from unittest.mock import MagicMock

import pytest

from custom.strategies._open_order import FLATTEN_TAG
from custom.strategies.base import BaseStrategy, Side
from nautilus_trader.model.enums import OrderSide


class _FakeStrategy(BaseStrategy):
    """
    A BaseStrategy with its cache/portfolio-backed properties replaced by plain values.

    `BaseStrategy.__init__` is bypassed because it needs a fully wired nautilus Strategy. The
    properties below shadow the Cython base-class attributes, which is what lets `log` be mocked.
    """

    def __init__(
        self,
        side="long",
        position_qty=0,
        open_buy_qty=0,
        open_sell_qty=0,
        max_position=1000,
        open_buys=None,
        open_sells=None,
        venue_orders=None,
        side_changed_ns=0,
        stop_loss=0.05,
    ):
        self._side = Side(side)
        # When set_side last switched. Orders initialized before this outlived that switch.
        self._side_changed_ns = side_changed_ns
        self._position_qty = position_qty
        self._open_buy_qty = open_buy_qty
        self._open_sell_qty = open_sell_qty
        self._max_position = max_position
        # Plain storage, so the real _prune_closed() runs under test
        self._buy_orders = set(open_buys or ())
        self._sell_orders = set(open_sells or ())

        self.stop_loss = stop_loss
        self.stop_price = None
        self._trading_enabled = True
        self._stopping_out = False
        self._flipping = False
        self._last_stop_out_attempt = 0
        self._entries_blocked_until_ns = 0
        self._last_wrong_way_flatten_ns = 0
        self._trader_helper = None
        self._total_entry_qty = 0
        self._last_tick = MagicMock(price=10.0)
        self.save_artifacts = False

        # `log`, `clock`, `config` and `cache` are non-writable Cython attributes on Actor, so
        # they are shadowed by properties below rather than assigned.
        self._log_mock = MagicMock()
        self._clock_mock = MagicMock()
        self._clock_mock.timestamp_ns.return_value = 10_000_000_000
        self._config_mock = MagicMock(instrument_id="X.ALPACA", stop_loss=stop_loss)
        self._cache_mock = MagicMock()
        # What the venue still holds. Separate from _buy_orders/_sell_orders on purpose: a sent
        # cancel empties those immediately, so they can be empty while an order is still working.
        self._cache_mock.orders_open.return_value = list(venue_orders or ())
        self._cache_mock.orders_inflight.return_value = []

        self.instrument = MagicMock()
        self.instrument.make_price.side_effect = lambda p: round(float(p), 4)

        self._position_discrepancy_start_ns = None
        self._force_reconcile_count = 0

        self.cancel_open_order = MagicMock()
        self.modify_open_order = MagicMock()
        self._submit_limit_order = MagicMock()
        self.exit_position_at_price = MagicMock()
        self._trigger_nt_reconciliation = MagicMock()

    # -- shadow the Cython / cache-backed surface ---------------------------------
    log = property(lambda s: s._log_mock)
    clock = property(lambda s: s._clock_mock)
    config = property(lambda s: s._config_mock)
    cache = property(lambda s: s._cache_mock)
    position_qty = property(lambda s: s._position_qty)
    max_position_allowed = property(lambda s: s._max_position)
    # open_orders / open_entries / open_exits are inherited, so the real implementations (and the
    # real _prune_closed) run against the plain _buy_orders / _sell_orders set in __init__.
    # Constructor args are literal venue sides; map them to entry/exit the way the real class does
    open_entries_qty = property(lambda s: s._open_buy_qty if s.is_long else s._open_sell_qty)
    open_exits_qty = property(lambda s: s._open_sell_qty if s.is_long else s._open_buy_qty)

    def _on_trade_tick(self, tick):  # abstract
        pass

    def _on_order_filled(self, order):  # abstract
        pass


# The long-only formulas as they existed before side support. Pinned deliberately.
def _old_max_buy_qty_allowed(max_position, open_buy_qty, position_qty):
    return max_position - open_buy_qty - position_qty


def _old_max_sell_qty_allowed(position_qty, open_sell_qty):
    return position_qty - open_sell_qty


# ---------------------------------------------------------------------------
# Long-side equivalence -- the regression that matters most
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("position_qty", [-300, -1, 0, 1, 250, 1000, 1500])
@pytest.mark.parametrize("open_buy_qty", [0, 50, 400])
@pytest.mark.parametrize("open_sell_qty", [0, 50, 400])
def test_long_caps_match_the_old_long_only_formulas(position_qty, open_buy_qty, open_sell_qty):
    s = _FakeStrategy("long", position_qty, open_buy_qty, open_sell_qty, max_position=1000)

    assert s._max_entry_qty_allowed() == _old_max_buy_qty_allowed(1000, open_buy_qty, position_qty)
    assert s._max_exit_qty_allowed() == _old_max_sell_qty_allowed(position_qty, open_sell_qty)


def test_long_exposure_is_the_raw_position():
    assert _FakeStrategy("long", position_qty=250).exposure == 250
    assert _FakeStrategy("long", position_qty=-250).exposure == -250  # wrong-way


def test_long_entry_and_exit_sides():
    s = _FakeStrategy("long")
    assert s._entry_order_side == OrderSide.BUY
    assert s._exit_order_side == OrderSide.SELL


# ---------------------------------------------------------------------------
# Short side
# ---------------------------------------------------------------------------


def test_short_exposure_is_the_magnitude_of_a_short_position():
    assert _FakeStrategy("short", position_qty=-400).exposure == 400
    assert _FakeStrategy("short", position_qty=400).exposure == -400  # wrong-way
    assert _FakeStrategy("short", position_qty=0).exposure == 0


def test_short_entry_and_exit_sides_are_swapped():
    s = _FakeStrategy("short")
    assert s._entry_order_side == OrderSide.SELL
    assert s._exit_order_side == OrderSide.BUY


def test_short_entry_cap_is_bounded_by_max_position():
    s = _FakeStrategy("short", position_qty=-400, open_sell_qty=100, max_position=1000)
    assert s._max_entry_qty_allowed() == 1000 - 100 - 400


def test_short_exit_cap_cannot_flip_through_flat_to_long():
    s = _FakeStrategy("short", position_qty=-400, open_buy_qty=0)
    assert s._max_exit_qty_allowed() == 400

    # A cover already resting for the full size leaves no room for another
    s = _FakeStrategy("short", position_qty=-400, open_buy_qty=400)
    assert s._max_exit_qty_allowed() == 0


def test_enter_and_exit_dispatch_to_the_right_venue_side():
    long_s = _FakeStrategy("long", position_qty=0)
    long_s.enter(100, 10.0, tag="t")
    assert long_s._submit_limit_order.call_args[0][0] == OrderSide.BUY

    long_s = _FakeStrategy("long", position_qty=500)
    long_s.exit(100, 10.0, tag="t")
    assert long_s._submit_limit_order.call_args[0][0] == OrderSide.SELL

    short_s = _FakeStrategy("short", position_qty=0)
    short_s.enter(100, 10.0, tag="t")
    assert short_s._submit_limit_order.call_args[0][0] == OrderSide.SELL

    short_s = _FakeStrategy("short", position_qty=-500)
    short_s.exit(100, 10.0, tag="t")
    assert short_s._submit_limit_order.call_args[0][0] == OrderSide.BUY


def test_exit_is_never_blocked_by_stopping_out_but_entry_is():
    """Exits must always get through -- that is the point of stopping out."""
    for side, pos in (("long", 500), ("short", -500)):
        s = _FakeStrategy(side, position_qty=pos)
        s._stopping_out = True

        s.enter(100, 10.0, tag="t")
        assert s._submit_limit_order.call_count == 0, f"{side}: entry should be blocked"

        s.exit(100, 10.0, tag="t")
        assert s._submit_limit_order.call_count == 1, f"{side}: exit should go through"


def test_a_stop_out_pauses_entries_for_ten_seconds_but_not_exits():
    s = _FakeStrategy("long", position_qty=500)
    s.stop_price = 9.95
    s._stop_out_if_needed(MagicMock(price=9.94))  # stop triggers at t=10s
    s._stopping_out = False  # e.g. price recovered before the exit filled

    s._clock_mock.timestamp_ns.return_value = 19_900_000_000  # 9.9s later
    s.enter(100, 10.0)
    assert s._submit_limit_order.call_count == 0, "entry should still be paused"
    s.exit(100, 10.0)
    assert s._submit_limit_order.call_count == 1, "exits are never paused"

    s._clock_mock.timestamp_ns.return_value = 20_000_000_000  # 10s later
    s.enter(100, 10.0)
    assert s._submit_limit_order.call_count == 2, "entry allowed once the pause is over"


def test_the_pause_restarts_when_the_stop_out_reaches_flat():
    s = _FakeStrategy("long", position_qty=500)
    s.stop_price = 9.95
    s._stop_out_if_needed(MagicMock(price=9.94))  # triggers at t=10s

    s._clock_mock.timestamp_ns.return_value = 13_000_000_000  # exit fills 3s later
    s._position_qty = 0
    s._stop_out_if_needed(MagicMock(price=9.90))

    s._clock_mock.timestamp_ns.return_value = 22_000_000_000  # 12s after trigger, 9s after flat
    assert s._in_stop_out_cooldown
    s._clock_mock.timestamp_ns.return_value = 23_000_000_000  # 10s after flat
    assert not s._in_stop_out_cooldown


def test_going_flat_without_a_stop_out_does_not_pause_entries():
    s = _FakeStrategy("long", position_qty=0)
    s._stop_out_if_needed(MagicMock(price=10.0))
    assert not s._in_stop_out_cooldown


def test_exit_is_never_blocked_by_flipping_but_entry_is():
    """A flip reaches flat by letting the exits fill, so blocking them would deadlock it."""
    for side, pos in (("long", 500), ("short", -500)):
        s = _FakeStrategy(side, position_qty=pos)
        s._flipping = True

        s.enter(100, 10.0, tag="t")
        assert s._submit_limit_order.call_count == 0, f"{side}: entry should be blocked"

        s.exit(100, 10.0, tag="t")
        assert s._submit_limit_order.call_count == 1, f"{side}: exit should go through"


# ---------------------------------------------------------------------------
# set_side guard
# ---------------------------------------------------------------------------


def test_set_side_succeeds_when_flat_with_no_open_orders():
    s = _FakeStrategy("long", position_qty=0)
    s.set_side("short")
    assert s.side == "short" and s.is_short and not s.is_long


def test_set_side_raises_when_not_flat():
    s = _FakeStrategy("long", position_qty=100)
    with pytest.raises(RuntimeError, match="while position is 100"):
        s.set_side("short")
    assert s.side == "long"


def test_set_side_raises_when_flat_but_orders_are_live_at_the_venue():
    order = MagicMock()
    order.client_order_id = "O-1"
    order.status_string.return_value = "PENDING_CANCEL"
    s = _FakeStrategy("long", position_qty=0, open_buys=[order], venue_orders=[order])
    with pytest.raises(RuntimeError, match="still live at the venue"):
        s.set_side("short")
    assert s.side == "long"


def test_set_side_raises_on_a_venue_order_the_local_books_have_dropped():
    """
    The case that stranded a short entry on 2026-09-22.

    `cancel_open_order` discards from the local books as soon as it sends, so they go empty
    while the order is still working -- and the venue can refuse the cancel outright. Reading
    the books here is what let the side flip out from under a live order.
    """
    order = MagicMock()
    order.client_order_id = "O-2"
    order.status_string.return_value = "ACCEPTED"
    s = _FakeStrategy("long", position_qty=0, open_buys=[], venue_orders=[order])
    assert len(s.open_orders) == 0, "precondition: the local books look clear"
    with pytest.raises(RuntimeError, match="still live at the venue"):
        s.set_side("short")
    assert s.side == "long"


def test_set_side_allows_a_stale_local_order_once_the_venue_is_clear():
    """The mirror: the venue is the authority, so a leftover local entry does not block."""
    s = _FakeStrategy("long", position_qty=0, open_buys=[MagicMock()], venue_orders=[])
    s.set_side("short")
    assert s.side == "short"


def test_set_side_counts_in_flight_orders_as_live():
    """An order still being submitted can reach the venue, so it blocks the switch too."""
    order = MagicMock()
    order.client_order_id = "O-3"
    order.status_string.return_value = "SUBMITTED"
    s = _FakeStrategy("long", position_qty=0)
    s._cache_mock.orders_inflight.return_value = [order]
    with pytest.raises(RuntimeError, match="still live at the venue"):
        s.set_side("short")


def test_set_side_asks_the_cache_for_this_instrument_only():
    s = _FakeStrategy("long", position_qty=0)
    s.set_side("short")
    for call in (s._cache_mock.orders_open, s._cache_mock.orders_inflight):
        assert call.call_args.kwargs["instrument_id"] == "X.ALPACA"


def test_set_side_is_a_noop_when_unchanged_even_if_not_flat():
    s = _FakeStrategy("long", position_qty=100, open_buys=[MagicMock()], venue_orders=[MagicMock()])
    s.set_side("long")  # must not raise
    assert s.side == "long"


def test_side_enum_compares_and_renders_as_its_plain_string():
    """StrEnum, so existing string comparisons and log/artifact formatting keep working."""
    assert Side.LONG == "long"
    assert Side.SHORT == "short"
    assert f"{Side.LONG}" == "long"
    assert str(Side.SHORT) == "short"
    assert Side("long") is Side.LONG
    assert list(Side) == [Side.LONG, Side.SHORT]


def test_set_side_accepts_the_enum_and_stores_a_member():
    s = _FakeStrategy(Side.LONG, position_qty=0)
    s.set_side(Side.SHORT)
    assert s.side is Side.SHORT

    # A plain string is coerced to the member, not stored raw
    s = _FakeStrategy("long", position_qty=0)
    s.set_side("short")
    assert s.side is Side.SHORT


def test_set_side_rejects_an_unknown_value():
    s = _FakeStrategy("long", position_qty=0)
    with pytest.raises(ValueError, match="must be one of"):
        s.set_side("flat")


# ---------------------------------------------------------------------------
# Stop-out direction and the ratchet
# ---------------------------------------------------------------------------


def test_stop_price_sits_below_the_market_when_long_and_above_when_short():
    tick = MagicMock(price=10.0)

    long_s = _FakeStrategy("long", position_qty=500, stop_loss=0.05)
    long_s._stop_out_if_needed(tick)
    assert long_s.stop_price == pytest.approx(9.95)

    short_s = _FakeStrategy("short", position_qty=-500, stop_loss=0.05)
    short_s._stop_out_if_needed(tick)
    assert short_s.stop_price == pytest.approx(10.05)


def test_stop_out_triggers_on_the_correct_side_of_the_stop():
    long_s = _FakeStrategy("long", position_qty=500)
    long_s.stop_price = 9.95
    long_s._stop_out_if_needed(MagicMock(price=9.94))  # through the stop
    assert long_s._stopping_out is True
    assert long_s.exit_position_at_price.called

    long_s = _FakeStrategy("long", position_qty=500)
    long_s.stop_price = 9.95
    long_s._stop_out_if_needed(MagicMock(price=9.96))  # still above
    assert long_s._stopping_out is False

    short_s = _FakeStrategy("short", position_qty=-500)
    short_s.stop_price = 10.05
    short_s._stop_out_if_needed(MagicMock(price=10.06))  # through the stop
    assert short_s._stopping_out is True
    assert short_s.exit_position_at_price.called

    short_s = _FakeStrategy("short", position_qty=-500)
    short_s.stop_price = 10.05
    short_s._stop_out_if_needed(MagicMock(price=10.04))  # still below
    assert short_s._stopping_out is False


def test_stop_out_exit_price_is_aggressive_in_the_closing_direction():
    long_s = _FakeStrategy("long", position_qty=500)
    long_s.stop_price = 9.95
    long_s._stop_out_if_needed(MagicMock(price=9.90))
    assert long_s.exit_position_at_price.call_args[0][0] < 9.90  # sell below

    short_s = _FakeStrategy("short", position_qty=-500)
    short_s.stop_price = 10.05
    short_s._stop_out_if_needed(MagicMock(price=10.10))
    assert short_s.exit_position_at_price.call_args[0][0] > 10.10  # buy above


@pytest.mark.parametrize(
    "side, existing_stop, fill_px, expected",
    [
        # Long: ratchet up only
        ("long", 9.95, 10.20, 10.15),  # higher stop -> tighten
        ("long", 9.95, 9.80, 9.95),  # lower stop -> keep existing
        # Short: ratchet down only
        ("short", 10.05, 9.80, 9.85),  # lower stop -> tighten
        ("short", 10.05, 10.20, 10.05),  # higher stop -> keep existing
    ],
)
def test_entry_fill_only_ever_tightens_the_stop(side, existing_stop, fill_px, expected):
    s = _FakeStrategy(side, stop_loss=0.05)
    s.stop_price = existing_stop

    fill = MagicMock(order_side=s._entry_order_side, last_qty=100, last_px=fill_px)
    s.on_order_filled(fill)

    assert s.stop_price == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Wrong-way flatten in _reconcile
# ---------------------------------------------------------------------------


def test_long_strategy_flattens_a_negative_position_by_buying():
    s = _FakeStrategy("long", position_qty=-250)
    s._reconcile()

    side, qty, _price, tag = s._submit_limit_order.call_args[0]
    assert side == OrderSide.BUY
    assert qty == 250
    assert tag == "flatten"


def test_short_strategy_flattens_a_positive_position_by_selling():
    s = _FakeStrategy("short", position_qty=250)
    s._reconcile()

    side, qty, _price, tag = s._submit_limit_order.call_args[0]
    assert side == OrderSide.SELL
    assert qty == 250
    assert tag == "flatten"


def test_a_correct_short_is_not_flattened():
    """The regression the old `position_at_broker < 0` check would cause."""
    s = _FakeStrategy("short", position_qty=-250)
    s._reconcile()

    assert s._submit_limit_order.call_count == 0
    assert s.modify_open_order.call_count == 0


def test_a_correct_long_is_not_flattened():
    s = _FakeStrategy("long", position_qty=250)
    s._reconcile()

    assert s._submit_limit_order.call_count == 0


def test_wrong_way_flatten_cancels_the_orders_that_would_deepen_it():
    deepening = MagicMock()
    s = _FakeStrategy("long", position_qty=-250, open_sells=[deepening])
    s._reconcile()
    s.cancel_open_order.assert_called_once_with(deepening)

    deepening = MagicMock()
    s = _FakeStrategy("short", position_qty=250, open_buys=[deepening])
    s._reconcile()
    s.cancel_open_order.assert_called_once_with(deepening)


# ---------------------------------------------------------------------------
# Cancel-while-iterating
#
# Two loops iterate a set that cancel_open_order() then discards from. They only survive because
# _prune_closed() REBINDS the sets instead of mutating them in place: cancel_open_order reads
# self.open_orders, which prunes, which swaps in a fresh set -- so the loop is left iterating the
# orphaned old one. Rewriting _prune_closed as an in-place discard turns both into
# "RuntimeError: Set changed size during iteration". These drive the real cancel_open_order rather
# than the harness mock, which is what makes them able to catch that.
# ---------------------------------------------------------------------------


def _with_real_cancel(s):
    """Swap the harness mock back out for the real cancel_open_order."""
    del s.cancel_open_order  # falls back to the BaseStrategy implementation
    s.cancel_order = MagicMock()  # the nautilus Strategy call it delegates to
    return s


def test_stop_out_cancels_every_entry_while_iterating_that_same_set():
    orders = [MagicMock(is_open=True, is_flatten=False, venue_order_id=f"v{i}") for i in range(3)]
    s = _with_real_cancel(_FakeStrategy("long", position_qty=500, open_buys=orders))
    s.stop_price = 9.95

    s._stop_out_if_needed(MagicMock(price=9.90))  # through the stop

    assert s.cancel_order.call_count == 3
    assert s._buy_orders == set()


def test_wrong_way_flatten_cancels_every_deepening_order_while_iterating_that_same_set():
    orders = [MagicMock(is_open=True, is_flatten=False, venue_order_id=f"v{i}") for i in range(3)]
    s = _with_real_cancel(_FakeStrategy("long", position_qty=-250, open_sells=orders))

    s._reconcile()

    assert s.cancel_order.call_count == 3
    assert s._sell_orders == set()


# ---------------------------------------------------------------------------
# The flatten order is exempt from the entry sweeps, and cleaned up after
# ---------------------------------------------------------------------------


def _open_order(is_flatten=False, oid="v1"):
    return MagicMock(is_open=True, is_flatten=is_flatten, venue_order_id=oid)


def test_entries_to_cancel_drops_only_the_flatten_order():
    entry, flatten = _open_order(oid="entry"), _open_order(is_flatten=True, oid="flatten")
    s = _FakeStrategy("long", position_qty=0, open_buys=[entry, flatten])
    assert s.open_entries == {entry, flatten}, "it stays an entry-side order"
    assert s.entries_to_cancel == {entry}


def test_stop_out_spares_the_flatten_order():
    entry, flatten = _open_order(oid="entry"), _open_order(is_flatten=True, oid="flatten")
    s = _with_real_cancel(_FakeStrategy("long", position_qty=500, open_buys=[entry, flatten]))
    s.stop_price = 9.95

    s._stop_out_if_needed(MagicMock(price=9.90))  # through the stop

    assert s.cancel_order.call_count == 1, "only the real entry is cancelled"
    assert s.cancel_order.call_args.kwargs["order"] is entry.order
    assert s._buy_orders == {flatten}, "the flatten order must survive the stop-out sweep"


def test_the_flatten_order_is_tagged_so_the_sweeps_can_see_it():
    s = _FakeStrategy("long", position_qty=-250)
    s._reconcile()
    s._submit_limit_order.assert_called_once()
    assert s._submit_limit_order.call_args.args[3] == FLATTEN_TAG


def test_reconcile_reprices_the_existing_flatten_order_rather_than_another_entry():
    entry, flatten = _open_order(oid="entry"), _open_order(is_flatten=True, oid="flatten")
    s = _FakeStrategy("long", position_qty=-250, open_buys=[entry, flatten])

    s._reconcile()

    s._submit_limit_order.assert_not_called()
    assert s.modify_open_order.call_args.args[0] is flatten


def test_the_flatten_order_is_cancelled_once_the_position_is_no_longer_wrong_way():
    flatten = _open_order(is_flatten=True, oid="flatten")
    s = _FakeStrategy("long", position_qty=0, open_buys=[flatten])

    s._reconcile()

    s.cancel_open_order.assert_called_once_with(flatten)


def test_a_normal_entry_is_left_alone_by_that_cleanup():
    entry = _open_order(oid="entry")
    s = _FakeStrategy("long", position_qty=0, open_buys=[entry])
    s._reconcile()
    s.cancel_open_order.assert_not_called()


def test_the_flatten_order_survives_while_the_position_is_still_wrong_way():
    flatten = _open_order(is_flatten=True, oid="flatten")
    s = _FakeStrategy("long", position_qty=-250, open_buys=[flatten])
    s._reconcile()
    s.cancel_open_order.assert_not_called()


# ---------------------------------------------------------------------------
# Orders that outlived a side switch are neither entries nor exits
# ---------------------------------------------------------------------------
# The books are keyed by venue side, so after a flip a leftover short entry (a SELL) reads as a
# long's exit. `_side_changed_ns` is when the switch happened; an order initialized before it
# belongs to the side we no longer trade.


def _cancel_rejected(client_order_id, reason):
    from nautilus_trader.model.events import OrderCancelRejected

    event = MagicMock(spec=OrderCancelRejected)
    event.client_order_id = client_order_id
    event.reason = reason
    return event


def _dated_order(ts_init, venue_side=OrderSide.SELL, oid="v1"):
    order = MagicMock(is_open=True, is_flatten=False, venue_order_id=oid)
    order.order = MagicMock(ts_init=ts_init, side=venue_side)
    order.client_order_id = oid
    order.leaves_qty = 50
    return order


SWITCH_NS = 1_000
BEFORE, AFTER = SWITCH_NS - 1, SWITCH_NS + 1


def test_a_sell_that_outlived_the_switch_is_not_counted_as_an_exit():
    """The 2026-09-22 order: a short's entry, re-filed as a long's exit, never cancelled."""
    stale = _dated_order(BEFORE)
    s = _FakeStrategy("long", position_qty=0, open_sells=[stale], side_changed_ns=SWITCH_NS)
    assert stale in s.open_orders, "still tracked, so it can be cancelled"
    assert s.open_exits == set(), "must not read as position protection"
    assert s.open_exits_qty == 0
    assert s.orders_from_a_previous_side == {stale}


def test_an_order_from_after_the_switch_is_a_normal_exit():
    fresh = _dated_order(AFTER)
    s = _FakeStrategy("long", position_qty=0, open_sells=[fresh], side_changed_ns=SWITCH_NS)
    assert s.open_exits == {fresh}
    assert s.orders_from_a_previous_side == set()


def test_a_stale_order_on_the_entry_side_is_excluded_too():
    stale, fresh = _dated_order(BEFORE, OrderSide.BUY, "stale"), _dated_order(AFTER, OrderSide.BUY, "fresh")
    s = _FakeStrategy("long", position_qty=0, open_buys=[stale, fresh], side_changed_ns=SWITCH_NS)
    assert s.open_entries == {fresh}


def test_nothing_is_stale_before_the_first_switch():
    """`_side_changed_ns` is 0 until a switch happens, so ordinary runs are untouched."""
    order = _dated_order(BEFORE)
    s = _FakeStrategy("long", position_qty=0, open_sells=[order], side_changed_ns=0)
    assert s.orders_from_a_previous_side == set()
    assert s.open_exits == {order}


def test_reconcile_cancels_the_order_from_the_previous_side():
    stale = _dated_order(BEFORE)
    s = _FakeStrategy("long", position_qty=0, open_sells=[stale], side_changed_ns=SWITCH_NS)
    s._reconcile()
    s.cancel_open_order.assert_called_once_with(stale)


def test_reconcile_leaves_current_side_orders_alone():
    fresh = _dated_order(AFTER)
    s = _FakeStrategy("long", position_qty=0, open_sells=[fresh], side_changed_ns=SWITCH_NS)
    s._reconcile()
    s.cancel_open_order.assert_not_called()


def test_set_side_records_when_the_switch_happened():
    s = _FakeStrategy("long", position_qty=0)
    s._clock_mock.timestamp_ns.return_value = 12_345
    s.set_side("short")
    assert s._side_changed_ns == 12_345


def test_a_refused_switch_does_not_record_a_time():
    order = MagicMock()
    order.client_order_id = "O-1"
    order.status_string.return_value = "ACCEPTED"
    s = _FakeStrategy("long", position_qty=0, venue_orders=[order])
    with pytest.raises(RuntimeError):
        s.set_side("short")
    assert s._side_changed_ns == 0


def test_the_full_path_a_refused_cancel_takes_after_a_switch():
    """
    End to end over the 2026-09-22 sequence, minus the flip that #1 now blocks.

    The cancel is refused after the books had already dropped the order, so `on_order_event`
    re-adds it by venue side -- a SELL, while long. It must come back as neither entry nor exit,
    and the next reconcile pass must cancel it rather than leave it resting.
    """
    cache_order = MagicMock(is_open=True, side=OrderSide.SELL, status="ACCEPTED", ts_init=BEFORE)
    cache_order.client_order_id = "O-stale"
    s = _FakeStrategy("long", position_qty=0, side_changed_ns=SWITCH_NS)
    s._cache_mock.order.return_value = cache_order

    s.on_order_event(_cancel_rejected("O-stale", reason="original order pending replacement"))

    assert len(s.open_orders) == 1, "it is tracked again, which is what lets it be cancelled"
    assert s.open_exits == set(), "but never as an exit"
    assert s.open_entries == set()

    s._reconcile()
    assert s.cancel_open_order.call_count == 1
    assert s.cancel_open_order.call_args.args[0].client_order_id == "O-stale"
