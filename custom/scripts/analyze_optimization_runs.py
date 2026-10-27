#!/usr/bin/env python3
"""
Look for market features that separate good days from bad days in a symbol's optimization results.

Every (param set, day) result in the study store is averaged per day, across all of the symbol's
param sets (or one study's), into the day's mean PnL. Days are also classed by how the param sets
that traded that day did: `always_negative` when every one of them lost, `always_positive` when
every one of them made money, `mostly_*` when at least `MOSTLY` of them did, else `mixed`.

Features come straight from the catalog's trade and quote parquet files, read with pyarrow rather
than as Nautilus objects. Each day's file is boiled down to per-session stats (pre-market, regular
hours, after-hours, the first minutes after the open, pre-open quotes), which are cached, and the
features are then derived across days from those: the gap from the previous close, the previous
day's volume and range, volumes relative to their trailing median, and so on.

Features are split in two groups:
    actionable -- known by `DECISION_TIME`, before the strategy starts trading, so usable as a
                  filter for whether to trade the day at all
    diagnostic -- measured after the open (e.g. how much the first 10 minutes trended). Not usable
                  as a filter, but they show what kind of day the strategy makes or loses money on.

With dozens of features some will correlate by chance, so the tables carry Benjamini-Hochberg
q-values alongside the raw p-values.

Edit the values under ``if __name__ == "__main__"`` and run from the repo root:
    python custom/scripts/analyze_optimization_runs.py
"""

import os
import sys


sys.path.append(os.getcwd())
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy import stats as sps

from custom.backtest_utils.load_catalog_data import CATALOG_PATH
from custom.optimize import study_store
from custom.utils.paths import data_subdir
from nautilus_trader.model.objects import FIXED_SCALAR


ET_TZ = "America/New_York"
# Features in the actionable group only use data before this ET wall-clock time. The backtests
# start at 09:29:00 and trading at 09:29:58.
DECISION_TIME = "09:29:00"
# Days to take the trailing median over, for volumes and volatilities relative to "normal"
TRAILING_DAYS = 20
# A gap longer than this (in calendar days) to the previous catalog day means days are missing,
# so features built on "the previous day" are left empty rather than taken from a stale day.
MAX_PREV_GAP_DAYS = 5
# Trades further than this from the rolling median price are treated as bad prints and dropped
BAD_PRINT_TOL = 0.01
BAD_PRINT_WINDOW = 101
# Bump to invalidate the session-stats cache after changing `session_stats`
STATS_VERSION = 1
N_WORKERS = 7

ACTIONABLE = [
    "gap_premarket_bps",
    "abs_gap_premarket_bps",
    "gap_vs_daily_vol",
    "prev_ah_return_bps",
    "pm_return_bps",
    "pm_range_bps",
    "pm_rv_bps",
    "pm_rv_rel",
    "pm_late_rv_bps",
    "pm_volume",
    "pm_volume_rel",
    "pm_late_volume_rel",
    "pm_trades",
    "pm_spread_bps",
    "pm_quote_imbalance",
    "prev_rth_volume",
    "prev_rth_volume_rel",
    "prev_ah_volume",
    "prev_ah_volume_rel",
    "prev_ah_range_bps",
    "prev_ah_rv_bps",
    "prev_rth_return_bps",
    "prev_rth_abs_return_bps",
    "prev_rth_range_bps",
    "prev_rth_rv_bps",
    "prev_last30_return_bps",
    "ret_5d_bps",
    "dist_from_ma20_bps",
    "daily_vol_20d_bps",
    "day_of_week",
    "days_since_prev_session",
]
DIAGNOSTIC = [
    "gap_open_bps",
    "abs_gap_open_bps",
    "open_vs_pm_last_bps",
    "f1_range_bps",
    "f10_return_bps",
    "f10_abs_return_bps",
    "f10_range_bps",
    "f10_rv_bps",
    "f10_efficiency",
    "f10_volume_rel",
]


