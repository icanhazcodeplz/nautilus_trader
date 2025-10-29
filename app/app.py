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
    bid_markers, ask_markers = CreateMarkers().load_markers()
    # bid_markers, ask_markers = [], []

    records = dict(
        ticks=ticks,
        ten_sec=[],
        one_min=bars,
        macd=[],
        bid_markers=bid_markers,
        ask_markers=ask_markers,
        TickChartLines=[
            # dict(key='ask', color='#EF5350CC', width=1, type=1),
            # dict(key='bid', color='#26A69ACC', width=1, type=1),
            dict(key='vwap', color='red', width=1, type=0),
            dict(key='time_vwap', color='green', width=1, type=0),
        ]
    )
    return jsonify(records)


# @app.route("/10min")
# @cross_origin(supports_credentials=True)
# def get_10min():
#     # cd = ChartData().load_pkl()
#     records = dict(
#         aapl={
#             "start": 200.0,
#             "price": 201.0,
#         }
#     )
#     return jsonify(records)
#
#
# # @cross_origin()
# class PostList(Resource):
#     @cross_origin()
#     def get(self):
#         return jsonify(
#             [
#                 dict(id=1, hi=2, hi2=3),
#                 dict(id=2, hi=2, hi2=3),
#             ]
#         )


# api.add_resource(PostList, "/posts")

if __name__ == "__main__":
    app.run(port=5001, debug=True)
