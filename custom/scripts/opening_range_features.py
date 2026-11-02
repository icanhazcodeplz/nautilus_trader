#!/usr/bin/env python3
"""
Classify whether a symbol rises more than `THRESHOLD_BPS` in the first 30 seconds after the open.

The up-move is (max_price - open_price) / open_price over 09:30:00-09:30:30, in bps, where the open
price is the first trade from 09:30. The label is 1 when it is over `THRESHOLD_BPS` (25), else 0.
Every feature is known by 09:30 except those named `atopen_...`, which need the opening trade and
are left out of forward selection by default.

The models are walk-forward logistic regressions on standardized features (logged where always
positive), refit daily on every earlier day from `TRAIN_START` and scored by log loss against the
training days' base rate. Features are chosen by greedy forward selection on those same days, so
their scores flatter them; the chosen features are then checked on the days before `TRAIN_START`
(2025), which played no part in choosing them.

CLASSIFIER RESULTS (AMZN, 2025-01-02 to 2026-10-28)
===================================================

The up-move is over 25 bps on 49% of 2026's days and 32% of 2025's. Nothing tried predicts it
reliably better than the base rate.

35 candidates: the 10 size-of-move features chosen for the opening range (below), and 25 new ones,
mostly signed: the gap, after-hours and overnight returns, the 04:00/09:00/09:25 pre-open trends,
where the last pre-open trade sits in the 04:00 and 09:25 ranges, the tick-rule buy/sell imbalance
of the 04:00 and 09:25 pre-open trades, the 09:25-09:30 quote size imbalance (mean, last minute,
last quote), the last quote's mid against the last trade, the previous session's RTH, last-30-minute,
first-10-minute and close-to-close returns, earlier days' up-move and rate over 25 bps, the peers'
median gap, pre-open trends and previous RTH return, and the opening trade against the last
pre-open trade (`atopen_`).

AUC alone, 2026 (2025): the only one with a Benjamini-Hochberg q under 0.1 is pre0400_range_bps 0.66
(0.54). Next are prev_f10_ret_bps 0.61 (0.52), prev_rth_rv_ma22 0.60 (0.51) and prev_rth_ret_bps 0.58
(0.62). prev_rth_ret_bps, yesterday's open-to-close return, is the one signed feature that holds up
in both years: up days tend to be followed by a bigger up-move at the open. Every pre-open
directional feature (gap, trends, positions, tick and quote imbalances, peers' gap and trends, and
the opening trade against the last pre-open trade) is 0.44-0.57, with no consistent side across years.

Forward selection on 2026 (142 days scored) chose prev_rth_rv_ma22, peer_prev_rth_ret_bps,
pre0925_position and q_last_mid_vs_last_bps: log loss 0.697 -> 0.670, AUC 0.64, accuracy 0.59
against 0.51. It doesn't hold up. q_last_mid_vs_last_bps, which made the biggest step, has AUC 0.51
in 2025, and the same model (without the peer feature) does worse than the base rate there (log
loss 0.655 vs 0.629, AUC 0.47). Small models of the features that look stable do no better:
pre0400_range_bps alone has AUC 0.60 on 2026 but a worse log loss than the base rate (0.704 vs
0.697); prev_rth_ret_bps alone beats the base rate on 2025 (0.618 vs 0.629, AUC 0.58) but not on
2026 (AUC 0.53); the two together beat it on neither.

MORE HISTORY, EARNINGS AND NEWS (AMZN, 2022-01-03 to 2026-10-30)
==================================================================

AMZN's catalog now starts in 2022 (1212 days); the peers' still start in 2025 (AAPL) and 2026. The
earnings dates are `EARNINGS_DATES`, from SEC EDGAR; the news was Alpaca's Benzinga headlines
(~3,000 AMZN headlines a year, steady since 2022; the download and feature code were removed). Scored walk-forward on
2023-2026 (952 days), each day fit on the 250 days before it, on the 6 range features without the
peers. The year-to-year level shifts seen with 2025 hold across all five years (median opening range
59, 49, 44, 49 and 66 bps; up-move over 25 bps on 43%, 37%, 26%, 32% and 49% of days), and a longer
window does worse: MAE 14.86 bps with 250 days, 15.31 with 500, 15.57 expanding (2026: 17.86, 19.51,
20.56).

Earnings: the 20 opens after a report had a median opening range of 127 bps (2.1x the trailing
22-day median; 51 on other days), and the model without them under-predicts them by a median 1.5x.
`earnings_open` (the first session after a report) cuts their MAE from 39 to 28 bps and helps every
year (+0.30, +0.03, +0.18, +0.15 bps; +0.16 +- 0.11 overall). Fitting it as a separate multiplier
from the earlier reports' residuals did worse (34 bps on those days, +0.08 overall). It can't be
judged on the 2026 model with the peers yet: 2 reports in its scored days. The report day itself
(`earnings_day`, whose open is before the report) adds nothing. Neither helps the up-move
classifier: earnings opens are as likely to fall as rise (AUC 0.50-0.52 every year).
`earnings_day` was picked 2nd by forward selection on 2026, on 4 events, but has AUC ~0.5 in every
other year.

News, as log(1 + count) known by 09:25: overnight headlines (since the previous 16:00) have
Spearman 0.07-0.16 with the opening range, the focused ones (tagged with at most 2 symbols)
0.08-0.14. The focused count is the better feature: +0.14 +- 0.09 bps, and the same sign in every
year (+0.29, +0.08, +0.05, +0.16). But it's mostly the earnings opens again: on top of
`earnings_open` it adds 0.01 bps, and away from earnings its Spearman with the residual is only
0.03-0.10. The pre-market count, the previous RTH's count and the overnight count over its trailing
median add nothing. Weekends bring more overnight news but no wider opens. For the classifier the news
features' AUCs are 0.42-0.60, flipping side between years, except the previous RTH's (0.51-0.54).

With five years the up-move classifier has a held-out result: `pre0400_range_bps` alone, fit on
every earlier day, beats the base rate in each of 2023-2026 (log loss 0.650 vs 0.664, 0.592 vs
0.607, 0.628 vs 0.630, 0.701 vs 0.727; AUC 0.54-0.64), and the opening range's top 3 features beat
it over all 4 years (0.639 vs 0.654, +0.015 +- 0.008). It's the only feature whose AUC is over 0.54
in all five years (0.54-0.65); prev_rth_rv_ma22 (0.51-0.60) and or_prev1 (0.53-0.59) are next.
Much of that is the volatility regime moving the base rate between years. Of the signed features,
only prev_f10_ret_bps keeps its side in all five years, and barely (0.52 in 2022-2025, 0.62 in 2026).

OPTIONS FEATURES (AMZN, 2025-01-02 to 2026-10-28; `OPTIONS`, from `options_features`)
=====================================================================================

Alpaca's options bars (no quotes, open interest or greeks) were downloaded by
`alpaca_options_download`. Options trade only in RTH, so every feature is the previous session's: the
implied move per session to the nearest expiry (Black-Scholes IV at the money from the last 30
minutes' 1-minute VWAPs, over the sessions to expiry), to the next expiry, and the forward move between
them; their ratio (an event priced into the nearest expiry); the ATM straddle; skew at half a standard
deviation; put/call volume and premium (near-term near-the-money, and every contract within 70
days of expiry); tick-rule premium flow of calls against puts; the OTM call share of OTM volume;
volume relative to its trailing median; 0DTE share; and the implied move over realized volatility and
over today's pre-open range. None of them helps either label.

Up-move classifier: by AUC alone, the only options features on the same side in both years are the
last 30 minutes' put/call volume, 0.58 (0.58), and the OTM call share, 0.42 (0.41); more puts into the
close, a bigger up-move. Neither beats the base rate walk-forward in either year (log loss 0.712 vs
0.697 on 2026, 0.652 vs 0.649 on 2025), since the relation shifts within each year. The implied move
over the pre-open range has AUC 0.375 (0.439), but it's pre0400_range_bps inverted. Forward selection
on 2026 took it 4th, after prev_rth_rv_ma22, prev_f10_rv_bps and peer_prev_rth_ret_bps (log loss
0.6793 -> 0.6790).

Opening range: the implied moves have Spearman 0.20-0.26 with it in 2026 and 0.25-0.32 in 2025, under
pre0400_range_bps (0.33, 0.35), and they add nothing to the 8 range features: each made the
walk-forward MAE 0.08-0.19 bps worse on 2026 (137 days), and up to 0.2 +- 0.18 bps better on 2025
without the peers. No options feature cut the MAE on both 2025 and 2026 by more than one standard
error; the best on 2026, put/call premium (+0.39 +- 0.21 bps), was +0.05 without the peers and under
the base model on 2025. VXAZN's failure (below) isn't from its 30-day horizon: implied volatility to
the nearest expiry, 1-4 sessions out, does no better.

Earnings are the one thing the options know. The implied move to the nearest expiry runs 2-4x the
forward move all week before a report, so on its own it doesn't single out the open after it. On
the six opens after reports where the nearest expiry was the next session (`opt_next_event_ratio`
3.1-4.2; under 1.6 on every other day), the opening range was 61-157 bps (median 56), and the
opening-range model without the peers predicted the five after its first fit 1.1-2.2x too low
(2025-05-02 153 vs 71, 2026-02-06 157 vs 79). Six events are too few to fit a coefficient on, and
adding it as a feature changed nothing.

UP-MOVE AS A NUMBER (regression on log(up-move + 5 bps), before the classifier)
================================================================================

The up-move is small and spread wide: median 19 bps (24.5 in 2026, 15 in 2025), 10%-90% 2-53 bps,
and 0 on 9 of 457 days. It is far harder to predict than the opening range. Each of the 10 features
has Spearman 0.16 or less with it (pre0400_range_bps 0.16, or_prev1 0.13, prev_rth_rv_ma22 0.12;
the peer features and gap_down_bps ~0), against 0.21-0.34 with the range. Walk-forward on 2026 (first
fit on 60 days, 142 scored), no model beats the trailing mean (MAE 18.4 bps): top3 18.4, top8 18.9,
all with negative out-of-sample R^2 (log). Training on 2025 as well, without the peer features, the
top 3 reach Spearman 0.19 and R^2 (log) 0.015 (MAE 16.9 vs 17.2 for the trailing mean, 327 days).
Most of the range's predictability is lost because the up-move is also a bet on the direction of
the first 30 seconds, which none of these features (all sizes of moves, none signed except
gap_down_bps) speak to. The offset barely matters: 0.5-20 bps change MAE by under 0.6 bps; 5-10
score best.

OPENING RANGE FEATURES (AMZN, 2026-01-02 to 2026-10-26)
===============================================

The features below were chosen for an earlier label, the opening range: (max_price - min_price) /
open_price over the first minute, in bps. In this section, "the label" means that range.

48 candidate features were ranked by greedy forward selection: starting from the trailing-mean
baseline, each step added the feature that most raised the walk-forward out-of-sample R^2 of an
OLS fit on log(label) (refit daily on every earlier day, first fit on 60 days, scored on 135 days
with every feature present). Positive features were logged. The 10 kept, in the order chosen
(rank 8 was later replaced; see 2025 below):

    rank  feature                       R^2 gain  loss if dropped  Spearman  coef sign
       1  prev_f10_rv_bps                 +0.153           0.039     0.30       +
       2  pre0400_range_bps               +0.068           0.040     0.34       +
       3  prev_rth_rv_ma22                +0.039           0.020     0.31       +
       4  or_prev1                        +0.010           0.007     0.32       +
       5  prev_rth_range_bps              +0.011           0.012     0.07       -
       6  peer_pre0400_volume_rel         +0.001           0.019     0.21       +
       7  peer_pre0400_range_bps_rel      +0.018           0.018     0.07       -
       8  prev_last30_rv_bps              +0.032                     0.33       +
       9  prev_ah_rv_bps                  -0.002           0.005     0.27       +
      10  gap_down_bps                    -0.003          -0.003     0.05       +

    R^2 gain        -- the out-of-sample R^2 (log) added when forward selection picked it
    loss if dropped -- the R^2 lost by taking it out of the 10-feature model
    Spearman        -- its rank correlation with the label on its own, over all days
    coef sign       -- its sign in the 10-feature model

Out-of-sample R^2 (log): trailing mean -0.01; 3 features 0.25; 7 features 0.29; 8 features 0.33;
all 10 features 0.32. The gains stop after 8: ranks 9-10 add nothing and are kept only to make 10. OLS on all 48 features overfits badly (R^2 -0.72); RidgeCV on all 48 gets 0.20. The
features were chosen by their score on the same days they're scored on, so these R^2s overstate
what to expect on new days.

The two negative signs only help alongside the others. Given realized volatility, a wide range
yesterday means a smooth trending day, which tends to be followed by a calmer open. Given AMZN's own
pre-open range, peers moving as much means the move was market-wide rather than AMZN's own.

Tried and removed (Spearman with the label in brackets). Most are strong on their own but
redundant with the 10 kept, since nearly every volatility measure tracks the others:
    realized volatility   yesterday's RTH RV (0.32) and its 5-day mean (0.36, the strongest
                          alone); downside RV of yesterday (0.30) and of the pre-market (0.33);
                          pre-market RV from 04:00 (0.32), 09:00 (0.31) and 09:25 (0.29)
    opening range history 3-day mean (0.33), 5-day mean (0.30), same weekday over 4 weeks (0.13)
    pre-open trades       09:25 and 09:00 ranges (0.29, 0.26); 09:25, 09:00 and 04:00 volumes
                          (0.21-0.27) and the same relative to their trailing median
                          (0.22-0.26); 09:25 trade count (0.24)
    pre-open quotes       09:25 quote updates (0.29); 09:25 and 09:00 median spread (0.01, 0.03);
                          signed 09:25 quote imbalance (-0.02) and its absolute value (0.03, the
                          original rank 8, which added nothing)
    prev day              RTH and full-session volume (0.18, 0.19) and relative RTH volume (0.03);
                          3-, 5- and 10-day means of the RTH range (0.16-0.25)
    gap                   signed gap (-0.01), absolute gap (0.23), up part (0.07)
    peers (median of      09:25 range (0.14), 04:00 RV (0.11), 09:25 relative volume (0.19),
      AAPL/GOOG/MSFT/     absolute gap (0.11), opening range 5-day mean (0.19)
      TSLA, each over its
      own trailing median)
    calendar              day of week (-0.02)
    implied volatility    yesterday's close from FRED of Cboe's VXAZN (0.06), VXN (0.26) and VIX
                          (0.11), their daily changes (~0), VXAZN over VXN (-0.16) and over
                          trailing RV (-0.09). VXAZN tracks AMZN's earnings cycle, not day-to-day
                          volatility, and made the models worse; the FRED code was removed.
    events (not used as   AMZN's earnings reactions and the session after are all in the top
      features)           10% of days (median 130 and 106 bps vs 64), as are 8 of the 9 days
                          when AMZN's pre-market volume was over 3x normal. The 8:30 CPI, jobs and
                          PPI releases, the open after FOMC, and monthly option expiry make no
                          difference. log(label) is nearly symmetric (skew 0.18 vs 1.58).

ADDING 2025 (only AMZN and AAPL have 2025 data, so the peer features are empty for 2025)
===========================================================================================

2025's opening ranges were narrower (median 49 bps vs 65 in 2026), and the features don't explain
the difference: models trained on 2025 and 2026 predicted 2026 about 11% too low, and did worse
than training on 2026 alone (also tried: rolling 120/250-day windows and exponential recency
weights, none better). So the models train on 2026 only. The features' Spearman with the label is
similar in both years, except the multi-day means (opening range and RTH RV), which were weaker in
2025.

With the last month (2026-09-28 to 2026-10-27) as the target, scored on it, the last 3 months and
since March, ~50 additions, removals, recency weightings and residual corrections of the top-7
model were tried. Only one helped everywhere: prev_last30_rv_bps, the RV of the previous session's
last 30 minutes (a window not in the original 48). It cut the last month's MAE from 12.9 to 11.7 bps
(Spearman 0.30 -> 0.50) and the last 3 months' from 13.9 to 13.1, and also helped on 2025, which
played no part in choosing it (MAE 14.95 -> 14.77 bps over Apr-Dec 2025). It replaced rank 8.
All models overpredict the calm last month (by ~10%); a bias correction from recent residuals
helped it but hurt the 3 months before, so it isn't used.

Trades come straight from the catalog's parquet files, with the readers
`analyze_optimization_runs` uses. Each day's files are boiled down to per-window stats in parallel
with joblib, which are cached, and the features are derived across days from those.

The AUC table carries Mann-Whitney p-values and Benjamini-Hochberg q-values for the training year.

Edit the values under ``if __name__ == "__main__"`` and run from the repo root:
    python custom/scripts/opening_range_features.py
"""

