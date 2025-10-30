import os
import sys

sys.path.append(os.getcwd())
from flask import Flask, render_template, jsonify

from flask_restful import Api
from flask_cors import CORS

from custom.app_utils.process_data import combine_tbbo_and_metrics_data
from custom.app_utils.viz import CreateMarkers

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, expose_headers=["Content-Range"])

api = Api(app)

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
    # bars = get_one_min_bars()
    # bars = [convert_bar_to_json(bar) for bar in bars]
    bars = []

    # ticks = get_and_convert_tbbo()
    ticks = combine_tbbo_and_metrics_data()
    price_markers = CreateMarkers().load_markers()

    records = dict(
        ticks=ticks,
        ten_sec=[],
        one_min=bars,
        macd=[],
        price_markers=price_markers,
        TickChartLines=[
            # dict(key='ask', color='#EF5350CC', width=1, type=1),
            # dict(key='bid', color='#26A69ACC', width=1, type=1),
            dict(key='vwap_lower', color='#4590d1', width=1, type=0),
            dict(key='vwap_value', color='#45d14c', width=1, type=0),
            dict(key='vwap_upper', color='red', width=1, type=0),
            # dict(key='day_vwap_value', color='green', width=1, type=0),
        ]
    )
    return jsonify(records)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True)
