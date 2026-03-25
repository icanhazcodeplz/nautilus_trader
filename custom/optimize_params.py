#!/usr/bin/env python3
import os
import sys

sys.path.append(os.getcwd())
from datetime import datetime
from time import sleep

import numpy as np
import pandas as pd
import optuna
import logging
import shutil


from custom.backtest_utils.prepare_top_gainers import (
    read_top_gainers_candidates,
    make_top_gainers_candidates_txt_and_prepare_catalog,
)
from custom.utils.process_manager import ProcessManager
from custom.backtest_runner import run_single_backtest_from_top_gainers_candidate

OPTUNA_DB_DIR = "optuna_dbs"
optuna.logging.get_logger("optuna").addHandler(logging.StreamHandler(sys.stdout))
# optuna.logging.set_verbosity(optuna.logging.WARNING)

user_attrs_map = {
    "total_bought": "Total Bought",
    "average_buy_price": "Average Buy Price",
    "max_loser": "Max Loser",
    "pnl": "PnL (total)",
}


def database_str_for(study_name, candidate):
    return f"sqlite:///{OPTUNA_DB_DIR}/{study_name}/{candidate}.db"


TOP_GAINER_CANDIDATES = read_top_gainers_candidates()
_skip_list = [
    "2026-03-17_PMAX",
    "2026-03-17_ORIS",
    "2026-03-17_SMX",
    "2026-03-17_ARTL",
    "2026-03-18_TPET",
    "2026-03-18_LNKS",
    "2026-03-18_CGTL",
    "2026-03-18_EVTV",
    "2026-03-19_ACXP",
    "2026-03-19_DLTH",
]
TOP_GAINER_CANDIDATES = [c for c in TOP_GAINER_CANDIDATES if c not in _skip_list]


def linspace_int(low, high, step):
    return list(range(low, high + 1, step))


def linspace_float(low, high, step):
    return [round(val, 4) for val in np.arange(low, high + step * 0.90, step)]


def optimize(trial):
    params = dict(
        # --- STRATEGY PARAMS ---------------------
        trade_size=100,
        max_position_multiplier=1,
        stop_pct=0.15,
        take_profit=None,
        vwap_window=160,
        variance_window=300,
        lower_scalar_multiplier=2.4,
        upper_scalar_multiplier=0.6,
        outer_band_multiplier=3.0,
        trailing_buy_order=False,
        only_buy_if_macd_positive=True,
        simple_take=False,
        trailing_take=True,
        num_sell_tiers=3,
        allow_trades=True,
        pressure_window=10,
        random_buy=False,
        random_seed=None,
        # --- TOP GAINERS PARAMS ----------------
        price_min=1.0,
        price_max=20.0,
        vol_30min_min=100_000,
        perc_gain_min=30,
        rank_max=5,
    )
    for param_name, space in trial.study.sampler._search_space.items():
        param_type = {type(val) for val in space}
        assert len(param_type) == 1
        param_type = param_type.pop()
        if param_type is int:
            params[param_name] = trial.suggest_int(param_name, low=min(space), high=max(space), step=1)
        elif param_type in [float, np.float64]:
            params[param_name] = trial.suggest_float(param_name, low=min(space), high=max(space), step=0.01)
        elif param_type is bool:
            params[param_name] = trial.suggest_categorical(param_name, space)
        else:
            raise ValueError(f"Unknown param type {param_type}")

    dataset = trial.study.study_name
    performance_stats = run_single_backtest_from_top_gainers_candidate(
        dataset, "momo", params, artifacts_location=None, log_level="ERROR"
    )
    try:
        for col_name, p_stat_name in user_attrs_map.items():
            trial.set_user_attr(col_name, performance_stats[p_stat_name])

        value = performance_stats["Pnl Per100"]
        return value
    except Exception as e:
        print(f"Error running trial {trial.number}. Returning -1.0. Exception:\n {e}")
        return -1.0


