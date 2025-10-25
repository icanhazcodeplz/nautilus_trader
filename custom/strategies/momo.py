from collections import deque

from custom.nt_extensions.indicators import RollingVWAP
from custom.utils.load_catalog_data import BACKTESTING_CATALOG
from custom.strategies.base import BaseStrategy
from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import TradeTick

from nautilus_trader.model.identifiers import InstrumentId


class MomoConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier:int
    stop_loss:float

    take_profit:float
    take_ratio:float
    vwap_window:int
    vwap_buy_threshold:float
    trailing_stop:bool
    # bar_type: BarType
    # fast_ema_period: PositiveInt = 10
    # slow_ema_period: PositiveInt = 20
    # request_historical_bars: bool = False

def initialize_deque_if_needed(dq:deque, value):
    if len(dq) == 0:
        for i in range(dq.maxlen):
            dq.append(value)
    return dq

class Momo(BaseStrategy):
    def __init__(self, config: MomoConfig) -> None:
        super().__init__(config)

        # self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        # self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)
        self.vwap = RollingVWAP(rolling_window=self.config.vwap_window)

        self.metrics_to_save = {
            "vwap": self.vwap,
            # "fast_ema": self.fast_ema,
            # "slow_ema": self.slow_ema,
            # "vwap10": self.vwap10,
        }
        self.trigger_buy = False
        self.trigger_sell = False

        self.size_dq = deque(maxlen=10)
        self.price_dq = deque(maxlen=10)
        self.recent_big_drop = False
        self.take_price = None
        self.trades_since_order = 0
        self.metrics = []

        self.initial_price = None
        self.made_buy = False

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        # best_ask = ob.best_ask_price()
        initialize_deque_if_needed(self.price_dq, tick.price)
        initialize_deque_if_needed(self.size_dq, tick.size)

        price_2ago = self.price_dq[-2]
        price_1ago = self.price_dq[-1]
        price = tick.price
        # if price - price_2ago <= -0.20 and price_2ago >= price_1ago >= price:
        #     self.recent_big_drop = True


        self.price_dq.append(tick.price)
        self.size_dq.append(tick.size)
        # if self.recent_big_drop and price > price_1ago:
        if price < (self.vwap.value - self.config.vwap_buy_threshold) and price > price_1ago and tick.size > 1:
            self.recent_big_drop = False
            # FIXME: ADd buy back
            # self.buy(self.config.trade_size, price, cancel_after_secs=60, tag="b")
        if (
                self.position_qty > 1 and
                price > (self.vwap.value + self.config.vwap_buy_threshold) and
                price <= price_1ago and
                tick.size > 1 and
                price > (self.position_avg_px + self.config.take_profit)
        ):
            # FIXME: ADd sell back
            # self.sell(int(self.position_qty / 2), limit_price=price, tag="vt")
            pass

        if (
            self.config.trailing_stop and
            self.stop_price is not None and
            price > self.position_avg_px + self.config.take_profit and
            tick.size > 10

        ):
            new_stop = price - self.config.stop_loss
            self.stop_price = max(self.stop_price, new_stop)

        if not self.made_buy:
            self.buy(self.config.trade_size, tick.price, cancel_after_secs=10, tag="b")
            self.made_buy = True

        if self.made_buy and self.position_qty > 0 and len(self.submitted_or_open_orders()) == 0:
            self.sell(self.position_qty, tick.price - 0.03, tag="s")

    def on_order_filled(self, order) -> None:
        pass
        # if order.is_buy:
        #     first_take_price = order.last_px + (self.config.take_profit * 1)
        #     second_take_price = order.last_px + (self.config.take_profit * 1.2)
        #     t1_qty = int(order.last_qty * self.config.take_ratio)
        #     t2_qty = int(t1_qty * 0.5)
        #     self.sell(quantity=t1_qty, limit_price=first_take_price, tag="t1")
        #     if t2_qty > 0:
        #         self.sell(quantity=t2_qty, limit_price=second_take_price, tag="t2")
        #     self.stop_price = self.position_avg_px - self.config.stop_loss
            # self.stop_price = self.position_avg_px

    def on_start(self) -> None:
        super().on_start()
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        # self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)

        # FIXME: Assumes all "metrics_to_save" are for trade ticks
        for cls in self.metrics_to_save.values():
            self.register_indicator_for_trade_ticks(self.config.instrument_id, cls)

        # Get historical data
        # if self.config.request_historical_bars:
        #     self.request_bars(
        #         self.config.bar_type,
        #         start=self._clock.utc_now() - pd.Timedelta(days=1),
        #     )
        # self.request_quote_ticks(self.config.instrument_id)
        # self.request_trade_ticks(self.config.instrument_id)

        # Subscribe to live data
        # self.subscribe_bars(self.config.bar_type)
        self.subscribe_trade_ticks(self.config.instrument_id)

        # Use for LIVE testing
        # self.buy(self.config.trade_size, 3.00, cancel_after_secs=10, tag="b")
        # self.buy(int(self.config.trade_size * 0.75), 260.0, cancel_after_secs=10, tag="b")

    def on_stop(self) -> None:
        super().on_stop()
        # Unsubscribe from data
        # self.unsubscribe_bars(self.config.bar_type)
        # self.unsubscribe_quote_ticks(self.config.instrument_id)
        # self.unsubscribe_order_book_deltas(self.config.instrument_id)
        # self.unsubscribe_order_book_at_interval(self.config.instrument_id)

        # # TODO: Only save if not already existing
        # bars_list = self.cache.bars(self.config.bar_type)
        # bars_list.sort(key=lambda x: x.ts_init)
        # BACKTESTING_CATALOG.write_data(bars_list)


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

