import os
import sys

sys.path.append(os.getcwd())
from flask import Flask, render_template, jsonify

from flask_restful import Resource, Api
from flask_cors import CORS, cross_origin

from examples.backtest_helpers import get_one_min_bars, get_tbbo

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}}, expose_headers=["Content-Range"])

api = Api(app)


def resample_ticks(ticks):
    return ticks


def convert_bar_to_json(bar):
    return {
        "time": int(bar.ts_event / 1e9),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "v": int(bar.volume),
    }

def convert_tbbo_to_json(tbbo):

    return {
        "time": tbbo.ts_event / 1e9,
        "price": float(tbbo.price),
        # "size": int(tbbo.size),
        "bid": float(tbbo.bid if str(tbbo.ask) != 'nan' else float(tbbo.price) - 1.0),
        # "bid_size": int(tbbo.bid_size),
        "ask": float(tbbo.ask if str(tbbo.ask) != 'nan' else tbbo.price + 1.0),
        # "ask_size": int(tbbo.ask_size),
    }

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def get_data():
    bars = get_one_min_bars()
    ticks = get_tbbo()
    ticks_ = [convert_tbbo_to_json(t) for t in ticks]

    # FIXME: get average price instead of dropping duplicates?
    seen_times = set()
    ticks_ = [t for t in ticks_ if not (t['time'] in seen_times or seen_times.add(t['time']))]

    records = dict(
        ticks=ticks_,
        ten_sec=[],
        one_min=[convert_bar_to_json(bar) for bar in bars],
        macd=[],
        bid_markers=[],
        ask_markers=[],
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
