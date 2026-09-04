#!/usr/bin/env python3
import traceback
from pathlib import Path

import pandas as pd

from custom.artifacts import ArtifactsIO, BACKTEST_RUNS_PATH
from custom.backtest_utils.backtest_run_utils import (
    add_default_venue,
    analyze_backtest,
    build_backtest_engine,
    register_custom_statistics,
    save_backtest_order_updates,
)
from custom.backtest_utils.load_catalog_data import load_catalog_data_to_engine
from nautilus_trader.trading.config import ImportableControllerConfig


def run_single_news_backtest(
    symbol,
    start_str,
    end_str,
    news_file,
    strategy_overrides=None,
    artifacts_location=None,
    log_level="ERROR",
    analyze=False,
):
    start_time = pd.Timestamp.now()
    strategy_overrides = dict(strategy_overrides or {})
    random_seed = strategy_overrides.pop("random_seed", None)

    controller = ImportableControllerConfig(
        controller_path="custom.managers.news_manager:NewsManagerBacktest",
        config_path="custom.managers.news_manager:NewsManagerBacktestConfig",
        config={
            "news_file": news_file,
            "strategy_overrides": strategy_overrides,
        },
    )

    engine = build_backtest_engine(artifacts_location, log_level, controller=controller)
    add_default_venue(engine, random_seed)
    test_instrument, engine = load_catalog_data_to_engine(engine, symbol, start_str, end_str, data_venue="ALPACA")
    register_custom_statistics(engine)

    try:
        engine.run()
    except Exception as e:
        print(f"Exception during news backtest: {e}")
        traceback.print_exc()
    finally:
        orders_report = engine.trader.generate_orders_report()
        performance_stats = {
            **engine.portfolio.analyzer.get_performance_stats_pnls(),
            **engine.portfolio.analyzer.get_performance_stats_returns(),
            **engine.portfolio.analyzer.get_performance_stats_general(),
        }
        if artifacts_location is not None:
            artifacts_io = ArtifactsIO(artifacts_location)
            artifacts_io.save_orders_report(orders_report)
            artifacts_io.save_performance_metrics(performance_stats)
            artifacts_io.save_config(
                {
                    "news_file": news_file,
                    "strategy_overrides": strategy_overrides,
                    "instrument_id": f"{symbol}.ALPACA",  # TODO: make dynaamic
                }
            )

    save_backtest_order_updates(engine, artifacts_location)

    if artifacts_location is not None and analyze:
        analyze_backtest()
        print(f"\nTotal Runtime {pd.Timestamp.now() - start_time}")

    engine.dispose()
    return performance_stats


if __name__ == "__main__":
    log_level = "INFO"

    strategy_overrides = dict(random_seed=1)

    symbol = "MOBX"
    start_str = "2026-05-14 04:00-04:00"
    end_str = "2026-05-14 8:06-04:00"
    news_file = str(Path(__file__).parent / "fake_news_events.jsonl")

    run_single_news_backtest(
        symbol,
        start_str,
        end_str,
        news_file,
        strategy_overrides=strategy_overrides,
        artifacts_location=BACKTEST_RUNS_PATH,
        log_level=log_level,
        analyze=True,
    )
