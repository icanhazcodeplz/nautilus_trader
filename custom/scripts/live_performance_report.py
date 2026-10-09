"""
Performance report for trades made on the live Alpaca (margin) account.

Pulls FILL (and FEE) account activities through the Alpaca adapter's
``AlpacaHttpClient``, rebuilds flat-to-flat round-trip trades for a symbol,
and prints daily, monthly and total day-trading statistics.

Edit the values under ``if __name__ == "__main__"`` and run:
    python -m custom.scripts.live_performance_report

"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.adapters.alpaca.utils import dt_to_iso_8601


MARKET_TZ = "America/New_York"
PAGE_SIZE = 100
QTY_EPS = 1e-9


# -- Fetching ---------------------------------------------------------------------------------


async def _get_activities(
    client: AlpacaHttpClient,
    activity_type: str,
    after: str,
    until: str,
) -> list[dict[str, Any]]:
    """
    Page through account activities of one type.

    ``AlpacaHttpClient.get_fills`` uses low-priority requests and silently stops early when
    the rate-limit reserve is hit, which would truncate a report, so page with priority
    requests here (they wait on the rate limiter instead of giving up).
    """
    endpoint = f"/v2/account/activities/{activity_type}"
    params: dict[str, Any] = {"after": after, "until": until, "direction": "asc", "page_size": PAGE_SIZE}
    activities: list[dict[str, Any]] = []
    while True:
        page = await client._request("GET", endpoint, params=params, priority=True)
        if not page:
            break
        activities.extend(page)  # type: ignore[arg-type]
        if len(page) < PAGE_SIZE:
            break
        params["page_token"] = page[-1]["id"]  # type: ignore[index]
    return activities


async def fetch_activities(
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    paper: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    client = AlpacaHttpClient(paper=paper, timeout=30)
    after, until = dt_to_iso_8601(start), dt_to_iso_8601(end)
    try:
        fills = await _get_activities(client, "FILL", after, until)
        try:
            fees = await _get_activities(client, "FEE", after, until)
        except Exception as e:
            print(f"WARNING: could not fetch FEE activities ({e}); fees will be reported as 0")
            fees = []
    finally:
        await client.close()

    symbol = symbol.upper()
    fills = [f for f in fills if f.get("symbol", "").upper() == symbol]
    fees = [f for f in fees if f.get("symbol", "").upper() == symbol]
    return fills, fees


# -- Round-trip reconstruction ----------------------------------------------------------------


@dataclass
class Trade:
    direction: str  # "LONG" | "SHORT"
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp | None = None
    max_qty: float = 0.0
    shares_traded: float = 0.0
    entry_notional: float = 0.0
    entry_qty: float = 0.0
    exit_notional: float = 0.0
    exit_qty: float = 0.0
    realized_pnl: float = 0.0
    num_fills: int = 0
    order_ids: set[str] = field(default_factory=set)

    @property
    def avg_entry(self) -> float:
        return self.entry_notional / self.entry_qty if self.entry_qty else math.nan

    @property
    def avg_exit(self) -> float:
        return self.exit_notional / self.exit_qty if self.exit_qty else math.nan


def build_trades(fills: list[dict[str, Any]]) -> tuple[list[Trade], Trade | None]:
    """
    Group fills into flat-to-flat round trips using average-cost accounting.

    Assumes the position is flat at the start of the range. A fill that flips the position
    closes the current trade and opens a new one with the remainder.

    Returns the closed trades and the still-open trade (if any).
    """
    fills = sorted(fills, key=lambda f: (pd.Timestamp(f["transaction_time"]), f["id"]))
    trades: list[Trade] = []
    current: Trade | None = None
    position = 0.0  # signed
    avg_cost = 0.0

    for fill in fills:
        ts = pd.Timestamp(fill["transaction_time"]).tz_convert(MARKET_TZ)
        price = float(fill["price"])
        sign = 1.0 if fill["side"] == "buy" else -1.0  # "sell" and "sell_short" reduce
        remaining = float(fill["qty"])

        while remaining > QTY_EPS:
            if abs(position) < QTY_EPS:
                current = Trade(direction="LONG" if sign > 0 else "SHORT", entry_time=ts)
                position, avg_cost = 0.0, 0.0

            assert current is not None
            current.num_fills += 1
            current.order_ids.add(fill["order_id"])

            if sign * position >= 0:  # opening / adding
                qty = remaining
                new_abs = abs(position) + qty
                avg_cost = (avg_cost * abs(position) + price * qty) / new_abs
                position += sign * qty
                current.entry_notional += price * qty
                current.entry_qty += qty
                current.max_qty = max(current.max_qty, abs(position))
            else:  # reducing / closing
                qty = min(remaining, abs(position))
                pnl_per_share = (price - avg_cost) if position > 0 else (avg_cost - price)
                current.realized_pnl += pnl_per_share * qty
                position += sign * qty
                current.exit_notional += price * qty
                current.exit_qty += qty
                if abs(position) < QTY_EPS:
                    position = 0.0
                    current.exit_time = ts
                    trades.append(current)
                    current = None

            remaining -= qty
            # shares_traded is attributed to whichever trade the slice belonged to
            (trades[-1] if current is None else current).shares_traded += qty

    return trades, current


def trades_to_df(trades: list[Trade], fees: list[dict[str, Any]]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame(
        {
            "entry_time": [t.entry_time for t in trades],
            "exit_time": [t.exit_time for t in trades],
            "direction": [t.direction for t in trades],
            "max_qty": [t.max_qty for t in trades],
            "shares_traded": [t.shares_traded for t in trades],
            "avg_entry": [t.avg_entry for t in trades],
            "avg_exit": [t.avg_exit for t in trades],
            "dollar_volume": [t.entry_notional + t.exit_notional for t in trades],
            "num_fills": [t.num_fills for t in trades],
            "num_orders": [len(t.order_ids) for t in trades],
            "gross_pnl": [t.realized_pnl for t in trades],
        },
    )
    df["hold_secs"] = (df["exit_time"] - df["entry_time"]).dt.total_seconds()
    df["date"] = df["exit_time"].dt.date
    df["return_pct"] = df["gross_pnl"] / (df["avg_entry"] * df["max_qty"]) * 100

    # Fee activities are daily, so spread each day's fees across that day's trades by share volume
    df["fees"] = 0.0
    if fees:
        fee_df = pd.DataFrame(fees)
        fee_df["date"] = pd.to_datetime(fee_df["date"]).dt.date
        daily_fees = fee_df.groupby("date")["net_amount"].apply(lambda s: -s.astype(float).sum())
        for day, fee in daily_fees.items():
            mask = df["date"] == day
            if mask.any():
                weights = df.loc[mask, "shares_traded"] / df.loc[mask, "shares_traded"].sum()
                df.loc[mask, "fees"] = fee * weights
    df["net_pnl"] = df["gross_pnl"] - df["fees"]
    return df


# -- Statistics -------------------------------------------------------------------------------


def _max_streak(flags: pd.Series) -> int:
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def _max_drawdown(pnl: pd.Series) -> float:
    if pnl.empty:
        return 0.0
    equity = pnl.cumsum()
    peak = np.maximum.accumulate(np.concatenate([[0.0], equity.to_numpy()]))[1:]
    return float((equity - peak).min())


def trade_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Statistics over a set of round-trip trades."""
    pnl = df["net_pnl"]
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    n = len(df)
    win_rate = len(wins) / n if n else math.nan
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    longs, shorts = df[df["direction"] == "LONG"], df[df["direction"] == "SHORT"]

    return {
        "trades": n,
        "long / short": f"{len(longs)} / {len(shorts)}",
        "winners": len(wins),
        "losers": len(losses),
        "scratch": n - len(wins) - len(losses),
        "win_rate": win_rate,
        "long_win_rate": (longs["net_pnl"] > 0).mean() if len(longs) else math.nan,
        "short_win_rate": (shorts["net_pnl"] > 0).mean() if len(shorts) else math.nan,
        "gross_pnl": df["gross_pnl"].sum(),
        "fees": df["fees"].sum(),
        "net_pnl": pnl.sum(),
        "long_pnl": longs["net_pnl"].sum(),
        "short_pnl": shorts["net_pnl"].sum(),
        "avg_trade": pnl.mean() if n else math.nan,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "win_loss_ratio": avg_win / -avg_loss if avg_loss else math.inf,
        "largest_win": wins.max() if len(wins) else 0.0,
        "largest_loss": losses.min() if len(losses) else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else math.inf,
        "expectancy": win_rate * avg_win + (1 - win_rate) * avg_loss if n else math.nan,
        "avg_return_pct": df["return_pct"].mean() if n else math.nan,
        "max_consec_wins": _max_streak(pnl > 0),
        "max_consec_losses": _max_streak(pnl < 0),
        "max_drawdown_trades": _max_drawdown(pnl),
        "avg_hold": _fmt_secs(df["hold_secs"].mean()) if n else "-",
        "avg_hold_win": _fmt_secs(df.loc[pnl > 0, "hold_secs"].mean()) if len(wins) else "-",
        "avg_hold_loss": _fmt_secs(df.loc[pnl < 0, "hold_secs"].mean()) if len(losses) else "-",
        "avg_size": df["max_qty"].mean() if n else math.nan,
        "shares_traded": df["shares_traded"].sum(),
        "dollar_volume": df["dollar_volume"].sum(),
        "pnl_per_share": pnl.sum() / (df["shares_traded"].sum() / 2) if n else math.nan,
        "orders": int(df["num_orders"].sum()),
        "fills": int(df["num_fills"].sum()),
    }


