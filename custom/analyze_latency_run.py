import json
import pandas as pd
from pathlib import Path

# Specify the run directory
# run_dir = Path("data/runs/latency/20251027_TSLA_live")  # TSLA live account 50 trades
# run_dir = Path("data/runs/latency/20251027_TSLA_paper")  # TSLA paper account 200 trades
run_dir = Path("data/runs/20251114_093405")


with open(run_dir / "config.json", "r") as f:
    config = json.load(f)
with open(run_dir / "order_submissions.json", "r") as f:
    _order_submissions = []
    for line in f:
        _order_submissions.append(json.loads(line))
with open(run_dir / "alpaca_trade_updates.json", "r") as f:
    _ws_updates = json.load(f)
with open(run_dir / "orders_events.json", "r") as f:
    _order_events = json.load(f)

order_submissions_df = pd.DataFrame(_order_submissions)
ws_updates_df = pd.DataFrame(_ws_updates)
order_events_df = pd.DataFrame(_order_events)

# Load ticks_with_orders.pkl
ticks_and_metrics = load_ticks_and_metrics_file(run_dir)
df = pd.DataFrame(ticks_and_metrics).T

for col in ["ts_event", "ts_recv"]:
    df[col] = pd.to_datetime(df[col], unit="ns")


def order_analysis(order_submissions_df, ws_updates_df):
    updates = ws_updates_df.copy()
    to_replace = ~updates["replaces"].isnull()
    replace_map = updates[to_replace][["id", "replaces"]].set_index("id").to_dict()["replaces"]
    updates.loc[to_replace, "id"] = updates.loc[to_replace, "replaces"]
    cols = ["id", "msg_received_dt", "at", "event", "timestamp", "updated_at", "replaced_at", "canceled_at"]
    updates = updates[cols]

    submissions = order_submissions_df.copy()

    def replace_if_needed(item):
        if item in replace_map:
            return replace_map[item]
        return item

    submissions["id"] = submissions["order_id"].apply(replace_if_needed)
    subs_cols = ["type", "id", "submit_dt"]
    submissions = submissions[subs_cols]

    def match_order(row):
        id = row["id"]
        type_ = row["type"]
        if type_ == "limit":
            event_name = "new"
        elif type_ == "replace":
            event_name = "replaced"
        elif type_ == "cancel":
            event_name = "canceled"
        else:
            raise ValueError(f"Unknown order type: {type_}")
        value = updates[(updates["id"] == id) & (updates["event"] == event_name)]["timestamp"].values[0]
        return pd.Timestamp(value)

    submissions["executed"] = submissions.apply(match_order, axis=1)

    submissions["submit_dt"] = pd.to_datetime(submissions["submit_dt"])
    submissions["latency"] = (submissions["executed"] - submissions["submit_dt"]).dt.total_seconds() * 1e3

    def p80(series):
        return series.quantile(0.8)

    def p90(series):
        return series.quantile(0.9)

    gp = submissions.groupby("type").agg({"latency": ["count", "min", "max", "mean", "std", p80, p90]})
    print(gp.round(1))
    return submissions


submission_latency = order_analysis(order_submissions_df, ws_updates_df)

basepoint = "ts_recv"
time_cols = ["ts_event", "ts_recv", "ts_clock", "ts_now", "order_event", "order_submit", "ts_now_after"]
df2 = df[time_cols].copy()
for col in time_cols:
    if col != basepoint:
        df2[col] = df2[col] - df2[basepoint]
        df2[col] = df2[col].dt.microseconds

pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)
pd.set_option("display.max_colwidth", None)
# print(df)
report = {}


def series_to_stats(s1, s2):
    diff = (s2 - s1).dt.total_seconds() * 1e3
    return {
        "count": diff.count(),
        "min": diff.min(),
        "max": diff.max(),
        "mean": diff.mean(),
        "std": diff.std(),
    }


report["tick_event_to_usable"] = series_to_stats(df["ts_event"], df["ts_now"])
# report["tick_rev_to_usable"] = series_to_stats(df["ts_recv"], df["ts_now"])

print(pd.DataFrame(report).T.round(1))
"""

apple ethernet
round-trip min/avg/max/stddev = 12.344/17.761/39.942/3.012 ms

nyc ethernet
round-trip min/avg/max/stddev = 13.799/18.072/36.389/3.097 ms

nyc wifi
round-trip min/avg/max/stddev = 14.238/22.172/41.195/4.517 ms
"""
