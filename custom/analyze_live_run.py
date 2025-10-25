import json
import pandas as pd
from pathlib import Path

# Specify the run directory
# run_dir = Path("data/runs/20251021_164830")  # Actual trade on live account
run_dir = Path("data/runs/20251023_162915")  # Paper QLGN buy+sell
run_dir = Path("data/runs/20251023_165218")  # Paper QLGN buy+sell synced to time.apple.com
run_dir = Path("data/runs/20251023_165535")  # Paper QLGN buy+sell synced to pool.ntp.org
run_dir = Path("data/runs/20251024_155750")  # Live AAPL


with open(run_dir / "config.json", "r") as f:
    config = json.load(f)
with open(run_dir / "alpaca_trade_updates.json", "r") as f:
    trade_updates = json.load(f)

trade_updates_df = pd.DataFrame(trade_updates)

# Load ticks_with_orders.pkl
df = pd.read_pickle(run_dir / "ticks_with_orders.pkl")
for col in ["ts_event", "ts_recv"]:
    df[col] = df[col].apply(lambda s: pd.Timestamp(s, tz="UTC"))

basepoint = "ts_recv"
time_cols = ["ts_event", "ts_recv", "ts_clock", "ts_now", "order_event", "order_submit", "ts_now_after_buy"]
df2 = df[time_cols]
for col in time_cols:
    if col != basepoint:
        df2[col] = df2[col] - df2[basepoint]
        df2[col] = df2[col].dt.microseconds

pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)
pd.set_option("display.max_colwidth", None)
print(df)
report = {}


def series_to_stats(s1, s2):
    diff = (s2 - s1).dt.total_seconds() * 1e3
    return {
        "count": diff.count(),
        "min": diff.min(),
        "max": diff.max(),
        "mean": diff.mean(),
        "stddev": diff.std(),
    }


report["tick_event_to_usable"] = series_to_stats(df["ts_event"], df["ts_now"])
report["tick_rev_to_usable"] = series_to_stats(df["ts_recv"], df["ts_now"])

print(pd.DataFrame(report).T.round(1))
"""

apple ethernet
round-trip min/avg/max/stddev = 12.344/17.761/39.942/3.012 ms

nyc ethernet
round-trip min/avg/max/stddev = 13.799/18.072/36.389/3.097 ms

nyc wifi
round-trip min/avg/max/stddev = 14.238/22.172/41.195/4.517 ms
"""
