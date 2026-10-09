#!/usr/bin/env python3
"""
Grid-search MomoStrategy params over every day in SAMPLE_DAYS.

One optuna trial = one param set backtested on every day. The trial's value is Pnl Per100 over
the whole set: sum of each day's realized PnL divided by the sum of each day's shares entered,
times 100. That is the same definition `custom.statistics.trade_avg_scaled.PnlPer100` applies
within a day, so a single day and the full set are scored on one scale.

Each day's backtest runs in its own one-shot spawned process. Nautilus initializes the Rust
logger once per interpreter and panics on the second `engine.run()` in the same one ("attempted
to set a logger after the logging system was already initialized").
"""
import os
import sys


sys.path.append(os.getcwd())
import json
import logging
import multiprocessing
from datetime import datetime

import numpy as np
import optuna
import pandas as pd

from custom.backtest_scripts.strategy_backtest_runner import run_single_backtest
from custom.backtest_utils.load_catalog_data import CATALOG_TIME_STR_FMT
from custom.backtest_utils.load_catalog_data import catalog_days_available
from custom.backtest_utils.prepare_top_gainers import parse_candidate_str
from custom.strategies.base import ET_TZ
from custom.strategies.momo import DirectionStrategy
from custom.strategies.momo import DirectionThreshold
from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import ExitStrategy


OPTUNA_DB_DIR = "optuna_dbs"

# Backtest window applied to every day, as New York wall-clock "HH:MM:SS". Each day's UTC offset
# (EST or EDT) is worked out in `et_time_str`.
START_TIME = "09:29:00"
END_TIME = "09:41:00"

# Days backtested concurrently within one trial.
N_DAY_WORKERS = 7

optuna.logging.get_logger("optuna").addHandler(logging.StreamHandler(sys.stdout))
# optuna.logging.set_verbosity(optuna.logging.WARNING)

# Per-trial aggregates stored as optuna user attrs, in the order the results table shows them.
AGG_ATTRS = ["pnl", "total_entered", "days_traded"]


def database_str_for(study_name):
    return f"sqlite:///{OPTUNA_DB_DIR}/{study_name}.db"


def pickle_path_for(study_name):
    return f"{OPTUNA_DB_DIR}/{study_name}.pkl"


SYMBOL = "GOOG"
SAMPLE_DAYS = [f"{day}_{SYMBOL}" for day in catalog_days_available(SYMBOL)]


def et_time_str(day_str, time_str):
    """Return "YYYY-MM-DD HH:MM:SS+zzzz" for New York wall-clock `time_str` on `day_str`, with that day's offset."""
    return pd.Timestamp(f"{day_str} {time_str}").tz_localize(ET_TZ).strftime(CATALOG_TIME_STR_FMT)


def run_day(args):
    """
    Backtest one day and return `(candidate, stats)`. Top-level so a spawned worker can import it.

    The analyzer omits any statistic that returns `None`, so a day with no positions has no
    "Total Entered" or "Average Entry Price" key at all. Default `pnl` and `entered` to zero so the
    day drops out of the trial's sums.
    """
    candidate, params = args
    symbol, day_str = parse_candidate_str(candidate)
    stats = run_single_backtest(
        symbol,
        et_time_str(day_str, START_TIME),
        et_time_str(day_str, END_TIME),
        "momo",
        params,
        artifacts_location=None,
        allow_trading_times=None,
        log_level="ERROR",
    )
    return candidate, {
        "pnl": float(stats.get("PnL (total)") or 0.0),
        "entered": int(stats.get("Total Entered") or 0),
        "avg_entry_price": stats.get("Average Entry Price"),
        "max_loser": stats.get("Max Loser"),
    }


def linspace_int(low, high, step):
    return list(range(low, high + 1, step))


def linspace_float(low, high, step):
    return [round(val, 4) for val in np.arange(low, high + step * 0.90, step)]


