"""
Tests for side flipping in MomoStrategy (`direction_strategy` x `direction_threshold`).

The default fixture is reversion against a rolling VWAP: short above it, long below. `set_side`
only switches while flat with nothing resting, so a flip is two phases -- `_flipping` blocks new
entries and cancels the resting ones, then the side switches once the exits have taken the
position flat.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from custom.strategies._side import Side
from custom.strategies.momo import DirectionStrategy, DirectionThreshold, MomoStrategy, parse_est_time


CONFIRM = 3  # Small enough to step through by hand


PRE_OPEN = pd.Timestamp("2026-03-10 09:00", tz="US/Eastern").tz_convert("UTC")
POST_OPEN = pd.Timestamp("2026-03-10 09:31", tz="US/Eastern").tz_convert("UTC")


def _make_strategy(
    side="long",
    position_qty=0,
    rolling_vwap=100.0,
    window_full=True,
    open_orders=(),
    direction_strategy=DirectionStrategy.REVERSION,
    direction_threshold=DirectionThreshold.ROLLING_VWAP,
    now=POST_OPEN,
):
    """
    A MomoStrategy with only the surface `_flip_side_if_needed` touches.

    `__init__` is bypassed (it needs a wired nautilus Strategy), so every attribute the method
    reads is set explicitly -- which also means a rename in the real class shows up here.
    """
    s = MagicMock(spec=MomoStrategy)
    s._side = Side(side)
    s._flipping = False
    s._pending_side_signal = None
    s._side_signal_count = 0
    s._direction_threshold_value = None
    s.position_qty = position_qty

    s.direction_strategy = direction_strategy
    s.direction_threshold = direction_threshold
    s.config = MagicMock(flip_side_confirm_ticks=CONFIRM)
    s.rolling_vwap = MagicMock(value=rolling_vwap, window_full=window_full)
    s.clock = MagicMock()
    s.clock.utc_now.return_value = now

    orders = list(open_orders)
    s.open_entries = list(orders)
    s.open_orders = list(orders)
    s.cancel_open_order = MagicMock()
    s.set_side = MagicMock(side_effect=lambda new_side: setattr(s, "_side", Side(new_side)))
    s.log = MagicMock()

    # Bind the real methods under test to the mock
    for name in ("_flip_side_if_needed", "_update_direction_threshold", "_signal_for", "_count_side_signal"):
        setattr(s, name, getattr(MomoStrategy, name).__get__(s, MomoStrategy))
    return s


def _tick(price):
    return MagicMock(price=price)


def _feed(s, price, n):
    for _ in range(n):
        s._flip_side_if_needed(_tick(price))


# ---------------------------------------------------------------------------
# Arming: N consecutive ticks
# ---------------------------------------------------------------------------


def test_one_tick_across_the_vwap_does_not_arm_a_flip():
    s = _make_strategy(side="long", rolling_vwap=100.0)
    s._flip_side_if_needed(_tick(101.0))  # above -> wants SHORT
    assert s._flipping is False
    assert s._side_signal_count == 1


def test_flip_arms_on_the_nth_consecutive_tick_and_not_before():
    # Holding a position keeps the flip pending, so _flipping stays observable
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0)
    for i in range(1, CONFIRM):
        s._flip_side_if_needed(_tick(101.0))
        assert s._flipping is False, f"armed early at tick {i}"
    s._flip_side_if_needed(_tick(101.0))  # the Nth
    assert s._flipping is True


def test_a_contrary_tick_resets_the_counter():
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0)
    _feed(s, 101.0, CONFIRM - 1)  # one short of arming
    s._flip_side_if_needed(_tick(99.0))  # below -> votes LONG, resets
    assert s._side_signal_count == 1
    _feed(s, 101.0, CONFIRM - 1)  # must start over
    assert s._flipping is False
    s._flip_side_if_needed(_tick(101.0))
    assert s._flipping is True


def test_signal_agreeing_with_current_side_never_arms():
    s = _make_strategy(side="long", rolling_vwap=100.0)
    _feed(s, 99.0, CONFIRM * 3)  # below -> LONG, which we already are
    assert s._flipping is False
    s.set_side.assert_not_called()


# ---------------------------------------------------------------------------
# Aborting is symmetric -- the regression this design exists to prevent
# ---------------------------------------------------------------------------


def test_one_tick_back_does_not_abort_an_armed_flip():
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0)
    _feed(s, 101.0, CONFIRM)
    assert s._flipping is True
    s._flip_side_if_needed(_tick(99.0))  # single tick back
    assert s._flipping is True, "a lone contrary tick must not call off the flip"


def test_abort_takes_the_same_n_ticks_as_arming():
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0)
    _feed(s, 101.0, CONFIRM)
    assert s._flipping is True
    _feed(s, 99.0, CONFIRM - 1)
    assert s._flipping is True, "aborted early"
    s._flip_side_if_needed(_tick(99.0))  # the Nth contrary tick
    assert s._flipping is False
    s.set_side.assert_not_called()  # aborted, not completed


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def test_nothing_happens_until_the_rolling_window_is_full():
    s = _make_strategy(side="long", rolling_vwap=100.0, window_full=False)
    _feed(s, 101.0, CONFIRM * 3)
    assert s._flipping is False
    assert s._side_signal_count == 0
    s.set_side.assert_not_called()


@pytest.mark.parametrize("mode", [DirectionStrategy.LONG_ONLY, DirectionStrategy.SHORT_ONLY])
def test_inert_unless_a_flipping_strategy_is_selected(mode):
    s = _make_strategy(side="long", rolling_vwap=100.0, direction_strategy=mode)
    _feed(s, 101.0, CONFIRM * 3)
    assert s._flipping is False
    assert s._side_signal_count == 0
    s.set_side.assert_not_called()
    s.cancel_open_order.assert_not_called()


# ---------------------------------------------------------------------------
# Momentum: the same machinery with "above" meaning LONG
# ---------------------------------------------------------------------------


def test_momentum_stays_long_above_the_vwap():
    s = _make_strategy(side="long", rolling_vwap=100.0, direction_strategy=DirectionStrategy.MOMENTUM)
    _feed(s, 101.0, CONFIRM * 3)
    assert s._flipping is False
    s.set_side.assert_not_called()


def test_momentum_flips_short_below_the_vwap():
    s = _make_strategy(side="long", rolling_vwap=100.0, direction_strategy=DirectionStrategy.MOMENTUM)
    _feed(s, 99.0, CONFIRM)
    s.set_side.assert_called_once_with(Side.SHORT)


def test_momentum_flips_long_above_the_vwap_from_short():
    s = _make_strategy(side="short", rolling_vwap=100.0, direction_strategy=DirectionStrategy.MOMENTUM)
    _feed(s, 101.0, CONFIRM)
    s.set_side.assert_called_once_with(Side.LONG)


# ---------------------------------------------------------------------------
# Ties: a tick exactly on the threshold is ignored
# ---------------------------------------------------------------------------


def test_tie_tick_neither_advances_nor_resets_the_counter():
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0)
    _feed(s, 101.0, CONFIRM - 1)
    s._flip_side_if_needed(_tick(100.0))  # exactly on the line
    assert s._side_signal_count == CONFIRM - 1
    assert s._flipping is False
    s._flip_side_if_needed(_tick(101.0))  # the Nth above tick
    assert s._flipping is True


def test_tie_tick_still_cancels_entries_and_completes_an_armed_flip():
    resting = MagicMock(is_open=True, venue_order_id="v1")
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0, open_orders=[resting])
    _feed(s, 101.0, CONFIRM)
    assert s._flipping is True
    s._flip_side_if_needed(_tick(100.0))
    assert s.cancel_open_order.call_count == 2, "tie tick must not skip the cancel re-issue"

    s.position_qty = 0
    s.open_orders = []
    s._flip_side_if_needed(_tick(100.0))
    s.set_side.assert_called_once_with(Side.SHORT)


# ---------------------------------------------------------------------------
# Open threshold: first tick at/after 9:30 ET, set once
# ---------------------------------------------------------------------------


def _open_strategy(side="long", direction_strategy=DirectionStrategy.MOMENTUM, now=POST_OPEN, **kw):
    return _make_strategy(
        side=side,
        direction_strategy=direction_strategy,
        direction_threshold=DirectionThreshold.OPEN,
        now=now,
        **kw,
    )


def test_open_threshold_is_not_set_before_the_market_opens():
    s = _open_strategy(now=PRE_OPEN)
    _feed(s, 101.0, CONFIRM * 3)
    assert s._direction_threshold_value is None
    assert s._side_signal_count == 0
    s.set_side.assert_not_called()


def test_open_threshold_is_the_first_tick_after_the_open_and_never_moves():
    s = _open_strategy(now=PRE_OPEN)
    s._flip_side_if_needed(_tick(98.0))  # pre-open tick is not the open
    assert s._direction_threshold_value is None

    s.clock.utc_now.return_value = POST_OPEN
    s._flip_side_if_needed(_tick(100.0))
    assert s._direction_threshold_value == 100.0
    s._flip_side_if_needed(_tick(105.0))
    assert s._direction_threshold_value == 100.0


def test_opening_tick_is_a_tie_and_does_not_vote():
    s = _open_strategy(side="short")
    s._flip_side_if_needed(_tick(100.0))  # sets the open, and is exactly on it
    assert s._side_signal_count == 0


def test_momentum_on_open_goes_long_above_the_open():
    s = _open_strategy(side="short")
    s._flip_side_if_needed(_tick(100.0))  # the open
    _feed(s, 101.0, CONFIRM)
    s.set_side.assert_called_once_with(Side.LONG)


def test_momentum_on_open_goes_short_below_the_open():
    s = _open_strategy(side="long")
    s._flip_side_if_needed(_tick(100.0))
    _feed(s, 99.0, CONFIRM)
    s.set_side.assert_called_once_with(Side.SHORT)


def test_reversion_on_open_goes_short_above_the_open():
    s = _open_strategy(side="long", direction_strategy=DirectionStrategy.REVERSION)
    s._flip_side_if_needed(_tick(100.0))
    _feed(s, 101.0, CONFIRM)
    s.set_side.assert_called_once_with(Side.SHORT)


def test_open_threshold_ignores_the_rolling_vwap():
    s = _open_strategy(side="long", rolling_vwap=50.0, window_full=False)
    s._flip_side_if_needed(_tick(100.0))
    assert s._direction_threshold_value == 100.0


# ---------------------------------------------------------------------------
# Completing the flip
# ---------------------------------------------------------------------------


def test_flip_completes_only_once_flat_with_nothing_resting():
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0)
    _feed(s, 101.0, CONFIRM)
    assert s._flipping is True
    s.set_side.assert_not_called()  # still holding stock

    s.position_qty = 0  # exits filled
    s._flip_side_if_needed(_tick(101.0))
    s.set_side.assert_called_once_with(Side.SHORT)
    assert s._flipping is False


def test_flip_does_not_complete_while_an_order_still_rests():
    resting = MagicMock(is_open=True, venue_order_id="v1")
    s = _make_strategy(side="long", position_qty=0, rolling_vwap=100.0, open_orders=[resting])
    _feed(s, 101.0, CONFIRM)
    assert s._flipping is True
    s.set_side.assert_not_called(), "open_orders non-empty; set_side would raise"


def test_flat_and_clear_flips_immediately_on_the_nth_tick():
    """Startup is flat with nothing resting, so the first confirmed signal flips straight through."""
    s = _make_strategy(side="long", position_qty=0, rolling_vwap=100.0)
    _feed(s, 101.0, CONFIRM)
    s.set_side.assert_called_once_with(Side.SHORT)
    assert s._flipping is False


def test_short_side_flips_back_to_long_below_the_vwap():
    s = _make_strategy(side="short", position_qty=0, rolling_vwap=100.0)
    _feed(s, 99.0, CONFIRM)
    s.set_side.assert_called_once_with(Side.LONG)


# ---------------------------------------------------------------------------
# Resting entries
# ---------------------------------------------------------------------------


def test_resting_entries_are_cancelled_and_recancelled_each_tick():
    """
    cancel_open_order is a silent no-op until the venue id lands, and a rejected cancel can put
    the order back -- so the cancel must be re-issued rather than done once.
    """
    resting = MagicMock(is_open=True, venue_order_id="v1")
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0, open_orders=[resting])
    _feed(s, 101.0, CONFIRM)
    assert s.cancel_open_order.call_count == 1

    s._flip_side_if_needed(_tick(101.0))
    assert s.cancel_open_order.call_count == 2, "cancel not re-issued on the next tick"


def test_no_cancels_are_issued_before_the_flip_arms():
    resting = MagicMock(is_open=True, venue_order_id="v1")
    s = _make_strategy(side="long", position_qty=100, rolling_vwap=100.0, open_orders=[resting])
    _feed(s, 101.0, CONFIRM - 1)
    s.cancel_open_order.assert_not_called()


# ---------------------------------------------------------------------------
# What _flipping does to the rest of the tick
# ---------------------------------------------------------------------------


def _tick_strategy(flipping, market_open_only=False, now=POST_OPEN, stop_entries_after=None):
    """A MomoStrategy mock wired for the real _on_trade_tick."""
    s = MagicMock(spec=MomoStrategy)
    s._stopping_out = False
    s._flipping = flipping
    s.market_open_only = market_open_only
    s._stop_entries_after = parse_est_time(stop_entries_after)
    s.is_long = True
    s.is_short = False
    s.exposure = 100
    s.max_position_allowed = 1000
    s.open_entries = set()
    s.open_exits_qty = 0
    s.last_entry_price = 10.0
    s.take_profit = 0.10
    s.vwap = MagicMock(low=9.0, high=11.0)
    s.config = MagicMock(
        only_buy_if_macd_positive=False,
        random_entry=False,
        trailing_entry_order=False,
        trailing_take=True,
        simple_take=False,
        trade_size=50,
    )
    s._ADJUST_ENTRIES_ONLY_EVERY_NS = 0
    s._ADJUST_EXITS_ONLY_EVERY_NS = 0
    s._last_entry_adjustment_ns = 0
    s._last_exit_adjustment_ns = 0
    s.clock = MagicMock()
    s.clock.timestamp_ns.return_value = 10**12
    s.clock.utc_now.return_value = now
    s._on_first_tick = MagicMock()
    s._entries_stopped_for_the_day = MomoStrategy._entries_stopped_for_the_day.__get__(s, MomoStrategy)
    s._flip_side_if_needed = MagicMock()  # exercised on its own above
    s._rolling_tiered_take = MagicMock()
    s.enter = MagicMock()
    s._on_trade_tick = MomoStrategy._on_trade_tick.__get__(s, MomoStrategy)
    return s


def test_flipping_blocks_entries():
    s = _tick_strategy(flipping=True)
    s._on_trade_tick(MagicMock(price=8.0))  # below vwap.low, would normally enter when long
    s.enter.assert_not_called()


def test_not_flipping_still_enters():
    s = _tick_strategy(flipping=False)
    s._on_trade_tick(MagicMock(price=8.0))
    s.enter.assert_called_once()


def test_take_ladder_keeps_running_while_flipping():
    """
    The one place _flipping deliberately differs from _stopping_out.

    Suppressing the ladder here would stop the exits being managed, and a flip waits on exactly
    those exits to reach flat -- so it would never complete.
    """
    s = _tick_strategy(flipping=True)
    s._on_trade_tick(MagicMock(price=8.0))
    s._rolling_tiered_take.assert_called_once()


def test_stopping_out_still_suppresses_the_take_ladder():
    """Pins the existing behavior, so the _flipping carve-out above can't be over-applied."""
    s = _tick_strategy(flipping=False)
    s._stopping_out = True
    s._on_trade_tick(MagicMock(price=8.0))
    s._rolling_tiered_take.assert_not_called()


