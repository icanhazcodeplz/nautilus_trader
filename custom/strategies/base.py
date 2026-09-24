import asyncio
from abc import abstractmethod
from collections import deque
from itertools import pairwise
from datetime import time
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from pandas import Timestamp

from custom.artifacts import ArtifactsIO
from custom.strategies._open_order import OpenOrder, CLOSED_STATUS_LIST, FLATTEN_TAG
from custom.strategies._side import Side  # noqa: F401  (re-exported: `from ...base import Side`)
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper
from custom.utils.precision_utils import make_Price
from nautilus_trader.adapters.alpaca.execution import MODIFY_HELD_PENDING_NEW_REASON
from nautilus_trader.common.component import TimeEvent

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.core.message import Event
from nautilus_trader.indicators.base import Indicator
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BarType, Bar
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick

from nautilus_trader.model.enums import OrderSide, OrderStatus
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.events import OrderRejected, OrderModifyRejected, OrderCancelRejected
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import LimitOrder, Order
from nautilus_trader.model.orders.list import OrderList
from nautilus_trader.trading.strategy import Strategy


ET_TZ = "US/Eastern"
NS_PER_SEC = 1_000_000_000


def parse_et_time(value: time | str) -> time:
    """Parse an "HH:MM" or "HH:MM:SS" US/Eastern wall-clock string into a `time`."""
    if isinstance(value, time):
        return value
    try:
        parts = [int(part) for part in value.split(":")]
    except (AttributeError, ValueError):
        raise ValueError(f"Expected a time formatted as 'HH:MM' or 'HH:MM:SS', got {value!r}") from None
    if len(parts) == 2:
        parts.append(0)
    if len(parts) != 3:
        raise ValueError(f"Expected a time formatted as 'HH:MM' or 'HH:MM:SS', got {value!r}")
    hour, minute, second = parts
    return time(hour=hour, minute=minute, second=second)


class BaseStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float
    allow_trades: bool = True
    print_update_every_secs: int = None


