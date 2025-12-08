from collections import deque, OrderedDict
from dataclasses import dataclass
from random import random
import math

import pandas as pd

from custom.nt_extensions.indicators import VWAPBands
from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from nautilus_trader.indicators import VolumeWeightedAveragePrice
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId


def market_round_up(price: float) -> float:
    if price < 1.0:
        raise NotImplementedError("Market rounding not implemented for penny stocks")
    return math.ceil(price * 100) / 100


def get_step_size(price: float, mean_variance: float) -> float:
    # TODO: Make this more intelligent
    if price < 1.0:
        raise NotImplementedError("Step size not implemented for penny stocks")
    if mean_variance > 0.10:
        return 0.02
    return 0.01


class Tiers:
    def __init__(self, quantity, starting_price, step_size, num_tiers, instrument):
        self.quantity = quantity
        if quantity < num_tiers:
            num_tiers = quantity

        target_prices = self._get_tier_prices(num_tiers, starting_price, step_size)
        target_prices = [instrument.make_price(price) for price in target_prices]
        target_qtys = self._get_tier_quantities(num_tiers, quantity)
        self.filled_tiers = []
        self.tiers = OrderedDict()
        for price, qty in zip(target_prices, target_qtys):
            self.tiers[price] = (qty, 0)

    @staticmethod
    def _get_tier_prices(tier_count: int, low_price: float, step_size: float) -> list[float]:
        lowest_tier_price = market_round_up(low_price)
        if tier_count == 1:
            return [lowest_tier_price]
        dollars = int(lowest_tier_price)
        cents = int((lowest_tier_price - dollars) * 100)
        step_cents = int(step_size * 100)

        # Adjust cents to be the next integer up that is evenly divided by step_cents
        if cents % step_cents != 0:
            cents = ((cents // step_cents) + 1) * step_cents

        lowest_tier_price = dollars + (cents / 100)
        return [lowest_tier_price + (i * step_size) for i in range(tier_count)]

    @staticmethod
    def _get_tier_quantities(tier_count: int, qty: int) -> list[int]:
        if tier_count == 1:
            return [qty]
        # For most bins, use the same qty for each bin
        qty_list = [int(qty / tier_count)] * (tier_count - 1)
        # Fill in remainder at the front
        remainder = qty - sum(qty_list)
        qty_list = [remainder] + qty_list
        return qty_list

    def add_to_lowest_price_available(self, qty):
        for price, (target_qty, taken_qty) in self.tiers.items():
            if taken_qty < target_qty:
                self.tiers[price] = (target_qty, taken_qty + qty)
                return price
        return None

    def add_qty_to_tier(self, price, qty):
        if price not in self.tiers:
            raise ValueError(f"Price {price} not in tier prices set {self.tiers}")
        target_qty, taken_qty = self.tiers[price]
        self.tiers[price] = (target_qty, taken_qty + qty)

    def total_taken_qty(self):
        taken_qty = sum(taken for _, taken in self.tiers.values())
        return taken_qty

    @property
    def available_qty(self):
        return self.quantity - self.total_taken_qty()

    def lowest_tier_price_and_available_qty(self):
        for price, (target_qty, taken_qty) in self.tiers.items():
            if taken_qty < target_qty:
                return price, target_qty - taken_qty
        return None, None


@dataclass
class Metric:
    obj: object
    name: str
    attrs: list[str]

    def get_vals(self):
        return {f"{self.name}_{attr}": round(getattr(self.obj, attr), 3) for attr in self.attrs}

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
    lower_pct: float
    upper_pct: float
    vwap_window: int
    variance_window_ratio: float

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
        self.vwap = VWAPBands(
            lower_pct=self.config.lower_pct,
            upper_pct=self.config.upper_pct,
            rolling_window=self.config.vwap_window,
            variance_window_ratio=self.config.variance_window_ratio,
        )
        # self.vwap_day = VolumeWeightedAveragePrice()

        self.tick_metrics_to_save = [
            Metric(obj=self.vwap, name="vwap", attrs=["value", "upper", "lower"]),
            # Metric(obj=self.vwap_day, name="day_vwap", attrs=["value"]),
        ]

        self.price_dq = deque(maxlen=2)
        self.take_price = None

        self.metrics = []

        self.last_take_ts = None
        self._stopping_out = False

    def stop_out_if_needed(self, tick: TradeTick):
        if self.config.use_oco_sell_orders or self.config.use_bracket_orders:
            return
        if self.position_qty == 0:
            self.stop_price = None
            self._stopping_out = False
            return

        if self.position_qty > 0 and self.stop_price is None:
            self.stop_price = tick.price - self.config.stop_loss
            self.log.info(f"Setting stop price to {self.stop_price}")

        if self.stop_price is not None and tick.price <= self.stop_price:
            self._stopping_out = True
            # TODO: HARDCODED to set stop price to 0.01 below current price
            new_limit_price = self.instrument.make_price(tick.price - 0.01)
            self.log.info(f"Stop price {self.stop_price} reached, selling at {new_limit_price}")

            # FIXME: this is not a great solution. The fills for selling are more accurate during backtesting
            # if you use a single order, but during live running it is less buggy to modify existing orders because
            # trying to cancel existing orders runs async.
            self.sell_position_at_price(new_limit_price)
            # FIXME: cancelling all orders sometimes also cancels the subsequent sell order because of the async calls
            # self.cancel_all_orders(self.config.instrument_id)
            # self.sell(quantity=self.position_qty, limit_price=new_limit_price, tag="s")

            # Adjust stop price so we don't send repeat orders
            self.stop_price = new_limit_price

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
                    self.modify_open_order(order, quantity=order.quantity, price=vwap_lower)

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
        if self.config.trailing_take and not self._stopping_out:
            self._rolling_tiered_take(price)

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
        #             self.cancel_open_order(order)

    def _rolling_tiered_take(self, price):
        position_qty = self.position_qty
        if position_qty == 0:
            return
        open_sells = self.open_sells
        num_tiers = 3
        step_size = get_step_size(price, self.vwap.mean_variance)
        tiers = Tiers(
            quantity=position_qty,
            starting_price=self.vwap.upper,
            step_size=step_size,
            num_tiers=num_tiers,
            instrument=self.instrument
        )

        orders_to_be_modified = []
        for open_order in open_sells:
            if open_order.price in tiers.tiers:
                tiers.add_qty_to_tier(open_order.price, open_order.quantity)
            else:
                orders_to_be_modified.append(open_order)

        target_qty_per_tier = max(int(position_qty / num_tiers), 1)
        for open_order in orders_to_be_modified:
            cancel_order = False
            new_price = tiers.add_to_lowest_price_available(open_order.quantity)
            if new_price is None:
                cancel_order = True
            else:
                if target_qty_per_tier > 5 and new_price > min(tiers.tiers.keys()):
                    if open_order.quantity < int(target_qty_per_tier * 0.50):
                        self.log.info(
                            f"Canceling {open_order} because it is less than half of target tier qty {target_qty_per_tier}"
                        )
                        cancel_order = True
                    elif open_order.quantity > int(target_qty_per_tier * 1.2):
                        self.log.info(
                            f"Canceling {open_order} because it is more than 1.2x target tier qty {target_qty_per_tier}"
                        )
                        cancel_order = True
                if not cancel_order:
                    self.modify_open_order(open_order, quantity=open_order.quantity, price=new_price)
            if cancel_order:
                self.cancel_open_order(open_order)

        while tiers.available_qty > 0:
            price, qty = tiers.lowest_tier_price_and_available_qty()
            self.sell(qty, price, cancel_after_secs=None, tag=f"{self.buy_orders_count}")
            tiers.add_qty_to_tier(price, qty)

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
