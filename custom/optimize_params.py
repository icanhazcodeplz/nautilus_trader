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

sys.path.append(os.getcwd())
from custom.utils.process_manager import ProcessManager
from custom.backtest_runner import run_single_backtest_from_dataset_name

DATABASE_STR = "sqlite:///optuna.db"
# mysql_optuna = "mysql://root@localhost/optuna"
optuna.logging.get_logger("optuna").addHandler(logging.StreamHandler(sys.stdout))

DATASET_NAMES = [
    "sgbx",
    "cnck",
    "radx",
    "bttc",
    "crbp",
    "migi",
    # "jzxn",
    # "mbrx",
    # "lhai",
    # "azi",
]
OPTIMIZE_BUY_SIGNALS = False
RANDOM_BUY = False
MIN_TRADES_THRESHOLD = 15


def linspace_int(low, high, step):
    return list(range(low, high + 1, step))


def linspace_float(low, high, step):
    return [round(val, 4) for val in np.arange(low, high + step * 0.90, step)]


def _try_round2(arg):
    try:
        return float(round(arg, 2))
    except:
        return arg


def _return_previous_trail_with_same_params(trial):
    for past_trial in trial.study.get_trials():
        if past_trial.state != 1:
            continue
        if all(_try_round2(past_trial.params[key]) == _try_round2(val) for key, val in trial.params.items()):
            print(f"Trial {past_trial.number} has the same params. Returning value {past_trial.value}")
            return past_trial.value
    return None


