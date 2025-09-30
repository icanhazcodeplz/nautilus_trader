#!/usr/bin/env python3
import os
import sys


sys.path.append(os.getcwd())

import optuna
import logging
from custom.runner import run_single_backtest

database_str = "sqlite:///optuna.db"
# mysql_optuna = "mysql://root@localhost/optuna"
optuna.logging.get_logger("optuna").addHandler(logging.StreamHandler(sys.stdout))

DATASET_NAMES = ["papl"]
_param_names = []  # Will store parameter names used in optimization


def run_multiple_files(test_files_objs, scalp_params, open_hours_only=True):
    pass

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
    strategy_name = "momo"

    params = dict(
        trade_size=100,
        # max_position_multiplier=trial.suggest_int("max_position_multiplier", low=1, high=3, step=1),
        max_position_multiplier=2,
        stop_loss=trial.suggest_float("stop_loss", low=0.25, high=0.45, step=0.05),
        take_profit=trial.suggest_float("take_profit", low=0.25, high=0.35, step=0.02),
        take_ratio=trial.suggest_float("take_ratio", low=0.8, high=1.0, step=0.10),
        vwap_window=trial.suggest_int("vwap_window", low=40, high=50, step=5),
        vwap_buy_threshold=trial.suggest_float("vwap_buy_threshold", low=0.10, high=0.30, step=0.05),
        trailing_stop=trial.suggest_categorical("trailing_stop", [True, False]),
    )

    global _param_names
    _param_names = list(trial.params.keys())
    if strategy_name != "random":
        previous_trail_value = _return_previous_trail_with_same_params(trial)
        if previous_trail_value is not None:
            return previous_trail_value

    value = run_single_backtest(DATASET_NAMES[0], strategy_name, params, return_engine=False, log_level="ERROR")
    return value



def load_or_create_optuna_study(study_name):
    try:
        return optuna.load_study(study_name=study_name, storage=database_str)
    except KeyError:
        return optuna.create_study(
            direction="maximize",
            pruner=optuna.pruners.NopPruner(),
            study_name=study_name,
            storage=database_str,
            sampler=optuna.samplers.RandomSampler(),
        )


def target(study_name, n_trials):
    study = load_or_create_optuna_study(study_name)
    study.optimize(optimize, n_trials=n_trials, timeout=60 * n_trials)


if __name__ == "__main__":
    study_name = "test"

    delete_existing = True
    run_trials = True
    total_trials = 100

    if delete_existing:
        try:
            optuna.delete_study(study_name=study_name, storage=database_str)
            optuna.create_study(
                direction="maximize", pruner=optuna.pruners.NopPruner(), study_name=study_name, storage=database_str
            )
        except KeyError:
            pass

    if run_trials:
        target(study_name, total_trials)
        # n_processes = max(os.cpu_count() - 2, 2)
        # n_trials = int(total_trials / n_processes)
        # pm = ProcessManager()
        # for i in range(n_processes):
        #     pm.add_and_start(name=str(i), target=target, args=(study_name, n_trials))
        # pm.block(sleep_secs=1)

    cols = [f"params_{p}" for p in _param_names]
    study = optuna.load_study(study_name=study_name, storage=database_str)

    df = study.trials_dataframe()
    df = df[df["state"] == "COMPLETE"]
    df = df[["value", *cols]]
    df = df.round(3)
    df = df[~df.duplicated()]

    top_ratio = 0.25
    top_count = int(len(df) * top_ratio)
    tops_df = df.sort_values("value", ascending=False).head(top_count)

    max = {}
    for col in cols:
        gp = df.groupby(col)["value"].mean().round(3)
        max[col.replace("params_", "")] = _try_round2(gp.sort_values().index[-1])
        print()
        print(gp)

    print(max)
    print(f"\n Mode of top {top_ratio}")
    print(tops_df.mode().to_dict(orient="records")[0])

    """
    mysql.server start

    mysql.server stop

    mysql.server restart

    mysql -u root -e "CREATE DATABASE IF NOT EXISTS optuna"

    mysql -u root -p optuna
    """
