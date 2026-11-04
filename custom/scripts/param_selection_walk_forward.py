#!/usr/bin/env python3
"""
Walk-forward test of choosing a symbol's MomoStrategy params from earlier optimizer results.

Every param set of the symbol's studies (in `custom/optimize/study_store`) that ends at `END_TIME`
and has a result on every day forms the grid. Each rule picks one param set per day, using only
the days before it, and takes that set's PnL on the day. A rule re-picks either every day or on
each week's first trading day, holding the pick all week. All rules are scored on the same days,
those after the first `EVAL_START` (which only train), against the grid average: trading every
param set at once, split evenly. `t` is the paired t-stat of a rule's daily PnL minus the grid
average's, and `pctile` is where its total falls among the grid's fixed param sets.

The rules score each param set over a window of earlier days: the last N days, the previous
calendar month, or every earlier day. Scores:
    best      total PnL
    sharpe    mean / std of daily PnL ("safest")
    worst     the worst day ("safest")
    dd        total minus max drawdown ("safest")
    additive  each swept param's value scored by its mean over the rest of the grid, and the set
              with the highest sum picked; averages over hundreds of sets, so far less noisy

Regime rules pick the best set over only the earlier days in the same quantile of a pre-open
feature as today, or over today's nearest earlier days by volatility features. The features come
from `opening_range_features` and `opening_range_predictions`; run those first. `or_bps`, the
actual opening range, is look-ahead, and is there only to show what a perfect forecast would be
worth.

RESULTS (AMZN, 1,032 param sets ending 09:46, 2026-01-02 to 2026-10-28, scored on the 144 days
from 2026-04-06)
=========================================================================================

The grid average makes $1,948. A fixed param set makes $1,252 (10th percentile) to $2,624 (90th),
median $1,978. No rule beats the grid average reliably:

    window                 best daily  safest daily  best weekly  safest weekly
    previous day                1,677
    previous week (5)           1,580         3,178        1,826          2,100
    previous month (21)         1,277         1,662        1,656          1,789
    previous calendar month     2,411         1,679        2,376          1,722
    previous 2 months (42)      2,656         1,565        1,967          1,389
    previous quarter (63)       1,625         1,353        1,420          1,147
    all prior days              1,623         2,263        1,503          1,880

    (safest = sharpe)

Neighbouring windows disagree, as noise does: daily sharpe makes $3,201 over 4 days, $3,178 over 5
and $1,759 over 6. The top daily rule's t is 2.0 and the top weekly rule's 1.16, about what the
best of ~75 rules reaches by chance. The worst-day and drawdown rules are mostly below the grid
average ($907-$2,034), since they pick sets that seldom trade. Holding a pick for a week narrows
the spread but doesn't lift the middle: 8 of the 12 rules above make less weekly than daily. The
calendar-month rules are the most consistent across cadences: additive over the previous calendar
month makes $2,619 daily (t 1.28) and $2,553 weekly (t 1.16, ahead in both halves of the test),
but it only uses 6 sets over the 144 days, so it is in effect a monthly re-pick.

Across param sets, yesterday's PnL has a rank correlation of +0.14 with today's; over every earlier
day it's -0.12, so the sets that did best historically tend to do slightly worse next. Weekly it is
+0.05 +- 0.07 (last week) and -0.08 +- 0.07 (every earlier week). Over all 207 days only 2 of 1,032
sets beat the grid average at t > 2, and the two halves' totals correlate 0.035 across sets: the
grid is close to flat against a daily PnL std of ~$77. The in-sample best (SL 120, TP 23,
reversion, ED 34, exit 09:35, skip re-entry) makes $3,459 over the 207 days ($16.7/day) against
the grid average's $1,851 ($8.9/day); expect the latter. Only force_exit_at holds a preference
month after month: 09:33 is below the grid average in 9 of 10 months, 09:35 above it in 9 of 10.

Pre-open features: no feature's correlation with the grid average or with any param's spread (its
highest value's mean PnL minus its lowest's) survives Benjamini-Hochberg (245 tests, lowest q
0.42). The coherent ones are volatility: a higher predicted_or_bps favours TP 26 over 21 (Spearman
0.21) and a higher grid average (0.16). Nothing predicts short_only against reversion, the biggest
swing in the grid (best |rho| 0.13). Picking within the half of the actual opening range
would add $1,926 (t 3.5), but within the half of predicted_or_bps only $278 (t 0.6), and the 80
nearest days by volatility features $97. The grid average makes money in every third of
predicted_or_bps, so there's no case for sitting out calm days. A better opening-range forecast is
where the value is.

Edit the values under ``if __name__ == "__main__"`` and run from the repo root:
    python custom/scripts/param_selection_walk_forward.py
"""

