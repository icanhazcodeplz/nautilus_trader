#!/usr/bin/env python3
"""
Predict a symbol's late opening range, 09:30:20 to 09:31, from what is known by 09:30:15.

The label is (max_price - min_price) / reference over 09:30:20-09:31:00, in bps, where the
reference is the last trade before 09:30:15. Every feature uses only trades before 09:30:15 (on the
day itself) and earlier days, leaving 5 seconds to act on a prediction before the label window opens.

Builds on `opening_range_features`, which predicts the whole first minute from the pre-open:
    - its window stats (pre-open, RTH, last 30 minutes, after hours) and its prior-day features are
      reused as they are, alongside new stats for the first 15 seconds after the open
    - OLS on log(label), with features logged where always positive
    - training on 2026 only: 2025's opening ranges ran narrower than the features explain
    - peer features are the median across peers of each peer's value over its own trailing median,
      and are empty on days missing any peer
Unlike there, features are chosen by greedy forward selection on the days before `HOLDOUT_START`
only, and the models are then scored on the held-out days from `HOLDOUT_START`, which played no
part in choosing them.

RESULTS (AMZN, 2026-01-02 to 2026-10-28)
========================================

The late opening range is narrower than the whole first minute's (median 34.5 bps vs 65; 10%-90%
22-55 bps) and right-skewed like it.

Forward selection over 27 candidates, scored on 119 days (2026-04 to 2026-09-25, each predicted
from a fit on every earlier 2026 day), chose:

    rank  feature                      R^2 (log)  Spearman alone
       1  prev_ah_rv_bps                   0.162            0.29
       2  early_range_bps                  0.238            0.36
       3  pre0400_range_bps                0.271            0.32
       4  prev_last30_rv_bps               0.288            0.27
       5  peer_pre0400_volume_rel          0.296            0.21
       6  peer_early_range_rel             0.299            0.14
       7  e10_volume_rel                   0.306            0.28
       8  prev_rth_rv_ma22                 0.313            0.27
       9  peer_pre0400_range_bps_rel       0.308  (falls from here)
      10  e10_range_bps                    0.304

The first 15 seconds help, though less than might be expected: early_range_bps is the strongest
feature alone (Spearman 0.36), but the pre-open and the previous session already carry much of what
it says. On the selection days, the 8 selected features reach R^2 (log) 0.31 and MAE 9.4 bps, against
0.16 and 10.5 bps for `opening_range_features`' pre-open top 8, and 11.2 bps for the trailing mean.
Since these features were chosen on the same days, those numbers flatter them.

On the 23 held-out days (2026-09-28 to 2026-10-28), every model does badly: R^2 (log) is negative and
Spearman 0.0-0.17. That month was calm and its late ranges varied little, so every model
overpredicts it (by 8-12%, the trailing mean by 30%). MAE: trailing mean 9.3, pre-open top 8 7.7,
selected top 4 7.4, top 8 8.0 bps. 23 days can't tell these apart, so top 4 being best there is noise.

Weak or no help (Spearman alone in brackets): the opening's distance from the last pre-open trade
(0.03), the open's gap from the previous close (0.15, but 0.40 Pearson on logs), the 09:25-09:30
range (0.18), the peers' first-15-second range (0.14, picked only as a correction alongside AMZN's
own), and earlier days' late opening range (0.22-0.25).

Edit the values under ``if __name__ == "__main__"`` and run from the repo root:
    python custom/scripts/late_opening_range_features.py
"""

import os
import sys


sys.path.append(os.getcwd())
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel
from joblib import delayed
from sklearn.metrics import mean_absolute_error
from sklearn.metrics import r2_score

from custom.scripts import opening_range_features as orf
from custom.scripts.analyze_optimization_runs import _catalog_files
from custom.scripts.analyze_optimization_runs import _et_ns
from custom.scripts.analyze_optimization_runs import _read_trades
from custom.scripts.analyze_optimization_runs import _window_stats
from custom.utils.paths import data_subdir
from scipy import stats as sps


# Features use trades before this; the label window opens at LABEL_START
DECISION = "09:30:15"
LABEL_START, LABEL_END = "09:30:20", "09:31:00"
# Bump to invalidate the window-stats cache after changing `window_stats`
STATS_VERSION = 1

