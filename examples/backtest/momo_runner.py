#!/usr/bin/env python3

from decimal import Decimal
from time import monotonic_ns

import pandas as pd

from nautilus_trader import TEST_DATA_DIR
from nautilus_trader.adapters.databento import DatabentoDataLoader
from nautilus_trader.backtest.engine import BacktestEngine, CacheConfig
from nautilus_trader.backtest.engine import BacktestEngineConfig
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

from typing import Any
from nautilus_trader.analysis.statistic import PortfolioStatistic


class Winners(PortfolioStatistic):

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0.0
        winners = [x for x in realized_pnls if x > 0.0]
        return len(winners)

class Losers(PortfolioStatistic):

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0.0
        losers = [x for x in realized_pnls if x < 0.0]
        return len(losers)

class Scratches(PortfolioStatistic):

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0.0
        match = [x for x in realized_pnls if x == 0.0]
        return len(match)

if __name__ == "__main__":
    level = "INFO"
    # level = "DEBUG"
    config = BacktestEngineConfig(
        trader_id=TraderId("M-1"),
        logging=LoggingConfig(log_level=level, log_level_file=None, log_directory="logs", log_file_name=f"{level}.log"),
        cache=CacheConfig(tick_capacity=10_000, bar_capacity=1000)
    )

    engine = BacktestEngine(config=config)
    venue = "SIM"
    engine.add_venue(
        venue=Venue(venue),
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

    momo_instrument = TestInstrumentProvider.equity(symbol="PAPL", venue=venue)
    engine.add_instrument(momo_instrument)

    loader = DatabentoDataLoader()

    # filenames = [
    #     "tsla-dbeq-basic-trades-2024-01.dbn.zst",
    #     "tsla-dbeq-basic-trades-2024-02.dbn.zst",
    #     "tsla-dbeq-basic-trades-2024-03.dbn.zst",
    # ]
    #
    # for filename in filenames:
    #     trades = loader.from_dbn_file(
    #         path=TEST_DATA_DIR / "databento" / "temp" / filename,
    #         instrument_id=TSLA_NYSE.id,
    #     )
    #     engine.add_data(trades)
    engine.add_data(loader.from_dbn_file(path=TEST_DATA_DIR / "databento" / "PAPL_20250723_mbo.dbn.zst", instrument_id=momo_instrument.id, include_trades=True))

    config = MomoConfig(
        instrument_id=momo_instrument.id,
        bar_type=BarType.from_str(f"{momo_instrument.id}-1-MINUTE-LAST-INTERNAL"),
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