import os
import sys


sys.path.append(os.getcwd())
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

from custom.optimize import study_store
from custom.scripts.opening_range_features import LABEL
from custom.utils.paths import data_subdir


SCORES = ["best", "sharpe", "worst", "dd", "additive"]
PREV_MONTH = "prev_month"
ALL_DAYS = "all"
# The volatility features regime rules find today's nearest earlier days by (logged)
KNN_FEATURES = ["predicted_or_bps", "pre0400_range_bps", "prev_last30_rv_bps"]


# LOAD ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def load_grid(conn, symbol, end_time) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Daily PnL (param sets x days) of every param set in `symbol`'s studies that ends at `end_time`
    and has a result on every day, with the params that vary between them (as strings).

    Param sets with an `opening_range_pred_pull` other than 0 are left out: it only does anything
    in backtests.
    """
    rows = conn.execute(
        "SELECT DISTINCT ps.params_hash, ps.params_json FROM param_sets ps"
        " JOIN study_param_sets sps ON sps.params_hash = ps.params_hash"
        " JOIN studies s ON s.study_hash = sps.study_hash WHERE s.symbol = ? AND s.end_time = ?",
        (symbol, end_time),
    ).fetchall()
    params = pd.DataFrame([{"params_hash": h, **json.loads(p)} for h, p in rows]).set_index("params_hash")
    if "opening_range_pred_pull" in params:
        params = params[params["opening_range_pred_pull"].fillna(0) == 0]

    results = study_store.load_symbol_results(conn, symbol)
    results = results[results["params_hash"].isin(params.index)]
    pnl = results.pivot(index="params_hash", columns="day", values="pnl")
    # Days most param sets have, then the param sets that have all of them
    pnl = pnl.loc[:, pnl.notna().mean() >= 0.5].dropna()
    pnl.columns = pd.to_datetime(pnl.columns)

    params = params.loc[pnl.index]
    swept = [c for c in params.columns if params[c].astype(str).nunique() > 1]
    return pnl, params[swept].astype(str)


def load_features(symbol, days) -> pd.DataFrame:
    """The pre-open features of `opening_range_features` plus the opening-range prediction and actual, on `days`."""
    analysis = Path(data_subdir("analysis"))
    features = pd.read_csv(analysis / f"{symbol}_opening_range_features.csv", index_col=0, parse_dates=True)
    preds = pd.read_csv(analysis / f"{symbol}_opening_range_predictions.csv", index_col=0, parse_dates=True)
    return features.drop(columns=LABEL).join(preds[["predicted_or_bps", LABEL]]).reindex(days)


# RULES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def score(kind, X: np.ndarray, params: pd.DataFrame) -> np.ndarray:
    """Each param set's score over the days (columns) of `X`; the highest is picked."""
    if kind == "best":
        return X.sum(1)
    if kind == "sharpe":
        return X.mean(1) / (X.std(1) + 1e-9)
    if kind == "worst":
        # Ties (sets that never traded) go to the higher total
        return X.min(1) + 1e-6 * X.sum(1)
    if kind == "dd":
        cum = np.cumsum(X, 1)
        return X.sum(1) - (np.maximum.accumulate(np.maximum(cum, 0), 1) - cum).max(1)
    if kind == "additive":
        total = pd.Series(X.sum(1))
        return sum(total.groupby(params[col].to_numpy()).transform("mean").to_numpy() for col in params.columns)
    raise ValueError(f"Unknown score {kind!r}")


def window_days(days: pd.DatetimeIndex, d: int, window) -> np.ndarray:
    """Indices of the days before day `d` a rule scores over: the last `window` days, `PREV_MONTH` or `ALL_DAYS`."""
    if window == ALL_DAYS:
        return np.arange(d)
    if window == PREV_MONTH:
        months = days.to_period("M")
        return np.flatnonzero(months == months[d] - 1)
    return np.arange(max(0, d - window), d)