def optimize(trial):
    # max_position_multiplier=trial.suggest_int("max_position_multiplier", low=1, high=3, step=1),
    # stop_loss=trial.suggest_float("stop_loss", low=0.25, high=0.45, step=0.05),
    # take_profit=trial.suggest_float("take_profit", low=0.25, high=0.35, step=0.02),
    # vwap_window=trial.suggest_int("vwap_window", low=40, high=50, step=5),
    # vwap_buy_threshold=trial.suggest_float("vwap_buy_threshold", low=0.10, high=0.30, step=0.05),
    # trailing_stop=trial.suggest_categorical("trailing_stop", [True, False]),
    strategy_name = "momo"

    params = dict(
        trade_size=100,
        max_position_multiplier=1,
        stop_pct=0.05,
        take_profit=None,
        vwap_window=160,
        variance_window=300,
        lower_scalar_multiplier=1.5,
        upper_scalar_multiplier=1.2,
        outer_band_multiplier=3.0,
        trailing_buy_order=False,
        only_buy_if_macd_positive=True,
        simple_take=False,
        trailing_take=True,
        num_sell_tiers=3,
        allow_trades=not OPTIMIZE_BUY_SIGNALS,
        random_buy=RANDOM_BUY,
        random_seed=None,
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
        elif param_type is str and param_name == "dataset":
            dataset = trial.suggest_categorical(param_name, space)
        else:
            raise ValueError(f"Unknown param type {param_type}")

    if strategy_name != "random":
        previous_trail_value = _return_previous_trail_with_same_params(trial)
        if previous_trail_value is not None:
            return previous_trail_value

    performance_stats = run_single_backtest_from_dataset_name(dataset, strategy_name, params, artifacts_location=None, log_level="ERROR")
    try:
        if OPTIMIZE_BUY_SIGNALS:
            buy_signals = performance_stats["buy_signals"]
            wins = performance_stats["buy_signal_wins"]
            if performance_stats["buy_signals"] < MIN_TRADES_THRESHOLD and not RANDOM_BUY:
                return -1.0
            return round(wins / buy_signals, 3)
        else:
            trades = performance_stats["Num Trades"]
            value = performance_stats["Pnl Per100"]
            print(f"Trial {trial.number} had {trades} trades, value {value}")
            if trades < MIN_TRADES_THRESHOLD and not RANDOM_BUY:
                print(
                    f"Trial {trial.number} had {trades} trades, less than required {MIN_TRADES_THRESHOLD}. Returning -1.0"
                )
                return -1.0
            return value

            # total_pnl = performance_stats["PnL (total)"].sum()
            # total_bought = performance_stats[TotalBought().name].sum()
            # value = total_pnl / total_bought * 100
            # return value
    except Exception as e:
        print(f"Error running trial {trial.number}. Returning -1.0. Exception:\n {e}")
        return -1.0


def load_or_create_optuna_study(study_name, sampler):
    return optuna.create_study(
        study_name=study_name,
        storage=DATABASE_STR,
        direction="maximize",
        sampler=sampler,
        pruner=optuna.pruners.NopPruner(),
        load_if_exists=True,
    )


def target(study_name, sampler, n_trials):
    study = load_or_create_optuna_study(study_name, sampler=sampler)
    # NOTE: setting n_jobs above 1 creates more threads, not processes. It is not any faster than n_jobs=1.
    study.optimize(optimize, n_trials=n_trials, timeout=60 * n_trials, n_jobs=1)


if __name__ == "__main__":
    delete_existing = False
    run_trials = False
    load_random_buy_study = False

    if RANDOM_BUY:
        study_name = "random"
        search_space = dict(random_seed=[1, 2, 3, 4, 5, 6])
    else:
        study_name = "test"
        # study_name = "test_macd"
        # study_name = "test_macd_outer_band"
        # study_name = "sell_tiers"
        search_space = dict(
            random_seed=[1, 2, 3, 4],
            # max_position_multiplier=linspace_int(low=1, high=1, step=1),
            # stop_pct=linspace_float(low=0.05, high=0.15, step=0.05),
            # take_profit=linspace_float(low=0.25, high=0.35, step=0.10),
            # variance_window=linspace_int(low=280, high=320, step=20),
            vwap_window=linspace_int(low=120, high=200, step=40),
            upper_scalar_multiplier=linspace_float(low=0.6, high=2.0, step=0.4),
            lower_scalar_multiplier=linspace_float(low=1.6, high=2.5, step=0.3),
            outer_band_multiplier=linspace_float(low=1.0, high=3.0, step=1.0),
            pressure_window=linspace_int(low=10, high=50, step=20),
            # num_sell_tiers=linspace_int(1,4,step=1),
            # trailing_buy_order=[True, False],
        )

    sampler = optuna.samplers.GridSampler(search_space={**search_space, "dataset": DATASET_NAMES})
    total_trials = sampler._n_min_trials
    print(f"Number Trials: {total_trials}\n")

    if delete_existing:
        try:
            optuna.delete_study(study_name=study_name, storage=DATABASE_STR)
        except KeyError:
            print("Study does not exist, nothing deleted")
            pass
    if run_trials:
        start = datetime.now()

        # RUN SINGLE PROCESS
        # target(study_name, sampler, total_trials)

        n_processes = 9
        trials_per_process = 1
        trials_started = 0
        last_progress_report = 0
        pm = ProcessManager()
        while trials_started < total_trials:
            if pm.num_running_processes < n_processes:
                print(f"Starting process {trials_started}")
                pm.add_and_start(name=str(trials_started), target=target, args=(study_name, sampler, trials_per_process))  # fmt: skip
                trials_started += trials_per_process
                pm.remove_completed()
            else:
                pm.remove_completed()
                sleep(1)

            # Progress tracking every 10 trials
            trials_completed = trials_started - pm.num_running_processes
            if trials_completed >= last_progress_report + 10:
                time_fmt = "%H:%M:%S"
                last_progress_report = (trials_completed // 10) * 10
                elapsed = datetime.now() - start
                avg_time_per_trial = elapsed / trials_completed
                remaining_trials = total_trials - trials_completed
                est_remaining = avg_time_per_trial * remaining_trials
                est_finish = (datetime.now() + est_remaining).strftime(time_fmt)
                print(f"\n\tProgress: {trials_completed}/{total_trials} trials ({trials_completed * 100 // total_trials}%)")  # fmt: skip
                print(f"\tElapsed: {str(elapsed).split('.')[0]} | Remaining: {str(est_remaining).split('.')[0]} | Finish: {est_finish}")  # fmt: skip

        pm.block(sleep_secs=1)
        print(f"TOTAL RUN TIME: {datetime.now() - start}")

    study = optuna.load_study(study_name=study_name, storage=DATABASE_STR)
    param_names = sampler._param_names
    rename_map = {f"params_{p}": p for p in param_names}
    value_round = 2

    value_multiplier = 1
    if load_random_buy_study:
        random_study = optuna.load_study(study_name="random", storage=DATABASE_STR)
        df = random_study.trials_dataframe().round(3)
        df = df.rename(columns=rename_map)
        df = df[df["state"] == "COMPLETE"]
        df = df[["value", "random_seed", "dataset"]]
        df = df[~df.duplicated()]
        random_buy_value_df = df.groupby("dataset").agg(["mean", "max"])["value"]
        random_buy_value_df = (random_buy_value_df * value_multiplier).round(value_round)

    df = study.trials_dataframe().round(3)
    df = df[df["state"] == "COMPLETE"]
    df = df.rename(columns=rename_map)
    df = df[["value", *param_names]]
    df = df[~df.duplicated()]
    df = df[df["value"] != -1.0]
    df["value"] = (df["value"] * value_multiplier).round(value_round)
    df_orig = df.copy()
    for dataset in DATASET_NAMES:
        if load_random_buy_study:
            print(random_buy_value_df.loc[dataset].to_frame().T)
        else:
            print(dataset)

        df = df_orig[df_orig["dataset"] == dataset]
        if "random_seed" in df.columns:
            param_names = [col for col in param_names if col != "random_seed"]
            gb = df.groupby(param_names).mean().drop(columns="random_seed").round(3)
            df = gb.reset_index()

        top_ratio = 0.20
        top_count = int(len(df) * top_ratio)
        tops_df = df.sort_values("value", ascending=False).head(top_count)
        print(
            f"Top {top_ratio}: low={tops_df['value'].min()} avg={round(tops_df['value'].mean(), value_round)} max={tops_df['value'].max()}"
        )
        max_ = {}
        for col in param_names:
            if col == "dataset":
                continue
            gp = df.groupby(col)["value"].mean().round(value_round)
            # max_[col] = _try_round2(gp.sort_values().index[-1])
            print(gp.to_frame().T)
            print()

        # print(f"\nBest set {max_}")
        # print(f"\nMode of top {top_ratio}")
        # print(tops_df.mode().to_dict(orient="records")[0])

        # print("\nCorrelations")
        # for col in param_names:
        #     corr = df[col].corr(df["value"])
        #     print(f"{col}: {corr:.3f}")

        print()
    print()

    """
    mysql.server start

    mysql.server stop

    mysql.server restart

    mysql -u root -e "CREATE DATABASE IF NOT EXISTS optuna"

    mysql -u root -p optuna
    """