class BaseStrategy(Strategy):
    MIN_TICK_LOOKBACK: int = 0
    _POSITION_DISCREPANCY_ALLOW_SECS = 10  # Raise if alpaca vs nt discrepancy lasts for longer than this
    _MODIFY_REJECT_COOLDOWN_SECS = 1  # Seconds to block retries after a ModifyRejected
    _RECONCILE_COOLDOWN_SECS = 3  # Minimum seconds between reconciliation attempts
    _ATTEMPT_STOP_OUT_EVERY_MS = 60
    _DELAY_RELEASE_INTERVAL_MS = 100  # How often the delay buffer is checked for ticks now due

    def __init__(self, config: BaseStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument = None  # Initialized in on_start
        self.metrics_to_save_on_tick = []
        self.metrics_to_save_on_1min = []

        self.stop_price = None
        self.stop_loss: float | None = None
        self.last_entry_dt: Timestamp = pd.Timestamp("1990", tz="UTC")
        self._side: Side = Side.LONG  # Switch with self.set_side()
        self._side_changed_ns: int = 0  # When set_side last switched; 0 while the side is original
        self._trading_enabled: bool = True
        self.allow_trading_times: Optional[set[pd.Timestamp]] = None
        self.internal_bars = False

        self._tick_data_dicts = {}
        self._tick_event_dt_adjusted = 0

        self.entry_orders_count = 0

        # Used to track order modifications to avoid sending duplicate modify orders when the cache is slow
        self._already_cancelled_orders = set()
        self._uncached_orders = set()

        self._initialized = False
        self._artifacts_io = None
        self.save_artifacts = False
        self._trader_helper = None

        self._buy_orders: set[OpenOrder] = set()
        self._sell_orders: set[OpenOrder] = set()
        self._position_discrepancy_start_ns = None
        self._raise_msg = None
        self._last_tick = None
        self._total_entry_qty = 0

        self._stopping_out = False
        self._flipping = False  # Waiting to go flat so set_side() can switch.
        self._last_stop_out_attempt = 0
        self._exec_engine = None  # Set by run_utils after node.build()
        self._force_reconcile_count = 0
        self._last_force_reconcile_ns = 0
        self._last_wrong_way_flatten_ns = 0
        self._reconciliation_task: asyncio.Task | None = None
        self._historical_loaded = False
        self._historical_ticks: list[TradeTick] = []

        # Data delay buffer (backtests only, see set_data_delay)
        self._tick_delay_enabled = False
        self._tick_delay_default_ns: int = 0
        self._tick_delay_windows: list[tuple[time, time, int]] = []
        self._delayed_ticks: deque[TradeTick] = deque()
        self._window_bounds: list[tuple[int, int, int]] = []  # [start_ns, stop_ns, delay_ns) for one ET day
        self._window_bounds_day: tuple[int, int] | None = None  # ET day the bounds above were built for
        self._tick_indicators: list[Indicator] = []  # Fed from the buffer while the delay is on
        self._feed_indicators_manually = False

    def initialize(self, artifacts_location: Optional[Path], trader_helper: Optional[AlpacaTraderHelper] = None):
        self._initialized = True
        self._trader_helper = trader_helper
        if artifacts_location is not None:
            self._artifacts_io = ArtifactsIO(artifacts_location)
            self.save_artifacts = True

    def _add_tick_data(self, ts_event, data_dict: dict):
        if ts_event in self._tick_data_dicts:
            self._tick_data_dicts[ts_event] |= data_dict
        else:
            self._tick_data_dicts[ts_event] = data_dict

    def set_trading_enabled(self, new_trading_enabled: bool):
        if new_trading_enabled != self._trading_enabled:
            self._trading_enabled = new_trading_enabled
            self.log.info(f"trading_enabled set to {new_trading_enabled}", color=LogColor.YELLOW)
            if self.save_artifacts:
                self._add_tick_data(self.clock.timestamp_ns(), {"allow_trading": int(new_trading_enabled)})

    @property
    def trading_enabled(self):
        return self._trading_enabled

    # SIDE ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    @property
    def side(self) -> Side:
        return self._side

    @property
    def is_long(self) -> bool:
        return self._side == Side.LONG

    @property
    def is_short(self) -> bool:
        return self._side == Side.SHORT

    @property
    def _side_sign(self) -> int:
        """
        +1 when long, -1 when short.

        Multiply a raw signed quantity by this to express it in the direction we intend to trade.
        """
        return 1 if self._side == Side.LONG else -1

    @property
    def direction_value(self) -> int:
        """+1 when long, -1 when short. Saved per tick as a metric so the side can be charted."""
        return self._side_sign

    @property
    def _entry_order_side(self) -> OrderSide:
        """The venue side that opens/increases the position."""
        return OrderSide.BUY if self.is_long else OrderSide.SELL

    @property
    def _exit_order_side(self) -> OrderSide:
        """The venue side that closes/reduces the position."""
        return OrderSide.SELL if self.is_long else OrderSide.BUY

    def set_side(self, new_side: Side | str) -> None:
        """
        Switch between Side.LONG and Side.SHORT.

        Accepts the enum or its plain string value. This is the only supported way to change
        side: it is where the value is validated and the guards below are enforced.

        Only allowed while flat with nothing live at the venue. Open orders matter as much as
        the position: on a netting venue, orders left over from the old side can fill alongside
        the new side's and net to zero, stranding the exits. Cancels are not instant and can be
        rejected, so the check is against the cache (`orders_live_at_venue`) and not the local
        books -- a caller that wants to flip should cancel, keep asking until the cancels
        confirm, and only then call this.
        """
        try:
            new_side = Side(new_side)
        except ValueError:
            valid = "', '".join(s.value for s in Side)
            raise ValueError(f"side must be one of '{valid}', got {new_side!r}") from None
        if new_side == self._side:
            return
        if self.position_qty != 0:
            raise RuntimeError(f"Cannot switch side to '{new_side}' while position is {self.position_qty}")
        live_orders = self.orders_live_at_venue
        if len(live_orders) > 0:
            raise RuntimeError(
                f"Cannot switch side to '{new_side}' with {len(live_orders)} order(s) still live at the venue: "
                f"{[f'{order.client_order_id} ({order.status_string()})' for order in live_orders]}"
            )
        self.log.info(f"Switching side from '{self._side}' to '{new_side}'", color=LogColor.YELLOW)
        self._side = new_side
        self._side_changed_ns = self.clock.timestamp_ns()

    @property
    def position_qty(self):
        """Raw signed net position. Positive is long, negative is short, regardless of self.side."""
        return int(self.portfolio.net_position(self.config.instrument_id))

    @property
    def exposure(self) -> int:
        """
        Position size in the direction we intend to trade.

        Positive means positioned correctly for self.side, negative means wrong-way.
        """
        return self.position_qty * self._side_sign

    def _prune_closed(self) -> None:
        """
        Drop closed orders from the tracked sets.

        Both lines rebind to a new set rather than mutating in place, and that is deliberate:
        callers iterate the set returned by open_entries/open_exits while calling
        cancel_open_order(), which discards from the rebound set, leaving the iteration
        unaffected. Do not rewrite this as an in-place discard.
        """
        self._buy_orders = {open_order for open_order in self._buy_orders if open_order.is_open}
        self._sell_orders = {open_order for open_order in self._sell_orders if open_order.is_open}

    @property
    def open_orders(self) -> set[OpenOrder]:
        self._prune_closed()
        return self._buy_orders | self._sell_orders

    @property
    def orders_live_at_venue(self) -> list[Order]:
        """
        Orders the venue may still fill, read from the cache rather than the local books.

        `cancel_open_order` drops an order from `_buy_orders`/`_sell_orders` the instant the
        cancel is *sent*, so `open_orders` empties while the order is still working. The venue
        can also refuse the cancel outright -- "original order pending replacement" is the
        common one mid-modify -- which puts the order back. The cache keeps an order until the
        venue confirms it is gone, so it is what to ask before doing anything that assumes
        nothing can fill.
        """
        instrument_id = self.config.instrument_id
        return self.cache.orders_open(instrument_id=instrument_id) + self.cache.orders_inflight(
            instrument_id=instrument_id,
        )

    def _is_from_a_previous_side(self, open_order: OpenOrder) -> bool:
        """Whether this order was submitted before the current side was adopted."""
        return self._side_changed_ns > 0 and open_order.order.ts_init < self._side_changed_ns

    @property
    def orders_from_a_previous_side(self) -> set[OpenOrder]:
        """
        Orders that outlived a side switch, and so belong to neither entries nor exits.

        A switch is only allowed once nothing is live at the venue, so these should not exist.
        They appear when a cancel is refused after the books had already dropped the order --
        `on_order_event` puts it back, and the books are keyed by venue side, so a leftover
        short entry re-files itself as a long's exit. Read as an exit it looks like position
        protection, is counted in `open_exits_qty`, and is never cancelled.
        """
        return {open_order for open_order in self.open_orders if self._is_from_a_previous_side(open_order)}

    @property
    def open_entries(self) -> set[OpenOrder]:
        """Open orders that open/increase the position."""
        self._prune_closed()
        orders = self._buy_orders if self.is_long else self._sell_orders
        return {open_order for open_order in orders if not self._is_from_a_previous_side(open_order)}

    @property
    def entries_to_cancel(self) -> set[OpenOrder]:
        """
        The entries a "cancel what would open a position" sweep should take.

        `_reconcile` unwinds a wrong-way position with a marketable limit on the entry side, so
        it lands in the same bucket as a real entry. Sweeping it away leaves the position
        wrong-way and the sweep re-sends it moments later.
        """
        return {open_order for open_order in self.open_entries if not open_order.is_flatten}

    @property
    def open_exits(self) -> set[OpenOrder]:
        """Open orders that close/reduce the position."""
        self._prune_closed()
        orders = self._sell_orders if self.is_long else self._buy_orders
        return {open_order for open_order in orders if not self._is_from_a_previous_side(open_order)}

    @property
    def open_entries_qty(self) -> int:
        return int(sum(o.leaves_qty for o in self.open_entries))

    @property
    def open_exits_qty(self) -> int:
        return int(sum(o.leaves_qty for o in self.open_exits))

    def clear_open_order_modify_params(self, order_event):
        # Find the OpenOrder based on the nt cache `order` and reset last_modify vals
        order_found = False
        for open_order in self.open_orders:
            if open_order.client_order_id == order_event.client_order_id:
                order_found = True
                self.log.debug(f"Clearing open order modify params for {order_event}")
                open_order.reset_last_modify_vals()
        if not order_found:
            self.log.debug(
                f"Order not found in self.open_orders while attempting to reset modify params, order event {order_event}"
            )

    def _remove_open_order(self, order):
        self._buy_orders.discard(order)
        self._sell_orders.discard(order)

    @property
    def position_avg_px(self):
        positions_open = self.cache.positions_open(instrument_id=self.config.instrument_id)
        if len(positions_open) == 0:
            return 0
            # raise RuntimeError("No position open")
        if len(positions_open) == 1:
            position = positions_open[0]
            position_average = position.avg_px_open
            return position_average
        else:
            raise RuntimeError("Multiple positions open")

    def modify_open_order(self, open_order: OpenOrder, quantity, price):
        now_ns = self.clock.timestamp_ns()
        qty_obj = self.instrument.make_qty(quantity)
        price_obj = make_Price(price)
        if open_order.update_last_modify_if_allowed(qty_obj, price_obj, now_ns):
            # Alpaca implements modify as cancel-and-replace, and the replacement carries the
            # chain's cumulative filled_qty, so `qty` is the order's total size and must exceed what
            # has already filled -- send the full target, and skip once it's been reached (Alpaca
            # would reject it with "qty must be > filled_qty").
            if int(open_order.filled_qty) >= int(qty_obj):
                self.log.debug(
                    f"Skipping modify for {open_order.client_order_id}: already filled "
                    f"{open_order.filled_qty} >= target {qty_obj}."
                )
                return False
            self.log.debug(f"Modifying order {open_order.client_order_id} with values {qty_obj} @ {price_obj}.")
            self.modify_order(open_order.order, quantity=qty_obj, price=price_obj)
            return True
        return False

    def cancel_open_order(self, open_order, client_id=None, params=None):
        if open_order in self.open_orders:
            # TODO: Perhaps use order status instead of the existence of venue_order_id?
            if open_order.venue_order_id is not None:
                self._remove_open_order(open_order)
                self.cancel_order(order=open_order.order, client_id=client_id, params=params)

    def exit_position_at_price(self, new_limit_price):
        remaining_qty_to_exit = self.exposure
        for order in self.open_exits:
            order_qty = order.quantity
            remaining_qty_to_exit -= order_qty
            if order.price != new_limit_price:
                modified = self.modify_open_order(order, quantity=order_qty, price=new_limit_price)
                if not modified:
                    self.log.debug(
                        f"While stopping out, did not modify order {order.client_order_id} to {new_limit_price}."
                    )
        if remaining_qty_to_exit > 0:
            self.exit(quantity=remaining_qty_to_exit, limit_price=new_limit_price, tag="s")

    def _stop_out_if_needed(self, tick: TradeTick):
        if self.position_qty == 0:
            self.stop_price = None
            self._stopping_out = False
            return

        if self.clock.timestamp_ns() - self._last_stop_out_attempt < self._ATTEMPT_STOP_OUT_EVERY_MS * 1e6:
            return

        sign = self._side_sign
        if self.exposure > 0 and self.stop_price is None:
            # Below the market when long, above it when short
            self.stop_price = tick.price - sign * self.stop_loss
            self.log.info(f"Setting stop price to {self.stop_price}")

        if self.stop_price is not None:
            if (float(tick.price) - float(self.stop_price)) * sign <= 0:
                self._stopping_out = True
                for order in self.entries_to_cancel:
                    self.cancel_open_order(order)

                self._last_stop_out_attempt = self.clock.timestamp_ns()
                if self.is_long:
                    new_price = max(float(tick.price) * 0.90, float(tick.price) - 0.20)
                else:
                    new_price = min(float(tick.price) * 1.10, float(tick.price) + 0.20)
                new_limit_price = self.instrument.make_price(new_price)
                self.log.info(
                    f"Stop price {self.stop_price} reached, exiting at {new_limit_price}", color=LogColor.YELLOW
                )
                self.exit_position_at_price(new_limit_price)
            else:
                self._stopping_out = False

    @property
    def max_position_allowed(self):
        return self.config.max_position_multiplier * self.config.trade_size

    def _max_entry_qty_allowed(self):
        """How much more we may open before hitting max_position_allowed."""
        return self.max_position_allowed - self.open_entries_qty - self.exposure

    def _max_exit_qty_allowed(self):
        """How much we may close without flipping through flat onto the other side."""
        return self.exposure - self.open_exits_qty

    def _raise_if_needed(self):
        """
        Need separate method because _reconcile raises were not causing system to raise
        """
        if self._raise_msg is not None:
            self.log.error(f"Raising RuntimeError: {self._raise_msg}")
            raise RuntimeError(self._raise_msg)

    def _save_tick_data(self, tick: TradeTick) -> None:
        if not self.save_artifacts:
            return
        if self._tick_event_dt_adjusted >= tick.ts_event:
            self._tick_event_dt_adjusted += 1
        else:
            self._tick_event_dt_adjusted = tick.ts_event
        tick_data = {
            "price": float(tick.price),
            "size": int(tick.size),
            "ts_event": tick.ts_event,
            # These can be used to measure data latency
            "ts_init": tick.ts_init,
            "ts_clock": self.clock.utc_now(),
            # "ts_now": pd.Timestamp.utcnow(),
        }
        for metric in self.metrics_to_save_on_tick:
            tick_data = {**tick_data, **metric.get_vals()}
        self._add_tick_data(self._tick_event_dt_adjusted, tick_data)

    # DATA DELAY BUFFER ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def set_data_delay(self, default_secs: float = 0.0, windows=None) -> None:
        """
        Hold trade ticks back before acting on them, to mirror live feed latency in a backtest.

        A backtest hands the strategy every print the instant it happens; live, the same print
        arrives late (measured at ~25ms typically, and over a second through the opening burst).
        This buffers each tick and releases it once `clock - tick.ts_event >= delay`. The simulated
        venue is unaffected: the engine feeds the exchange before it feeds subscribers, so the
        exchange stays current while the strategy reacts late, which is the live skew.

        Leave this off for live runs, where the latency is already real, and call it before the
        run starts -- the release timer is registered in `on_start`.

        Parameters
        ----------
        default_secs : float
            Delay for ticks outside every window. 0 with no windows disables buffering entirely.
        windows : iterable of (start, stop, delay_secs), optional
            Per-time-of-day overrides covering [start, stop), e.g.
            [("09:30:00", "09:30:10", 1.5), ("09:30:10", "09:31:00", 0.5)]. Times are US/Eastern
            wall clock as "HH:MM"/"HH:MM:SS" (or `time` objects) and may not overlap. Ticks outside
            all windows use `default_secs`.

        """
        if default_secs < 0:
            raise ValueError(f"default_secs must be >= 0, got {default_secs!r}")

        parsed: list[tuple[time, time, int]] = []
        for window in windows or []:
            start, stop, delay_secs = window
            start, stop = parse_et_time(start), parse_et_time(stop)
            if start >= stop:
                raise ValueError(f"Window start must be before stop, got {start} -> {stop}")
            if delay_secs < 0:
                raise ValueError(f"Window delay must be >= 0, got {delay_secs!r}")
            parsed.append((start, stop, int(delay_secs * NS_PER_SEC)))

        parsed.sort(key=lambda w: w[0])
        for (_, prev_stop, _), (next_start, _, _) in pairwise(parsed):
            if next_start < prev_stop:
                raise ValueError(f"Windows overlap: {next_start} starts before {prev_stop}")

        self._tick_delay_default_ns = int(default_secs * NS_PER_SEC)
        self._tick_delay_windows = parsed
        self._tick_delay_enabled = self._tick_delay_default_ns > 0 or bool(parsed)
        self._window_bounds = []
        self._window_bounds_day = None

        if self._tick_delay_enabled:
            windows_str = ", ".join(f"{s}-{e} {ns / NS_PER_SEC}s" for s, e, ns in parsed) or "none"
            self.log.info(
                f"Data delay enabled: default {self._tick_delay_default_ns / NS_PER_SEC}s, windows: {windows_str}",
                color=LogColor.YELLOW,
            )

    def _tick_delay_ns(self, ts_event: int) -> int:
        """Return the delay that applies to a tick, by the wall-clock time of day it printed at."""
        if not self._tick_delay_windows:
            return self._tick_delay_default_ns

        if self._window_bounds_day is None or not (
            self._window_bounds_day[0] <= ts_event < self._window_bounds_day[1]
        ):
            self._build_window_bounds(ts_event)

        for start_ns, stop_ns, delay_ns in self._window_bounds:
            if start_ns <= ts_event < stop_ns:
                return delay_ns

        return self._tick_delay_default_ns

    def _build_window_bounds(self, ts_event: int) -> None:
        """
        Resolve the window wall-clock times against the ET day holding `ts_event`.

        Done once per day rather than per tick: the result is a list of raw nanosecond ranges, so
        the lookup above stays integer comparisons.
        """
        day = pd.Timestamp(ts_event, unit="ns", tz="UTC").tz_convert(ET_TZ).normalize()
        # 26h clears the next midnight whether the day is 23, 24 or 25 hours long (DST).
        next_day = (day + pd.Timedelta(hours=26)).normalize()
        self._window_bounds_day = (day.value, next_day.value)
        self._window_bounds = [
            (
                (day + pd.Timedelta(hours=start.hour, minutes=start.minute, seconds=start.second)).value,
                (day + pd.Timedelta(hours=stop.hour, minutes=stop.minute, seconds=stop.second)).value,
                delay_ns,
            )
            for start, stop, delay_ns in self._tick_delay_windows
        ]

    def _release_delayed_ticks(self, event: TimeEvent = None) -> None:
        """Process every buffered tick whose delay has now elapsed, oldest first."""
        ts_now = self.clock.timestamp_ns()
        while self._delayed_ticks:
            tick = self._delayed_ticks[0]
            if ts_now - tick.ts_event < self._tick_delay_ns(tick.ts_event):
                return  # Ticks are buffered in order, so nothing behind this one is due either
            self._delayed_ticks.popleft()
            self._process_trade_tick(tick)

    def _indicators_ready(self) -> bool:
        """
        Return whether the tick indicators hold enough data to act on.

        `indicators_initialized` only covers indicators registered with the engine, and it returns
        True when none are -- which is the case while the delay buffer feeds them instead.
        """
        if self._feed_indicators_manually:
            return all(indicator.initialized for indicator in self._tick_indicators)
        return self.indicators_initialized()

    # TICK HANDLING ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    def on_trade_tick(self, tick: TradeTick) -> None:
        if not self._tick_delay_enabled:
            self._process_trade_tick(tick)
            return

        self._delayed_ticks.append(tick)
        self._release_delayed_ticks()

    def _process_trade_tick(self, tick: TradeTick) -> None:
        if self._feed_indicators_manually:
            # The engine updates registered indicators the moment a tick is delivered, which would
            # leave them ahead of the delayed decision path. Feed them here instead, so they see
            # exactly what the strategy has seen -- as they do live.
            for indicator in self._tick_indicators:
                indicator.handle_trade_tick(tick)

        self._raise_if_needed()
        self._last_tick = tick

        if self.stop_loss is None:
            self.stop_loss = self.config.stop_loss

        self._stop_out_if_needed(tick)

        #  Actual operations of this method
        if self._historical_loaded and self._indicators_ready():
            self._on_trade_tick(tick)

        self._save_tick_data(tick)

    def _on_bar(self, bar: Bar) -> None:
        pass

    def on_bar(self, bar: Bar):
        self._on_bar(bar)
        if self.save_artifacts:
            tick_data = {}
            for metric in self.metrics_to_save_on_1min:
                tick_data = {**tick_data, **metric.get_vals()}
            self._add_tick_data(bar.ts_init, tick_data)

    def on_historical_data(self, data) -> None:
        if isinstance(data, TradeTick):
            if self._feed_indicators_manually:
                for indicator in self._tick_indicators:
                    indicator.handle_trade_tick(data)
            self._historical_ticks.append(data)
            self._save_tick_data(data)

    def _on_historical_ticks_loaded(self, request_id) -> None:
        self._historical_loaded = True

    def _submit_orders_if_allowed(self, order_or_order_list, expire_time=None) -> None:
        entry_included = False
        entry_side = self._entry_order_side
        if self.config.allow_trades:
            # For OrderList type, submit all at once, but parse through each to check for an entry order (from
            # bracket) and to add to uncached
            if isinstance(order_or_order_list, OrderList):
                raise ValueError("Need to figure out how to update _buy_orders and _sell_orders")
                self.submit_order_list(order_or_order_list)
                for order in order_or_order_list.orders:
                    if order.side == entry_side:
                        entry_included = True

            # If single item, submit and add to uncached
            elif isinstance(order_or_order_list, Order):
                self.submit_order(order_or_order_list)
                open_order = OpenOrder(order_or_order_list, expire_time=expire_time)
                # Bucketed by the real venue side, not entry/exit
                if order_or_order_list.side == OrderSide.BUY:
                    self._buy_orders.add(open_order)
                if order_or_order_list.side == OrderSide.SELL:
                    self._sell_orders.add(open_order)
                if order_or_order_list.side == entry_side:
                    entry_included = True
            else:
                raise ValueError(f"Unexpected order type: {type(order_or_order_list)}")
        if entry_included:
            self.entry_orders_count += 1
            self.last_entry_dt = self.clock.utc_now()

    def _submit_limit_order(
        self, side: OrderSide, quantity: int, limit_price: float, tag: str = None, cancel_after_secs: int = None
    ):
        tags = [tag] if tag is not None else None
        order: LimitOrder = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(quantity),
            price=make_Price(limit_price),
            time_in_force=TimeInForce.DAY,
            expire_time=None,
            tags=tags,
        )
        utc_now = self.clock.utc_now()
        expire_time = utc_now + timedelta(seconds=cancel_after_secs) if cancel_after_secs is not None else None
        self._submit_orders_if_allowed(order, expire_time=expire_time)

    def _submit_sided_limit_order(
        self, side: OrderSide, quantity, limit_price, tag=None, cancel_after_secs=None
    ) -> None:
        """
        Shared body of _buy/_sell.

        The trading_enabled, _stopping_out and _flipping guards apply only when this order would
        OPEN the position. Exits must always be allowed through, which is the whole point of
        stopping out -- and of flipping, which gets flat by letting the existing exits fill.
        """
        is_entry = side == self._entry_order_side
        if is_entry:
            if not self.trading_enabled:
                self.log.info(f"self.trading_enabled is False, skipping {self._side} entry order")
                return
            if self._stopping_out:
                self.log.info("Ignoring entry request because self._stopping_out is True")
                return
            if self._flipping:
                self.log.info("Ignoring entry request because self._flipping is True")
                return
            max_qty = self._max_entry_qty_allowed()
            reason = f"to avoid exceeding max position of {self.max_position_allowed}"
        else:
            max_qty = self._max_exit_qty_allowed()
            reason = f"to avoid flipping past flat (exposure {self.exposure})"

        allowed_qty = min(quantity, max_qty)
        if allowed_qty != quantity:
            self.log.debug(f"Quantity reduced from {quantity} to {allowed_qty} {reason}.")
        if allowed_qty > 0:
            self._submit_limit_order(side, allowed_qty, limit_price, tag, cancel_after_secs)

    def _buy(self, quantity, limit_price, tag=None, cancel_after_secs=None) -> None:
        """Submit a literal BUY. The entry when long, the cover when short."""
        self._submit_sided_limit_order(OrderSide.BUY, quantity, limit_price, tag, cancel_after_secs)

    def _sell(self, quantity, limit_price, tag=None, cancel_after_secs=None) -> None:
        """Submit a literal SELL. The exit when long, the entry when short."""
        self._submit_sided_limit_order(OrderSide.SELL, quantity, limit_price, tag, cancel_after_secs)

    def enter(self, quantity, limit_price, tag=None, cancel_after_secs=None) -> None:
        """Open or increase the position, in the direction of self.side."""
        if self.is_long:
            self._buy(quantity, limit_price, tag, cancel_after_secs)
        else:
            self._sell(quantity, limit_price, tag, cancel_after_secs)

    def exit(self, quantity, limit_price, tag=None, cancel_after_secs=None) -> None:
        """Close or reduce the position, in the direction of self.side."""
        if self.is_long:
            self._sell(quantity, limit_price, tag, cancel_after_secs)
        else:
            self._buy(quantity, limit_price, tag, cancel_after_secs)

    @abstractmethod
    def _on_order_filled(self, order) -> None:
        pass

    def on_order_filled(self, order) -> None:
        self._on_order_filled(order)
        sign = self._side_sign
        if order.order_side == self._entry_order_side:
            self._total_entry_qty += int(order.last_qty)
            self.stop_loss = self.config.stop_loss
            new_stop_price = float(order.last_px) - sign * self.stop_loss
            if self.stop_price is not None:
                # Only ever tighten: upward when long, downward when short
                if (new_stop_price - float(self.stop_price)) * sign > 0:
                    self.log.info(f"Changing stop price from {self.stop_price} to {new_stop_price}")
                    self.stop_price = new_stop_price
            else:
                self.log.info(f"Setting stop price to {new_stop_price}")
                self.stop_price = new_stop_price
        else:
            if self.save_artifacts:
                realized_pnl = self.portfolio.realized_pnl(self.config.instrument_id)
                if realized_pnl is not None:
                    self._add_tick_data(order.ts_event, {"pnl": float(realized_pnl)})

    @abstractmethod
    def _on_trade_tick(self, tick: TradeTick) -> None:
        pass

    def on_order_event(self, order_event) -> None:
        if isinstance(order_event, OrderRejected):
            cache_order = self.cache.order(order_event.client_order_id)
            # self._remove_open_order(order)
            if cache_order.side == self._entry_order_side:
                if "insufficient qty available" in cache_order.last_event.reason:
                    wrong_way = "shorting" if self.is_long else "going long"
                    self.log.warning(
                        f"Entry order rejected for insufficient quantity. Accidental {wrong_way} is likely. "
                        "Running reconciliation."
                    )
                    self._reconcile()
        elif isinstance(order_event, OrderModifyRejected):
            self.clear_open_order_modify_params(order_event)
            if order_event.reason == MODIFY_HELD_PENDING_NEW_REASON:
                # The exec client never sent this modify; it held it until Alpaca confirmed the order,
                # so nothing is out of sync and the next tick should re-modify at the current price.
                self.log.debug(f"Modify for {order_event.client_order_id} was held while pending_new; retrying")
                return

            # Only apply cooldown for errors where retrying quickly won't help
            _COOLDOWN_REASONS = (
                "order is not open",
                "qty must be",  # qty must be > filled_qty
                "cannot replace order in pending_new status",
                "cannot replace order in pending_cancel status",
                "order is already in",  # filled/replaced/rejected state
                "already closed",
                "order already pending replacement",
                "order chain not fully replaced",
                "too_late_to_cancel",
                "insufficient qty available for order",
            )
            if self.is_long:
                # This error sometimes shows up transiently when trying to sell a long position
                _COOLDOWN_REASONS += ("cannot be sold short",)

            _SKIP_COOLDOWN_REASONS = (
                "potential wash trade detected",  # This should only arise in testing when we have high buy orders
                "order parameters are not changed",
                "no venue_order_id",  # Occurs if we try to modify immediately after sending and before we get response.
            )
            reason = (order_event.reason or "").lower()
            if any(r in reason for r in _COOLDOWN_REASONS):
                self.log.info(f"Applying {self._MODIFY_REJECT_COOLDOWN_SECS}s cooldown for: {reason}")
                for open_order in self.open_orders:
                    if open_order.client_order_id == order_event.client_order_id:
                        open_order._last_modify_ns = self.clock.timestamp_ns() + int(
                            self._MODIFY_REJECT_COOLDOWN_SECS * 1e9
                        )
                        break
            elif any(r in reason for r in _SKIP_COOLDOWN_REASONS):
                self.log.info(f"Skipping cooldown for: {reason}")
            else:
                self.log.error(f"Unknown OrderModifyRejected reason: {reason}\n Order_event: {order_event}")
            self._trigger_nt_reconciliation()
        elif isinstance(order_event, OrderCancelRejected):
            reason = order_event.reason or ""
            if "already in" in reason and "filled" in reason:
                self.log.warning(
                    f"Cancel rejected for {order_event.client_order_id}: already filled at venue. "
                    "Triggering targeted reconciliation to sync cache."
                )
                cache_order = self.cache.order(order_event.client_order_id)
                if cache_order and self._exec_engine is not None:
                    self._exec_engine._loop.create_task(self._exec_engine._query_and_reconcile_order(cache_order))
                return
            cache_order = self.cache.order(order_event.client_order_id)
            if cache_order and cache_order.is_open:
                # Re-add to open_orders if missing (e.g. cancel was rejected after cancel_open_order optimistically
                # removed it)
                already_tracked = any(oo.client_order_id == cache_order.client_order_id for oo in self.open_orders)
                if not already_tracked:
                    open_order = OpenOrder(cache_order)
                    if cache_order.side == OrderSide.BUY:
                        self._buy_orders.add(open_order)
                    elif cache_order.side == OrderSide.SELL:
                        self._sell_orders.add(open_order)
                    self.log.error(
                        f"Re-added {cache_order.client_order_id} to open_orders (side={cache_order.side}, "
                        f"status={cache_order.status}). Tracking is by venue side, so if it predates a side "
                        f"switch it counts as neither entry nor exit and _reconcile will cancel it."
                    )

    def close_position_limit_order(self):
        last_trade = self.cache.trade_tick(self.config.instrument_id)
        limit_price = self.position_avg_px if last_trade is None else last_trade.price
        # Aggressive in the direction that closes: below the market when long, above when short
        aggressive_price = limit_price * 0.9 if self.is_long else limit_price * 1.1
        self.exit_position_at_price(make_Price(aggressive_price))

    def _trigger_nt_reconciliation(self):
        """Trigger an async force-reconciliation via the execution engine to re-sync cache with broker."""
        # Skip if a reconciliation is already in-flight
        if self._reconciliation_task is not None and not self._reconciliation_task.done():
            return
        now_ns = self.clock.timestamp_ns()
        secs_since_last = (now_ns - self._last_force_reconcile_ns) / 1e9
        if secs_since_last < self._RECONCILE_COOLDOWN_SECS:
            return
        self._force_reconcile_count += 1
        self._last_force_reconcile_ns = now_ns
        self._log.warning(f"Triggering nt reconciliation (attempt {self._force_reconcile_count}) via execution engine")
        self._reconciliation_task = self._exec_engine._loop.create_task(self._exec_engine.reconcile_execution_state())

    def _reconcile(self, event: TimeEvent = None):
        # TODO: Remove overlap with _trigger_force_reconciliation
        if self._trader_helper is not None:
            # Running live. Get position from broker
            position_at_broker = self._trader_helper.get_position_obj(self.config.instrument_id.symbol.value)
            position_at_broker = int(position_at_broker.qty)
        else:
            # Running a backtest, so just use the local position
            position_at_broker = self.position_qty

        # FLATTEN POSITION IF NEEDED (position is opposite the side we are trading)
        # Deliberately literal, not entry/exit: this branch runs precisely when the position
        # contradicts self.side, so entry/exit is inverted here. Direction comes from the sign of
        # the actual position instead.
        if position_at_broker * self._side_sign < 0:
            now_ns = self.clock.timestamp_ns()
            if (now_ns - self._last_wrong_way_flatten_ns) / 1e9 < 1.0:
                return  # Cooldown: only attempt flatten once per second
            self._last_wrong_way_flatten_ns = now_ns
            self._prune_closed()
            if position_at_broker < 0:
                flatten_side = OrderSide.BUY
                orders_to_cancel = self._sell_orders  # Would deepen the short
                orders_to_modify = self._buy_orders
                price = self._last_tick.price * 1.1
            else:
                flatten_side = OrderSide.SELL
                orders_to_cancel = self._buy_orders  # Would deepen the long
                orders_to_modify = self._sell_orders
                price = self._last_tick.price * 0.9
            self.log.error(
                f"Position {position_at_broker} is opposite of side '{self._side}'! Canceling the open orders "
                f"that would deepen it and submitting a {flatten_side} to flatten."
            )
            for open_order in orders_to_cancel:
                self.cancel_open_order(open_order)
            if len(orders_to_modify) > 0:
                # Prefer the flatten order already working over some unrelated order on that
                # side, so repeated passes reprice the one order instead of conscripting a
                # different one each time.
                open_order_to_modify = next(
                    (open_order for open_order in orders_to_modify if open_order.is_flatten),
                    next(iter(orders_to_modify)),
                )
                self.modify_open_order(open_order_to_modify, quantity=abs(position_at_broker), price=price)
            else:
                self._submit_limit_order(flatten_side, abs(position_at_broker), price, FLATTEN_TAG)
            return  # Return from here to allow orders time to cancel and flatten

        self._cancel_orders_from_a_previous_side()

        # The position is no longer wrong-way, so any flatten order still working has outlived
        # its job. Nothing else cancels it -- the entry sweeps skip it by design -- and left
        # resting it would open the very position it was sent to close.
        self._cancel_stale_flatten_orders()

        # CHECK POSITION DISCREPANCY BETWEEN LOCAL AND BROKER
        if self.position_qty != position_at_broker:
            now_ns = self.clock.timestamp_ns()
            if self._position_discrepancy_start_ns is None:
                self._position_discrepancy_start_ns = now_ns
                self._log.error(
                    f"Position discrepancy detected. Cache Position: {self.position_qty}, Alpaca Position: {position_at_broker}, Diff: {self.position_qty - position_at_broker}"
                )
            elif (now_ns - self._position_discrepancy_start_ns) / 1e9 > self._POSITION_DISCREPANCY_ALLOW_SECS:
                # If reconciliation is in-flight, give it more time instead of raising
                if self._reconciliation_task is not None and not self._reconciliation_task.done():
                    self._log.warning(
                        f"Position discrepancy exceeded {self._POSITION_DISCREPANCY_ALLOW_SECS}s "
                        f"but reconciliation is in-flight. Resetting timer."
                    )
                    self._position_discrepancy_start_ns = now_ns
                    return
                self._raise_msg = (
                    f"Position discrepancy has existed for more than {self._POSITION_DISCREPANCY_ALLOW_SECS}. Raising."
                )
                for open_order in self.open_orders:
                    self.log.warning(f"Strategy OpenOrder {open_order.order}")
                for order in set(self.cache.orders_open() + self.cache.orders_inflight()):
                    self.log.warning(f"Cache order {order}")
                return
            # Trigger reconciliation on every check (cooldown enforced inside)
            self._trigger_nt_reconciliation()
        else:
            self._position_discrepancy_start_ns = None
            self._force_reconcile_count = 0

        # COMPARE OPEN ORDERS TO CACHED ORDERS
        self_open_orders = set(open_order.order for open_order in self.open_orders)
        cache_open_orders = set(self.cache.orders_open() + self.cache.orders_inflight())
        cache_open_orders = set(order for order in cache_open_orders if order.status != OrderStatus.PENDING_CANCEL)
        if (len(self_open_orders) + len(cache_open_orders)) > 0:
            for order in cache_open_orders - self_open_orders:
                self.log.warning(
                    f"Cache open order, venue_id {order.venue_order_id} not found in self.open_orders.\nOrder: {order}"
                )
                for order in cache_open_orders:
                    self.log.debug(f"Cache order {order}")
                for open_order in self_open_orders:
                    self.log.debug(f"OpenOrder {open_order}")

            for order in self_open_orders - cache_open_orders:
                self.log.warning(f"OpenOrder {order} not found in cache.")
                try:
                    # Try to retrieve order and see if status is closed. If so, remove from self.open_orders
                    cache_order = self.cache.order(order.client_order_id)
                    if cache_order.status in CLOSED_STATUS_LIST:
                        self.log.info(f"Removing open order {order} from self.open_orders because it is closed.")
                        self._remove_open_order(order)
                except Exception as e:
                    # FIXME: Test this
                    self.log.error(f"Failed to remove open order {order} from self.open_orders. Exception: {e}")
                    # check if self_order was just recently opened

    def _cancel_orders_from_a_previous_side(self) -> None:
        for open_order in self.orders_from_a_previous_side:
            self.log.error(
                f"Canceling {open_order.client_order_id}: submitted under a previous side, so it can only "
                f"trade against the current '{self._side}' one."
            )
            self.cancel_open_order(open_order)

    def _cancel_stale_flatten_orders(self) -> None:
        for open_order in self.open_orders:
            if open_order.is_flatten:
                self.log.info(
                    f"Canceling flatten order {open_order.client_order_id}: the wrong-way position it was "
                    "sent for is gone."
                )
                self.cancel_open_order(open_order)

    def _cancel_partial_fills_and_orders_past_timeout(self, event: TimeEvent):
        for open_order in self.open_orders:
            if open_order.expire_time is not None and self.clock.utc_now() > open_order.expire_time:
                self.cancel_open_order(open_order)

            # elif open_order.first_partial_fill_ns is not None:
            #     ns_since_earliest_fill = self.clock.timestamp_ns() - open_order.first_partial_fill_ns
            #     if (ns_since_earliest_fill / 1e9) > CANCEL_PARTIAL_FILLS_AFTER_SECS:
            #         self.log.info(
            #             f"Canceling order {open_order.client_order_id}, because first fill occurred more than {CANCEL_PARTIAL_FILLS_AFTER_SECS} secs ago"
            #         )
            #         self.cancel_open_order(open_order)

    def _print_update(self):
        return ""

    def print_update(self, event: TimeEvent):
        tick_str = ""
        if self._last_tick is not None:
            timestamp = pd.Timestamp(self._last_tick.ts_event, unit="ns")
            tick_str = f"Last Tick {timestamp} size {self._last_tick.size} @ {self._last_tick.price}"

        # Get P&L information
        realized_pnl = self.portfolio.realized_pnl(self.config.instrument_id)

        open_entries_str = "\n".join(str(o) for o in self.open_entries) if len(self.open_entries) > 0 else ""
        open_exits_str = "\n".join(str(o) for o in self.open_exits) if len(self.open_exits) > 0 else ""
        OpenEntryQty = self.open_entries_qty
        OpenExitQty = self.open_exits_qty

        # Neither an entry nor an exit, so they appear in no other line here. They should not
        # exist at all, which is why they are worth calling out when they do.
        stale_orders = self.orders_from_a_previous_side
        stale_str = ""
        if stale_orders:
            stale_str = f"FROM A PREVIOUS SIDE ({len(stale_orders)}): " + "\n".join(str(o) for o in stale_orders) + "\n"

        position_str = ""
        if self.exposure != 0:
            avg_px = self.position_avg_px
            gain = (self._last_tick.price - avg_px) * self._side_sign
            unrealized = self.exposure * gain
            diff = self.exposure - OpenExitQty
            if diff > 0:
                self.log.warning(f"Diff: {diff}")
            position_str = f"Position {self.position_qty} @ {round(avg_px, 2)} | PerShare {round(gain, 2)} | PnL ${round(unrealized, 2)} | Diff={diff}\n"
        metrics_data = {}
        for metric in self.metrics_to_save_on_tick + self.metrics_to_save_on_1min:
            vals = {k: str(round(v, 3)) if v is not None else "None" for k, v in metric.get_vals().items()}
            metrics_data = {**metrics_data, **vals}
        self.log.info(
            f"UPDATE: {self.config.instrument_id} | Side: {self._side}\n{tick_str}\n"
            f"Total Entered {self._total_entry_qty} | Realized: {realized_pnl}\n"
            f"{OpenEntryQty=} Orders: {open_entries_str}\n"
            f"{OpenExitQty=} Orders: {open_exits_str}\n"
            f"{stale_str}"
            f"{position_str}"
            f"{self._print_update()}"
            f"{metrics_data}",
            color=LogColor.CYAN,
        )

    def _set_trading_enabled_based_on_allow_trading_times(self, event: TimeEvent):
        current_5min = pd.Timestamp(self.clock.utc_now()).floor("5min")
        self.set_trading_enabled(current_5min in self.allow_trading_times)

    def on_start(self) -> None:
        if not self._initialized:
            raise RuntimeError("Strategy must be initialized before starting. Call method `initialize` first.")

        # TIME INTERVAL FUNCTIONS
        self.clock.set_timer(
            name="cancel_orders_timer",
            interval=timedelta(seconds=1),
            callback=self._cancel_partial_fills_and_orders_past_timeout,
        )
        self.clock.set_timer(
            name="reconcile_internal_fn",
            interval=timedelta(seconds=3),
            callback=self._reconcile,
        )

        # Only create print_update timer if requested in config
        if self.config.print_update_every_secs is not None:
            self.clock.set_timer(
                name="print_update",
                interval=timedelta(seconds=self.config.print_update_every_secs),
                callback=self.print_update,
            )

        # Only create buy_ranges check if needed
        if self.allow_trading_times is not None:
            self.clock.set_timer(
                name="set_trading_enabled_based_on_allow_trading_times",
                interval=timedelta(seconds=15),
                callback=self._set_trading_enabled_based_on_allow_trading_times,
            )

        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        # TICK DATA
        # Register indicators and request historical trade ticks if needed
        max_tick_lookback = self.MIN_TICK_LOOKBACK
        for metric in self.metrics_to_save_on_tick:
            # A metric can also read plain attributes off a non-indicator object (e.g. the
            # strategy itself); only real indicators get fed ticks by the engine.
            if isinstance(metric.obj, Indicator):
                if self._tick_delay_enabled:
                    self._tick_indicators.append(metric.obj)
                else:
                    self.register_indicator_for_trade_ticks(self.config.instrument_id, metric.obj)
            max_tick_lookback = max(max_tick_lookback, metric.tick_lookback)

        self._feed_indicators_manually = bool(self._tick_indicators)

        if self._tick_delay_enabled:
            # Ticks are also released as new ones arrive; this covers quiet stretches, where the
            # next tick may be further away than the delay itself.
            self.clock.set_timer(
                name="release_delayed_ticks",
                interval=timedelta(milliseconds=self._DELAY_RELEASE_INTERVAL_MS),
                callback=self._release_delayed_ticks,
            )

        if max_tick_lookback > 0:
            # Set a long lookback to ensure we get at least `max_tick_lookback` ticks back
            trade_tick_start = self.clock.utc_now() - timedelta(days=2)
            self.request_trade_ticks(
                self.config.instrument_id,
                start=trade_tick_start,
                limit=max_tick_lookback,
                callback=self._on_historical_ticks_loaded,
            )

        self.subscribe_trade_ticks(self.config.instrument_id)

        # TODO: Test if "internal" vs "external" bars does anything for us
        #    Can't request "historical" internal bars. Not implemented in NT
        bar_type = BarType.from_str(f"{self.config.instrument_id}-1-MINUTE-LAST-EXTERNAL")
        if self.internal_bars:
            bar_type = BarType.from_str(f"{self.config.instrument_id}-1-MINUTE-LAST-INTERNAL")
        for metric in self.metrics_to_save_on_1min:
            self.register_indicator_for_bars(bar_type=bar_type, indicator=metric.obj)

        # Subscribe to 1-minute bars
        if len(self.metrics_to_save_on_1min) > 0:
            self.subscribe_bars(bar_type)
            self.request_bars(bar_type, start=self._clock.utc_now() - pd.Timedelta(minutes=30))

        # self.subscribe_quote_ticks(self.config.instrument_id)
        # self.subscribe_order_book_depth(self.config.instrument_id, book_type=BookType.L1_MBP)
        # self.subscribe_order_book_deltas(self.config.instrument_id, depth=20)  # For debugging
        # self.subscribe_order_book_at_interval(self.config.instrument_id, depth=20)  # For debugging

        # Get historical data
        # self.request_quote_ticks(self.config.instrument_id)

    def on_stop(self) -> None:
        if self._delayed_ticks:
            # These would have been acted on after the run ended, so they are simply dropped.
            self.log.debug(f"Discarding {len(self._delayed_ticks)} tick(s) held in the delay buffer")
            self._delayed_ticks.clear()

        if self.position_qty != 0:
            # Cancel entry orders, but use "close_position_limit_order" to modify the exit orders
            self.cancel_all_orders(self.config.instrument_id, order_side=self._entry_order_side)
            self.close_position_limit_order()
        else:
            self.cancel_all_orders(self.config.instrument_id)

        # Unsubscribe from data
        self.unsubscribe_trade_ticks(self.config.instrument_id)

    def on_dispose(self) -> None:
        if self.save_artifacts:
            self._artifacts_io.save_ticks_and_metrics(self._tick_data_dicts)

    def on_instrument(self, instrument: Instrument) -> None:
        pass

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        pass

    def on_order_book(self, order_book: OrderBook) -> None:
        pass

    def on_quote_tick(self, tick: QuoteTick) -> None:
        pass

    def on_data(self, data: Data) -> None:
        pass

    def on_event(self, event: Event) -> None:
        pass

    def on_reset(self) -> None:
        raise NotImplementedError

    def on_save(self) -> dict[str, bytes]:
        return {}

    def on_load(self, state: dict[str, bytes]) -> None:
        pass
