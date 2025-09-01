from examples.backtest_helpers import data_subdir
import json


class CreateMarkers:
    bid_markers_file = data_subdir("viz", 'bid_markers.txt')
    ask_markers_file = data_subdir("viz", 'ask_markers.txt')

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
        with open(self.bid_markers_file, 'w') as f:
            json.dump(bid_markers, f)

        with open(self.ask_markers_file, 'w') as f:
            json.dump(ask_markers, f)

    def load_markers(self):
        with open(self.bid_markers_file, 'r') as f:
            bid_markers = json.load(f)
        with open(self.ask_markers_file, 'r') as f:
            ask_markers = json.load(f)

        return bid_markers, ask_markers


def create_and_save_markers(trades, sell_legs):
    cm = CreateMarkers()
    bid_markers, ask_markers = cm.create_markers(trades, sell_legs)
    cm._save_to_txt(bid_markers, ask_markers)