# (prefix, start, end, bar) ET wall-clock windows the trades are boiled down over per day, on top of
# those of `opening_range_features`. Realized volatilities are taken over the closes of `bar`-long bars.
WINDOWS = [
    ("early", "09:30:00", DECISION, orf.SECOND),
    # The first 15 seconds without the opening cross and the seconds right after it
    ("e10", "09:30:05", DECISION, orf.SECOND),
    ("pre0925", "09:25", "09:30", orf.TEN_SECS),
    ("late", LABEL_START, LABEL_END, orf.SECOND),
]

LABEL = "late_or_bps"
# In the order forward selection chose them; see the module docstring
FEATURES = [
    "prev_ah_rv_bps",
    "early_range_bps",
    "pre0400_range_bps",
    "prev_last30_rv_bps",
    "peer_pre0400_volume_rel",
    "peer_early_range_rel",
    "e10_volume_rel",
    "prev_rth_rv_ma22",
]


# WINDOW STATS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def window_stats(day: str, trade_path: Path) -> dict:
    """One day's per-window trade stats, from its own catalog file only."""
    ts, price, size = _read_trades(trade_path)
    out = {"day": day, "stats_version": STATS_VERSION}
    for prefix, start, end, bar_ns in WINDOWS:
        out |= _window_stats(prefix, ts, price, size, _et_ns(day, start), _et_ns(day, end), bar_ns)
    return out


def load_window_stats(symbol, venue, refresh=False) -> pd.DataFrame:
    """
    This script's window stats for every catalog day of `symbol`, joined to those of
    `opening_range_features`.

    Cached per symbol; only days missing from the cache (or computed by an older `STATS_VERSION`)
    are read from the catalog.
    """
    cache_path = Path(data_subdir("analysis")) / f"{symbol}_late_opening_range_stats.parquet"
    trade_files = _catalog_files("trade_tick", symbol, venue)
    if not trade_files:
        raise SystemExit(f"No {symbol}.{venue} trade files in the catalog")

    cached = pd.DataFrame()
    if cache_path.exists() and not refresh:
        cached = pd.read_parquet(cache_path)
        cached = cached[cached["stats_version"] == STATS_VERSION]
    todo = [day for day in sorted(trade_files) if day not in set(cached.get("day", []))]
    if todo:
        print(f"Reading {len(todo)} day(s) of {symbol} ticks from the catalog for the late opening range...")
        fresh = pd.DataFrame(
            Parallel(n_jobs=orf.N_WORKERS)(delayed(window_stats)(day, trade_files[day]) for day in todo)
        )
        cached = pd.concat([cached, fresh], ignore_index=True).drop_duplicates("day", keep="last")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cached.sort_values("day").to_parquet(cache_path, index=False)

    out = cached[cached["day"].isin(trade_files)].copy()
    out.index = pd.to_datetime(out.pop("day"))
    out = out.drop(columns="stats_version").sort_index()
    # Its cache can hold columns of windows it has since dropped, which may share a name with ours
    others = orf.load_window_stats(symbol, venue, refresh).drop(columns="stats_version")
    return out.join(others.drop(columns=out.columns, errors="ignore"))


# LABEL AND FEATURES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def build_label(s: pd.DataFrame) -> pd.Series:
    """The late opening range: (high - low) over 09:30:20-09:31 / the last trade before 09:30:15, in bps."""
    return orf._range_bps(s, "late", s["early_last"]).rename(LABEL)


def _early_range_bps(s: pd.DataFrame) -> pd.Series:
    """The range from the open to 09:30:15 over the opening trade."""
    return orf._range_bps(s, "early", s["early_first"])