def rebalance_days(days: pd.DatetimeIndex, eval_days, cadence) -> list[int]:
    """The day each of `eval_days` is picked on: itself, or its week's first trading day."""
    if cadence == "daily":
        return list(eval_days)
    if cadence == "weekly":
        weeks = days.to_period("W-SUN")
        first = {week: int(np.flatnonzero(weeks == week)[0]) for week in weeks.unique()}
        return [first[weeks[d]] for d in eval_days]
    raise ValueError(f"Unknown cadence {cadence!r}")


def walk_forward(P, params, days, eval_days, kind, window, cadence) -> tuple[np.ndarray, list[int]]:
    """A rule's PnL on each of `eval_days`, and the param set (row of `P`) it picked."""
    picked = {}
    picks = []
    for r in rebalance_days(days, eval_days, cadence):
        if r not in picked:
            picked[r] = int(np.argmax(score(kind, P[:, window_days(days, r, window)], params)))
        picks.append(picked[r])
    return P[picks, list(eval_days)], picks


def summarize(name, daily, P, eval_days, picks=None) -> dict:
    """A rule's total and risk on `eval_days`, against the grid average and the grid's fixed param sets."""
    grid_avg = P[:, eval_days].mean(0)
    diff = daily - grid_avg
    cum = np.cumsum(daily)
    return {
        "rule": name,
        "total": round(daily.sum()),
        "vs_avg": round(diff.sum()),
        "t": round(diff.mean() / diff.std() * np.sqrt(len(diff)), 2) if diff.std() > 0 else np.nan,
        "worst_day": round(daily.min()),
        "max_dd": round((np.maximum.accumulate(np.maximum(cum, 0)) - cum).max()),
        "pctile": round((P[:, eval_days].sum(1) < daily.sum()).mean(), 2),
        "n_sets": len(set(picks)) if picks is not None else np.nan,
    }


def _window_name(window):
    return {ALL_DAYS: "all prior days", PREV_MONTH: "previous calendar month"}.get(window, f"last {window} days")


# REGIME RULES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def quantile_rule(P, params, x: np.ndarray, eval_days, n_quantiles, kind) -> np.ndarray:
    """Pick the best set over the earlier days in the same quantile of `x` as today (cut points from the earlier days)."""
    daily = []
    for d in eval_days:
        prior = np.arange(d)
        cuts = np.quantile(x[prior], np.linspace(0, 1, n_quantiles + 1)[1:-1])
        same = prior[np.searchsorted(cuts, x[prior]) == np.searchsorted(cuts, x[d])]
        daily.append(P[int(np.argmax(score(kind, P[:, same], params))), d])
    return np.array(daily)


def knn_rule(P, X: np.ndarray, eval_days, k) -> np.ndarray:
    """Pick the best set over the `k` earlier days nearest today in the standardized features `X`."""
    daily = []
    for d in eval_days:
        prior = np.arange(d)
        dist = np.sqrt((((X[prior] - X[d]) / X[prior].std(0)) ** 2).sum(1))
        daily.append(P[int(np.argmax(P[:, prior[np.argsort(dist)[:k]]].sum(1))), d])
    return np.array(daily)


def _ordered(values):
    try:
        return sorted(values, key=float)
    except ValueError:
        return sorted(values)


def feature_spreads(P, params, days, features) -> pd.DataFrame:
    """
    Spearman of each pre-open feature with the grid average and with each swept param's daily
    spread (its last value's mean PnL minus its first's), with Benjamini-Hochberg q-values.
    """
    targets = {"grid_avg": P.mean(0)}
    for col in params.columns:
        values = _ordered(params[col].unique())
        lo, hi = values[0], values[-1]
        targets[f"{col}: {hi} - {lo}"] = P[(params[col] == hi).to_numpy()].mean(0) - P[
            (params[col] == lo).to_numpy()
        ].mean(0)
    candidates = [c for c in features.columns if c != LABEL]

    rows = []
    for target, values in targets.items():
        for feature in candidates:
            pair = pd.DataFrame({"x": features[feature].to_numpy(), "y": values}, index=days).dropna()
            if len(pair) < 100:
                continue
            r = sps.spearmanr(pair["x"], pair["y"])
            rows.append({"target": target, "feature": feature, "n": len(pair), "rho": r.statistic, "p": r.pvalue})
    table = pd.DataFrame(rows)
    table["q"] = sps.false_discovery_control(table["p"], method="bh")
    return table


