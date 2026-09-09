#!/usr/bin/env python3
import random

import pandas as pd

from custom.artifacts import BACKTEST_RUNS_PATH
from custom.artifacts import ArtifactsIO
from custom.backtest_utils.backtest_run_utils import add_default_venue
from custom.backtest_utils.backtest_run_utils import analyze_backtest
from custom.backtest_utils.backtest_run_utils import build_backtest_engine
from custom.backtest_utils.backtest_run_utils import register_custom_statistics
from custom.backtest_utils.backtest_run_utils import save_backtest_order_updates
from custom.backtest_utils.load_catalog_data import CATALOG_TIME_STR_FMT
from custom.backtest_utils.load_catalog_data import load_catalog_data_to_engine
from custom.backtest_utils.prepare_top_gainers import get_allow_buy_times_for_candidate
from custom.backtest_utils.prepare_top_gainers import parse_candidate_str
from custom.strategies.base import BaseStrategy
from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.strategies.open_fade import OpenFade
from custom.strategies.open_fade import OpenFadeConfig
from custom.strategies.random import Random
from custom.strategies.random import RandomConfig
from custom.utils.run_utils import run_strategy
from nautilus_trader.adapters.alpaca.utils import ns_to_iso_8601


def run_single_backtest(
    symbol,
    start_str,
    end_str,
    strategy_name,
    params,
    artifacts_location=None,
    allow_buy_times=None,
    log_level="ERROR",
    analyze=False,
):
    start_time = pd.Timestamp.now()
    params_copy = params.copy()
    random_seed = params_copy.pop("random_seed", None)
    random.seed(random_seed)

    engine = build_backtest_engine(artifacts_location, log_level)
    add_default_venue(engine, random_seed)
    test_instrument, engine = load_catalog_data_to_engine(engine, symbol, start_str, end_str, data_venue="ALPACA")
    register_custom_statistics(engine)

    if strategy_name == "random":
        config = RandomConfig(instrument_id=test_instrument.id, **params_copy)
        strategy = Random(config=config)
    elif strategy_name == "momo":
        config = MomoStrategyConfig(instrument_id=test_instrument.id, **params_copy)
        strategy = MomoStrategy(config=config)
    elif strategy_name == "open_fade":
        config = OpenFadeConfig(instrument_id=test_instrument.id, **params_copy)
        strategy = OpenFade(config=config)
    else:
        raise ValueError(f"Unknown strategy_name {strategy_name!r}")

    # `allow_buy_times` / `internal_bars` are BaseStrategy-only concepts.
    if isinstance(strategy, BaseStrategy):
        if allow_buy_times is not None:
            strategy.allow_buy_times = allow_buy_times
            strategy.set_allow_buys(False)
        strategy.internal_bars = True

    performance_stats = run_strategy(strategy, engine, artifacts_location, run_config=config.dict())

    save_backtest_order_updates(engine, artifacts_location)

    if artifacts_location is not None and analyze:
        analyze_backtest()
        print(f"\nTotal Runtime {pd.Timestamp.now() - start_time}")

    engine.dispose()
    return performance_stats


def run_single_backtest_from_top_gainers_candidate(
    candidate_str, strategy_name, params, artifacts_location=None, log_level="ERROR", analyze=False
):
    rank_max = params.pop("rank_max")
    vol_30min_min = params.pop("vol_30min_min")
    perc_gain_min = params.pop("perc_gain_min")
    price_min = params.pop("price_min")
    price_max = params.pop("price_max")

    symbol, day_str = parse_candidate_str(candidate_str)
    allow_buy_times = get_allow_buy_times_for_candidate(
        symbol, day_str, rank_max, vol_30min_min, perc_gain_min, price_min, price_max
    )

    start = allow_buy_times[0] - pd.Timedelta(minutes=30)
    end = allow_buy_times[-1] + pd.Timedelta(minutes=20)

    # start = allow_buy_times[0] + pd.Timedelta(minutes=2)
    # end = allow_buy_times[0] + pd.Timedelta(minutes=5)
    start_str = pd.Timestamp.strftime(start, CATALOG_TIME_STR_FMT)
    end_str = pd.Timestamp.strftime(end, CATALOG_TIME_STR_FMT)

    return run_single_backtest(
        symbol,
        start_str,
        end_str,
        strategy_name,
        params,
        allow_buy_times=allow_buy_times,
        artifacts_location=artifacts_location,
        log_level=log_level,
        analyze=analyze,
    )


