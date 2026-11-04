#!/usr/bin/env python3
"""
Predict AMZN's opening range from what is known before 09:30 ET.

The label (`LABEL`, `or_bps`) is (max_price - min_price) / open_price over 09:30:00-09:31:00 ET, in
bps, where the open price is the first trade from 09:30. Every feature uses only AMZN's trades
before 09:30: today's pre-open, and earlier sessions.

The model is OLS on log(or_bps) (`numpy.linalg.lstsq`), with each always-positive feature logged,
predicting exp() of the fit (the median). It is walk-forward: each scored day is predicted from a fit on the `TRAIN_DAYS`
days before it. Only three quarters are scored. `TUNE_QUARTERS` (2026 Q2 and Q3, pooled) pick the
parameters, and `HOLDOUT_QUARTER` (2026 Q4) is the hold-out. Every earlier day, from 2022, is used
only for fitting.

Tuning: for each window in `TRAIN_DAYS_GRID`, greedy forward selection adds the candidate that most
lowers the tuning quarters' MAE, until none does or `MAX_FEATURES` is reached. The window and
features with the lowest tuning MAE win (`TRAIN_DAYS`, `FEATURES`). The hold-out quarter plays no
part in choosing them.

Candidates (`build_features`):
    pre-open    04:00-09:30 range, realized volatility and volume; 09:25-09:30 range, volume and trade
                count; 09:29-09:30 range and trade count; 09:29:55-09:30 trade count. Volumes, counts
                and some ranges are also taken relative to their trailing `TRAILING_DAYS`-day median
                (`_rel`).
    gap         the absolute and the downward gap of the last pre-open trade from the previous close
    history     the previous opening range and its 5-day mean; the previous session's range and
                realized volatility (and its 22-day mean), and its realized volatility over the first
                10 minutes, the last 30 minutes and after hours
    earnings    `earnings_open`: the first session after an earnings release (`EARNINGS_DATES`)

The 9 tariff and trade-news days of 2025 (`TARIFF_DAYS`) are dropped.

RESULTS (AMZN, fit from 2022-01-03 to 2026-11-02)
==================================================

Scored days: 2026 Q2 62 (median 68 bps), Q3 64 (68 bps), Q4 23 to 2026-11-02 (52 bps, including the
earnings open on 2026-10-30 at 150 bps). Forward selection's result at each window, MAE in bps:

    train_days  features  Q2+Q3 (tuning)  Q4 (hold-out)
            60         4           16.24          22.32
           100         6           16.31          20.95
           140         5           16.61          18.57
           180         7           16.20          16.69   <- chosen (`TRAIN_DAYS`, `FEATURES`)
           220         7           16.46          16.83

The windows are nearly tied on the tuning quarters (16.20-16.61), but the hold-out favours 140-220
days. The chosen model by quarter (trailing mean in brackets): Q2 17.33 (21.03), Q3 15.10 (20.22),
Q4 16.69 +- 2.94 (25.46). Its R^2 (log) is 0.13, 0.32 and 0.32, and its Spearman 0.36, 0.59 and 0.38.
It overpredicts Q4 by 14%, which was calmer than the window it was fit on.

The features in the order chosen. "alone" is the tuning MAE gain of a model with that feature only,
over the trailing mean. "drop" is the MAE lost by dropping it from the 7, on the tuning quarters and
on Q4. "coef" is per standard deviation of its log in the last fit before Q4. Every sign held in
91-100% of the tuning days' fits.

    feature              Spearman  alone   drop: tune  Q4    coef
    prev_last30_rv_bps       0.32   2.08        0.26   1.65  +0.07   previous session's last 30 minutes' RV
    prev_f10_rv_bps          0.32   1.97        0.65   2.74  +0.10   previous session's first 10 minutes' RV
    pre0400_range_bps        0.37   1.67        0.82   0.60  +0.22   today's 04:00-09:30 range
    pre0400_range_rel        0.29   0.41        0.39   0.33  -0.11   the same over its 20-day median
    prev_ah_rv_bps           0.23   1.82        0.22   1.68  +0.05   previous session's after-hours RV
    prev_rth_range_bps       0.19   0.66        0.05   1.44  -0.05   previous session's 09:30-16:00 range
    pre0400_rv_bps           0.34   1.74        0.03   0.12  -0.02   today's 04:00-09:30 RV

The pre-open range is the strongest single input. With its relative version's negative coefficient,
the pair amounts to +0.11 on today's range and +0.11 on its 20-day median, so the normal level of
the pre-open range counts as much as today's. The previous session's realized volatility (first 10
minutes, last 30 minutes, after hours) carries the rest. Each is about as good alone (1.8-2.1 bps)
and they add to each other. The previous session's range has a negative sign: given its volatility,
a wide range means a trending day, followed by a calmer open. It and pre0400_rv_bps add little on
the tuning quarters (0.03-0.05 bps).

Strong alone but not chosen, because they repeat the chosen ones: the previous RTH RV (1.92) and its
22-day mean (1.62), the 09:25-09:30 range (1.73), the previous opening range (1.66) and its 5-day
mean (1.41), and the pre-open volume over its median (1.33). Weak alone (under 0.6 bps): the last
minute's and last 5 seconds' trade counts and range, the 09:25-09:30 volume and trades, and the gap.
`earnings_open` (0.58 alone) has only one earnings open on each tuning quarter. It was chosen at 220
days but not at 180.

Adding `earnings_open` to the 7 by hand doesn't help. At 180 days it changes the MAE by -0.09 +- 0.16 bps
over Q2-Q4 (Q2 17.33 -> 17.18, Q3 15.10 -> 15.45, Q4 16.69 -> 16.74). At 140 days the change is
-0.06 +- 0.08 and at 220 days +0.06 +- 0.13. The 7 features already see the earnings opens coming:
their pre-open ranges are wide. Without the flag they predicted 105, 149 and 144 bps for the opens
on 2026-04-30, 07-31 and 10-30 (actual 130, 124 and 150). With it they predicted 115, 169 and 144.

TRIED BEFORE (scored on 2023-2026 or 2026 before this setup; none helped)
==========================================================================

Each was added to the earlier top-8 features with `earnings_open`:
    - Calendar flags: quarterly option expiry and S&P rebalances, Russell reconstitution, the days
      around AMZN's reports, and the opens after BABA, WMT, GOOG and MSFT earnings, as flags or as
      days left out of training. Each was within +-0.07 bps.
    - Implied volatility: from FRED (VXAZN, VXN, VIX) and from Alpaca's AMZN options bars (implied
      moves to the nearest expiries, straddle, skew, put/call volume and premium, 0DTE share). Each
      was within about +-0.2 bps, with no gain on both 2025 and 2026.
    - The Nasdaq opening-cross imbalance (NOII, from Databento): paired and imbalance shares, side,
      and the reference, near and far prices. Within +-0.1 bps.
    - Quotes: pre-open spreads, depth and quote updates. Trade sizes and odd-lot share. Within +-0.05 bps.
    - News: Benzinga headline counts. These were mostly the earnings opens again, adding 0.01 bps over
      `earnings_open`.
    - Peers (AAPL, GOOG, META and MSFT, and earlier BABA, WMT and TSLA): the median across every
      subset of peers of their pre-open range, volume and trade counts, alone and over their
      trailing median, and their gap. Tuned on 2026 Q3, the peer model cut Q3's MAE to 12.79 bps
      (14.12 with AMZN's own features) but was worse than the trailing mean on Q4 (25.30 vs 23.97).
      Added one at a time, almost every peer feature made Q3 worse, and none helped both Q3 and Q4
      by more than a fraction of a standard error. The best was MSFT's pre-open range (+0.13 bps on
      Q3, +0.91 on Q4).
    - Fitting windows longer than about a year did worse: the opening range's level shifts from year
      to year (median 59, 49, 44, 49 and 66 bps in 2022-2026).

Each run also writes `FEATURES`' predictions for every day from `PREDICT_START` (2026), each from a fit
on the `TRAIN_DAYS` days before it, to ``data/analysis/AMZN_opening_range_predictions.csv``
(`write_predictions`; columns `predicted_or_bps` and `or_bps`).

Edit the values under ``if __name__ == "__main__"`` and run from the repo root:
    python custom/scripts/opening_range_features.py
"""

