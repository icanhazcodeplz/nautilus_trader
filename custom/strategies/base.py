from abc import abstractmethod
from copy import copy
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from pandas import Timestamp

from custom.artifacts import ArtifactsIO
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.core.message import Event
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick

from nautilus_trader.model import Quantity, Price
from nautilus_trader.model.enums import OrderSide, ContingencyType, OrderStatus
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.events import OrderRejected, OrderFilled
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import LimitOrder, Order
from nautilus_trader.model.orders.list import OrderList
from nautilus_trader.trading.strategy import Strategy

CLOSED_STATUS_LIST = {
    OrderStatus.DENIED,
    OrderStatus.FILLED,
    OrderStatus.REJECTED,
    OrderStatus.CANCELED,
    OrderStatus.EXPIRED,
    # OrderStatus.PENDING_CANCEL,
}

ONLY_MODIFY_EVERY_NS = 80e6  # e6 converts from ms to ns
CANCEL_PARTIAL_FILLS_AFTER_SECS = 3


class OpenOrder:
    def __init__(self, order: Order, expire_time=None):
        self.order = order
        self.expire_time = expire_time

        self._last_modify_ns = 0
        self._last_modify_qty = None
        self._last_modify_price = None
        self._first_partial_fill_ns = None

    @property
    def is_open(self):
        return self.order.status not in CLOSED_STATUS_LIST

    @property
    def price(self):
        return self._last_modify_price if self._last_modify_price is not None else self.order.price

    @property
    def quantity(self):
        return self._last_modify_qty if self._last_modify_qty is not None else self.order.quantity

    @property
    def first_partial_fill_ns(self):
        if self._first_partial_fill_ns is not None:
            return self._first_partial_fill_ns
        if self.order.filled_qty > 0:
            self._first_partial_fill_ns = min(
                fill.ts_event for fill in self.order.events if isinstance(fill, OrderFilled)
            )
        return self._first_partial_fill_ns

    @property
    def leaves_qty(self):
        return self.quantity - self.filled_qty

    @property
    def filled_qty(self):
        return self.order.filled_qty

    @property
    def venue_order_id(self):
        return self.order.venue_order_id

    @property
    def client_order_id(self):
        return self.order.client_order_id

    def _can_be_modified(self, now_ns):
        if (now_ns - self._last_modify_ns) < ONLY_MODIFY_EVERY_NS:
            return False
        return self.order.status not in [
            OrderStatus.SUBMITTED,
            OrderStatus.PENDING_UPDATE,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.FILLED,
        ]

    def update_last_modify_if_allowed(self, quantity: Quantity, price: Price, now_ns: int):
        if not self._can_be_modified(now_ns):
            return False
        if quantity == self._last_modify_qty and price == self._last_modify_price:
            return False
        self._last_modify_ns = now_ns
        self._last_modify_qty = quantity
        self._last_modify_price = price
        return True

    def __eq__(self, other):
        return self.client_order_id == other.client_order_id

    def __hash__(self):
        return hash(self.order.client_order_id)

    def __repr__(self):
        side_str = "SELL" if self.order.side == OrderSide.SELL else "BUY"
        return f"OpenOrder({side_str} client_id={self.client_order_id}, venue_id={self.venue_order_id} price={self.price}, quantity={self.quantity}, leaves_qty={self.leaves_qty})"


class BaseStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float
    allow_trades: bool = True