def target(candidate, study_name, sampler):
    db = database_str_for(study_name, candidate)
    os.makedirs(os.path.dirname(db.replace("sqlite:///", "")), exist_ok=True)
    study = optuna.create_study(
        study_name=candidate,
        storage=db,
        direction="maximize",
        sampler=sampler,
        pruner=optuna.pruners.NopPruner(),
        load_if_exists=True,
    )
    n_trials = sampler._n_min_trials
    # NOTE: setting n_jobs above 1 creates more threads, not processes. It is not any faster than n_jobs=1.
    study.optimize(optimize, n_trials=n_trials, timeout=60 * n_trials, n_jobs=1)


if __name__ == "__main__":
    delete_existing = False
    run_trials = False
    MAKE_RESULTS_PICKLE = False
    date_strs = [
        "2026-03-09",
        "2026-03-10",
        "2026-03-11",
        "2026-03-12",
        "2026-03-13",
        "2026-03-16",
        "2026-03-17",
        "2026-03-18",
        "2026-03-19",
        "2026-03-20",
    ]

    study_name = "test"
    search_space = dict(
        random_seed=[1, 2, 3, 4],
        # max_position_multiplier=linspace_int(low=1, high=1, step=1),
        # stop_pct=linspace_float(low=0.05, high=0.15, step=0.05),
        # take_profit=linspace_float(low=0.25, high=0.35, step=0.10),
        # variance_window=linspace_int(low=250, high=350, step=50),
        # vwap_window=linspace_int(low=120, high=200, step=40),
        upper_scalar_multiplier=linspace_float(low=0.6, high=1.8, step=0.4),
        lower_scalar_multiplier=linspace_float(low=1.6, high=2.4, step=0.4),
        outer_band_multiplier=linspace_float(low=1.0, high=3.0, step=1.0),
        pressure_window=linspace_int(low=5, high=15, step=10),
        # num_sell_tiers=linspace_int(1,4,step=1),
        # random_buy=[True, False],
        rank_max=linspace_int(low=3, high=3, step=1),
        vol_30min_min=linspace_int(low=100_000, high=100_000, step=1),
        perc_gain_min=linspace_int(low=30, high=30, step=1),
        price_min=linspace_float(low=0.8, high=0.8, step=0.50),
        price_max=linspace_float(low=20.0, high=20.0, step=0.50),
    )

    candidates_txt_was_edited = make_top_gainers_candidates_txt_and_prepare_catalog(
        date_strs,
        rank_max=max(search_space["rank_max"]),
        vol_30min_min=min(search_space["vol_30min_min"]),
        perc_gain_min=min(search_space["perc_gain_min"]),
        price_min=min(search_space["price_min"]),
        price_max=max(search_space["price_max"]),
    )
    if candidates_txt_was_edited:
        raise RuntimeError("top_gainers_candidates was edited, need to rerun")

    sampler = optuna.samplers.GridSampler(search_space)
    trials_per_candidate = sampler._n_min_trials
    total_trials = trials_per_candidate * len(TOP_GAINER_CANDIDATES)

    if delete_existing:
        shutil.rmtree(f"{OPTUNA_DB_DIR}/{study_name}", ignore_errors=True)

    os.makedirs(f"{OPTUNA_DB_DIR}/{study_name}", exist_ok=True)
    print(
        f"Trials per candidate: {trials_per_candidate} | Candidates: {len(TOP_GAINER_CANDIDATES)} | Total: {total_trials}\n"
    )

    if run_trials:
        start = datetime.now()

        # RUN SINGLE PROCESS
        # target(TOP_GAINER_CANDIDATES[0], study_name, sampler)

        n_processes = 9
        pm = ProcessManager()
        for i, candidate in enumerate(TOP_GAINER_CANDIDATES):
            while pm.num_running_processes >= n_processes:
                pm.remove_completed()
                sleep(1)
            print(f"Starting {candidate} ({i + 1}/{len(TOP_GAINER_CANDIDATES)})")
            pm.add_and_start(name=candidate, target=target, args=(candidate, study_name, sampler))
            pm.remove_completed()

        pm.block(sleep_secs=1)
        print(f"TOTAL RUN TIME: {datetime.now() - start}")

    param_names = list(search_space.keys())
    rename_map = {f"params_{p}": p for p in param_names}
    rename_map = {**rename_map, **{f"user_attrs_{u}": u for u in user_attrs_map.keys()}}

    if MAKE_RESULTS_PICKLE:
        all_dfs = []
        for candidate in TOP_GAINER_CANDIDATES:
            try:
                study = optuna.load_study(study_name=candidate, storage=database_str_for(study_name, candidate))
                cdf = study.trials_dataframe().round(3)
                cdf["dataset"] = candidate
                all_dfs.append(cdf)
            except Exception:
                print(f"No study found for {candidate}")

        df = pd.concat(all_dfs)
        df = df[df["state"] == "COMPLETE"]
        df = df.rename(columns=rename_map)
        df = df[["value", "dataset", *param_names, *user_attrs_map.keys()]]
        df = df[~df.duplicated()]
        df = df[df["value"] != -1.0]
        df.to_pickle(f"{OPTUNA_DB_DIR}/{study_name}.pkl")
    else:
        df = pd.read_pickle(f"{OPTUNA_DB_DIR}/{study_name}.pkl")

    # Remove columns with only a single unique value
    single_value_cols = [col for col in df.columns if df[col].nunique() <= 1 and col != "dataset"]
    df = df.drop(columns=single_value_cols)
    param_names = [p for p in param_names if p not in single_value_cols]

    print("=== Avg Value per Dataset ===")
    agg_cols = ["value", *user_attrs_map.keys()]
    print(df.groupby("dataset")[agg_cols].mean().round(2).sort_values("value").to_string())
    print()

    bins = [0, 1.0, 2.0, 3.0, 6.0, float("inf")]
    bin_labels = ["0-1", "1-2", "2-3", "3-6", "6+"]
    df["price_bin"] = pd.cut(df["average_buy_price"], bins=bins, labels=bin_labels)

    print("=== Avg Value by Param & Price Bin ===")
    for col in param_names:
        table = df.groupby([col, "price_bin"], observed=False)["value"].mean().unstack("price_bin").round(2)
        counts = df.groupby("price_bin").size()
        avg_bought = df.groupby("price_bin")["total_bought"].mean().round(1)
        print(f"\n--- {col} ---")
        print(f"{'trials':>30s}  {counts.to_dict()}")
        print(f"{'avg_total_bought':>30s}  {avg_bought.to_dict()}")
        print(table)
    print()

    df = df.drop(columns=["price_bin"])
    df_orig = df.copy()
    for dataset in TOP_GAINER_CANDIDATES:
        print(dataset)
        df = df_orig[df_orig["dataset"] == dataset]
        df = df.drop(columns=["dataset"])
        if "random_seed" in df.columns:
            param_names = [col for col in param_names if col != "random_seed"]
            gb = df.groupby(param_names).mean().drop(columns="random_seed").round(3)
            df = gb.reset_index()

        top_ratio = 0.20
        top_count = int(len(df) * top_ratio)
        tops_df = df.sort_values("value", ascending=False).head(top_count)
        if not tops_df.empty:
            print(
                f"Top {top_ratio}: low={tops_df['value'].min()} avg={round(tops_df['value'].mean(), 2)} max={tops_df['value'].max()}"
            )
        for col in param_names:
            if col == "dataset":
                continue
            gp = df.groupby(col)["value"].mean().round(2)
            # max_[col] = _try_round2(gp.sort_values().index[-1])
            if not gp.empty:
                print(gp.to_frame().T)
                print()
        print()

    """
    mysql.server start

    mysql.server stop

    mysql.server restart

    mysql -u root -e "CREATE DATABASE IF NOT EXISTS optuna"

    mysql -u root -p optuna
    """
