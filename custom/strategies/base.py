import asyncio
from abc import abstractmethod
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from pandas import Timestamp

from custom.artifacts import ArtifactsIO
from custom.strategies._open_order import OpenOrder, CLOSED_STATUS_LIST
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper
from nautilus_trader.common.component import TimeEvent

from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.core.message import Event
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BarType
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


class BaseStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float
    allow_trades: bool = True
    print_update_every_secs: int = None


class BaseStrategy(Strategy):
    buy_signal_delay_secs: int = 1
    _POSITION_DISCREPANCY_ALLOW_SECS = 10  # Raise if alpaca vs nt discrepancy lasts for longer than this
    _MODIFY_REJECT_COOLDOWN_SECS = 1  # Seconds to block retries after a ModifyRejected
    _RECONCILE_COOLDOWN_SECS = 3  # Minimum seconds between reconciliation attempts

    def __init__(self, config: BaseStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument = None  # Initialized in on_start
        self.metrics_to_save_on_tick = []
        self.metrics_to_save_on_1min = []

        self.stop_price = None
        self.last_buy_dt: Timestamp = pd.Timestamp("1990", tz="UTC")
        self._tick_data_dicts = {}
        self._tick_event_dt_adjusted = 0

        self._buy_signals_count = 0
        self.buy_orders_count = 0
        self.buy_sell_signals = []
        self.last_buy_signal_dt = 0

        # Used to track order modifications to avoid sending duplicate modify orders when the cache is slow
        self._already_cancelled_orders = set()
        self._uncached_orders = set()
        self._last_log_update_dt = 0

        self._initialized = False
        self._artifacts_io = None
        self.save_artifacts = False
        self._trader_helper = None

        self._open_buys: set[OpenOrder] = set()
        self._open_sells: set[OpenOrder] = set()
        self._position_discrepancy_start_ns = None
        self._raise_msg = None
        self._last_tick = None
        self._total_buy_qty = 0

        self._stopping_out = False
        self._exec_engine = None  # Set by run_utils after node.build()
        self._force_reconcile_count = 0
        self._last_force_reconcile_ns = 0
        self._last_negative_flatten_ns = 0
        self._reconciliation_task: asyncio.Task | None = None

    def initialize(self, artifacts_location: Optional[Path], trader_helper: Optional[AlpacaTraderHelper] = None):
        self._initialized = True
        self._trader_helper = trader_helper
        if artifacts_location is not None:
            self._artifacts_io = ArtifactsIO(artifacts_location)
            self.save_artifacts = True

    @property
    def position_qty(self):
        return int(self.portfolio.net_position(self.config.instrument_id))

    @property
    def open_buys(self) -> set[OpenOrder]:
        self._open_buys = {open_order for open_order in self._open_buys if open_order.is_open}
        return self._open_buys

    @property
    def open_sells(self) -> set[OpenOrder]:
        self._open_sells = {open_order for open_order in self._open_sells if open_order.is_open}
        return self._open_sells

    @property
    def open_sells_qty(self) -> int:
        return int(sum(o.leaves_qty for o in self.open_sells))

    @property
    def open_orders(self) -> set[OpenOrder]:
        return self.open_buys.union(self.open_sells)

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
        self._open_buys.discard(order)
        self._open_sells.discard(order)

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

    @property
    def open_buy_qty(self):
        return sum(int(open_order.leaves_qty) for open_order in self._open_buys)

    @property
    def open_sell_qty(self):
        return sum(int(open_order.leaves_qty) for open_order in self._open_sells)

    def modify_open_order(self, open_order: OpenOrder, quantity, price):
        now_ns = self.clock.timestamp_ns()
        qty_obj = self.instrument.make_qty(quantity)
        price_obj = self.instrument.make_price(price)
        if open_order.update_last_modify_if_allowed(qty_obj, price_obj, now_ns):
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

    def sell_position_at_price(self, new_limit_price):
        remaining_qty_to_sell = self.position_qty
        for order in self.open_sells:
            order_qty = order.quantity
            remaining_qty_to_sell -= order_qty
            if order.price != new_limit_price:
                modified = self.modify_open_order(order, quantity=order_qty, price=new_limit_price)
                if not modified:
                    self.log.debug(
                        f"While stopping out, did not modify order {order.client_order_id} to {new_limit_price}."
                    )
        if remaining_qty_to_sell > 0:
            self.sell(quantity=remaining_qty_to_sell, limit_price=new_limit_price, tag="s")

    @property
    def max_position_allowed(self):
        return self.config.max_position_multiplier * self.config.trade_size

    def _max_buy_qty_allowed(self):
        return self.max_position_allowed - self.open_buy_qty - self.position_qty

    def _max_sell_qty_allowed(self):
        return self.position_qty - self.open_sell_qty

    def log_buy_signal(self, tick, tag=None):
        return  # TODO: Rethink buy signals?
        if (self._tick_event_dt_adjusted - self.last_buy_signal_dt) / 1e9 < self.buy_signal_delay_secs:
            return
        self._buy_signals_count += 1
        if tag is None:
            tag = f"{self._buy_signals_count}"
        buy_signal_dict = dict(
            side="buy", time=self._tick_event_dt_adjusted, price=float(tick.price), tag=tag, win=None, win_delay=None
        )
        self.buy_sell_signals.append(buy_signal_dict)
        self.log.info(f"Buy signal {self._buy_signals_count}: {buy_signal_dict}")
        self.last_buy_signal_dt = self._tick_event_dt_adjusted

    def _update_buy_signals(self, tick):
        # Track buy-sell signals
        for signal in self.buy_sell_signals:
            if signal["win"] is None or signal["win_delay"] is None:
                if signal["side"] == "buy":
                    win = tick.price >= (signal["price"] + self.config.stop_loss)
                    loss = tick.price <= (signal["price"] - self.config.stop_loss)
                    if signal["win"] is None and (win or loss):
                        signal["win"] = win
                        signal["win_time"] = self._tick_event_dt_adjusted
                    if (
                        signal["win_delay"] is None
                        and (win or loss)
                        and ((self._tick_event_dt_adjusted - signal["time"]) > 80 * 1e6)
                    ):
                        signal["win_delay"] = win
                        signal["win_delay_time"] = self._tick_event_dt_adjusted

    def _raise_if_needed(self):
        """
        Need separate method because _reconcile raises were not causing system to raise
        """
        if self._raise_msg is not None:
            self.log.error(f"Raising RuntimeError: {self._raise_msg}")
            raise RuntimeError(self._raise_msg)

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._raise_if_needed()
        self._last_tick = tick
        if self._tick_event_dt_adjusted >= tick.ts_event:
            self._tick_event_dt_adjusted += 1
        else:
            self._tick_event_dt_adjusted = tick.ts_event

        tick_data = {"price": float(tick.price), "size": int(tick.size)}
        if self.save_artifacts:
            tick_data = {
                **tick_data,
                "ts_event": tick.ts_event,
                "ts_recv": tick.ts_init,
                "ts_clock": self.clock.utc_now(),
                "ts_now": pd.Timestamp.utcnow(),
            }

        #  Actual operations of this method
        if self.indicators_initialized():
            self._on_trade_tick(tick)

        # Record if needed
        if self.save_artifacts:
            tick_data["ts_now_after"] = pd.Timestamp.utcnow()

        # TODO: Rethink buy signals?
        # self._update_buy_signals(tick)

        if self.save_artifacts:
            for metric in self.metrics_to_save_on_tick:
                tick_data = {**tick_data, **metric.get_vals()}
            self._tick_data_dicts[self._tick_event_dt_adjusted] = tick_data

            for metric in self.metrics_to_save_on_1min:
                # FIXME: Implement this
                pass

    def _submit_orders_if_allowed(self, order_or_order_list, expire_time=None) -> None:
        buy_included = False
        if self.config.allow_trades:
            # For OrderList type, submit all at once, but parse through each to check for a buy order (from bracket)
            # and to add to uncached
            if isinstance(order_or_order_list, OrderList):
                raise ValueError("Need to figure out how to update open_buys and open_sells")
                self.submit_order_list(order_or_order_list)
                for order in order_or_order_list.orders:
                    if order.side == OrderSide.BUY:
                        buy_included = True

            # If single item, submit and add to uncached
            elif isinstance(order_or_order_list, Order):
                self.submit_order(order_or_order_list)
                open_order = OpenOrder(order_or_order_list, expire_time=expire_time)
                if order_or_order_list.side == OrderSide.BUY:
                    buy_included = True
                    self._open_buys.add(open_order)
                if order_or_order_list.side == OrderSide.SELL:
                    self._open_sells.add(open_order)
            else:
                raise ValueError(f"Unexpected order type: {type(order_or_order_list)}")
        if buy_included:
            self.buy_orders_count += 1
            self.last_buy_dt = self.clock.utc_now()

    def _submit_limit_order(self, side: OrderSide, quantity: int, limit_price: float, tag: str, cancel_after_secs=None):
        tags = [tag]
        order: LimitOrder = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(quantity),
            price=self.instrument.make_price(limit_price),
            time_in_force=TimeInForce.DAY,
            expire_time=None,
            tags=tags,
        )
        utc_now = self.clock.utc_now()
        expire_time = utc_now + timedelta(seconds=cancel_after_secs) if cancel_after_secs is not None else None
        self._submit_orders_if_allowed(order, expire_time=expire_time)

    def buy(self, quantity, limit_price, tag, cancel_after_secs=None) -> None:
        if self._stopping_out:
            self.log.info(f"Ignoring buy request because self._stopping_out is True")
            return
        allowed_qty = min(quantity, self._max_buy_qty_allowed())
        if allowed_qty != quantity:
            self.log.debug(
                f"Buy quantity reduced from {quantity} to {allowed_qty} to avoid exceeding max position of {self.max_position_allowed}."
            )
        if allowed_qty > 0:
            self._submit_limit_order(OrderSide.BUY, allowed_qty, limit_price, tag, cancel_after_secs)

    def sell(self, quantity, limit_price, tag, cancel_after_secs=None) -> None:
        allowed_qty = min(quantity, self._max_sell_qty_allowed())
        if allowed_qty != quantity:
            self.log.info(f"Sell quantity reduced from {quantity} to {allowed_qty} to avoid going short.")
        if allowed_qty > 0:
            self._submit_limit_order(OrderSide.SELL, allowed_qty, limit_price, tag, cancel_after_secs)

    @abstractmethod
    def _on_order_filled(self, order) -> None:
        pass

    def on_order_filled(self, order) -> None:
        self._on_order_filled(order)
        if order.order_side == OrderSide.BUY:
            self._total_buy_qty += int(order.last_qty)

    @abstractmethod
    def _on_trade_tick(self, tick: TradeTick) -> None:
        pass

    def on_order_event(self, order_event) -> None:
        if isinstance(order_event, OrderRejected):
            cache_order = self.cache.order(order_event.client_order_id)
            # self._remove_open_order(order)
            if cache_order.side == OrderSide.BUY:
                if "insufficient qty available" in cache_order.last_event.reason:
                    self.log.warning(
                        f"Buy order rejected for insufficient quantity. Accidental shorting is likely. Running reconciliation."
                    )
                    self._reconcile()
        elif isinstance(order_event, OrderModifyRejected):
            self.clear_open_order_modify_params(order_event)

            # Only apply cooldown for errors where retrying quickly won't help
            _COOLDOWN_REASONS = (
                "order is not open",
                "qty must be",  # qty must be > filled_qty
                "cannot replace order in pending_new status",
                "cannot replace order in pending_cancel status",
                "order is already in",  # filled/replaced/rejected state
                "already closed",
                "order already pending replacement",
                "cannot be sold short",
                "order chain not fully replaced",
                "too_late_to_cancel",
                "insufficient qty available for order",
            )
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
                        self._open_buys.add(open_order)
                    elif cache_order.side == OrderSide.SELL:
                        self._open_sells.add(open_order)
                    self.log.error(
                        f"Re-added {cache_order.client_order_id} to open_orders (side={cache_order.side}, status={cache_order.status})"
                    )

    def close_position_limit_order(self):
        last_trade = self.cache.trade_tick(self.config.instrument_id)
        limit_price = self.position_avg_px if last_trade is None else last_trade.price
        self.sell_position_at_price(self.instrument.make_price(limit_price * 0.9))

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
            position_at_broker = self._trader_helper.get_position_obj()
            position_at_broker = int(position_at_broker.qty)
        else:
            # Running a backtest, so just use the local position
            position_at_broker = self.position_qty

        # FLATTEN POSITION IF NEEDED
        if position_at_broker < 0:
            now_ns = self.clock.timestamp_ns()
            if (now_ns - self._last_negative_flatten_ns) / 1e9 < 1.0:
                return  # Cooldown: only attempt flatten once per second
            self._last_negative_flatten_ns = now_ns
            self.log.error(
                f"Position {position_at_broker} is negative! Canceling all existing open_sells and buying to flatten."
            )
            for open_order in self.open_sells:
                self.cancel_open_order(open_order)
            price = self._last_tick.price * 1.1
            if len(self.open_buys) > 0:
                open_buy_to_modify = list(self.open_buys)[0]
                self.modify_open_order(open_buy_to_modify, quantity=abs(position_at_broker), price=price)
            else:
                self._submit_limit_order(OrderSide.BUY, abs(position_at_broker), price, "flatten")
            return  # Return from here to allow orders time to cancel and buy

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

        open_buys_str = "\n".join(str(o) for o in self.open_buys) if len(self.open_buys) > 0 else ""
        open_sells_str = "\n".join(str(o) for o in self.open_sells) if len(self.open_sells) > 0 else ""
        OpenBuysQty = int(sum(o.leaves_qty for o in self.open_buys))
        OpenSellsQty = int(sum(o.leaves_qty for o in self.open_sells))

        position_str = ""
        if self.position_qty > 0:
            avg_px = self.position_avg_px
            gain = self._last_tick.price - avg_px
            unrealized = self.position_qty * gain
            diff = self.position_qty - OpenSellsQty
            if diff > 0:
                self.log.warning(f"Diff: {diff}")
            position_str = f"Position {self.position_qty} @ {round(avg_px, 2)} | PerShare {round(gain, 2)} | PnL ${round(unrealized, 2)} | {OpenSellsQty=} | Diff={diff}\n"
        metrics_data = {}
        for metric in self.metrics_to_save_on_tick + self.metrics_to_save_on_1min:
            vals = {k: str(round(v, 3)) for k, v in metric.get_vals().items()}
            metrics_data = {**metrics_data, **vals}
        self.log.info(
            f"UPDATE: {self.config.instrument_id}\n{tick_str}\n"
            f"Total Bought {self._total_buy_qty} | Realized: {realized_pnl} | {OpenBuysQty=} Orders: {open_buys_str}\n"
            f"{position_str}"
            # f"{open_sells_str}"
            f"{self._print_update()}"
            f"{metrics_data}",
            color=LogColor.CYAN,
        )
        self._last_log_update_dt = self._tick_event_dt_adjusted

    def on_start(self) -> None:
        if not self._initialized:
            raise RuntimeError("Strategy must be initialized before starting. Call method `initialize` first.")

        # TIME INTERVAL FUNCTIONS
        self.clock.set_timer(
            name="cancel_orders_timer",
            interval=timedelta(seconds=0.5),
            callback=self._cancel_partial_fills_and_orders_past_timeout,
        )
        self.clock.set_timer(name="reconcile_internal_fn", interval=timedelta(seconds=3), callback=self._reconcile)
        if self.config.print_update_every_secs is not None:
            self.clock.set_timer(
                name="print_update",
                interval=timedelta(seconds=self.config.print_update_every_secs),
                callback=self.print_update,
            )

        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        # TICK DATA
        # Register indicators and request historical trade ticks if needed
        max_tick_lookback = 0
        for metric in self.metrics_to_save_on_tick:
            self.register_indicator_for_trade_ticks(self.config.instrument_id, metric.obj)
            max_tick_lookback = max(max_tick_lookback, metric.tick_lookback)

        if max_tick_lookback > 0:
            # Set a long lookback to ensure we get at least `max_tick_lookback` ticks back
            trade_tick_start = self.clock.utc_now() - timedelta(days=2)
            self.request_trade_ticks(self.config.instrument_id, start=trade_tick_start, limit=max_tick_lookback)

        self.subscribe_trade_ticks(self.config.instrument_id)

        bar_type = BarType.from_str(f"{self.config.instrument_id}-1-MINUTE-LAST-INTERNAL")
        # bar_type = BarType.from_str(f"{self.config.instrument_id}-1-MINUTE-LAST-EXTERNAL")
        for metric in self.metrics_to_save_on_1min:
            self.register_indicator_for_bars(bar_type=bar_type, indicator=metric.obj)

        # Subscribe to 1-minute bars
        # TODO: Test if "internal" vs "external" bars does anything for us
        if len(self.metrics_to_save_on_1min) > 0:
            self.subscribe_bars(bar_type)

        # self.subscribe_quote_ticks(self.config.instrument_id)
        # self.subscribe_order_book_depth(self.config.instrument_id, book_type=BookType.L1_MBP)
        # self.subscribe_order_book_deltas(self.config.instrument_id, depth=20)  # For debugging
        # self.subscribe_order_book_at_interval(self.config.instrument_id, depth=20)  # For debugging

        # Get historical data
        # if self.config.request_historical_bars:
        #     self.request_bars( self.config.bar_type, start=self._clock.utc_now() - pd.Timedelta(days=1) )
        # self.request_quote_ticks(self.config.instrument_id)

    def on_stop(self) -> None:
        if self.position_qty > 0:
            # Cancel BUY orders, but use "close_position_limit_order" to modify sell orders
            self.cancel_all_orders(self.config.instrument_id, order_side=OrderSide.BUY)
            self.close_position_limit_order()
        else:
            self.cancel_all_orders(self.config.instrument_id)

        # Unsubscribe from data
        self.unsubscribe_trade_ticks(self.config.instrument_id)

    def on_dispose(self) -> None:
        if self.save_artifacts:
            self._artifacts_io.save_ticks_and_metrics(self._tick_data_dicts)
            self._artifacts_io.save_signals(self.buy_sell_signals)

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
        pass

    def on_save(self) -> dict[str, bytes]:
        return {}

    def on_load(self, state: dict[str, bytes]) -> None:
        pass
