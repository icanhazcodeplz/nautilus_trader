#!/usr/bin/env python3

from decimal import Decimal

import pandas as pd

from examples.backtest_helpers import VENUE, SYMBOL, get_order_book_delta, get_trades
from nautilus_trader.backtest.engine import BacktestEngine, CacheConfig
from nautilus_trader.backtest.engine import BacktestEngineConfig

from examples.orders_to_trades import orders_to_trades
from examples.viz import create_and_save_markers
from nautilus_trader.analysis.statistics.trade_counts import Winners, Losers, Scratches
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.examples.strategies.momo import MomoConfig, Momo
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

level = "INFO"
# level = "DEBUG"

engine = BacktestEngine(config=BacktestEngineConfig(
    trader_id=TraderId("M-1"),
    logging=LoggingConfig(log_level=level, log_level_file=None, log_directory="logs", log_file_name=f"{level}.log"),
    cache=CacheConfig(tick_capacity=10_000, bar_capacity=1000)
))

engine.add_venue(
    venue=Venue(VENUE),
    book_type=BookType.L3_MBO,
    oms_type=OmsType.NETTING,
    account_type=AccountType.MARGIN,
    base_currency=USD,
    starting_balances=[Money(100.0, USD)],
    trade_execution=False,
    latency_model=LatencyModel(0.060 * 1e9)  # 60 milliseconds
)
engine.portfolio.analyzer.register_statistic(Winners())
engine.portfolio.analyzer.register_statistic(Losers())
engine.portfolio.analyzer.register_statistic(Scratches())

test_instrument = TestInstrumentProvider.equity(symbol=SYMBOL, venue=VENUE)
engine.add_instrument(test_instrument)


engine.add_data(get_order_book_delta())
engine.add_data(get_trades())

config = MomoConfig(
    instrument_id=test_instrument.id,
    bar_type=BarType.from_str(f"{test_instrument.id}-1-MINUTE-LAST-INTERNAL"),
    trade_size=Decimal(10),
    fast_ema_period=10,
    slow_ema_period=20,
)

strategy = Momo(config=config)
engine.add_strategy(strategy=strategy)

engine.run()

# order_fills_report = engine.trader.generate_order_fills_report()
orders_report = engine.trader.generate_orders_report()
# fills_report = engine.trader.generate_fills_report()
with pd.option_context(
    "display.max_rows",
    100,
    "display.max_columns",
    None,
    "display.width",
    300
):
    # print(engine.trader.generate_account_report(NYSE))
    # print(order_fills_report)
    # print(engine.trader.generate_positions_report())
    pass

# For repeated backtest runs make sure to reset the engine
engine.reset()
engine.dispose()

trades, sell_legs = orders_to_trades(orders_report)
create_and_save_markers(trades, sell_legs)
print()