# DAY RESULTS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def load_results(conn, symbol, study_hash=None) -> pd.DataFrame:
    """One row per (params_hash, day) result: every param set of `symbol`, or only `study_hash`'s."""
    if study_hash is not None:
        study = study_store.find_study(conn, study_hash)
        if study["symbol"] != symbol:
            raise ValueError(f"Study {study_hash} is for {study['symbol']}, not {symbol}")
        return study_store.load_study_results(conn, study["study_hash"])
    return pd.read_sql_query(
        "SELECT dr.params_hash, dr.day, dr.pnl, dr.entered FROM day_results dr"
        " JOIN param_sets ps ON ps.params_hash = dr.params_hash WHERE ps.symbol = ?",
        conn,
        params=(symbol,),
    )


def summarize_days(results: pd.DataFrame, mostly: float) -> pd.DataFrame:
    """Per day: mean PnL over every param set, and how the param sets that traded did."""
    traded = results[results["entered"] > 0]
    by_day = results.groupby("day")
    by_day_traded = traded.groupby("day")
    days = pd.DataFrame(
        {
            "n_param_sets": by_day.size(),
            "mean_pnl": by_day["pnl"].mean(),
            "n_traded": by_day_traded.size(),
            "mean_pnl_traded": by_day_traded["pnl"].mean(),
            "frac_pos": by_day_traded["pnl"].apply(lambda pnl: (pnl > 0).mean()),
            "frac_neg": by_day_traded["pnl"].apply(lambda pnl: (pnl < 0).mean()),
        }
    )
    days["n_traded"] = days["n_traded"].fillna(0).astype(int)
    days["category"] = np.select(
        [
            days["n_traded"] == 0,
            days["frac_neg"] == 1,
            days["frac_pos"] == 1,
            days["frac_neg"] >= mostly,
            days["frac_pos"] >= mostly,
        ],
        ["no_trades", "always_negative", "always_positive", "mostly_negative", "mostly_positive"],
        default="mixed",
    )
    days.index = pd.to_datetime(days.index)
    return days


# SESSION STATS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _catalog_files(data_cls: str, symbol: str, venue: str) -> dict[str, Path]:
    """ET day -> parquet file. Each catalog file holds one day, so its start timestamp names its day."""
    directory = CATALOG_PATH / "data" / data_cls / f"{symbol}.{venue}"
    files = {}
    for path in sorted(directory.glob("*.parquet")):
        start = pd.to_datetime(path.name[:19], format="%Y-%m-%dT%H-%M-%S", utc=True)
        files[start.tz_convert(ET_TZ).strftime("%Y-%m-%d")] = path
    return files


def _decode_fixed(column) -> np.ndarray:
    """A Nautilus fixed-point price/quantity column (64- or 128-bit raw integers) as floats."""
    arr = column.combine_chunks()
    width = arr.type.byte_width
    raw = np.frombuffer(arr.buffers()[1], dtype=np.uint8)[arr.offset * width : (arr.offset + len(arr)) * width]
    if width == 8:
        return raw.view("<i8").astype(np.float64) / FIXED_SCALAR
    words = raw.view("<u8").reshape(-1, 2)
    high = words[:, 1].view("<i8").astype(np.float64)
    return (high * 2.0**64 + words[:, 0].astype(np.float64)) / FIXED_SCALAR


def _read_trades(path):
    table = pq.read_table(path, columns=["price", "size", "ts_event"])
    ts = table.column("ts_event").to_numpy()
    price = _decode_fixed(table.column("price"))
    size = _decode_fixed(table.column("size"))
    order = np.argsort(ts, kind="stable")
    ts, price, size = ts[order], price[order], size[order]
    median = pd.Series(price).rolling(BAD_PRINT_WINDOW, center=True, min_periods=1).median().to_numpy()
    good = np.abs(price / median - 1) < BAD_PRINT_TOL
    return ts[good], price[good], size[good]


def _et_ns(day: str, hhmm: str) -> int:
    return pd.Timestamp(f"{day} {hhmm}", tz=ET_TZ).value


def _bar_closes(ts, price, start_ns, bar_ns):
    """The last price of each `bar_ns` bar that has a trade."""
    bars = (ts - start_ns) // bar_ns
    last_of_bar = np.r_[bars[1:] != bars[:-1], True]
    return price[last_of_bar]


def _realized_vol_bps(ts, price, start_ns, bar_ns) -> float:
    closes = _bar_closes(ts, price, start_ns, bar_ns)
    if len(closes) < 3:
        return np.nan
    return float(np.sqrt(np.sum(np.diff(np.log(closes)) ** 2)) * 1e4)


