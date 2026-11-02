#!/usr/bin/env python3
"""
Predict a symbol's opening range for each day from a model trained only on the days before it.

The opening range, the model and the features are those of `opening_range_features`: OLS on
log(or_bps) over the logs of its top 8 features. For each day from `START` on, the model is refit on
the earlier days in its training window (the last `TRAIN_DAYS` catalog days, from `TRAIN_START`)
that have the label and all 8 features, then predicts that day from its features.

The peer features need every peer's catalog data, which may not reach back as far as the symbol's
(AMZN's and AAPL's catalogs start in 2025, the other peers' in 2026). Where the full model has fewer than `MIN_TRAIN`
training days, or the day's peer features are missing, the day is predicted by the same model
without the peer features, which can train on the days before the peers' data starts. `n_features`
says which model predicted each day.

Every feature is known by 09:30 and built only from that day's pre-open and earlier days, so no
prediction sees its own day's opening range or anything later. A day is predicted even when its own
label is missing, as long as its features are present.

The prediction is exp() of the fitted log, so it estimates the median opening range rather than the
mean; `n_train` gives the number of days each was fit on.

TRAINING WINDOW (AMZN, predicting 2026-01-02 to 2026-10-27)
=============================================================

MAE in bps over the last month (from 2026-09-28), the last 3 months (from 2026-07-27), since March
and all of 2026:

    full model from          window     last month  last 3m  since Mar  2026
    20 days of 2026          expanding       11.83    13.06      16.53  17.57
    60 days of 2026          expanding       11.83    13.06      16.18  16.91
    60 days of 2026          250 days        11.83    13.06      16.17  16.89  <- used
    80 days of 2026          250 days        11.83    13.06      16.16  16.88
    100 days of 2026         250 days        11.83    13.06      16.67  17.29
    never (no peers)         250 days        11.72    13.06      16.82  17.41
    never (no peers)         200 days        12.32    13.02      16.56  17.23
    never (no peers)         300 days        12.13    13.34      17.19  17.72
    never (no peers)         expanding       12.56    14.35      17.90  18.29

The full model can only train on days with peer data, so a window of 200+ days is the same as an
expanding one for it; shorter windows did worse (last month: 60 days 13.0, 120 days 12.4, 160 days
12.0). The window matters for the model without peers, which trains on 2025: 250 days (about a year)
is best on the last month, while expanding keeps all of 2025's narrower ranges and predicts 2026
about 11% too low. Recency weights (half-lives of 60-250 days) were no better than the plain window.
Waiting for 60 days of 2026 before switching to the full model helps February and March (MAE 30.7
-> 26.4 and 22.7 -> 20.0) and doesn't touch the months after. Filling the peer features' missing
2025 days with 1 (a normal day) was erratic in January, where the peer coefficients are fit on a
handful of days. The differences of under ~0.7 bps are within one standard error.

With AAPL's 2025 data, the full model could train on 2025 using AAPL alone for the peer features:

    peer features                          window     last month  last 3m  since Mar  2026
    all 4 peers, 2026 only (used)          250 days        11.75    13.08      16.10  16.83
    AAPL in 2025, all 4 peers in 2026      160 days        11.97    12.90      16.43  17.00
    AAPL in 2025, all 4 peers in 2026      250 days        11.54    12.77      16.71  17.35
    AAPL in 2025, all 4 peers in 2026      expanding       12.47    14.18      17.84  18.29
    AAPL alone, both years                 250 days        12.38    13.07      16.78  17.41
    AAPL alone, both years                 expanding       12.96    14.32      17.90  18.32

The 250-day window with AAPL in 2025 is slightly better on the last 3 months but worse on everything
before (since March +0.6 +- 0.4 bps), and AAPL alone throughout is worse everywhere: one peer is a
noisier gauge of market-wide activity than the median of four. So the peer features stay empty on
days missing any peer, and the window results above are unchanged. Recency weights did no better.

Writes data/analysis/<SYMBOL>_opening_range_predictions.csv. Edit the values under
``if __name__ == "__main__"`` and run from the repo root:
    python custom/scripts/opening_range_predictions.py
"""

import os
import sys


sys.path.append(os.getcwd())
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from sklearn.metrics import r2_score

from custom.scripts.opening_range_features import FEATURES
from custom.scripts.opening_range_features import LABEL
from custom.scripts.opening_range_features import build_features
from custom.scripts.opening_range_features import build_label
from custom.scripts.opening_range_features import build_peer_features
from custom.scripts.opening_range_features import load_window_stats
from custom.scripts.opening_range_features import make_model
from custom.utils.paths import data_subdir
from scipy import stats as sps


def load_dataset(symbol, venue, peers, features, refresh_stats) -> pd.DataFrame:
    """Every catalog day's `features` and label (the label may be missing)."""
    stats = load_window_stats(symbol, venue, refresh_stats)
    label = build_label(stats)
    own = build_features(stats)
    peer = build_peer_features(peers, venue, refresh_stats, own.index)
    return own.join(peer)[features].join(label)


