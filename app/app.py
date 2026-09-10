import os
import sys


sys.path.append(os.getcwd())
import pandas as pd
from flask import Flask
from flask import jsonify
from flask import render_template
from flask_cors import CORS
from flask_restful import Api

from custom.artifacts import BACKTEST_RUNS_PATH
from custom.artifacts import ArtifactsIO
from custom.artifacts import CreateMarkers
from custom.backtest_utils.backtest_run_utils import analyze_trades
from custom.utils.orders_to_trades import orders_to_trades
from custom.utils.paths import data_subdir


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, expose_headers=["Content-Range"])

api = Api(app)
artifacts_dir = data_subdir("runs", "20260313_144138")
artifacts_dir = data_subdir("paper_runs", "20260317_143429")
artifacts_dir = BACKTEST_RUNS_PATH

artifacts_io = ArtifactsIO(artifacts_dir)

baby_blue = "#59e5ea"
light_green = "#45d14c"
highlight_green = "#39ff5e"
light_purple = "#b98ae8"
light_orange = "#f0a860"
white = "#ffffff"


def convert_bar_to_json(bar):
    return {
        "time": bar.ts_event / 1e9,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "v": int(bar.volume),
    }


def create_vert_lines_items(day):
    day_start = pd.Timestamp(int(day), unit="ns", tz="UTC")
    return [
        {
            "time": str((day_start + pd.Timedelta(hours=9, minutes=30)).value),
            "annotation": "9:30",
            "color": "blue",
            "thickness": 2,
        },
        {
            "time": str((day_start + pd.Timedelta(hours=9, minutes=30, seconds=1)).value),
            "annotation": "9:30:01",
            "color": "blue",
            "thickness": 2,
        },
        {
            "time": str((day_start + pd.Timedelta(hours=9, minutes=30, seconds=10)).value),
            "annotation": "9:30:10",
            "color": "blue",
            "thickness": 2,
        },
        {
            "time": str((day_start + pd.Timedelta(hours=9, minutes=31)).value),
            "annotation": "9:31",
            "color": "blue",
            "thickness": 2,
        },
        {
            "time": str((day_start + pd.Timedelta(hours=9, minutes=35)).value),
            "annotation": "9:35",
            "color": "blue",
            "thickness": 2,
        },
    ]