# ANALYSIS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def rank_ic(P, window, cadence="daily", days=None) -> tuple[float, float]:
    """
    Mean and standard error of the rank correlation, across param sets, between their PnL over the
    last `window` periods (0 = every earlier one) and their PnL in the next. Weekly sums days into weeks.
    """
    if cadence == "weekly":
        weeks = days.to_period("W-SUN")
        P = np.stack([P[:, weeks == week].sum(1) for week in weeks.unique()], 1)
    ics = [
        sps.spearmanr(P[:, (max(0, i - window) if window else 0) : i].sum(1), P[:, i]).statistic
        for i in range(max(window, 1) + (cadence == "weekly"), P.shape[1])
    ]
    return np.nanmean(ics), np.nanstd(ics) / np.sqrt(len(ics))


def marginals_by_month(P, params, days) -> dict[str, pd.DataFrame]:
    """Each swept param's mean PnL/day per value minus the grid average, per month."""
    grid_avg = P.mean(0)
    months = days.to_period("M")
    out = {}
    for col in params.columns:
        rel = pd.DataFrame(P).groupby(params[col].to_numpy()).mean() - grid_avg
        table = rel.T.groupby(months).mean().T
        table.columns = table.columns.astype(str)
        table["total"] = rel.sum(axis=1)
        out[col] = table.loc[_ordered(table.index)]
    return out


