from abc import abstractmethod
from datetime import timedelta

from custom import ENV
from custom.app_utils.viz import write_to_metrics_txt_file
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.core.message import Event
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas, Bar
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import LimitOrder
from nautilus_trader.trading.strategy import Strategy


class BaseConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier:int
    stop_loss:float


class BaseStrategy(Strategy):
    def __init__(self, config: BaseConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument = None  # Initialized in on_start
        self.stop_price = None
        self.metrics_to_save = None
        self._metrics_values = []

    @property
    def position_qty(self):
        return int(self.portfolio.net_position(self.config.instrument_id))

    @property
    def position_avg_px(self):
        positions_open = self.cache.positions_open()
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

    def sell_position_at_price(self, new_limit_price):
        remaining_qty_to_sell = self.position_qty
        open_orders = self.submitted_or_open_orders(side=OrderSide.SELL)
        for order in open_orders:
            order_qty = order.quantity
            remaining_qty_to_sell -= order_qty
            if order.price != new_limit_price:
                self.modify_order(order, quantity=order_qty, price=new_limit_price)
        if remaining_qty_to_sell > 0:
            self.sell(quantity=remaining_qty_to_sell, limit_price=new_limit_price, tag="s")

    def stop_out_if_needed(self, tick: TradeTick):
        if self.position_qty == 0:
            self.stop_price = None
            return

        if self.stop_price is not None and tick.price <= self.stop_price:
            # TODO: HARDCODED to set stop price to 0.1 below current price
            new_limit_price = self.instrument.make_price(tick.price - 0.0)

            # FIXME: BRENT - this is not a great solution. The fills for selling are more accurate during backtesting
            # if you use a single order, but during live running it is less buggy to modify existing orders because
            # trying to cancel existing orders runs async.
            if ENV.LIVE:
                self.sell_position_at_price(new_limit_price)
            else:
                self.cancel_all_orders(self.config.instrument_id)
                self.sell(quantity=self.position_qty, limit_price=new_limit_price, tag="s")

    def _max_buy_qty_allowed(self):
        max_position_allowed = self.config.max_position_multiplier * self.config.trade_size
        open_orders = self.submitted_or_open_orders(side=OrderSide.BUY)
        buy_qty_open_orders = sum(order.quantity for order in open_orders)
        return max_position_allowed - buy_qty_open_orders - self.position_qty

    def _max_sell_qty_allowed(self):
        open_orders = self.submitted_or_open_orders(side=OrderSide.SELL)
        sell_qty_open_orders = sum(order.quantity for order in open_orders)
        return self.position_qty - sell_qty_open_orders

    def on_trade_tick(self, tick: TradeTick) -> None:
        self.stop_out_if_needed(tick)

        # Cancel open buy orders if they have reached their expiration time
        open_buys = self.submitted_or_open_orders(side=OrderSide.BUY)
        for buy_order in open_buys:
            expire_time = buy_order.tags[1]  # FIXME: BRENT - hardcoded to look at second item
            if self.clock.utc_now() > expire_time:
                self.cancel_order(buy_order)

        self._on_trade_tick(tick)
        if self.metrics_to_save is not None:
            metrics_vals = {name: round(item.value, 3) for name, item in self.metrics_to_save.items()}
            metrics_vals["time"] = tick.ts_event / 1e9
            self._metrics_values.append(metrics_vals)

    def buy(self, quantity, limit_price, tag, cancel_after_secs) -> None:
        allowed_qty = min(quantity, self._max_buy_qty_allowed())
        if allowed_qty > 0:
            expire_time = self.clock.utc_now() + timedelta(seconds=cancel_after_secs)
            order: LimitOrder = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(allowed_qty),
                price=self.instrument.make_price(limit_price),
                time_in_force=TimeInForce.DAY,
                expire_time=None,
                # emulation_trigger=TriggerType.LAST_PRICE,
                tags=[tag, expire_time]
            )
            self.submit_order(order)
        else:
            self.log.info(f"Not buying {quantity} @ {limit_price} because max position of reached")

    def sell(self, quantity, limit_price, tag) -> None:
        allowed_qty = min(quantity, self._max_sell_qty_allowed())
        if self.position_qty <= 0:
            raise Exception("Cannot sell when position is negative")

        if allowed_qty > 0:
            order: LimitOrder = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.instrument.make_qty(allowed_qty),
                price=self.instrument.make_price(limit_price),
                time_in_force=TimeInForce.GTC,
                # emulation_trigger=TriggerType.LAST_PRICE,
                tags=[tag]
            )
            self.submit_order(order)
        else:
            self.log.info(f"Not selling {quantity} @ {limit_price} because position is {self.position_qty}")

    def on_start(self) -> None:
        """
        Actions to be performed on strategy start.
        """
        raise NotImplementedError
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
        # self.unsubscribe_quote_ticks(self.config.instrument_id)
        # self.unsubscribe_order_book_deltas(self.config.instrument_id)
        # self.unsubscribe_order_book_at_interval(self.config.instrument_id)
        if len(self._metrics_values) > 0:
            write_to_metrics_txt_file(self._metrics_values)

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

    def on_order_filled(self, order) -> None:
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
        pass