import os
import sys


sys.path.append(os.getcwd())
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel
from joblib import delayed

from custom.scripts.analyze_optimization_runs import _catalog_files
from custom.scripts.analyze_optimization_runs import _et_ns
from custom.scripts.analyze_optimization_runs import _read_trades
from custom.scripts.analyze_optimization_runs import _window_stats
from custom.utils.paths import data_subdir
from scipy import stats as sps


LABEL = "or_bps"
# Days to take the trailing median over, for values relative to "normal"
TRAILING_DAYS = 20
# A gap longer than this (in calendar days) to the previous catalog day means days are missing,
# so features built on earlier days are left empty rather than taken from stale days.
MAX_PREV_GAP_DAYS = 5
# Bump to invalidate the window-stats cache after changing `WINDOWS`
STATS_VERSION = 8
N_WORKERS = 7

MINUTE, TEN_SECS, SECOND = 60 * 10**9, 10 * 10**9, 10**9
# (prefix, start, end, bar) ET wall-clock windows the trades are boiled down over per day. Realized
# volatilities are taken over the closes of `bar`-long bars. Windows from 09:30 on are only used for
# the label and as earlier sessions.
WINDOWS = [
    ("pre0400", "04:00", "09:30", MINUTE),
    ("pre0925", "09:25", "09:30", TEN_SECS),
    ("pre0929", "09:29", "09:30", SECOND),
    ("pre092955", "09:29:55", "09:30", SECOND),
    ("or1", "09:30", "09:31", SECOND),
    ("f10", "09:30", "09:40", TEN_SECS),
    ("rth", "09:30", "16:00", MINUTE),
    ("last30", "15:30", "16:00", MINUTE),
    ("ah", "16:00", "20:00", MINUTE),
]