import os
import sys


sys.path.append(os.getcwd())
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from joblib import Parallel
from joblib import delayed
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.metrics import log_loss
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer
from sklearn.preprocessing import StandardScaler

from custom.scripts.analyze_optimization_runs import _catalog_files
from custom.scripts.analyze_optimization_runs import _decode_fixed
from custom.scripts.analyze_optimization_runs import _et_ns
from custom.scripts.analyze_optimization_runs import _read_trades
from custom.scripts.analyze_optimization_runs import _window_stats
from custom.scripts.options_features import load_options_state
from custom.utils.paths import data_subdir
from scipy import stats as sps


# Days to take the trailing median over, for values relative to "normal"
TRAILING_DAYS = 20
# A gap longer than this (in calendar days) to the previous catalog day means days are missing,
# so features built on earlier days are left empty rather than taken from stale days.
MAX_PREV_GAP_DAYS = 5
# Bump to invalidate the window-stats cache after changing `window_stats`
STATS_VERSION = 6
N_WORKERS = 7

MINUTE, TEN_SECS, SECOND = 60 * 10**9, 10 * 10**9, 10**9
# (prefix, start, end, bar) ET wall-clock windows the trades are boiled down over per day.
# Realized volatilities are taken over the closes of `bar`-long bars.
WINDOWS = [
    ("or1", "09:30", "09:31", SECOND),
    ("f30", "09:30", "09:30:30", SECOND),
    ("f10", "09:30", "09:40", TEN_SECS),
    ("pre0400", "04:00", "09:30", MINUTE),
    ("pre0900", "09:00", "09:30", MINUTE),
    ("pre0925", "09:25", "09:30", TEN_SECS),
    ("rth", "09:30", "16:00", MINUTE),
    ("last30", "15:30", "16:00", MINUTE),
    ("ah", "16:00", "20:00", MINUTE),
]
# Pre-open windows whose buy/sell imbalance is estimated with the tick rule
TICK_IMBALANCE_WINDOWS = ["pre0400", "pre0925"]

