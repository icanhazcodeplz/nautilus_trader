from collections import deque
import random

from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from custom.strategies.momo import backfill_deque_with_value_if_empty

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId


class NewsStrategyConfig(BaseStrategyConfig, frozen=True, kw_only=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float

    take_profit: float

    simple_take: bool = False
    random_buy: bool = False
    print_update_every_secs: int = None

    allow_trades: bool = True


class NewsStrategy(BaseStrategy):
    def __init__(self, config: NewsStrategyConfig) -> None:
        super().__init__(config)

        # FIXME: This is temporary
        self.take_profit = self.config.take_profit if self.config.take_profit is not None else self.config.stop_loss
        # self.vwap = VWAPBandsNew(
        #     lower_scalar_multiplier=self.config.lower_scalar_multiplier,
        #     upper_scalar_multiplier=self.config.upper_scalar_multiplier,
        #     rolling_window=self.config.vwap_window,
        #     variance_window=self.config.variance_window,
        #     outer_band_multiplier=self.config.outer_band_multiplier,
        #     pressure_window=self.config.pressure_window,
        # )
        self.metrics_to_save_on_tick = [
            # Metric(
            #     obj=self.vwap,
            #     name="vwap",
            #     attrs=[
            #         "value",
            #         "low",
            #         "high",
            #         "low_inner",
            #         "low_outer",
            #         "high_inner",
            #         "high_outer",
            #         "pressure",
            #     ],
            # ),
            # Metric(obj=self.vwap_day, name="day_vwap", attrs=["value"]),
        ]

        self.price_dq = deque(maxlen=2)
        self.take_price = None

        self.metrics = []

        self.last_take_ts = None
        self._sell_diff_start_ns: int | None = None
        self._last_tier_adjustment_ns = None

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        backfill_deque_with_value_if_empty(self.price_dq, tick.price)
        if self.last_take_ts is None:
            self.last_take_ts = self.clock.utc_now()

        price = tick.price
        self.price_dq.append(tick.price)

        buy_orders = self.open_buys
        position_qty = self.position_qty

        allow_buy = True
        if self._stopping_out:
            allow_buy = False

        # BUY LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if allow_buy:
            if self.config.random_buy:
                if (
                    len(buy_orders) == 0
                    and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > 20
                    and position_qty < self.max_position_allowed
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
                        self.buy(
                            self.config.trade_size, buy_limit, cancel_after_secs=10, tag=f"{self.buy_orders_count}"
                        )

            elif (
                price < self.vwap.low
                # and price_1ago > self.vwap.low
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
                    self.buy(self.config.trade_size, price, cancel_after_secs=1, tag=f"{self._buy_signals_count}")

        # TAKE LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        allow_take = True
        if self._stopping_out:
            allow_take = False
        if allow_take:
            if (
                self.open_sell_qty < position_qty
                # and price >= self.take_price
                and (self.clock.utc_now() - self.last_take_ts).total_seconds() > 1
            ):
                if self.config.simple_take:
                    self.sell(position_qty, limit_price=self.take_price, cancel_after_secs=None, tag="simple")
                elif (
                    # price > self.vwap.upper
                    # and price <= price_1ago
                    tick.size > 1
                ):
                    # and price > self.vwap.upper
                    sell_qty = max(int(position_qty), int(self.config.trade_size / 10), 1)
                    self.sell(sell_qty, limit_price=price, cancel_after_secs=10, tag="t")
                    self.last_take_ts = self.clock.utc_now()

    def _print_update(self):
        # def open_for_secs(open_order):
        #     return round((self.clock.timestamp_ns() - open_order.order.last_event.ts_event) / 1e9, 1)
        #
        # if self.config.trailing_take:
        #     if len(self.open_sells) > 0:
        #         ordered_sells = sorted(self.open_sells, key=lambda x: x.price)
        #         sells_str = "\n".join(
        #             f"{o.leaves_qty} @ {o.price}\t OpenSecs {open_for_secs(o)}\t {o.order.venue_order_id}\t {o.order.client_order_id}"
        #             for o in ordered_sells
        #         )
        #         msg = f"{round(self.vwap.mean_variance, 2)} {round(self.vwap.high, 3)}\n{sells_str}\n"
        #         return msg
        return ""

    def _on_order_filled(self, order_filled) -> None:
        if order_filled.is_buy:
            self.take_price = order_filled.last_px + self.take_profit