# The model chosen by the last tuning run (see the module docstring)
TRAIN_DAYS = 180
FEATURES = [
    "prev_last30_rv_bps",
    "prev_f10_rv_bps",
    "pre0400_range_bps",
    "pre0400_range_rel",
    "prev_ah_rv_bps",
    "prev_rth_range_bps",
    "pre0400_rv_bps",
]

# AMZN's quarterly earnings release dates, from the filing dates of its 8-K Item 2.02 (Results of
# Operations) on SEC EDGAR (CIK 0001018724), https://data.sec.gov/submissions/CIK0001018724.json.
# All accepted 16:05-16:27 ET, after the close. Add each new quarter by hand.
EARNINGS_DATES = [
    "2022-02-03", "2022-04-28", "2022-07-28", "2022-10-27",
    "2023-02-02", "2023-04-27", "2023-08-03", "2023-10-26",
    "2024-02-01", "2024-04-30", "2024-08-01", "2024-10-31",
    "2025-02-06", "2025-05-01", "2025-07-31", "2025-10-30",
    "2026-02-05", "2026-04-29", "2026-07-30", "2026-10-29",
]

# Days dropped from the dataset: AMZN's tariff and trade-news days, from the "Tariffs and trade"
# table of custom/claude_plans/amzn-high-volume-days.md. Market-wide shocks no feature here could
# see coming. 2026-06-29, which also had a tariff threat, is left in: Prime Day's record sales were
# the bigger news.
TARIFF_DAYS = [
    "2025-03-04", "2025-04-03", "2025-04-04", "2025-04-07", "2025-04-08",
    "2025-04-09", "2025-05-12", "2025-05-13", "2025-10-10",
]


# WINDOW STATS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def window_stats(day: str, trade_path: Path) -> dict:
    """One day's per-window trade stats, from its own catalog file only."""
    ts, price, size = _read_trades(trade_path)
    out = {"day": day, "stats_version": STATS_VERSION}
    for prefix, start, end, bar_ns in WINDOWS:
        out |= _window_stats(prefix, ts, price, size, _et_ns(day, start), _et_ns(day, end), bar_ns)
    return out


