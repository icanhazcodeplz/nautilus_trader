from collections import deque

from nautilus_trader.indicators import Indicator
from nautilus_trader.model import TradeTick


class RollingVWAP(Indicator):
    def __init__(self, rolling_window: int, variance_window_ratio: float = 1, upper_lower_scaler: float = 0.0):
        super().__init__(params=[rolling_window, variance_window_ratio, upper_lower_scaler])

        self.rolling_window = rolling_window
        self.variance_window_ratio = variance_window_ratio
        self.upper_lower_scaler = upper_lower_scaler

        self._trade_values = deque(maxlen=rolling_window)
        self._volumes = deque(maxlen=rolling_window)
        self._variances = deque(maxlen=int(rolling_window * variance_window_ratio))

        self.value = 0
        self.upper = 0
        self.lower = 0

    def handle_trade_tick(self, tick: TradeTick):
        self.update_raw(price=float(tick.price), volume=float(tick.size))

    def update_raw(
        self,
        price,
        volume,
    ):
        # No weighting for this price (also avoiding divide by zero)
        if volume == 0:
            return

        if not self.initialized:
            self._set_has_inputs(True)
            self._set_initialized(True)
            self.value = price

        self._trade_values.append(price * volume)
        self._volumes.append(volume)

        if self.variance_window_ratio is not None:
            variance_from_val = abs(price - self.value)
            self._variances.append(variance_from_val)
            mean_var = sum(self._variances) / len(self._variances)
            self.upper = self.value + mean_var + self.upper_lower_scaler
            self.lower = self.value - mean_var - self.upper_lower_scaler

        self.value = sum(self._trade_values) / sum(self._volumes)

    def _reset(self):
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
