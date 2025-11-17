import json
import pickle
import pandas as pd

from custom.utils import data_subdir


VIZ_ARTIFACTS_PATH = data_subdir("viz")


def dict_to_file(dict_, filename):
    with open(filename, "w") as f:
        json.dump(dict_, f)


def load_txt_file_to_dict(filename):
    with open(filename, "r") as f:
        return json.load(f)


class ArtifactsIO:
    def __init__(self, directory):
        self.directory = directory

    def save_config(self, config):
        dict_to_file(config, self.directory / "config.json")

    def load_config(self):
        return load_txt_file_to_dict(self.directory / "config.json")

    def save_orders_events(self, events):
        dict_to_file(events, self.directory / "orders_events.json")

    def load_orders_events(self):
        return load_txt_file_to_dict(self.directory / "orders_events.json")

    def save_performance_metrics(self, performance_metrics):
        dict_to_file(performance_metrics, self.directory / "performance_metrics.json")

    def load_performance_metrics(self):
        return load_txt_file_to_dict(self.directory / "performance_metrics.json")

    def save_orders_report(self, orders_report_df: pd.DataFrame):
        orders_report_df.to_pickle(self.directory / "orders_report.pkl")

    def load_orders_report(self):
        return pd.read_pickle(self.directory / "orders_report.pkl")

    def save_signals(self, signals):
        dict_to_file(signals, self.directory / "signals.txt")

    def load_signals(self):
        return load_txt_file_to_dict(self.directory / "signals.txt")

    def save_ticks_and_metrics(self, metrics):
        filepath = self.directory / "ticks_and_metrics.pkl"
        with open(filepath, "wb") as f:
            pickle.dump(metrics, f, protocol=pickle.HIGHEST_PROTOCOL)

    def load_ticks_and_metrics_file(self):
        filepath = self.directory / "ticks_and_metrics.pkl"
        with open(filepath, "rb") as f:
            return pickle.load(f)


class CreateMarkers:
    @staticmethod
    def _make_marker_dict(dt, position, color, shape, text, price):
        return dict(time=dt, position=position, color=color, shape=shape, text=text, price=price)

    def create_trades_markers(self, trades, sell_legs):
        if trades.empty:
            print("No trades to create markers for")
            return []
        price_markers = []

        for _, ser in trades.iterrows():
            # Buy markers
            price_markers.append(
                self._make_marker_dict(
                    dt=ser["buy_dt"],
                    position="aboveBar",
                    color="#f77a0c",
                    shape="arrowDown",
                    text=f"{ser['desc']}|{ser['qty']}",
                    price=round(ser["buy_price"], 2),
                )
            )

            # Trade ending markers
            pnl = ser["pnl"]
            price_markers.append(
                self._make_marker_dict(
                    dt=ser["sell_dt"],
                    position="aboveBar",
                    color="#fc0317" if pnl < 0 else "#07fc03",
                    shape="arrowDown",
                    # text=f"{order['desc']}-{qty}",
                    text=f"{pnl}",
                    price=ser["avg_sell_price"],
                )
            )

        # Sell leg markers
        for _, ser in sell_legs.iterrows():
            color = "#fc0317" if ser["pnl"] < 0 else "#07fc03"

            text = f"{ser['desc']}|{ser['qty']}"
            price_markers.append(
                self._make_marker_dict(
                    dt=ser["dt"],
                    position="belowBar",
                    color=color,
                    shape="arrowUp",
                    text=text,
                    price=ser["price"],
                )
            )

        price_markers.sort(key=lambda x: x["time"])
        return price_markers
