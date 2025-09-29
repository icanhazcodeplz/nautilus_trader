from custom.utils import data_subdir
import json

BID_MARKERS_FILE = data_subdir("viz", 'bid_markers.txt')
ASK_MARKERS_FILE = data_subdir("viz", 'ask_markers.txt')
METRICS_FILE = data_subdir("viz", 'metrics.txt')

def dict_to_file(dict_, filename):
    with open(filename, 'w') as f:
        json.dump(dict_, f)

def load_txt_file_to_dict(filename):
    with open(filename, 'r') as f:
        return json.load(f)

class CreateMarkers:

    @staticmethod
    def _make_marker_dict(dt, position, color, shape, text):
        formatted_dt = dt
        return dict(time=formatted_dt, position=position, color=color, shape=shape, text=text)

    def create_markers(self, trades, sell_legs):
        bid_markers = []
        ask_markers = []

        for _, ser in trades.iterrows():
            ask_markers.append(
                self._make_marker_dict(
                    dt=ser["buy_dt"],
                    position="aboveBar",
                    color="#f77a0c",
                    shape="arrowDown",
                    # text=f"{order['desc']}-{qty}",
                    text=f"{ser["qty"]}",
                )
            )
            pnl = ser["pnl"]
            ask_markers.append(
                self._make_marker_dict(
                    dt=ser["sell_dt"],
                    position="aboveBar",
                    color="#fc0317" if pnl < 0 else "#07fc03",
                    shape="arrowDown",
                    # text=f"{order['desc']}-{qty}",
                    text=f"{pnl}",
                )
            )

        for _, ser in sell_legs.iterrows():
            color = "#fc0317" if ser["pnl"] < 0 else "#07fc03"

            text = f"{ser['qty']}{ser['desc']}"
            bid_markers.append(
                self._make_marker_dict(
                    dt=ser["dt"],
                    position="belowBar",
                    color=color,
                    shape="arrowUp",
                    text=text,
                )
            )

        return bid_markers, ask_markers

    def _save_to_txt(self, bid_markers, ask_markers):
        dict_to_file(bid_markers, BID_MARKERS_FILE)
        dict_to_file(ask_markers, ASK_MARKERS_FILE)

    def load_markers(self):
        bid_markers = load_txt_file_to_dict(BID_MARKERS_FILE)
        ask_markers = load_txt_file_to_dict(ASK_MARKERS_FILE)
        return bid_markers, ask_markers



def create_and_save_markers(trades, sell_legs):
    cm = CreateMarkers()
    bid_markers, ask_markers = cm.create_markers(trades, sell_legs)
    cm._save_to_txt(bid_markers, ask_markers)

def write_to_metrics_txt_file(metrics):
    dict_to_file(metrics, METRICS_FILE)


def load_metrics_from_txt_file():
    return load_txt_file_to_dict(METRICS_FILE)