#!/usr/bin/env python3
"""
Measure Alpaca's replace latency in live runs, for the backtest's model of slow replaces at the open.

For every run in data/runs, pairs each `PATCH /v2/orders/<id>` in the DEBUG logs with that order's
"replaced" trade update, and takes the time between them. A replace normally lands in ~20ms, but
in the first seconds after the open it can sit pending for several seconds.

Prints the median latency (secs) in each of `WINDOWS` (seconds after 09:30:00 ET, by when the PATCH
was sent) as `OPEN_REPLACE_LATENCY_WINDOWS`, ready to paste into
custom/backtest_scripts/backtest_config.py, followed by each window's samples (ms).
"""
import glob
import json
import re
from pathlib import Path

import pandas as pd

from custom.utils.paths import data_subdir

# [start, stop) seconds after the open
WINDOWS = [(0.0, 2.5), (2.5, 5.0), (5.0, 10.0)]

_PATCH_RE = re.compile(r"PATCH https://\S*/v2/orders/([0-9a-f-]{36})")


def _patch_send_times(run_dir: Path) -> dict[str, pd.Timestamp]:
    """Venue order ID -> when its first PATCH went out, from the run's DEBUG logs."""
    sent = {}
    for log in sorted(glob.glob(str(run_dir / "DEBUG_*.log"))):
        with open(log, errors="replace") as f:
            for line in f:
                match = _PATCH_RE.search(line)
                if match:
                    sent.setdefault(match.group(1), pd.Timestamp(line[:30]))
    return sent


def replace_latencies(runs_dir: Path) -> pd.DataFrame:
    """One row per replace: run, symbol, seconds after the open it was sent, latency in ms."""
    rows = []
    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        updates_path = run_dir / "alpaca_trade_updates.json"
        if not updates_path.exists():
            continue
        sent = _patch_send_times(run_dir)
        if not sent:
            continue
        for update in json.loads(updates_path.read_text()):
            if update.get("event") != "replaced" or update.get("id") not in sent:
                continue
            sent_at = sent[update["id"]].tz_convert("US/Eastern")
            market_open = sent_at.normalize() + pd.Timedelta(hours=9, minutes=30)
            rows.append(
                dict(
                    run=run_dir.name,
                    symbol=update.get("symbol"),
                    secs_after_open=(sent_at - market_open).total_seconds(),
                    latency_ms=(pd.Timestamp(update["timestamp"]) - sent_at).total_seconds() * 1000,
                ),
            )
    return pd.DataFrame(rows)


def _et_str(secs_after_open: float) -> str:
    t = pd.Timestamp("2000-01-01 09:30") + pd.Timedelta(seconds=secs_after_open)
    return t.strftime("%H:%M:%S.%f")[:10]


def main():
    df = replace_latencies(data_subdir("runs"))
    print(f"{len(df)} replaces across {df['run'].nunique()} runs\n")
    print("OPEN_REPLACE_LATENCY_WINDOWS = [")
    for start, stop in WINDOWS:
        in_window = df[(df.secs_after_open >= start) & (df.secs_after_open < stop)]
        median_secs = in_window.latency_ms.median() / 1000
        print(f'    ("{_et_str(start)}", "{_et_str(stop)}", {median_secs:.3f}),')
    print("]\n")
    for start, stop in WINDOWS:
        in_window = df[(df.secs_after_open >= start) & (df.secs_after_open < stop)]
        print(f"{_et_str(start)}-{_et_str(stop)} samples (ms): {sorted(round(ms) for ms in in_window.latency_ms)}")


if __name__ == "__main__":
    main()
