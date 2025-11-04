from custom.utils import data_subdir
import json

PRICE_MARKERS_FILE = data_subdir("viz", "price_markers.txt")
SIGNALS_FILE = data_subdir("viz", "signals.txt")
TICKS_AND_METRICS_FILE = data_subdir("viz", "ticks_and_metrics.txt")


def dict_to_file(dict_, filename):
    with open(filename, "w") as f:
        json.dump(dict_, f)


def load_txt_file_to_dict(filename):
    with open(filename, "r") as f:
        return json.load(f)


class CreateMarkers:
    @staticmethod
    def _make_marker_dict(dt, position, color, shape, text, price):
        return dict(time=dt, position=position, color=color, shape=shape, text=text, price=price)

    def create_trades_markers(self, trades, sell_legs):
        price_markers = []

        for _, ser in trades.iterrows():
            # Buy markers
            price_ = ser["buy_price"]
            price_markers.append(
                self._make_marker_dict(
                    dt=ser["buy_dt"],
                    position="aboveBar",
                    color="#f77a0c",
                    shape="arrowDown",
                    text=f"{ser['desc']}|{price_}",
                    price=price_,
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

            text = f"{ser['qty']}{ser['desc']}"
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

        return price_markers

    def create_and_save_markers(self, trades, sell_legs):
        markers = self.create_trades_markers(trades, sell_legs)
        markers.sort(key=lambda x: x["time"])
        dict_to_file(markers, PRICE_MARKERS_FILE)

    def load_markers(self):
        return load_txt_file_to_dict(PRICE_MARKERS_FILE)


def write_to_ticks_and_metrics_txt_file(metrics):
    # FIXME: This is slow. Switch to something faster?
    dict_to_file(metrics, TICKS_AND_METRICS_FILE)


def load_ticks_and_metrics_from_txt_file():
    return load_txt_file_to_dict(TICKS_AND_METRICS_FILE)


def write_to_signals_file(signals):
    dict_to_file(signals, SIGNALS_FILE)


def load_signals_file():
    return load_txt_file_to_dict(SIGNALS_FILE)
