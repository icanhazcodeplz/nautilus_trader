from typing import Tuple

import pandas as pd

def market_round(price):
    if price < 1.0:
        return round(price, 4)
    return round(price, 2)

def _process_nautilus_orders_df(orders_df: pd.DataFrame) -> pd.DataFrame:
    orders = orders_df[["ts_init", "side", "filled_qty", "avg_px", "tags"]].copy()
    orders["ts_init"] = orders["ts_init"] / 1e9
    orders["avg_px"] = orders["avg_px"].astype(float)
    orders["tags"] = orders["tags"].apply(lambda s: s[0])
    orders["filled_qty"] = orders["filled_qty"].astype(int)
    return orders



def orders_to_trades(orders: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    orders = _process_nautilus_orders_df(orders)
    buys_df = orders[orders["side"] == "BUY"].drop(columns="side")
    sells_df = orders[(orders["side"] == "SELL")].drop(columns="side")
    buys = [tuple(row) for row in buys_df.to_numpy()]
    sells = [tuple(row) for row in sells_df.to_numpy()]
    trades = []
    sell_legs = []
    for buy_id, (buy_dt, buy_qty, buy_price, b_desc) in enumerate(buys, start=1):
        if not sells:
            print(f"No more sells. Ignoring last buy: {buy_dt} qty {buy_qty} at ${buy_price}.")
            break

        qty = buy_qty
        sell_value = 0
        latest_sell_dt = buy_dt  # just to initialize
        while qty > 0:
            sell_dt, sell_qty, sell_price, s_desc = sells.pop(0)

            latest_sell_dt = max(sell_dt, latest_sell_dt)

            if sell_qty > qty:
                remaining_sell_qty = sell_qty - qty
                new_sell_row = (sell_dt, remaining_sell_qty, sell_price, s_desc)
                sells = [new_sell_row, *sells]
                sell_qty = qty

            sell_value += sell_qty * sell_price
            qty -= sell_qty
            sell_legs += [
                dict(
                    buy_id=buy_id,
                    desc=s_desc,
                    dt=sell_dt,
                    qty=sell_qty,
                    price=sell_price,
                    pnl=market_round((sell_price - buy_price) * sell_qty),
                )
            ]

        sell_price = market_round(sell_value / buy_qty)
        price_diff = market_round(sell_price - buy_price)
        trades += [
            dict(
                buy_id=buy_id,
                desc=b_desc,
                buy_dt=buy_dt,
                sell_dt=latest_sell_dt,
                duration=(latest_sell_dt - buy_dt),
                qty=buy_qty,
                buy_price=buy_price,
                avg_sell_price=sell_price,
                pnl=market_round(price_diff * buy_qty),
            )
        ]

    trades_df = pd.DataFrame(trades)
    return trades_df, pd.DataFrame(sell_legs)
