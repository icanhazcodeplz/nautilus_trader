from copy import copy

from custom.strategies._side import Side
from custom.utils.precision_utils import make_Price
from nautilus_trader.model import Price


class ExitTiers:
    """
    The ladder of resting exit prices used to scale out of a position.

    A LONG position closes by selling, so its rungs ascend away from `starting_price`. A SHORT
    closes by buying, so its rungs descend. Everything else -- rung width, grid snapping, quantity
    split -- is identical in both directions.

    Assumes `starting_price` is above ~$0.30. Below that a descending ladder can walk to $0.00,
    because the one-tick floor on a rung overrides MAX_STEP_PCT once a tick is worth more than 2%
    of price. Nothing guards against it -- no instrument this trades comes near that range.
    """

    # One rung is this fraction of a mean excursion, so a 3-tier ladder spans ~1 excursion.
    STEP_FRACTION = 0.3
    MIN_STEP_PCT = 0.0002  # Never tighter than 2bp of price
    MAX_STEP_PCT = 0.02  # Never wider than 2% of price

    def __init__(self, direction: Side, quantity, starting_price, mean_variance, num_tiers, step_fraction=None):
        # Accepts the enum or its plain string, matching BaseStrategy.set_side
        self.direction = Side(direction)
        self.quantity = quantity
        if quantity < num_tiers:
            num_tiers = quantity

        tick = self._ladder_tick(self.direction, starting_price, mean_variance, num_tiers, step_fraction)
        step_size = self._get_step_size(starting_price, mean_variance, step_fraction, tick)
        raw_prices = self._get_tier_prices(num_tiers, starting_price, step_size, self.direction, tick)
        target_prices = [make_Price(price) for price in raw_prices]

        self.step_size = step_size
        # An even split across rungs, with the remainder folded into the largest clip.
        # 10 shares over 3 tiers splits [4, 3, 3], so max_qty_per_tier is 4.
        self.max_qty_per_tier = quantity // num_tiers + quantity % num_tiers
        self.prices = set(target_prices)
        self.available_prices = copy(self.prices)

    def _nearest(self, prices: set) -> Price:
        """The rung closest to the market: the lowest when long, the highest when short."""
        return min(prices) if self.direction == Side.LONG else max(prices)

    @property
    def nearest_price(self) -> Price:
        return self._nearest(self.prices)

    @property
    def nearest_available_price(self) -> Price:
        return self._nearest(self.available_prices)

    def pop_next_price(self) -> Price:
        """Take the rung nearest the market off the ladder and return it."""
        price = self.nearest_available_price
        self.available_prices.remove(price)
        return price

    @staticmethod
    def _tick_size(price: float) -> float:
        """US equities quote in cents at/above $1 and in hundredths of a cent below it."""
        return 0.01 if price >= 1.0 else 0.0001

    @classmethod
    def _ladder_tick(cls, direction, price, mean_variance, num_tiers, step_fraction=None) -> float:
        """
        One tick grid for the whole ladder, taken from whichever end is coarser.

        A ladder anchored near $1.00 can cross it, and the tick changes there (0.0001 -> 0.01).
        Sizing every rung off the anchor alone breaks either way across that line: rungs above
        $1.00 built on the fine grid are sub-penny, which SEC Rule 612 forbids and the venue
        rejects; and once rounded to cents for the venue they collide, so a 5-rung ladder silently
        becomes 4 in `self.prices`. Quantizing the whole ladder to the coarser of the two grids
        keeps every rung both legal and distinct.

        One pass is enough: 0.01 is the coarsest grid, so widening to it cannot push the far rung
        across another boundary.

        """
        tick = cls._tick_size(price)
        step = cls._get_step_size(price, mean_variance, step_fraction, tick)
        span = (num_tiers - 1) * step
        furthest = price + span if Side(direction) == Side.LONG else price - span
        return max(tick, cls._tick_size(furthest))

    @classmethod
    def _get_step_size(
        cls, price: float, mean_variance: float, step_fraction: float = None, tick: float = None
    ) -> float:
        """
        Size one rung from how far this stock actually travels, not from a price band.

        `mean_variance` is mean(|price - vwap|), so it already scales with both price and
        volatility. That is what keeps the ladder's economic width comparable at $5 and at $800,
        with no bands to maintain and no discontinuities.

        The relative floor does more than guard against zero: `VWAPBands.mean_variance` is 0.0
        until the indicator warms up, and without it a $250 name would ladder at 1-cent rungs for
        the whole warm-up.

        `tick` defaults to this price's own grid; `_ladder_tick` passes the ladder-wide one.
        """
        tick = cls._tick_size(price) if tick is None else tick
        frac = cls.STEP_FRACTION if step_fraction is None else step_fraction
        step = (mean_variance or 0.0) * frac
        step = max(step, price * cls.MIN_STEP_PCT)
        step = min(step, price * cls.MAX_STEP_PCT)
        step = max(step, tick)
        return round(round(step / tick) * tick, 10)

    @classmethod
    def _snap_up(cls, price: float, step: float, tick: float) -> float:
        """
        Lowest grid point at or above `price`, on a grid anchored at zero.

        Integer-tick arithmetic on purpose. `math.ceil(price / step)` in float can read a price
        that sits exactly on the grid as 3.0000000001 and kick the whole ladder up a rung.
        Anchoring at zero rather than re-anchoring at each dollar keeps this monotonic even for
        steps that do not divide 100 evenly, so a higher starting price can never produce a lower
        bottom rung.
        """
        price_ticks = round(price / tick)
        step_ticks = max(1, round(step / tick))
        n = -(-price_ticks // step_ticks)  # Ceil division on ints
        return round(n * step_ticks * tick, 10)

    @classmethod
    def _snap_down(cls, price: float, step: float, tick: float) -> float:
        """
        Highest grid point at or below `price`. The mirror of `_snap_up` for short ladders.

        Note the monotonicity guarantee does NOT flip sign here: floor division is non-decreasing
        in price just as ceil division is. So the short-side property reads "a lower starting_price
        can never produce a higher top rung" -- the anchor never jumps back toward the market as
        the market moves away from it, which is what would churn orders in _rolling_tiered_take.
        """
        price_ticks = round(price / tick)
        step_ticks = max(1, round(step / tick))
        n = price_ticks // step_ticks  # Floor division on ints
        return round(n * step_ticks * tick, 10)

    @classmethod
    def _get_tier_prices(
        cls, tier_count: int, starting_price: float, step_size: float, direction: Side, tick: float = None
    ) -> list[float]:
        # Every rung is built off one integer base rather than accumulating `anchor + i * step`,
        # so they all land exactly on the tick grid.
        direction = Side(direction)
        tick = cls._tick_size(starting_price) if tick is None else tick
        step_ticks = max(1, round(step_size / tick))
        if direction == Side.LONG:
            base_ticks = round(cls._snap_up(starting_price, step_size, tick) / tick)
            tier_ticks = [base_ticks + i * step_ticks for i in range(tier_count)]
        else:
            base_ticks = round(cls._snap_down(starting_price, step_size, tick) / tick)
            tier_ticks = [base_ticks - i * step_ticks for i in range(tier_count)]
        return [round(t * tick, 10) for t in tier_ticks]