def _window_stats(prefix, ts, price, size, start_ns, end_ns, bar_ns) -> dict:
    lo, hi = np.searchsorted(ts, [start_ns, end_ns])
    t, p, v = ts[lo:hi], price[lo:hi], size[lo:hi]
    if len(p) == 0:
        return {f"{prefix}_volume": 0.0, f"{prefix}_trades": 0}
    return {
        f"{prefix}_first": p[0],
        f"{prefix}_last": p[-1],
        f"{prefix}_high": p.max(),
        f"{prefix}_low": p.min(),
        f"{prefix}_volume": v.sum(),
        f"{prefix}_trades": len(p),
        f"{prefix}_rv_bps": _realized_vol_bps(t, p, start_ns, bar_ns),
    }


def session_stats(day: str, trade_path: Path, quote_path: Path | None) -> dict:
    """One day's per-session stats, from its own catalog files only."""
    ts, price, size = _read_trades(trade_path)
    decision = _et_ns(day, DECISION_TIME)
    open_ns, close_ns = _et_ns(day, "09:30"), _et_ns(day, "16:00")
    minute, ten_secs, second = 60 * 10**9, 10 * 10**9, 10**9

    out = {"day": day, "stats_version": STATS_VERSION}
    out |= _window_stats("pm", ts, price, size, _et_ns(day, "04:00"), decision, minute)
    out |= _window_stats("pm_late", ts, price, size, _et_ns(day, "09:00"), decision, minute)
    out |= _window_stats("rth", ts, price, size, open_ns, close_ns, minute)
    out |= _window_stats("last30", ts, price, size, _et_ns(day, "15:30"), close_ns, minute)
    out |= _window_stats("ah", ts, price, size, close_ns, _et_ns(day, "20:00"), minute)
    out |= _window_stats("f1", ts, price, size, open_ns, _et_ns(day, "09:31"), second)
    out |= _window_stats("f10", ts, price, size, open_ns, _et_ns(day, "09:40"), ten_secs)

    # How directly the first 10 minutes moved: net move over the path length of 10-second closes.
    # Near 1 is a one-way trend, near 0 is chop back and forth.
    lo, hi = np.searchsorted(ts, [open_ns, _et_ns(day, "09:40")])
    closes = _bar_closes(ts[lo:hi], price[lo:hi], open_ns, ten_secs)
    path = np.abs(np.diff(closes)).sum() if len(closes) > 1 else 0.0
    out["f10_efficiency"] = abs(closes[-1] - closes[0]) / path if path > 0 else np.nan

    if quote_path is not None:
        quotes = pq.read_table(
            quote_path,
            columns=["bid_price", "ask_price", "bid_size", "ask_size"],
            filters=[("ts_event", ">=", _et_ns(day, "09:00")), ("ts_event", "<", decision)],
        )
        if quotes.num_rows > 0:
            bid, ask = _decode_fixed(quotes.column("bid_price")), _decode_fixed(quotes.column("ask_price"))
            bid_size, ask_size = _decode_fixed(quotes.column("bid_size")), _decode_fixed(quotes.column("ask_size"))
            valid = (bid > 0) & (ask >= bid)
            mid = (bid + ask) / 2
            out["pm_spread_bps"] = float(np.median(((ask - bid) / mid)[valid]) * 1e4)
            depth = bid_size + ask_size
            out["pm_quote_imbalance"] = float(np.mean(((bid_size - ask_size) / depth)[valid & (depth > 0)]))
    return out


def _session_stats_task(args):
    return session_stats(*args)


