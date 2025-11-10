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
    variance_window_ratio: float
    upper_lower_scaler: float

    use_bracket_orders: bool = False
    use_oco_sell_orders: bool = False
    simple_take: bool = False

    record_op_speed: bool = False  # Record operation speed
    allow_trades: bool = True


def initialize_deque_if_needed(dq: deque, value):
    if len(dq) == 0:
        for i in range(dq.maxlen):
            dq.append(value)
    return dq


class MomoStrategy(BaseStrategy):
    def __init__(self, config: MomoStrategyConfig) -> None:
        super().__init__(config)
        if sum([self.config.simple_take, self.config.use_bracket_orders, self.config.use_oco_sell_orders]) > 1:
            raise ValueError("Cannot use more than one of simple_take, use_bracket_orders, use_oco_sell_orders")

        self.vwap = RollingVWAP(
            rolling_window=self.config.vwap_window,
            variance_window_ratio=self.config.variance_window_ratio,
            upper_lower_scaler=self.config.upper_lower_scaler,
        )
        self.vwap_day = VolumeWeightedAveragePrice()

        self.metrics_to_save = [
            Metric(obj=self.vwap, name="vwap", attrs=["value", "upper", "lower"]),
            Metric(obj=self.vwap_day, name="day_vwap", attrs=["value"]),
        ]

        self.size_dq = deque(maxlen=10)
        self.price_dq = deque(maxlen=10)
        self.take_price = None

        self.metrics = []

        self.last_take_ts = None
        self.last_buy_ts = None

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # self.log.info(f"Trade tick: {tick}")
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        # best_ask = ob.best_ask_price()
        initialize_deque_if_needed(self.price_dq, tick.price)
        initialize_deque_if_needed(self.size_dq, tick.size)
        if self.last_take_ts is None:
            self.last_take_ts = self.clock.utc_now()
            self.last_buy_ts = self.clock.utc_now() - pd.Timedelta(minutes=10)

        price_1ago = self.price_dq[-1]
        price = tick.price

        self.price_dq.append(tick.price)
        self.size_dq.append(tick.size)
        position_qty = self.position_qty
        # BUY LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if (
            price < self.vwap.lower and price_1ago > self.vwap.lower
            # and (price > price_1ago)
            # and (price > self.vwap_day.value)
        ):
            if (
                position_qty < self.max_position_allowed
                # and tick.size > 1
                # and (self.clock.utc_now() - self.last_buy_ts).total_seconds() > random.randint(1, 20)
                and (self.clock.utc_now() - self.last_buy_ts).total_seconds() > 1
            ):
                self.log_buy_signal(tick)
                self.log.info(f"Buying at {price}")
                if self.config.use_bracket_orders:
                    self.buy_bracket(
                        self.config.trade_size,
                        price,
                        self.config.stop_loss,
                        self.config.take_profit,
                        tag=f"{self._buy_signals_count}",
                    )
                else:
                    self.buy(self.config.trade_size, price, cancel_after_secs=30, tag=f"{self._buy_signals_count}")
                self.last_buy_ts = self.clock.utc_now()

        if not self.config.use_bracket_orders and not self.config.use_oco_sell_orders:
            # TAKE LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
            if (
                position_qty > 0
                and price >= self.take_price
                and (self.clock.utc_now() - self.last_take_ts).total_seconds() > 1
            ):
                if self.config.simple_take:
                    self.sell(position_qty, limit_price=price, cancel_after_secs=10, tag="simple")
                elif (
                    price > (self.vwap.value + self.config.vwap_sell_threshold)
                    and price <= price_1ago
                    and tick.size > 1
                ):
                    # and price > self.vwap.upper
                    sell_qty = max(int(position_qty * self.config.take_ratio), int(self.config.trade_size / 10), 1)
                    self.sell(sell_qty, limit_price=price, cancel_after_secs=10, tag="t")
                    self.last_take_ts = self.clock.utc_now()

        open_buys = self.submitted_or_open_orders(side=OrderSide.BUY)
        if len(open_buys) > 0 and price > self.vwap.value and tick.size > 1:
            for order in open_buys:
                if order.status != OrderStatus.PENDING_CANCEL:
                    self.log.info(
                        f"Canceling order {order.client_order_id}, tag {order.tags[0]} because current price {price} is higher than vwap {self.vwap.value}"
                    )
                    self.cancel_order(order)

    def _on_order_filled(self, order_filled) -> None:
        if order_filled.is_buy:
            if self.config.use_oco_sell_orders:
                cached_order = self.cache.order(order_filled.client_order_id)
                tag = cached_order.tags[0]
                if tag == '16':
                    print()
                price = order_filled.last_px
                self.sell_oco(
                    quantity=order_filled.last_qty,
                    stop_price=price - self.config.stop_loss,
                    take_price=price + self.config.take_profit,
                    tag=tag,
                )
            else:
                # Set take and stop losses based on order fill price
                self.take_price = order_filled.last_px + self.config.take_profit
                new_stop_price = order_filled.last_px - self.config.stop_loss
                if self.stop_price is not None:
                    if new_stop_price > self.stop_price:
                        self.log.info(f"Changing stop price from {self.stop_price} to {new_stop_price}")
                        self.stop_price = new_stop_price
                else:
                    self.log.info(f"Setting stop price to {new_stop_price}")
                    self.stop_price = new_stop_price

        if order_filled.is_sell and not self.config.use_oco_sell_orders:
            self.take_price = order_filled.last_px + self.config.take_profit

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