def load_window_stats(symbol: str, venue: str, refresh: bool = False) -> pd.DataFrame:
    """
    `window_stats` for every catalog day of `symbol`, indexed by day.

    Cached in ``data/analysis/<SYMBOL>_opening_range_stats.parquet``; only days missing from the cache
    (or computed by an older `STATS_VERSION`) are read from the catalog.
    """
    cache_path = Path(data_subdir("analysis")) / f"{symbol}_opening_range_stats.parquet"
    trade_files = _catalog_files("trade_tick", symbol, venue)
    if not trade_files:
        raise SystemExit(f"No {symbol}.{venue} trade files in the catalog")

    cached = pd.DataFrame()
    if cache_path.exists() and not refresh:
        cached = pd.read_parquet(cache_path)
        cached = cached[cached["stats_version"] == STATS_VERSION]
    todo = [day for day in sorted(trade_files) if day not in set(cached.get("day", []))]
    if todo:
        print(f"Reading {len(todo)} day(s) of {symbol} ticks from the catalog...")
        fresh = pd.DataFrame(Parallel(n_jobs=N_WORKERS)(delayed(window_stats)(day, trade_files[day]) for day in todo))
        cached = pd.concat([cached, fresh], ignore_index=True).drop_duplicates("day", keep="last")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cached.sort_values("day").to_parquet(cache_path, index=False)

    out = cached[cached["day"].isin(trade_files)].copy()
    out.index = pd.to_datetime(out.pop("day"))
    return out.drop(columns="stats_version").sort_index()


# LABEL AND FEATURES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _range_bps(s: pd.DataFrame, prefix: str, base: pd.Series) -> pd.Series:
    return (s[f"{prefix}_high"] - s[f"{prefix}_low"]) / base * 1e4


def _vs_trailing(series: pd.Series) -> pd.Series:
    """Each value over the median of the `TRAILING_DAYS` values before it."""
    return series / series.shift(1).rolling(TRAILING_DAYS, min_periods=5).median()


def _stale(s: pd.DataFrame) -> pd.Series:
    """Days whose previous catalog day is too long before to use."""
    return pd.Series(s.index, index=s.index).diff().dt.days > MAX_PREV_GAP_DAYS


def _opening_range_bps(s: pd.DataFrame) -> pd.Series:
    """(high - low) / open over 09:30-09:31, in bps (empty without trades in that minute)."""
    return _range_bps(s, "or1", s["or1_first"])


def build_label(s: pd.DataFrame) -> pd.Series:
    return _opening_range_bps(s).rename(LABEL)


def earnings_open(index: pd.DatetimeIndex) -> pd.Series:
    """1 on the first session of `index` to open after each of AMZN's earnings releases, else 0."""
    reports = pd.DatetimeIndex(EARNINGS_DATES)
    flag = pd.Series(0.0, index=index, name="earnings_open")
    for report, pos in zip(reports, index.searchsorted(reports, side="right"), strict=True):
        if pos < len(index) and (index[pos] - report).days <= MAX_PREV_GAP_DAYS:
            flag.iloc[pos] = 1.0
    return flag


