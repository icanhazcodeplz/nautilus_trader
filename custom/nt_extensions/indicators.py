from collections import deque

import numpy as np
import pandas as pd

from nautilus_trader.indicators import Indicator
from nautilus_trader.model import TradeTick
from nautilus_trader.common.component import Logger


class VWAPBands(Indicator):
    initial_upper_lower_scalar = 0.50
    adjust_every = 500

    def __init__(
        self,
        lower_pct: float,
        upper_pct: float,
        rolling_window: int = 150,
        variance_window_ratio: float = 1,
        adjustment_window: int = 3000,
    ):
        super().__init__(params=[lower_pct, upper_pct, rolling_window, variance_window_ratio, adjustment_window])
        if rolling_window > adjustment_window:
            raise ValueError("Rolling window must be less than adjustment window")
        self.tick_lookback = adjustment_window

        self._log = Logger(name=self.__class__.__name__)
        self.lower_pct = lower_pct
        self.upper_pct = upper_pct
        self.rolling_window = rolling_window
        self.variance_window_ratio = variance_window_ratio
        self.adjustment_window = adjustment_window

        self.lower_scalar = self.initial_upper_lower_scalar
        self.upper_scalar = self.initial_upper_lower_scalar

        # Queues for the rolling window calculations
        self._trade_values = deque(maxlen=rolling_window)
        self._volumes = deque(maxlen=rolling_window)
        # Make the variance window longer/shorter based on variance_window_ratio
        self._variances = deque(maxlen=int(rolling_window * variance_window_ratio))

        # Queues for the adjustment window calculations
        self._prices = deque(maxlen=adjustment_window)
        self._values_for_adj = deque(maxlen=adjustment_window)
        self._mean_variances_for_adj = deque(maxlen=adjustment_window)

        self._window_filled = False
        self._adjust_counter = 1

        self.value = 0
        self.lower = 0
        self.upper = 0

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

        prices_array = np.array(self._prices)
        values_arr = np.array(self._values_for_adj)
        mean_variances_array = np.array(self._mean_variances_for_adj)

        upper_base = values_arr + mean_variances_array
        diff_price_to_upper_base = prices_array - upper_base
        upper_scalar = np.percentile(diff_price_to_upper_base, 100 - self.upper_pct)

        lower_base = values_arr - mean_variances_array
        diff_price_to_lower_base = prices_array - lower_base
        lower_scalar = np.percentile(diff_price_to_lower_base, self.lower_pct)
        lower_scalar = abs(lower_scalar)

        self._log.info(f"Upper {self.upper_scalar}->{upper_scalar}, Lower {self.lower_scalar}->{lower_scalar}")
        self.upper_scalar = upper_scalar
        self.lower_scalar = lower_scalar

    def update_raw(self, price, volume):
        # No weighting for this price (also avoiding divide by zero)
        if volume == 0:
            return

        if not self.initialized:
            self._set_has_inputs(True)
            self._set_initialized(True)
            self.value = price

        self._trade_values.append(price * volume)
        self._volumes.append(volume)

        self.value = sum(self._trade_values) / sum(self._volumes)

        variance_from_val = abs(price - self.value)
        self._variances.append(variance_from_val)
        mean_var = sum(self._variances) / len(self._variances)
        self._mean_variances_for_adj.append(mean_var)

        self._prices.append(price)
        self._values_for_adj.append(self.value)

        if self._adjust_counter % self.adjust_every == 0:
            self._adjust_counter = 1
            self._adjust_upper_lower()
        else:
            self._adjust_counter += 1

        lower = self.lower_scalar if self._reached_max_window() else self.initial_upper_lower_scalar
        upper = self.upper_scalar if self._reached_max_window() else self.initial_upper_lower_scalar

        self.lower = self.value - mean_var - lower
        self.upper = self.value + mean_var + upper

    def _reset(self):
        raise NotImplementedError
        self._trade_values = deque(maxlen=self.rolling_window)
        self._volumes = deque(maxlen=self.rolling_window)
        self.value = 0


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
