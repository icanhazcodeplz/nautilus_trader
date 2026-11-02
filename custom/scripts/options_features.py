#!/usr/bin/env python3
"""
A symbol's options features per day, from the data `alpaca_options_download` saves in
``data/options/<SYMBOL>/``. `opening_range_features` uses them as features of the next open.

Equity options trade only 09:30-16:00, so every feature describes a whole session: its closing
implied volatility (from the 1-minute bars of its last 30 minutes) and its options volume and flow.
`build_options_state` indexes them by that session, so they must be shifted a session to be known
by the next 09:30.

Implied volatility is Black-Scholes on each 1-minute bar's VWAP against the stock's VWAP of the same
minute (Alpaca has no historical options quotes), taken per contract as the volume-weighted mean
over the session's last 30 minutes, then interpolated across out-of-the-money strikes to the money for each expiry.
"""

import os
import sys


sys.path.append(os.getcwd())
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel
from joblib import delayed
from scipy.special import ndtr

from custom.utils.paths import data_subdir


NY_TZ = "America/New_York"
YEAR_SECS = 365 * 24 * 3600
RATE = 0.04
# Bump to invalidate the per-day options stats cache after changing `day_options_stats`
STATS_VERSION = 2
N_WORKERS = 7
# Days to take the trailing median over, for values relative to "normal"
TRAILING_DAYS = 20
# Expiries whose ATM IV is measured each day, nearest first, after the day itself
N_IV_EXPIRIES = 3


# IMPLIED VOLATILITY ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def bs_price(spot, strike, t, sigma, is_call):
    sd = sigma * np.sqrt(t)
    d1 = (np.log(spot / strike) + (RATE + sigma**2 / 2) * t) / sd
    d2 = d1 - sd
    disc = strike * np.exp(-RATE * t)
    call = spot * ndtr(d1) - disc * ndtr(d2)
    return np.where(is_call, call, call - spot + disc)


def implied_vol(price, spot, strike, t, is_call, lo=0.01, hi=5.0, iterations=50) -> np.ndarray:
    """Black-Scholes implied volatility by bisection, NaN where the price is outside [lo, hi]'s prices."""
    lo_arr, hi_arr = np.full_like(price, lo), np.full_like(price, hi)
    ok = (bs_price(spot, strike, t, lo_arr, is_call) < price) & (price < bs_price(spot, strike, t, hi_arr, is_call))
    for _ in range(iterations):
        mid = (lo_arr + hi_arr) / 2
        above = bs_price(spot, strike, t, mid, is_call) > price
        hi_arr, lo_arr = np.where(above, mid, hi_arr), np.where(above, lo_arr, mid)
    return np.where(ok, (lo_arr + hi_arr) / 2, np.nan)


def _smile_at(k: np.ndarray, iv: np.ndarray, at: float) -> float:
    """IV at log-moneyness `at`, linearly interpolated, or NaN if `at` isn't between two strikes."""
    if len(k) < 2 or not (k[0] <= at <= k[-1]):
        return np.nan
    return float(np.interp(at, k, iv))


# PER-DAY OPTIONS STATS ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _signed_premium(bars: pd.DataFrame) -> pd.Series:
    """Each bar's premium (vwap * volume * 100), signed by the tick rule on bar closes per contract."""
    sign = np.sign(bars.groupby("symbol")["close"].diff()).replace(0, np.nan)
    sign = sign.groupby(bars["symbol"]).ffill().fillna(0)
    return sign * bars["vwap"] * bars["volume"] * 100


