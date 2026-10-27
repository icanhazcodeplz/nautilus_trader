#!/usr/bin/env python3
"""
Compare every param set that a symbol's studies have run, on the metrics that matter for trading it.

The param sets of all the symbol's studies are pooled. Results are stored per `(param set, day)`
and shared between studies, so a param set in several studies is counted once. Param sets are only
compared over the same days: those with results on fewer than `MIN_COVERAGE` of the symbol's days
are dropped, and the rest are scored over the days all of them share.

Per param set:
    value          Pnl Per100 over the days, as in `optimize_multi_day` (sum PnL / sum entered * 100)
    value_h1/h2    the same over the first and second half of the days
    nbr_value      mean value of its grid neighbours: the param sets that differ from it in one
                   numeric param, by one step. A high value with a low nbr_value is a lone spike.
    pnl, pnl_day   total PnL and mean PnL per day (no-trade days count as 0)
    entered        shares entered over the days
    days_traded    days with any entry
    win_rate       share of traded days with positive PnL
    avg_win/loss   mean PnL of winning and losing days
    profit_factor  winning days' PnL over losing days' |PnL|
    sharpe         daily mean / daily std of PnL, annualized
    t_stat         daily mean / its standard error
    worst_day, worst_trade, worst_month, pos_months (share of months with positive PnL), max_dd
    p_neg          bootstrap probability that the days' total PnL is negative

Then: how values spread across param sets, whether the first half of the days predicts the second
(if not, the top of the ranking is mostly noise), the value by each param, and the best param set
month by month.

Run from the repo root, with the symbol as the argument or set in `SYMBOL` below:
    python custom/optimize/analyze_optimizations.py AAPL
"""

import os
import sys


sys.path.append(os.getcwd())

import itertools

import numpy as np
import pandas as pd

from custom.optimize import study_store
from custom.optimize.optimize_multi_day import N_BOOTSTRAP
from custom.optimize.optimize_multi_day import _comparable_days
from custom.optimize.optimize_multi_day import _max_drawdown
from custom.optimize.optimize_multi_day import _summarize
from custom.optimize.study_store import HASH_DISPLAY_LEN


TRADING_DAYS_PER_YEAR = 252
# Param sets compared in the first-half/second-half check
SPLIT_TOP_K = 10

TABLE_COLUMNS = [
    "value",
    "nbr_value",
    "value_h1",
    "value_h2",
    "pnl",
    "entered",
    "days_traded",
    "win_rate",
    "profit_factor",
    "sharpe",
    "worst_day",
    "worst_trade",
    "max_dd",
    "p_neg",
]
ROUNDING = {
    "value": 3,
    "nbr_value": 3,
    "value_h1": 3,
    "value_h2": 3,
    "pnl": 2,
    "pnl_day": 2,
    "win_rate": 3,
    "avg_win": 2,
    "avg_loss": 2,
    "profit_factor": 2,
    "sharpe": 2,
    "t_stat": 2,
    "worst_day": 2,
    "worst_trade": 2,
    "worst_month": 2,
    "pos_months": 3,
    "max_dd": 2,
    "p_neg": 3,
}


def _per100(pnl, entered):
    total_entered = entered.sum()
    return pnl.sum() / total_entered * 100 if total_entered else np.nan


# LOAD ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def load_param_sets(conn, studies: pd.DataFrame) -> pd.DataFrame:
    """
    The studies' param sets as one row each, with their backtest window and the studies they are in.

    The window is not in a param set's params, but it is part of its hash, so it is a column here
    too: it is what tells apart two param sets with the same params run over different windows.
    """
    frames = []
    for study in studies.itertuples():
        param_sets = study_store.study_param_sets(conn, study.study_hash)
        param_sets["window"] = f"{study.start_time}-{study.end_time}"
        param_sets["studies"] = study.study_hash[:HASH_DISPLAY_LEN]
        frames.append(param_sets)
    param_sets = pd.concat(frames, ignore_index=True)
    studies_of = param_sets.groupby("params_hash")["studies"].agg(",".join)
    return param_sets.drop_duplicates("params_hash").drop(columns="studies").join(studies_of, on="params_hash")