def build_features(s: pd.DataFrame) -> pd.DataFrame:
    """The candidate features per day, from trades before 09:30 and earlier sessions."""
    prev = s.shift(1)
    last = s["pre0400_last"]
    f = pd.DataFrame(index=s.index)

    # Today's pre-open. Counts and volumes get +1 so that a window without trades stays positive.
    f["pre0400_range_bps"] = _range_bps(s, "pre0400", last)
    f["pre0400_range_rel"] = _vs_trailing(f["pre0400_range_bps"])
    f["pre0400_rv_bps"] = s["pre0400_rv_bps"]
    f["pre0400_volume_rel"] = _vs_trailing(s["pre0400_volume"] + 1)
    f["pre0925_range_bps"] = _range_bps(s, "pre0925", last)
    f["pre0925_range_rel"] = _vs_trailing(f["pre0925_range_bps"])
    f["pre0925_volume_rel"] = _vs_trailing(s["pre0925_volume"] + 1)
    f["pre0925_trades_rel"] = _vs_trailing(s["pre0925_trades"] + 1)
    f["pre0929_range_bps"] = _range_bps(s, "pre0929", last)
    f["pre0929_range_rel"] = _vs_trailing(f["pre0929_range_bps"])
    f["pre0929_trades"] = s["pre0929_trades"] + 1
    f["pre0929_trades_rel"] = _vs_trailing(s["pre0929_trades"] + 1)
    f["pre092955_trades_rel"] = _vs_trailing(s["pre092955_trades"] + 1)

    # The gap from the previous close
    f["gap_abs_bps"] = (last / prev["rth_last"] - 1).abs() * 1e4
    f["gap_down_bps"] = (1 - last / prev["rth_last"]).clip(lower=0) * 1e4

    # Earlier sessions
    opening_range = _opening_range_bps(s)
    f["or_prev1"] = opening_range.shift(1)
    f["or_ma5"] = opening_range.shift(1).rolling(5, min_periods=3).mean()
    f["prev_rth_range_bps"] = _range_bps(prev, "rth", prev["rth_first"])
    f["prev_rth_rv_bps"] = prev["rth_rv_bps"]
    f["prev_rth_rv_ma22"] = prev["rth_rv_bps"].rolling(22, min_periods=10).mean()
    f["prev_f10_rv_bps"] = prev["f10_rv_bps"]
    f["prev_last30_rv_bps"] = prev["last30_rv_bps"]
    f["prev_ah_rv_bps"] = prev["ah_rv_bps"]
    f.loc[_stale(s), [c for c in f.columns if c.startswith(("gap_", "or_", "prev_"))]] = np.nan

    f["earnings_open"] = earnings_open(s.index)
    return f.replace([np.inf, -np.inf], np.nan)


def build_dataset(symbol: str, venue: str, refresh: bool) -> pd.DataFrame:
    """The label and every candidate per day, without the tariff days."""
    s = load_window_stats(symbol, venue, refresh)
    df = build_features(s).join(build_label(s))
    df = df[df[LABEL].notna() & (df[LABEL] > 0)]
    return df[~df.index.isin(pd.DatetimeIndex(TARIFF_DAYS))]


# MODELS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


class Design:
    """
    The candidates as a matrix, each logged where it is always positive, and log(label). Models are
    lists of feature names, looked up as column positions in it.
    """

    def __init__(self, df: pd.DataFrame, candidates: list[str]):
        self.index = df.index
        self.col = {name: j for j, name in enumerate(candidates)}
        self.logged = set()
        z = np.empty((len(df), len(candidates)))
        for j, name in enumerate(candidates):
            x = df[name].to_numpy(float)
            if (x[np.isfinite(x)] > 0).all():
                x = np.log(x)
                self.logged.add(name)
            z[:, j] = x
        self.z = z
        self.ylog = np.log(df[LABEL].to_numpy(float))

    def cols(self, names: list[str]) -> list[int]:
        return [self.col[n] for n in names]


def fit(z: np.ndarray, ylog: np.ndarray, cols: list[int], rows: np.ndarray) -> np.ndarray:
    """OLS coefficients (intercept first) of log(label) on `cols` over `rows`."""
    a = np.column_stack([np.ones(len(rows)), z[np.ix_(rows, cols)]])
    return np.linalg.lstsq(a, ylog[rows], rcond=None)[0]


def predict(z: np.ndarray, ylog: np.ndarray, cols: list[int], positions: np.ndarray, train_days: int | None) -> np.ndarray:
    """
    The prediction for each row in `positions`, from an OLS fit of log(label) on the `cols` of the
    `train_days` rows before it (every earlier row if None) that have all of them. No `cols` is the
    trailing mean of log(label).
    """
    rows = np.flatnonzero(np.isfinite(z[:, cols]).all(axis=1) & np.isfinite(ylog))
    out = np.empty(len(positions))
    for k, p in enumerate(positions):
        end = np.searchsorted(rows, p)
        train = rows[0 if train_days is None else max(0, end - train_days) : end]
        if cols:
            beta = fit(z, ylog, cols, train)
            out[k] = beta[0] + z[p, cols] @ beta[1:]
        else:
            out[k] = ylog[train].mean()
    return np.exp(out)