UP30 = "up30_bps"
# The up-move threshold the classifier predicts
THRESHOLD_BPS = 25
LABEL = f"up30_gt{THRESHOLD_BPS}"
# Inverse L2 strength of the logistic regression, on standardized features
REG_C = 1.0
# In the order forward selection chose them on 2026; they don't hold up on 2025 (see the module docstring)
FEATURES = ["prev_rth_rv_ma22", "peer_prev_rth_ret_bps", "pre0925_position", "q_last_mid_vs_last_bps"]
# Features of `opening_range_features`' earlier label, the opening range; see the module docstring
RANGE_FEATURES = [
    "prev_f10_rv_bps",
    "pre0400_range_bps",
    "prev_rth_rv_ma22",
    "or_prev1",
    "prev_rth_range_bps",
    "peer_pre0400_volume_rel",
    "peer_pre0400_range_bps_rel",
    "prev_last30_rv_bps",
    "prev_ah_rv_bps",
    "gap_down_bps",
]
# Features known only once the opening trade prints, after 09:30. Left out of forward selection
# unless `allow_at_open`.
AT_OPEN_PREFIX = "atopen_"

# Quarterly earnings release dates, from the filing dates of each company's 8-K Item 2.02 (Results of
# Operations) on SEC EDGAR, https://data.sec.gov/submissions/CIK<10-digit CIK>.json. Every AMZN
# release was accepted 16:05-16:27 ET, after the close, so the market reacts at the next open. Add
# each new quarter by hand.
EARNINGS_DATES = {
    # CIK 0001018724
    "AMZN": [
        "2022-02-03", "2022-04-28", "2022-07-28", "2022-10-27",
        "2023-02-02", "2023-04-27", "2023-08-03", "2023-10-26",
        "2024-02-01", "2024-04-30", "2024-08-01", "2024-10-31",
        "2025-02-06", "2025-05-01", "2025-07-31", "2025-10-30",
        "2026-02-05", "2026-04-29", "2026-07-30", "2026-10-29",
    ],
}


