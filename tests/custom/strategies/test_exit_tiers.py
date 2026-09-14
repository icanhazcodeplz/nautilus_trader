"""
Tests for the exit ladder in custom/strategies/_exit_tiers.py.

The ladder used to size its rungs from a hardcoded price-band table, which made the rungs
progressively tighter in relative terms as price rose (1.00% of price at $0.50 but 0.035% at $999)
and jumped discontinuously at each band edge. Rungs are now sized from `mean_variance`, so most of
what is pinned here is about width staying comparable across the whole price range.

A LONG ladder ascends from `starting_price` (selling out of a long); a SHORT ladder descends
(buying to cover). Tests that do not care which way it runs are parametrized over both.

Nothing here goes below ~$0.30: the ladder assumes prices above that, so sub-penny behaviour is
deliberately neither handled nor tested.
"""

import pytest

from custom.strategies._exit_tiers import ExitTiers
from custom.strategies._side import Side


BOTH = ("long", "short")


# A typical name moves about 1% of its price away from vwap
def _realistic_mv(price):
    return price * 0.01


def _floats(tiers):
    return sorted(float(p) for p in tiers.prices)


# ---------------------------------------------------------------------------
# Scaling across the price range -- the point of the change
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", BOTH)
@pytest.mark.parametrize("price", [5, 20, 50, 99, 150, 250, 500, 800, 1500, 3000])
def test_ladder_width_stays_a_comparable_fraction_of_price(price, direction):
    """
    A 3-rung ladder should span a similar % of price at $5 and at $3000.

    With STEP_FRACTION=0.3 and a 1%-of-price excursion, one rung is ~0.3% and the span between
    the bottom and top rung of a 3-tier ladder is ~0.6%.
    """
    tiers = ExitTiers(direction, quantity=300, starting_price=price, mean_variance=_realistic_mv(price), num_tiers=3)
    prices = _floats(tiers)
    span_pct = (prices[-1] - prices[0]) / price * 100

    assert 0.4 <= span_pct <= 0.9, f"${price} {direction}: span {span_pct:.3f}% of price"


@pytest.mark.parametrize("direction", BOTH)
def test_below_about_three_dollars_the_penny_tick_sets_the_floor(direction):
    """
    Known and unavoidable: a 1-cent tick is 0.5% of a $2 stock, so rungs cannot be relatively as
    tight down here as they are higher up. Documented rather than worked around -- the ladder is
    as tight as the tick grid allows.
    """
    price = 2.0
    step = ExitTiers._get_step_size(price, _realistic_mv(price))
    assert step == 0.01, "should be pinned to one tick, not to the variance-derived 0.006"

    tiers = ExitTiers(direction, quantity=300, starting_price=price, mean_variance=_realistic_mv(price), num_tiers=3)
    prices = _floats(tiers)
    assert (prices[-1] - prices[0]) / price * 100 == pytest.approx(1.0)


def test_step_has_no_discontinuity_at_the_old_band_edges():
    """The old table stepped at $1, $10, $100, $300 and $1000 -- $1000 was a 35x cliff."""
    for edge in (10.0, 100.0, 300.0, 1000.0):
        below = ExitTiers._get_step_size(edge - 0.01, _realistic_mv(edge - 0.01))
        above = ExitTiers._get_step_size(edge + 0.01, _realistic_mv(edge + 0.01))
        ratio = max(below, above) / min(below, above)
        assert ratio < 1.10, f"${edge}: step jumps {below} -> {above} ({ratio:.1f}x)"


def test_step_responds_to_volatility_at_the_same_price():
    """A quiet and a volatile $250 name should not get the same ladder."""
    quiet = ExitTiers._get_step_size(250.0, 250 * 0.003)
    volatile = ExitTiers._get_step_size(250.0, 250 * 0.02)
    assert volatile > quiet * 2


def test_step_is_monotonic_in_mean_variance():
    steps = [ExitTiers._get_step_size(250.0, mv) for mv in (0.0, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0)]
    assert steps == sorted(steps)


# ---------------------------------------------------------------------------
# Floors and caps
# ---------------------------------------------------------------------------


def test_warm_up_with_zero_variance_still_gives_a_sane_ladder():
    """
    VWAPBands.mean_variance is 0.0 until it warms up.

    Without a floor relative to price this laddered at 1-cent rungs, which at $250 is 0.004% --
    the exact too-tight failure the change exists to fix.
    """
    step = ExitTiers._get_step_size(250.0, 0.0)
    assert step == pytest.approx(250.0 * ExitTiers.MIN_STEP_PCT, abs=0.01)
    assert step >= 5 * ExitTiers._tick_size(250.0)


def test_step_is_capped_when_variance_blows_out():
    step = ExitTiers._get_step_size(250.0, 500.0)  # absurd excursion, e.g. after a halt
    assert step <= 250.0 * ExitTiers.MAX_STEP_PCT


