import json
import pickle
from enum import StrEnum
from typing import Any
import msgspec

import pandas as pd

from custom.utils.market_utils import market_round
from custom.utils.paths import data_subdir
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.config import CacheConfig, DatabaseConfig
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.serialization.serializer import MsgSpecSerializer
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.identifiers import TraderId

VIZ_ARTIFACTS_PATH = data_subdir("viz")


def dict_to_file(dict_, filename):
    with open(filename, "w") as f:
        json.dump(dict_, f, indent=2, default=str)


def load_txt_file_to_dict(filename):
    with open(filename, "r") as f:
        return json.load(f)


class ArtifactsIO:
    def __init__(self, directory):
        self.directory = directory
        self._run_dt_str = str(directory).split("/")[-1]  # TODO: Sloppy. Should pass this in as arg

    def save_config(self, config):
        dict_to_file(config, self.directory / "config.json")

    def load_config(self):
        config = load_txt_file_to_dict(self.directory / "config.json")
        for drop_key in [
            "log_rejected_due_post_only_as_warning",
            "use_uuid_client_order_ids",
            "use_hyphens_in_client_order_ids",
            "oms_type",
            "manage_contingent_orders",
            "manage_gtd_expiry",
            "log_events",
            "log_commands",
            "print_update_every_secs",
            "external_order_claims",
            "strategy_id",
            "order_id_tag",
        ]:
            config["strategy"].pop(drop_key)
        return config

    def save_orders_events(self, events):
        dict_to_file(events, self.directory / "orders_events.json")

    @staticmethod
    def _parse_fill_event(fill_event):
        return {
            "ts_init": fill_event.ts_init,
            "ts_event": fill_event.ts_event,
            "side": "buy" if fill_event.order_side == OrderSide.BUY else "sell",
            "qty": int(fill_event.last_qty),
            "price": float(fill_event.last_px),
            "venue_order_id": str(fill_event.venue_order_id),
            "client_order_id": str(fill_event.client_order_id),
        }

    def read_db_order_events(self):
        database = CacheDatabaseAdapter(
            trader_id=TraderId(f"T-{self._run_dt_str}"),  # must match what the run used
            instance_id=UUID4(),
            serializer=MsgSpecSerializer(
                encoding=msgspec.msgpack,
                timestamps_as_str=True,
            ),
            config=CacheConfig(database=DatabaseConfig()),
        )
        return database.load_orders()

    def get_fills(self) -> list[dict[str, Any]]:
        orders = self.read_db_order_events()
        fill_events = [
            self._parse_fill_event(event)
            for order in orders.values()
            for event in order.events
            if isinstance(event, OrderFilled)
        ]

        fill_events = sorted(fill_events, key=lambda e: e["ts_event"])
        return fill_events

    def get_fills_and_buys(self) -> list[dict[str, Any]]:
        fills = self.get_fills()
        df = pd.DataFrame([f for f in fills if f["side"] == "buy"])

        def fills_to_buys(gp):
            ts_event = gp["ts_event"].max()
            qty = gp["qty"].sum()
            avg_px = (gp["qty"] * gp["price"]).sum() / qty
            return pd.Series({"ts_event": ts_event, "qty": int(qty), "avg_px": float(avg_px)})

        buys = df.groupby("client_order_id").apply(fills_to_buys)
        return fills, buys

    def save_performance_metrics(self, performance_metrics):
        dict_to_file(performance_metrics, self.directory / "performance_metrics.json")

    def load_performance_metrics(self):
        return load_txt_file_to_dict(self.directory / "performance_metrics.json")

    def save_orders_report(self, orders_report_df: pd.DataFrame):
        orders_report_df.to_pickle(self.directory / "orders_report.pkl")

    def load_orders_report(self, process=False):
        df = pd.read_pickle(self.directory / "orders_report.pkl")
        if process:
            cols = [
                "venue_order_id",
                "side",
                "ts_init",
                "ts_last",
                "quantity",
                "price",
                "filled_qty",
                "avg_px",
                "status",
                "tags",
                "init_id",
            ]
            df = df[cols]
            for col in ["ts_init", "ts_last"]:
                df[col] = pd.to_datetime(df[col]).dt.tz_localize("UTC").dt.tz_convert("US/Eastern")

        return df

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


class Colors(StrEnum):
    BLACK = "#000000"
    ORANGE = "#f77a0c"
    GREEN = "#07fc03"
    RED = "#fc0317"
    BLUE = "#2196F3"
    YELLOW = "#FFD600"


class CreateMarkers:
    @staticmethod
    def _make_marker_dict(dt, position, color, shape, text, price):
        return dict(time=dt, position=position, color=color, shape=shape, text=text, price=price)

    def _arrow_above(self, dt: int, price: float, color: Colors, text: str) -> dict:
        return self._make_marker_dict(
            dt=dt,
            price=market_round(price),
            color=color.value,
            text=text,
            position="aboveBar",
            shape="arrowDown",
        )

    def _arrow_below(self, dt: int, price: float, color: Colors, text: str) -> dict:
        return self._make_marker_dict(
            dt=dt,
            price=market_round(price),
            color=color.value,
            text=text,
            position="belowBar",
            shape="arrowUp",
        )

    def create_trades_markers(self, trades, sell_legs=None):
        if trades.empty:
            print("No trades to create markers for")
            return []
        trades = trades[trades["avg_sell_price"] > 0]

        price_markers = []

        for _, ser in trades.iterrows():
            # Buy markers
            text = f"{ser['desc']}|{ser['qty']}"
            price_markers.append(self._arrow_above(ser["buy_dt"], ser["buy_price"], Colors.ORANGE, text))

            # Trade ending markers
            price_markers.append(
                self._arrow_above(
                    dt=ser["sell_dt"],
                    color=(Colors.RED if ser["pnl"] < 0 else Colors.GREEN),
                    text=f"{ser['desc']}|{ser['pnl']}",
                    price=ser["avg_sell_price"],
                )
            )

        if sell_legs is not None:
            # Sell leg markers
            for _, ser in sell_legs.iterrows():
                color = Colors.RED if ser["pnl"] < 0 else Colors.GREEN
                text = f"{ser['qty']}"
                price_markers.append(self._arrow_below(dt=ser["dt"], color=color, text=text, price=ser["price"]))

        price_markers.sort(key=lambda x: x["time"])
        return price_markers

    def create_fill_markers(self, fills):
        if len(fills) == 0:
            print("No fills to create markers for")
            return []
        markers = []
        for fill in fills:
            color = Colors.YELLOW if fill["side"] == "buy" else Colors.BLUE
            markers.append(
                self._arrow_below(
                    dt=fill["ts_event"],
                    price=fill["price"],
                    color=color,
                    text=str(fill["qty"]),
                ),
            )
        return markers
