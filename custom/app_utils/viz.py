from custom.utils import data_subdir
import json

PRICE_MARKERS_FILE = data_subdir("viz", "price_markers.txt")
METRICS_FILE = data_subdir("viz", "metrics.txt")
SIGNALS_FILE = data_subdir("viz", "signals.txt")


def dict_to_file(dict_, filename):
    with open(filename, "w") as f:
        json.dump(dict_, f)


def load_txt_file_to_dict(filename):
    with open(filename, "r") as f:
        return json.load(f)


class CreateMarkers:
    @staticmethod
    def _make_marker_dict(dt, position, color, shape, text):
        formatted_dt = dt
        return dict(time=formatted_dt, position=position, color=color, shape=shape, text=text)

    def create_signal_markers(self):
        # Markers from the "buy" and "sell" signals from the strategy
        signals = load_signals_file()
        signal_markers = []
        for sig in signals:
            if sig["side"] == "buy":
                signal_markers.append(
                    self._make_marker_dict(
                        dt=sig["time"],
                        position="belowBar",
                        color="#f77a0c",
                        shape="arrowUp",
                        text=str(sig["tag"]),
                    )
                )
            else:
                raise NotImplementedError
        return signal_markers

    def create_trades_markers(self, trades, sell_legs):
        price_markers = []

        for _, ser in trades.iterrows():
            # Buy markers
            price_markers.append(
                self._make_marker_dict(
                    dt=str(ser["buy_dt"]),
                    position="aboveBar",
                    color="#f77a0c",
                    shape="arrowDown",
                    text=f"{ser['desc']}|{ser['buy_price']}",
                )
            )

            # Trade ending markers
            pnl = ser["pnl"]
            price_markers.append(
                self._make_marker_dict(
                    dt=str(ser["sell_dt"]),
                    position="aboveBar",
                    color="#fc0317" if pnl < 0 else "#07fc03",
                    shape="arrowDown",
                    # text=f"{order['desc']}-{qty}",
                    text=f"{pnl}",
                )
            )

        # Sell leg markers
        for _, ser in sell_legs.iterrows():
            color = "#fc0317" if ser["pnl"] < 0 else "#07fc03"

            text = f"{ser['qty']}{ser['desc']}"
            price_markers.append(
                self._make_marker_dict(
                    dt=str(ser["dt"]),
                    position="belowBar",
                    color=color,
                    shape="arrowUp",
                    text=text,
                )
            )

        return price_markers

    def create_and_save_markers(self, trades, sell_legs):
        trades_markers = self.create_trades_markers(trades, sell_legs)
        signal_markers = self.create_signal_markers()
        markers = trades_markers + signal_markers
        markers.sort(key=lambda x: int(x["time"]))
        dict_to_file(markers, PRICE_MARKERS_FILE)

    def load_markers(self):
        return load_txt_file_to_dict(PRICE_MARKERS_FILE)


def write_to_metrics_txt_file(metrics):
    dict_to_file(metrics, METRICS_FILE)


def load_metrics_from_txt_file():
    return load_txt_file_to_dict(METRICS_FILE)


def write_to_signals_file(signals):
    dict_to_file(signals, SIGNALS_FILE)


def load_signals_file():
    return load_txt_file_to_dict(SIGNALS_FILE)
