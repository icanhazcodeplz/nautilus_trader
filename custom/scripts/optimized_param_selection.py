#!/usr/bin/env python3
"""
Walk-forward test of picking MomoStrategy's `GRID` params by the average of a 3^n neighbourhood.

Each combination of `PER_SET_GRID` (a "slice") gets a grid of the n params of `GRID` (e.g. take_profit x
entry_distance). Every `UPDATE_EVERY` trading days (or on each week's first trading day, with None), every
point not on an edge of `GRID` is scored by the average, over itself and its neighbours one step away in
each `GRID` param in the same slice, of a metric over the previous `LOOKBACK` trading days. Edges can't be
a centre, so every average is over 3^n param sets (9 for two params, 27 for three). Each slice's best
centre is found, and the best of those is traded until the next pick. Metrics:
    pnl          total PnL
    pnl_per100   PnL per 100 shares entered (a set that never entered has none, and is left out of the average)
    sharpe       mean / std of daily PnL (0 for a set whose PnL never varies, e.g. one that never traded)

Each rule is scored from `SCORE_STARTING` (the first trading day on or after it that has `LOOKBACK` earlier
days; earlier days still feed the lookbacks), or with None from the first week that has `LOOKBACK` earlier
days, whatever `UPDATE_EVERY` is, against:
    grid average     every param set of the grid at once, split evenly
    best fixed set   the single param set with the highest PnL over those same weeks (look-ahead)
    best pnl_per100 fixed set
                     the single param set with the highest PnL per 100 shares entered over them (look-ahead)
    best daily set   each day's highest-PnL param set that day (look-ahead): the ceiling for any picking rule
`t` is the paired t-stat of a rule's daily PnL minus the grid average's.

Days around `SYMBOL`'s earnings (`EARNINGS_DATES` of `custom/utils/earnings_dates.py`) are left out of everything,
lookbacks and scoring alike: each day whose trading-day offset from an earnings open is in
`LEAVE_OUT_EARNINGS_DAYS`. Offset 0 is the earnings open, the first session after the release (releases
come after the close), so -1 is the release day itself.

Results come from the study store: run the grid with `custom/optimize/optimize_multi_day.py` first, with
the same `SYMBOL`, `START_TIME`, `END_TIME` and params. Run from the repo root:
    python custom/scripts/optimized_param_selection.py
"""

import os
import sys


sys.path.append(os.getcwd())
import itertools
from enum import Enum

import msgspec
import numpy as np
import pandas as pd

from custom.optimize import study_store
from custom.optimize.optimize_multi_day import linspace_int
from custom.scripts.opening_range_features import MAX_PREV_GAP_DAYS
from custom.strategies.momo import DirectionStrategy
from custom.strategies.momo import DirectionThreshold
from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import ExitStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.earnings_dates import EARNINGS_DATES


SYMBOL = "GOOG"
METRICS = ["pnl", "pnl_per100", "sharpe"]
START_TIME = "09:29:00"
END_TIME = "09:36:00"
SCORE_STARTING = "2026-10-01"


if SYMBOL == "AMZN":
    FIXED_PARAMS = dict(
        allow_trades=True,
        max_position_multiplier=1,
        trade_size=100,
        band_multiplier=0.5,
        band_window=150,
        band_variance_window=300,
        flip_side_confirm_ticks=150,
        exit_strategy=ExitStrategy.SIMPLE_TAKE,
        num_exit_tiers=1,
        start_trading_at="09:30",
        stop_entries_after="09:31",
        force_exit_at="09:35",
        entry_exclusion_band=None,
        entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
        direction_threshold=DirectionThreshold.ROLLING_VWAP,
        opening_range_pred_pull=None,
        take_profit=None,
        skip_same_side_reentry_after_take_profit=True,
    )
    PER_SET_GRID = dict(
        direction_strategy=[DirectionStrategy.REVERSION, DirectionStrategy.SHORT_ONLY],
        rolling_vwap_window=[3000, 3200, 3400],
    )
    GRID = dict(
        stop_loss=linspace_int(low=22, high=34, step=1),
        entry_distance=linspace_int(low=26, high=35, step=1),
    )
    LOOKBACK = 7
    LEAVE_OUT_EARNINGS_DAYS = [-1, 0, 1]
    # LEAVE_OUT_EARNINGS_DAYS = [0]
    UPDATE_EVERY = 1

