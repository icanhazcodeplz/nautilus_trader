import os
import sys

sys.path.append(os.getcwd())
from flask import Flask, render_template, jsonify

from flask_restful import Api
from flask_cors import CORS

from custom.backtest_runner import analyze_trades
from custom.utils.paths import data_subdir
from custom.utils.orders_to_trades import orders_to_trades
from custom.artifacts import CreateMarkers, ArtifactsIO, VIZ_ARTIFACTS_PATH

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, expose_headers=["Content-Range"])

api = Api(app)
artifacts_dir = data_subdir("runs", "20260206_160334")
artifacts_dir = VIZ_ARTIFACTS_PATH

artifacts_io = ArtifactsIO(artifacts_dir)


def convert_bar_to_json(bar):
    return {
        "time": bar.ts_event / 1e9,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "v": int(bar.volume),
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def get_data():
    ticks_dict = artifacts_io.load_ticks_and_metrics_file()

    signals = artifacts_io.load_signals()

    orders_report = artifacts_io.load_orders_report()
    trades, sell_legs = orders_to_trades(orders_report)
    analyze_trades(trades, print_report=True)

    if artifacts_dir == VIZ_ARTIFACTS_PATH:
        # FIXME: Refactor so backtests use the same markers!
        markers = CreateMarkers().create_trades_markers(trades, sell_legs)
    else:
        trades["desc"] = trades["buy_id"].astype(str)
        trades_markers = CreateMarkers().create_trades_markers(trades)
        fills = artifacts_io.get_fills()
        fill_markers = CreateMarkers().create_fill_markers(fills)
        markers = sorted(trades_markers + fill_markers, key=lambda x: x["time"])

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

    # Flatten ticks into a list and sort by time
    ticks = [{"time": int(t), **vals} for t, vals in ticks_dict.items()]
    ticks = sorted(ticks, key=lambda x: x["time"])

    # Convert time to string for JS client
    for t in ticks:
        t["time"] = str(t["time"])

    for s in signals:
        s["time"] = str(s["time"])

    baby_blue = "#59e5ea"
    records = dict(
        ticks=ticks,
        ten_sec=[],
        one_min=[],
        macd=[],
        fill_markers=markers,
        signals=signals,
        TickChartLines=[
            dict(key="vwap_value", color="#45d14c", width=2, type=0),
            dict(key="vwap_low", color="red", width=1.5, type=0),
            dict(key="vwap_low_inner", color=baby_blue, width=1, type=0),
            dict(key="vwap_low_outer", color=baby_blue, width=1, type=0),
            dict(key="vwap_high", color=baby_blue, width=1.5, type=0),
            dict(key="vwap_high_inner", color="red", width=1, type=0),
            dict(key="vwap_high_outer", color="red", width=1, type=0),
        ],
        SecondaryTickChartLines=[
            dict(key="vwap_pressure", color="#e70f0f", color_negative=baby_blue, width=1, type=0),
        ],
    )
    return jsonify(records)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
