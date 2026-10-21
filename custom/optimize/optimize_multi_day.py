#!/usr/bin/env python3
"""
Grid-search MomoStrategy params for one symbol over every day in the catalog.

A study is identified by the hash of its spec (symbol, time window and the `params` dict with its
swept lists), not by a name; see `custom/optimize/study_store.py`. Results are stored per
`(param set, day)` and shared between studies, so `MODE = "run"` only ever backtests pairs that
have no result yet: a new grid, days added to the catalog since the last run, or whatever an
interrupted run left over.

A param set's score is Pnl Per100 over the days: sum of each day's realized PnL divided by the
sum of each day's shares entered, times 100. That is the same definition
`custom.statistics.trade_avg_scaled.PnlPer100` applies within a day, so a single day and the full
set are scored on one scale.

Backtests run in a pool of long-lived worker processes, which take them in chunks of one day's
param sets. A worker therefore loads a day's ticks from the catalog once and reuses them for every
param set of that day, rather than paying for a fresh interpreter and a fresh load per backtest.
That needs the Rust logger bypassed: it can only be initialized once per interpreter, and the
second engine would panic ("attempted to set a logger after the logging system was already
initialized"). Workers also share one string hash seed, so a result does not depend on which worker
ran it.
"""

import os
import sys


sys.path.append(os.getcwd())
import itertools
import json
import multiprocessing
from datetime import datetime

import numpy as np
import pandas as pd

from custom.backtest_scripts.strategy_backtest_runner import run_single_backtest
from custom.backtest_utils.load_catalog_data import BACKTESTING_CATALOG
from custom.backtest_utils.load_catalog_data import catalog_days_available
from custom.backtest_utils.load_catalog_data import get_catalog_data
from custom.optimize import study_store
from custom.optimize.study_store import HASH_DISPLAY_LEN
from custom.strategies.momo import DirectionStrategy
from custom.strategies.momo import DirectionThreshold
from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import ExitStrategy


# Backtests run concurrently.
N_WORKERS = 8
# Param sets of one day handed to a worker at once. Results are stored a chunk at a time, so an
# interrupted run loses at most one chunk per worker.
CHUNK_SIZE = 25
# Workers are replaced after this many chunks, so anything a long-lived interpreter accumulates
# across engines is bounded.
MAX_CHUNKS_PER_WORKER = 20
# String hash seed of every worker. Set iteration order follows string hashes, and a backtest's
# fills can depend on it (~0.1% of results differed by cents between workers with different seeds),
# so a fixed seed keeps results the same whichever worker runs them.
WORKER_HASH_SEED = "0"
N_BOOTSTRAP = 2000


def run_day(task):
    """
    Backtest one param set on one day. Top-level so a spawned worker can import it.

    The analyzer omits any statistic that returns `None`, so a day with no positions has no
    "Total Entered" or "Average Entry Price" key at all. Default `pnl` and `entered` to zero so the
    day drops out of the sums.
    """
    p_hash, symbol, day_str, start_time, end_time, params = task
    stats = run_single_backtest(
        symbol,
        f"{day_str} {start_time}",
        f"{day_str} {end_time}",
        "momo",
        params,
        save_artifacts=None,
        allow_trading_times=None,
        log_level="ERROR",
        bypass_logging=True,
        raise_errors=True,
    )
    return (
        p_hash,
        day_str,
        {
            "pnl": float(stats.get("PnL (total)") or 0.0),
            "entered": int(stats.get("Total Entered") or 0),
            "avg_entry_price": stats.get("Average Entry Price"),
            "max_loser": stats.get("Max Loser"),
        },
    )


# Catalog query results of the day a worker is on, keyed by the query's arguments.
_query_memo = {}
_query_memo_day = None


def _init_worker():
    """
    Memoize catalog queries for the worker's lifetime.

    Every backtest of a day makes the same queries: that day's quote and trade ticks, plus the
    historical trade ticks the strategy requests in `on_start` (two days of them, ~800k ticks for
    AAPL). The data engine queries the same catalog object, so patching the instance covers both.
    """
    query = BACKTESTING_CATALOG.query

    def memoized_query(*args, **kwargs):
        key = repr((args, sorted(kwargs.items())))
        if key not in _query_memo:
            _query_memo[key] = query(*args, **kwargs)
        return _query_memo[key]

    BACKTESTING_CATALOG.query = memoized_query


