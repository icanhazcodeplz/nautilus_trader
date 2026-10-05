#!/usr/bin/env python3
"""
Report a paper/live run's tick latency right after the market open, and how long the venue took
to answer the strategy's order requests.

Pass several runs to get each run's report followed by stats pooled across all of them.

Set the values in the `run_latency_report(...)` call at the bottom of this file and run it:

    python custom/backtest_scripts/run_latency_report.py

Or call `run_latency_report` from a notebook and work with the DataFrames it returns.
"""

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from custom.artifacts import ArtifactsIO
from custom.utils.paths import data_subdir

MARKET_TZ = ZoneInfo("America/New_York")

# Each request the strategy sends, keyed by the event that marks it sent, and the events that
# answer it. The answers' ts_init is when the node got them, so answer.ts_init - request.ts_event
# is the round trip as the node saw it -- no venue clock involved.
_REQUEST_ANSWERS = {
    "OrderSubmitted": ("submit", ("OrderAccepted", "OrderRejected")),
    "OrderPendingUpdate": ("modify", ("OrderUpdated", "OrderModifyRejected")),
    "OrderPendingCancel": ("cancel", ("OrderCanceled", "OrderCancelRejected")),
}


@dataclass
class LatencyReport:
    run_dir: Path
    # One row per tick: ts_event, ts_init, latency_ms, dt_local
    ticks: pd.DataFrame
    # The ticks in the open window, with the start of the bin each falls in
    open_ticks: pd.DataFrame
    # Venue-to-adapter latency per bin after the open, indexed by bin start (local time)
    open_bins: pd.DataFrame
    # One row per order request and its answer
    requests: pd.DataFrame
    # Round trip stats per request kind
    request_summary: pd.DataFrame
    # One row per fill, with how long after the venue's fill time the node had it
    fills: pd.DataFrame


@dataclass
class AggregateReport:
    # One row per run: its open-window latency, request round trips and fill lags
    runs: pd.DataFrame
    # Venue-to-adapter latency per bin after the open, pooled across runs, indexed by time of day
    open_bins: pd.DataFrame
    # Round trip stats per request kind, pooled across runs
    request_summary: pd.DataFrame
    # Fill lag stats, pooled across runs
    fill_summary: pd.DataFrame


def latest_paper_run() -> Path:
    runs_dir = data_subdir("paper_runs")
    runs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    if not runs:
        raise FileNotFoundError(f"No runs found in {runs_dir}")
    return runs[-1]


def load_tick_latencies(run_dir: Path) -> pd.DataFrame:
    """Return one row per tick in the run's ticks_and_metrics.pkl, sorted by event time."""
    data = ArtifactsIO(run_dir).load_ticks_and_metrics_file()
    df = pd.DataFrame(
        [
            {"ts_event": v["ts_event"], "ts_init": v["ts_init"]}
            for v in data.values()
            if "ts_event" in v
        ],
    )
    if df.empty:
        raise ValueError(f"No tick entries with ts_event found in {run_dir}/ticks_and_metrics.pkl")

    df["latency_ms"] = (df["ts_init"] - df["ts_event"]) / 1e6
    df["dt_local"] = pd.to_datetime(df["ts_event"], unit="ns", utc=True).dt.tz_convert(MARKET_TZ)
    return df.sort_values("ts_event", ignore_index=True)


