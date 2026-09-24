import random
from datetime import time
from enum import StrEnum

import pandas as pd

from custom.nt_extensions.indicators import PressureVWAPBands, RollingVWAP, VWAPBands
from custom.strategies._side import Side
from custom.strategies.base import BaseStrategy, BaseStrategyConfig
from custom.strategies._exit_tiers import ExitTiers
from custom.strategies.metric import Metric
from nautilus_trader.common.enums import LogColor
from nautilus_trader.indicators.trend import MACDHistogram
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.identifiers import InstrumentId


class DirectionStrategy(StrEnum):
    LONG_ONLY = "long_only"
    SHORT_ONLY = "short_only"
    REVERSION = "reversion"  # Above the threshold -> SHORT, below -> LONG
    MOMENTUM = "momentum"  # Above the threshold -> LONG, below -> SHORT


class DirectionThreshold(StrEnum):
    ROLLING_VWAP = "rolling_vwap"
    OPEN = "open"  # First tick price at/after the market open; no threshold exists before then


class EntryStrategy(StrEnum):
    # Take the current price once it trades through the near VWAP band
    CROSS_VWAP_BAND = "cross_vwap_band"
    # Rest on the near VWAP band and reprice with it, rather than crossing the spread
    FOLLOW_VWAP_BAND = "follow_vwap_band"
    # Enter at random intervals
    RANDOM = "random"
    # Rest at a fixed distance from the direction threshold, on the side we trade into
    SIT_AT_DISTANCE = "sit_at_distance"