def run_chunk(tasks):
    """Backtest a chunk of `run_day` tasks, all on one day. Top-level so a spawned worker can import it."""
    global _query_memo_day
    day_str = tasks[0][2]
    if day_str != _query_memo_day:
        # Only one day's ticks are held at a time
        _query_memo.clear()
        get_catalog_data.cache_clear()
        _query_memo_day = day_str
    return [run_day(task) for task in tasks]


def make_pool():
    """
    Create the pool backtests run in: spawned interpreters with a fixed string hash seed.

    PYTHONHASHSEED is read once, at interpreter start, so it has to be in the environment the
    workers are spawned with; this interpreter's own seed is left as it was.
    """
    os.environ["PYTHONHASHSEED"] = WORKER_HASH_SEED
    ctx = multiprocessing.get_context("spawn")
    return ctx.Pool(processes=N_WORKERS, initializer=_init_worker, maxtasksperchild=MAX_CHUNKS_PER_WORKER)


def _chunk_by_day(tasks):
    chunks = []
    for _, day_tasks in itertools.groupby(sorted(tasks, key=lambda task: task[2]), key=lambda task: task[2]):
        day_tasks = list(day_tasks)
        chunks.extend(day_tasks[i : i + CHUNK_SIZE] for i in range(0, len(day_tasks), CHUNK_SIZE))
    return chunks


def linspace_int(low, high, step):
    return list(range(low, high + 1, step))


def linspace_float(low, high, step):
    return [round(val, 4) for val in np.arange(low, high + step * 0.90, step)]


# RUN ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def run_study(conn, symbol, start_time, end_time, params, restart=False) -> str:
    """Create or resume the study for this spec and backtest every (param set, day) it is missing."""
    study_hash = study_store.upsert_study(conn, symbol, start_time, end_time, params)
    if restart:
        deleted = study_store.delete_study_results(conn, study_hash)
        print(f"Restart: deleted {deleted} day results")

    days = catalog_days_available(symbol)
    if not days:
        raise ValueError(f"No catalog days for {symbol}")
    grid = {
        study_store.params_hash(symbol, start_time, end_time, param_set): param_set
        for param_set in study_store.expand_grid(params)
    }
    missing = study_store.missing_pairs(conn, study_hash, days)
    print(
        f"Study {study_hash[:HASH_DISPLAY_LEN]} | {symbol} {start_time}-{end_time} | {len(grid)} param sets x "
        f"{len(days)} days | {len(grid) * len(days) - len(missing)} done, {len(missing)} to run\n"
    )
    if not missing:
        return study_hash

    tasks = [(p_hash, symbol, day, start_time, end_time, grid[p_hash]) for p_hash, day in missing]
    started = datetime.now()
    # `chunksize=1` hands each worker one of our day chunks at a time, rather than the pool
    # batching several of them together. Each chunk's results are stored as it lands; a backtest
    # that raises stops the run rather than being stored as a score.
    with make_pool() as pool:
        i = 0
        for chunk_results in pool.imap_unordered(run_chunk, _chunk_by_day(tasks), chunksize=1):
            for p_hash, day, result in chunk_results:
                study_store.insert_result(conn, p_hash, day, result)
            i += len(chunk_results)
            elapsed = datetime.now() - started
            eta = elapsed / i * (len(tasks) - i)
            print(f"  {i}/{len(tasks)} backtests | elapsed {elapsed} | eta {eta}")
    study_store.touch_study(conn, study_hash)
    print(f"TOTAL RUN TIME: {datetime.now() - started}\n")
    return study_hash


# SHOW ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _max_drawdown(daily_pnl):
    cum = np.cumsum(daily_pnl)
    return float((np.maximum.accumulate(np.maximum(cum, 0)) - cum).max())