def optimize(trial):
    # Mirrors the momo params at the end of `strategy_backtest_runner.py`; `search_space` overrides.
    params = dict(
        allow_trades=True,
        max_position_multiplier=1,
        trade_size=100,
        stop_loss=3.0,
        take_profit=0.5,
        upper_scalar_multiplier=0.5,
        rolling_vwap_window=3000,
        vwap_window=150,
        variance_window=300,
        outer_band_multiplier=2.5,
        pressure_window=25,
        only_buy_if_macd_positive=False,
        direction_strategy=DirectionStrategy.REVERSION,
        direction_threshold=DirectionThreshold.ROLLING_VWAP,
        exit_strategy=ExitStrategy.SIT_ON_ROLLING_VWAP,
        num_exit_tiers=2,
        entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
        entry_distance=0.5,
        start_trading_at="09:29:58",
        stop_entries_after="09:31",
        force_exit_at="09:40",
        entry_exclusion_band=None,
        random_seed=1,
    )
    for param_name, space in trial.study.sampler._search_space.items():
        param_type = {type(val) for val in space}
        assert len(param_type) == 1, f"{param_name}: grid values must share one type, got {param_type}"
        param_type = param_type.pop()
        if param_type is bool:
            params[param_name] = trial.suggest_categorical(param_name, space)
        elif param_type is int:
            params[param_name] = trial.suggest_int(param_name, low=min(space), high=max(space), step=1)
        elif param_type in [float, np.float64]:
            params[param_name] = trial.suggest_float(param_name, low=min(space), high=max(space), step=0.01)
        elif issubclass(param_type, str):
            # Plain strings ("09:31") and StrEnums (DirectionStrategy) alike.
            params[param_name] = trial.suggest_categorical(param_name, space)
        else:
            raise ValueError(f"Unknown param type {param_type}")

    # `maxtasksperchild=1` gives every backtest a fresh interpreter (see module docstring), but it
    # counts *tasks*, and `map` batches items into chunks of several days per task by default.
    # `chunksize=1` makes one day one task. A day that raises propagates out of `map` and fails
    # the trial, so a bug is not hidden as a score.
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=N_DAY_WORKERS, maxtasksperchild=1) as pool:
        per_day = dict(pool.map(run_day, [(day, params) for day in SAMPLE_DAYS], chunksize=1))

    total_pnl = sum(d["pnl"] for d in per_day.values())
    total_entered = sum(d["entered"] for d in per_day.values())
    if total_entered == 0:
        raise optuna.TrialPruned("No entry fills on any day, Pnl Per100 is undefined")

    traded = [d for d in per_day.values() if d["entered"] > 0]
    trial.set_user_attr("pnl", round(total_pnl, 2))
    trial.set_user_attr("total_entered", total_entered)
    trial.set_user_attr("days_traded", len(traded))
    # trial.set_user_attr(
    #     "avg_entry_price", round(sum(d["avg_entry_price"] * d["entered"] for d in traded) / total_entered, 4)
    # )
    # A string, so the whole breakdown survives `trials_dataframe()` as one column.
    breakdown = {day: {"pnl": d["pnl"], "entered": d["entered"]} for day, d in per_day.items()}
    trial.set_user_attr("per_day", json.dumps(breakdown))

    return round(total_pnl / total_entered * 100, 3)


def target(study_name, sampler):
    os.makedirs(OPTUNA_DB_DIR, exist_ok=True)
    study = optuna.create_study(
        study_name=study_name,
        storage=database_str_for(study_name),
        direction="maximize",
        sampler=sampler,
        pruner=optuna.pruners.NopPruner(),
        load_if_exists=True,
    )
    # NOTE: setting n_jobs above 1 creates more threads, not processes. It is not any faster than n_jobs=1.
    study.optimize(optimize, n_trials=sampler._n_min_trials, n_jobs=1)


