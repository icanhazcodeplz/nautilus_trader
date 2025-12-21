from collections import deque

import numpy as np

from nautilus_trader.indicators import Indicator
from nautilus_trader.model import TradeTick
from nautilus_trader.common.component import Logger


class VWAPBands(Indicator):
    initial_upper_lower_scalar = 1.0
    adjust_every = 100

    def __init__(
        self,
        lower_scalar_multiplier: float,
        upper_scalar_multiplier: float,
        rolling_window: int = 150,
        variance_window: int = 300,
        adjustment_window: int = 3000,
    ):
        super().__init__(
            params=[
                lower_scalar_multiplier,
                upper_scalar_multiplier,
                rolling_window,
                variance_window,
                adjustment_window,
            ]
        )
        if rolling_window > adjustment_window:
            raise ValueError("Rolling window must be less than adjustment window")
        self.tick_lookback = adjustment_window

        self._log = Logger(name=self.__class__.__name__)
        self.lower_scalar_multiplier = lower_scalar_multiplier
        self.upper_scalar_multiplier = upper_scalar_multiplier
        self.rolling_window = rolling_window
        self.variance_window = variance_window
        self.adjustment_window = adjustment_window

        self.lower_scalar = self.initial_upper_lower_scalar
        self.upper_scalar = self.initial_upper_lower_scalar

        # Queues for the rolling window calculations
        self._trade_values = deque(maxlen=rolling_window)
        self._volumes = deque(maxlen=rolling_window)
        # Make the variance window longer/shorter based on variance_window
        self._variances = deque(maxlen=variance_window)

        # Queues for the adjustment window calculations
        self._prices = deque(maxlen=adjustment_window)
        self._vwaps_for_adj = deque(maxlen=adjustment_window)
        self._mean_variances_for_adj = deque(maxlen=adjustment_window)
        self._window_filled = False
        self._adjust_counter = 1
        self._lower_scalar_base = 0.0
        self._upper_scalar_base = 0.0

        self.vwap = None
        self.mean_variance = 0
        self.lower_base = 0
        self.lower = 0
        self.upper = 0

    @property
    def value(self):
        return self.vwap

    def handle_trade_tick(self, tick: TradeTick):
        self.update_raw(price=float(tick.price), volume=float(tick.size))

    def _reached_max_window(self):
        if self._window_filled:
            # Adding this at top allows bools below to be skipped once we fill window
            return True
        self._window_filled = (
            (len(self._trade_values) == self._trade_values.maxlen)
            and (len(self._volumes) == self._volumes.maxlen)
            and (len(self._variances) == self._variances.maxlen)
            and (len(self._prices) == self._prices.maxlen)
        )
        return self._window_filled

    def _adjust_upper_lower(self):
        if not self._window_filled:
            return

        prices_arr = np.array(self._prices)
        vwap_arr = np.array(self._vwaps_for_adj)
        mean_variances_arr = np.array(self._mean_variances_for_adj)

        # base values are average_var above or below vwap
        lower_base = vwap_arr - mean_variances_arr
        upper_base = vwap_arr + mean_variances_arr

        # Get the differences from price to lower_base to get the base lower scalar. Find the 5th percentile to remove
        # the bottom 5% outliers.
        diff_price_to_lower_base = prices_arr - lower_base
        self._lower_scalar_base = abs(np.percentile(diff_price_to_lower_base, 5))

        diff_price_to_upper_base = prices_arr - upper_base
        self._upper_scalar_base = np.percentile(diff_price_to_upper_base, 95)

        # The final lower scalar includes user defined multiplier
        self.lower_scalar = self._lower_scalar_base * self.lower_scalar_multiplier
        self.upper_scalar = self._upper_scalar_base * self.upper_scalar_multiplier

        # self._log.info(f"Upper {self.upper_scalar}->{upper_scalar}, Lower {self.lower_scalar}->{lower_scalar}")
        # self.lower_scalar = lower_scalar

    def update_raw(self, price, volume):
        # No weighting for this price (also avoiding divide by zero)
        if volume == 0:
            return

        if not self.initialized:
            if self.vwap is None:
                # Need some initial value to start with to avoid raising
                self.vwap = price
            self._set_has_inputs(True)  # What is this ever used for?
            # Wait to "set_intialized" until after window is fully filled.
            if self._window_filled:
                self._set_initialized(True)
                # Once window is filled, do the initial upper/lower adjustment
                self._adjust_upper_lower()
                self._adjust_counter = 1

        self._trade_values.append(price * volume)
        self._volumes.append(volume)

        self.vwap = sum(self._trade_values) / sum(self._volumes)

        variance_from_val = abs(price - self.vwap)
        self._variances.append(variance_from_val)
        self.mean_variance = sum(self._variances) / len(self._variances)
        self._mean_variances_for_adj.append(self.mean_variance)

        self._prices.append(price)
        self._vwaps_for_adj.append(self.vwap)

        if self._adjust_counter % self.adjust_every == 0:
            self._adjust_counter = 1
            self._adjust_upper_lower()
        else:
            self._adjust_counter += 1

        lower_scalar = self.lower_scalar if self._reached_max_window() else self.initial_upper_lower_scalar
        upper_scalar = self.upper_scalar if self._reached_max_window() else self.initial_upper_lower_scalar

        self.lower_base = self.vwap - self.mean_variance
        self.lower = self.lower_base - lower_scalar

        self.upper = self.vwap + self.mean_variance + upper_scalar

    def _reset(self):
        raise NotImplementedError
        self._trade_values = deque(maxlen=self.rolling_window)
        self._volumes = deque(maxlen=self.rolling_window)
        self.vwap = 0


class RollingTimeVWAP(Indicator):
    def __init__(self, rolling_window: int, bin_ms: int):
        super().__init__(params=[rolling_window, bin_ms])

        self.rolling_window = rolling_window
        self.bin_ns = bin_ms * 1e6

        self._trade_values = deque(maxlen=rolling_window)
        self._volumes = deque(maxlen=rolling_window)
        self._current_bin_start = None
        self._current_bin_value = 0
        self._current_bin_volume = 0
        self.value = None
        self.vwap_current_bin = None

    def handle_trade_tick(self, tick: TradeTick):
        self.update_raw(ts_init=tick.ts_init, price=float(tick.price), volume=float(tick.size))

    def update_raw(self, ts_init, price, volume):
        # No weighting for this price (also avoiding divide by zero)
        if volume == 0:
            return

        if not self.initialized:
            self._set_has_inputs(True)
            self._set_initialized(True)
            self._current_bin_start = ts_init
            self.value = price

        this_trade_value = price * volume
        if ts_init <= (self._current_bin_start + self.bin_ns):
            self._current_bin_value += this_trade_value
            self._current_bin_volume += volume
            self.vwap_current_bin = self._current_bin_value / self._current_bin_volume
        else:
            self._trade_values.append(self._current_bin_value)
            self._volumes.append(self._current_bin_volume)
            self.value = sum(self._trade_values) / sum(self._volumes)

            self._current_bin_start = ts_init
            self._current_bin_value = this_trade_value
            self._current_bin_volume = volume
            self.vwap_current_bin = price

    def _reset(self):
        raise Exception("Not supported")
