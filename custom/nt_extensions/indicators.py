from collections import deque

from nautilus_trader.indicators import Indicator
from nautilus_trader.model import TradeTick


class RollingVWAP(Indicator):

    def __init__(self, rolling_window: int):
        super().__init__(params=[rolling_window])

        self.rolling_window = rolling_window

        self._trade_values = deque(maxlen=rolling_window)
        self._volumes = deque(maxlen=rolling_window)
        self.value = 0

    def handle_trade_tick(self, tick: TradeTick):
        self.update_raw(price=float(tick.price), volume=float(tick.size))

    def update_raw(
        self,
        price,
        volume,
    ):
        if not self.initialized:
            self._set_has_inputs(True)
            self._set_initialized(True)

        # No weighting for this price (also avoiding divide by zero)
        if volume == 0:
            return

        self._trade_values.append(price * volume)
        self._volumes.append(volume)
        self.value = sum(self._trade_values) / sum(self._volumes)

    def _reset(self):
        self._trade_values = deque(maxlen=self.rolling_window)
        self._volumes = deque(maxlen=self.rolling_window)
        self.value = 0

    # def handle_bar(self, bar):
    #     self.update_raw(
    #         (
    #             bar.close.as_double() +
    #             bar.high.as_double() +
    #             bar.low.as_double()
    #         ) / 3.0,
    #         bar.volume.as_double(),
    #         pd.Timestamp(bar.ts_init, tz="UTC"),
    #     )
