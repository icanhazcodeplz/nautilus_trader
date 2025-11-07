import json
from abc import abstractmethod
from datetime import timedelta

import pandas as pd
from pandas import Timestamp

from custom.app_utils.viz import write_to_ticks_and_metrics_txt_file, write_to_signals_file
from custom.utils import run_artifacts_subdir
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.core.message import Event
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import OrderSide, ContingencyType
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.model.orders.list import OrderList
from nautilus_trader.trading.strategy import Strategy


class BaseStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier: int
    stop_loss: float
    record_op_speed: bool  # Record operation speed in the "on_trade_tick" method
    allow_trades: bool = True


class BaseStrategy(Strategy):
    save_artifacts: bool = False

    def __init__(self, config: BaseStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument = None  # Initialized in on_start
        self.metrics_to_save = []

        self.stop_price = None
        self._last_buy_dt: Timestamp = pd.Timestamp("1990", tz="UTC")
        self._tick_data_dicts = {}
        self._tick_init_dt_adjusted = 0

        self._buy_signals_count = 0
        self.buy_sell_signals = []

        # Used to track order modifications to avoid sending duplicate modify orders when the cache is slow
        self._last_order_modify_dict = {}

    @property
    def position_qty(self):
        return int(self.portfolio.net_position(self.config.instrument_id))

    @property
    def position_avg_px(self):
        positions_open = self.cache.positions_open(instrument_id=self.config.instrument_id)
        if len(positions_open) == 0:
            raise RuntimeError("No position open")
        if len(positions_open) == 1:
            position = positions_open[0]
            position_average = position.avg_px_open
            return position_average
        else:
            raise RuntimeError("Multiple positions open")

    def submitted_or_open_orders(self, side=OrderSide.NO_ORDER_SIDE):
        return set(self.cache.orders_inflight(side=side) + self.cache.orders_open(side=side))

    def _modify_order(self, order, quantity, price):
        last_mod = self._last_order_modify_dict.get(order.client_order_id, None)
        mod_vals = (quantity, price)
        if last_mod is not None and last_mod == mod_vals:
            self.log.debug(f"Not modifying order {order.client_order_id} because modify request has already been sent.")
            return
        self._last_order_modify_dict[order.client_order_id] = mod_vals
        self.modify_order(order, quantity=quantity, price=price)

    def sell_position_at_price(self, new_limit_price):
        remaining_qty_to_sell = self.position_qty
        open_orders = self.submitted_or_open_orders(side=OrderSide.SELL)
        for order in open_orders:
            order_qty = order.quantity
            remaining_qty_to_sell -= order_qty
            if order.price != new_limit_price:
                self._modify_order(order, quantity=order_qty, price=new_limit_price)
        if remaining_qty_to_sell > 0:
            self.sell(quantity=remaining_qty_to_sell, limit_price=new_limit_price, tag="s")

    def stop_out_if_needed(self, tick: TradeTick):
        if self.position_qty == 0:
            self.stop_price = None
            return

        if self.position_qty > 0 and self.stop_price is None:
            self.stop_price = tick.price - self.config.stop_loss
            self.log.info(f"Setting stop price to {self.stop_price}")

        if self.stop_price is not None and tick.price <= self.stop_price:
            # TODO: HARDCODED to set stop price to 0.01 below current price
            new_limit_price = self.instrument.make_price(tick.price - 0.01)

            # FIXME: this is not a great solution. The fills for selling are more accurate during backtesting
            # if you use a single order, but during live running it is less buggy to modify existing orders because
            # trying to cancel existing orders runs async.
            self.sell_position_at_price(new_limit_price)
            # FIXME: cancelling all orders sometimes also cancels the subsequent sell order because of the async calls
            # self.cancel_all_orders(self.config.instrument_id)
            # self.sell(quantity=self.position_qty, limit_price=new_limit_price, tag="s")

            # Adjust stop price so we don't send repeat orders
            self.stop_price = tick.price

    @property
    def max_position_allowed(self):
        return self.config.max_position_multiplier * self.config.trade_size

    def _max_buy_qty_allowed(self):
        open_orders = self.submitted_or_open_orders(side=OrderSide.BUY)
        buy_qty_open_orders = sum(order.quantity for order in open_orders)
        return self.max_position_allowed - buy_qty_open_orders - self.position_qty

    def _max_sell_qty_allowed(self):
        open_orders = self.submitted_or_open_orders(side=OrderSide.SELL)
        sell_qty_open_orders = sum(order.quantity for order in open_orders)
        return self.position_qty - sell_qty_open_orders

    def log_buy_signal(self, tick, tag=None):
        self._buy_signals_count += 1
        if tag is None:
            tag = f"{self._buy_signals_count}"
        self.buy_sell_signals.append(
            dict(side="buy", time=self._tick_init_dt_adjusted, price=float(tick.price), tag=tag, win=None, win_delay=None)
        )

    def on_trade_tick(self, tick: TradeTick) -> None:
        if self._tick_init_dt_adjusted >= tick.ts_init:
            self._tick_init_dt_adjusted += 1
        else:
            self._tick_init_dt_adjusted = tick.ts_init

        tick_data = {"price": float(tick.price), "size": int(tick.size)}
        if self.config.record_op_speed:
            tick_data = {
                **tick_data,
                "ts_event": tick.ts_event,
                "ts_recv": tick.ts_init,
                "ts_clock": self.clock.utc_now(),
                "ts_now": pd.Timestamp.utcnow(),
            }

        #  Actual operations of this method
        # self.stop_out_if_needed(tick)
        self._on_trade_tick(tick)

        # Record if needed
        if self.config.record_op_speed:
            tick_data["ts_now_after"] = pd.Timestamp.utcnow()

        # Track buy-sell signals
        for signal in self.buy_sell_signals:
            if signal["win"] is None or signal["win_delay"] is None:
                if signal["side"] == "buy":
                    win = tick.price >= (signal["price"] + self.config.stop_loss)
                    loss = tick.price <= (signal["price"] - self.config.stop_loss)
                    if signal["win"] is None and (win or loss):
                        signal["win"] = win
                        signal["win_time"] = self._tick_init_dt_adjusted
                    if signal["win_delay"] is None and (win or loss) and ((self._tick_init_dt_adjusted - signal["time"]) > 60 * 1e6):
                        signal["win_delay"] = win
                        signal["win_delay_time"] = self._tick_init_dt_adjusted


        if self.save_artifacts:
            for metric in self.metrics_to_save:
                tick_data = {**tick_data, **metric.get_vals()}
            self._tick_data_dicts[self._tick_init_dt_adjusted] = tick_data

    def _submit_limit_order(self, side: OrderSide, quantity: int, limit_price: float, tag: str, cancel_after_secs=None):
        tags = [tag]
        if cancel_after_secs is not None:
            expire_time = self.clock.utc_now() + timedelta(seconds=cancel_after_secs)
            tags.append(expire_time)
        order: LimitOrder = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(quantity),
            price=self.instrument.make_price(limit_price),
            time_in_force=TimeInForce.DAY,
            expire_time=None,
            tags=tags,
        )
        if self.config.allow_trades:
            self.submit_order(order, position_id=None, client_id=None, params=None)

    def buy(self, quantity, limit_price, tag, cancel_after_secs=None) -> None:
        allowed_qty = min(quantity, self._max_buy_qty_allowed())
        if allowed_qty != quantity:
            self.log.info(
                f"Buy quantity reduced from {quantity} to {allowed_qty} to avoid exceeding max position of {self.max_position_allowed}."
            )
        if allowed_qty > 0:
            self._submit_limit_order(OrderSide.BUY, allowed_qty, limit_price, tag, cancel_after_secs)
            self._last_buy_dt = self.clock.utc_now()

    def buy_bracket(self, quantity, limit_price, stop_loss, take_profit, tag, cancel_after_secs=None) -> None:
        allowed_qty = min(quantity, self._max_buy_qty_allowed())
        if allowed_qty != quantity:
            self.log.info(
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
                contingency_type=ContingencyType.OCO,
                entry_order_type=OrderType.LIMIT,
                entry_price=self.instrument.make_price(limit_price),
                time_in_force=TimeInForce.DAY,
                entry_tags=entry_tags,
                tp_tags=[f"{tag}t"],
                sl_tags=[f"{tag}s"],
                tp_price=tp_price,
                tp_time_in_force=TimeInForce.DAY,
                sl_order_type=OrderType.STOP_LIMIT,
                sl_time_in_force=TimeInForce.DAY,
                sl_trigger_price=sl_trigger_price,
                # FIXME: hardcoded to 0.02 below stop_loss_price
                sl_price=self.instrument.make_price(limit_price - stop_loss - 0.02)
            )

            if self.config.allow_trades:
                self.submit_order_list(order_list)

            self._last_buy_dt = self.clock.utc_now()


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
        if order.is_buy:
            new_stop_price = order.last_px - self.config.stop_loss
            if self.stop_price is not None:
                if new_stop_price > self.stop_price:
                    self.log.info(f"Changing stop price from {self.stop_price} to {new_stop_price}")
                    self.stop_price = new_stop_price
            else:
                self.log.info(f"Setting stop price to {new_stop_price}")
                self.stop_price = new_stop_price
        self._on_order_filled(order)

        # Clean up order modify dict to reduce memory usage
        self._last_order_modify_dict.pop(order.client_order_id, None)

    def _cancel_orders_past_timeout(self, event: TimeEvent):
        # Cancel open orders if they have reached their expiration time
        open_orders = self.submitted_or_open_orders()
        for order in open_orders:
            if len(order.tags) > 1:
                expire_time = order.tags[1]  # FIXME: hardcoded to look at second item
                if self.clock.utc_now() > expire_time:
                    self.cancel_order(order)

    def on_start(self) -> None:
        self.clock.set_timer(
            name="cancel_orders_timer",
            interval=timedelta(seconds=0.25),
            callback=self._cancel_orders_past_timeout,
        )

        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        # Subscribe to live data
        self.subscribe_trade_ticks(self.config.instrument_id)
        # self.subscribe_quote_ticks(self.config.instrument_id)
        # self.subscribe_order_book_depth(self.config.instrument_id, book_type=BookType.L1_MBP)
        # self.subscribe_order_book_deltas(self.config.instrument_id, depth=20)  # For debugging
        # self.subscribe_order_book_at_interval(self.config.instrument_id, depth=20)  # For debugging

    def close_position_limit_order(self):
        last_trade = self.cache.trade_tick(self.config.instrument_id)
        limit_price = self.position_avg_px if last_trade is None else last_trade.price
        self.sell_position_at_price(self.instrument.make_price(limit_price * 0.8))

    def on_stop(self) -> None:
        if self.position_qty > 0:
            # Cancel BUY orders, but use "close_position_limit_order" to modify sell orders
            self.cancel_all_orders(self.config.instrument_id, order_side=OrderSide.BUY)
            self.close_position_limit_order()
        else:
            self.cancel_all_orders(self.config.instrument_id)

        # Unsubscribe from data
        self.unsubscribe_trade_ticks(self.config.instrument_id)

        # Record metrics and trade ticks if present
        if len(self._tick_data_dicts) > 0:
            write_to_ticks_and_metrics_txt_file(self._tick_data_dicts)

        if len(self.buy_sell_signals) > 0:
            write_to_signals_file(list(reversed(self.buy_sell_signals)))

    @abstractmethod
    def _on_trade_tick(self, tick: TradeTick) -> None:
        pass

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

    def on_order_event(self, order) -> None:
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

    def on_dispose(self) -> None:
        if self.config.record_op_speed:
            # Get all orders for this strategy
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

            with open(run_artifacts_subdir("orders_events.json"), "w") as f:
                as_json = json.dumps(all_events)
                f.write(as_json)


"""
  async def _periodic_task(self):
      while True:
          await asyncio.sleep(60)  # Wait 60 seconds
          # Do your periodic operation here
          self.log.info("Running periodic task")

  def on_start(self):
      # Start the periodic task
      self.create_task(self._periodic_task())

  2. Clock Timers (Recommended for Trading Logic)

  Use the built-in clock timer system:

  def on_start(self):
      # Set a timer that fires every 60 seconds
      self.clock.set_timer(
          name="my_periodic_timer",
          interval=timedelta(seconds=60),
          callback=self._on_timer_event,
      )

  def _on_timer_event(self, event: TimeEvent):
      # Called every 60 seconds
      self.log.info("Timer fired!")
      # Do your periodic operation here

  3. Time Alerts (One-time events)

  For one-time future events:

  def on_start(self):
      # Fire once at a specific time
      alert_time = self.clock.utc_now() + timedelta(minutes=5)
      self.clock.set_time_alert(
          name="my_alert",
          alert_time=alert_time,
          callback=self._on_alert,
      )

  def _on_alert(self, event: TimeEvent):
      self.log.info("Alert triggered!")

"""
