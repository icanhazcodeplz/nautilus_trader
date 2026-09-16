
import pandas as pd


def market_round_3_or_4(price):
    if price < 1.0:
        return round(price, 4)
    return round(price, 3)


def _process_nautilus_orders_df(orders_df: pd.DataFrame) -> pd.DataFrame:
    orders = orders_df[["ts_last", "side", "filled_qty", "avg_px", "tags"]].copy()
    # orders["ts_last"] = orders["ts_last"]
    orders["avg_px"] = orders["avg_px"].astype(float)
    orders["tags"] = orders["tags"].apply(lambda s: s[0] if s is not None else "Missing")
    orders["filled_qty"] = orders["filled_qty"].astype(int)
    orders = orders.sort_values(by="ts_last")
    return orders


def _split_entries_and_exits(orders: pd.DataFrame) -> tuple[list, list]:
    """
    Split filled orders into the legs that opened positions and the legs that closed them.

    Walks the fills in time order tracking the running signed position. Whichever side moves the
    position off flat opens an "epoch" and fixes that epoch's entry side, so a short epoch counts
    its sells as entries and its buys as exits.

    An epoch never spans a direction change, because `set_side` (custom/strategies/base.py) refuses
    to flip unless the position is flat with nothing resting.

    Returns (entries, exits) in time order. Each entry carries the sign of its own epoch, so a run
    that switched sides midway still prices every trade against the direction it was actually
    taken in.
    """
    entries = []
    exits = []
    running_qty = 0
    entry_side = None
    for row in orders.itertuples(index=False):
        if running_qty == 0:
            entry_side = row.side
        if row.side == entry_side:
            sign = 1 if entry_side == "BUY" else -1
            entries.append((row.ts_last, row.filled_qty, row.avg_px, row.tags, sign))
        else:
            exits.append((row.ts_last, row.filled_qty, row.avg_px, row.tags))
        running_qty += row.filled_qty if row.side == "BUY" else -row.filled_qty
    return entries, exits


def orders_to_trades(orders_report: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if orders_report.empty:
        return pd.DataFrame(), pd.DataFrame()
    orders = orders_report[orders_report["filled_qty"].astype(int) > 0]

    orders = _process_nautilus_orders_df(orders)
    entries, exits = _split_entries_and_exits(orders)
    trades = []
    exit_legs = []
    for trade_id, (entry_dt, entry_qty, entry_price, e_desc, sign) in enumerate(entries, start=1):
        qty = entry_qty
        exit_value = 0
        latest_exit_dt = entry_dt  # just to initialize
        record_trade = True  # Only record trades that get completely closed
        while qty > 0:
            if not exits:
                print(f"No more exits. Ignoring last entry: {entry_dt} qty {entry_qty} at ${entry_price}.")
                record_trade = False
                break
            exit_dt, exit_qty, exit_price, x_desc = exits.pop(0)

            latest_exit_dt = max(exit_dt, latest_exit_dt)

            if exit_qty > qty:
                remaining_exit_qty = exit_qty - qty
                new_exit_row = (exit_dt, remaining_exit_qty, exit_price, x_desc)
                exits = [new_exit_row, *exits]
                exit_qty = qty

            exit_value += exit_qty * exit_price
            qty -= exit_qty
            exit_legs += [
                dict(
                    trade_id=trade_id,
                    desc=x_desc,
                    dt=exit_dt,
                    qty=exit_qty,
                    price=exit_price,
                    pnl=market_round_3_or_4((exit_price - entry_price) * exit_qty * sign),
                )
            ]
        if record_trade:
            exit_price = market_round_3_or_4(exit_value / entry_qty)
            price_diff = market_round_3_or_4(exit_price - entry_price)
            trades += [
                dict(
                    trade_id=trade_id,
                    desc=e_desc,
                    entry_dt=entry_dt,
                    exit_dt=latest_exit_dt,
                    duration=(latest_exit_dt - entry_dt),
                    qty=entry_qty,
                    entry_price=entry_price,
                    avg_exit_price=exit_price,
                    pnl=market_round_3_or_4(price_diff * entry_qty * sign),
                )
            ]

    return pd.DataFrame(trades), pd.DataFrame(exit_legs)
