from datetime import timedelta

import pandas as pd

from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId


class LatencyTestStrategyConfig(BaseStrategyConfig, frozen=True):
    instrument_id: InstrumentId
    max_position_multiplier: int = 1
    order_count: int = 2
    buy_on_tick: bool = True

    trade_size: int = 1
    stop_loss: float = None


class LatencyTestStrategy(BaseStrategy):
    min_secs_between_buys = 10

    def __init__(self, config: LatencyTestStrategyConfig) -> None:
        super().__init__(config)

        self._trade_ticks = []
        self.trade_tick_event = {}
        self._order_count = 0

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # USE FOR STREAMING TRADES
        # init_dt = pd.Timestamp(tick.ts_init, unit='ns').tz_localize('UTC').tz_convert('US/Eastern').strftime('%H:%M:%S.%f')
        # ts_event = pd.Timestamp(tick.ts_event, unit='ns').tz_localize('UTC').tz_convert('US/Eastern').strftime('%H:%M:%S.%f')
        # print(f"{ts_event}  {init_dt}  {tick.price}  {tick.size}")

        if self.config.buy_on_tick:
            if (
                len(self.submitted_or_open_orders()) == 0
                and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > self.min_secs_between_buys
            ):
                self.buy(quantity=1, limit_price=tick.price * 0.85, tag="b")

    def _modify_or_cancel(self, _: TimeEvent):
        open_orders = self.submitted_or_open_orders()

        if len(open_orders) > 1:
            raise RuntimeError(f"More than one order open. Raising")

        if not self.config.buy_on_tick:
            if (
                len(open_orders) == 0
                and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > self.min_secs_between_buys
            ):
                self.buy(quantity=1, limit_price=0.5, tag="b")

        for order in open_orders:
            event_names = [str(event.__class__.__name__) for event in order.events]
            if "OrderPendingUpdate" not in event_names:
                new_price = order.price * 0.95
                self.modify_order(
                    order, quantity=self.instrument.make_qty(1), price=self.instrument.make_price(new_price)
                )
            elif "OrderUpdated" in event_names and "OrderPendingCancel" not in event_names:
                self.cancel_order(order)
                self._order_count += 1
                if self._order_count >= self.config.order_count:
                    self.stop()

    def on_start(self) -> None:
        self.clock.set_timer(name="modify_or_cancel", interval=timedelta(seconds=3), callback=self._modify_or_cancel)

        self.instrument = self.cache.instrument(self.config.instrument_id)
        self.subscribe_trade_ticks(self.config.instrument_id)