@pytest.mark.parametrize("price", [0.35, 0.50, 0.99])
def test_sub_dollar_prices_use_the_finer_tick(price):
    assert ExitTiers._tick_size(price) == 0.0001
    step = ExitTiers._get_step_size(price, _realistic_mv(price))
    assert step >= 0.0001


def test_step_never_goes_below_one_tick():
    assert ExitTiers._get_step_size(1.00, 0.0) >= 0.01
    assert ExitTiers._get_step_size(0.35, 0.0) >= 0.0001


# ---------------------------------------------------------------------------
# Grid anchoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step", [0.01, 0.05, 0.10, 0.20, 0.25, 0.35, 0.75, 2.40])
def test_long_bottom_rung_never_moves_down_as_the_start_price_rises(step):
    """
    The old per-dollar anchor ran backwards: anchors went 500.70 -> 501.05 -> 501.00, so a higher
    starting_price produced a LOWER bottom rung. Steps that do not divide 100 evenly (0.35 here)
    are what triggered it.
    """
    previous = 0.0
    for cents in range(0, 600):
        start = 500.00 + cents / 100
        bottom = ExitTiers._snap_up(start, step, 0.01)
        assert bottom >= previous, f"step={step} start={start}: bottom dropped {previous} -> {bottom}"
        previous = bottom


@pytest.mark.parametrize("step", [0.01, 0.05, 0.10, 0.20, 0.25, 0.35, 0.75, 2.40])
def test_short_top_rung_never_moves_down_as_the_start_price_rises(step):
    """
    The short mirror of the invariant above -- and note it does NOT flip sign.

    Floor division is non-decreasing in price just as ceil division is, so `_snap_down` is also
    monotonic upward. Read from the short side that means a *lower* starting_price never yields a
    *higher* top rung: the anchor never jumps back toward the market as the market runs away.
    """
    previous = 0.0
    for cents in range(0, 600):
        start = 500.00 + cents / 100
        top = ExitTiers._snap_down(start, step, 0.01)
        assert top >= previous, f"step={step} start={start}: top dropped {previous} -> {top}"
        previous = top


@pytest.mark.parametrize("direction", BOTH)
@pytest.mark.parametrize("step", [0.01, 0.10, 0.35, 0.75])
def test_rungs_are_evenly_spaced_by_exactly_one_step(step, direction):
    prices = sorted(ExitTiers._get_tier_prices(5, 500.00, step, direction))
    gaps = [round(b - a, 10) for a, b in zip(prices, prices[1:])]
    assert gaps == [pytest.approx(step)] * 4


def test_long_bottom_rung_is_never_below_the_starting_price():
    for cents in range(0, 300):
        start = 250.00 + cents / 100
        tiers = ExitTiers("long", quantity=99, starting_price=start, mean_variance=2.5, num_tiers=3)
        assert min(float(p) for p in tiers.prices) >= start


def test_short_top_rung_is_never_above_the_starting_price():
    for cents in range(0, 300):
        start = 250.00 + cents / 100
        tiers = ExitTiers("short", quantity=99, starting_price=start, mean_variance=2.5, num_tiers=3)
        assert max(float(p) for p in tiers.prices) <= start


@pytest.mark.parametrize("snap_fn", [ExitTiers._snap_up, ExitTiers._snap_down], ids=["snap_up", "snap_down"])
def test_a_price_already_on_the_grid_is_not_kicked_off_its_rung(snap_fn):
    """Guards the float-ceil hazard that integer-tick arithmetic exists to avoid."""
    for step in (0.10, 0.25, 0.35, 0.75):
        for n in range(1, 40):
            on_grid = round(n * step, 10)
            assert snap_fn(on_grid, step, 0.01) == pytest.approx(on_grid)


@pytest.mark.parametrize("direction", BOTH)
@pytest.mark.parametrize("price", [5.0, 50.0, 250.0, 800.0])
def test_rungs_land_exactly_on_the_tick_grid(price, direction):
    tiers = ExitTiers(direction, quantity=99, starting_price=price, mean_variance=_realistic_mv(price), num_tiers=4)
    for p in tiers.prices:
        cents = float(p) * 100
        assert cents == pytest.approx(round(cents), abs=1e-6), f"{p} is not a whole number of cents"


@pytest.mark.parametrize(
    "direction, price_a, price_b",
    [("long", 250.00, 250.01), ("short", 250.75, 250.74)],
)
def test_single_tier_is_snapped_like_any_other(direction, price_a, price_b):
    """
    The old code short-circuited tier_count == 1 and skipped snapping entirely, so the lone rung
    moved on every tick of vwap.high and churned modifies.
    """
    a = ExitTiers._get_tier_prices(1, price_a, 0.75, direction)
    b = ExitTiers._get_tier_prices(1, price_b, 0.75, direction)
    assert a == b


