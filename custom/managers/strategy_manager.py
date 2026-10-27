from typing import TYPE_CHECKING

from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.trading.controller import Controller


if TYPE_CHECKING:
    from nautilus_trader.trading.trader import Trader

from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import ExitStrategy
from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper


class _StrategyManagerConfig(ActorConfig, frozen=True):
    price_min = 1.0
    price_max = 20.0
    vol_30min_requirement = 100_000
    must_be_in_top = 5
    min_perc_gain = 40

class StrategyManagerBacktestConfig(_StrategyManagerConfig, frozen=True):
    day_str: str

class StrategyManagerConfig(_StrategyManagerConfig, frozen=True):
    pass

class StrategyManagerBacktest(Controller):

    def __init__(self, trader: Trader, config: StrategyManagerBacktestConfig) -> None:
        super().__init__(trader=trader, config=config)
        self._strategies: dict[str, MomoStrategy] = {}
        print()



class StrategyManager(Controller):
    """
    Monitors 5-minute bars across symbols and dynamically
    creates / starts / stops MomoStrategy instances.
    """

    def __init__(self, trader: Trader, config: StrategyManagerConfig) -> None:
        super().__init__(trader=trader, config=config)
        self._strategies: dict[str, MomoStrategy] = {}

    # -- lifecycle ------------------------------------------------------------

    def on_start(self) -> None:
        for symbol in self.config.symbols:
            bar_type = BarType.from_str(f"{symbol}.ALPACA-5-MINUTE-LAST-EXTERNAL")
            bar_type = BarType.from_str(f"{symbol}.ALPACA-1-MINUTE-LAST-EXTERNAL")
            self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        symbol = bar.bar_type.instrument_id.symbol.value

        if self._should_start_strategy(symbol, bar):
            if symbol not in self._strategies:
                self._launch_strategy(symbol)
        elif self._should_stop_strategy(symbol, bar):
            if symbol in self._strategies:
                self._teardown_strategy(symbol)

    def on_stop(self) -> None:
        for symbol in list(self._strategies):
            self._teardown_strategy(symbol)

    # -- strategy management --------------------------------------------------

    def _launch_strategy(self, symbol: str) -> None:
        config = MomoStrategyConfig(
            trade_size=10,
            max_position_multiplier=10,
            stop_loss=100,  # bps, ~$0.05 on a $5 stock
            take_profit=None,
            exit_strategy=ExitStrategy.SIT_ON_VWAP_BANDS,
            num_exit_tiers=3,
            entry_strategy=EntryStrategy.RANDOM,
            start_trading_at="09:30",
            allow_trades=True,
            print_update_every_secs=5,
        )
        strategy = MomoStrategy(config=config, symbol=symbol)
        helper = AlpacaTraderHelper()
        strategy.initialize(artifacts_io=None, trader_helper=helper)

        self.create_strategy(strategy, start=True)
        self._strategies[symbol] = strategy
        self.log.info(f"Launched strategy for {symbol}")

    def _teardown_strategy(self, symbol: str) -> None:
        strategy = self._strategies.pop(symbol, None)
        if strategy is None:
            return
        self.remove_strategy(strategy)
        self.log.info(f"Removed strategy for {symbol}")

    # -- decision logic (override these) --------------------------------------

    def _should_start_strategy(self, symbol: str, bar: Bar) -> bool:
        """Return True if a strategy should be launched for this symbol."""
        # TODO: implement your 5-minute screening logic
        return True

    def _should_stop_strategy(self, symbol: str, bar: Bar) -> bool:
        """Return True if the running strategy should be stopped."""
        # TODO: implement your exit/stop logic
        return False