def select_days(results: pd.DataFrame, param_sets: pd.DataFrame, min_coverage: float):
    """
    The param sets to compare and the days to compare them over.

    A param set is kept when it has results on at least `min_coverage` of the days any of the
    symbol's param sets has; the days are those every kept param set has.
    """
    n_days = results.groupby("params_hash")["day"].nunique().reindex(param_sets["params_hash"], fill_value=0)
    all_days = results["day"].nunique()
    kept = n_days.index[n_days >= min_coverage * all_days] if all_days else n_days.index[:0]
    dropped = n_days.drop(kept)
    if len(dropped):
        by_n_days = dropped.value_counts().sort_index(ascending=False)
        counts = ", ".join(f"{n} with {d} day(s)" for d, n in by_n_days.items())
        print(
            f"NOTE: {len(dropped)} param set(s) have results on fewer than {min_coverage:.0%} of the "
            f"{all_days} days and are left out ({counts}). Lower MIN_COVERAGE to include them, or "
            "run their studies to backfill them.\n"
        )
    return param_sets[param_sets["params_hash"].isin(kept)], _comparable_days(results, kept)


# METRICS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def performance(results: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    """Per param set metrics over `days`, which every param set in `results` must have a result for."""
    results = results[results["day"].isin(days)]
    pnl = results.pivot(index="params_hash", columns="day", values="pnl")[days]
    entered = results.pivot(index="params_hash", columns="day", values="entered")[days]
    max_loser = results.pivot(index="params_hash", columns="day", values="max_loser").reindex(columns=days)
    months = pd.Index(days).str[:7]
    half = len(days) // 2
    # The same resample indices for every param set, so their p_neg compare like for like
    boot_idx = np.random.default_rng(0).integers(0, len(days), size=(N_BOOTSTRAP, len(days)))

    rows = []
    for p_hash in pnl.index:
        day_pnl = pnl.loc[p_hash].to_numpy(dtype=float)
        day_entered = entered.loc[p_hash].to_numpy(dtype=float)
        day_max_loser = max_loser.loc[p_hash].to_numpy(dtype=float)
        traded_pnl = day_pnl[day_entered > 0]
        wins = traded_pnl[traded_pnl > 0]
        losses = traded_pnl[traded_pnl < 0]
        month_pnl = pd.Series(day_pnl).groupby(months).sum()
        std = day_pnl.std(ddof=1) if len(day_pnl) > 1 else np.nan
        mean = day_pnl.mean()
        rows.append(
            {
                "params_hash": p_hash,
                "value": _per100(day_pnl, day_entered),
                "value_h1": _per100(day_pnl[:half], day_entered[:half]),
                "value_h2": _per100(day_pnl[half:], day_entered[half:]),
                "pnl": day_pnl.sum(),
                "pnl_day": mean,
                "entered": int(day_entered.sum()),
                "days_traded": len(traded_pnl),
                "win_rate": len(wins) / len(traded_pnl) if len(traded_pnl) else np.nan,
                "avg_win": wins.mean() if len(wins) else np.nan,
                "avg_loss": losses.mean() if len(losses) else np.nan,
                "profit_factor": wins.sum() / -losses.sum() if len(losses) else np.inf if len(wins) else np.nan,
                "sharpe": mean / std * np.sqrt(TRADING_DAYS_PER_YEAR) if std > 0 else np.nan,
                "t_stat": mean / std * np.sqrt(len(day_pnl)) if std > 0 else np.nan,
                "worst_day": day_pnl.min(),
                "worst_trade": np.nanmin(day_max_loser) if not np.isnan(day_max_loser).all() else np.nan,
                "worst_month": month_pnl.min(),
                "pos_months": (month_pnl > 0).mean(),
                "max_dd": _max_drawdown(day_pnl),
                "p_neg": (day_pnl[boot_idx].sum(axis=1) < 0).mean(),
            }
        )
    return pd.DataFrame(rows)


def _is_numeric_param(col: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col)


def neighbor_values(scores: pd.DataFrame, varied: list[str]) -> pd.DataFrame:
    """
    Mean value of each param set's grid neighbours, and how many it has.

    Two param sets are neighbours when they agree on every varied param but one numeric one, and
    no other param set with those same params sits between them on it. Categorical params only
    ever split the grid, so a param set's neighbours always share its categories.
    """
    keys = scores[varied].astype(str)
    values = scores["value"].to_numpy()
    nbr_sum = np.zeros(len(scores))
    nbr_n = np.zeros(len(scores), dtype=int)
    for param in [p for p in varied if _is_numeric_param(scores[p])]:
        others = [p for p in varied if p != param]
        group_key = keys[others].agg("|".join, axis=1) if others else pd.Series("", index=scores.index)
        for _, group in scores.groupby(group_key):
            order = scores.index.get_indexer(group.sort_values(param).index)
            for i, j in itertools.pairwise(order):
                for a, b in ((i, j), (j, i)):
                    if not np.isnan(values[b]):
                        nbr_sum[a] += values[b]
                        nbr_n[a] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        nbr_value = np.where(nbr_n > 0, nbr_sum / nbr_n, np.nan)
    return pd.DataFrame({"nbr_value": nbr_value, "n_nbrs": nbr_n}, index=scores.index)