class MomoStrategyConfig(BaseStrategyConfig, frozen=True, kw_only=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float
    take_profit: float

    upper_scalar_multiplier: float = 1.0
    lower_scalar_multiplier: float | None = None  # Defaults to upper_scalar_multiplier
    vwap_window: int = 150
    variance_window: int = 300
    outer_band_multiplier: float = 1.0
    pressure_window: int = 50
    rolling_vwap_window: int = 1000

    direction_strategy: DirectionStrategy = DirectionStrategy.LONG_ONLY
    direction_threshold: DirectionThreshold = DirectionThreshold.OPEN
    flip_side_confirm_ticks: int = 150
    only_buy_if_macd_positive: bool = False
    entry_strategy: EntryStrategy = EntryStrategy.CROSS_VWAP_BAND
    # Distance from the direction threshold that `sit_at_distance` rests its entry at. Required
    # by that strategy, ignored by the others.
    entry_distance: float | None = None
    simple_take: bool = False
    trailing_take: bool = False
    num_exit_tiers: int = 1
    # "HH:MM" or "HH:MM:SS" in US/Eastern. Nothing trades until the clock reaches this time -- no entries, no
    # exits, no ticks acted on at all. None trades from the first tick received, which for a feed
    # that carries pre-market prints means trading before the open.
    start_trading_at: str | None = None
    # "HH:MM" or "HH:MM:SS" in US/Eastern. Once the clock reaches this time no new entries are placed for the
    # rest of the day; exits keep running. None disables the cutoff.
    stop_entries_after: str | None = None
    # Price distance from the direction threshold. With `reversion`, entries are blocked while the
    # price is within this band of the threshold. With `momentum`, entries are only allowed while
    # the price is within it. None disables the band; long_only/short_only ignore it.
    entry_exclusion_band: float | None = None

    print_update_every_secs: int = None
    allow_trades: bool = True


def parse_est_time(value: str | None) -> time | None:
    """Parse an "HH:MM" or "HH:MM:SS" wall-clock string into a `time`, or None when no value is given."""
    if value is None:
        return None
    try:
        parts = [int(part) for part in value.split(":")]
        if len(parts) not in (2, 3):
            raise ValueError
        return time(*parts)
    except (ValueError, TypeError):
        raise ValueError(
            f"Expected a time formatted as 'HH:MM' or 'HH:MM:SS' (e.g. '09:31' or '09:31:15'), got {value!r}",
        ) from None


def is_market_open(now_utc: pd.Timestamp) -> bool:
    now_est = now_utc.tz_convert("US/Eastern")
    market_open = now_est.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_est.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_est < market_close


class MomoStrategy(BaseStrategy):
    _ADJUST_EXITS_ONLY_EVERY_NS = 80e6  # e6 converts from ms to ns
    _ADJUST_ENTRIES_ONLY_EVERY_NS = 100e6
    _MAX_ALLOWED_EXIT_DIFF_SECS = 5
    _LOG_FLIP_WAIT_EVERY_NS = 5e9  # Throttle for the "flip still waiting" line

    def __init__(self, config: MomoStrategyConfig) -> None:
        super().__init__(config)
        if sum([self.config.trailing_take, self.config.simple_take]) > 1:
            raise ValueError("Cannot use more than one of simple_take, trailing_take")

        self.entry_strategy = EntryStrategy(self.config.entry_strategy)
        self.direction_strategy = DirectionStrategy(self.config.direction_strategy)
        self.direction_threshold = DirectionThreshold(self.config.direction_threshold)

        self._start_trading_at: time | None = parse_est_time(self.config.start_trading_at)
        self._stop_entries_after: time | None = parse_est_time(self.config.stop_entries_after)
        if (
            self._start_trading_at is not None
            and self._stop_entries_after is not None
            and self._start_trading_at >= self._stop_entries_after
        ):
            raise ValueError(
                f"start_trading_at {self.config.start_trading_at!r} is not before "
                f"stop_entries_after {self.config.stop_entries_after!r}, so no entry could ever be placed"
            )
        if self.config.entry_exclusion_band is not None and self.config.entry_exclusion_band < 0:
            raise ValueError(f"entry_exclusion_band must be >= 0 or None, got {self.config.entry_exclusion_band!r}")
        self._entry_exclusion_band: float | None = self.config.entry_exclusion_band

        if self.entry_strategy == EntryStrategy.SIT_AT_DISTANCE:
            if self.config.entry_distance is None:
                raise ValueError(f"entry_distance is required with entry_strategy '{self.entry_strategy}'")
            if self.config.entry_distance < 0:
                raise ValueError(f"entry_distance must be >= 0, got {self.config.entry_distance!r}")
            if self._entry_exclusion_band is not None:
                # Both measure from the direction threshold, and they contradict: the band blocks
                # entries near the threshold, which is exactly where this strategy rests its own.
                raise ValueError(f"entry_exclusion_band cannot be used with entry_strategy '{self.entry_strategy}'")
            if self.direction_strategy == DirectionStrategy.MOMENTUM:
                # Momentum reads a move away from the threshold as the signal, so it goes long
                # above it -- while this rests its long entry below it, fading the same move.
                raise ValueError(
                    f"direction_strategy '{DirectionStrategy.MOMENTUM}' cannot be used with "
                    f"entry_strategy '{self.entry_strategy}'"
                )

        self.take_profit = self.config.take_profit if self.config.take_profit is not None else self.config.stop_loss
        lower_scalar_multiplier = (
            self.config.lower_scalar_multiplier
            if self.config.lower_scalar_multiplier is not None
            else self.config.upper_scalar_multiplier
        )
        # self.vwap = PressureVWAPBands(
        self.vwap = VWAPBands(
            lower_scalar_multiplier=lower_scalar_multiplier,
            upper_scalar_multiplier=self.config.upper_scalar_multiplier,
            rolling_window=self.config.vwap_window,
            variance_window=self.config.variance_window,
            # Below used for PressureVWAPBands
            # outer_band_multiplier=self.config.outer_band_multiplier,
            # pressure_window=self.config.pressure_window,
        )
        # self.vwap_day = VolumeWeightedAveragePrice()
        self.rolling_vwap = RollingVWAP(rolling_window=self.config.rolling_vwap_window, update_every_secs=5)
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
            Metric(obj=self.rolling_vwap, name="rolling_vwap", attrs=["value"]),
            Metric(obj=self, name="", attrs=["direction_value"]),
        ]
        if self.config.only_buy_if_macd_positive:
            self.metrics_to_save_on_1min.append(Metric(obj=self.macd, name="macd", attrs=["value"]))

        self.last_entry_price = None

        self.metrics = []

        self._exit_diff_start_ns: int | None = None
        self._last_exit_adjustment_ns: int | None = None
        self._last_entry_adjustment_ns: int | None = None
        self._completed_first_tick_logic: bool = False

        self._pending_side_signal: Side | None = None
        self._side_signal_count: int = 0
        self._direction_threshold_value: float | None = None
        self._last_flip_wait_log_ns: int = 0

    def _on_first_tick(self, tick: TradeTick):
        if self._completed_first_tick_logic:
            return

        if self.direction_strategy == DirectionStrategy.LONG_ONLY:
            self.set_side(Side.LONG)
        elif self.direction_strategy == DirectionStrategy.SHORT_ONLY:
            self.set_side(Side.SHORT)
        else:
            pass
        if self._last_exit_adjustment_ns is None:
            self._last_exit_adjustment_ns = self.clock.timestamp_ns()
        if self._last_entry_adjustment_ns is None:
            self._last_entry_adjustment_ns = self.clock.timestamp_ns()

        self._completed_first_tick_logic = True

    def _update_direction_threshold(self, tick: TradeTick) -> None:
        if self.direction_threshold == DirectionThreshold.ROLLING_VWAP:
            # `initialized` is True after one tick, which says nothing about how much data is
            # behind `value`. Wait for a full window before acting on it.
            if self.rolling_vwap.window_full:
                self._direction_threshold_value = self.rolling_vwap.value
        elif self.direction_threshold == DirectionThreshold.OPEN:
            # The first tick seen at/after the open. Set once, never reset (single-day runs).
            if self._direction_threshold_value is None and is_market_open(self.clock.utc_now()):
                self._direction_threshold_value = float(tick.price)

    def _flip_side_if_needed(self, tick: TradeTick) -> None:
        """
        `set_side` only switches while flat with nothing resting, so a flip is two phases. Phase
        one sets `_flipping`, which blocks new entries and cancels the resting ones while the
        existing exits are left to fill on their own. Phase two switches the side, and only once
        the position is confirmed flat -- flipping `_side` with stock still on means `exposure`
        goes negative, which sends `_rolling_tiered_take` into reconciliation and trips the
        wrong-way flatten in `_reconcile`.

        The signal compares the tick price to the direction threshold; `direction_strategy`
        decides which side "above" means. A tick exactly on the threshold is ignored: it neither
        advances nor resets the confirm counter.
        """
        # Always tracked, even when the side is fixed, so the threshold can be charted.
        self._update_direction_threshold(tick)
        if self.direction_strategy in (DirectionStrategy.LONG_ONLY, DirectionStrategy.SHORT_ONLY):
            return
        threshold = self._direction_threshold_value
        if threshold is None:
            return

        price = float(tick.price)
        if price != threshold:
            self._count_side_signal(self._signal_for(price > threshold))

        if not self._flipping:
            return

        # Entries would deepen the position we are trying to close out of. Re-issued every tick
        # rather than once: cancel_open_order is a silent no-op until the venue id lands, and a
        # rejected cancel can put the order back (see on_order_event in base).
        for order in self.entries_to_cancel:
            self.cancel_open_order(order)

        # The venue decides when the flip may complete, not the local books: `cancel_open_order`
        # empties those as soon as it sends, and a cancel refused mid-modify leaves the order
        # working. Completing here on the local view is what strands an old-side entry at the
        # venue, where it fills against the new side.
        live_orders = self.orders_live_at_venue
        if self.position_qty == 0 and len(live_orders) == 0:
            self.set_side(self._pending_side_signal)
            self._flipping = False
        else:
            self._log_flip_wait(live_orders)

    def _log_flip_wait(self, live_orders) -> None:
        """
        Say what a flip is still waiting on, at most once every `_LOG_FLIP_WAIT_EVERY_NS`.

        A cancel the venue keeps refusing holds the flip open indefinitely, which is the safe
        outcome but an invisible one -- without this the strategy simply looks stuck.
        """
        now_ns = self.clock.timestamp_ns()
        if now_ns - self._last_flip_wait_log_ns < self._LOG_FLIP_WAIT_EVERY_NS:
            return
        self._last_flip_wait_log_ns = now_ns
        waiting_on = [f"{order.client_order_id} ({order.status_string()})" for order in live_orders]
        self.log.info(
            f"Flip to '{self._pending_side_signal}' waiting on position {self.position_qty} "
            f"and {len(live_orders)} order(s) live at the venue: {waiting_on}",
            color=LogColor.YELLOW,
        )

    def _signal_for(self, above_threshold: bool) -> Side:
        if self.direction_strategy == DirectionStrategy.REVERSION:
            return Side.SHORT if above_threshold else Side.LONG
        return Side.LONG if above_threshold else Side.SHORT  # MOMENTUM

    def _count_side_signal(self, signal: Side) -> None:
        if signal == self._pending_side_signal:
            self._side_signal_count += 1
        else:
            self._pending_side_signal = signal
            self._side_signal_count = 1

        if self._side_signal_count >= self.config.flip_side_confirm_ticks:
            # One rule arms and disarms, so calling off a flip costs the same N ticks as starting
            # one. Without that symmetry a single tick back across the line would un-arm a flip
            # that has already canceled its entries, and the pair would thrash near the signal line.
            was_flipping = self._flipping
            self._flipping = signal != self._side
            if was_flipping and not self._flipping:
                # `signal` is the side we just reconfirmed, i.e. the one we already are -- the
                # abandoned flip was to its opposite, so don't name `signal` as the target here.
                self.log.info(f"Flip called off; staying '{self._side}'", color=LogColor.YELLOW)
            elif self._flipping and not was_flipping:
                self.log.info(f"Flipping from '{self._side}' to '{signal}'", color=LogColor.YELLOW)

    def _on_trade_tick(self, tick: TradeTick) -> None:
        self._on_first_tick(tick)
        # self.log.info(f"Trade tick: {tick}")
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        self._flip_side_if_needed(tick)
        if not self._trading_has_started():
            return

        price = tick.price

        entry_orders = self.open_entries
        exposure = self.exposure

        allow_entries = True
        if self._stopping_out:
            allow_entries = False
        if self._flipping:
            allow_entries = False
        if self.config.only_buy_if_macd_positive:
            if not self.macd.initialized or self.macd.value < 0:
                allow_entries = False
        if self._entries_stopped_for_the_day():
            allow_entries = False
        if self._entry_blocked_by_exclusion_band(float(price)):
            allow_entries = False

        # ENTRY LOGIC ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        if (
            allow_entries
            and (exposure < self.max_position_allowed)
            and (self.clock.timestamp_ns() - self._last_entry_adjustment_ns > self._ADJUST_ENTRIES_ONLY_EVERY_NS)
        ):
            self._last_entry_adjustment_ns = self.clock.timestamp_ns()

            if self.entry_strategy == EntryStrategy.RANDOM:
                if (
                    len(entry_orders) == 0
                    and (self.clock.utc_now() - self.last_entry_dt).total_seconds() > 20
                    and random.random() < 0.3
                ):
                    # Only send the entry command if it has been at least 10 seconds of flat
                    all_positions = self.cache.positions(instrument_id=self.config.instrument_id)
                    if len(all_positions) > 0:
                        most_recent_close = all_positions[0].ts_closed
                    else:
                        most_recent_close = 0

                    if (self.clock.timestamp_ns() - most_recent_close) / 1e9 > 10:
                        entry_limit = price
                        self.enter(self.config.trade_size, entry_limit, cancel_after_secs=10)

            elif self.entry_strategy == EntryStrategy.FOLLOW_VWAP_BAND:
                # enter() submits a buy when long and a sell when short, so trail the near
                # band on the side the entry sits: the lower band when long, upper when short.
                trail_price = self.instrument.make_price(self.vwap.high if self.is_short else self.vwap.low)
                for order in self.open_entries:
                    if order.price != trail_price:
                        self.modify_open_order(order, quantity=order.quantity, price=trail_price)

                if len(entry_orders) == 0:
                    self.enter(self.config.trade_size, trail_price, cancel_after_secs=None)

            elif self.entry_strategy == EntryStrategy.SIT_AT_DISTANCE:
                # Keep one entry resting `entry_distance` from the threshold, below it when long
                # and above it when short. Nothing rests until the threshold exists, and it is
                # repriced whenever the threshold moves (`rolling_vwap`) rather than re-sent.
                threshold = self._direction_threshold_value
                if threshold is not None:
                    offset = -self.config.entry_distance if self.is_long else self.config.entry_distance
                    entry_price = self.instrument.make_price(threshold + offset)
                    for order in self.open_entries:
                        if order.price != entry_price:
                            self.modify_open_order(order, quantity=order.quantity, price=entry_price)

                    if len(entry_orders) == 0:
                        self.enter(self.config.trade_size, entry_price, cancel_after_secs=None)

            elif self.entry_strategy == EntryStrategy.CROSS_VWAP_BAND and (
                (self.is_long and price < self.vwap.low) or (self.is_short and price > self.vwap.high)
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

    def _trading_has_started(self) -> bool:
        """
        True once the Eastern wall clock has reached `start_trading_at`.

        The mirror of `_entries_stopped_for_the_day`, except this gates the whole tick rather
        than entries alone: before the start there is nothing to exit, so nothing to run.
        """
        if self._start_trading_at is None:
            return True
        now_est = self.clock.utc_now().tz_convert("US/Eastern")
        return now_est.time() >= self._start_trading_at

    def _entries_stopped_for_the_day(self) -> bool:
        """True once the Eastern wall clock has reached `stop_entries_after`."""
        if self._stop_entries_after is None:
            return False
        now_est = self.clock.utc_now().tz_convert("US/Eastern")
        return now_est.time() >= self._stop_entries_after

    def _entry_blocked_by_exclusion_band(self, price: float) -> bool:
        """Apply `entry_exclusion_band` around the direction threshold."""
        band = self._entry_exclusion_band
        if band is None:
            return False
        threshold = self._direction_threshold_value
        if self.direction_strategy == DirectionStrategy.REVERSION:
            # Reversion fades the threshold, so entries right on top of it are the low-edge ones
            # and get blocked. No threshold yet means nothing to measure against, so unaffected.
            return threshold is not None and abs(price - threshold) <= band
        if self.direction_strategy == DirectionStrategy.MOMENTUM:
            # Momentum wants to join a move away from the threshold, so only entries still close
            # to it are allowed. No threshold yet means an entry cannot qualify.
            return threshold is None or abs(price - threshold) > band
        return False

    def _rolling_tiered_take(self):
        exposure_qty = self.exposure
        if exposure_qty == 0:
            return
        elif exposure_qty < 0:
            self.log.info(f"Exposure {exposure_qty} is negative (wrong-way). Running reconciliation.")
            self._reconcile()
            return
        tiers = ExitTiers(
            direction=self.side,
            quantity=exposure_qty,
            # Ladder away from the band the position is exiting into: up from the upper band when
            # long, down from the lower band when short.
            starting_price=self.vwap.high if self.is_long else self.vwap.low,
            mean_variance=self.vwap.mean_variance,
            num_tiers=self.config.num_exit_tiers,
        )
        orders_to_be_modified = []
        existing_open_exit_qty = 0
        qty_taken_in_tiers = 0
        for i, open_order in enumerate(sorted(self.open_exits, key=lambda order: order.price, reverse=self.is_short)):
            existing_open_exit_qty += open_order.leaves_qty
            if existing_open_exit_qty > exposure_qty:
                self.log.info(
                    f"Existing open exit qty {existing_open_exit_qty} is greater than position qty {exposure_qty}. "
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
        available_qty_increase = exposure_qty - existing_open_exit_qty

        while len(tiers.available_prices) > 0:
            # Use the available price nearest the market
            price = tiers.pop_next_price()

            # Only exit up to (exposure_qty - qty_taken_in_tiers) to limit "insufficient qty" error
            qty_to_exit = exposure_qty - qty_taken_in_tiers

            # Minimize to tier max if there are more prices to exit at after this one
            if len(tiers.available_prices) > 0:
                qty_to_exit = min(tiers.max_qty_per_tier, qty_to_exit)

            if qty_to_exit > 0:
                # Modify an existing order if possible, otherwise create a new one
                if len(orders_to_be_modified) > 0:
                    open_order = orders_to_be_modified.pop(0)
                    # Need to adjust based on leaves_qty incase order is already partially filled
                    requested_qty_change = qty_to_exit - open_order.leaves_qty

                    qty_change = min(requested_qty_change, available_qty_increase)  # can be negative
                    new_order_qty = open_order.quantity + qty_change  # should be positive
                    if new_order_qty > 0:
                        qty_taken_in_tiers += new_order_qty - open_order.filled_qty  # Only include unfilled qty
                        # WARNING: modify_open_order does not always do anything. It has other checks about qty
                        # and frequency of modifications.
                        self.modify_open_order(open_order, quantity=new_order_qty, price=price)
                    else:
                        self.log.error(
                            f"Requesting new_order_qty of {new_order_qty}. Skipping modification. exposure_qty: {exposure_qty}."
                        )
                elif any(o.order.status == OrderStatus.PENDING_UPDATE for o in self.open_exits):
                    # Don't create new exit orders while existing exits are mid-modification,
                    # since the venue still holds the old (larger) qty until the replace confirms.
                    # This check is inside the loop (not before it) because a modify earlier in
                    # this same loop iteration can put an order into PENDING_UPDATE.
                    self.log.debug("Skipping new exit: existing exit order is PENDING_UPDATE")
                    break
                else:
                    self.exit(qty_to_exit, price, cancel_after_secs=None)
                    qty_change = qty_to_exit
                    qty_taken_in_tiers += qty_to_exit
                if qty_change > 0:
                    # Only reduce if qty_change is positive
                    available_qty_increase -= qty_change

        # Cancel left-over orders
        for order in orders_to_be_modified:
            self.log.info(f"Canceling left over order_to_be_modified: {order}")
            self.cancel_open_order(order)

        # If (position - exits) is non_zero for more than _MAX_ALLOWED_EXIT_DIFF_SECS, exit diff at lowest tier
        # in a new order.
        exit_diff = self.exposure - self.open_exits_qty
        if exit_diff == 0:
            self._exit_diff_start_ns = None
        elif self._exit_diff_start_ns is None:
            self._exit_diff_start_ns = self.clock.timestamp_ns()
        elif (self.clock.timestamp_ns() - self._exit_diff_start_ns) / 1e9 > self._MAX_ALLOWED_EXIT_DIFF_SECS:
            self.log.info(
                f"Adding exit order for {exit_diff} at nearest tier because exit_diff existed for more than {self._MAX_ALLOWED_EXIT_DIFF_SECS} secs."
            )
            self.exit(exit_diff, tiers.nearest_price, cancel_after_secs=None)

    def _print_update(self):
        def open_for_secs(open_order):
            return round((self.clock.timestamp_ns() - open_order.order.last_event.ts_event) / 1e9, 1)

        if self.config.trailing_take:
            if len(self.open_exits) > 0:
                ordered_exits = sorted(self.open_exits, key=lambda x: x.price)
                exits_str = "\n".join(
                    f"{o.leaves_qty} @ {o.price}\t OpenSecs {open_for_secs(o)}\t {o.order.venue_order_id}\t {o.order.client_order_id}"
                    for o in ordered_exits
                )
                msg = f"{round(self.vwap.mean_variance, 2)} {round(self.vwap.high, 3)}\n{exits_str}\n"
                return msg
        return ""

    def _on_order_filled(self, order_filled) -> None:
        if order_filled.order_side == self._entry_order_side:
            self.last_entry_price = order_filled.last_px