def _predict_many(z, ylog, models, positions, train_days):
    return [predict(z, ylog, cols, positions, train_days) for cols in models]


def walk_forward(d: Design, models: dict[str, list[str]], positions: np.ndarray, train_days: int | None) -> pd.DataFrame:
    """Each model's (name -> features) prediction on the rows of `positions`."""
    names, cols = list(models), [d.cols(m) for m in models.values()]
    chunks = [list(range(len(cols)))[i :: N_WORKERS] for i in range(min(N_WORKERS, len(cols)))]
    results = Parallel(n_jobs=len(chunks))(delayed(_predict_many)(d.z, d.ylog, [cols[i] for i in c], positions, train_days) for c in chunks)
    preds = {}
    for chunk, result in zip(chunks, results, strict=True):
        preds |= {names[i]: r for i, r in zip(chunk, result, strict=True)}
    return pd.DataFrame({n: preds[n] for n in names}, index=d.index[positions])


def mae(preds: pd.DataFrame, actual: pd.Series, quarters: list[str]) -> pd.Series:
    """Each model's MAE (bps) over the days of `quarters`."""
    days = preds.index.to_period("Q").isin([pd.Period(q) for q in quarters])
    return preds[days].sub(actual.loc[preds.index[days]], axis=0).abs().mean()


def score(pred: pd.Series, actual: pd.Series) -> dict:
    actual = actual.loc[pred.index]
    err = (pred - actual).abs()
    log_a = np.log(actual)
    return {
        "days": len(pred),
        "mae_bps": err.mean(),
        "mae_se": err.std(ddof=1) / np.sqrt(len(err)),
        "r2_log": 1 - ((log_a - np.log(pred)) ** 2).sum() / ((log_a - log_a.mean()) ** 2).sum(),
        "spearman": sps.spearmanr(pred, actual).statistic,
        "bias_pct": (np.exp(np.mean(np.log(pred) - log_a)) - 1) * 100,
    }


def forward_select(d, candidates, positions, actual, train_days, tune_quarters, holdout_quarter, max_features):
    """
    Greedy forward selection from the trailing mean: each step adds the candidate that most lowers
    the MAE over `tune_quarters`, until none does or `max_features` are chosen. Returns each step as
    (features, tuning MAE, hold-out MAE); the hold-out MAE is only reported.
    """
    chosen = []
    preds = walk_forward(d, {"base": []}, positions, train_days)
    steps = [([], mae(preds, actual, tune_quarters)["base"], mae(preds, actual, [holdout_quarter])["base"])]
    while len(chosen) < max_features:
        preds = walk_forward(d, {c: [*chosen, c] for c in candidates if c not in chosen}, positions, train_days)
        tune = mae(preds, actual, tune_quarters)
        best = tune.idxmin()
        if tune[best] >= steps[-1][1]:
            break
        chosen.append(best)
        steps.append((list(chosen), tune[best], mae(preds[[best]], actual, [holdout_quarter])[best]))
    return steps