def load_session_stats(symbol, venue, days, refresh=False) -> pd.DataFrame:
    """
    Session stats for `days`, plus the `TRAILING_DAYS` catalog days before the first of them.

    Cached per symbol; only days missing from the cache (or computed by an older
    `STATS_VERSION`) are read from the catalog.
    """
    cache_path = Path(data_subdir("analysis")) / f"{symbol}_session_stats.parquet"
    trade_files = _catalog_files("trade_tick", symbol, venue)
    quote_files = _catalog_files("quote_tick", symbol, venue)
    catalog_days = sorted(trade_files)
    first = catalog_days.index(min(days)) if min(days) in catalog_days else 0
    wanted = catalog_days[max(0, first - TRAILING_DAYS) :]
    wanted = [day for day in wanted if day <= max(days)]

    cached = pd.DataFrame()
    if cache_path.exists() and not refresh:
        cached = pd.read_parquet(cache_path)
        cached = cached[cached["stats_version"] == STATS_VERSION]
    todo = [day for day in wanted if day not in set(cached.get("day", []))]
    if todo:
        print(f"Reading {len(todo)} day(s) of {symbol} ticks from the catalog...")
        tasks = [(day, trade_files[day], quote_files.get(day)) for day in todo]
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=N_WORKERS, mp_context=ctx) as pool:
            fresh = pd.DataFrame(list(pool.map(_session_stats_task, tasks)))
        cached = pd.concat([cached, fresh], ignore_index=True).drop_duplicates("day", keep="last")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cached.sort_values("day").to_parquet(cache_path, index=False)

    out = cached[cached["day"].isin(wanted)].copy()
    out.index = pd.to_datetime(out.pop("day"))
    return out.sort_index()


# FEATURES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _bps(numerator, denominator):
    return (numerator / denominator - 1) * 1e4


def _vs_trailing(series: pd.Series) -> pd.Series:
    """Each value over the median of the `TRAILING_DAYS` values before it."""
    return series / series.shift(1).rolling(TRAILING_DAYS, min_periods=5).median()


def build_features(s: pd.DataFrame) -> pd.DataFrame:
    """Features per day from the session stats, using the previous catalog day where needed."""
    prev = s.shift(1)
    f = pd.DataFrame(index=s.index)
    f["days_since_prev_session"] = pd.Series(s.index, index=s.index).diff().dt.days
    f["day_of_week"] = s.index.dayofweek

    # Built on the previous day's session
    f["gap_premarket_bps"] = _bps(s["pm_last"], prev["rth_last"])
    f["abs_gap_premarket_bps"] = f["gap_premarket_bps"].abs()
    f["prev_ah_return_bps"] = _bps(prev["ah_last"], prev["rth_last"])
    f["pm_range_bps"] = (s["pm_high"] - s["pm_low"]) / prev["rth_last"] * 1e4
    f["prev_rth_volume"] = prev["rth_volume"]
    f["prev_rth_volume_rel"] = _vs_trailing(s["rth_volume"]).shift(1)
    f["prev_ah_volume"] = prev["ah_volume"]
    f["prev_ah_volume_rel"] = _vs_trailing(s["ah_volume"]).shift(1)
    f["prev_ah_range_bps"] = (prev["ah_high"] - prev["ah_low"]) / prev["rth_last"] * 1e4
    f["prev_ah_rv_bps"] = prev["ah_rv_bps"]
    f["prev_rth_return_bps"] = _bps(prev["rth_last"], prev["rth_first"])
    f["prev_rth_abs_return_bps"] = f["prev_rth_return_bps"].abs()
    f["prev_rth_range_bps"] = (prev["rth_high"] - prev["rth_low"]) / prev["rth_first"] * 1e4
    f["prev_rth_rv_bps"] = prev["rth_rv_bps"]
    f["prev_last30_return_bps"] = _bps(prev["last30_last"], prev["last30_first"])
    f["ret_5d_bps"] = _bps(s["rth_last"].shift(1), s["rth_last"].shift(6))
    f["dist_from_ma20_bps"] = _bps(s["pm_last"], s["rth_last"].shift(1).rolling(TRAILING_DAYS, min_periods=5).mean())
    daily_returns = np.log(s["rth_last"]).diff()
    f["daily_vol_20d_bps"] = daily_returns.shift(1).rolling(TRAILING_DAYS, min_periods=5).std() * 1e4
    f["gap_vs_daily_vol"] = f["gap_premarket_bps"] / f["daily_vol_20d_bps"]
    stale = f["days_since_prev_session"] > MAX_PREV_GAP_DAYS
    f.loc[stale, [c for c in f.columns if c not in ("day_of_week", "days_since_prev_session")]] = np.nan

    # Today's pre-market only
    f["pm_return_bps"] = _bps(s["pm_last"], s["pm_first"])
    f["pm_rv_bps"] = s["pm_rv_bps"]
    f["pm_rv_rel"] = _vs_trailing(s["pm_rv_bps"])
    f["pm_late_rv_bps"] = s["pm_late_rv_bps"]
    f["pm_volume"] = s["pm_volume"]
    f["pm_volume_rel"] = _vs_trailing(s["pm_volume"])
    f["pm_late_volume_rel"] = _vs_trailing(s["pm_late_volume"])
    f["pm_trades"] = s["pm_trades"]
    f["pm_spread_bps"] = s.get("pm_spread_bps")
    f["pm_quote_imbalance"] = s.get("pm_quote_imbalance")

    # Diagnostic: after the open
    f["gap_open_bps"] = _bps(s["rth_first"], prev["rth_last"]).where(~stale)
    f["abs_gap_open_bps"] = f["gap_open_bps"].abs()
    f["open_vs_pm_last_bps"] = _bps(s["rth_first"], s["pm_last"])
    f["f1_range_bps"] = (s["f1_high"] - s["f1_low"]) / s["f1_first"] * 1e4
    f["f10_return_bps"] = _bps(s["f10_last"], s["f10_first"])
    f["f10_abs_return_bps"] = f["f10_return_bps"].abs()
    f["f10_range_bps"] = (s["f10_high"] - s["f10_low"]) / s["f10_first"] * 1e4
    f["f10_rv_bps"] = s["f10_rv_bps"]
    f["f10_efficiency"] = s["f10_efficiency"]
    f["f10_volume_rel"] = _vs_trailing(s["f10_volume"])
    return f[ACTIONABLE + DIAGNOSTIC]


