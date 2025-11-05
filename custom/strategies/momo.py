from collections import deque
from dataclasses import dataclass

from custom.nt_extensions.indicators import RollingVWAP
from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from nautilus_trader.indicators import VolumeWeightedAveragePrice
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import OrderSide, OrderStatus


@dataclass
class Metric:
    obj: object
    name: str
    attrs: list[str]

    def get_vals(self):
        return {f"{self.name}_{attr}": round(getattr(self.obj, attr), 3) for attr in self.attrs}


class MomoStrategyConfig(BaseStrategyConfig, frozen=True, kw_only=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float

    take_profit: float
    take_ratio: float
    vwap_window: int
    vwap_buy_threshold: float
    vwap_sell_threshold: float
    time_vwap_window: int
    time_vwap_bin_ms: int
    trailing_stop: bool
    simple_take: bool = False

    record_op_speed: bool = False  # Record operation speed
    # bar_type: BarType
    # fast_ema_period: PositiveInt = 10
    # slow_ema_period: PositiveInt = 20
    # request_historical_bars: bool = False


def initialize_deque_if_needed(dq: deque, value):
    if len(dq) == 0:
        for i in range(dq.maxlen):
            dq.append(value)
    return dq


class MomoStrategy(BaseStrategy):
    def __init__(self, config: MomoStrategyConfig) -> None:
        super().__init__(config)

        self.vwap = RollingVWAP(
            rolling_window=self.config.vwap_window, variance_window_ratio=1.5, upper_lower_scaler=0.05
        )
        # self.time_vwap = RollingVWAP(rolling_window=self.config.time_vwap_window)
        self.vwap_day = VolumeWeightedAveragePrice()

        self.metrics_to_save = [
            Metric(obj=self.vwap, name="vwap", attrs=["value", "upper", "lower"]),
            # Metric(obj=self.time_vwap, name="time_vwap", attrs=["value"]),
            Metric(obj=self.vwap_day, name="day_vwap", attrs=["value"]),
        ]

        self.size_dq = deque(maxlen=10)
        self.price_dq = deque(maxlen=10)
        self.take_price = None
        self.buy_signals_count = 0

        self.metrics = []

        self.last_take_ts = None
        self.last_buy_ts = None

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # if self.clock.utc_now() > pd.Timestamp("2025-09-19T13:04:21.473147857", tz="UTC"):
        #     stop_here=1
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        # best_ask = ob.best_ask_price()
        initialize_deque_if_needed(self.price_dq, tick.price)
        initialize_deque_if_needed(self.size_dq, tick.size)
        if self.last_take_ts is None:
            self.last_take_ts = self.clock.utc_now()
            self.last_buy_ts = self.clock.utc_now()

        price_2ago = self.price_dq[-2]
        price_1ago = self.price_dq[-1]
        price = tick.price
        # if price - price_2ago <= -0.20 and price_2ago >= price_1ago >= price:
        #     self.recent_big_drop = True

        self.price_dq.append(tick.price)
        self.size_dq.append(tick.size)
        position_qty = self.position_qty
        # if self.recent_big_drop and price > price_1ago:

        # BUY LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if price < (self.vwap.lower) and (price > price_1ago) and (price > self.vwap_day.value):
            self.buy_signals_count += 1
            # if self.buy_signals_count == 39:
            #     for tick in self.cache.trade_ticks(self.config.instrument_id)[0:5]:
            #         print(pd.Timestamp(tick.ts_event, tz="UTC"), tick.price, tick.size)
            # TODO: abstract this out?
            self._buy_sell_signals.append(
                dict(side="buy", time=tick.ts_init, tag=f"{self.buy_signals_count}|{float(price)}")
            )
            if (
                position_qty < self.max_position_allowed
                and tick.size > 1
                and (self.clock.utc_now() - self.last_buy_ts).total_seconds() > 1
            ):
                self.log.info(f"Buying at {price}. vwap: {self.vwap.value}")
                self.buy(self.config.trade_size, price, cancel_after_secs=30, tag=f"{self.buy_signals_count}")
                self.last_buy_ts = self.clock.utc_now()

        # TAKE LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if (
            position_qty > 1
            and price >= self.take_price
            and (self.clock.utc_now() - self.last_take_ts).total_seconds() > 1
        ):
            if (
                self.config.simple_take
                or (
                    price > (self.vwap.value + self.config.vwap_sell_threshold)
                    and price <= price_1ago
                    and tick.size > 1
                )
                # and price > self.vwap.upper
            ):
                sell_qty = max(int(position_qty * self.config.take_ratio), int(self.config.trade_size / 10))
                self.sell(sell_qty, limit_price=price, cancel_after_secs=10, tag="vt")
                self.last_take_ts = self.clock.utc_now()

        open_buys = self.submitted_or_open_orders(side=OrderSide.BUY)
        if len(open_buys) > 0 and price > self.vwap.value and tick.size > 1:
            for order in open_buys:
                if order.status != OrderStatus.PENDING_CANCEL:
                    self.log.info(
                        f"Canceling order {order.client_order_id} @{order.price} because current price {price} is higher than vwap {self.vwap.value}"
                    )
                    self.cancel_order(order)

        if (
            self.config.trailing_stop
            and self.stop_price is not None
            and price > self.position_avg_px + self.config.take_profit
            and tick.size > 10
        ):
            new_stop = price - self.config.stop_loss
            self.stop_price = max(self.stop_price, new_stop)

    def _on_order_filled(self, order) -> None:
        if order.is_buy:
            self.take_price = order.last_px + self.config.take_profit
        elif order.is_sell:
            self.take_price = order.last_px + self.config.take_profit
        else:
            raise ValueError("Invalid order type")

    def on_start(self) -> None:
        super().on_start()
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        # FIXME: Assumes all "metrics_to_save" are for trade ticks
        for metric in self.metrics_to_save:
            self.register_indicator_for_trade_ticks(self.config.instrument_id, metric.obj)

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

    def on_stop(self) -> None:
        super().on_stop()

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
        raise NotImplementedError
