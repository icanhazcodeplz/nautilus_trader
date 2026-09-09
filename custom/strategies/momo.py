from collections import deque
import random

import pandas as pd
import torch

from custom.nt_extensions.indicators import VWAPBandsNew
from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from custom.strategies._tiers import Tiers
from custom.strategies.metric import Metric
from nautilus_trader.indicators.trend import MACDHistogram
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.identifiers import InstrumentId

from lstm.lstm_common import TICK_LOOKBACK, build_live_features, get_device, load_model


class MomoStrategyConfig(BaseStrategyConfig, frozen=True, kw_only=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float

    take_profit: float
    lower_scalar_multiplier: float
    upper_scalar_multiplier: float
    vwap_window: int
    variance_window: int
    outer_band_multiplier: float = 1.0
    pressure_window: int = 50

    only_buy_if_macd_positive: bool = False
    trailing_buy_order: bool = False
    simple_take: bool = False
    trailing_take: bool = False
    random_buy: bool = False
    lstm_buy: bool = False
    num_sell_tiers: int = 1
    print_update_every_secs: int = None

    allow_trades: bool = True


def backfill_deque_with_value_if_empty(dq: deque, value):
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
    _ADJUST_TIERS_ONLY_EVERY_MS = 80
    _MAX_ALLOWED_SELL_DIFF_SECS = 5

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
            pressure_window=self.config.pressure_window,
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
        self._sell_diff_start_ns: int | None = None
        self._last_tier_adjustment_ns = None

        # LSTM Model internal params
        self.lstm_model = None
        self.lstm_device = None
        self._candle_10s_mids = deque(maxlen=30)
        self._candle_1m_mids = deque(maxlen=60)
        self._last_10s_bucket = None
        self._last_1m_bucket = None
        if self.config.lstm_buy:
            self.lstm_device = get_device()
            self.lstm_model = load_model("lstm/lstm_best.pt", self.lstm_device)

    def _update_candle_mids(self, tick: TradeTick):
        ts_ns = tick.ts_event
        bucket_10s = ts_ns // (10 * 1_000_000_000)
        bucket_1m = ts_ns // (60 * 1_000_000_000)

        quote = self.cache.quote_tick(self.config.instrument_id)
        if quote is None:
            return
        mid = (float(quote.bid_price) + float(quote.ask_price)) / 2

        if self._last_10s_bucket is not None and bucket_10s != self._last_10s_bucket:
            self._candle_10s_mids.append(mid)
        self._last_10s_bucket = bucket_10s

        if self._last_1m_bucket is not None and bucket_1m != self._last_1m_bucket:
            self._candle_1m_mids.append(mid)
        self._last_1m_bucket = bucket_1m

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # self.log.info(f"Trade tick: {tick}")
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        if self.market_open_only and not is_market_open(self.clock.utc_now()):
            return
        if self.config.lstm_buy:
            self._update_candle_mids(tick)
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
        if self.config.only_buy_if_macd_positive:
            if not self.macd.initialized or self.macd.value < 0:
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
            elif self.config.lstm_buy:
                trade_ticks = self.cache.trade_ticks(self.config.instrument_id)
                quote_tick = self.cache.quote_tick(self.config.instrument_id)
                if (
                    len(trade_ticks) >= TICK_LOOKBACK
                    and quote_tick is not None
                    and len(self._candle_10s_mids) >= 30
                    and len(self._candle_1m_mids) >= 60
                    and len(buy_orders) == 0
                    and position_qty < self.max_position_allowed
                    and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > 1
                    and tick.price < self.vwap.low
                ):
                    tick_feat, ctx_feat = build_live_features(
                        list(reversed(trade_ticks[:TICK_LOOKBACK])),
                        quote_tick,
                        list(self._candle_10s_mids),
                        list(self._candle_1m_mids),
                    )
                    tick_feat = tick_feat.to(self.lstm_device)
                    ctx_feat = ctx_feat.to(self.lstm_device)
                    with torch.no_grad():
                        logit = self.lstm_model(tick_feat, ctx_feat).item()
                    prob_up = torch.sigmoid(torch.tensor(logit)).item()
                    if prob_up > 0.85:
                        self.buy(
                            self.config.trade_size,
                            tick.price,
                            cancel_after_secs=10,
                            tag=f"lstm_p{prob_up:.2f}",
                        )

            elif self.config.trailing_buy_order:
                vwap_lower = self.instrument.make_price(self.vwap.low)
                for order in self.open_buys:
                    if order.price != vwap_lower:
                        self.modify_open_order(order, quantity=order.quantity, price=vwap_lower)

                if len(buy_orders) == 0 and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > 1:
                    # FIXME: Clunky to add buy orders count tag here. Should be handled in buy()
                    self.buy(self.config.trade_size, vwap_lower, cancel_after_secs=None, tag=f"{self.buy_orders_count}")
            elif (
                price < self.vwap.low
                # and price_1ago > self.vwap.low
                # and (price > price_1ago)
                # and (price > self.vwap_day.value)
            ):
                if (
                    position_qty < self.max_position_allowed
                    # and tick.size > 1
                    # and (self.clock.utc_now() - self.last_buy_ts).total_seconds() > random.randint(1, 20)
                    and (self.clock.utc_now() - self.last_buy_dt).total_seconds() > 1
                ):
                    self.buy(self.config.trade_size, price, cancel_after_secs=1, tag=f"{self.buy_orders_count}")

        # TAKE LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        allow_take = True
        if self._stopping_out:
            allow_take = False
        if allow_take:
            if self.config.trailing_take:
                if (
                    self._last_tier_adjustment_ns is None
                    or (self.clock.timestamp_ns() - self._last_tier_adjustment_ns) / 1e9
                    > self._ADJUST_TIERS_ONLY_EVERY_MS / 1000
                ):
                    self._rolling_tiered_take()
            else:
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
        )
        orders_to_be_modified = []
        existing_open_sell_qty = 0
        qty_taken_in_tiers = 0
        for i, open_order in enumerate(sorted(self.open_sells, key=lambda order: order.price)):
            existing_open_sell_qty += open_order.leaves_qty
            if existing_open_sell_qty > position_qty:
                self.log.info(
                    f"Existing open sell qty {existing_open_sell_qty} is greater than position qty {position_qty}. "
                    "Canceling order and skipping adjusting tiers."
                )
                self.cancel_open_order(open_order)
                return

            # If there is no open order at the lowest tier level, add this order to modified list, even if it's in
            # an available tier
            if (
                len(tiers.prices) > 1  # At least two tiers
                and (i + 1) == len(self.open_sells)  # Last open_sell in self.open_sells
                and len(orders_to_be_modified) == 0  # No orders to be modified
                and len(tiers.available_prices) > 0  # At least one available tier
                and min(tiers.available_prices) == min(tiers.prices)  # Min tier price still available
                and open_order.price != min(tiers.prices)  # This order is not at min
            ):
                orders_to_be_modified.append(open_order)
            elif open_order.price in tiers.prices:
                qty_taken_in_tiers += open_order.leaves_qty
                tiers.available_prices.discard(open_order.price)
            else:
                orders_to_be_modified.append(open_order)

        # If we adjust two orders at the same time, we often get an "insufficient qty" error from alpaca. To reduce
        # this likelihood, limit the amount of increase qty to the current position
        available_qty_increase = position_qty - existing_open_sell_qty

        while len(tiers.available_prices) > 0:
            price = min(tiers.available_prices)
            tiers.available_prices.remove(price)

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
                elif any(o.order.status == OrderStatus.PENDING_UPDATE for o in self.open_sells):
                    # Don't create new sell orders while existing sells are mid-modification,
                    # since the venue still holds the old (larger) qty until the replace confirms.
                    # This check is inside the loop (not before it) because a modify earlier in
                    # this same loop iteration can put an order into PENDING_UPDATE.
                    self.log.debug("Skipping new sell: existing sell order is PENDING_UPDATE")
                    break
                else:
                    self.sell(qty_to_sell, price, cancel_after_secs=None, tag=f"{self.buy_orders_count}")
                    qty_change = qty_to_sell
                    qty_taken_in_tiers += qty_to_sell
                if qty_change > 0:
                    # Only reduce if qty_change is positive
                    available_qty_increase -= qty_change

        for order in orders_to_be_modified:
            self.log.info(f"Canceling left over order_to_be_modified: {order}")
            self.cancel_open_order(order)

        # If (position - sells) is non_zero for more than _MAX_ALLOWED_SELL_DIFF_SECS, sell diff at lowest tier
        # in a new order.
        sell_diff = self.position_qty - self.open_sells_qty
        if sell_diff == 0:
            self._sell_diff_start_ns = None
        elif self._sell_diff_start_ns is None:
            self._sell_diff_start_ns = self.clock.timestamp_ns()
        elif (self.clock.timestamp_ns() - self._sell_diff_start_ns) / 1e9 > self._MAX_ALLOWED_SELL_DIFF_SECS:
            self.log.info(
                f"Adding sell order for {sell_diff} at lowest tier because sell_diff existed for more than {self._MAX_ALLOWED_SELL_DIFF_SECS} secs."
            )
            self.sell(sell_diff, min(tiers.prices), cancel_after_secs=None, tag=f"{self.buy_orders_count}")

    def _print_update(self):
        def open_for_secs(open_order):
            return round((self.clock.timestamp_ns() - open_order.order.last_event.ts_event) / 1e9, 1)

        if self.config.trailing_take:
            if len(self.open_sells) > 0:
                ordered_sells = sorted(self.open_sells, key=lambda x: x.price)
                sells_str = "\n".join(
                    f"{o.leaves_qty} @ {o.price}\t OpenSecs {open_for_secs(o)}\t {o.order.venue_order_id}\t {o.order.client_order_id}"
                    for o in ordered_sells
                )
                msg = f"{round(self.vwap.mean_variance, 2)} {round(self.vwap.high, 3)}\n{sells_str}\n"
                return msg
        return ""

    def _on_order_filled(self, order_filled) -> None:
        if order_filled.is_buy:
            self.take_price = order_filled.last_px + self.take_profit
