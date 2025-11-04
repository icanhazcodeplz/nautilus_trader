import os
import sys

sys.path.append(os.getcwd())
from flask import Flask, render_template, jsonify

from flask_restful import Api
from flask_cors import CORS

from custom.app_utils.viz import CreateMarkers, load_signals_file, load_ticks_and_metrics_from_txt_file

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
    ticks_dict = load_ticks_and_metrics_from_txt_file()
    markers = CreateMarkers().load_markers()
    signals = load_signals_file()

    # Markers may not have same time as a tick
    for m in markers:
        time_ = str(m['time'])
        if time_ in ticks_dict:
            # If tick already exists, add fill price to that tick
            ticks_dict[time_]['fill'] = m['price']
        else:
            # If not tick at that time, create a new tick with fill price
            ticks_dict[time_] = {'fill': m['price']}
        # Convert to string because of JS
        m['time'] = str(m['time'])


    # Flatten ticks into a list and sort by time
    ticks = [{"time":int(t), **vals} for t, vals in ticks_dict.items()]
    ticks = sorted(ticks, key=lambda x: x['time'])

    # Convert time to string for JS client
    for t in ticks:
        t['time'] = str(t['time'])

    for s in signals:
        s['time'] = str(s['time'])

    records = dict(
        ticks=ticks,
        ten_sec=[],
        one_min=[],
        macd=[],
        fill_markers=markers,
        signals=signals,
        TickChartLines=[
            # dict(key='ask', color='#EF5350CC', width=1, type=1),
            dict(key='vwap_lower', color='#4590d1', width=1, type=0),
            dict(key='vwap_value', color='#45d14c', width=1, type=0),
            dict(key='vwap_upper', color='red', width=1, type=0),
            # dict(key='day_vwap_value', color='green', width=1, type=0),
        ]
    )
    return jsonify(records)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True)