# WINDOW STATS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _tick_imbalance(ts, price, size, start_ns, end_ns) -> float:
    """
    (buy - sell) / total volume between `start_ns` and `end_ns`, signing each trade by the tick
    rule: an uptick is a buy, a downtick a sell, and an unchanged price keeps the last sign.
    """
    lo, hi = np.searchsorted(ts, [start_ns, end_ns])
    p, v = price[lo:hi], size[lo:hi]
    if len(p) < 2:
        return np.nan
    sign = pd.Series(np.sign(np.diff(p))).replace(0, np.nan).ffill().fillna(0).to_numpy()
    return float((sign * v[1:]).sum() / v[1:].sum())


def quote_stats(day: str, quote_path: Path | None) -> dict:
    """The 09:25-09:30 quotes' size imbalance, (bid size - ask size) / depth, and the last quote."""
    if quote_path is None:
        return {}
    q = pq.read_table(
        quote_path,
        columns=["bid_price", "ask_price", "bid_size", "ask_size", "ts_event"],
        filters=[("ts_event", ">=", _et_ns(day, "09:25")), ("ts_event", "<", _et_ns(day, "09:30"))],
    )
    if q.num_rows == 0:
        return {}
    ts = q.column("ts_event").to_numpy()
    bid, ask = _decode_fixed(q.column("bid_price")), _decode_fixed(q.column("ask_price"))
    bid_size, ask_size = _decode_fixed(q.column("bid_size")), _decode_fixed(q.column("ask_size"))
    depth = bid_size + ask_size
    valid = (bid > 0) & (ask >= bid) & (depth > 0)
    if not valid.any():
        return {}
    imbalance = np.where(valid, (bid_size - ask_size) / np.where(depth > 0, depth, 1), np.nan)
    last_minute = valid & (ts >= _et_ns(day, "09:29"))
    last = np.flatnonzero(valid)[-1]
    return {
        "q0925_imbalance": float(np.nanmean(imbalance)),
        "q0929_imbalance": float(np.nanmean(imbalance[last_minute])) if last_minute.any() else np.nan,
        "q_last_imbalance": float(imbalance[last]),
        "q_last_mid": float((bid[last] + ask[last]) / 2),
    }