def build_features(s: pd.DataFrame, label: pd.Series) -> pd.DataFrame:
    """
    The symbol's own features per day, all known by 09:30:15: those of `opening_range_features`
    (pre-open and earlier days), plus the first 15 seconds after the open and its history.
    """
    f = orf.build_features(s)
    prev = s.shift(1)
    stale = pd.Series(s.index, index=s.index).diff().dt.days > orf.MAX_PREV_GAP_DAYS
    early_range = _early_range_bps(s)

    # The first 15 seconds
    f["early_range_bps"] = early_range
    f["early_range_rel"] = orf._vs_trailing(early_range)
    f["early_rv_bps"] = s["early_rv_bps"]
    f["early_volume_rel"] = orf._vs_trailing(s["early_volume"])
    f["early_trades_rel"] = orf._vs_trailing(s["early_trades"])
    f["early_move_bps"] = (s["early_last"] / s["early_first"] - 1).abs() * 1e4
    f["e10_range_bps"] = orf._range_bps(s, "e10", s["early_first"])
    f["e10_rv_bps"] = s["e10_rv_bps"]
    f["e10_volume_rel"] = orf._vs_trailing(s["e10_volume"])
    # Where it opened
    f["open_gap_bps"] = (s["early_first"] / prev["rth_last"] - 1).abs() * 1e4
    f["open_vs_preopen_bps"] = (s["early_first"] / s["pre0400_last"] - 1).abs() * 1e4
    # The last 5 minutes before the open
    f["pre0925_range_bps"] = orf._range_bps(s, "pre0925", s["pre0400_last"])
    f["pre0925_volume_rel"] = orf._vs_trailing(s["pre0925_volume"])
    # Earlier days' late opening range
    f["late_or_prev1"] = label.shift(1)
    f["late_or_ma5"] = label.shift(1).rolling(5, min_periods=3).mean()

    f.loc[stale, ["open_gap_bps", "late_or_prev1", "late_or_ma5"]] = np.nan
    return f


def build_peer_features(peers, venue, refresh, index) -> pd.DataFrame:
    """
    The peers' pre-open (from `opening_range_features`) and first 15 seconds on the days of
    `index`: each peer's value over its own trailing median, then the median across peers. Empty on
    days missing any peer.
    """
    per_peer = []
    for peer in peers:
        s = load_window_stats(peer, venue, refresh)
        per_peer.append(
            pd.DataFrame(
                {
                    "peer_early_range_rel": orf._vs_trailing(_early_range_bps(s)),
                    "peer_early_volume_rel": orf._vs_trailing(s["early_volume"]),
                }
            )
        )
    by_day = pd.concat(per_peer).groupby(level=0)
    early = by_day.median().where(by_day.count() == len(peers)).reindex(index)
    return early.join(orf.build_peer_features(peers, venue, refresh, index))


# MODELS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def model_columns(df: pd.DataFrame, features: list[str]) -> list[str]:
    """`features` as `make_model` columns: logged (`log_<feature>`) where always positive in `df`."""
    return [f"log_{f}" if (df[f] > 0).all() else f for f in features]


def _predict_each_day(data: pd.DataFrame, columns: list[str], min_train: int) -> list[float]:
    X, y = data.drop(columns=LABEL), data[LABEL]
    model = orf.make_model(columns)
    return [model.fit(X.iloc[:i], y.iloc[:i]).predict(X.iloc[[i]])[0] for i in range(min_train, len(data))]


def walk_forward(data: pd.DataFrame, models: dict[str, list[str]], min_train: int) -> pd.DataFrame:
    """
    Each model's prediction for every day of `data` after the first `min_train`, from a fit on every
    earlier day. `models` maps a name to `make_model` columns; `data` must have them all present.
    """
    preds = Parallel(n_jobs=orf.N_WORKERS)(
        delayed(_predict_each_day)(data, columns, min_train) for columns in models.values()
    )
    return pd.DataFrame(dict(zip(models, preds, strict=True)), index=data.index[min_train:])


def score(preds: pd.DataFrame, actual: pd.Series) -> pd.DataFrame:
    """Each model's out-of-sample error on the days of `preds`."""
    actual = actual.loc[preds.index]
    rows = []
    for name, pred in preds.items():
        rows.append(
            {
                "model": name,
                "n_test": len(pred),
                "mae_bps": mean_absolute_error(actual, pred),
                "oos_r2_log": r2_score(np.log(actual), np.log(pred)),
                "spearman_rho": sps.spearmanr(pred, actual).statistic,
                "bias_pct": (np.exp(np.mean(np.log(pred) - np.log(actual))) - 1) * 100,
            }
        )
    return pd.DataFrame(rows).set_index("model")