def feature_summary(d, df, candidates, chosen, positions, train_days, tune_quarters, holdout_quarter) -> pd.DataFrame:
    """
    For each candidate: its Spearman with the label and the MAE of a model with it alone, over the
    tuning quarters. For each chosen feature also: the MAE lost by dropping it from the chosen model
    (tuning and hold-out), its coefficient on the standardized (logged) feature in a fit on the
    `train_days` days before the hold-out, and the share of the tuning days' fits with that sign.
    """
    actual = df[LABEL]
    tune_days = df.index.to_period("Q").isin([pd.Period(q) for q in tune_quarters])
    alone = mae(walk_forward(d, {c: [c] for c in candidates} | {"none": []}, positions, train_days), actual, tune_quarters)
    rows = []
    for c in candidates:
        pair = df.loc[tune_days, [c, LABEL]].dropna()
        rows.append({"feature": c, "spearman": sps.spearmanr(pair[c], pair[LABEL]).statistic, "alone_gain": alone["none"] - alone[c]})
    table = pd.DataFrame(rows).set_index("feature")

    drop = walk_forward(d, {"all": chosen} | {c: [x for x in chosen if x != c] for c in chosen}, positions, train_days)
    for quarters, name in [(tune_quarters, "drop_loss_tune"), ([holdout_quarter], "drop_loss_holdout")]:
        m = mae(drop, actual, quarters)
        table.loc[chosen, name] = (m[chosen] - m["all"]).to_numpy()

    cols = d.cols(chosen)
    valid = np.flatnonzero(np.isfinite(d.z[:, cols]).all(axis=1))
    holdout_start = np.flatnonzero(df.index.to_period("Q") == pd.Period(holdout_quarter))[0]
    last = valid[valid < holdout_start][-train_days if train_days else 0 :]
    sd = d.z[np.ix_(last, cols)].std(axis=0)
    table.loc[chosen, "coef_std"] = fit(d.z, d.ylog, cols, last)[1:] * sd
    signs = []
    for p in np.flatnonzero(tune_days):
        before = valid[valid < p]
        signs.append(np.sign(fit(d.z, d.ylog, cols, before[-train_days if train_days else 0 :])[1:]))
    table.loc[chosen, "sign_kept"] = (np.array(signs) == np.sign(table.loc[chosen, "coef_std"].to_numpy())).mean(axis=0)
    table.loc[chosen, "step"] = range(1, len(chosen) + 1)
    table["logged"] = [c in d.logged for c in table.index]
    return table.sort_values(["step", "alone_gain"], ascending=[True, False], na_position="last")


def write_predictions(symbol: str, df: pd.DataFrame, d: Design, start: str) -> Path:
    """
    `FEATURES`' prediction for every day from `start` that has all of them, each from an OLS fit on
    the `TRAIN_DAYS` days before it, written with the actual `LABEL` to
    ``data/analysis/<SYMBOL>_opening_range_predictions.csv``.
    """
    has_features = np.isfinite(d.z[:, d.cols(FEATURES)]).all(axis=1)
    positions = np.flatnonzero((df.index >= start) & has_features)
    pred = predict(d.z, d.ylog, d.cols(FEATURES), positions, TRAIN_DAYS)
    out = pd.DataFrame({"predicted_or_bps": pred, LABEL: df[LABEL].iloc[positions].to_numpy()}, index=df.index[positions].rename("day"))
    out_path = Path(data_subdir("analysis")) / f"{symbol}_opening_range_predictions.csv"
    out.round(3).to_csv(out_path)
    print(f"\n{len(out)} predictions from {out.index.min().date()} to {out.index.max().date()} (MAE {(out['predicted_or_bps'] - out[LABEL]).abs().mean():.2f} bps) written to {out_path}")
    return out_path