def window_stats(day: str, trade_path: Path, quote_path: Path | None) -> dict:
    """One day's per-window trade and quote stats, from its own catalog files only."""
    ts, price, size = _read_trades(trade_path)
    out = {"day": day, "stats_version": STATS_VERSION}
    for prefix, start, end, bar_ns in WINDOWS:
        out |= _window_stats(prefix, ts, price, size, _et_ns(day, start), _et_ns(day, end), bar_ns)
    for prefix in TICK_IMBALANCE_WINDOWS:
        _, start, end, _ = next(w for w in WINDOWS if w[0] == prefix)
        out[f"{prefix}_tick_imbalance"] = _tick_imbalance(ts, price, size, _et_ns(day, start), _et_ns(day, end))
    return out | quote_stats(day, quote_path)


def load_window_stats(symbol, venue, refresh=False) -> pd.DataFrame:
    """
    Window stats for every catalog day of `symbol`.

    Cached per symbol; only days missing from the cache (or computed by an older `STATS_VERSION`)
    are read from the catalog.
    """
    cache_path = Path(data_subdir("analysis")) / f"{symbol}_opening_range_stats.parquet"
    trade_files = _catalog_files("trade_tick", symbol, venue)
    quote_files = _catalog_files("quote_tick", symbol, venue)
    if not trade_files:
        raise SystemExit(f"No {symbol}.{venue} trade files in the catalog")

    cached = pd.DataFrame()
    if cache_path.exists() and not refresh:
        cached = pd.read_parquet(cache_path)
        cached = cached[cached["stats_version"] == STATS_VERSION]
    todo = [day for day in sorted(trade_files) if day not in set(cached.get("day", []))]
    if todo:
        print(f"Reading {len(todo)} day(s) of {symbol} ticks from the catalog...")
        fresh = pd.DataFrame(
            Parallel(n_jobs=N_WORKERS)(
                delayed(window_stats)(day, trade_files[day], quote_files.get(day)) for day in todo
            )
        )
        cached = pd.concat([cached, fresh], ignore_index=True).drop_duplicates("day", keep="last")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cached.sort_values("day").to_parquet(cache_path, index=False)

    out = cached[cached["day"].isin(trade_files)].copy()
    out.index = pd.to_datetime(out.pop("day"))
    return out.sort_index()


# LABEL AND FEATURES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _range_bps(s: pd.DataFrame, prefix: str, base: pd.Series) -> pd.Series:
    return (s[f"{prefix}_high"] - s[f"{prefix}_low"]) / base * 1e4


def _ret_bps(end: pd.Series, start: pd.Series) -> pd.Series:
    return (end / start - 1) * 1e4


def _vs_trailing(series: pd.Series) -> pd.Series:
    """Each value over the median of the `TRAILING_DAYS` values before it."""
    return series / series.shift(1).rolling(TRAILING_DAYS, min_periods=5).median()


def _pre0400_range_bps(s: pd.DataFrame) -> pd.Series:
    """The 04:00-09:30 range over the last pre-open trade."""
    return _range_bps(s, "pre0400", s["pre0400_last"])


def _opening_range_bps(s: pd.DataFrame) -> pd.Series:
    """The opening range: (high - low) / open over the first minute after 09:30, in bps."""
    return _range_bps(s, "or1", s["or1_first"])


def _stale(s: pd.DataFrame, sessions_back: int = 1) -> pd.Series:
    """Days whose last `sessions_back` previous catalog days aren't all recent enough to use."""
    gap = (pd.Series(s.index, index=s.index).diff().dt.days > MAX_PREV_GAP_DAYS).astype(float)
    return gap.rolling(sessions_back, min_periods=1).max().astype(bool)


def build_up30(s: pd.DataFrame) -> pd.Series:
    """The up-move: (high - open) / open over 09:30:00-09:30:30, in bps."""
    return _ret_bps(s["f30_high"], s["f30_first"]).rename(UP30)


def build_label(s: pd.DataFrame) -> pd.Series:
    """1 when the up-move is over `THRESHOLD_BPS`, else 0 (empty without trades in the first 30 seconds)."""
    up30 = build_up30(s)
    return (up30 > THRESHOLD_BPS).astype(float).where(up30.notna()).rename(LABEL)


def build_features(s: pd.DataFrame) -> pd.DataFrame:
    """The symbol's own sizes of moves per day, all known by 09:30, using earlier catalog days where needed."""
    prev = s.shift(1)
    f = pd.DataFrame(index=s.index)

    # Today's pre-open
    f["pre0400_range_bps"] = _pre0400_range_bps(s)

    # Earlier days
    f["or_prev1"] = _opening_range_bps(s).shift(1)
    f["gap_down_bps"] = (1 - s["pre0400_last"] / prev["rth_last"]).clip(lower=0) * 1e4
    f["prev_rth_range_bps"] = _range_bps(s, "rth", s["rth_first"]).shift(1)
    f["prev_rth_rv_ma22"] = s["rth_rv_bps"].shift(1).rolling(22, min_periods=10).mean()
    f["prev_f10_rv_bps"] = prev["f10_rv_bps"]
    f["prev_last30_rv_bps"] = prev["last30_rv_bps"]
    f["prev_ah_rv_bps"] = prev["ah_rv_bps"]

    f.loc[_stale(s), [c for c in f.columns if c.startswith(("or_", "gap", "prev_"))]] = np.nan
    return f