# ---------------------------------------------------------------------------
# The "open" threshold blocks trading until the market is open
# ---------------------------------------------------------------------------


def test_open_threshold_blocks_all_trading_before_the_open():
    s = _tick_strategy(flipping=False, market_open_only=True, now=PRE_OPEN)
    s._on_trade_tick(MagicMock(price=8.0))
    s.enter.assert_not_called()
    s._rolling_tiered_take.assert_not_called()
    # The flip check still runs pre-open so the threshold keeps tracking; the `open`
    # threshold guards on is_market_open itself, so no side can change before 9:30.
    s._flip_side_if_needed.assert_called_once()


def test_open_threshold_trades_normally_after_the_open():
    s = _tick_strategy(flipping=False, market_open_only=True, now=POST_OPEN)
    s._on_trade_tick(MagicMock(price=8.0))
    s.enter.assert_called_once()


# ---------------------------------------------------------------------------
# stop_entries_after: no new entries once the ET clock reaches the cutoff
# ---------------------------------------------------------------------------


def _et(hhmm):
    return pd.Timestamp(f"2026-03-10 {hhmm}", tz="US/Eastern").tz_convert("UTC")


def test_entries_allowed_before_the_cutoff():
    s = _tick_strategy(flipping=False, now=_et("09:30:59"), stop_entries_after="09:31")
    s._on_trade_tick(MagicMock(price=8.0))
    s.enter.assert_called_once()


def test_entries_blocked_at_and_after_the_cutoff():
    for hhmm in ("09:31:00", "09:31:01", "15:59:00"):
        s = _tick_strategy(flipping=False, now=_et(hhmm), stop_entries_after="09:31")
        s._on_trade_tick(MagicMock(price=8.0))
        s.enter.assert_not_called(), hhmm


def test_exit_ladder_keeps_running_after_the_cutoff():
    s = _tick_strategy(flipping=False, now=_et("10:00:00"), stop_entries_after="09:31")
    s._on_trade_tick(MagicMock(price=8.0))
    s._rolling_tiered_take.assert_called_once()


def test_no_cutoff_when_unset():
    s = _tick_strategy(flipping=False, now=_et("15:59:00"), stop_entries_after=None)
    s._on_trade_tick(MagicMock(price=8.0))
    s.enter.assert_called_once()


@pytest.mark.parametrize("bad", ["9", "09:31:00", "nine", "25:00"])
def test_bad_cutoff_string_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_est_time(bad)