def create_horiz_lines_items(ticks):
    if not ticks:
        return []

    day_start = pd.Timestamp(int(ticks[0]["time"]), unit="ns", tz="UTC").tz_convert("US/Eastern").normalize()
    start_ns = (day_start + pd.Timedelta(hours=9, minutes=30)).value
    end_ns = (day_start + pd.Timedelta(hours=9, minutes=35)).value

    first_min_end_ns = (day_start + pd.Timedelta(hours=9, minutes=31)).value
    first_10s_end_ns = (day_start + pd.Timedelta(hours=9, minutes=30, seconds=10)).value

    window_prices = [t["price"] for t in ticks if start_ns <= int(t["time"]) <= end_ns and "price" in t]
    if not window_prices:
        return []

    first_min_prices = [
        t["price"] for t in ticks if start_ns <= int(t["time"]) <= first_min_end_ns and "price" in t
    ]

    first_10s_prices = [
        t["price"] for t in ticks if start_ns <= int(t["time"]) <= first_10s_end_ns and "price" in t
    ]

    # Last price before the open, falling back to the first price of the session
    pre_open_price = next(
        (t["price"] for t in reversed(ticks) if int(t["time"]) < start_ns and "price" in t),
        window_prices[0],
    )

    return [
        {
            "start_time": str(start_ns),
            "end_time": str(end_ns),
            "price": pre_open_price,
            "color": white,
            "annotation": f"{pre_open_price:.2f}",
            "line_style": "dotted",
            "opacity": 0.8,
        },
        {
            "start_time": str(start_ns),
            "end_time": str(end_ns),
            "price": pre_open_price+0.50,
            "color": highlight_green,
            "annotation": "+.50",
            "line_style": "dotted",
            "opacity": 0.8,
        },
        {
            "start_time": str(start_ns),
            "end_time": str(end_ns),
            "price": pre_open_price+1.00,
            "color": highlight_green,
            "annotation": "+1",
            "line_style": "dotted",
            "opacity": 0.8,
        },
        {
            "start_time": str(start_ns),
            "end_time": str(end_ns),
            "price": pre_open_price-0.50,
            "color": highlight_green,
            "annotation": "-.50",
            "line_style": "dotted",
            "opacity": 0.8,
        },
        {
            "start_time": str(start_ns),
            "end_time": str(end_ns),
            "price": pre_open_price-1.00,
            "color": highlight_green,
            "annotation": "-1",
            "line_style": "dotted",
            "opacity": 0.8,
        },
        # {
        #     "start_time": str(start_ns),
        #     "end_time": str(end_ns),
        #     "price": (high_price := max(window_prices)),
        #     "color": baby_blue,
        #     "annotation": f"{high_price:.2f}",
        #     "line_style": "dotted",
        #     "opacity": 0.8,
        # },
        # {
        #     "start_time": str(start_ns),
        #     "end_time": str(end_ns),
        #     "price": (low_price := min(window_prices)),
        #     "color": baby_blue,
        #     "annotation": f"{low_price:.2f}",
        #     "line_style": "dotted",
        #     "opacity": 0.8,
        # },
        # {
        #     "start_time": str(start_ns),
        #     "end_time": str(first_min_end_ns),
        #     "price": (first_min_high := max(first_min_prices)),
        #     "color": light_purple,
        #     "annotation": f"{first_min_high:.2f}",
        #     "line_style": "dotted",
        #     "opacity": 0.8,
        # },
        # {
        #     "start_time": str(start_ns),
        #     "end_time": str(first_min_end_ns),
        #     "price": (first_min_low := min(first_min_prices)),
        #     "color": light_purple,
        #     "annotation": f"{first_min_low:.2f}",
        #     "line_style": "dotted",
        #     "opacity": 0.8,
        # },
        # {
        #     "start_time": str(start_ns),
        #     "end_time": str(first_10s_end_ns),
        #     "price": (first_10s_high := max(first_10s_prices)),
        #     "color": light_orange,
        #     "annotation": f"{first_10s_high:.2f}",
        #     "line_style": "dotted",
        #     "opacity": 0.8,
        # },
        # {
        #     "start_time": str(start_ns),
        #     "end_time": str(first_10s_end_ns),
        #     "price": (first_10s_low := min(first_10s_prices)),
        #     "color": light_orange,
        #     "annotation": f"{first_10s_low:.2f}",
        #     "line_style": "dotted",
        #     "opacity": 0.8,
        # },
    ]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def get_data():
    ticks_dict = artifacts_io.load_ticks_and_metrics_file()
    symbol = artifacts_io.symbol

    orders_report, fills, positions, order_durations_list = artifacts_io.get_run_data()

    trades, sell_legs = orders_to_trades(orders_report)
    analyze_trades(trades, print_report=True)

    if not trades.empty:
        trades["desc"] = trades["buy_id"].astype(str)
    trades_markers = CreateMarkers().create_trades_markers(trades)
    order_markers = CreateMarkers().create_order_markers(order_durations_list)
    fill_markers = CreateMarkers().create_fill_markers(fills)
    markers = sorted(trades_markers + fill_markers + order_markers, key=lambda x: x["time"])

    # Markers may not have same time as a tick
    for m in markers:
        time_ = str(m["time"])
        if time_ in ticks_dict:
            # If tick already exists, add fill price to that tick
            ticks_dict[time_]["fill"] = m["price"]
        else:
            # If not tick at that time, create a new tick with fill price
            ticks_dict[time_] = {"fill": m["price"]}
        # Convert to string because of JS
        m["time"] = str(m["time"])

    if len(order_durations_list) > 0:
        # Order durations may not have same time as a tick
        for od in order_durations_list:
            for time_key in ("start_time", "end_time"):
                time_ = str(od[time_key])
                if time_ not in ticks_dict:
                    ticks_dict[time_] = {}
            od["start_time"] = str(od["start_time"])
            od["end_time"] = str(od["end_time"])

    first_tick_ns = int(next(iter(ticks_dict)))
    day = pd.Timestamp(first_tick_ns, unit="ns", tz="UTC").tz_convert("US/Eastern").normalize().value
    vert_lines = create_vert_lines_items(day)

    # Vertical lines may not have same time as a tick
    for vl in vert_lines:
        if vl["time"] not in ticks_dict:
            ticks_dict[vl["time"]] = {}

    # For each item in positions, if the time exists as a key in ticks_dict, add the `position` field to that entry in the ticks_dict
    for p in positions:
        time_ = str(p["time"])
        if time_ in ticks_dict:
            ticks_dict[time_]["position"] = p["position"]

    # Flatten ticks into a list and sort by time
    ticks = [{"time": int(t), **vals} for t, vals in ticks_dict.items()]
    ticks = sorted(ticks, key=lambda x: x["time"])

    # Convert time to string for JS client
    for t in ticks:
        t["time"] = str(t["time"])

    title_start_str = (
        pd.Timestamp(int(ticks[0]["time"]), unit="ns", tz="UTC").tz_convert("US/Eastern").strftime("%m/%d %H:%M")
    )
    title_end_str = pd.Timestamp(int(ticks[-1]["time"]), unit="ns", tz="UTC").tz_convert("US/Eastern").strftime("%H:%M")
    title = (
        f"{symbol} {title_start_str} to {title_end_str} {' - backtest' if artifacts_dir == BACKTEST_RUNS_PATH else ''}",
    )
    records = dict(
        title=title,
        ticks=ticks,
        ten_sec=[],
        one_min=[],
        macd=[],
        fill_markers=markers,
        orderDurations=order_durations_list,
        TickChartLines=[
            dict(key="vwap_value", color=light_green, width=2, type=0),
            dict(key="vwap_low", color="red", width=1.5, type=0),
            # dict(key="vwap_low_inner", color=baby_blue, width=1, type=0),
            # dict(key="vwap_low_outer", color=baby_blue, width=1, type=0),
            dict(key="vwap_high", color=baby_blue, width=1.5, type=0),
            # dict(key="vwap_high_inner", color="red", width=1, type=0),
            # dict(key="vwap_high_outer", color="red", width=1, type=0),
        ],
        TickChart2Lines=[
            # dict(key="vwap_pressure", color="#e70f0f", color_negative=baby_blue, width=1, type=0),
            # dict(key="allow_buy", color="#e70f0f", color_negative=baby_blue, width=1, type=1),
            # dict(key="macd_value", color="#e70f0f", color_negative=baby_blue, width=1, type=1),
            # dict(key="pnl", color="green", color_negative="red", width=1, type=1),
        ],
        TickChart3Lines=[
            # dict(key="position", color="#e70f0f", color_negative=baby_blue, width=1, type=1),
        ],
        VertLines=vert_lines,
        HorizLines=create_horiz_lines_items(ticks),
    )
    return jsonify(records)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