# MAIN ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def main(symbol, venue, tune_quarters, holdout_quarter, grid, max_features, refresh_stats, predict_start):
    df = build_dataset(symbol, venue, refresh_stats)
    candidates = [c for c in df.columns if c != LABEL]
    d = Design(df, candidates)
    actual = df[LABEL]
    quarters = df.index.to_period("Q")
    all_quarters = [*tune_quarters, holdout_quarter]
    positions = np.flatnonzero(quarters.isin([pd.Period(q) for q in all_quarters]) & np.isfinite(d.z).all(axis=1))
    print(f"=== {symbol}: {len(df)} days from {df.index.min().date()} to {df.index.max().date()}; {len(candidates)} candidates ===")
    for quarter in all_quarters:
        days = actual.iloc[positions[quarters[positions] == pd.Period(quarter)]]
        role = "hold-out" if quarter == holdout_quarter else "tuning"
        print(f"   {quarter} ({role}): {len(days)} days ({days.index.min().date()} to {days.index.max().date()}), median {LABEL} {days.median():.1f} bps")
    tune_label = "+".join(tune_quarters)

    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_rows", 500):
        print(f"\n=== Forward selection on {tune_label} MAE, by fitting window ===")
        print(f"   {'train_days':>10s} {'features':>8s} {'tune MAE':>9s} {'hold-out MAE':>13s}   features")
        results = {}
        for train_days in grid:
            steps = forward_select(d, candidates, positions, actual, train_days, tune_quarters, holdout_quarter, max_features)
            results[train_days] = steps
            feats, mae_t, mae_h = steps[-1]
            print(f"   {str(train_days):>10s} {len(feats):8d} {mae_t:9.2f} {mae_h:13.2f}   {', '.join(feats)}", flush=True)
        best_days = min(results, key=lambda k: results[k][-1][1])
        chosen = results[best_days][-1][0]

        print(f"\n--- Steps at train_days={best_days} (the lowest {tune_label} MAE) ---")
        for feats, mae_t, mae_h in results[best_days]:
            print(f"   {len(feats):2d} {'+ ' + feats[-1] if feats else 'trailing mean':28s} tune {mae_t:6.2f}   hold-out {mae_h:6.2f}")

        print(f"\n=== Features at train_days={best_days} ===")
        print(
            "   spearman: with the label over the tuning days; alone_gain: tuning MAE gain of a model with it alone\n"
            "   over the trailing mean; drop_loss: MAE lost by dropping it from the chosen model; coef_std: its\n"
            "   coefficient per standard deviation (of its log if logged) in the last fit before the hold-out;\n"
            "   sign_kept: share of the tuning days' fits with that sign"
        )
        summary = feature_summary(d, df, candidates, chosen, positions, best_days, tune_quarters, holdout_quarter)
        with pd.option_context("display.float_format", "{:,.3f}".format):
            print(summary.to_string())

        models = {("trailing mean", best_days): [], ("selected", best_days): chosen}
        if FEATURES and (FEATURES != chosen or TRAIN_DAYS != best_days):
            models[("FEATURES (saved)", TRAIN_DAYS)] = FEATURES
        rows = []
        for (name, train_days), feats in models.items():
            pred = walk_forward(d, {name: feats}, positions, train_days)[name]
            for quarter in all_quarters:
                in_q = pred.index.to_period("Q") == pd.Period(quarter)
                rows.append({"model": name, "quarter": quarter, "train_days": train_days, "features": len(feats), **score(pred[in_q], actual)})
        print("\n=== Models by quarter ===")
        with pd.option_context("display.float_format", "{:,.3f}".format):
            print(pd.DataFrame(rows).set_index(["model", "quarter"]).to_string())
        print(f"\nSelected (train_days={best_days}): {chosen}")

    out_path = Path(data_subdir("analysis")) / f"{symbol}_opening_range_features.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path)
    print(f"\nPer-day label and features written to {out_path}")
    write_predictions(symbol, df, d, predict_start)


if __name__ == "__main__":
    SYMBOL = "AMZN"
    VENUE = "ALPACA"
    # Parameters are chosen on these quarters' MAE (pooled); the hold-out quarter is only scored
    TUNE_QUARTERS = ["2026Q2", "2026Q3"]
    HOLDOUT_QUARTER = "2026Q4"
    # Fitting windows tried, in days with every feature of the model (None for every earlier day)
    TRAIN_DAYS_GRID = [60, 100, 140, 180, 220]
    # Forward selection steps at most
    MAX_FEATURES = 10
    # Re-read every day from the catalog rather than using cached window stats
    REFRESH_STATS = False
    # `FEATURES`' predictions are written for every day from here (`write_predictions`)
    PREDICT_START = "2026-01-01"

    main(SYMBOL, VENUE, TUNE_QUARTERS, HOLDOUT_QUARTER, TRAIN_DAYS_GRID, MAX_FEATURES, REFRESH_STATS, PREDICT_START)
