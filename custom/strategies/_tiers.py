from copy import copy

from custom.utils.precision_utils import make_Price


class Tiers:
    # One rung is this fraction of a mean excursion, so a 3-tier ladder spans ~1 excursion.
    STEP_FRACTION = 0.3
    MIN_STEP_PCT = 0.0002  # Never tighter than 2bp of price
    MAX_STEP_PCT = 0.02  # Never wider than 2% of price

    def __init__(self, quantity, starting_price, mean_variance, num_tiers, step_fraction=None):
        self.quantity = quantity
        if quantity < num_tiers:
            num_tiers = quantity

        step_size = self._get_step_size(starting_price, mean_variance, step_fraction)
        target_prices = self._get_tier_prices(num_tiers, starting_price, step_size)
        target_prices = [make_Price(price) for price in target_prices]
        target_qtys = self._get_tier_quantities(num_tiers, quantity)
        self.step_size = step_size
        self.max_qty_per_tier = max(target_qtys)
        self.prices = set(target_prices)
        self.available_prices = copy(self.prices)

    @staticmethod
    def _tick_size(price: float) -> float:
        """Mirrors precision_utils.make_Price: precision 2 at/above $1, else 4."""
        return 0.01 if price >= 1.0 else 0.0001

    @classmethod
    def _get_step_size(cls, price: float, mean_variance: float, step_fraction: float = None) -> float:
        """
        Size one rung from how far this stock actually travels, not from a price band.

        `mean_variance` is mean(|price - vwap|), so it already scales with both price and
        volatility. That is what keeps the ladder's economic width comparable at $5 and at $800,
        with no bands to maintain and no discontinuities.

        The relative floor does more than guard against zero: `VWAPBands.mean_variance` is 0.0
        until the indicator warms up, and without it a $250 name would ladder at 1-cent rungs for
        the whole warm-up.
        """
        tick = cls._tick_size(price)
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
    def _get_tier_prices(cls, tier_count: int, low_price: float, step_size: float) -> list[float]:
        # Every rung is built off one integer base rather than accumulating `lowest + i * step`,
        # so they all land exactly on the tick grid.
        tick = cls._tick_size(low_price)
        step_ticks = max(1, round(step_size / tick))
        base_ticks = round(cls._snap_up(low_price, step_size, tick) / tick)
        return [round((base_ticks + i * step_ticks) * tick, 10) for i in range(tier_count)]

    @staticmethod
    def _get_tier_quantities(tier_count: int, qty: int) -> list[int]:
        # TODO: not using this functionality. Remove?
        if tier_count == 1:
            return [qty]
        # For most bins, use the same qty for each bin
        qty_list = [int(qty / tier_count)] * (tier_count - 1)
        # Fill in remainder at the front, so the largest clip exits at the rung nearest the market
        remainder = qty - sum(qty_list)
        qty_list = [remainder] + qty_list
        return qty_list
