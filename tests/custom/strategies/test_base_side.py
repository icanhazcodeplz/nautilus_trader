"""
Tests for long/short side support in BaseStrategy.

The critical property is that `side == "long"` behaves exactly as the long-only implementation
did, so the old cap formulas are pinned here literally and compared against the new ones.
"""

from unittest.mock import MagicMock

import pytest

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
        stop_loss=0.05,
    ):
        self._side = Side(side)
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
        self._last_stop_out_attempt = 0
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
        self._cache_mock.orders_open.return_value = []
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


def test_set_side_raises_when_flat_but_orders_are_resting():
    s = _FakeStrategy("long", position_qty=0, open_buys=[MagicMock()])
    with pytest.raises(RuntimeError, match="open order"):
        s.set_side("short")
    assert s.side == "long"


def test_set_side_is_a_noop_when_unchanged_even_if_not_flat():
    s = _FakeStrategy("long", position_qty=100, open_buys=[MagicMock()])
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
    orders = [MagicMock(is_open=True, venue_order_id=f"v{i}") for i in range(3)]
    s = _with_real_cancel(_FakeStrategy("long", position_qty=500, open_buys=orders))
    s.stop_price = 9.95

    s._stop_out_if_needed(MagicMock(price=9.90))  # through the stop

    assert s.cancel_order.call_count == 3
    assert s._buy_orders == set()


def test_wrong_way_flatten_cancels_every_deepening_order_while_iterating_that_same_set():
    orders = [MagicMock(is_open=True, venue_order_id=f"v{i}") for i in range(3)]
    s = _with_real_cancel(_FakeStrategy("long", position_qty=-250, open_sells=orders))

    s._reconcile()

    assert s.cancel_order.call_count == 3
    assert s._sell_orders == set()