def _signed_moves(s: pd.DataFrame) -> pd.DataFrame:
    """Signed moves before the open and over the previous session, all known by 09:30, in bps."""
    prev, prev2 = s.shift(1), s.shift(2)
    f = pd.DataFrame(index=s.index)
    f["gap_bps"] = _ret_bps(s["pre0400_last"], prev["rth_last"])
    f["ah_ret_bps"] = _ret_bps(prev["ah_last"], prev["rth_last"])
    f["overnight_ret_bps"] = _ret_bps(s["pre0400_first"], prev["ah_last"])
    f["pm_trend_bps"] = _ret_bps(s["pre0400_last"], s["pre0400_first"])
    f["pre0900_trend_bps"] = _ret_bps(s["pre0900_last"], s["pre0900_first"])
    f["pre0925_trend_bps"] = _ret_bps(s["pre0925_last"], s["pre0925_first"])
    f["prev_rth_ret_bps"] = _ret_bps(prev["rth_last"], prev["rth_first"])
    f["prev_last30_ret_bps"] = _ret_bps(prev["last30_last"], prev["last30_first"])
    f["prev_f10_ret_bps"] = _ret_bps(prev["f10_last"], prev["f10_first"])
    f["prev_cc_ret_bps"] = _ret_bps(prev["rth_last"], prev2["rth_last"])
    f.loc[_stale(s), ["gap_bps", "ah_ret_bps", "overnight_ret_bps", "prev_rth_ret_bps", "prev_last30_ret_bps", "prev_f10_ret_bps"]] = np.nan
    f.loc[_stale(s, 2), "prev_cc_ret_bps"] = np.nan
    return f


def build_signed_features(s: pd.DataFrame) -> pd.DataFrame:
    """
    The symbol's own signed features per day, all known by 09:30 except those named
    `AT_OPEN_PREFIX`...: signed moves, where the last pre-open trade sits in the pre-open range, the
    pre-open's buy/sell imbalance, and the earlier days' up-moves.
    """
    f = _signed_moves(s)
    for prefix in ("pre0400", "pre0925"):
        width = s[f"{prefix}_high"] - s[f"{prefix}_low"]
        f[f"{prefix}_position"] = ((s["pre0400_last"] - s[f"{prefix}_low"]) / width).where(width > 0)
        f[f"{prefix}_tick_imbalance"] = s[f"{prefix}_tick_imbalance"]
    for col in ("q0925_imbalance", "q0929_imbalance", "q_last_imbalance"):
        f[col] = s.get(col, np.nan)
    f["q_last_mid_vs_last_bps"] = _ret_bps(s.get("q_last_mid", np.nan), s["pre0400_last"])

    up30 = build_up30(s)
    f["up30_prev1"] = up30.shift(1)
    f["up30_rate_ma10"] = (up30 > THRESHOLD_BPS).astype(float).where(up30.notna()).shift(1).rolling(10, min_periods=5).mean()
    f.loc[_stale(s), ["up30_prev1"]] = np.nan

    # The opening trade against the last pre-open trade
    f[f"{AT_OPEN_PREFIX}open_vs_last_bps"] = _ret_bps(s["or1_first"], s["pre0400_last"])
    return f


def build_peer_features(peers, venue, refresh, index) -> pd.DataFrame:
    """
    The peers' pre-open on the days of `index`, as the median across peers of: each peer's
    04:00-09:30 range and volume over its own trailing median (so a volatile peer doesn't dominate),
    and its signed gap and pre-open trends. Empty on days missing any peer, since a median of fewer
    peers is a noisier feature (AAPL alone in 2025 made the opening-range predictions worse).
    """
    per_peer = []
    for peer in peers:
        s = load_window_stats(peer, venue, refresh)
        moves = _signed_moves(s)
        per_peer.append(
            pd.DataFrame(
                {
                    "peer_pre0400_range_bps_rel": _vs_trailing(_pre0400_range_bps(s)),
                    "peer_pre0400_volume_rel": _vs_trailing(s["pre0400_volume"]),
                    "peer_gap_bps": moves["gap_bps"],
                    "peer_pm_trend_bps": moves["pm_trend_bps"],
                    "peer_pre0925_trend_bps": moves["pre0925_trend_bps"],
                    "peer_prev_rth_ret_bps": moves["prev_rth_ret_bps"],
                }
            )
        )
    by_day = pd.concat(per_peer).groupby(level=0)
    return by_day.median().where(by_day.count() == len(peers)).reindex(index)


def build_options_features(symbol: str, s: pd.DataFrame, refresh=False) -> pd.DataFrame:
    """
    The previous session's options features (`options_features`), on the days of `s`, with the
    implied move also over realized volatility and over today's pre-open range.
    """
    f = load_options_state(symbol, refresh).reindex(s.index).shift(1)
    f.loc[_stale(s)] = np.nan
    f["opt_move1_vs_rv_ma22"] = f["opt_move1_bps"] / s["rth_rv_bps"].shift(1).rolling(22, min_periods=10).mean()
    f["opt_move1_vs_pre0400"] = f["opt_move1_bps"] / _pre0400_range_bps(s)
    return f


def build_earnings_features(symbol: str, s: pd.DataFrame) -> pd.DataFrame:
    """
    `earnings_open`: 1 on the first session after an earnings release (all after the close), else 0.
    `earnings_day`: 1 on the release day itself, whose open comes before the report. Both empty for
    symbols without `EARNINGS_DATES`.
    """
    f = pd.DataFrame(np.nan, index=s.index, columns=["earnings_open", "earnings_day"])
    if symbol not in EARNINGS_DATES:
        return f
    reports = pd.DatetimeIndex(EARNINGS_DATES[symbol])
    f[:] = 0.0
    f.loc[s.index.isin(reports), "earnings_day"] = 1.0
    after = s.index.searchsorted(reports, side="right")
    for report, pos in zip(reports, after, strict=True):
        if pos < len(s) and (s.index[pos] - report).days <= MAX_PREV_GAP_DAYS:
            f.iloc[pos, f.columns.get_loc("earnings_open")] = 1.0
    return f