elif SYMBOL == "AAPL":
    FIXED_PARAMS = dict(
        allow_trades=True,
        max_position_multiplier=1,
        trade_size=100,
        band_multiplier=0.5,
        band_window=150,
        band_variance_window=300,
        flip_side_confirm_ticks=150,
        exit_strategy=ExitStrategy.SIMPLE_TAKE,
        num_exit_tiers=1,
        force_exit_at="09:35",
        entry_exclusion_band=None,
        entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
        direction_threshold=DirectionThreshold.ROLLING_VWAP,
        opening_range_pred_pull=None,
        take_profit=None,
        direction_strategy=DirectionStrategy.REVERSION,
        skip_same_side_reentry_after_take_profit=False,
        stop_entries_after="09:31",
        start_trading_at="09:30",
    )
    PER_SET_GRID = dict(
        rolling_vwap_window=[3000, 3200],
    )
    GRID = dict(
        stop_loss=linspace_int(low=30, high=45, step=1),
        entry_distance=linspace_int(low=29, high=42, step=1),
    )
    LOOKBACK = 20
    LEAVE_OUT_EARNINGS_DAYS = [-1, 0, 1]
    # LEAVE_OUT_EARNINGS_DAYS = [0]
    UPDATE_EVERY = 1

elif SYMBOL == "GOOG":
    FIXED_PARAMS = dict(
        allow_trades=True,
        max_position_multiplier=1,
        trade_size=100,
        band_multiplier=0.5,
        rolling_vwap_window=3200,
        band_window=150,
        band_variance_window=300,
        flip_side_confirm_ticks=150,
        exit_strategy=ExitStrategy.SIMPLE_TAKE,
        num_exit_tiers=1,
        force_exit_at="09:35",
        entry_exclusion_band=None,
        entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
        direction_threshold=DirectionThreshold.ROLLING_VWAP,
        opening_range_pred_pull=None,
        direction_strategy=DirectionStrategy.REVERSION,
        skip_same_side_reentry_after_take_profit=True,
        start_trading_at="09:30",
        stop_entries_after="09:31",
        take_profit=None,
    )

    PER_SET_GRID = dict()

    GRID = dict(
        stop_loss=linspace_int(low=38, high=58, step=1),
        entry_distance=linspace_int(low=40, high=54, step=1),
    )
    LOOKBACK = 20
    LEAVE_OUT_EARNINGS_DAYS = [-1, 0, 1]
    # LEAVE_OUT_EARNINGS_DAYS = [0]
    UPDATE_EVERY = 1

# LOAD ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _display(value):
    return value.value if isinstance(value, Enum) else value


def earnings_days(days: pd.DatetimeIndex, offsets: list[int], symbol: str) -> pd.DatetimeIndex:
    """
    The days of `days` at each of `offsets` trading days (positions in `days`) from an earnings open:
    the first day of `days` after each of `symbol`'s `EARNINGS_DATES`. A release whose next day in `days`
    is more than `MAX_PREV_GAP_DAYS` later (one before or after `days`, or with its open missing) is
    skipped.
    """
    if not offsets:
        return pd.DatetimeIndex([])
    if symbol not in EARNINGS_DATES:
        raise ValueError(
            f"No earnings dates for {symbol}: add them to custom/utils/earnings_dates.py, "
            "or set LEAVE_OUT_EARNINGS_DAYS = []"
        )
    days = days.sort_values()
    out = []
    for release in pd.DatetimeIndex(EARNINGS_DATES[symbol]):
        open_pos = days.searchsorted(release, side="right")
        if open_pos == len(days) or (days[open_pos] - release).days > MAX_PREV_GAP_DAYS:
            continue
        out.extend(days[open_pos + k] for k in offsets if 0 <= open_pos + k < len(days))
    return pd.DatetimeIndex(sorted(set(out)))