def open_ticks(ticks: pd.DataFrame, market_open: str, seconds: float, bin_s: float) -> pd.DataFrame:
    """Return the ticks with event time in the first `seconds` after the open, tagged with their bin."""
    # Take the session date from the last tick, since startup backfill can reach back a day
    open_ts = ticks["dt_local"].iloc[-1].normalize() + pd.Timedelta(f"{market_open}:00")
    in_window = (ticks["dt_local"] >= open_ts) & (ticks["dt_local"] < open_ts + pd.Timedelta(seconds=seconds))
    window = ticks[in_window]
    width = pd.Timedelta(seconds=bin_s)
    return window.assign(bin_start=open_ts + (window["dt_local"] - open_ts) // width * width)


def open_bins(open_ticks: pd.DataFrame, by: pd.Series) -> pd.DataFrame:
    """
    Venue-to-adapter latency in fixed bins by event time, to tell a stalled feed apart from a
    backed-up one or a few stray late ticks.

    A stall shows up as a burst of ticks whose latency falls toward zero. A backed-up feed
    keeps arriving at a steady pace but with latency held high for many bins in a row. Stray
    late ticks show up as many short runs of late ticks rather than a few long ones.
    """
    bins = open_ticks.groupby(by.rename("start"))["latency_ms"].agg(["size", "min", "median", "max"])
    return bins.rename(columns={"size": "n"})


def load_order_events(run_dir: Path) -> dict[str, list]:
    """
    Return the run's order events grouped by client order id and sorted by ts_init.

    Backtests save them to order_updates.pkl. Live and paper runs keep them in the cache
    database, which is only readable while that database is up; if it isn't, this returns
    an empty dict.
    """
    artifacts_io = ArtifactsIO(run_dir)
    events_by_order: dict[str, list] = {}
    if (run_dir / "order_updates.pkl").exists():
        for event in artifacts_io.load_backtest_order_updates_to_pkl():
            events_by_order.setdefault(event.client_order_id.value, []).append(event)
    else:
        try:
            orders = artifacts_io.read_db_order_events()
        except Exception as e:
            print(f"Order events unavailable (cache database not readable: {e})")
            return {}
        for order in orders.values():
            events_by_order[order.client_order_id.value] = list(order.events)
    return {cid: sorted(events, key=lambda e: e.ts_init) for cid, events in events_by_order.items()}


def _local_time(ts_ns: int) -> pd.Timestamp:
    return pd.Timestamp(ts_ns, unit="ns", tz="UTC").tz_convert(MARKET_TZ)


def order_requests(events_by_order: dict[str, list]) -> pd.DataFrame:
    """
    One row per request the strategy sent: submit, modify or cancel, and how long until the node
    had the venue's answer. Answers with no request in flight (a reconciliation replaying an old
    update, say) are skipped, as are requests still unanswered when the run ended.
    """
    rows = []
    for cid, events in events_by_order.items():
        pending = None
        for event in events:
            name = type(event).__name__
            if name in _REQUEST_ANSWERS:
                pending = event
                continue
            if pending is None:
                continue
            kind, answers = _REQUEST_ANSWERS[type(pending).__name__]
            if name in answers:
                rows.append(
                    {
                        "sent": _local_time(pending.ts_event),
                        "kind": kind,
                        "client_order_id": cid,
                        "answer": name.removeprefix("Order"),
                        "round_trip_ms": (event.ts_init - pending.ts_event) / 1e6,
                    },
                )
                pending = None
            elif name == "OrderFilled" and kind == "submit":
                # A marketable order can fill before its accept is processed
                pending = None
    columns = ["sent", "kind", "client_order_id", "answer", "round_trip_ms"]
    return pd.DataFrame(rows, columns=columns).sort_values("sent", ignore_index=True)


def request_summary(requests: pd.DataFrame) -> pd.DataFrame:
    """Round trip stats per request kind, plus how many were rejected."""
    summary = (
        requests.assign(rejected=requests["answer"].str.endswith("Rejected"))
        .groupby("kind")
        .agg(
            n=("round_trip_ms", "size"),
            rejected=("rejected", "sum"),
            median=("round_trip_ms", "median"),
            p90=("round_trip_ms", lambda s: s.quantile(0.9)),
            min=("round_trip_ms", "min"),
            max=("round_trip_ms", "max"),
        )
    )
    return summary.reindex([k for k in ("submit", "modify", "cancel") if k in summary.index])


def fill_summary(fills: pd.DataFrame) -> pd.DataFrame:
    return fills["lag_ms"].describe().to_frame().T


def order_fills(events_by_order: dict[str, list]) -> pd.DataFrame:
    """How long after the venue's fill time the node had each fill (includes any clock offset)."""
    rows = [
        {
            "filled": _local_time(event.ts_event),
            "client_order_id": cid,
            "lag_ms": (event.ts_init - event.ts_event) / 1e6,
        }
        for cid, events in events_by_order.items()
        for event in events
        if type(event).__name__ == "OrderFilled"
    ]
    columns = ["filled", "client_order_id", "lag_ms"]
    return pd.DataFrame(rows, columns=columns).sort_values("filled", ignore_index=True)


def build_report(run_dir: Path, market_open: str, open_detail_s: float, open_detail_bin_s: float) -> LatencyReport:
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")
    ticks = load_tick_latencies(run_dir)
    window = open_ticks(ticks, market_open, open_detail_s, open_detail_bin_s)
    events_by_order = load_order_events(run_dir)
    requests = order_requests(events_by_order)
    return LatencyReport(
        run_dir=run_dir,
        ticks=ticks,
        open_ticks=window,
        open_bins=open_bins(window, window["bin_start"]),
        requests=requests,
        request_summary=request_summary(requests),
        fills=order_fills(events_by_order),
    )


def aggregate_reports(reports: list[LatencyReport]) -> AggregateReport:
    """Pool the runs' open-window ticks, requests and fills, and summarize each run in one row."""
    runs = pd.DataFrame(
        [
            {
                "run": r.run_dir.name,
                "open_ticks": len(r.open_ticks),
                "open_median_ms": r.open_ticks["latency_ms"].median(),
                "open_max_ms": r.open_ticks["latency_ms"].max(),
                "requests": len(r.requests),
                "request_median_ms": r.requests["round_trip_ms"].median(),
                "fills": len(r.fills),
                "fill_median_ms": r.fills["lag_ms"].median(),
            }
            for r in reports
        ],
    ).set_index("run")
    # Every run's bins start at the same time of day, so pool them by that
    pooled_ticks = pd.concat([r.open_ticks for r in reports], ignore_index=True)
    pooled_bins = open_bins(pooled_ticks, pooled_ticks["bin_start"].dt.strftime("%H:%M:%S.%f").str[:-3])
    pooled_requests = pd.concat([r.requests for r in reports], ignore_index=True)
    pooled_fills = pd.concat([r.fills for r in reports], ignore_index=True)
    return AggregateReport(
        runs=runs,
        open_bins=pooled_bins,
        request_summary=request_summary(pooled_requests),
        fill_summary=fill_summary(pooled_fills),
    )


def run_latency_report(
    run: str | None = None,
    run_dir: str | Path | list[str | Path] | None = None,
    market_open: str = "09:30",
    open_detail_s: float = 15.0,
    open_detail_bin_s: float = 0.25,
) -> tuple[list[LatencyReport], AggregateReport]:
    """
    Build the latency report for each run, and stats pooled across all of them.

    Parameters
    ----------
    run : str, optional
        Run timestamp directory under data/paper_runs, e.g. "20260922_092920". None uses the
        most recent run there.
    run_dir : str, Path, or list of them, optional
        Full path to a run's artifacts directory, or a list of them. Takes precedence over
        `run`, for runs that live somewhere other than data/paper_runs.
    market_open : str
        "HH:MM" local market time.
    open_detail_s : float
        How many seconds after the open to profile.
    open_detail_bin_s : float
        Bin width in seconds for that profile.

    """
    if run_dir is not None:
        run_dirs = [Path(d) for d in (run_dir if isinstance(run_dir, list) else [run_dir])]
    elif run is not None:
        run_dirs = [data_subdir("paper_runs", run)]
    else:
        run_dirs = [latest_paper_run()]

    reports = [build_report(d, market_open, open_detail_s, open_detail_bin_s) for d in run_dirs]
    return reports, aggregate_reports(reports)


def _show(title: str, df: pd.DataFrame, **kwargs) -> None:
    print(f"\n{title}:")
    print(df.to_string(**kwargs) if not df.empty else "  (none)")


def print_report(report: LatencyReport) -> None:
    ticks = report.ticks
    print(f"Run: {report.run_dir}")
    print(f"Ticks: {len(ticks)}, {ticks['dt_local'].iloc[0]} to {ticks['dt_local'].iloc[-1]}")

    open_bins = report.open_bins.copy()
    open_bins.index = open_bins.index.strftime("%H:%M:%S.%f").str[:-3]
    _show("Venue to adapter after the open, by event time (ms)", open_bins, float_format="{:.0f}".format)
    # Backtests' order_updates.pkl keeps no OrderSubmitted/OrderPending* events, so they have no requests
    _show("Request to answer, as seen by the node (ms)", report.request_summary, float_format="{:.1f}".format)
    requests = report.requests.assign(sent=report.requests["sent"].dt.strftime("%H:%M:%S.%f").str[:-3])
    _show("Each request", requests, index=False, float_format="{:.1f}".format)
    _show(
        "Fills, venue fill time to node receipt (includes any clock offset)",
        fill_summary(report.fills),
        float_format="{:.1f}".format,
    )


def print_aggregate(aggregate: AggregateReport) -> None:
    print(f"\n{'=' * 30} All {len(aggregate.runs)} runs {'=' * 30}")
    _show("Each run", aggregate.runs, float_format="{:.1f}".format)
    _show("Venue to adapter after the open, all runs pooled (ms)", aggregate.open_bins, float_format="{:.0f}".format)
    _show("Request to answer, all runs pooled (ms)", aggregate.request_summary, float_format="{:.1f}".format)
    _show("Fills, all runs pooled (ms)", aggregate.fill_summary, float_format="{:.1f}".format)


if __name__ == "__main__":
    reports, aggregate = run_latency_report(
        run="20260930_092805",  # None uses the most recent run under data/paper_runs
        # Full path to a run, or a list of them; overrides `run`
        run_dir=[
            "/Users/brent/code/nautilus_trader/data/runs/20260928_092632",
            "/Users/brent/code/nautilus_trader/data/runs/20260929_092633",
            "/Users/brent/code/nautilus_trader/data/runs/20260930_092805",
            "/Users/brent/code/nautilus_trader/data/runs/20261001_092727",
            "/Users/brent/code/nautilus_trader/data/runs/20261002_092645",
        ],
        market_open="09:30",
        open_detail_s=12.0,
        open_detail_bin_s=0.5,
    )
    for i, report in enumerate(reports):
        if i:
            print(f"\n{'=' * 80}\n")
        print_report(report)
    if len(reports) > 1:
        print_aggregate(aggregate)