# ANALYSIS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _bh_qvalues(p: pd.Series) -> pd.Series:
    """Benjamini-Hochberg q-values: the false discovery rate at which each p-value is significant."""
    p = p.dropna()
    order = p.sort_values()
    q = order * len(order) / np.arange(1, len(order) + 1)
    return q[::-1].cummin()[::-1].clip(upper=1).reindex(p.index)


def correlate(df: pd.DataFrame, features: list[str], target: str, groups: tuple[str, str]) -> pd.DataFrame:
    """
    Each feature against `target`: Pearson and Spearman with p-values, and the feature's median on
    the two `groups` of days with a Mann-Whitney p-value for the difference.
    """
    neg_group, pos_group = groups
    rows = []
    for name in features:
        pair = df[[name, target]].dropna()
        if len(pair) < 10 or pair[name].nunique() < 3:
            continue
        pearson = sps.pearsonr(pair[name], pair[target])
        spearman = sps.spearmanr(pair[name], pair[target])
        neg = df.loc[df["group"] == neg_group, name].dropna()
        pos = df.loc[df["group"] == pos_group, name].dropna()
        mw_p = sps.mannwhitneyu(neg, pos).pvalue if len(neg) >= 3 and len(pos) >= 3 else np.nan
        rows.append(
            {
                "feature": name,
                "n": len(pair),
                "pearson_r": pearson.statistic,
                "pearson_p": pearson.pvalue,
                "spearman_rho": spearman.statistic,
                "spearman_p": spearman.pvalue,
                f"median_{neg_group}": neg.median(),
                f"median_{pos_group}": pos.median(),
                "mw_p": mw_p,
            }
        )
    table = pd.DataFrame(rows).set_index("feature")
    table.insert(table.columns.get_loc("spearman_p") + 1, "spearman_q", _bh_qvalues(table["spearman_p"]))
    return table.reindex(table["spearman_rho"].abs().sort_values(ascending=False).index)


def quintiles(df: pd.DataFrame, feature: str, target: str) -> pd.DataFrame:
    pair = df[[feature, target, "category"]].dropna(subset=[feature, target])
    bins = pd.qcut(pair[feature], 5, duplicates="drop")
    g = pair.groupby(bins, observed=True)
    return pd.DataFrame(
        {
            "days": g.size(),
            f"mean_{target}": g[target].mean(),
            f"total_{target}": g[target].sum(),
            "frac_days_pos": g[target].apply(lambda x: (x > 0).mean()),
            "always_neg": g["category"].apply(lambda c: (c == "always_negative").sum()),
            "always_pos": g["category"].apply(lambda c: (c == "always_positive").sum()),
        }
    )