def run_multiple_backtests(dataset_names, strategy_name, params, log_level="ERROR"):
    performance_stats = []
    for dataset_name in dataset_names:
        p_stats = run_single_backtest_from_top_gainers_candidate(
            dataset_name, strategy_name, params, artifacts_location=None, log_level=log_level
        )
        performance_stats.append({"name": dataset_name, **p_stats})
    stats_df = pd.DataFrame(performance_stats).round(3)
    return stats_df


def replay_live_run(live_run_artifacts_dir, log_level="ERROR"):
    """Run a backtest with the same parameters as a live run."""
    artifacts_io = ArtifactsIO(live_run_artifacts_dir)
    config = artifacts_io.load_config()

    # Derive dataset name from run date and symbol
    ticks = artifacts_io.load_ticks_and_metrics_file()
    start_str = ns_to_iso_8601(min(ticks.keys()))
    end_str = ns_to_iso_8601(max(ticks.keys()))

    symbol = config.pop("instrument_id").split(".")[0]

    return run_single_backtest(symbol, start_str, end_str, "momo", config, log_level=log_level, analyze=True)


if __name__ == "__main__":
    # live_run_artifacts_dir = data_subdir("runs", "20260313_144138")
    # replay_live_run(live_run_artifacts_dir)

    log_level = "INFO"
    # log_level = "DEBUG"
    # log_level = "ERROR"
    # log_level = "WARNING"

    strategy_name = "open_fade"

    if strategy_name == "momo":
        lstm_buy = False
        params = dict(
            allow_trades=True,
            max_position_multiplier=1,
            trade_size=10,
            stop_loss=0.2,
            take_profit=None,
            upper_scalar_multiplier=0.5,
            lower_scalar_multiplier=1.5,
            vwap_window=150,
            variance_window=300,
            outer_band_multiplier=2.5,
            pressure_window=25,
            simple_take=False,
            only_buy_if_macd_positive=False,
            trailing_take=True,
            num_sell_tiers=3,
            trailing_buy_order=False,
            random_buy=not lstm_buy,
            lstm_buy=lstm_buy,
            random_seed=1,
            # --- TOP GAINERS PARAMS ----------------
            # price_min=0.8,
            # price_max=20.0,
            # vol_30min_min=100_000,
            # perc_gain_min=30,
            # rank_max=5,
        )

    elif strategy_name == "open_fade":
        params = dict(
            trade_size=10,
            entry_offsets=(0.50,),
            stop_offset=0.5,
            flip_threshold=0.30,
            random_seed=1,
        )

    # candidate_str = "2026-03-19_LNKS"
    #
    # run_single_backtest_from_top_gainers_candidate(
    #     candidate_str, strategy_name, params, artifacts_location=BACKTEST_RUNS_PATH, log_level="ERROR", analyze=True
    # )

    symbol = "AMZN"
    day_str = "2026-08-11"
    start_str = day_str + " " + "09:20-04:00"
    end_str__ = day_str + " " + "09:38-04:00"
    run_single_backtest(
        symbol,
        start_str,
        end_str__,
        strategy_name,
        params,
        allow_buy_times=None,
        artifacts_location=BACKTEST_RUNS_PATH,
        log_level=log_level,
        analyze=True,
    )

    # datasets = ["0129_vivssm"]
    # all_stats = []
    # if len(datasets) > 1:
    #     for random_seed in [1]:
    #         params["random_seed"] = random_seed
    #         stats = run_multiple_backtests(datasets, strategy_name, params, log_level=log_level)
    #         all_stats.append(stats)
    #     stats = pd.concat(all_stats)
    #     with pd.option_context("display.max_rows", 100, "display.max_columns", None, "display.width", 300):
    #         print(stats)
    #         pass
    # else:
    #     symbol, start_str, end_str = extract_dataset_name_info(datasets[0])
    #     run_backtest_and_analyze(symbol, start_str, end_str, strategy_name, params, log_level=log_level)
