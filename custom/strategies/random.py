from decimal import Decimal
from random import random
from typing import Tuple

from custom.strategies.base import BaseStrategy
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument



class RandomConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier:int
    stop_loss:float
    take_profit:float
    take_ratio:float = 1.0


class Random(BaseStrategy):
    def __init__(self, config: RandomConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument = None  # Initialized in on_start
        self.stop_price = None

        self.trades_since_flat = 0
        self.make_trade_at = self._update_make_trade_at()


    def _update_make_trade_at(self):
        return int(random() * 100) + 10
        # return 50

    def _on_trade_tick(self, tick: TradeTick) -> None:
        price = tick.price
        open_orders = self.open_orders
        if len(open_orders) == 0:
            if self.position_qty == 0:
                self.trades_since_flat += 1
                if self.trades_since_flat >= self.make_trade_at:
                    self.buy(self.config.trade_size, price, cancel_after_secs=10, tag="b")
            else:
                position_average = self.position_avg_px
                base_price = max(float(tick.price), position_average)
                take_price = base_price + self.config.take_profit
                self.stop_price = base_price - self.config.stop_loss
                if self.position_qty < 10:
                    sell_qty = self.position_qty
                else:
                    sell_qty = int(self.position_qty * self.config.take_ratio)
                self.sell(quantity=sell_qty, limit_price=take_price, tag="t")

    def on_order_filled(self, order) -> None:
        if order.is_sell:
            if self.position_qty == 0:
                self.trades_since_flat = 0
                self.make_trade_at = self._update_make_trade_at()