def predict_each_day(
    df: pd.DataFrame,
    feature_sets: list[list[str]],
    start: str,
    train_start: str | None,
    train_days: int | None,
    min_train: int,
) -> pd.DataFrame:
    """
    For each day from `start`, the prediction of the first of `feature_sets` that the day has all
    its features for (present and positive), and that has at least `min_train` training days: the
    earlier days within the last `train_days` (all of them if None), from `train_start`, with the
    label and all those features. Days no feature set can predict are left unpredicted.
    """
    in_range = df.index >= train_start if train_start is not None else np.ones(len(df), bool)
    usable = {
        i: df[features].notna().all(axis=1) & (df[features] > 0).all(axis=1)
        for i, features in enumerate(feature_sets)
    }

    rows = []
    for pos in np.flatnonzero(df.index >= start):
        day = df.index[pos]
        window = np.zeros(len(df), bool)
        window[0 if train_days is None else max(0, pos - train_days) : pos] = True
        row = {"day": day, "n_features": 0, "n_train": 0, f"predicted_{LABEL}": np.nan, LABEL: df.at[day, LABEL]}
        for i, features in enumerate(feature_sets):
            train = df[window & in_range & usable[i] & df[LABEL].notna()]
            if usable[i][day] and len(train) >= min_train:
                model = make_model([f"log_{f}" for f in features]).fit(train[features], train[LABEL])
                row |= {
                    "n_features": len(features),
                    "n_train": len(train),
                    f"predicted_{LABEL}": model.predict(df.loc[[day], features])[0],
                }
                break
        rows.append(row)
    return pd.DataFrame(rows).set_index("day")


def main(symbol, venue, peers, features, start, train_start, train_days, min_train, refresh_stats):
    df = load_dataset(symbol, venue, peers, features, refresh_stats)
    feature_sets = [features, [f for f in features if not f.startswith("peer_")]]
    preds = predict_each_day(df, feature_sets, start, train_start, train_days, min_train)

    pred_col = f"predicted_{LABEL}"
    print(f"=== {symbol}: {len(preds)} days from {preds.index.min().date()} to {preds.index.max().date()} ===")
    unpredicted = preds.index[preds[pred_col].isna()]
    if len(unpredicted):
        print(f"No prediction for {len(unpredicted)} day(s) (features missing or too few training days):")
        for day in unpredicted:
            print(f"  {day.date()}")
    fallback = preds.index[preds["n_features"].between(1, len(features) - 1)]
    if len(fallback):
        print(
            f"{len(fallback)} day(s) predicted without the peer features, from {fallback.min().date()} "
            f"to {fallback.max().date()}"
        )

    scored = preds.dropna()
    by_month = scored.groupby(scored.index.to_period("M"))
    summary = pd.DataFrame(
        {
            "days": by_month.size(),
            "days_without_peers": by_month["n_features"].apply(lambda n: (n < len(features)).sum()),
            "min_n_train": by_month["n_train"].min(),
            "mae_bps": by_month.apply(lambda g: mean_absolute_error(g[LABEL], g[pred_col])),
            "mean_actual_bps": by_month[LABEL].mean(),
            "mean_predicted_bps": by_month[pred_col].mean(),
        }
    )
    summary.loc["all"] = [
        len(scored),
        (scored["n_features"] < len(features)).sum(),
        scored["n_train"].min(),
        mean_absolute_error(scored[LABEL], scored[pred_col]),
        scored[LABEL].mean(),
        scored[pred_col].mean(),
    ]
    with pd.option_context("display.float_format", "{:,.2f}".format):
        print(f"\n=== Out-of-sample error by month ===\n{summary.to_string()}")
    print(
        f"\nAll {len(scored)} scored days: R^2 (log) "
        f"{r2_score(np.log(scored[LABEL]), np.log(scored[pred_col])):.3f}, "
        f"Spearman {sps.spearmanr(scored[pred_col], scored[LABEL]).statistic:.3f}"
    )

    out_path = Path(data_subdir("analysis")) / f"{symbol}_opening_range_predictions.csv"
    preds.round({pred_col: 2, LABEL: 2}).to_csv(out_path)
    print(f"\nPer-day predictions written to {out_path}")


if __name__ == "__main__":
    SYMBOL = "AMZN"
    VENUE = "ALPACA"
    # Must match the peers `opening_range_features` ranked the features with
    PEERS = ["AAPL", "GOOG", "MSFT", "TSLA"]
    # The top 8 features of `opening_range_features`, all fit on their logs
    MODEL_FEATURES = FEATURES[:8]
    # First day to predict
    START = "2026-01-01"
    # First day trained on (None for the start of the catalog)
    TRAIN_START = None
    # Catalog days before each day in its training window (None for all of them). 2025's opening
    # ranges ran narrower than the features explain, so a window of about a year lets them age out;
    # see the module docstring.
    TRAIN_DAYS = 250
    # Fewest training days a model is fit on; below this the full model gives way to the one without
    # the peer features, and that one leaves the day unpredicted
    MIN_TRAIN = 60
    # Re-read every day from the catalog rather than using cached window stats
    REFRESH_STATS = False

    main(SYMBOL, VENUE, PEERS, MODEL_FEATURES, START, TRAIN_START, TRAIN_DAYS, MIN_TRAIN, REFRESH_STATS)
