#!/usr/bin/env python3
import random

import pandas as pd

from custom.artifacts import LiveArtifactsIO, BacktestArtifactsIO, backtest_dir, BACKTEST_RUNS_TEMP_FOLDER
from custom.backtest_scripts.backtest_config import DATA_DELAY_SECS
from custom.backtest_scripts.backtest_config import DATA_DELAY_WINDOWS
from custom.backtest_utils.backtest_run_utils import add_default_venue, run_strategy
from custom.backtest_utils.backtest_run_utils import analyze_backtest
from custom.backtest_utils.backtest_run_utils import build_backtest_engine
from custom.backtest_utils.backtest_run_utils import save_backtest_order_updates
from custom.backtest_utils.load_catalog_data import et_to_utc_str, catalog_days_available
from custom.backtest_utils.load_catalog_data import load_catalog_data_to_engine
from custom.backtest_utils.load_catalog_data import to_et_str
from custom.backtest_utils.prepare_top_gainers import get_allow_trading_times_for_candidate
from custom.backtest_utils.prepare_top_gainers import parse_candidate_str
from custom.strategies.momo import MomoStrategy, DirectionStrategy, DirectionThreshold, EntryStrategy, ExitStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.run_utils import register_custom_statistics


def run_single_backtest(
    symbol:str,
    start_str_est:str,
    end_str_est:str,
    config: MomoStrategyConfig,
    random_seed=42,
    save_artifacts:bool=True,
    save_artifacts_to_temp:bool=False,
    allow_trading_times=None,
    log_level:str="ERROR",
    analyze:bool=False,
    bypass_logging:bool=False,
    raise_errors:bool=False,
):
    start_time = pd.Timestamp.now()
    random.seed(random_seed)

    if save_artifacts:
        if save_artifacts_to_temp:
            _bt_dir = BACKTEST_RUNS_TEMP_FOLDER
        else:
            _bt_dir = backtest_dir(symbol, config, start_str_est, end_str_est)
        artifacts_io = BacktestArtifactsIO(directory=_bt_dir)
        artifacts_io.save_run_info(symbol)
        artifacts_location = artifacts_io.directory
    else:
        artifacts_io = None
        artifacts_location = None

    engine = build_backtest_engine(artifacts_location, log_level, bypass_logging=bypass_logging)
    add_default_venue(engine, random_seed)
    # The catalog reads a string without an offset as UTC
    test_instrument, engine = load_catalog_data_to_engine(
        engine, symbol, et_to_utc_str(start_str_est), et_to_utc_str(end_str_est), data_venue="ALPACA"
    )
    register_custom_statistics(engine.portfolio.analyzer)

    strategy = MomoStrategy(config=config, symbol=symbol)

    if allow_trading_times is not None:
        strategy.allow_trading_times = allow_trading_times
        strategy.set_trading_enabled(False)
    strategy.internal_bars = True
    strategy.set_data_delay(default_secs=DATA_DELAY_SECS, windows=DATA_DELAY_WINDOWS)

    performance_stats = run_strategy(
        strategy,
        engine,
        run_config={**config.dict(), "symbol": symbol},
        artifacts_io=artifacts_io,
        raise_errors=raise_errors,
    )

    if save_artifacts:
        save_backtest_order_updates(engine, artifacts_io)
        if analyze:
            analyze_backtest(artifacts_io)
            print(f"Total Runtime {pd.Timestamp.now() - start_time}")

    engine.dispose()
    return performance_stats


def run_single_backtest_from_top_gainers_candidate(
    candidate_str, params, artifacts_location=None, log_level="ERROR", analyze=False
):
    rank_max = params.pop("rank_max")
    vol_30min_min = params.pop("vol_30min_min")
    perc_gain_min = params.pop("perc_gain_min")
    price_min = params.pop("price_min")
    price_max = params.pop("price_max")

    symbol, day_str = parse_candidate_str(candidate_str)
    allow_trading_times = get_allow_trading_times_for_candidate(
        symbol, day_str, rank_max, vol_30min_min, perc_gain_min, price_min, price_max
    )

    start = allow_trading_times[0] - pd.Timedelta(minutes=30)
    end = allow_trading_times[-1] + pd.Timedelta(minutes=20)

    # start = allow_trading_times[0] + pd.Timedelta(minutes=2)
    # end = allow_trading_times[0] + pd.Timedelta(minutes=5)
    start_str_est = to_et_str(start)
    end_str_est = to_et_str(end)

    return run_single_backtest(
        symbol,
        start_str_est,
        end_str_est,
        MomoStrategyConfig(**params),
        save_artifacts=artifacts_location,
        allow_trading_times=allow_trading_times,
        log_level=log_level,
        analyze=analyze,
    )


def replay_live_run(live_run_artifacts_dir, symbol, log_level="ERROR", random_seed=1):
    """Run a backtest with the same parameters as one symbol of a live run."""
    artifacts_io = LiveArtifactsIO(live_run_artifacts_dir, symbol)
    config = MomoStrategyConfig(**artifacts_io.load_config())

    # Derive dataset name from run date and symbol
    ticks = artifacts_io.load_ticks_and_metrics_file()
    # Widened to whole seconds, which is all the Eastern string carries, so the window still covers every tick
    start_str_est = to_et_str(pd.Timestamp(min(ticks.keys()), unit="ns", tz="UTC").floor("s"))
    end_str_est = to_et_str(pd.Timestamp(max(ticks.keys()), unit="ns", tz="UTC").ceil("s"))

    return run_single_backtest(
        symbol,
        start_str_est,
        end_str_est,
        config,
        random_seed=random_seed,
        save_artifacts=True,
        log_level=log_level,
        analyze=True,
    )


if __name__ == "__main__":
    # live_run_artifacts_dir = data_subdir("runs", "20261002_092645")
    # replay_live_run(live_run_artifacts_dir, log_level="INFO")

    log_level = "INFO"
    # log_level = "DEBUG"
    # log_level = "ERROR"
    # log_level = "WARNING"

    config = MomoStrategyConfig(
        allow_trades=True,
        max_position_multiplier=1,
        trade_size=100,
        stop_loss=100,  # bps, ~$2.00 on AMZN
        take_profit=22,  # bps, ~$0.60 on AMZN
        band_multiplier=1,
        band_window=1000,
        band_variance_window=2000,
        rolling_vwap_window=1000,
        direction_strategy=DirectionStrategy.REVERSION,
        direction_threshold=DirectionThreshold.ROLLING_VWAP,
        exit_strategy=ExitStrategy.SIMPLE_TAKE,
        num_exit_tiers=1,
        entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
        entry_distance=22,  # bps
        start_trading_at="09:29:58",
        stop_entries_after="09:31",
        force_exit_at="09:40",
        entry_exclusion_band=None,
    )

    # candidate_str = "2026-03-19_LNKS"
    #
    # run_single_backtest_from_top_gainers_candidate(
    #     candidate_str, strategy_name, params, artifacts_location=BACKTEST_RUNS_PATH, log_level="ERROR", analyze=True
    # )

    symbol = "AAPL"

    day_strs = ["2026-1-21"]
    day_strs = catalog_days_available(symbol)
    for day_str in day_strs:
        start_str_est = f"{day_str} 09:29"
        end_str_est = f"{day_str} 09:41"
        run_single_backtest(
            symbol,
            start_str_est,
            end_str_est,
            config,
            save_artifacts=True,
            save_artifacts_to_temp=False,
            allow_trading_times=None,
            log_level=log_level,
            analyze=True,
            bypass_logging=True,
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
