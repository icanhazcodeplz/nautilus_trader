#!/usr/bin/env python3

from decimal import Decimal

import pandas as pd

from examples.backtest_helpers import VENUE, SYMBOL, get_order_book_delta, get_trades
from nautilus_trader.backtest.engine import BacktestEngine, CacheConfig
from nautilus_trader.backtest.engine import BacktestEngineConfig

from nautilus_trader.analysis.statistics.trade_counts import Winners, Losers, Scratches
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
)
engine.portfolio.analyzer.register_statistic(Winners())
engine.portfolio.analyzer.register_statistic(Losers())
engine.portfolio.analyzer.register_statistic(Scratches())

test_instrument = TestInstrumentProvider.equity(symbol=SYMBOL, venue=VENUE)
engine.add_instrument(test_instrument)


# loader = DatabentoDataLoader()
# # filenames = [
# #     "tsla-dbeq-basic-trades-2024-01.dbn.zst",
# #     "tsla-dbeq-basic-trades-2024-02.dbn.zst",
# #     "tsla-dbeq-basic-trades-2024-03.dbn.zst",
# # ]
# #
# # for filename in filenames:
# #     trades = loader.from_dbn_file(
# #         path=TEST_DATA_DIR / "databento" / "temp" / filename,
# #         instrument_id=TSLA_NYSE.id,
# #     )
# #     engine.add_data(trades)
#
# data = loader.from_dbn_file(path=TEST_DATA_DIR / "databento" / "PAPL_20250723_mbo.dbn.zst",
#                             instrument_id=momo_instrument.id, include_trades=True)
# engine.add_data(data)
# BACKTESTING_CATALOG.write_data(data)


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

with pd.option_context(
    "display.max_rows",
    100,
    "display.max_columns",
    None,
    "display.width",
    300
):
    # print(engine.trader.generate_account_report(NYSE))
    # print(engine.trader.generate_order_fills_report())
    # print(engine.trader.generate_positions_report())
    pass

# For repeated backtest runs make sure to reset the engine
engine.reset()
engine.dispose()