def _choose_groups(days: pd.DataFrame, min_days: int) -> tuple[str, str]:
    """
    The negative/positive day groups to compare features on: the strict always_* categories when
    both have at least `min_days` days, otherwise widened to include the mostly_* days.
    """
    strict = days["category"].isin(["always_negative", "always_positive"])
    counts = days.loc[strict, "category"].value_counts()
    if counts.get("always_negative", 0) >= min_days and counts.get("always_positive", 0) >= min_days:
        days["group"] = days["category"].where(strict)
        return "always_negative", "always_positive"
    days["group"] = days["category"].map(
        {
            "always_negative": "negative",
            "mostly_negative": "negative",
            "always_positive": "positive",
            "mostly_positive": "positive",
        }
    )
    return "negative", "positive"


def main(symbol, venue, study_hash, target, mostly, min_group_days, top_quintiles, refresh_features):
    conn = study_store.open_store()
    results = load_results(conn, symbol, study_hash)
    if results.empty:
        raise SystemExit(f"No results for {symbol}{f' in study {study_hash}' if study_hash else ''}")
    days = summarize_days(results, mostly)
    print(
        f"=== {symbol}: {results['params_hash'].nunique()} param sets, {len(results)} results, {len(days)} days "
        f"({days.index.min().date()} to {days.index.max().date()}) ==="
    )
    print(f"param sets per day: {days['n_param_sets'].min()}-{days['n_param_sets'].max()}\n")

    with pd.option_context("display.width", 250, "display.max_columns", None, "display.max_rows", 500):
        summary = days.groupby("category").agg(
            days=("mean_pnl", "size"),
            mean_pnl=("mean_pnl", "mean"),
            total_mean_pnl=("mean_pnl", "sum"),
            mean_frac_pos=("frac_pos", "mean"),
        )
        print(f"=== Day categories (mostly = at least {mostly:.0%} of the param sets that traded) ===")
        print(summary.round(2).to_string(), "\n")
        for category in ["always_negative", "always_positive"]:
            chosen = days[days["category"] == category]
            print(f"--- {category}: {len(chosen)} days ---")
            if len(chosen):
                cols = ["mean_pnl", "mean_pnl_traded", "n_traded", "n_param_sets"]
                print(chosen[cols].round(2).to_string(), "\n")

        stats = load_session_stats(symbol, venue, days.index.strftime("%Y-%m-%d").tolist(), refresh_features)
        features = build_features(stats)
        df = days.join(features, how="inner")
        missing = len(days) - len(df)
        if missing:
            print(f"NOTE: {missing} result day(s) have no catalog data and are left out\n")

        groups = _choose_groups(df, min_group_days)
        print(f"Feature medians compared on {groups[0]} vs {groups[1]} days "
              f"({(df['group'] == groups[0]).sum()} vs {(df['group'] == groups[1]).sum()})\n")

        for title, names in [("ACTIONABLE (known before the open)", ACTIONABLE), ("DIAGNOSTIC (after the open)", DIAGNOSTIC)]:
            table = correlate(df, names, target, groups)
            print(f"=== {title}: correlation with {target}, strongest first ===")
            with pd.option_context("display.float_format", "{:,.3f}".format):
                print(table.to_string(), "\n")
            for name in table.index[:top_quintiles]:
                print(f"--- {name} quintiles ---")
                print(quintiles(df, name, target).round(2).to_string(), "\n")

    out_path = Path(data_subdir("analysis")) / f"{symbol}_day_features.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path)
    print(f"Per-day results and features written to {out_path}")


if __name__ == "__main__":
    SYMBOL = "AAPL"
    VENUE = "ALPACA"
    # A study hash prefix to only use that study's param sets. None = every param set of SYMBOL.
    STUDY_HASH = None
    # "mean_pnl" (every param set, non-trading ones count as 0) or "mean_pnl_traded" (only those
    # that traded that day)
    TARGET = "mean_pnl"
    # Share of the param sets that traded for a day to count as mostly negative/positive
    MOSTLY = 0.9
    # Fewer always_negative or always_positive days than this, and features are compared on the
    # mostly_* days as well
    MIN_GROUP_DAYS = 10
    # Quintile tables printed for this many of the strongest features in each group
    TOP_QUINTILES = 4
    # Re-read every day from the catalog rather than using cached session stats
    REFRESH_FEATURES = False

    main(SYMBOL, VENUE, STUDY_HASH, TARGET, MOSTLY, MIN_GROUP_DAYS, TOP_QUINTILES, REFRESH_FEATURES)