# ANALYSIS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def discriminate(df: pd.DataFrame, features: list[str], years: list[int]) -> pd.DataFrame:
    """
    Each feature's AUC for `LABEL` (0.5 = no information, below 0.5 = higher values mean fewer
    up-moves) in each of `years`, with the Mann-Whitney p- and q-values of the first year, and its
    Spearman with the up-move itself.
    """
    rows = []
    for name in features:
        row = {"feature": name}
        for i, year in enumerate(years):
            pair = df.loc[df.index.year == year, [name, LABEL, UP30]].dropna()
            if len(pair) < 20 or pair[name].nunique() < 2 or pair[LABEL].nunique() < 2:
                row[f"auc_{year}"] = np.nan
                continue
            row[f"auc_{year}"] = roc_auc_score(pair[LABEL], pair[name])
            if i == 0:
                row["n"] = len(pair)
                row["p"] = sps.mannwhitneyu(pair.loc[pair[LABEL] == 1, name], pair.loc[pair[LABEL] == 0, name]).pvalue
                row[f"spearman_{UP30}"] = sps.spearmanr(pair[name], pair[UP30]).statistic
        rows.append(row)
    table = pd.DataFrame(rows).set_index("feature")
    ok = table["p"].notna()
    table.loc[ok, "q"] = sps.false_discovery_control(table.loc[ok, "p"], method="bh")
    table = table[["n", *[f"auc_{y}" for y in years], "p", "q", f"spearman_{UP30}"]]
    return table.reindex((table[f"auc_{years[0]}"] - 0.5).abs().sort_values(ascending=False).index)


def quintiles(df: pd.DataFrame, feature: str) -> pd.DataFrame:
    pair = df[[feature, LABEL, UP30]].dropna()
    g = pair.groupby(pd.qcut(pair[feature], 5, duplicates="drop"), observed=True)
    return pd.DataFrame({"days": g.size(), f"{LABEL}_rate": g[LABEL].mean(), f"median_{UP30}": g[UP30].median()})


def _split_log(features: list[str]) -> tuple[list[str], list[str]]:
    """The columns of `features` to log (named `log_<column>`) and to pass through as they are."""
    logged = [f.removeprefix("log_") for f in features if f.startswith("log_")]
    return logged, [f for f in features if not f.startswith("log_")]


def model_columns(df: pd.DataFrame, features: list[str]) -> list[str]:
    """`features` as `make_model` columns: logged (`log_<feature>`) where always positive in `df`."""
    return [f"log_{f}" if (df[f] > 0).all() else f for f in features]


def make_model(features: list[str]):
    """
    Logistic regression on the standardized `features`, each `log_<column>` logged first. With no
    features, the training days' base rate.
    """
    if not features:
        return DummyClassifier(strategy="prior")
    logged, raw = _split_log(features)
    columns = ColumnTransformer([("log", FunctionTransformer(np.log), logged), ("raw", "passthrough", raw)])
    return make_pipeline(columns, StandardScaler(), LogisticRegression(C=REG_C))


def _predict_each_day(data: pd.DataFrame, columns: list[str], min_train: int) -> list[float]:
    X, y = data.drop(columns=LABEL), data[LABEL]
    model = make_model(columns)
    return [model.fit(X.iloc[:i], y.iloc[:i]).predict_proba(X.iloc[[i]])[0, 1] for i in range(min_train, len(data))]


def walk_forward(data: pd.DataFrame, models: dict[str, list[str]], min_train: int) -> pd.DataFrame:
    """
    Each model's probability of `LABEL` for every day of `data` after the first `min_train`, from a
    fit on every earlier day. `models` maps a name to `make_model` columns, which `data` must have
    present on every day.
    """
    probs = Parallel(n_jobs=N_WORKERS)(
        delayed(_predict_each_day)(data, columns, min_train) for columns in models.values()
    )
    return pd.DataFrame(dict(zip(models, probs, strict=True)), index=data.index[min_train:])


def score(probs: pd.DataFrame, actual: pd.Series) -> pd.DataFrame:
    """Each model's out-of-sample log loss, Brier score, AUC and accuracy (at 0.5) on the days of `probs`."""
    actual = actual.loc[probs.index]
    rows = []
    for name, p in probs.items():
        rows.append(
            {
                "model": name,
                "n_test": len(p),
                "log_loss": log_loss(actual, p, labels=[0, 1]),
                "brier": brier_score_loss(actual, p),
                "auc": roc_auc_score(actual, p),
                "accuracy": ((p > 0.5) == (actual == 1)).mean(),
                "mean_prob": p.mean(),
            }
        )
    return pd.DataFrame(rows).set_index("model")


def forward_select(data: pd.DataFrame, candidates: list[str], min_train: int, max_features: int) -> list[str]:
    """
    Greedy forward selection: starting from the base rate, each step adds the candidate that most
    lowers the walk-forward log loss on `data`. Stops after `max_features` or when no candidate
    lowers it.
    """
    columns = dict(zip(candidates, model_columns(data, candidates), strict=True))
    best = score(walk_forward(data, {"base rate": []}, min_train), data[LABEL]).iloc[0]
    print(f"   0   base rate                      log loss {best.log_loss:.4f}  AUC {best.auc:.3f}")
    chosen = []
    for _ in range(max_features):
        models = {c: [columns[f] for f in chosen + [c]] for c in candidates if c not in chosen}
        scores = score(walk_forward(data, models, min_train), data[LABEL])
        step = scores.loc[scores["log_loss"].idxmin()]
        if step.log_loss >= best.log_loss:
            break
        best = step
        chosen.append(step.name)
        print(f"  {len(chosen):2d} + {step.name:28s} log loss {step.log_loss:.4f}  AUC {step.auc:.3f}", flush=True)
    return chosen


