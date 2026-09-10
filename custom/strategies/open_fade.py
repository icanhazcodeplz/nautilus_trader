"""
Opening-print mean-reversion strategy.

Rests limit orders just before the open, reprices them to the actual opening print, and takes
profit back at that print with a wide stop.

Timeline (all times US/Eastern):

    09:00:00  COLLECT   start accumulating trade prices into a deque(maxlen=avg_ticks)
    09:29:57  ARM       submit SELL entries at avg_price + entry_offsets
    09:30:00  OPEN      open_price = last trade seen; reprice entries to open_price + offsets
    ..ticks..           flip SELL <-> BUY when price crosses open_price -/+ flip_threshold,
                        but ONLY while nothing has filled
    ..fill..            flipping is permanently disabled; submit / resize the OCO exit
    09:31:00  DEADLINE  cancel all unfilled entry orders
    15:55:00  FLATTEN   cancel the OCO and close any position
    on_stop             belt-and-braces cancel + close

Notes
-----
This subclasses ``Strategy`` directly rather than ``BaseStrategy`` because ``BaseStrategy.sell()``
clamps quantity to ``position_qty - open_sell_qty`` (it can never open a short) and
``_submit_orders_if_allowed`` raises on an ``OrderList``.

"""

from collections import deque
from pathlib import Path

import pandas as pd

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import ContingencyType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.model.orders import Order
from nautilus_trader.model.orders import OrderList
from nautilus_trader.model.orders import StopMarketOrder
from nautilus_trader.trading.strategy import Strategy


ET = "US/Eastern"

# Order tags
ENTRY_TAG = "ENTRY"
TAKE_PROFIT_TAG = "TAKE_PROFIT"
STOP_LOSS_TAG = "STOP_LOSS"
FLATTEN_TAG = "FLATTEN"

# Clock alert names
_ALERT_COLLECT = "OF-COLLECT"
_ALERT_ARM = "OF-ARM"
_ALERT_OPEN = "OF-OPEN"
_ALERT_DEADLINE = "OF-DEADLINE"
_ALERT_FLATTEN = "OF-FLATTEN"


class OpenFadeConfig(StrategyConfig, frozen=True, kw_only=True):
    """Configuration for ``OpenFade``."""

    instrument_id: InstrumentId
    trade_size: int = 10
    entry_offsets: tuple[float, ...] = (0.50, 1.00)
    take_offset: float = 0.0
    stop_offset: float = 3.00
    flip_threshold: float = 0.30
    avg_ticks: int = 10
    collect_from_et: str = "09:25:00"
    arm_at_et: str = "09:29:58"
    open_at_et: str = "09:30:00"
    entry_window_secs: int = 60
    flatten_at_et: str = "09:36:00"
    # How far through the last trade to price the flattening limit order. Priced against the
    # position (below the last trade when selling, above it when buying) so it is marketable.
    flatten_offset: float = 0.20
    max_flips: int = 10
    # Leave OUO handling to the venue alone. With the strategy-side OrderManager also active, a
    # partial fill on one leg makes it resize the sibling, and the venue then propagates that back
    # through the OUO link and cancels the partially-filled leg (`backtest/engine.pyx:8296`
    # compares the child's cumulative fills against the parent's remaining quantity), leaving the
    # rest of the position with no exit orders at all.
    manage_contingent_orders: bool = False


def et_time_today(now_utc: pd.Timestamp, hhmmss: str) -> pd.Timestamp:
    """Return today's US/Eastern wall-clock ``hhmmss`` for ``now_utc``, as a UTC timestamp."""
    now_et = pd.Timestamp(now_utc).tz_convert(ET)
    hour, minute, second = (int(part) for part in hhmmss.split(":"))
    target_et = now_et.replace(hour=hour, minute=minute, second=second, microsecond=0)
    return target_et.tz_convert("UTC")