def main(symbol, end_time, eval_start, windows, scores, cadences, regime_features, knn_ks, neighbour_windows):
    conn = study_store.open_store()
    pnl, params = load_grid(conn, symbol, end_time)
    P, days = pnl.to_numpy(), pnl.columns
    eval_days = list(range(eval_start, len(days)))
    fixed_totals = P[:, eval_days].sum(1)
    print(
        f"=== {symbol}: {len(pnl)} param sets ending {end_time} x {len(days)} days ({days[0].date()} to "
        f"{days[-1].date()}), scored on {len(eval_days)} days from {days[eval_start].date()} ==="
    )
    print(f"swept: { {col: _ordered(params[col].unique()) for col in params.columns} }")
    print(
        f"grid average {P[:, eval_days].mean(0).sum():,.0f}; fixed param sets p10 {np.percentile(fixed_totals, 10):,.0f}, "
        f"median {np.median(fixed_totals):,.0f}, p90 {np.percentile(fixed_totals, 90):,.0f}, max {fixed_totals.max():,.0f}"
    )

    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_rows", 500):
        rows = []
        for cadence in cadences:
            for window in windows:
                for kind in scores:
                    # One day has no spread to score anything but its total by
                    if window == 1 and kind != "best":
                        continue
                    daily, picks = walk_forward(P, params, days, eval_days, kind, window, cadence)
                    rows.append(summarize(f"{cadence} {kind}, {_window_name(window)}", daily, P, eval_days, picks))
        rows.append(summarize("grid average", P[:, eval_days].mean(0), P, eval_days))
        rows.append(
            summarize(
                "hindsight best fixed set (look-ahead)", P[int(np.argmax(fixed_totals)), eval_days], P, eval_days, [0]
            )
        )
        rows.append(
            summarize(
                "in-sample best over every day (look-ahead)", P[int(np.argmax(P.sum(1))), eval_days], P, eval_days, [0]
            )
        )
        print("\n=== Walk-forward rules, best total first ===")
        print(pd.DataFrame(rows).sort_values("total", ascending=False).to_string(index=False))

        print("\n=== Neighbouring windows (daily) ===")
        for kind in ("best", "sharpe"):
            totals = {
                w: round(walk_forward(P, params, days, eval_days, kind, w, "daily")[0].sum()) for w in neighbour_windows
            }
            print(f"{kind}: {totals}")

        print("\n=== Rank correlation of param sets' PnL over a window with the next period's ===")
        for cadence, ic_windows in (("daily", [1, 5, 21, 63, 0]), ("weekly", [1, 4, 13, 0])):
            for window in ic_windows:
                mean, se = rank_ic(P, window, cadence, days)
                print(f"{cadence} last {window or 'all'}: {mean:+.3f} +- {se:.3f}")

        half = len(days) // 2
        t_all = (P - P.mean(0)).mean(1) / (P - P.mean(0)).std(1) * np.sqrt(len(days))
        print(
            f"\nOver all days: {(t_all > 2).sum()} of {len(P)} param sets beat the grid average at t > 2 "
            f"(max t {t_all.max():.2f}); first-half vs second-half totals correlate "
            f"{np.corrcoef(P[:, :half].sum(1), P[:, half:].sum(1))[0, 1]:.3f} across sets"
        )
        best = int(np.argmax(P.sum(1)))
        print(
            f"In-sample best over every day: {params.iloc[best].to_dict()} makes {P[best].sum():,.0f} "
            f"vs the grid average's {P.mean(0).sum():,.0f}"
        )

        print("\n=== Each swept param's mean PnL/day minus the grid average, by month ===")
        for col, table in marginals_by_month(P, params, days).items():
            print(f"\n--- {col} ---")
            print(table.round(1).to_string())

        features = load_features(symbol, days)
        spreads = feature_spreads(P, params, days, features)
        print(
            f"\n=== Pre-open features against each param's spread ({len(spreads)} tests, lowest q {spreads['q'].min():.2f}) ==="
        )
        for target, group in spreads.groupby("target", sort=False):
            print(f"\n--- {target} ---")
            print(group.sort_values("p").head(5).round(3).to_string(index=False))

        rows = []
        for feature in regime_features:
            x = features[feature].to_numpy()
            if np.isnan(x).any():
                print(f"NOTE: {feature} is missing on some days; skipped")
                continue
            tag = " (look-ahead)" if feature == "or_bps" else ""
            for n_quantiles in (2, 3):
                for kind in ("best", "additive"):
                    daily = quantile_rule(P, params, x, eval_days, n_quantiles, kind)
                    rows.append(
                        summarize(f"{kind} within {n_quantiles}-quantile of {feature}{tag}", daily, P, eval_days)
                    )
        X = np.log(features[KNN_FEATURES].to_numpy())
        for k in knn_ks:
            rows.append(
                summarize(
                    f"best over {k} nearest days by {', '.join(KNN_FEATURES)}",
                    knn_rule(P, X, eval_days, k),
                    P,
                    eval_days,
                )
            )
        print("\n=== Regime rules (daily), best total first ===")
        print(pd.DataFrame(rows).sort_values("total", ascending=False).drop(columns="n_sets").to_string(index=False))

        terciles = pd.qcut(features["predicted_or_bps"], 3, labels=["low", "mid", "high"])
        print("\n=== Grid average PnL/day by third of predicted_or_bps (all days) ===")
        print(
            pd.Series(P.mean(0), index=days).groupby(terciles, observed=True).agg(["size", "mean"]).round(2).to_string()
        )


if __name__ == "__main__":
    SYMBOL = "AMZN"
    # Only param sets of studies with this backtest end time form the grid
    END_TIME = "09:46:00"
    # The first this many days only train; every rule is scored on the days after them
    EVAL_START = 63
    # Trailing windows in days, PREV_MONTH (the previous calendar month) or ALL_DAYS
    WINDOWS = [1, 5, 10, 21, 42, 63, PREV_MONTH, ALL_DAYS]
    CADENCES = ["daily", "weekly"]
    # Features whose quantiles regime rules pick within. `or_bps` is look-ahead.
    REGIME_FEATURES = ["predicted_or_bps", "pre0400_range_bps", "or_bps"]
    KNN_KS = [20, 40, 80]
    # Windows the daily best and sharpe rules are also run over, to see how much a window's result is noise
    NEIGHBOUR_WINDOWS = [3, 4, 5, 6, 8, 10, 15, 30, 35, 40, 42, 45, 50, 55]

    main(SYMBOL, END_TIME, EVAL_START, WINDOWS, SCORES, CADENCES, REGIME_FEATURES, KNN_KS, NEIGHBOUR_WINDOWS)