def score_param_sets(results: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    """Pnl Per100 and risk stats per param set, over `days` only."""
    results = results[results["day"].isin(days)]
    pnl = results.pivot(index="params_hash", columns="day", values="pnl")[days]
    entered = results.pivot(index="params_hash", columns="day", values="entered")[days]
    months = pd.Index(days).str[:7]
    # The same resample indices for every param set, so their P(total < 0) compare like for like.
    boot_idx = np.random.default_rng(0).integers(0, len(days), size=(N_BOOTSTRAP, len(days)))

    rows = []
    for p_hash in pnl.index:
        day_pnl = pnl.loc[p_hash].to_numpy()
        day_entered = entered.loc[p_hash].to_numpy()
        traded = day_entered > 0
        total_entered = int(day_entered.sum())
        rows.append(
            {
                "params_hash": p_hash,
                "value": round(day_pnl.sum() / total_entered * 100, 3) if total_entered else np.nan,
                "pnl": round(day_pnl.sum(), 2),
                "entered": total_entered,
                "days_traded": int(traded.sum()),
                "win_rate": round((day_pnl[traded] > 0).mean(), 3) if traded.any() else np.nan,
                "worst_day": round(day_pnl.min(), 2),
                "worst_month": round(pd.Series(day_pnl).groupby(months).sum().min(), 2),
                "max_dd": round(_max_drawdown(day_pnl), 2),
                "p_neg": round((day_pnl[boot_idx].sum(axis=1) < 0).mean(), 3),
            }
        )
    return pd.DataFrame(rows)


def _comparable_days(results: pd.DataFrame, hashes) -> list[str]:
    """Days that every one of `hashes` has a result for, so all of them are scored on the same days."""
    days_by_hash = results.groupby("params_hash")["day"].apply(set)
    common = set.intersection(*(days_by_hash.get(h, set()) for h in hashes)) if len(hashes) else set()
    return sorted(common)


def per_day_table(results: pd.DataFrame, p_hash: str, days: list[str]) -> pd.DataFrame:
    """One param set's day-by-day results with a totals row."""
    df = results[(results["params_hash"] == p_hash) & results["day"].isin(days)][["day", "pnl", "entered"]]
    df = df.sort_values("day")
    totals = pd.DataFrame([{"day": "TOTAL", "pnl": df["pnl"].sum(), "entered": df["entered"].sum()}])
    df = pd.concat([df, totals], ignore_index=True)
    df["pnl_per100"] = (df["pnl"] / df["entered"].replace(0, np.nan) * 100).round(3)
    return df


def show_study(conn, study_hash_prefix):
    study = study_store.find_study(conn, study_hash_prefix)
    study_hash = study["study_hash"]
    spec = study["spec"]
    param_sets = study_store.study_param_sets(conn, study_hash)
    results = study_store.load_study_results(conn, study_hash)

    swept = [p for p in study_store.swept_names(spec) if param_sets[p].astype(str).nunique() > 1]
    fixed = {name: val for name, val in spec.items() if not isinstance(val, list)}
    print(
        f"=== Study {study_hash[:HASH_DISPLAY_LEN]} | {study['symbol']} {study['start_time']}-{study['end_time']} ==="
    )
    print(f"created {study['created_at']} | last run {study['last_run_at']}")
    print(f"swept: { {p: spec[p] for p in study_store.swept_names(spec)} }")
    print(f"fixed: {fixed}\n")

    days = _comparable_days(results, param_sets["params_hash"])
    n_missing = len(set(catalog_days_available(study["symbol"])) - set(days))
    if n_missing:
        print(f"NOTE: {n_missing} catalog day(s) not run for every param set; MODE='run' backfills them.\n")
    if not days:
        print("No day has a result for every param set yet.")
        return

    scores = score_param_sets(results, days).merge(param_sets[["params_hash", *swept]], on="params_hash")
    scores.insert(0, "hash", scores.pop("params_hash").str[:HASH_DISPLAY_LEN])
    ranked = scores.sort_values("value", ascending=False)
    print(f"=== {len(ranked)} param sets over {len(days)} days ({days[0]} to {days[-1]}), best first ===")
    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_rows", 500):
        print(ranked.to_string(index=False))
    print()

    if swept:
        print("=== Value by param ===")
        for col in swept:
            print(f"\n--- {col} ---")
            print(scores.groupby(col)["value"].agg(["mean", "max", "count"]).round(2).to_string())
        print()

    if len(swept) > 1:
        print("=== Value by full param combination ===")
        print(scores.groupby(swept)["value"].mean().round(2).sort_values(ascending=False).to_string())
        print()

    best = ranked.iloc[0]
    best_hash = param_sets.loc[param_sets["params_hash"].str.startswith(best["hash"]), "params_hash"].iloc[0]
    print(f"=== Best param set {best['hash']}: value={best['value']} ===")
    print({p: best[p].item() if isinstance(best[p], np.generic) else best[p] for p in swept})
    print(per_day_table(results, best_hash, days).to_string(index=False))


def list_studies(conn, symbol=None):
    """Every study (for `symbol`, or all symbols), oldest first, with its best score."""
    studies = study_store.list_studies(conn, symbol)
    if studies.empty:
        print(f"No studies{f' for {symbol}' if symbol else ''}")
        return

    catalog_days = {}
    rows = []
    for study in studies.itertuples():
        param_sets = study_store.study_param_sets(conn, study.study_hash)
        results = study_store.load_study_results(conn, study.study_hash)
        days = _comparable_days(results, param_sets["params_hash"])
        best = score_param_sets(results, days)["value"].max() if days else np.nan
        if study.symbol not in catalog_days:
            catalog_days[study.symbol] = len(catalog_days_available(study.symbol))
        total_days = catalog_days[study.symbol]
        swept = {name: val for name, val in study.spec.items() if isinstance(val, list)}
        rows.append(
            {
                "hash": study.study_hash[:HASH_DISPLAY_LEN],
                "symbol": study.symbol,
                "window": f"{study.start_time}-{study.end_time}",
                "created": study.created_at,
                "last_run": study.last_run_at,
                "param_sets": len(param_sets),
                "days": f"{len(days)}/{total_days}",
                "best_value": best,
                "swept": "; ".join(f"{name}={_summarize(vals)}" for name, vals in swept.items()),
            }
        )
    with pd.option_context("display.width", 300, "display.max_columns", None, "display.max_colwidth", 120):
        print(pd.DataFrame(rows).to_string(index=False))


def _summarize(values):
    if len(values) > 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return f"[{min(values)}..{max(values)}]x{len(values)}"
    return json.dumps(values)


if __name__ == "__main__":
    # "run": create or resume the study below (or STUDY_HASH) and backtest whatever it is missing.
    # "restart": delete the study's results, then run. "show": print a study's results.
    # "list": every study for SYMBOL (None = all symbols).
    MODE = "run"
    # A past study's hash prefix (see "list"). None = the study defined by the spec below.
    STUDY_HASH = None

    SYMBOL = "AMZN"
    # Backtest window applied to every day, as New York wall-clock "HH:MM:SS". Each day's UTC
    # offset (EST or EDT) is worked out in `run_single_backtest`.
    START_TIME = "09:29:00"
    END_TIME = "09:41:00"
    # Momo params. A list is swept by the grid; anything else is fixed. A study's hash covers all
    # of it, so any edit here starts a new study -- one that reuses every result it shares with
    # earlier studies.
    params = dict(
        allow_trades=True,
        max_position_multiplier=1,
        trade_size=100,
        stop_loss=linspace_int(low=160, high=160, step=80),
        take_profit=linspace_int(low=20, high=32, step=4),
        upper_scalar_multiplier=0.5,
        # rolling_vwap_window=3000,
        rolling_vwap_window=linspace_int(low=2000, high=6000, step=1000),
        vwap_window=150,
        variance_window=300,
        outer_band_multiplier=2.5,
        flip_side_confirm_ticks=150,
        pressure_window=25,
        only_buy_if_macd_positive=False,
        direction_strategy=DirectionStrategy.REVERSION,
        # direction_strategy=[DirectionStrategy.REVERSION, DirectionStrategy.MOMENTUM],
        direction_threshold=DirectionThreshold.ROLLING_VWAP,
        # direction_threshold=[DirectionThreshold.OPEN, DirectionThreshold.ROLLING_VWAP],
        exit_strategy=ExitStrategy.SIMPLE_TAKE,
        # exit_strategy=[ExitStrategy.SIT_ON_ROLLING_VWAP, ExitStrategy.SIMPLE_TAKE],
        num_exit_tiers=2,
        # num_exit_tiers=linspace_int(1, 3, step=1),
        entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
        entry_distance=linspace_int(low=20, high=60, step=5),
        start_trading_at="09:29:58",
        stop_entries_after=["09:31"],
        force_exit_at="09:40",
        entry_exclusion_band=None,
        random_seed=1,
    )
    conn = study_store.open_store()
    if MODE in ("run", "restart"):
        if STUDY_HASH is not None:
            study = study_store.find_study(conn, STUDY_HASH)
            SYMBOL, START_TIME, END_TIME, params = (
                study["symbol"],
                study["start_time"],
                study["end_time"],
                study["spec"],
            )
        study_hash = run_study(conn, SYMBOL, START_TIME, END_TIME, params, restart=MODE == "restart")
        show_study(conn, study_hash)
    elif MODE == "show":
        if STUDY_HASH is None:
            STUDY_HASH = study_store.spec_hash(SYMBOL, START_TIME, END_TIME, params)
            if not study_store.study_exists(conn, STUDY_HASH):
                raise SystemExit(
                    f"The spec in this file ({STUDY_HASH[:HASH_DISPLAY_LEN]}) has not been run. "
                    "Set MODE = 'run', or set STUDY_HASH to a study from MODE = 'list'."
                )
        show_study(conn, STUDY_HASH)
    elif MODE == "list":
        list_studies(conn, SYMBOL)
    else:
        raise ValueError(f"Unknown MODE {MODE!r}")