class BaseStrategy(Strategy):
    buy_signal_delay_secs: int = 1
    log_update_every_secs: int = None
    position_discrepancy_allow_secs = 10

    def __init__(self, config: BaseStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument = None  # Initialized in on_start
        self.tick_metrics_to_save = []

        self.stop_price = None
        self.last_buy_dt: Timestamp = pd.Timestamp("1990", tz="UTC")
        self._tick_data_dicts = {}
        self._tick_init_dt_adjusted = 0

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

        self._open_buys = set()
        self._open_sells = set()
        self._position_discrepancy_start_ns = None
        self._raise_msg = None

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
    def open_orders(self) -> set[OpenOrder]:
        return self.open_buys.union(self.open_sells)

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
            # TODO: Perhaps use order status instead of the existance of venue_order_id?
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
                    self.log.warning(
                        f"While stopping out, failed to modify order {order.client_order_id} to {new_limit_price}."
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
        if (self._tick_init_dt_adjusted - self.last_buy_signal_dt) / 1e9 < self.buy_signal_delay_secs:
            return
        self._buy_signals_count += 1
        if tag is None:
            tag = f"{self._buy_signals_count}"
        buy_signal_dict = dict(
            side="buy", time=self._tick_init_dt_adjusted, price=float(tick.price), tag=tag, win=None, win_delay=None
        )
        self.buy_sell_signals.append(buy_signal_dict)
        self.log.info(f"Buy signal {self._buy_signals_count}: {buy_signal_dict}")
        self.last_buy_signal_dt = self._tick_init_dt_adjusted

    def _update_buy_signals(self, tick):
        # Track buy-sell signals
        for signal in self.buy_sell_signals:
            if signal["win"] is None or signal["win_delay"] is None:
                if signal["side"] == "buy":
                    win = tick.price >= (signal["price"] + self.config.stop_loss)
                    loss = tick.price <= (signal["price"] - self.config.stop_loss)
                    if signal["win"] is None and (win or loss):
                        signal["win"] = win
                        signal["win_time"] = self._tick_init_dt_adjusted
                    if (
                        signal["win_delay"] is None
                        and (win or loss)
                        and ((self._tick_init_dt_adjusted - signal["time"]) > 80 * 1e6)
                    ):
                        signal["win_delay"] = win
                        signal["win_delay_time"] = self._tick_init_dt_adjusted

    def _raise_if_needed(self):
        """
        Need separate method because _reconcile raises were not causing system to raise
        """
        if self._raise_msg is not None:
            self.log.error(f"Raising RuntimeError: {self._raise_msg}")
            raise RuntimeError(self._raise_msg)

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._raise_if_needed()
        if self._tick_init_dt_adjusted >= tick.ts_init:
            self._tick_init_dt_adjusted += 1
        else:
            self._tick_init_dt_adjusted = tick.ts_init

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
            for metric in self.tick_metrics_to_save:
                tick_data = {**tick_data, **metric.get_vals()}
            self._tick_data_dicts[self._tick_init_dt_adjusted] = tick_data

        if (
            self.log_update_every_secs is not None
            and (self._tick_init_dt_adjusted - self._last_log_update_dt) / 1e9 > self.log_update_every_secs
        ):
            timestamp = pd.Timestamp(self._tick_init_dt_adjusted, unit="ns")
            self.log.info(
                f"Update\nTick {timestamp}: {tick_data}\nPosition {self.position_qty} @ {self.position_avg_px}\nBuy signals: {len(self.buy_sell_signals)} | Buys {self.buy_orders_count} | Open Buys {len(self.open_buys)} | Open Sells {len(self.open_sells)}"
            )
            self._last_log_update_dt = self._tick_init_dt_adjusted

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
        allowed_qty = min(quantity, self._max_buy_qty_allowed())
        if allowed_qty != quantity:
            self.log.debug(
                f"Buy quantity reduced from {quantity} to {allowed_qty} to avoid exceeding max position of {self.max_position_allowed}."
            )
        if allowed_qty > 0:
            self._submit_limit_order(OrderSide.BUY, allowed_qty, limit_price, tag, cancel_after_secs)

    def buy_bracket(self, quantity, limit_price, stop_loss, take_profit, tag, cancel_after_secs=None) -> None:
        allowed_qty = min(quantity, self._max_buy_qty_allowed())
        if allowed_qty != quantity:
            self.log.debug(
                f"Buy quantity reduced from {quantity} to {allowed_qty} to avoid exceeding max position of {self.max_position_allowed}."
            )
        if allowed_qty > 0:
            entry_tags = [tag]
            if cancel_after_secs is not None:
                expire_time = self.clock.utc_now() + timedelta(seconds=cancel_after_secs)
                entry_tags.append(expire_time)

            # Calculate stop-loss and take-profit prices
            sl_trigger_price = self.instrument.make_price(limit_price - stop_loss)
            tp_price = self.instrument.make_price(limit_price + take_profit)

            # Create bracket order with entry, stop-loss, and take-profit
            order_list: OrderList = self.order_factory.bracket(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(allowed_qty),
                contingency_type=ContingencyType.OUO,
                entry_order_type=OrderType.LIMIT,
                entry_price=self.instrument.make_price(limit_price),
                time_in_force=TimeInForce.DAY,
                entry_tags=entry_tags,
                tp_tags=[tag, "t"],
                sl_tags=[tag, "s"],
                tp_price=tp_price,
                tp_time_in_force=TimeInForce.DAY,
                sl_order_type=OrderType.STOP_LIMIT,
                sl_time_in_force=TimeInForce.DAY,
                sl_trigger_price=sl_trigger_price,
                # FIXME: hardcoded to 0.10 below stop_loss_price
                sl_price=self.instrument.make_price(limit_price - stop_loss - 0.1),
            )
            self._submit_orders_if_allowed(order_list)

    def sell_oco(self, quantity, stop_price, take_price, tag) -> None:
        # Create oco order with stop-loss, and take-profit
        order_list: OrderList = self.order_factory.oco_sell(
            instrument_id=self.config.instrument_id,
            quantity=self.instrument.make_qty(quantity),
            contingency_type=ContingencyType.OUO,  # One updates the other
            tp_tags=[tag, "t"],
            sl_tags=[tag, "s"],
            tp_price=self.instrument.make_price(take_price),
            tp_time_in_force=TimeInForce.DAY,
            sl_order_type=OrderType.STOP_LIMIT,
            sl_time_in_force=TimeInForce.DAY,
            sl_trigger_price=self.instrument.make_price(stop_price),
            # FIXME: hardcoded to 0.10 below stop_loss_price
            sl_price=self.instrument.make_price(stop_price - 0.1),
        )

        self._submit_orders_if_allowed(order_list)

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

    @abstractmethod
    def _on_trade_tick(self, tick: TradeTick) -> None:
        pass

    def on_order_event(self, order) -> None:
        if isinstance(order, OrderRejected):
            cache_order = self.cache.order(order.client_order_id)
            # self._remove_open_order(order)
            if cache_order.side == OrderSide.BUY:
                if "insufficient qty available" in cache_order.last_event.reason:
                    self.log.warning(
                        f"Buy order rejected for insufficient quantity. Accidental shorting is likely. Running reconciliation."
                    )
                    self._reconcile()

    def close_position_limit_order(self):
        last_trade = self.cache.trade_tick(self.config.instrument_id)
        limit_price = self.position_avg_px if last_trade is None else last_trade.price
        self.sell_position_at_price(self.instrument.make_price(limit_price * 0.8))

    def _reconcile(self, event: TimeEvent = None):
        if self._trader_helper is None:
            return
        position = self._trader_helper.get_position_obj()
        if self._trader_helper.flatten_if_short_with_retry(position):
            # Need to get position again after if flattening occurred
            position = self._trader_helper.get_position_obj()

        position_at_broker = int(position.qty)
        if position_at_broker < 0:
            self._raise_msg = f"Position still negative after attempting to flatten. Cache Position: {self.position_qty}, Alpaca Position: {position_at_broker}."
            return

        # CHECK POSITION DISCREPANCY BETWEEN LOCAL AND BROKER
        if self.position_qty != position_at_broker:
            now_ns = self.clock.timestamp_ns()
            if self._position_discrepancy_start_ns is None:
                self._position_discrepancy_start_ns = now_ns
            elif (now_ns - self._position_discrepancy_start_ns) / 1e9 > self.position_discrepancy_allow_secs:
                self._log.error(
                    f"Position discrepancy detected. Cache Position: {self.position_qty}, Alpaca Position: {position_at_broker}"
                )
                for open_order in self.open_orders:
                    self.log.warning(f"Strategy OpenOrder {open_order.order}")
                for order in set(self.cache.orders_open() + self.cache.orders_inflight()):
                    self.log.warning(f"Cache order {order}")
                self._raise_msg = (
                    f"Position discrepancy has existed for more than {self.position_discrepancy_allow_secs}. Raising."
                )
                return
        else:
            self._position_discrepancy_start_ns = None

        # COMPARE OPEN ORDERS TO CACHED ORDERS
        self_open_orders = set(open_order.order for open_order in self.open_orders)
        cache_open_orders = set(self.cache.orders_open() + self.cache.orders_inflight())
        cache_open_orders = set(order for order in cache_open_orders if order.status != OrderStatus.PENDING_CANCEL)
        if (len(self_open_orders) + len(cache_open_orders)) > 0:
            for order in cache_open_orders - self_open_orders:
                self.log.error(
                    f"Cache open order, venue_id {order.venue_order_id} not found in self.open_orders.\nOrder: {order}"
                )
                for order in cache_open_orders:
                    self.log.warning(f"Cache order {order}")
                for open_order in self_open_orders:
                    self.log.warning(f"OpenOrder {open_order.order}")

            for order in self_open_orders - cache_open_orders:
                self.log.warning(f"OpenOrder {order} not found in cache.")
                try:
                    # Try to retrieve order and see if status is closed. If so, remove from self.open_orders
                    cache_order = self.cache.order(order.client_order_id)
                    if cache_order.status in CLOSED_STATUS_LIST:
                        self.log.info(f"Removing open order {order} from self.open_orders because it is closed.")
                        self._remove_open_order(order)
                except Exception as e:
                    # check if self_order was just recently opened
                    print()

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

        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        # TICK DATA
        # Register indicators and request historical trade ticks if needed
        max_tick_lookback = 0
        for metric in self.tick_metrics_to_save:
            self.register_indicator_for_trade_ticks(self.config.instrument_id, metric.obj)
            max_tick_lookback = max(max_tick_lookback, metric.tick_lookback)

        if max_tick_lookback > 0:
            # Set a long lookback to ensure we get at least `max_tick_lookback` ticks back
            trade_tick_start = self.clock.utc_now() - timedelta(days=2)
            self.request_trade_ticks(self.config.instrument_id, start=trade_tick_start, limit=max_tick_lookback)

        self.subscribe_trade_ticks(self.config.instrument_id)

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

            # Get and save order events
            all_orders = self.cache.orders(strategy_id=self.id)
            all_events = []
            for order in all_orders:
                for event in order.events:
                    all_events.append(
                        {
                            "id": str(order.client_order_id),
                            "venue_id": str(order.venue_order_id),
                            "side": str(order.side),
                            "quantity": float(order.quantity),
                            "filled_qty": float(order.filled_qty),
                            "price": float(order.price) if hasattr(order, "price") else None,
                            "avg_px": float(order.avg_px) if order.avg_px else None,
                            "event": str(event.__class__.__name__),
                            "ts_init": event.ts_init,
                            "ts_event": event.ts_event,
                        }
                    )
            self._artifacts_io.save_orders_events(all_events)

    def on_instrument(self, instrument: Instrument) -> None:
        pass

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        pass

    def on_order_book(self, order_book: OrderBook) -> None:
        pass

    def on_quote_tick(self, tick: QuoteTick) -> None:
        pass

    def on_bar(self, bar: Bar) -> None:
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