def monthly_table(results: pd.DataFrame, p_hash: str, days: list[str]) -> pd.DataFrame:
    """One param set's results by month, with a totals row."""
    df = results[(results["params_hash"] == p_hash) & results["day"].isin(days)].copy()
    df["month"] = df["day"].str[:7]
    df["traded"] = df["entered"] > 0
    df["won"] = df["pnl"] > 0
    agg = {"days": ("day", "count"), "traded": ("traded", "sum"), "won": ("won", "sum")}
    agg |= {"pnl": ("pnl", "sum"), "entered": ("entered", "sum"), "worst_day": ("pnl", "min")}
    by_month = pd.concat([df, df.assign(month="TOTAL")]).groupby("month").agg(**agg).reset_index()
    by_month["pnl_per100"] = (by_month["pnl"] / by_month["entered"].replace(0, np.nan) * 100).round(3)
    return by_month.round({"pnl": 2, "worst_day": 2})


# REPORT ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _print_df(df: pd.DataFrame):
    with pd.option_context("display.width", 300, "display.max_columns", None, "display.max_rows", 500):
        print(df.to_string(index=False))
    print()


def _plain(value):
    return value.item() if isinstance(value, np.generic) else value


def print_studies(studies: pd.DataFrame, param_sets: pd.DataFrame):
    rows = []
    for study in studies.itertuples():
        prefix = study.study_hash[:HASH_DISPLAY_LEN]
        swept = {name: val for name, val in study.spec.items() if isinstance(val, list)}
        rows.append(
            {
                "hash": prefix,
                "window": f"{study.start_time}-{study.end_time}",
                "created": study.created_at,
                "last_run": study.last_run_at,
                "compared": int(param_sets["studies"].str.contains(prefix).sum()),
                "swept": "; ".join(f"{name}={_summarize(vals)}" for name, vals in swept.items()),
            }
        )
    with pd.option_context("display.width", 300, "display.max_columns", None, "display.max_colwidth", 120):
        print(pd.DataFrame(rows).to_string(index=False))
    print()


def print_distribution(scores: pd.DataFrame):
    value = scores["value"].dropna()
    print("=== Spread of value across param sets ===")
    print(
        f"{len(scores)} param sets | {(value > 0).mean():.1%} have value > 0 | "
        f"{(scores['p_neg'] < 0.05).mean():.1%} have p_neg < 0.05 | "
        f"{(scores['sharpe'] > 1).mean():.1%} have sharpe > 1"
    )
    quantiles = value.quantile([0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]).round(3)
    print("value quantiles: " + ", ".join(f"p{int(q * 100)}={v}" for q, v in quantiles.items()))
    print(
        "The best of many param sets is the luckiest as much as the best: with this many tried, the "
        "top value overstates what that param set will make, and p_neg is not corrected for it.\n"
    )


def print_split_check(scores: pd.DataFrame, days: list[str]):
    """Rank on the first half of the days and see how those picks did in the second half."""
    half = len(days) // 2
    print(f"=== First half ({days[0]} to {days[half - 1]}) vs second half ({days[half]} to {days[-1]}) ===")
    if len(scores) < 3:
        print("Too few param sets to compare.\n")
        return
    corr = scores["value_h1"].corr(scores["value_h2"], method="spearman")
    top = scores.nlargest(SPLIT_TOP_K, "value_h1")
    h2_rank = scores["value_h2"].rank(pct=True)
    print(f"rank correlation of value between halves: {corr:.3f}  (near 0 = the ranking does not persist)")
    print(
        f"top {len(top)} by first half: second-half value mean {top['value_h2'].mean():.3f}, "
        f"median percentile {h2_rank[top.index].median():.0%}, "
        f"vs all param sets' median {scores['value_h2'].median():.3f}"
    )
    best_h1 = top.iloc[0]
    print(
        f"best by first half ({best_h1['hash']}): value_h1 {best_h1['value_h1']:.3f} -> value_h2 "
        f"{best_h1['value_h2']:.3f} (percentile {h2_rank[top.index[0]]:.0%})\n"
    )


def print_by_param(scores: pd.DataFrame, varied: list[str]):
    params = [p for p in varied if p != "window"] + (["window"] if "window" in varied else [])
    if not params:
        return
    print("=== Value by param (across every param set with that value) ===")
    for param in params:
        key = scores[param].astype(str) if not _is_numeric_param(scores[param]) else scores[param]
        grouped = scores.groupby(key, dropna=False)
        table = grouped["value"].agg(["mean", "median", "max", "count"])
        table["share_pos"] = grouped["value"].apply(lambda v: (v > 0).mean())
        table["sharpe_mean"] = grouped["sharpe"].mean()
        table["p_neg_mean"] = grouped["p_neg"].mean()
        print(f"\n--- {param} ---")
        print(table.round(3).to_string())
    print()


