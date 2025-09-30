#!/usr/bin/env python3

import pandas as pd

from custom.catalog_options import CATALOG_OPTIONS
from custom.utils.load_catalog_data import VENUE, get_catalog_data
from custom.nt_extensions.limit_fill_model import LimitFillModel
from custom.utils.orders_to_trades import orders_to_trades
from custom.strategies.momo import MomoConfig, Momo
from custom.app_utils.viz import create_and_save_markers
from custom.strategies.random import RandomConfig, Random
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.analysis.statistics.expectancy import Expectancy
from nautilus_trader.analysis.statistics.long_ratio import LongRatio
from nautilus_trader.analysis.statistics.loser_min import MinLoser
from nautilus_trader.analysis.statistics.profit_factor import ProfitFactor
from nautilus_trader.analysis.statistics.returns_avg import ReturnsAverage
from nautilus_trader.analysis.statistics.returns_avg_loss import ReturnsAverageLoss
from nautilus_trader.analysis.statistics.returns_avg_win import ReturnsAverageWin
from nautilus_trader.analysis.statistics.returns_volatility import ReturnsVolatility
from nautilus_trader.analysis.statistics.risk_return_ratio import RiskReturnRatio
from nautilus_trader.analysis.statistics.sharpe_ratio import SharpeRatio
from nautilus_trader.analysis.statistics.sortino_ratio import SortinoRatio
from nautilus_trader.analysis.statistics.trade_avg import AvgTrade
from nautilus_trader.analysis.statistics.trade_avg_scaled import AvgTradeScaled
from nautilus_trader.analysis.statistics.trade_counts import Winners, Losers
from nautilus_trader.analysis.statistics.win_loss_ratio import WinLossRatio
from nautilus_trader.analysis.statistics.winner_min import MinWinner
from nautilus_trader.backtest.models import LatencyModel
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType, QuoteTick, TradeTick
from nautilus_trader.model.enums import AccountType, BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider


def run_single_backtest(dataset_name, strategy_name, params, return_engine=False, log_level="ERROR"):
    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("M-1"),
        logging=LoggingConfig(log_level=log_level, log_level_file=None, log_directory="logs", log_file_name=f"{log_level}.log"),
        cache=CacheConfig(tick_capacity=10_000, bar_capacity=1000)
    ))

    engine.add_venue(
        venue=Venue(VENUE),
        # book_type=BookType.L3_MBO,
        book_type=BookType.L1_MBP,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(100000.0, USD)],
        fill_model=LimitFillModel(),
        # trade_execution=True,
        latency_model=LatencyModel(0.120 * 1e9)  # 120 milliseconds
    )

    dataset_params = CATALOG_OPTIONS[dataset_name]
    symbol = dataset_params["symbol"]

    test_instrument = TestInstrumentProvider.equity(symbol=symbol, venue=VENUE)
    engine.add_instrument(test_instrument)

    engine.add_data(get_catalog_data(symbol, dataset_params["start"], dataset_params["end"], data_cls=TradeTick))
    engine.add_data(get_catalog_data(symbol, dataset_params["start"], dataset_params["end"], data_cls=QuoteTick))

    avg_trade_scaled = AvgTradeScaled(per_x_bought=100)

    for stat_class in [ReturnsVolatility, SharpeRatio, SortinoRatio, LongRatio, ProfitFactor, RiskReturnRatio, ReturnsAverage, ReturnsAverageLoss, ReturnsAverageWin, MinWinner, MinLoser, Expectancy]:
        engine.portfolio.analyzer.deregister_statistic(stat_class())

    for stat_class in [Winners, Losers, WinLossRatio, AvgTrade]:  # Scratches
        engine.portfolio.analyzer.register_statistic(stat_class())

    engine.portfolio.analyzer.register_statistic(avg_trade_scaled)

    if strategy_name == "random":
        config = RandomConfig(
            instrument_id=test_instrument.id,
            **params
        )
        strategy = Random(config=config)
    elif strategy_name == "momo":
        config = MomoConfig(
            instrument_id=test_instrument.id,
            **params
        )
        strategy = Momo(config=config)


    engine.add_strategy(strategy=strategy)
    engine.run()
    if return_engine:
        return engine
    return avg_trade_scaled.calculate_from_positions(engine.portfolio.analyzer._positions)

if __name__ == "__main__":
    log_level = "INFO"
    # log_level = "DEBUG"
    # log_level = "ERROR"

    dataset_name = "papl"
    strategy_name = "momo"

    params = dict(
        trade_size = 100,
        max_position_multiplier = 1,
        stop_loss = 0.30,
        take_profit = 0.30,
        take_ratio = 0.5,
        vwap_window = 50,
        vwap_buy_threshold = 0.25,
        trailing_stop = True,
    )

    engine = run_single_backtest(dataset_name, strategy_name, params, return_engine=True, log_level=log_level)

    order_fills_report = engine.trader.generate_order_fills_report()
    orders_report = engine.trader.generate_orders_report()
    fills_report = engine.trader.generate_fills_report()
    positions_report = engine.trader.generate_positions_report()
    positions = positions_report[['peak_qty', 'ts_opened', 'ts_closed','avg_px_open','avg_px_close','realized_pnl']]

    orders_report = orders_report[orders_report["filled_qty"].astype(int) > 0]
    orders = orders_report[['side', 'quantity', 'filled_qty', 'price', 'avg_px', 'tags', 'ts_init', 'ts_last']].copy()
    orders['ts_init'] = pd.to_datetime(orders['ts_init'], unit='ns')
    orders['ts_last'] = pd.to_datetime(orders['ts_last'], unit='ns')

    trades, sell_legs = orders_to_trades(orders_report)
    create_and_save_markers(trades, sell_legs)

    with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
        # print(t_orders)
        # print(engine.trader.generate_account_report(NYSE))
        # print(order_fills_report)
        pass

    trades, sell_legs = orders_to_trades(orders_report)
    create_and_save_markers(trades, sell_legs)

    print()
    # engine.reset()
    # engine.dispose()