def period_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Trade statistics plus day-level statistics (for multi-day periods)."""
    stats = trade_stats(df)
    daily = df.groupby("date")["net_pnl"].sum()
    green, red = daily[daily > 0], daily[daily < 0]
    std = daily.std(ddof=1) if len(daily) > 1 else math.nan
    downside = daily[daily < 0].std(ddof=1) if len(red) > 1 else math.nan
    stats.update(
        {
            "trading_days": len(daily),
            "green_days": len(green),
            "red_days": len(red),
            "day_win_rate": len(green) / len(daily) if len(daily) else math.nan,
            "avg_day": daily.mean(),
            "avg_green_day": green.mean() if len(green) else 0.0,
            "avg_red_day": red.mean() if len(red) else 0.0,
            "best_day": f"{daily.max():,.2f} ({daily.idxmax()})",
            "worst_day": f"{daily.min():,.2f} ({daily.idxmin()})",
            "daily_std": std,
            "sharpe_daily_pnl_ann": daily.mean() / std * math.sqrt(252) if std else math.nan,
            "sortino_daily_pnl_ann": daily.mean() / downside * math.sqrt(252) if downside else math.nan,
            "max_drawdown_daily": _max_drawdown(daily),
            "max_consec_green_days": _max_streak(daily > 0),
            "max_consec_red_days": _max_streak(daily < 0),
            "avg_trades_per_day": len(df) / len(daily),
        },
    )
    return stats


def daily_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day, g in df.groupby("date"):
        s = trade_stats(g)
        rows.append(
            {
                "date": day,
                "trades": s["trades"],
                "L/S": s["long / short"],
                "win%": s["win_rate"] * 100,
                "gross": s["gross_pnl"],
                "fees": s["fees"],
                "net": s["net_pnl"],
                "avg_win": s["avg_win"],
                "avg_loss": s["avg_loss"],
                "best": s["largest_win"],
                "worst": s["largest_loss"],
                "PF": s["profit_factor"],
                "max_dd": s["max_drawdown_trades"],
                "avg_hold": s["avg_hold"],
                "shares": s["shares_traded"],
            },
        )
    table = pd.DataFrame(rows).set_index("date")
    table["cum_net"] = table["net"].cumsum()
    return table


def monthly_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    months = pd.to_datetime(df["date"]).dt.to_period("M")
    for month, g in df.groupby(months):
        s = period_stats(g)
        rows.append(
            {
                "month": str(month),
                "days": s["trading_days"],
                "green/red": f"{s['green_days']}/{s['red_days']}",
                "trades": s["trades"],
                "win%": s["win_rate"] * 100,
                "gross": s["gross_pnl"],
                "fees": s["fees"],
                "net": s["net_pnl"],
                "avg_day": s["avg_day"],
                "avg_trade": s["avg_trade"],
                "PF": s["profit_factor"],
                "W/L": s["win_loss_ratio"],
                "max_dd": s["max_drawdown_daily"],
                "best_day": s["best_day"],
                "worst_day": s["worst_day"],
            },
        )
    table = pd.DataFrame(rows).set_index("month")
    table["cum_net"] = table["net"].cumsum()
    return table


# -- Output -----------------------------------------------------------------------------------


def _fmt_secs(secs: float) -> str:
    if secs is None or math.isnan(secs):
        return "-"
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _fmt_value(key: str, value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)):
        return f"{value:,}"
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if math.isinf(value):
        return "inf"
    if "rate" in key:
        return f"{value * 100:.1f}%"
    if key.endswith("_pct"):
        return f"{value:.3f}%"
    return f"{value:,.2f}"


def _print_section(title: str) -> None:
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")


def print_report(symbol: str, start: str, end: str, df: pd.DataFrame, open_trade: Trade | None) -> None:
    with pd.option_context(
        "display.max_rows", None,
        "display.max_columns", None,
        "display.width", 250,
        "display.float_format", "{:,.2f}".format,
    ):
        _print_section(f"DAILY SUMMARY  {symbol}  {start} -> {end}")
        print(daily_table(df).to_string())

        _print_section("MONTHLY SUMMARY")
        print(monthly_table(df).to_string())

    _print_section("TOTAL")
    stats = period_stats(df)
    width = max(len(k) for k in stats)
    for key, value in stats.items():
        print(f"  {key:<{width}}  {_fmt_value(key, value)}")

    if open_trade is not None:
        print(
            f"\nNOTE: open {open_trade.direction} position at end of range "
            f"(opened {open_trade.entry_time}, entry qty {open_trade.entry_qty:g} @ {open_trade.avg_entry:.4f}, "
            f"realized so far {open_trade.realized_pnl:,.2f}) is excluded from the stats.",
        )


# -- Entry point ------------------------------------------------------------------------------


def main(
    symbol: str,
    start_date: str,
    end_date: str | None = None,
    paper: bool = False,
    csv_dir: Path | None = None,
) -> None:
    """
    Parameters
    ----------
    symbol : str
        Ticker symbol, e.g. "AAPL".
    start_date : str
        Start date (inclusive, YYYY-MM-DD, US/Eastern).
    end_date : str, optional
        End date (inclusive, YYYY-MM-DD). Defaults to today.
    paper : bool, default False
        Use the paper account instead of live.
    csv_dir : Path, optional
        Directory to write trades/daily/monthly CSVs.
    """
    symbol = symbol.upper()
    start = pd.Timestamp(start_date, tz=MARKET_TZ).normalize()
    end_day = pd.Timestamp(end_date, tz=MARKET_TZ) if end_date else pd.Timestamp.now(tz=MARKET_TZ)
    end = end_day.normalize() + pd.Timedelta(days=1)  # exclusive upper bound

    fills, fees = asyncio.run(fetch_activities(symbol, start, end, paper=paper))
    print(f"Fetched {len(fills)} fills and {len(fees)} fee activities for {symbol} "
          f"({'paper' if paper else 'live'} account)")
    if not fills:
        return

    trades, open_trade = build_trades(fills)
    df = trades_to_df(trades, fees)
    if df.empty:
        print("No closed round-trip trades in range.")
        return

    print_report(symbol, start.date().isoformat(), (end - pd.Timedelta(days=1)).date().isoformat(), df, open_trade)

    if csv_dir:
        csv_dir.mkdir(parents=True, exist_ok=True)
        tag = f"{symbol}_{start.date()}_{(end - pd.Timedelta(days=1)).date()}"
        df.to_csv(csv_dir / f"{tag}_trades.csv", index=False)
        daily_table(df).to_csv(csv_dir / f"{tag}_daily.csv")
        monthly_table(df).to_csv(csv_dir / f"{tag}_monthly.csv")
        print(f"\nCSVs written to {csv_dir}")


if __name__ == "__main__":
    SYMBOL = "AMZN"
    START_DATE = "2026-09-01"
    END_DATE = None  # inclusive; None = today
    PAPER = False
    CSV_DIR = None  # e.g. Path("data/performance_reports")

    main(
        symbol=SYMBOL,
        start_date=START_DATE,
        end_date=END_DATE,
        paper=PAPER,
        csv_dir=CSV_DIR,
    )