def print_leaders(scores: pd.DataFrame):
    """The best param set under each metric, to see whether they agree."""
    criteria = [
        ("value", False),
        ("nbr_value", False),
        ("sharpe", False),
        ("value_h2", False),
        ("p_neg", True),
        ("max_dd", True),
        ("worst_month", False),
    ]
    rows = []
    for col, ascending in criteria:
        ranked = scores.dropna(subset=[col]).sort_values(col, ascending=ascending)
        if ranked.empty:
            continue
        best = ranked.iloc[0]
        rows.append(
            {
                "best_by": col,
                "hash": best["hash"],
                **{c: best[c] for c in ("value", "nbr_value", "sharpe", "p_neg", "max_dd")},
                "value_rank": int(scores["value"].rank(ascending=False, method="min")[best.name]),
            }
        )
    print("=== Best param set by each metric ===")
    _print_df(pd.DataFrame(rows).round(ROUNDING))


def print_best(scores: pd.DataFrame, results: pd.DataFrame, param_sets: pd.DataFrame, days, varied, rank_by):
    best = scores.iloc[0]
    params = param_sets.set_index("params_hash").loc[best["params_hash"]]
    print(f"=== Best param set by {rank_by}: {best['hash']} (in studies {params['studies']}) ===")
    print("varied: " + str({p: _plain(params[p]) for p in varied}))
    fixed = {p: _plain(v) for p, v in params.drop([*varied, "studies"]).items()}
    print(f"fixed: {fixed}")
    metrics = {col: _plain(best[col]) for col in [*ROUNDING, "entered", "days_traded", "n_nbrs"]}
    print("metrics: " + ", ".join(f"{k}={round(v, ROUNDING.get(k, 0)) if v == v else v}" for k, v in metrics.items()))
    print()
    _print_df(monthly_table(results, best["params_hash"], days))


def analyze(conn, symbol, min_coverage=1.0, rank_by="value", top_n=25):
    studies = study_store.list_studies(conn, symbol)
    if studies.empty:
        raise SystemExit(f"No studies for {symbol}")
    param_sets = load_param_sets(conn, studies)
    results = study_store.load_symbol_results(conn, symbol)

    param_sets, days = select_days(results, param_sets, min_coverage)
    print(f"=== {symbol}: {len(studies)} studies ===")
    print_studies(studies, param_sets)
    if len(days) < 2:
        print("Fewer than 2 days shared by the param sets compared; nothing to analyze.")
        return

    param_cols = [c for c in param_sets.columns if c not in ("params_hash", "studies")]
    varied = [c for c in param_cols if param_sets[c].astype(str).nunique() > 1]
    scores = performance(results[results["params_hash"].isin(param_sets["params_hash"])], days)
    scores = scores.merge(param_sets[["params_hash", *varied, "studies"]], on="params_hash")
    scores = scores.join(neighbor_values(scores, varied))
    scores.insert(0, "hash", scores["params_hash"].str[:HASH_DISPLAY_LEN])
    scores = scores.sort_values(rank_by, ascending=rank_by in ("p_neg", "max_dd"), na_position="last")
    scores = scores.reset_index(drop=True)

    print(f"Compared over {len(days)} days, {days[0]} to {days[-1]}\n")
    print_distribution(scores)
    print(f"=== Top {min(top_n, len(scores))} of {len(scores)} param sets by {rank_by} ===")
    _print_df(scores.head(top_n)[["hash", *varied, *TABLE_COLUMNS]].round(ROUNDING))
    print_leaders(scores)
    print_split_check(scores, days)
    print_by_param(scores, varied)
    print_best(scores, results, param_sets, days, varied, rank_by)


if __name__ == "__main__":
    SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "AMZN"
    # Leave out param sets with results on fewer than this share of the symbol's days. 1.0 compares
    # only fully run param sets; lower it to bring in param sets of studies that have not been
    # backfilled, at the cost of comparing everything over fewer days.
    MIN_COVERAGE = 1.0
    # Metric the param sets are ranked by (any column of `performance`, or "nbr_value")
    RANK_BY = "value"
    TOP_N = 25

    analyze(study_store.open_store(), SYMBOL, min_coverage=MIN_COVERAGE, rank_by=RANK_BY, top_n=TOP_N)