def main(symbol, venue, peers, train_start, min_train, max_features, allow_at_open, top_quintiles, refresh_stats, options):
    stats = load_window_stats(symbol, venue, refresh_stats)
    features = build_features(stats).join(build_signed_features(stats))
    features = features.join(build_peer_features(peers, venue, refresh_stats, features.index))
    if options:
        features = features.join(build_options_features(symbol, stats))
    features = features.join(build_earnings_features(symbol, stats))
    candidates = list(features.columns)
    df = features.join(build_up30(stats)).join(build_label(stats))
    missing = df[LABEL].isna().sum()
    df = df.dropna(subset=[LABEL])
    train = df[df.index >= train_start]
    print(
        f"=== {symbol}: {len(df)} days with an up-move ({df.index.min().date()} to {df.index.max().date()}), "
        f"peers {', '.join(peers)} ==="
    )
    if missing:
        print(f"NOTE: {missing} day(s) have no trades in the first 30 seconds and are left out")

    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_rows", 500):
        print(f"\n=== {UP30} and the share of days over {THRESHOLD_BPS} bps, by year ===")
        by_year = df.groupby(df.index.year)
        print(pd.DataFrame({"days": by_year.size(), f"median_{UP30}": by_year[UP30].median(), f"{LABEL}_rate": by_year[LABEL].mean()}).round(3).to_string())

        years = sorted({pd.Timestamp(train_start).year, *df.index.year}, reverse=True)
        table = discriminate(df, candidates, years)
        print(f"\n=== How well each feature separates {LABEL} days, strongest in {years[0]} first ===")
        with pd.option_context("display.float_format", "{:,.3f}".format):
            print(table.to_string())
        for name in table.index[:top_quintiles]:
            print(f"\n--- {name} quintiles ({years[0]}) ---")
            print(quintiles(train, name).round(3).to_string())

        selectable = [c for c in candidates if allow_at_open or not c.startswith(AT_OPEN_PREFIX)]
        data = train[selectable + [LABEL]].dropna()
        print(
            f"\n=== Forward selection on walk-forward log loss: {len(data)} days from {train_start} with every "
            f"candidate, first fit on {min_train} ==="
        )
        chosen = forward_select(data, selectable, min_train, max_features)

        columns = dict(zip(selectable, model_columns(data, selectable), strict=True))
        signed = [c for c in chosen if c not in RANGE_FEATURES]
        models = {
            "base rate": [],
            "range features top3": [columns[f] for f in RANGE_FEATURES[:3]],
            "range features top8": [columns[f] for f in RANGE_FEATURES[:8]],
            "selected, signed only": [columns[f] for f in signed],
        }
        models |= {f"selected top{k}": [columns[f] for f in chosen[:k]] for k in range(1, len(chosen) + 1)}
        print(f"\n=== Walk-forward logistic regression for {LABEL}, from {train_start} (scored on the selection days) ===")
        with pd.option_context("display.float_format", "{:,.3f}".format):
            print(score(walk_forward(data, models, min_train), data[LABEL]).to_string())

        # Features chosen on `train_start`'s year, checked on the earlier days, which played no part in choosing them
        held_out = df[df.index < train_start]
        own = [c for c in chosen if c in held_out.columns and held_out[c].notna().mean() > 0.9]
        if own and len(held_out) > min_train:
            used = list(dict.fromkeys(own + RANGE_FEATURES[:3]))
            data = held_out[used + [LABEL]].dropna()
            columns = dict(zip(used, model_columns(data, used), strict=True))
            models = {"base rate": [], "range features top3": [columns[f] for f in RANGE_FEATURES[:3]]}
            models |= {"selected (with data before train_start)": [columns[f] for f in own]}
            models |= {f"selected top{k}": [columns[f] for f in own[:k]] for k in range(1, len(own))}
            print(
                f"\n=== The same models before {train_start} ({len(data)} days, first fit on {min_train}), "
                f"without the features missing there ==="
            )
            with pd.option_context("display.float_format", "{:,.3f}".format):
                print(score(walk_forward(data, models, min_train), data[LABEL]).to_string())

    out_path = Path(data_subdir("analysis")) / f"{symbol}_{LABEL}_features.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path)
    print(f"\nPer-day label and features written to {out_path}")


if __name__ == "__main__":
    SYMBOL = "AMZN"
    VENUE = "ALPACA"
    # Other catalog symbols whose pre-open activity gauges market-wide activity
    PEERS = ["AAPL", "GOOG", "MSFT", "TSLA"]
    # First day the models train on. 2025's up-moves ran smaller (a lower base rate), and the peers
    # have no 2025 data; the days before it are used only to check the features chosen.
    TRAIN_START = "2026-01-01"
    # Days the first walk-forward fit is made on
    MIN_TRAIN = 60
    # Forward selection steps at most
    MAX_FEATURES = 10
    # Let forward selection pick features known only once the opening trade prints
    ALLOW_AT_OPEN = False
    # Quintile tables printed for this many of the strongest features
    TOP_QUINTILES = 5
    # Re-read every day from the catalog rather than using cached window stats
    REFRESH_STATS = False
    # Add the previous session's options features (`options_features`; download them first with
    # `alpaca_options_download`). They don't help (see the module docstring), and their missing days
    # change which days forward selection sees.
    OPTIONS = False

    main(SYMBOL, VENUE, PEERS, TRAIN_START, MIN_TRAIN, MAX_FEATURES, ALLOW_AT_OPEN, TOP_QUINTILES, REFRESH_STATS, OPTIONS)
