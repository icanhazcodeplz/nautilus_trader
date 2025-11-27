from collections import deque
from copy import copy
from dataclasses import dataclass
from random import random

import pandas as pd

from custom.nt_extensions.indicators import RollingVWAP
from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from nautilus_trader.indicators import VolumeWeightedAveragePrice
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import OrderSide


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
    lower_scalar: float
    upper_scalar: float

    trailing_buy_order: bool = False
    use_bracket_orders: bool = False
    use_oco_sell_orders: bool = False
    simple_take: bool = False
    trailing_take: bool = False
    random_buy: bool = False

    allow_trades: bool = True


def initialize_deque_if_needed(dq: deque, value):
    if len(dq) == 0:
        for i in range(dq.maxlen):
            dq.append(value)
    return dq


def is_market_open(now_utc: pd.Timestamp) -> bool:
    now_est = now_utc.tz_convert("US/Eastern")
    market_open = now_est.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_est.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_est < market_close


class MomoStrategy(BaseStrategy):
    rolling_take_tiers = 3  # TODO: Not implemented yet

    def __init__(self, config: MomoStrategyConfig) -> None:
        super().__init__(config)
        if (
            sum(
                [
                    self.config.trailing_take,
                    self.config.simple_take,
                    self.config.use_bracket_orders,
                    self.config.use_oco_sell_orders,
                ]
            )
            > 1
        ):
            raise ValueError("Cannot use more than one of simple_take, use_bracket_orders, use_oco_sell_orders")

        if sum([self.config.trailing_buy_order, self.config.random_buy]) > 1:
            raise ValueError("Cannot use more than one of trailing_buy_order, random_buy")
        # FIXME: This is temporary
        self.take_profit = self.config.take_profit if self.config.take_profit is not None else self.config.stop_loss
        self.market_open_only = self.config.use_bracket_orders or self.config.use_oco_sell_orders
        self.vwap = RollingVWAP(
            rolling_window=self.config.vwap_window,
            variance_window_ratio=self.config.variance_window_ratio,
            lower_scalar=self.config.lower_scalar,
            upper_scalar=self.config.upper_scalar,
        )
        # self.vwap_day = VolumeWeightedAveragePrice()

        self.metrics_to_save = [
            Metric(obj=self.vwap, name="vwap", attrs=["value", "upper", "lower"]),
            # Metric(obj=self.vwap_day, name="day_vwap", attrs=["value"]),
        ]

        self.price_dq = deque(maxlen=2)
        self.take_price = None

        self.metrics = []

        self.last_take_ts = None

    def stop_out_if_needed(self, tick: TradeTick):
        if self.config.use_oco_sell_orders or self.config.use_bracket_orders:
            return
        if self.position_qty == 0:
            self.stop_price = None
            return

        if self.position_qty > 0 and self.stop_price is None:
            self.stop_price = tick.price - self.config.stop_loss
            self.log.info(f"Setting stop price to {self.stop_price}")

        if self.stop_price is not None and tick.price <= self.stop_price:
            # TODO: HARDCODED to set stop price to 0.01 below current price
            new_limit_price = self.instrument.make_price(tick.price - 0.01)

            # FIXME: this is not a great solution. The fills for selling are more accurate during backtesting
            # if you use a single order, but during live running it is less buggy to modify existing orders because
            # trying to cancel existing orders runs async.
            self.sell_position_at_price(new_limit_price)
            # FIXME: cancelling all orders sometimes also cancels the subsequent sell order because of the async calls
            # self.cancel_all_orders(self.config.instrument_id)
            # self.sell(quantity=self.position_qty, limit_price=new_limit_price, tag="s")

            # Adjust stop price so we don't send repeat orders
            self.stop_price = tick.price

    def _on_trade_tick(self, tick: TradeTick) -> None:
        self.stop_out_if_needed(tick)
        # self.log.info(f"Trade tick: {tick}")
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        if self.market_open_only and not is_market_open(self.clock.utc_now()):
            return

        buy_orders = self.open_buys
        position_qty = self.position_qty
        if self.config.random_buy:
            if (
                len(buy_orders) == 0
                and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > 20
                and position_qty == 0
                and random() < 0.3
            ):
                # Only send buy command if it has been at least 10 seconds of flat
                all_positions = self.cache.positions(instrument_id=self.config.instrument_id)
                if len(all_positions) > 0:
                    most_recent_close = all_positions[0].ts_closed
                else:
                    most_recent_close = 0

                if (self.clock.timestamp_ns() - most_recent_close) / 1e9 > 10:
                    buy_limit = tick.price + 0.00
                    self.buy(self.config.trade_size, buy_limit, cancel_after_secs=10, tag=f"{self.buy_orders_count}")

        initialize_deque_if_needed(self.price_dq, tick.price)
        if self.last_take_ts is None:
            self.last_take_ts = self.clock.utc_now()

        price_1ago = self.price_dq[-1]
        price = tick.price

        self.price_dq.append(tick.price)

        # BUY LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if self.config.trailing_buy_order:
            vwap_lower = self.instrument.make_price(self.vwap.lower)
            for order in self.open_buys:
                if order.price != vwap_lower:
                    self.modify_order(order, quantity=order.quantity, price=vwap_lower)

            if len(buy_orders) == 0 and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > 1:
                # FIXME: Clunky to add buy orders count tag here. Should be handled in buy()
                self.buy(self.config.trade_size, vwap_lower, cancel_after_secs=None, tag=f"{self.buy_orders_count}")

        if (
            price < self.vwap.lower and price_1ago > self.vwap.lower
            # and (price > price_1ago)
            # and (price > self.vwap_day.value)
        ):
            self.log_buy_signal(tick)
            if (
                position_qty < self.max_position_allowed
                # and tick.size > 1
                # and (self.clock.utc_now() - self.last_buy_ts).total_seconds() > random.randint(1, 20)
                and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > 1
            ):
                if self.config.use_bracket_orders:
                    self.buy_bracket(
                        self.config.trade_size,
                        price,
                        self.config.stop_loss,
                        self.take_profit,
                        tag=f"{self._buy_signals_count}",
                    )
                elif not self.config.trailing_buy_order:
                    self.buy(self.config.trade_size, price, cancel_after_secs=1, tag=f"{self._buy_signals_count}")

        # TAKE LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if self.config.trailing_take:
            self._rolling_tiered_take()

        if not self.config.use_bracket_orders and not self.config.use_oco_sell_orders and not self.config.trailing_take:
            if (
                self.open_sell_qty < position_qty
                and price >= self.take_price
                and (self.clock.utc_now() - self.last_take_ts).total_seconds() > 1
            ):
                if self.config.simple_take:
                    self.sell(position_qty, limit_price=price, cancel_after_secs=10, tag="simple")
                elif (
                    # price > self.vwap.upper
                    # and price <= price_1ago
                    tick.size > 1
                ):
                    # and price > self.vwap.upper
                    sell_qty = max(int(position_qty * self.config.take_ratio), int(self.config.trade_size / 10), 1)
                    self.sell(sell_qty, limit_price=price, cancel_after_secs=10, tag="t")
                    self.last_take_ts = self.clock.utc_now()

        # Cancel buy if price has spiked above vwap
        # open_buys = self.open_buys
        # if (
        #     not (self.config.trailing_buy_order or self.config.random_buy)
        #     and len(open_buys) > 0
        #     and price > self.vwap.value
        #     and tick.size > 1
        # ):
        #     for order in copy(open_buys):
        #         if order.status != OrderStatus.SUBMITTED:
        #             self.log.info(
        #                 f"Canceling order {order.client_order_id}, tags {order.tags} because current price {price} is higher than vwap {self.vwap.value}"
        #             )
        #             self.cancel_order(order)

    def _rolling_tiered_take(self):
        position_qty = self.position_qty
        vwap_upper = self.instrument.make_price(self.vwap.upper)
        for order in self.open_sells:
            if order.price != vwap_upper:
                # If order has not started to fill yet, update quantity to the position_qty
                new_qty = position_qty if order.filled_qty == 0 else order.quantity
                self.modify_order(order, quantity=new_qty, price=vwap_upper)

        if position_qty > 0 and len(self.open_sells) == 0:
            self.sell(position_qty, vwap_upper, cancel_after_secs=None, tag=f"{self.buy_orders_count}")

    def _on_order_filled(self, order_filled) -> None:
        if order_filled.is_buy:
            if self.config.use_oco_sell_orders:
                cached_order = self.cache.order(order_filled.client_order_id)
                tag = cached_order.tags[0]
                price = order_filled.last_px
                self.sell_oco(
                    quantity=order_filled.last_qty,
                    stop_price=price - self.config.stop_loss,
                    take_price=price + self.take_profit,
                    tag=tag,
                )
            else:
                # Set take and stop losses based on order fill price
                self.take_price = order_filled.last_px + self.take_profit
                new_stop_price = order_filled.last_px - self.config.stop_loss
                if self.stop_price is not None:
                    if new_stop_price > self.stop_price:
                        self.log.info(f"Changing stop price from {self.stop_price} to {new_stop_price}")
                        self.stop_price = new_stop_price
                else:
                    self.log.info(f"Setting stop price to {new_stop_price}")
                    self.stop_price = new_stop_price

        # FIXME: NEed to figure out tiered take prices
        # if order_filled.is_sell and not self.config.use_oco_sell_orders:
        #     self.take_price = order_filled.last_px + self.take_profit

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
