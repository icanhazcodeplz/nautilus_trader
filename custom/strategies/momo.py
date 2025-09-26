from nautilus_trader.indicators.volume import deque

from custom.utils.load_catalog_data import BACKTESTING_CATALOG
from custom.strategies.base import BaseStrategy
from nautilus_trader.config import PositiveInt
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import TradeTick

from nautilus_trader.model.identifiers import InstrumentId

from nautilus_trader.model.instruments import Instrument


class MomoConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    trade_size: int
    max_position_multiplier:int
    stop_loss:float
    take_profit:float

    bar_type: BarType
    fast_ema_period: PositiveInt = 10
    slow_ema_period: PositiveInt = 20
    request_historical_bars: bool = False

def initialize_deque_if_needed(dq:deque, value):
    if len(dq) == 0:
        for i in range(dq.maxlen):
            dq.append(value)
    return dq

class Momo(BaseStrategy):
    def __init__(self, config: MomoConfig) -> None:
        PyCondition.is_true(
            config.fast_ema_period < config.slow_ema_period,
            "{config.fast_ema_period=} must be less than {config.slow_ema_period=}",
        )
        super().__init__(config)

        self.instrument: Instrument = None  # Initialized in on_start

        # Create the indicators for the strategy
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)
        self.trigger_buy = False
        self.trigger_sell = False

        self.size_dq = deque(maxlen=10)
        self.price_dq = deque(maxlen=10)
        self.recent_big_drop = False
        self.stop_price = None
        self.take_price = None
        self.trades_since_order = 0

    def _on_trade_tick(self, tick: TradeTick) -> None:
        # NOTE: Need to be subscribed to order book deltas to get best bid/ask prices
        # ob = self.cache.order_book(self.config.instrument_id)
        # best_bid = ob.best_bid_price()
        # best_ask = ob.best_ask_price()

        initialize_deque_if_needed(self.price_dq, tick.price)
        initialize_deque_if_needed(self.size_dq, tick.size)

        price_2ago = self.price_dq[-2]
        price_1ago = self.price_dq[-1]
        price = tick.price
        if price - price_2ago <= -0.20 and price_2ago >= price_1ago >= price:
            self.recent_big_drop = True

        self.price_dq.append(tick.price)
        self.size_dq.append(tick.size)
        if self.recent_big_drop and price > price_1ago:
            self.recent_big_drop = False
            self.buy(self.config.trade_size, price, cancel_after_secs=10, tag="b")

    def on_order_filled(self, order) -> None:
        if order.is_buy:
            take_price = order.last_px + self.config.take_profit
            self.sell(quantity=order.last_qty, limit_price=take_price, tag="t")
            self.stop_price = self.position_avg_px - self.config.stop_loss

    def on_start(self) -> None:
        """
        Actions to be performed on strategy start.
        """
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        # Register the indicators for updating
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)

        # Get historical data
        # if self.config.request_historical_bars:
        #     self.request_bars(
        #         self.config.bar_type,
        #         start=self._clock.utc_now() - pd.Timedelta(days=1),
        #     )
        # self.request_quote_ticks(self.config.instrument_id)
        # self.request_trade_ticks(self.config.instrument_id)

        # Subscribe to live data
        self.subscribe_bars(self.config.bar_type)
        self.subscribe_trade_ticks(self.config.instrument_id)


    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)

        # Unsubscribe from data
        self.unsubscribe_bars(self.config.bar_type)
        # self.unsubscribe_quote_ticks(self.config.instrument_id)
        self.unsubscribe_trade_ticks(self.config.instrument_id)
        self.unsubscribe_order_book_deltas(self.config.instrument_id)
        # self.unsubscribe_order_book_at_interval(self.config.instrument_id)

        # TODO: Only save if not already existing
        bars_list = self.cache.bars(self.config.bar_type)
        bars_list.sort(key=lambda x: x.ts_init)
        BACKTESTING_CATALOG.write_data(bars_list)


    def on_bar(self, bar: Bar) -> None:
        pass
        # if not self.indicators_initialized():
        #     self.log.info(
        #         f"Waiting for indicators to warm up [{self.cache.bar_count(self.config.bar_type)}]",
        #         color=LogColor.BLUE,
        #     )
        #     return  # Wait for indicators to warm up...
        #
        # if bar.is_single_price():
        #     # Implies no market information for this bar
        #     return
        #
        # # BUY LOGIC
        # if self.fast_ema.value >= self.slow_ema.value:
        #     if self.portfolio.is_flat(self.config.instrument_id):
        #         self.trigger_buy = True
        # # SELL LOGIC
        # elif self.fast_ema.value < self.slow_ema.value:
        #     if self.portfolio.is_net_long(self.config.instrument_id):
        #         self.trigger_sell = True


    def on_reset(self) -> None:
        self.fast_ema.reset()
        self.slow_ema.reset()

