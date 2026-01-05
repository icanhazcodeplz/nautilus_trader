from collections import deque
from copy import copy
from dataclasses import dataclass
import random

import pandas as pd

from custom.nt_extensions.indicators import VWAPBands, VWAPBandsNew
from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from custom.strategies._tiers import Tiers
from nautilus_trader.indicators.trend import MACDHistogram
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId


@dataclass
class Metric:
    obj: object
    name: str
    attrs: list[str]

    def get_vals(self):
        return {f"{self.name}_{attr}": getattr(self.obj, attr) for attr in self.attrs}

    @property
    def tick_lookback(self):
        if hasattr(self.obj, "tick_lookback"):
            return self.obj.tick_lookback
        return 0


class MomoStrategyConfig(BaseStrategyConfig, frozen=True, kw_only=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float

    take_profit: float
    take_ratio: float
    lower_scalar_multiplier: float
    upper_scalar_multiplier: float
    vwap_window: int
    variance_window: int
    outer_band_multiplier:float=1.0

    only_buy_if_macd_positive: bool = False
    trailing_buy_order: bool = False
    simple_take: bool = False
    trailing_take: bool = False
    random_buy: bool = False
    num_sell_tiers: int = 1
    print_update_every_secs: int = None

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
    adjust_tiers_only_every_ms = 100
    attempt_stop_out_every_ms = 60  # TODO: Move stopout logic into BaseStrategy?

    def __init__(self, config: MomoStrategyConfig) -> None:
        super().__init__(config)
        if sum([self.config.trailing_take, self.config.simple_take]) > 1:
            raise ValueError("Cannot use more than one of simple_take, trailing_take")

        if sum([self.config.trailing_buy_order, self.config.random_buy]) > 1:
            raise ValueError("Cannot use more than one of trailing_buy_order, random_buy")
        # FIXME: This is temporary
        self.take_profit = self.config.take_profit if self.config.take_profit is not None else self.config.stop_loss
        self.market_open_only = False  # TODO: remove this?
        # self.vwap = VWAPBands(
        self.vwap = VWAPBandsNew(
            lower_scalar_multiplier=self.config.lower_scalar_multiplier,
            upper_scalar_multiplier=self.config.upper_scalar_multiplier,
            rolling_window=self.config.vwap_window,
            variance_window=self.config.variance_window,
            outer_band_multiplier=self.config.outer_band_multiplier,
        )
        # self.vwap_day = VolumeWeightedAveragePrice()
        self.macd = MACDHistogram(fast_period=12, slow_period=26, signal_period=9)
        self.metrics_to_save_on_tick = [
            Metric(
                obj=self.vwap,
                name="vwap",
                attrs=[
                    "value",
                    "low",
                    "high",
                    "low_inner",
                    "low_outer",
                    "high_inner",
                    "high_outer",
                    "pressure",
                ],
            ),
            # Metric(obj=self.vwap_day, name="day_vwap", attrs=["value"]),
        ]
        if self.config.only_buy_if_macd_positive:
            self.metrics_to_save_on_1min.append(Metric(obj=self.macd, name="macd", attrs=["value"]))

        self.price_dq = deque(maxlen=2)
        self.take_price = None

        self.metrics = []

        self.last_take_ts = None
        self._stopping_out = False
        self._last_stop_out_attempt = 0
        self._last_tier_adjustment_ns = None

    def stop_out_if_needed(self, tick: TradeTick):
        if self.position_qty == 0:
            self.stop_price = None
            self._stopping_out = False
            return

        if self.clock.timestamp_ns() - self._last_stop_out_attempt < self.attempt_stop_out_every_ms * 1e6:
            time_since = (self.clock.timestamp_ns() - self._last_stop_out_attempt) / 1e6
            self.log.debug(
                f"Skipping stop out attempt because last attempt {time_since} ms ago. Limit {self.attempt_stop_out_every_ms}"
            )
            return

        if self.position_qty > 0 and self.stop_price is None:
            self.stop_price = tick.price - self.config.stop_loss
            self.log.info(f"Setting stop price to {self.stop_price}")

        if self.stop_price is not None and tick.price <= self.stop_price:
            self._last_stop_out_attempt = self.clock.timestamp_ns()
            self._stopping_out = True
            # TODO: HARDCODED to set stop price to 0.01 below current price
            new_limit_price = self.instrument.make_price(tick.price - 0.25)
            self.log.info(f"Stop price {self.stop_price} reached, selling at {new_limit_price}")

            # FIXME: this is not a great solution. The fills for selling are more accurate during backtesting
            # if you use a single order, but during live running it is less buggy to modify existing orders because
            # trying to cancel existing orders runs async.
            self.sell_position_at_price(new_limit_price)
            # FIXME: cancelling all orders sometimes also cancels the subsequent sell order because of the async calls
            # self.cancel_all_orders(self.config.instrument_id)
            # self.sell(quantity=self.position_qty, limit_price=new_limit_price, tag="s")

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
                and random.random() < 0.3
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
            vwap_lower = self.instrument.make_price(self.vwap.low)
            for order in self.open_buys:
                if order.price != vwap_lower:
                    self.modify_open_order(order, quantity=order.quantity, price=vwap_lower)

            if len(buy_orders) == 0 and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > 1:
                # FIXME: Clunky to add buy orders count tag here. Should be handled in buy()
                self.buy(self.config.trade_size, vwap_lower, cancel_after_secs=None, tag=f"{self.buy_orders_count}")

        if (
            price < self.vwap.low and price_1ago > self.vwap.low
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
                if not self.config.trailing_buy_order:
                    if self.config.only_buy_if_macd_positive:
                        if self.macd.initialized and self.macd.value > 0:
                            self.buy(
                                self.config.trade_size, price, cancel_after_secs=1, tag=f"{self._buy_signals_count}"
                            )
                    else:
                        self.buy(self.config.trade_size, price, cancel_after_secs=1, tag=f"{self._buy_signals_count}")

        # TAKE LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if self.config.trailing_take and not self._stopping_out:
            if (
                self._last_tier_adjustment_ns is None
                or (self.clock.timestamp_ns() - self._last_tier_adjustment_ns) / 1e9
                > self.adjust_tiers_only_every_ms / 1000
            ):
                self._rolling_tiered_take()

        if not self.config.trailing_take:
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
        #             self.cancel_open_order(order)

    def _rolling_tiered_take(self):
        self._last_tier_adjustment_ns = self.clock.timestamp_ns()
        position_qty = self.position_qty
        if position_qty == 0:
            return
        elif position_qty < 0:
            self.log.info(f"Position qty {position_qty} is negative. Running reconciliation.")
            self._reconcile()
            return
        tiers = Tiers(
            quantity=position_qty,
            starting_price=self.vwap.high,
            mean_variance=self.vwap.mean_variance,
            num_tiers=self.config.num_sell_tiers,
            instrument=self.instrument,
        )
        orders_to_be_modified = []
        existing_open_sell_qty = 0
        qty_taken_in_tiers = 0
        for open_order in self.open_sells:
            existing_open_sell_qty += open_order.leaves_qty
            if existing_open_sell_qty > position_qty:
                self.log.error(
                    f"Existing open sell qty {existing_open_sell_qty} is greater than position qty {position_qty}. Canceling order and skipping adjusting tiers."
                )
                self.cancel_open_order(open_order)
                return
            if tiers.take_price_if_available(open_order.price):
                qty_taken_in_tiers += open_order.leaves_qty
            else:
                orders_to_be_modified.append(open_order)

        # If we adjust two orders at the same time, we often get an "insufficient qty" error from alpaca. To reduce
        # this likelihood, limit the amount of increase qty to the current position
        available_qty_increase = position_qty - existing_open_sell_qty

        while len(tiers.available_prices) > 0:
            price = tiers.available_prices.pop()

            # Only sell up to (position_qty - qty_taken_in_tiers) to limit "insufficient qty" error
            qty_to_sell = position_qty - qty_taken_in_tiers

            # Minimize to tier max if there are more prices to sell at after this one
            if len(tiers.available_prices) > 0:
                qty_to_sell = min(tiers.max_qty_per_tier, qty_to_sell)

            if qty_to_sell > 0:
                # Modify an existing order if possible, otherwise create a new one
                if len(orders_to_be_modified) > 0:
                    open_order = orders_to_be_modified.pop(0)
                    # Need to adjust based on leaves_qty incase order is already partially filled
                    requested_qty_change = qty_to_sell - open_order.leaves_qty

                    qty_change = min(requested_qty_change, available_qty_increase)  # can be negative
                    new_order_qty = open_order.quantity + qty_change  # should be positive
                    if new_order_qty > 0:
                        qty_taken_in_tiers += new_order_qty - open_order.filled_qty  # Only include unfilled qty
                        # WARNING: modify_open_order does not always do anything. It has other checks about qty
                        # and frequency of modifications.
                        self.modify_open_order(open_order, quantity=new_order_qty, price=price)
                    else:
                        self.log.error(
                            f"Requesting new_order_qty of {new_order_qty}. Skipping modification. position_qty: {position_qty}."
                        )
                else:
                    self.sell(qty_to_sell, price, cancel_after_secs=None, tag=f"{self.buy_orders_count}")
                    qty_change = qty_to_sell
                    qty_taken_in_tiers += qty_to_sell
                if qty_change > 0:
                    # Only reduce if qty_change is positive
                    available_qty_increase -= qty_change

        open_sells_after = self.open_sells
        if len(open_sells_after) > len(tiers.prices):
            self.log.error(
                f"Number of open sell orders {len(copy(self.open_sells))} is greater than number of tiers {len(tiers.prices)}"
            )

    def _print_update(self):
        def open_for_secs(open_order):
            return round((self.clock.timestamp_ns() - open_order.order.last_event.ts_event) / 1e9, 1)

        if self.config.trailing_take:
            if len(self.open_sells) > 0:
                ordered_sells = sorted(self.open_sells, key=lambda x: x.price)
                sells_str = "\n".join(f"{o.leaves_qty} @ {o.price}  OpenSecs {open_for_secs(o)}" for o in ordered_sells)
                msg = f"{round(self.vwap.mean_variance, 2)} {round(self.vwap.high, 3)}\n{sells_str}\n"
                return msg
        return ""

    def _on_order_filled(self, order_filled) -> None:
        if order_filled.is_buy:
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
