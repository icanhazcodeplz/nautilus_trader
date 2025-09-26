#!/usr/bin/env python3
import os
import sys

sys.path.append(os.getcwd())

import optuna
import logging

from backtest.mock_clients import WSFileIB
from backtest import backtest_test_data_path
from backtest.scalp_file import run_multiple_files, run_files_multiprocess
from trading.process_manager import ProcessManager
from trading.data_io import all_files_in_dir

mysql_optuna = "mysql://root@localhost/optuna"
optuna.logging.get_logger("optuna").addHandler(logging.StreamHandler(sys.stdout))

filenames = all_files_in_dir(backtest_test_data_path("ib_ws"), extension=".txt")
symbol = "AAPL"
filenames = [f for f in filenames if symbol in f]
# filenames = [f for f in filenames if "20250527" in f]
test_file_objs = []
for filename in filenames:
    filepath = backtest_test_data_path("ib_ws", filename)
    ws_test_file = WSFileIB(filepath, open_hours_only=True)
    if True:
        test_file_objs += [ws_test_file]

ENTRIES = True


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
    initial_stop = 0.10
    enter_if_above = "box"
    previous_closed_count = 6

    volume_check_count = 10

    increase_amt = 0.2
    increase_over_x_candles = 2
    volume_threshold_multiplier = 1.5

    if ENTRIES:
        # enter_if_above = trial.suggest_categorical("enter_if_above", choices=["box", "h"])
        # previous_closed_count = trial.suggest_int("previous_closed_count", 5, 10)
        # volume_check_count = trial.suggest_int("volume_check_count", 4, 10)
        #
        increase_amt = trial.suggest_float("increase_amt", low=0.20, high=0.30, step=0.02)
        increase_over_x_candles = trial.suggest_int("increase_over_x_candles", 2, 5)
        volume_check_count = trial.suggest_int("volume_check_count", 4, 10)
        volume_threshold_multiplier = trial.suggest_float("volume_threshold_multiplier", low=1.1, high=2, step=0.1)

        initial_stop = trial.suggest_float("initial_stop", low=0.10, high=0.20, step=0.01)

        take_increment = initial_stop
        stop_gap = initial_stop
        initial_qty_ratio = 1.0
        number_takes = 1
    else:
        take_increment = trial.suggest_float("take_increment", low=0.23, high=0.29, step=0.01)
        stop_gap = trial.suggest_float("stop_gap", low=0.21, high=0.28, step=0.01)
        initial_qty_ratio = trial.suggest_float("initial_qty_ratio", low=0.10, high=0.30, step=0.05)
        number_takes = trial.suggest_int("number_takes", 2, 6)

    previous_trail_value = _return_previous_trail_with_same_params(trial)
    if previous_trail_value is not None:
        return previous_trail_value

    scalp_params = dict(
        entry_class="Wave",
        entry_kwargs=dict(
            increase_amt=increase_amt,
            increase_over_x_candles=increase_over_x_candles,
            volume_threshold_multiplier=volume_threshold_multiplier,
            # previous_closed_count=previous_closed_count,
            # enter_if_above=enter_if_above,
            volume_check_count=volume_check_count,
        ),
        take_stop_kwargs=dict(
            take_increment=take_increment,
            stop_gap=stop_gap,
            initial_qty_ratio=initial_qty_ratio,
            number_takes=number_takes,
        ),
        initial_stop=initial_stop,
    )
    metrics = run_multiple_files(test_files_objs=test_file_objs, scalp_params=scalp_params, open_hours_only=True)

    # metrics = run_files_multiprocess(test_files_objs=test_file_objs, scalp_params=scalp_params, open_hours_only=True)
    if ENTRIES:
        metric_to_use = metrics["win_rate"]
    else:
        metric_to_use = metrics["pnl"] / metrics["trades"]
    return round(metric_to_use, 3)


def load_or_create_optuna_study(study_name):
    try:
        return optuna.load_study(study_name=study_name, storage=mysql_optuna)
    except KeyError:
        return optuna.create_study(
            direction="maximize",
            pruner=optuna.pruners.NopPruner(),
            study_name=study_name,
            storage=mysql_optuna,
            sampler=optuna.samplers.RandomSampler(),
        )


def target(study_name, n_trials):
    study = load_or_create_optuna_study(study_name)
    study.optimize(optimize, n_trials=n_trials, timeout=60 * n_trials)


if __name__ == "__main__":
    study_name = f"{symbol}-exits"
    if ENTRIES:
        study_name = f"{symbol}-entries"

    delete_existing = False
    run_trials = True
    total_trials = 10

    if delete_existing:
        try:
            optuna.delete_study(study_name=study_name, storage=mysql_optuna)
            optuna.create_study(
                direction="maximize", pruner=optuna.pruners.NopPruner(), study_name=study_name, storage=mysql_optuna
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

    if ENTRIES:
        cols = [
            # "params_enter_if_above",
            # "params_previous_closed_count",
            "params_increase_amt",
            "params_increase_over_x_candles",
            "params_volume_check_count",
            "volume_threshold_multiplier",
            "params_initial_stop",
        ]
    else:
        cols = [
            "params_take_increment",
            "params_stop_gap",
            "params_initial_qty_ratio",
            "params_number_takes",
        ]
    study = optuna.load_study(study_name=study_name, storage=mysql_optuna)

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