def load_results(study_name, param_names):
    study = optuna.load_study(study_name=study_name, storage=database_str_for(study_name))
    df = study.trials_dataframe()
    df = df[df["state"] == "COMPLETE"]
    rename_map = {f"params_{p}": p for p in param_names}
    rename_map |= {f"user_attrs_{u}": u for u in [*AGG_ATTRS, "per_day"]}
    df = df.rename(columns=rename_map)
    return df[["number", "value", *param_names, *AGG_ATTRS, "per_day"]].reset_index(drop=True)


def per_day_table(per_day_json):
    """Expand one trial's `per_day` attr into a day-by-day frame with a totals row."""
    rows = [{"day": day, **vals} for day, vals in json.loads(per_day_json).items()]
    df = pd.DataFrame(rows)
    totals = pd.DataFrame([{"day": "TOTAL", "pnl": df["pnl"].sum(), "entered": df["entered"].sum()}])
    df = pd.concat([df, totals], ignore_index=True)
    df["pnl_per100"] = (df["pnl"] / df["entered"].replace(0, np.nan) * 100).round(3)
    return df


if __name__ == "__main__":
    delete_existing = True
    run_trials = True
    MAKE_RESULTS_PICKLE = True

    study_name = "test"
    # Every value in a list must share one type, and `None` cannot be a grid value: a sweep that
    # wants to "turn off" `entry_exclusion_band` or `take_profit` needs a numeric sentinel instead.
    search_space = dict(
        # direction_strategy=[DirectionStrategy.REVERSION, DirectionStrategy.MOMENTUM],
        entry_distance=linspace_float(low=0.8, high=1.0, step=0.1),
        stop_entries_after=["09:31"],
        # direction_threshold=[DirectionThreshold.OPEN, DirectionThreshold.ROLLING_VWAP],
        take_profit=linspace_float(low=0.6, high=0.8, step=0.05),
        stop_loss=linspace_float(low=2.4, high=3.0, step=0.20),
        # num_exit_tiers=linspace_int(1, 3, step=1),
        # rolling_vwap_window=linspace_int(low=1000, high=3000, step=1000),
    )

    sampler = optuna.samplers.GridSampler(search_space)
    n_trials = sampler._n_min_trials

    if delete_existing:
        for path in (database_str_for(study_name).replace("sqlite:///", ""), pickle_path_for(study_name)):
            if os.path.exists(path):
                os.remove(path)

    print(f"Trials: {n_trials} | Days per trial: {len(SAMPLE_DAYS)} | Backtests: {n_trials * len(SAMPLE_DAYS)}\n")

    if run_trials:
        start = datetime.now()
        target(study_name, sampler)
        print(f"TOTAL RUN TIME: {datetime.now() - start}")

    param_names = list(search_space.keys())

    if MAKE_RESULTS_PICKLE:
        df = load_results(study_name, param_names)
        df.to_pickle(pickle_path_for(study_name))
    else:
        df = pd.read_pickle(pickle_path_for(study_name))

    # A swept param that only ever took one value has nothing to compare.
    param_names = [p for p in param_names if df[p].nunique() > 1]

    print("=== Trials, best first ===")
    ranked = df.drop(columns=["per_day"]).sort_values("value", ascending=False)
    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_rows", 500):
        print(ranked.to_string(index=False))
    print()

    print("=== Value by param ===")
    for col in param_names:
        print(f"\n--- {col} ---")
        print(df.groupby(col)["value"].agg(["mean", "max", "count"]).round(2).to_string())
    print()

    if len(param_names) > 1:
        print("=== Value by full param combination ===")
        combo = df.groupby(param_names)["value"].mean().round(2).sort_values(ascending=False)
        print(combo.to_string())
        print()

    best = ranked.iloc[0]
    print(f"=== Best trial #{int(best['number'])}: value={best['value']} ===")
    print({p: best[p] for p in param_names})
    per_day_df = per_day_table(df.loc[df["number"] == best["number"], "per_day"].iloc[0])
    print(per_day_df.to_string(index=False))
