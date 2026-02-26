from copy import copy

import math


def market_round_up(price: float) -> float:
    if price < 1.0:
        raise NotImplementedError("Market rounding not implemented for penny stocks")
    return math.ceil(price * 100) / 100


class Tiers:
    def __init__(self, quantity, starting_price, mean_variance, num_tiers, instrument):
        self.quantity = quantity
        if quantity < num_tiers:
            num_tiers = quantity

        step_size = self._get_step_size(starting_price, mean_variance)
        target_prices = self._get_tier_prices(num_tiers, starting_price, step_size)
        target_prices = [instrument.make_price(price) for price in target_prices]
        target_qtys = self._get_tier_quantities(num_tiers, quantity)
        self.max_qty_per_tier = max(target_qtys)
        self.prices = set(target_prices)
        self.available_prices = copy(self.prices)

    @staticmethod
    def _get_step_size(price: float, mean_variance: float) -> float:
        # TODO: Make this more intelligent
        if price < 1.0:
            raise NotImplementedError("Step size not implemented for penny stocks")
        if mean_variance > 0.10:
            return 0.02
        return 0.01

    @staticmethod
    def _get_tier_prices(tier_count: int, low_price: float, step_size: float) -> list[float]:
        lowest_tier_price = market_round_up(low_price)
        if tier_count == 1:
            return [lowest_tier_price]
        dollars = int(lowest_tier_price)
        cents = int((lowest_tier_price - dollars) * 100)
        step_cents = int(step_size * 100)

        # Adjust cents to be the next integer up that is evenly divided by step_cents
        if cents % step_cents != 0:
            cents = ((cents // step_cents) + 1) * step_cents

        lowest_tier_price = dollars + (cents / 100)
        return [lowest_tier_price + (i * step_size) for i in range(tier_count)]

    @staticmethod
    def _get_tier_quantities(tier_count: int, qty: int) -> list[int]:
        # TODO: not using this functionality. Remove?
        if tier_count == 1:
            return [qty]
        # For most bins, use the same qty for each bin
        qty_list = [int(qty / tier_count)] * (tier_count - 1)
        # Fill in remainder at the front
        remainder = qty - sum(qty_list)
        qty_list = [remainder] + qty_list
        return qty_list