def day_options_stats(day: str, bars_path: Path, stock: pd.DataFrame, contracts: pd.DataFrame) -> dict:
    """
    One session's closing options state: per expiry after `day`, the ATM IV, the total variance to
    expiry and the skew; and the session's 1-minute volume and tick-rule premium flow of the
    downloaded contracts (the nearest expiries, near the money).
    """
    out = {"day": day, "stats_version": STATS_VERSION}
    bars = pd.read_parquet(bars_path)
    if bars.empty:
        return out
    bars = bars.merge(contracts, on="symbol").sort_values(["symbol", "ts"])
    bars = bars.merge(stock[["ts", "vwap"]].rename(columns={"vwap": "spot"}), on="ts", how="left")
    bars["is_call"] = bars["type"] == "call"
    et = bars["ts"].dt.tz_convert(NY_TZ)
    # The session's last 30 minutes, which end at 13:00 on half days
    session_end = min(et.max().floor("min") + pd.Timedelta(minutes=1), pd.Timestamp(f"{day} 16:00", tz=NY_TZ))
    last30 = et >= session_end - pd.Timedelta(minutes=30)
    bars["premium"] = bars["vwap"] * bars["volume"] * 100
    bars["signed_premium"] = _signed_premium(bars)

    # Volume and flow, over the whole session and its last 30 minutes (empty on half days)
    for name, sel in (("day", np.ones(len(bars), bool)), ("last30", last30.to_numpy())):
        b = bars[sel]
        if b.empty:
            continue
        calls, puts = b[b["is_call"]], b[~b["is_call"]]
        out[f"{name}_call_volume"], out[f"{name}_put_volume"] = calls["volume"].sum(), puts["volume"].sum()
        out[f"{name}_call_premium"], out[f"{name}_put_premium"] = calls["premium"].sum(), puts["premium"].sum()
        out[f"{name}_flow"] = (calls["signed_premium"].sum() - puts["signed_premium"].sum()) / b["premium"].sum()
        otm_calls = calls[calls["strike_price"] > calls["spot"]]
        otm_puts = puts[puts["strike_price"] < puts["spot"]]
        out[f"{name}_otm_call_share"] = otm_calls["volume"].sum() / (otm_calls["volume"].sum() + otm_puts["volume"].sum())
    out["day_0dte_volume"] = bars.loc[bars["expiration_date"] == day, "volume"].sum()

    # Closing implied volatility, per expiry after the day
    close = bars[last30 & (bars["expiration_date"] > day) & bars["spot"].notna() & (bars["volume"] > 0)].copy()
    expiry_ts = pd.to_datetime(close["expiration_date"]).dt.tz_localize(NY_TZ) + pd.Timedelta(hours=16)
    close["t"] = ((expiry_ts - close["ts"].dt.tz_convert(NY_TZ)).dt.total_seconds() - 30) / YEAR_SECS
    close["iv"] = implied_vol(
        close["vwap"].to_numpy(), close["spot"].to_numpy(), close["strike_price"].to_numpy(), close["t"].to_numpy(), close["is_call"].to_numpy()
    )
    close = close.dropna(subset=["iv"])
    close["w"] = close["volume"]
    per_contract = close.groupby(["expiration_date", "symbol", "strike_price", "is_call"]).apply(
        lambda g: pd.Series({"iv": np.average(g["iv"], weights=g["w"]), "spot": np.average(g["spot"], weights=g["w"]), "volume": g["w"].sum()}),
        include_groups=False,
    ).reset_index()
    spot_close = float(stock.loc[stock["ts"] < session_end, "close"].iloc[-1])
    for i, (expiry, g) in enumerate(per_contract.groupby("expiration_date")):
        if i >= N_IV_EXPIRIES:
            break
        g = g.assign(k=np.log(g["strike_price"] / g["spot"]))
        # The out-of-the-money side of each strike: puts below the money, calls above
        otm = g[(g["k"] < 0) != g["is_call"]].sort_values("k")
        t = ((pd.Timestamp(expiry).tz_localize(NY_TZ) + pd.Timedelta(hours=16)) - session_end).total_seconds() / YEAR_SECS
        atm = _smile_at(otm["k"].to_numpy(), otm["iv"].to_numpy(), 0.0)
        n = i + 1
        out[f"exp{n}_date"] = expiry
        out[f"exp{n}_sessions"] = int(np.busday_count(pd.Timestamp(day).date() + pd.Timedelta(days=1), pd.Timestamp(expiry).date() + pd.Timedelta(days=1)))
        out[f"exp{n}_t"] = t
        out[f"exp{n}_atm_iv"] = atm
        out[f"exp{n}_w"] = atm**2 * t
        # Half a standard deviation to expiry, so it stays within the downloaded strikes on volatile days
        sd = atm * np.sqrt(t) / 2 if np.isfinite(atm) else np.nan
        put_iv = _smile_at(otm["k"].to_numpy(), otm["iv"].to_numpy(), -sd)
        call_iv = _smile_at(otm["k"].to_numpy(), otm["iv"].to_numpy(), sd)
        out[f"exp{n}_skew"] = (put_iv - call_iv) / atm
        # Model-free check: the ATM straddle at the strike nearest the close, over the close
        nearest = g.loc[(g["strike_price"] - spot_close).abs().idxmin(), "strike_price"]
        pair = close[(close["expiration_date"] == expiry) & (close["strike_price"] == nearest)].groupby("is_call")["vwap"].last()
        out[f"exp{n}_straddle_bps"] = pair.sum() / spot_close * 1e4 if len(pair) == 2 else np.nan
    out["spot_close"] = spot_close
    return out


def load_options_stats(symbol: str, refresh=False) -> pd.DataFrame:
    """The per-day options stats of every day with minute bars, cached per symbol."""
    folder = data_subdir("options", symbol)
    cache_path = Path(data_subdir("analysis")) / f"{symbol}_options_day_stats.parquet"
    files = {p.stem: p for p in sorted((folder / "minute_bars").glob("*.parquet"))}
    cached = pd.DataFrame()
    if cache_path.exists() and not refresh:
        cached = pd.read_parquet(cache_path)
        cached = cached[cached["stats_version"] == STATS_VERSION]
    todo = [day for day in files if day not in set(cached.get("day", []))]
    if todo:
        print(f"Computing options stats for {len(todo)} day(s)...")
        contracts = pd.read_parquet(folder / "contracts.parquet")[["symbol", "expiration_date", "strike_price", "type"]]
        stock = pd.read_parquet(folder / "stock_minute_bars.parquet")
        stock_day = stock["ts"].dt.tz_convert(NY_TZ).dt.strftime("%Y-%m-%d")
        by_day = {day: g for day, g in stock.groupby(stock_day)}
        fresh = pd.DataFrame(
            Parallel(n_jobs=N_WORKERS)(
                delayed(day_options_stats)(day, files[day], by_day[day], contracts) for day in todo if day in by_day
            )
        )
        cached = pd.concat([cached, fresh], ignore_index=True).drop_duplicates("day", keep="last")
        cached.sort_values("day").to_parquet(cache_path, index=False)
    out = cached[cached["day"].isin(files)].copy()
    out.index = pd.to_datetime(out.pop("day"))
    return out.sort_index()


