import random

import pandas as pd

from custom.nt_extensions.indicators import PressureVWAPBands, VWAPBands
from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from custom.strategies._exit_tiers import ExitTiers
from custom.strategies.metric import Metric
from nautilus_trader.indicators.trend import MACDHistogram
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.identifiers import InstrumentId


class MomoStrategyConfig(BaseStrategyConfig, frozen=True, kw_only=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float

    take_profit: float
    lower_scalar_multiplier: float = 1.0
    upper_scalar_multiplier: float = 1.0
    vwap_window: int = 150
    variance_window: int = 300
    outer_band_multiplier: float = 1.0
    pressure_window: int = 50

    only_buy_if_macd_positive: bool = False
    trailing_buy_order: bool = False
    simple_take: bool = False
    trailing_take: bool = False
    random_entry: bool = False
    num_exit_tiers: int = 1
    print_update_every_secs: int = None

    allow_trades: bool = True


def is_market_open(now_utc: pd.Timestamp) -> bool:
    now_est = now_utc.tz_convert("US/Eastern")
    market_open = now_est.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_est.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_est < market_close


class MomoStrategy(BaseStrategy):
    _ADJUST_EXITS_ONLY_EVERY_NS = 80e6  # e6 converts from ms to ns
    _MAX_ALLOWED_SELL_DIFF_SECS = 5

    def __init__(self, config: MomoStrategyConfig) -> None:
        super().__init__(config)
        if sum([self.config.trailing_take, self.config.simple_take]) > 1:
            raise ValueError("Cannot use more than one of simple_take, trailing_take")

        if sum([self.config.trailing_buy_order, self.config.random_entry]) > 1:
            raise ValueError("Cannot use more than one of trailing_buy_order, random_entry")
        # FIXME: This is temporary
        self.take_profit = self.config.take_profit if self.config.take_profit is not None else self.config.stop_loss
        self.market_open_only = False

        # self.vwap = PressureVWAPBands(
        self.vwap = VWAPBands(
            lower_scalar_multiplier=self.config.lower_scalar_multiplier,
            upper_scalar_multiplier=self.config.upper_scalar_multiplier,
            rolling_window=self.config.vwap_window,
            variance_window=self.config.variance_window,
            # Below used for PressureVWAPBands
            # outer_band_multiplier=self.config.outer_band_multiplier,
            # pressure_window=self.config.pressure_window,
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
                    # Below used for PressureVWAPBands
                    # "low_inner",
                    # "low_outer",
                    # "high_inner",
                    # "high_outer",
                    # "pressure",
                ],
            ),
        ]
        if self.config.only_buy_if_macd_positive:
            self.metrics_to_save_on_1min.append(Metric(obj=self.macd, name="macd", attrs=["value"]))

        self.last_entry_price = None

        self.metrics = []

        self._sell_diff_start_ns: int | None = None
        self._last_exit_adjustment_ns: int | None = None

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # self.log.info(f"Trade tick: {tick}")
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        if self.market_open_only and not is_market_open(self.clock.utc_now()):
            return
        if self._last_exit_adjustment_ns is None:
            self._last_exit_adjustment_ns = self.clock.timestamp_ns()

        price = tick.price

        entry_orders = self.open_entries
        exposure = self.exposure

        allow_trading = True
        if self._stopping_out:
            allow_trading = False
        if self.config.only_buy_if_macd_positive:
            if not self.macd.initialized or self.macd.value < 0:
                allow_trading = False

        # ENTRY LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if allow_trading:
            if self.config.random_entry:
                if (
                    len(entry_orders) == 0
                    and (self.clock.utc_now() - self.last_entry_dt).total_seconds() > 20
                    and exposure < self.max_position_allowed
                    and random.random() < 0.3
                ):
                    # Only send the entry command if it has been at least 10 seconds of flat
                    all_positions = self.cache.positions(instrument_id=self.config.instrument_id)
                    if len(all_positions) > 0:
                        most_recent_close = all_positions[0].ts_closed
                    else:
                        most_recent_close = 0

                    if (self.clock.timestamp_ns() - most_recent_close) / 1e9 > 10:
                        # enter() submits a buy when long and a sell when short, so the only
                        # thing this branch decides is the price -- and at a zero offset the
                        # entry sits on the tick either way.
                        entry_limit = tick.price + 0.00
                        self.enter(self.config.trade_size, entry_limit, cancel_after_secs=10)
            elif self.config.trailing_buy_order:
                vwap_lower = self.instrument.make_price(self.vwap.low)
                for order in self.open_entries:
                    if order.price != vwap_lower:
                        self.modify_open_order(order, quantity=order.quantity, price=vwap_lower)

                if len(entry_orders) == 0 and (self.clock.utc_now() - self.last_entry_dt).total_seconds() > 1:
                    # FIXME: Clunky to add entry orders count tag here. Should be handled in enter()
                    self.enter(self.config.trade_size, vwap_lower, cancel_after_secs=None)
            elif (
                price < self.vwap.low
                # and price_1ago > self.vwap.low
                # and (price > price_1ago)
            ):
                if (
                    exposure < self.max_position_allowed
                    # and tick.size > 1
                    # and (self.clock.utc_now() - self.last_buy_ts).total_seconds() > random.randint(1, 20)
                    and (self.clock.utc_now() - self.last_entry_dt).total_seconds() > 1
                ):
                    self.enter(self.config.trade_size, price, cancel_after_secs=1)

        # TAKE LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if not self._stopping_out and (
            self.clock.timestamp_ns() - self._last_exit_adjustment_ns > self._ADJUST_EXITS_ONLY_EVERY_NS
        ):
            self._last_exit_adjustment_ns = self.clock.timestamp_ns()

            if self.config.trailing_take:
                self._rolling_tiered_take()
            elif self.config.simple_take:
                if self.open_exits_qty < exposure:
                    if self.is_long:
                        take_price = self.last_entry_price + self.take_profit
                    else:
                        take_price = self.last_entry_price - self.take_profit

                    self.exit(exposure, limit_price=take_price, tag="simple")

    def _rolling_tiered_take(self):
        position_qty = self.exposure
        if position_qty == 0:
            return
        elif position_qty < 0:
            self.log.info(f"Exposure {position_qty} is negative (wrong-way). Running reconciliation.")
            self._reconcile()
            return
        tiers = ExitTiers(
            direction=self.side,
            quantity=position_qty,
            # Ladder away from the band the position is exiting into: up from the upper band when
            # long, down from the lower band when short.
            starting_price=self.vwap.high if self.is_long else self.vwap.low,
            mean_variance=self.vwap.mean_variance,
            num_tiers=self.config.num_exit_tiers,
        )
        orders_to_be_modified = []
        existing_open_sell_qty = 0
        qty_taken_in_tiers = 0
        for i, open_order in enumerate(sorted(self.open_exits, key=lambda order: order.price, reverse=self.is_short)):
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
                and (i + 1) == len(self.open_exits)  # This is the last open exit in self.open_exits
                and len(orders_to_be_modified) == 0  # No orders to be modified
                and len(tiers.available_prices) > 0  # At least one available tier
                and tiers.nearest_available_price == tiers.nearest_price  # Nearest tier still available
                and open_order.price != tiers.nearest_price  # This order is not at the nearest tier
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
            # Use the available price nearest the market
            price = tiers.pop_next_price()

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
                elif any(o.order.status == OrderStatus.PENDING_UPDATE for o in self.open_exits):
                    # Don't create new sell orders while existing sells are mid-modification,
                    # since the venue still holds the old (larger) qty until the replace confirms.
                    # This check is inside the loop (not before it) because a modify earlier in
                    # this same loop iteration can put an order into PENDING_UPDATE.
                    self.log.debug("Skipping new exit: existing exit order is PENDING_UPDATE")
                    break
                else:
                    self.exit(qty_to_sell, price, cancel_after_secs=None)
                    qty_change = qty_to_sell
                    qty_taken_in_tiers += qty_to_sell
                if qty_change > 0:
                    # Only reduce if qty_change is positive
                    available_qty_increase -= qty_change

        # Cancel left-over orders
        for order in orders_to_be_modified:
            self.log.info(f"Canceling left over order_to_be_modified: {order}")
            self.cancel_open_order(order)

        # If (position - sells) is non_zero for more than _MAX_ALLOWED_SELL_DIFF_SECS, sell diff at lowest tier
        # in a new order.
        sell_diff = self.exposure - self.open_exits_qty
        if sell_diff == 0:
            self._sell_diff_start_ns = None
        elif self._sell_diff_start_ns is None:
            self._sell_diff_start_ns = self.clock.timestamp_ns()
        elif (self.clock.timestamp_ns() - self._sell_diff_start_ns) / 1e9 > self._MAX_ALLOWED_SELL_DIFF_SECS:
            self.log.info(
                f"Adding sell order for {sell_diff} at nearest tier because sell_diff existed for more than {self._MAX_ALLOWED_SELL_DIFF_SECS} secs."
            )
            self.exit(sell_diff, tiers.nearest_price, cancel_after_secs=None)

    def _print_update(self):
        def open_for_secs(open_order):
            return round((self.clock.timestamp_ns() - open_order.order.last_event.ts_event) / 1e9, 1)

        if self.config.trailing_take:
            if len(self.open_exits) > 0:
                ordered_sells = sorted(self.open_exits, key=lambda x: x.price)
                sells_str = "\n".join(
                    f"{o.leaves_qty} @ {o.price}\t OpenSecs {open_for_secs(o)}\t {o.order.venue_order_id}\t {o.order.client_order_id}"
                    for o in ordered_sells
                )
                msg = f"{round(self.vwap.mean_variance, 2)} {round(self.vwap.high, 3)}\n{sells_str}\n"
                return msg
        return ""

    def _on_order_filled(self, order_filled) -> None:
        if self.is_long and order_filled.is_buy:
            self.last_entry_price = order_filled.last_px
        elif self.is_short and not order_filled.is_sell:
            self.last_entry_price = order_filled.last_px