def forward_select(data: pd.DataFrame, candidates: list[str], min_train: int, select_end, max_features: int):
    """
    Greedy forward selection: each step adds the candidate that most raises the walk-forward
    out-of-sample R^2 (log) on the days up to `select_end`. Returns the chosen features in order and
    each step's R^2.
    """
    selection = data[data.index <= select_end]
    columns = dict(zip(candidates, model_columns(data, candidates), strict=True))
    chosen, r2s = [], []
    for _ in range(max_features):
        models = {c: [columns[f] for f in chosen + [c]] for c in candidates if c not in chosen}
        r2 = score(walk_forward(selection, models, min_train), selection[LABEL])["oos_r2_log"]
        chosen.append(r2.idxmax())
        r2s.append(r2.max())
        print(f"  {len(chosen):2d} + {chosen[-1]:28s} R^2 (log) {r2s[-1]:.3f}", flush=True)
    return chosen, r2s


# MAIN ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def main(symbol, venue, peers, train_start, holdout_start, min_train, max_features, top_quintiles, refresh_stats):
    stats = load_window_stats(symbol, venue, refresh_stats)
    label = build_label(stats)
    features = build_features(stats, label)
    features = features.join(build_peer_features(peers, venue, refresh_stats, features.index))
    candidates = list(features.columns)
    df = features.join(label)
    df = df[(df.index >= train_start) & df[LABEL].notna() & (df[LABEL] > 0)]
    print(
        f"=== {symbol}: {len(df)} days with a late opening range ({df.index.min().date()} to "
        f"{df.index.max().date()}), peers {', '.join(peers)} ==="
    )

    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_rows", 500):
        print(f"\n=== {LABEL} ({LABEL_START}-{LABEL_END}) ===")
        print(df[LABEL].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]).round(2).to_string())

        table = orf.correlate(df, candidates, LABEL)
        print(f"\n=== Correlation with {LABEL}, strongest first ===")
        with pd.option_context("display.float_format", "{:,.3f}".format):
            print(table.to_string())
        for name in table.index[:top_quintiles]:
            print(f"\n--- {name} quintiles ---")
            print(orf.quintiles(df, name, LABEL).round(2).to_string())

        data = df[candidates + [LABEL]].dropna()
        print(
            f"\n=== Forward selection on {(data.index < holdout_start).sum()} days before {holdout_start} "
            f"({min_train} to fit first), on the {len(data)} days with every candidate ==="
        )
        chosen, _ = forward_select(data, candidates, min_train, pd.Timestamp(holdout_start) - pd.Timedelta(days=1), max_features)

        columns = dict(zip(candidates, model_columns(data, candidates), strict=True))
        prior = [f for f in orf.FEATURES[:8] if f in candidates]
        models = {"trailing_mean": [], "opening_range_features top8 (pre-open only)": [columns[f] for f in prior]}
        models |= {f"selected top{k}": [columns[f] for f in chosen[:k]] for k in range(1, len(chosen) + 1)}
        preds = walk_forward(data, models, min_train)
        for name, days in [
            (f"selection days (to {holdout_start})", preds.index < holdout_start),
            (f"held-out days (from {holdout_start})", preds.index >= holdout_start),
        ]:
            print(f"\n=== Walk-forward OLS on log({LABEL}), {name} ===")
            with pd.option_context("display.float_format", "{:,.3f}".format):
                print(score(preds[days], data[LABEL]).to_string())

    out_path = Path(data_subdir("analysis")) / f"{symbol}_late_opening_range_features.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path)
    print(f"\nPer-day label and features written to {out_path}")


if __name__ == "__main__":
    SYMBOL = "AMZN"
    VENUE = "ALPACA"
    # Other catalog symbols whose open gauges market-wide activity
    PEERS = ["AAPL", "GOOG", "MSFT", "TSLA"]
    # First day used. 2025's opening ranges ran narrower; see `opening_range_features`.
    TRAIN_START = "2026-01-01"
    # Days from here are held out of feature selection and only scored
    HOLDOUT_START = "2026-09-28"
    # Days the first walk-forward fit is made on
    MIN_TRAIN = 60
    # Forward selection steps
    MAX_FEATURES = 10
    # Quintile tables printed for this many of the strongest features
    TOP_QUINTILES = 5
    # Re-read every day from the catalog rather than using cached window stats
    REFRESH_STATS = False

    main(SYMBOL, VENUE, PEERS, TRAIN_START, HOLDOUT_START, MIN_TRAIN, MAX_FEATURES, TOP_QUINTILES, REFRESH_STATS)