# ---------------------------------------------------------------------------
# The $1.00 tick boundary
#
# _tick_size changes at $1.00 and so does Price precision. A ladder that crosses the line has to
# be quantized to one grid or it either emits sub-penny prices above $1.00 (illegal under SEC Rule
# 612, rejected by the venue) or rounds distinct rungs onto the same cent and loses them to the set.
# ---------------------------------------------------------------------------


def _assert_no_sub_penny_above_a_dollar(tiers):
    for p in tiers.prices:
        if float(p) >= 1.0:
            cents = float(p) * 100
            assert cents == pytest.approx(round(cents), abs=1e-9), f"{p} is sub-penny at/above $1"


def test_ascending_ladder_does_not_lose_a_rung_crossing_a_dollar():
    """Regression: this built ['0.9990','0.9994','0.9998','1.00','1.00'] -- 4 rungs, not 5."""
    tiers = ExitTiers("long", quantity=300, starting_price=0.9990, mean_variance=0.009990, num_tiers=5)
    assert len(tiers.prices) == 5
    _assert_no_sub_penny_above_a_dollar(tiers)


def test_descending_ladder_does_not_lose_a_rung_crossing_a_dollar():
    """
    Precision is deliberately NOT asserted to be uniform here.

    make_Price picks it per value, so the rung below the line is precision 4 ('0.9900') while the
    rest are precision 2. That is cosmetic: Price compares and hashes equal across precisions, and
    _ladder_tick has already quantized every rung to the cent grid, so none of them collide.
    """
    tiers = ExitTiers("short", quantity=300, starting_price=1.02, mean_variance=0.0333, num_tiers=4)
    assert len(tiers.prices) == 4
    assert max(float(p) for p in tiers.prices) <= 1.02
    _assert_no_sub_penny_above_a_dollar(tiers)


# ---------------------------------------------------------------------------
# Quantity split
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", BOTH)
@pytest.mark.parametrize(
    "num_tiers, qty, expected",
    [
        (1, 100, 100),  # Single rung takes the whole clip
        (2, 100, 50),  # Even split when divisible
        (3, 99, 33),
        (3, 100, 34),  # Remainder folds into the largest clip
        (5, 103, 23),
    ],
)
def test_max_qty_per_tier(num_tiers, qty, expected, direction):
    tiers = ExitTiers(direction, quantity=qty, starting_price=10.0, mean_variance=0.1, num_tiers=num_tiers)
    assert tiers.max_qty_per_tier == expected


@pytest.mark.parametrize("direction", BOTH)
def test_tier_count_is_capped_by_share_count(direction):
    """Known limitation: at high prices a small clip silently gets fewer rungs."""
    tiers = ExitTiers(direction, quantity=2, starting_price=800.0, mean_variance=8.0, num_tiers=3)
    assert len(tiers.prices) == 2


# ---------------------------------------------------------------------------
# Nearest-the-market accessors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", BOTH)
def test_nearest_price_is_min_for_long_and_max_for_short(direction):
    tiers = ExitTiers(direction, quantity=300, starting_price=100.0, mean_variance=1.0, num_tiers=4)
    expected = min(tiers.prices) if direction == "long" else max(tiers.prices)
    assert tiers.nearest_price == expected
    assert tiers.nearest_available_price == expected


@pytest.mark.parametrize("direction", BOTH)
def test_pop_next_price_removes_from_available_but_not_from_prices(direction):
    tiers = ExitTiers(direction, quantity=300, starting_price=100.0, mean_variance=1.0, num_tiers=4)
    price = tiers.pop_next_price()
    assert price not in tiers.available_prices
    assert price in tiers.prices
    assert len(tiers.available_prices) == 3


@pytest.mark.parametrize("direction", BOTH)
def test_pop_next_price_walks_outward_from_the_market(direction):
    tiers = ExitTiers(direction, quantity=300, starting_price=100.0, mean_variance=1.0, num_tiers=4)
    popped = [float(tiers.pop_next_price()) for _ in range(4)]
    assert popped == sorted(popped, reverse=(direction == "short"))


@pytest.mark.parametrize("direction", BOTH)
def test_pop_next_price_raises_once_the_ladder_is_drained(direction):
    tiers = ExitTiers(direction, quantity=300, starting_price=100.0, mean_variance=1.0, num_tiers=2)
    tiers.pop_next_price()
    tiers.pop_next_price()
    with pytest.raises(ValueError):
        tiers.pop_next_price()


def test_direction_accepts_the_enum_and_the_bare_string():
    a = ExitTiers(Side.SHORT, quantity=300, starting_price=100.0, mean_variance=1.0, num_tiers=3)
    b = ExitTiers("short", quantity=300, starting_price=100.0, mean_variance=1.0, num_tiers=3)
    assert a.direction is Side.SHORT and b.direction is Side.SHORT
    assert a.prices == b.prices


def test_direction_is_required():
    with pytest.raises(TypeError):
        ExitTiers(quantity=300, starting_price=100.0, mean_variance=1.0, num_tiers=3)
