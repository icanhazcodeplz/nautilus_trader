import json
import pickle
from enum import StrEnum
from typing import Any
import msgspec
import numpy as np

import pandas as pd

from custom.utils.market_utils import market_round
from custom.utils.paths import data_subdir
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.config import CacheConfig, DatabaseConfig
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled, OrderEvent
from nautilus_trader.serialization.serializer import MsgSpecSerializer
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.identifiers import TraderId

BACKTEST_RUNS_PATH = data_subdir("backtest_runs")


def dict_to_file(dict_, filename):
    with open(filename, "w") as f:
        json.dump(dict_, f, indent=2, default=str)


def load_txt_file_to_dict(filename):
    with open(filename, "r") as f:
        return json.load(f)


class ArtifactsIO:
    def __init__(self, directory):
        self.directory = directory
        self._backtest = directory == BACKTEST_RUNS_PATH
        self._run_dt_str = str(directory).split("/")[-1]  # TODO: Sloppy. Should pass this in as arg

    def _save_pickle(self, data, filename):
        filepath = self.directory / filename
        with open(filepath, "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)

    def _load_pickle(self, filename):
        filepath = self.directory / filename
        with open(filepath, "rb") as f:
            return pickle.load(f)

    @property
    def symbol(self):
        config = self.load_config()
        return config["instrument_id"].split(".")[0]

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
            if drop_key in config:
                config.pop(drop_key)
        return config

    @staticmethod
    def _parse_fill_event(fill_event: OrderFilled) -> dict[str, Any]:
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

    def _order_events_to_order_durations(self, order_events):
        if len(order_events) == 0:
            return []
        def parse_order_event_dict(o_dict):
            options = o_dict.get("options")
            if options is not None:
                price = options.get("price")
                if price is None:
                    price = options.get("trigger_price")
                o_dict["price"] = price
            return o_dict

        as_dicts = [parse_order_event_dict(type(e).to_dict(e)) for e in order_events]

        df = pd.DataFrame(as_dicts)

        def _append_to_durations(durations, side, price, qty, start, end):
            if start == end:
                end += 1
            durations.append(dict(side=side, price=price, qty=qty, start_time=str(start), end_time=str(end)))
            return durations

        def client_id_to_durations(orders):
            if orders["ts_event"].nunique() < len(orders):
                dupes = orders[orders.duplicated(subset="ts_event", keep=False)]
                dupes_types = set(dupes["type"])
                if dupes_types == {"OrderFilled"}:
                    # Do nothing, all the same type
                    pass
                elif dupes_types == {"OrderUpdated"}:
                    # Several resizes of one order inside a single nanosecond: entry partials that
                    # land on the same tick each trigger a resize, so a leg goes 12 -> 20 without
                    # ever resting at 12. Only the final size was ever live, so keep it.
                    orders = orders.drop(index=dupes.index[:-1])
                elif dupes_types == {"OrderAccepted", "OrderFilled"} or dupes_types == {"OrderUpdated", "OrderFilled"}:
                    filled_idx = dupes[dupes["type"] == "OrderFilled"].index
                    orders.loc[filled_idx, "ts_event"] += 1
                elif dupes_types == {"OrderFilled", "OrderCanceled"}:
                    canceled_idx = dupes[dupes["type"] == "OrderCanceled"].index
                    orders.loc[canceled_idx, "ts_event"] += 1
                elif dupes_types == {"OrderUpdated", "OrderCanceled"}:
                    # An OUO leg whose sibling filled in two partials on the one tick: the first
                    # partial shrinks this leg (OrderUpdated), the second closes the sibling and
                    # so cancels it. Drop the update rather than ordering it before the cancel:
                    # the resized leg never rested at that size, and keeping it would emit a
                    # zero-length duration at a quantity the market never saw. The cancel then
                    # closes the duration the leg actually had.
                    updated_idx = dupes[dupes["type"] == "OrderUpdated"].index
                    orders = orders.drop(index=updated_idx)
                elif dupes_types == {"OrderUpdated", "OrderFilled", "OrderCanceled"}:
                    filled_idx = dupes[dupes["type"] == "OrderFilled"].index
                    canceled_idx = dupes[dupes["type"] == "OrderCanceled"].index
                    orders.loc[filled_idx, "ts_event"] += 1
                    orders.loc[canceled_idx, "ts_event"] += 2
                else:
                    raise RuntimeError(
                        f"Unhandled same-ts_event event combination {sorted(dupes_types)} "
                        f"({len(dupes)} events) for {dupes.index.tolist()}"
                    )
                orders = orders.sort_values("ts_event")

            durations = []

            for _, order in orders.iterrows():
                if order["type"] == "OrderInitialized":
                    qty = int(order["quantity"])
                    leaves_qty_at_start = qty
                    filled_qty = 0
                    price = float(order["price"])
                    side = "buy" if str(order["order_side"]) == "BUY" else "sell"
                elif order["type"] == "OrderAccepted":
                    start = order["ts_event"]
                elif order["type"] == "OrderFilled":
                    filled_qty += int(order["last_qty"])
                    leaves_qty = qty - filled_qty
                    if leaves_qty == 0:
                        end = orders.iloc[-1]["ts_event"]
                        durations = _append_to_durations(durations, side, price, leaves_qty_at_start, start, end)
                    elif leaves_qty < 0:
                        raise
                elif order["type"] == "OrderUpdated":
                    # Add the previous order to durations.
                    updated_time = order["ts_event"]
                    durations = _append_to_durations(durations, side, price, leaves_qty_at_start, start, updated_time)

                    # Now update values because we just started a new order
                    start = updated_time
                    qty = int(order["quantity"])
                    leaves_qty_at_start = qty - filled_qty
                    price = float(order["price"])
                    if price is np.nan:
                        price = float(order["trigger_price"])
                elif order["type"] == "OrderCanceled":
                    cancel_time = order["ts_event"]
                    durations = _append_to_durations(durations, side, price, leaves_qty_at_start, start, cancel_time)
                else:
                    raise

            return pd.DataFrame(durations)

        durations_df = df.groupby("client_order_id")[
            ["order_side", "type", "ts_event", "quantity", "price", "trigger_price", "last_px", "last_qty"]
        ].apply(client_id_to_durations)
        durations_df["start_time"] = durations_df["start_time"].astype("int64")
        durations_df["end_time"] = durations_df["end_time"].astype("int64")
        durations_list = durations_df.reset_index(drop=True).to_dict(orient="records")
        return durations_list

    def get_run_data(self):
        orders_report = self.load_orders_report()

        if self._backtest:
            order_events = self.load_backtest_order_updates_to_pkl()
            fill_events = [self._parse_fill_event(event) for event in order_events if isinstance(event, OrderFilled)]
            order_durations_list = self._order_events_to_order_durations(order_events)

        else:
            order_events = self.read_db_order_events()
            fill_events = [
                self._parse_fill_event(event)
                for order in order_events.values()
                for event in order.events
                if isinstance(event, OrderFilled)
            ]

            order_durations_list = self._alpaca_order_durations_list(time_as_ns_int=True, as_list=True)

        fills = sorted(fill_events, key=lambda e: e["ts_event"])

        # Use fill events to create a list of (time, position) dicts
        position = 0
        positions = []
        for fill in fills:
            qty = fill["qty"] if fill["side"] == "buy" else -fill["qty"]
            position += qty
            positions.append({"time": fill["ts_event"], "position": position})

        return orders_report, fills, positions, order_durations_list

    def save_backtest_order_updates_to_pkl(self, fills_list: list[OrderEvent]):
        self._save_pickle(fills_list, "order_updates.pkl")

    def load_backtest_order_updates_to_pkl(self):
        return self._load_pickle("order_updates.pkl")

    def save_performance_metrics(self, performance_metrics):
        dict_to_file(performance_metrics, self.directory / "performance_metrics.json")

    def load_performance_metrics(self):
        return load_txt_file_to_dict(self.directory / "performance_metrics.json")

    def load_alpaca_trade_updates(self):
        txt = load_txt_file_to_dict(self.directory / "alpaca_trade_updates.json")
        if len(txt) == 0:
            return None
        alpaca_updates_df = pd.DataFrame.from_dict(txt)
        cols_to_use = [
            "msg_received_dt",
            "at",  # Time alpaca generated the message
            "timestamp",  # Time event occurred at exchange
            "filled_at",
            "event",
            "side",
            "id",
            "client_order_id",
            "qty",
            "filled_qty",
            "status",
            "limit_price",
            "replaced_by",
            "replaces",
        ]
        if "price" in alpaca_updates_df.columns:
            cols_to_use.append("price")
        if "position_qty" in alpaca_updates_df.columns:
            cols_to_use.append("position_qty")
        alpaca_updates_df = alpaca_updates_df[cols_to_use]
        return alpaca_updates_df

    def _alpaca_order_durations_list(self, time_as_ns_int: bool = False, as_list=False) -> pd.DataFrame:
        alpaca_updates_df = self.load_alpaca_trade_updates()
        if alpaca_updates_df is None:
            return []

        def order_duration(order_df: pd.DataFrame) -> pd.Series:
            order_df = order_df[order_df["event"] != "order_replace_rejected"]
            order_df = order_df[order_df["event"] != "order_cancel_rejected"]

            first_event_ser = order_df.iloc[0]
            side = first_event_ser["side"]
            price = float(first_event_ser["limit_price"])

            event_new = order_df[order_df["event"] == "new"]
            if len(event_new) > 0:
                # If there is a "new" event, can use that for the start_time.
                start_time = event_new["timestamp"].values[0]
            else:
                # If not, we assume that this is an order that replaced another order. Get the
                # start time of this new order by getting the "replaced" timestamp of the previous order
                replaces = order_df["replaces"].values[0]
                replaced_df = alpaca_updates_df[alpaca_updates_df["id"] == replaces]
                try:
                    start_time = replaced_df[replaced_df["event"] == "replaced"]["timestamp"].values[0]
                except IndexError as e:
                    with pd.option_context("display.max_columns", None, "display.width", None):
                        print(f"No start_time for order \n{order_df}\nSKIPPING")
                    return None

            end_time = order_df["timestamp"].iloc[-1]

            # Alpaca uses the `qty` field different for different order events. Need to handle
            # each case differently
            last_event = order_df.iloc[-1]
            last_event_name = last_event["event"]
            if last_event_name == "replaced":
                order_qty = last_event["qty"]
            elif last_event_name == "fill":
                order_qty = last_event["filled_qty"]
            elif last_event_name == "canceled":
                order_qty = last_event["qty"]
            else:
                raise ValueError(f"Unknown last order event: {last_event_name}")

            return pd.Series(dict(side=side, price=price, qty=int(order_qty), start_time=start_time, end_time=end_time))

        order_duration_df = alpaca_updates_df.groupby("id").apply(order_duration, include_groups=False)
        # Drop rows that have any nans
        order_duration_df = order_duration_df.dropna()

        if time_as_ns_int:
            # Convert "start_time" and "end_time" columns into ns since epoch
            for col in ["start_time", "end_time"]:
                order_duration_df[col] = pd.to_datetime(order_duration_df[col]).astype("int64")

            # If end_time equals start_time, add one nanosecond to end_time so that the plotting
            # tools don't break
            mask = order_duration_df["start_time"] == order_duration_df["end_time"]
            order_duration_df.loc[mask, "end_time"] += 1

        if as_list:
            return order_duration_df.reset_index(drop=True).to_dict(orient="records")
        return order_duration_df.sort_values("start_time")

    def save_orders_report(self, orders_report_df: pd.DataFrame):
        self._save_pickle(orders_report_df, "orders_report.pkl")

    def load_orders_report(self, process=False):
        # TODO: Create orders_report from individual order events
        df = self._load_pickle("orders_report.pkl")
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

    def save_ticks_and_metrics(self, metrics):
        self._save_pickle(metrics, "ticks_and_metrics.pkl")

    def load_ticks_and_metrics_file(self):
        return self._load_pickle("ticks_and_metrics.pkl")


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

    def _text_above(self, dt: int, price: float, color: Colors, text: str) -> dict:
        return dict(
            time=dt, position="aboveBar", color=color, shape="circle", text=text, price=market_round(price), size=0.0
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

    def create_order_markers(self, orders_list):
        markers = []
        for order in orders_list:
            color = Colors.YELLOW if order["side"] == "buy" else Colors.ORANGE
            markers.append(
                self._text_above(
                    dt=order["start_time"],
                    price=order["price"],
                    color=color,
                    text=str(order["qty"]),
                ),
            )
        return markers