class OpenFade(Strategy):
    """Fades the opening print back toward it, from whichever side price extends to."""

    def __init__(self, config: OpenFadeConfig) -> None:
        super().__init__(config)

        self.instrument: Instrument | None = None  # Initialized in on_start

        # Artifact plumbing (see `initialize`)
        self._initialized = False
        self._artifacts_io = None
        self._trader_helper = None
        self.save_artifacts = False
        # The price series the chart app plots, keyed by ts_event. `BaseStrategy` builds the same
        # structure; this strategy does not inherit from it, so it keeps its own copy.
        self._tick_data_dicts: dict[int, dict] = {}
        self._tick_event_dt_adjusted = 0

        # Price tracking
        self._recent_prices: deque = deque(maxlen=config.avg_ticks)
        self._collecting = False
        self._last_price: float | None = None
        self.open_price: float | None = None

        # State machine
        self._side: OrderSide = OrderSide.SELL
        self._entry_orders: list[Order] = []
        self._any_fill = False
        self._entry_deadline_passed = False
        self._flips = 0
        self._pending_flip_side: OrderSide | None = None

        # Exit legs
        self._tp_order: LimitOrder | None = None
        self._sl_order: StopMarketOrder | None = None
        self._pending_oco = False  # An entry filled before open_price was known
        self._flatten_order: LimitOrder | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, artifacts_location: Path | None = None, trader_helper=None) -> None:
        """Match the ``BaseStrategy`` hook that ``custom.utils.run_utils.run_strategy`` calls."""
        self._initialized = True
        self._trader_helper = trader_helper
        if artifacts_location is not None:
            from custom.artifacts import ArtifactsIO

            self._artifacts_io = ArtifactsIO(artifacts_location)
            self.save_artifacts = True

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.subscribe_trade_ticks(self.config.instrument_id)

        now = self.clock.utc_now()
        open_at = et_time_today(now, self.config.open_at_et)
        deadline = open_at + pd.Timedelta(seconds=self.config.entry_window_secs)

        alerts = [
            (_ALERT_COLLECT, et_time_today(now, self.config.collect_from_et), self._on_collect),
            (_ALERT_ARM, et_time_today(now, self.config.arm_at_et), self._on_arm),
            (_ALERT_OPEN, open_at, self._on_open),
            (_ALERT_DEADLINE, deadline, self._on_deadline),
            (_ALERT_FLATTEN, et_time_today(now, self.config.flatten_at_et), self._on_flatten),
        ]
        for name, alert_time, callback in alerts:
            if alert_time > now:
                self.clock.set_time_alert(name=name, alert_time=alert_time, callback=callback)
            else:
                self.log.warning(f"Alert {name} at {alert_time} is already past, not scheduling")

    def on_stop(self) -> None:
        self._cancel_all_entries()
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_trade_ticks(self.config.instrument_id)

    # ------------------------------------------------------------------
    # Time alerts
    # ------------------------------------------------------------------

    def _on_collect(self, event: TimeEvent) -> None:
        self._collecting = True
        self.log.info("Collecting trade ticks for the opening average", color=LogColor.BLUE)

    def _on_arm(self, event: TimeEvent) -> None:
        if not self._recent_prices:
            self.log.warning("No trade ticks collected, cannot arm entry orders")
            return

        avg_price = sum(self._recent_prices) / len(self._recent_prices)
        self.log.info(
            f"Arming entries off {len(self._recent_prices)}-tick average {avg_price:.4f}",
            color=LogColor.BLUE,
        )
        self._submit_entries(OrderSide.SELL, avg_price)

    def _on_open(self, event: TimeEvent) -> None:
        if self._last_price is None:
            self.log.warning("No trade tick seen before the open, cannot set open_price")
            return

        self.open_price = self._last_price
        self.log.info(f"open_price={self.open_price:.4f}", color=LogColor.GREEN)

        # Reprice whatever is still resting to the real opening print.
        for order in list(self._entry_orders):
            if not order.is_open:
                continue  # Filled pre-open, or already gone; `_on_order_filled` handled it
            offset = self._offset_of(order)
            if offset is None:
                continue
            self.modify_order(order, price=self._entry_price(order.side, offset))

        # An entry that filled before the open could not build its OCO without open_price.
        if self._pending_oco:
            self._pending_oco = False
            self._sync_oco()

    def _on_deadline(self, event: TimeEvent) -> None:
        self._entry_deadline_passed = True
        self._pending_flip_side = None
        self._cancel_all_entries()
        self.log.info("Entry window closed", color=LogColor.YELLOW)

    def _on_flatten(self, event: TimeEvent) -> None:
        self.log.info("Force-flattening", color=LogColor.YELLOW)
        self.cancel_all_orders(self.config.instrument_id)

        net = self._net_position()
        if net == 0:
            return

        if self._last_price is None:
            self.log.warning("No trade tick seen, flattening at market")
            self.close_all_positions(self.config.instrument_id)
            return

        # Cross the last trade by `flatten_offset` so the limit is marketable, rather than resting
        # at a price the market has to come back to.
        exit_side = OrderSide.BUY if net < 0 else OrderSide.SELL
        signed = self.config.flatten_offset if exit_side == OrderSide.BUY else -self.config.flatten_offset
        price = self.instrument.make_price(self._last_price + signed)

        order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=self.instrument.make_qty(abs(net)),
            price=price,
            time_in_force=TimeInForce.DAY,
            post_only=False,
            reduce_only=True,
            tags=[FLATTEN_TAG],
        )
        self._flatten_order = order
        self.log.info(
            f"Flatten {'BUY' if exit_side == OrderSide.BUY else 'SELL'} {abs(net)} "
            f"limit={price} (last={self._last_price:.4f})",
            color=LogColor.YELLOW,
        )
        self.submit_order(order)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def on_trade_tick(self, tick: TradeTick) -> None:
        price = float(tick.price)
        self._last_price = price
        if self._collecting:
            self._recent_prices.append(price)

        self._maybe_flip(price)
        self._save_tick_data(tick)

    def _save_tick_data(self, tick: TradeTick) -> None:
        """
        Record a tick for the chart app, as `BaseStrategy._save_tick_data` does.

        Ticks are keyed by ts_event, so simultaneous ticks would collide; nudge the key forward
        rather than overwrite, which is what keeps every trade in the plotted series.
        """
        if not self.save_artifacts:
            return
        if self._tick_event_dt_adjusted >= tick.ts_event:
            self._tick_event_dt_adjusted += 1
        else:
            self._tick_event_dt_adjusted = tick.ts_event
        self._tick_data_dicts[self._tick_event_dt_adjusted] = {
            "price": float(tick.price),
            "size": int(tick.size),
            "ts_event": tick.ts_event,
        }

    def on_dispose(self) -> None:
        # Without this the chart app falls back to whatever `ticks_and_metrics.pkl` a previous run
        # left behind, and plots this run's orders over a stale price series.
        if self.save_artifacts:
            self._artifacts_io.save_ticks_and_metrics(self._tick_data_dicts)

    # ------------------------------------------------------------------
    # Entries and flipping
    # ------------------------------------------------------------------

    def _entry_price(self, side: OrderSide, offset: float) -> Price:
        """Price an entry ``offset`` away from the reference, on the far side of the market."""
        base = self.open_price
        signed = offset if side == OrderSide.SELL else -offset
        return self.instrument.make_price(base + signed)

    def _offset_of(self, order: Order) -> float | None:
        """Recover the configured offset an entry order was created with, from its tags."""
        for tag in order.tags or []:
            if tag.startswith(f"{ENTRY_TAG}_"):
                return float(tag.split("_", 1)[1])
        return None

    def _submit_entries(self, side: OrderSide, base_price: float) -> None:
        self._side = side
        for offset in self.config.entry_offsets:
            signed = offset if side == OrderSide.SELL else -offset
            order = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=self.instrument.make_qty(self.config.trade_size),
                price=self.instrument.make_price(base_price + signed),
                time_in_force=TimeInForce.DAY,
                # NOT post_only: the 09:30 reprice can cross on a gap, and the matching engine
                # rejects a modify that would make a post-only order marketable.
                post_only=False,
                tags=[f"{ENTRY_TAG}_{offset}"],
            )
            self._entry_orders.append(order)
            self.submit_order(order)

    def _open_entries(self) -> list[Order]:
        return [o for o in self._entry_orders if o.is_open]

    def _cancel_all_entries(self) -> None:
        open_entries = self._open_entries()
        if open_entries:
            self.cancel_orders(open_entries)

    def _maybe_flip(self, price: float) -> None:
        if self.open_price is None:
            return  # Not open yet
        if self._any_fill or self._entry_deadline_passed:
            return  # A fill locks in the side for the day
        if self._pending_flip_side is not None:
            return  # Waiting on cancels to confirm
        if self._flips >= self.config.max_flips:
            return

        threshold = self.config.flip_threshold
        if self._side == OrderSide.SELL and price <= self.open_price - threshold:
            self._begin_flip(OrderSide.BUY, price)
        elif self._side == OrderSide.BUY and price >= self.open_price + threshold:
            self._begin_flip(OrderSide.SELL, price)

    def _begin_flip(self, new_side: OrderSide, price: float) -> None:
        """
        Cancel the resting entries, then submit the other side once they are confirmed gone.

        Two-phase because cancel latency is ~55ms: a naive cancel-then-submit would leave old
        sells and new buys live at the same time on a NETTING venue, where both filling nets to
        zero with two reduce-only exits stranded on nothing.
        """
        self._pending_flip_side = new_side
        self._flips += 1
        self.log.info(
            f"Flip #{self._flips} to {'BUY' if new_side == OrderSide.BUY else 'SELL'} at {price:.4f}",
            color=LogColor.MAGENTA,
        )
        open_entries = self._open_entries()
        if open_entries:
            self.cancel_orders(open_entries)
        else:
            self._complete_flip()

    def _complete_flip(self) -> None:
        new_side = self._pending_flip_side
        if new_side is None:
            return
        self._pending_flip_side = None
        if self._any_fill or self._entry_deadline_passed:
            return  # Raced with a fill or the deadline; do not re-arm
        self._entry_orders = [o for o in self._entry_orders if o.is_open]
        self._submit_entries(new_side, self.open_price)

    def on_order_canceled(self, event) -> None:
        if self._pending_flip_side is not None and not self._open_entries():
            self._complete_flip()

    # ------------------------------------------------------------------
    # Fills and the OCO exit
    # ------------------------------------------------------------------

    def on_order_filled(self, event) -> None:
        order = self.cache.order(event.client_order_id)
        if order is None:
            return
        tags = order.tags or []
        if any(t in (TAKE_PROFIT_TAG, STOP_LOSS_TAG) for t in tags):
            return  # An exit leg filled; the OUO contingency cancels its sibling

        if not any(t.startswith(ENTRY_TAG) for t in tags):
            return

        self._any_fill = True  # Flipping is now off for the day
        self._pending_flip_side = None

        if self.open_price is None:
            # Filled before 09:30 — the take-profit price is not known yet.
            self._pending_oco = True
            self.log.warning("Entry filled before the open; deferring OCO until open_price is set")
            return

        self._sync_oco()

    def _net_position(self) -> int:
        """Current signed exposure. A seam so tests can drive sizing without a filled cache."""
        return int(self.portfolio.net_position(self.config.instrument_id))

    def _sync_oco(self) -> None:
        """Submit the OCO exit, or resize it in place if a live one already covers the position."""
        net = self._net_position()
        qty = abs(net)
        if qty == 0:
            return

        # Derive the exit side from the actual exposure rather than the armed side: entry orders
        # fill in partials, so the position can be rebuilt after an earlier pair has closed out.
        exit_side = OrderSide.BUY if net < 0 else OrderSide.SELL

        # `is_closed` (not `is_open`) is the right test here: a just-submitted leg is neither open
        # nor closed while in flight, and treating that as "needs a new pair" would double-submit.
        pair_dead = (
            self._tp_order is None
            or (self._tp_order.is_closed and (self._sl_order is None or self._sl_order.is_closed))
        )
        if pair_dead:
            # Either the first fill, or a later partial fill after the previous pair completed.
            self._submit_oco(exit_side, qty)
            return

        # Resize the existing pair rather than adding a second one.
        self._resize_legs()

    def _resize_legs(self) -> None:
        """
        Size both live exit legs to the current net exposure.

        Recomputed from the position every time rather than from a remembered target: entries fill
        in partials (sometimes several within the same nanosecond), so any cached quantity goes
        stale immediately. Legs still in flight are skipped here and picked up by
        `on_order_accepted`, which is what closes the submission-latency race.
        """
        qty = abs(self._net_position())
        if qty == 0:
            return
        new_qty = self.instrument.make_qty(qty)
        for leg in (self._tp_order, self._sl_order):
            if leg is not None and leg.is_open and leg.quantity != new_qty:
                self.modify_order(leg, quantity=new_qty)

    def on_order_accepted(self, event) -> None:
        # A partial fill can land while the OCO legs are still in flight, when they are neither
        # open nor closed and so cannot be modified. Re-check sizing as soon as they are live.
        for leg in (self._tp_order, self._sl_order):
            if leg is not None and leg.client_order_id == event.client_order_id:
                self._resize_legs()
                return

    def _submit_oco(self, exit_side: OrderSide, qty: int) -> None:
        """
        Build a two-leg OUO order list.

        ``OrderFactory.oco_sell`` hardcodes ``OrderSide.SELL`` on both legs, so it cannot cover a
        short. This mirrors its body with the side parameterized.
        """
        # Take profit back at the opening print; stop `stop_offset` beyond it.
        tp_price = self.instrument.make_price(self.open_price + self.config.take_offset)
        if exit_side == OrderSide.BUY:  # Covering a short
            sl_trigger = self.instrument.make_price(self.open_price + self.config.stop_offset)
        else:  # Selling out of a long
            sl_trigger = self.instrument.make_price(self.open_price - self.config.stop_offset)

        order_list_id = self.order_factory.generate_order_list_id()
        tp_client_order_id = self.order_factory.generate_client_order_id()
        sl_client_order_id = self.order_factory.generate_client_order_id()
        quantity = self.instrument.make_qty(qty)

        tp_order = LimitOrder(
            trader_id=self.order_factory.trader_id,
            strategy_id=self.order_factory.strategy_id,
            instrument_id=self.config.instrument_id,
            client_order_id=tp_client_order_id,
            order_side=exit_side,
            quantity=quantity,
            price=tp_price,
            init_id=UUID4(),
            ts_init=self.clock.timestamp_ns(),
            time_in_force=TimeInForce.DAY,
            post_only=False,
            reduce_only=True,
            display_qty=None,
            emulation_trigger=TriggerType.NO_TRIGGER,
            trigger_instrument_id=None,
            contingency_type=ContingencyType.OUO,
            order_list_id=order_list_id,
            linked_order_ids=[sl_client_order_id],
            tags=[TAKE_PROFIT_TAG],
        )
        sl_order = StopMarketOrder(
            trader_id=self.order_factory.trader_id,
            strategy_id=self.order_factory.strategy_id,
            instrument_id=self.config.instrument_id,
            client_order_id=sl_client_order_id,
            order_side=exit_side,
            quantity=quantity,
            trigger_price=sl_trigger,
            trigger_type=TriggerType.DEFAULT,
            init_id=UUID4(),
            ts_init=self.clock.timestamp_ns(),
            time_in_force=TimeInForce.DAY,
            reduce_only=True,
            emulation_trigger=TriggerType.NO_TRIGGER,
            trigger_instrument_id=None,
            contingency_type=ContingencyType.OUO,
            order_list_id=order_list_id,
            linked_order_ids=[tp_client_order_id],
            tags=[STOP_LOSS_TAG],
        )

        self._tp_order = tp_order
        self._sl_order = sl_order
        self.log.info(
            f"OCO exit {'BUY' if exit_side == OrderSide.BUY else 'SELL'} {qty} "
            f"tp={tp_price} sl={sl_trigger}",
            color=LogColor.GREEN,
        )
        self.submit_order_list(OrderList(order_list_id=order_list_id, orders=[sl_order, tp_order]))

    # ------------------------------------------------------------------
    # Introspection (used by the sweep harness)
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Per-run summary of what the state machine actually did."""
        entries = self._entry_orders
        filled = [o for o in entries if o.filled_qty > 0]
        return {
            "open_price": self.open_price,
            "side": "BUY" if self._side == OrderSide.BUY else "SELL",
            "flips": self._flips,
            "entries_submitted": len(entries),
            "entries_filled": len(filled),
            "filled_offsets": sorted(self._offset_of(o) for o in filled),
            "net_position": self._net_position(),
            "tp_status": self._tp_order.status_string() if self._tp_order else None,
            "sl_status": self._sl_order.status_string() if self._sl_order else None,
        }
