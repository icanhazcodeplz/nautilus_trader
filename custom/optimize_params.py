#!/usr/bin/env python3
import os
import sys
from datetime import datetime

import numpy as np
from optuna.samplers import GridSampler

from custom.process_manager import ProcessManager

sys.path.append(os.getcwd())

import optuna
import logging
from custom.runner import run_single_backtest, run_multiple_backtests
from nautilus_trader.analysis.statistics.trade_avg_scaled import TotalBought

DATABASE_STR = "sqlite:///optuna.db"
# mysql_optuna = "mysql://root@localhost/optuna"
optuna.logging.get_logger("optuna").addHandler(logging.StreamHandler(sys.stdout))

DATASET_NAMES = ["papl", "mss"]

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
    # take_ratio=trial.suggest_float("take_ratio", low=0.8, high=1.0, step=0.10),
    # vwap_window=trial.suggest_int("vwap_window", low=40, high=50, step=5),
    # vwap_buy_threshold=trial.suggest_float("vwap_buy_threshold", low=0.10, high=0.30, step=0.05),
    # trailing_stop=trial.suggest_categorical("trailing_stop", [True, False]),
    strategy_name = "momo"

    params = dict(
        trade_size=100,
        max_position_multiplier=2,
        stop_loss=0.3,
        take_profit=0.3,
        take_ratio=0.8,
        vwap_window=60,
        vwap_buy_threshold=0.10,
        trailing_stop=False,
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
        else:
            raise ValueError(f"Unknown param type {param_type}")

    if strategy_name != "random":
        previous_trail_value = _return_previous_trail_with_same_params(trial)
        if previous_trail_value is not None:
            return previous_trail_value

    performance_stats = run_multiple_backtests(DATASET_NAMES, strategy_name, params, log_level="ERROR")
    total_pnl = performance_stats['PnL (total)'].sum()
    total_bought = performance_stats[TotalBought().name].sum()
    value = total_pnl / total_bought * 100
    return value


def load_or_create_optuna_study(study_name, sampler):
    return optuna.create_study(
        study_name=study_name,
        storage=DATABASE_STR,
        direction="maximize",
        sampler=sampler,
        pruner=optuna.pruners.NopPruner(),
        load_if_exists=True
    )


def target(study_name, sampler, n_trials):
    study = load_or_create_optuna_study(study_name, sampler=sampler)
    # NOTE: setting n_jobs above 1 creates more threads, not processes. It is not any faster than n_jobs=1.
    study.optimize(optimize, n_trials=n_trials, timeout=60 * n_trials, n_jobs=1)


if __name__ == "__main__":
    study_name = "test"

    delete_existing = True
    run_trials = True

    search_space= dict(
        random_seed= [4, 5, 6],
        # max_position_multiplier=linspace_int( low=1, high=3, step=1),
        stop_loss=linspace_float( low=0.25, high=0.35, step=0.1),
        take_profit=linspace_float( low=0.25, high=0.35, step=0.05),
        # take_ratio=linspace_float( low=0.8, high=1.0, step=0.10),
        # vwap_window=linspace_int( low=40, high=50, step=5),
        # vwap_buy_threshold=linspace_float( low=0.10, high=0.30, step=0.05),
        # trailing_stop=[True, False],
    )
    sampler = optuna.samplers.GridSampler(search_space=search_space)
    total_trials = sampler._n_min_trials
    print(f"Running {total_trials} trials")

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

        # RUN MULTIPROCESSING
        n_processes = max(os.cpu_count() - 2, 2)
        n_trials = int(total_trials / n_processes) + 1
        pm = ProcessManager()
        for i in range(n_processes):
            pm.add_and_start(name=str(i), target=target, args=(study_name, sampler, n_trials))
        pm.block(sleep_secs=1)
        print(f"TOTAL RUN TIME: {datetime.now() - start}")

    study = optuna.load_study(study_name=study_name, storage=DATABASE_STR)

    param_names = sampler._param_names
    rename_map = {f"params_{p}":p for p in param_names}
    df = study.trials_dataframe().round(3)
    df = df[df["state"] == "COMPLETE"]
    df = df.rename(columns=rename_map)
    df = df[["value", *param_names]]
    df = df[~df.duplicated()]
    if "random_seed" in df.columns:
        param_names = [col for col in param_names if col != "random_seed"]
        gb = df.groupby(param_names).mean().drop(columns="random_seed").round(3)
        df = gb.reset_index()

    top_ratio = 0.25
    top_count = int(len(df) * top_ratio)
    tops_df = df.sort_values("value", ascending=False).head(top_count)

    max_ = {}
    for col in param_names:
        gp = df.groupby(col)["value"].mean().round(3)
        max_[col] = _try_round2(gp.sort_values().index[-1])
        print()
        print(gp.to_string())

    print(f"\nBest set {max_}")
    print(f"\nMode of top {top_ratio}")
    print(tops_df.mode().to_dict(orient="records")[0])

    """
    mysql.server start

    mysql.server stop

    mysql.server restart

    mysql -u root -e "CREATE DATABASE IF NOT EXISTS optuna"

    mysql -u root -p optuna
    """