def load_grid(conn) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Daily PnL and shares entered (param sets x days) of every param set of the grid, on the days all of
    them have other than the `LEAVE_OUT_EARNINGS_DAYS` ones, and each set's `PER_SET_GRID` and `GRID`
    values.
    """
    swept = {**PER_SET_GRID, **GRID}
    rows = []
    for combo in itertools.product(*swept.values()):
        values = dict(zip(swept, combo, strict=True))
        config = msgspec.convert({**FIXED_PARAMS, **values}, MomoStrategyConfig)
        p_hash = study_store.params_hash(SYMBOL, START_TIME, END_TIME, config)
        rows.append({"params_hash": p_hash, **{k: _display(v) for k, v in values.items()}})
    params = pd.DataFrame(rows).set_index("params_hash")

    hashes = list(params.index)
    results = pd.read_sql_query(
        f"SELECT params_hash, day, pnl, entered FROM day_results WHERE params_hash IN ({','.join('?' * len(hashes))})",
        conn,
        params=hashes,
    )
    missing = params.index.difference(results["params_hash"].unique())
    if len(missing):
        raise ValueError(
            f"{len(missing)} of {len(params)} param sets have no results; run the grid with optimize_multi_day first"
        )
    pnl = results.pivot(index="params_hash", columns="day", values="pnl").loc[params.index]
    entered = results.pivot(index="params_hash", columns="day", values="entered").loc[params.index]
    complete = pnl.notna().all()
    if not complete.all():
        print(f"NOTE: {(~complete).sum()} days that some param sets have no result for are left out")
    pnl, entered = pnl.loc[:, complete], entered.loc[:, complete]
    pnl.columns = entered.columns = pd.to_datetime(pnl.columns)

    # Offsets are counted on the days that have results, before any are dropped
    dropped = earnings_days(pnl.columns, LEAVE_OUT_EARNINGS_DAYS, SYMBOL)
    if len(dropped):
        print(
            f"NOTE: {len(dropped)} earnings days left out (offsets {LEAVE_OUT_EARNINGS_DAYS}): "
            f"{', '.join(str(day.date()) for day in dropped)}"
        )
    keep = ~pnl.columns.isin(dropped)
    return pnl.loc[:, keep], entered.loc[:, keep], params


def slice_labels(params: pd.DataFrame) -> pd.Series:
    """Each param set's slice: its `PER_SET_GRID` values joined, or "" for all with an empty `PER_SET_GRID`."""
    if not PER_SET_GRID:
        return pd.Series("", index=params.index)
    return params[list(PER_SET_GRID)].astype(str).agg("|".join, axis=1)


