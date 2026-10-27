"""
Tests for `force_exit_at`: at that Eastern time an open position is stopped out regardless of
price, and no new entries are placed from then on.
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from custom.strategies.momo import MomoStrategy
from tests.custom.strategies.test_base_side import _FakeStrategy
from tests.custom.strategies.test_rolling_vwap_reversion import _config
from tests.custom.strategies.test_rolling_vwap_reversion import _et
from tests.custom.strategies.test_rolling_vwap_reversion import _tick_strategy


# ---------------------------------------------------------------------------
# BaseStrategy: a due force-exit stops out whatever the price
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("side", "position_qty"), [("long", 100), ("short", -100)])
def test_a_due_force_exit_stops_out_with_the_price_nowhere_near_the_stop(side, position_qty):
    s = _FakeStrategy(side, position_qty=position_qty, stop_loss=3.6)
    s._force_exit_due = lambda: True

    s._stop_out_if_needed(MagicMock(price=10.0))

    assert s._stopping_out is True
    s.exit_position_at_price.assert_called_once()


def test_no_force_exit_leaves_a_position_alone_inside_its_stop():
    s = _FakeStrategy("long", position_qty=100, stop_loss=3.6)

    s._stop_out_if_needed(MagicMock(price=10.0))

    assert s._stopping_out is False
    s.exit_position_at_price.assert_not_called()


def test_a_due_force_exit_does_nothing_when_flat():
    s = _FakeStrategy("long", position_qty=0)
    s._force_exit_due = lambda: True

    s._stop_out_if_needed(MagicMock(price=10.0))

    s.exit_position_at_price.assert_not_called()


# ---------------------------------------------------------------------------
# MomoStrategy: when force_exit_at is due, and that it blocks entries
# ---------------------------------------------------------------------------


def test_force_exit_is_due_at_and_after_the_time_only():
    assert not _tick_strategy(flipping=False, now=_et("09:35:59"), force_exit_at="09:36")._force_exit_due()
    for hhmm in ("09:36:00", "09:36:01", "15:59:00"):
        assert _tick_strategy(flipping=False, now=_et(hhmm), force_exit_at="09:36")._force_exit_due(), hhmm


def test_force_exit_is_resolved_again_on_a_new_day():
    # Across the spring-forward DST change too, so the cutoff is not carried over as a fixed offset
    s = _tick_strategy(flipping=False, now=_et("09:36:00"), force_exit_at="09:36")
    assert s._force_exit_due()
    for now, due in [("2026-03-09 09:00", False), ("2026-03-08 09:35:59", False), ("2026-03-08 09:36", True)]:
        s.clock.timestamp_ns.return_value = pd.Timestamp(now, tz="US/Eastern").value
        assert s._force_exit_due() is due, now


def test_force_exit_is_never_due_when_unset():
    assert not _tick_strategy(flipping=False, now=_et("15:59:00"), force_exit_at=None)._force_exit_due()


def test_entries_are_blocked_once_force_exit_is_due():
    s = _tick_strategy(flipping=False, now=_et("09:36:00"), force_exit_at="09:36")
    s._on_trade_tick(MagicMock(price=8.0))  # would enter otherwise (see test_not_flipping_still_enters)
    s.enter.assert_not_called()


def test_entries_still_allowed_before_force_exit():
    s = _tick_strategy(flipping=False, now=_et("09:35:59"), force_exit_at="09:36")
    s._on_trade_tick(MagicMock(price=8.0))
    s.enter.assert_called_once()


def test_a_start_at_or_after_force_exit_is_rejected():
    for start in ("09:36", "09:37"):
        with pytest.raises(ValueError, match="force_exit_at"):
            MomoStrategy(config=_config(start_trading_at=start, force_exit_at="09:36"), symbol="AMZN")


def test_a_start_before_force_exit_is_accepted():
    MomoStrategy(config=_config(start_trading_at="09:29:45", force_exit_at="09:36"), symbol="AMZN")