def load_daily_volume(symbol: str) -> pd.DataFrame:
    """Each day's call and put volume and premium over every contract within its daily-bars lookback."""
    folder = data_subdir("options", symbol)
    contracts = pd.read_parquet(folder / "contracts.parquet")[["symbol", "type"]]
    bars = pd.concat([pd.read_parquet(p) for p in sorted((folder / "daily_bars").glob("*.parquet"))], ignore_index=True)
    bars = bars.merge(contracts, on="symbol")
    bars["day"] = bars["ts"].dt.tz_convert(NY_TZ).dt.normalize().dt.tz_localize(None)
    bars["premium"] = bars["vwap"] * bars["volume"] * 100
    g = bars.pivot_table(index="day", columns="type", values=["volume", "premium"], aggfunc="sum")
    g.columns = [f"all_{t}_{v}" for v, t in g.columns]
    return g


# FEATURES ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _vs_trailing(series: pd.Series) -> pd.Series:
    return series / series.shift(1).rolling(TRAILING_DAYS, min_periods=5).median()


def build_options_state(o: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """
    Each session's closing options measures, indexed by that session (shift by one session to use
    them as features of the next open).
    """
    f = pd.DataFrame(index=o.index)
    # The implied move per session to the nearest expiry after the day, and the forward move per
    # session between it and the next expiry (the "normal" daily move, without an event in the first)
    f["opt_move1_bps"] = np.sqrt(o["exp1_w"] / o["exp1_sessions"]) * 1e4
    fwd_w = (o["exp2_w"] - o["exp1_w"]) / (o["exp2_sessions"] - o["exp1_sessions"])
    f["opt_move_fwd_bps"] = np.sqrt(fwd_w.where(fwd_w > 0)) * 1e4
    f["opt_event_ratio"] = f["opt_move1_bps"] / f["opt_move_fwd_bps"]
    # The same ratio only where the nearest expiry is the next session, so the event is known to be
    # in it (as on the opens after earnings); 1 elsewhere
    f["opt_next_event_ratio"] = f["opt_event_ratio"].where(o["exp1_sessions"] == 1, 1.0)
    f["opt_move2_bps"] = np.sqrt(o["exp2_w"] / o["exp2_sessions"]) * 1e4
    f["opt_atm_iv1"] = o["exp1_atm_iv"]
    f["opt_straddle1_per_session_bps"] = o["exp1_straddle_bps"] / np.sqrt(o["exp1_sessions"])
    f["opt_move1_chg"] = np.log(f["opt_move2_bps"] / f["opt_move2_bps"].shift(1))
    f["opt_skew1"] = o["exp1_skew"]
    f["opt_skew2"] = o["exp2_skew"]
    f["opt_skew2_chg"] = o["exp2_skew"] - o["exp2_skew"].shift(1)

    # Volume and flow of the near-the-money, near-term contracts
    f["opt_pc_volume"] = o["day_put_volume"] / o["day_call_volume"]
    f["opt_pc_premium"] = o["day_put_premium"] / o["day_call_premium"]
    f["opt_last30_pc_volume"] = o["last30_put_volume"] / o["last30_call_volume"]
    f["opt_day_flow"] = o["day_flow"]
    f["opt_last30_flow"] = o["last30_flow"]
    f["opt_otm_call_share"] = o["day_otm_call_share"]
    f["opt_last30_otm_call_share"] = o["last30_otm_call_share"]
    near_volume = o["day_call_volume"] + o["day_put_volume"]
    f["opt_volume_rel"] = _vs_trailing(near_volume)
    f["opt_last30_volume_rel"] = _vs_trailing(o["last30_call_volume"] + o["last30_put_volume"])
    f["opt_0dte_share"] = o["day_0dte_volume"] / near_volume

    # Every contract expiring within the daily bars' lookback
    d = daily.reindex(o.index)
    f["opt_all_pc_volume"] = d["all_put_volume"] / d["all_call_volume"]
    f["opt_all_pc_premium"] = d["all_put_premium"] / d["all_call_premium"]
    f["opt_all_volume_rel"] = _vs_trailing(d["all_put_volume"] + d["all_call_volume"])
    f["opt_all_pc_volume_rel"] = f["opt_all_pc_volume"] / f["opt_all_pc_volume"].shift(1).rolling(TRAILING_DAYS, min_periods=5).median()
    return f


def load_options_state(symbol: str, refresh=False) -> pd.DataFrame:
    """`build_options_state` of every day with downloaded options data, indexed by that session."""
    return build_options_state(load_options_stats(symbol, refresh), load_daily_volume(symbol))
