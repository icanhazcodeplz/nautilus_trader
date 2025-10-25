from collections import deque
from datetime import timedelta

import pandas as pd
from nautilus_trader.common.component import TimeEvent

from custom.nt_extensions.indicators import RollingVWAP
from custom.utils.load_catalog_data import BACKTESTING_CATALOG
from custom.strategies.base import BaseStrategy
from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import TradeTick

from nautilus_trader.model.identifiers import InstrumentId


class LatencyTestStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    max_position_multiplier:int = 1
    trade_size: int = 1
    buy_on_tick: bool = True

class LatencyTestStrategy(BaseStrategy):
    min_secs_between_buys = 10
    def __init__(self, config: LatencyTestStrategyConfig) -> None:
        super().__init__(config)

        self._trade_ticks = []
        self.trade_tick_event = {}

    def on_trade_tick(self, tick: TradeTick) -> None:
        self.trade_tick_event = {
            "sz": int(tick.size),
            "price": float(tick.price),
            "ts_event": tick.ts_event,
            "ts_recv": tick.ts_init,
            "ts_clock": self.clock.utc_now(),
            "ts_now": pd.Timestamp.utcnow(),
        }
        if self.config.buy_on_tick:
            if len(self.submitted_or_open_orders()) == 0 and (self.clock.utc_now() - self._last_buy_dt).total_seconds() > self.min_secs_between_buys:
                self.buy(quantity=1, limit_price=tick.price * 0.85, tag="b")

        self.trade_tick_event["ts_now_after_buy"] = pd.Timestamp.utcnow()
        self._trade_ticks.append(self.trade_tick_event.copy())
        self.trade_tick_event = {}

    def _modify_or_cancel(self, _: TimeEvent):
        open_orders = self.submitted_or_open_orders()

        if len(open_orders) > 1:
            raise RuntimeError(f"More than one order open. Raising")

        if not self.config.buy_on_tick:
            if len(open_orders) == 0 and (self.clock.utc_now() - self._last_buy_dt).total_seconds() > self.min_secs_between_buys:
                self.buy(quantity=1, limit_price=0.5, tag="b")

        for order in open_orders:
            event_names = [str(event.__class__.__name__) for event in order.events]
            if "OrderPendingUpdate" not in event_names:
                new_price = order.price * 0.95
                self.modify_order(order, quantity=self.instrument.make_qty(1), price=self.instrument.make_price(new_price))
            elif "OrderUpdated" in event_names and "OrderPendingCancel" not in event_names:
                self.cancel_order(order)

    def on_start(self) -> None:
        self.clock.set_timer(
            name="modify_or_cancel_order",
            interval=timedelta(seconds=3),
            callback=self._modify_or_cancel,
        )

        self.instrument = self.cache.instrument(self.config.instrument_id)

        self.subscribe_trade_ticks(self.config.instrument_id)

    def on_stop(self) -> None:
        super().on_stop()
        self.unsubscribe_trade_ticks(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        pass
        # if not self.indicators_initialized():
        #     self.log.info(
        #         f"Waiting for indicators to warm up [{self.cache.bar_count(self.config.bar_type)}]",
        #         color=LogColor.BLUE,
        #     )
        #     return  # Wait for indicators to warm up...

    def on_reset(self) -> None:
        self.fast_ema.reset()
        self.slow_ema.reset()