def squares(params: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """
    Each 3^n neighbourhood of the n params of `GRID` within a slice (3x3 for two of them, 3x3x3 for
    three): its centre (row of `params`) and its 3^n members. A centre is never on an edge of `GRID`.
    """
    too_few = [name for name, values in GRID.items() if len(values) < 3]
    if too_few:
        raise ValueError(f"{too_few} in GRID have fewer than 3 values, so no centre: move them to FIXED_PARAMS")
    # Each set's position in each of GRID's value lists, one column per param
    pos = np.stack(
        [params[name].map({v: i for i, v in enumerate(values)}).to_numpy() for name, values in GRID.items()], 1
    )
    sizes = np.array([len(values) for values in GRID.values()])
    slices = slice_labels(params).to_numpy()
    index = {(s, *p): i for i, (s, p) in enumerate(zip(slices, map(tuple, pos), strict=True))}
    offsets = np.array(list(itertools.product((-1, 0, 1), repeat=len(GRID))))
    centres, members = [], []
    for i, (s, p) in enumerate(zip(slices, pos, strict=True)):
        if ((p > 0) & (p < sizes - 1)).all():
            centres.append(i)
            members.append([index[(s, *(p + offset))] for offset in offsets])
    return np.array(centres), np.array(members)


# RULES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def metric(kind, P: np.ndarray, E: np.ndarray) -> np.ndarray:
    """Each param set's `kind` over the days (columns) of `P` and `E`; NaN where it has none."""
    if kind == "pnl":
        return P.sum(1)
    if kind == "pnl_per100":
        entered = E.sum(1)
        return np.where(entered > 0, P.sum(1) / np.where(entered > 0, entered, 1) * 100, np.nan)
    if kind == "sharpe":
        std = P.std(1)
        return np.where(std > 0, P.mean(1) / np.where(std > 0, std, 1), 0.0)
    raise ValueError(f"Unknown metric {kind!r}")


def square_scores(kind, P, E, members) -> np.ndarray:
    """Each square's average `kind` over its members; -inf for a square where none of them has one."""
    values = metric(kind, P, E)[members]
    has = ~np.isnan(values).all(1)
    out = np.full(len(members), -np.inf)
    out[has] = np.nanmean(values[has], 1)
    return out


def week_starts(days: pd.DatetimeIndex) -> list[int]:
    """Index of each week's first trading day, for the weeks that have `LOOKBACK` earlier days."""
    weeks = days.to_period("W-SUN")
    firsts = [int(np.flatnonzero(weeks == week)[0]) for week in weeks.unique()]
    return [i for i in firsts if i >= LOOKBACK]


def first_scored(days: pd.DatetimeIndex) -> int:
    """
    Index of the first day scored: the first on or after `SCORE_STARTING` that has `LOOKBACK` earlier
    days, or with None the first week's first trading day that has them.
    """
    if SCORE_STARTING is None:
        return week_starts(days)[0]
    first = max(LOOKBACK, int(days.searchsorted(pd.Timestamp(SCORE_STARTING))))
    if first >= len(days):
        raise ValueError(f"No days to score on or after {SCORE_STARTING} (last day {days[-1].date()})")
    return first


def update_starts(days: pd.DatetimeIndex) -> list[int]:
    """
    Index of each day the rule picks on, from `first_scored`: every `UPDATE_EVERY` trading days, or with
    None on it and then each week's first trading day.
    """
    first = first_scored(days)
    if UPDATE_EVERY is None:
        return [first] + [i for i in week_starts(days) if i > first]
    return list(range(first, len(days), UPDATE_EVERY))


def walk_forward(kind, P, E, days, centres, members) -> tuple[np.ndarray, pd.DataFrame]:
    """
    The rule's param set (row of `P`) on each evaluated day, and one row per pick: the day it was made
    on, how many days it was held, the set and its square's score.
    """
    starts = update_starts(days)
    ends = [*starts[1:], len(days)]
    picks = np.empty(len(days) - starts[0], dtype=int)
    periods = []
    for start, end in zip(starts, ends, strict=True):
        window = np.arange(start - LOOKBACK, start)
        scores = square_scores(kind, P[:, window], E[:, window], members)
        best = int(np.argmax(scores))
        picks[start - starts[0] : end - starts[0]] = centres[best]
        periods.append({"start": days[start].date(), "days": end - start, "set": centres[best], "score": scores[best]})
    return picks, pd.DataFrame(periods)


# SCORING ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def summarize(name, pnl: np.ndarray, entered: np.ndarray, grid_avg: np.ndarray, weeks, picks=None) -> dict:
    """A strategy's results over the evaluated days, against the grid average."""
    diff = pnl - grid_avg
    cum = np.cumsum(pnl)
    weekly = pd.Series(pnl).groupby(weeks).sum()
    weekly_avg = pd.Series(grid_avg).groupby(weeks).sum()
    return {
        "strategy": name,
        "pnl": round(pnl.sum()),
        "pnl_per100": round(pnl.sum() / entered.sum() * 100, 2) if entered.sum() > 0 else np.nan,
        "sharpe": round(pnl.mean() / pnl.std(), 3) if pnl.std() > 0 else np.nan,
        "vs_avg": round(diff.sum()),
        "t": round(diff.mean() / diff.std() * np.sqrt(len(diff)), 2) if diff.std() > 0 else np.nan,
        "weeks_ahead": f"{(weekly > weekly_avg).sum()}/{len(weekly)}" if diff.any() else "",
        "worst_day": round(pnl.min()),
        "worst_week": round(weekly.min()),
        "max_dd": round((np.maximum.accumulate(np.maximum(cum, 0)) - cum).max()),
        "shares": round(entered.sum()),
        "n_sets": len(set(picks)) if picks is not None else np.nan,
    }


def pick_table(picks, picked: pd.DataFrame, P, E, grid_avg, cols, params) -> pd.DataFrame:
    """One row per pick: the rule's PnL, PnL per 100 and the grid average's while it was held, and its params."""
    period = np.repeat(np.arange(len(picked)), picked["days"].to_numpy())
    df = pd.DataFrame({"pnl": P[picks, cols], "entered": E[picks, cols], "grid_avg": grid_avg})
    table = df.groupby(period).sum()
    table.insert(0, "days", picked["days"].to_numpy())
    table["pnl_per100"] = (table["pnl"] / table["entered"].replace(0, np.nan) * 100).round(2)
    table["vs_avg"] = table["pnl"] - table["grid_avg"]
    table["cum"] = table["pnl"].cumsum()
    table["score"] = picked["score"].round(3).to_numpy()
    chosen = params.iloc[picked["set"].to_numpy()].reset_index(drop=True)
    table = table.drop(columns="entered").join(chosen[list(GRID) + list(PER_SET_GRID)])
    table.index = [str(start) for start in picked["start"]]
    return table


def next_pick(kind, P, E, days, centres, members, params) -> pd.DataFrame:
    """Each slice's best square over the last `LOOKBACK` days: what the rule would trade next."""
    window = np.arange(len(days) - LOOKBACK, len(days))
    scores = square_scores(kind, P[:, window], E[:, window], members)
    table = params.iloc[centres].reset_index(drop=True)
    table["score"] = scores
    slices = slice_labels(table).to_numpy()
    best = table.loc[table.groupby(slices)["score"].idxmax()].sort_values("score", ascending=False)
    best.insert(0, "pick", np.where(best.index == int(np.argmax(scores)), "<--", ""))
    best["score"] = best["score"].round(3)
    return best.reset_index(drop=True)


# MAIN ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


if __name__ == "__main__":
    conn = study_store.open_store()
    pnl_df, entered_df, params = load_grid(conn)
    P, E, days_all = pnl_df.to_numpy(), entered_df.to_numpy(), pnl_df.columns
    centres, members = squares(params)

    cols = np.arange(first_scored(days_all), len(days_all))
    eval_days = days_all[cols]
    weeks = eval_days.to_period("W-SUN")
    grid_avg, grid_entered = P[:, cols].mean(0), E[:, cols].mean(0)
    best_fixed = int(np.argmax(P[:, cols].sum(1)))
    # nanargmax: a set that never entered has no PnL per 100
    best_fixed_per100 = int(np.nanargmax(metric("pnl_per100", P[:, cols], E[:, cols])))
    best_daily = P[:, cols].argmax(0)

    cadence = "each week" if UPDATE_EVERY is None else f"every {UPDATE_EVERY} days"
    print(
        f"=== {SYMBOL} {START_TIME}-{END_TIME}: {len(params)} param sets ({len(centres)} square centres) x "
        f"{len(days_all)} days ({days_all[0].date()} to {days_all[-1].date()}); lookback {LOOKBACK} days, "
        f"re-picked {cadence}, scored on {len(eval_days)} days from {eval_days[0].date()} ==="
    )

    rows, tables = [], {}
    for kind in METRICS:
        picks, picked = walk_forward(kind, P, E, days_all, centres, members)
        rows.append(summarize(f"square avg {kind}", P[picks, cols], E[picks, cols], grid_avg, weeks, picks))
        tables[kind] = pick_table(picks, picked, P, E, grid_avg, cols, params)
    rows.append(summarize("grid average", grid_avg, grid_entered, grid_avg, weeks))
    rows.append(
        summarize(
            "best fixed set (look-ahead)", P[best_fixed, cols], E[best_fixed, cols], grid_avg, weeks, [best_fixed]
        )
    )
    rows.append(
        summarize(
            "best pnl_per100 fixed set (look-ahead)",
            P[best_fixed_per100, cols],
            E[best_fixed_per100, cols],
            grid_avg,
            weeks,
            [best_fixed_per100],
        )
    )
    rows.append(
        summarize("best daily set (look-ahead)", P[best_daily, cols], E[best_daily, cols], grid_avg, weeks, best_daily)
    )

    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_rows", 500):
        print("\n=== Summary ===")
        print(pd.DataFrame(rows).to_string(index=False))
        print(f"\nbest fixed set: {params.iloc[best_fixed].to_dict()}")
        print(f"best pnl_per100 fixed set: {params.iloc[best_fixed_per100].to_dict()}")

        fixed_daily = pd.DataFrame(
            {"pnl": P[best_fixed, cols], "entered": E[best_fixed, cols], "grid_avg": grid_avg},
            index=eval_days.date,
        )
        fixed_daily["pnl_per100"] = (fixed_daily["pnl"] / fixed_daily["entered"].replace(0, np.nan) * 100).round(2)
        fixed_daily["vs_avg"] = fixed_daily["pnl"] - fixed_daily["grid_avg"]
        fixed_daily["cum"] = fixed_daily["pnl"].cumsum()
        print("\n=== Best fixed set (look-ahead): daily ===")
        print(fixed_daily.round(1).to_string())

        for kind, table in tables.items():
            print(f"\n=== Square avg {kind}: each pick ({cadence}) ===")
            print(table.round(1).to_string())

        span = f"{days_all[-LOOKBACK].date()} to {days_all[-1].date()}"
        for kind in METRICS:
            print(f"\n=== Next pick by square avg {kind} (over {span}): each slice's best ===")
            print(next_pick(kind, P, E, days_all, centres, members, params).to_string(index=False))
