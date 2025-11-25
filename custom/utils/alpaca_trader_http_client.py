from time import sleep

import pandas as pd
from alpaca.common import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce

from custom.utils.retry import retry
from nautilus_trader.adapters.alpaca.utils import get_alpaca_key_and_secret
from nautilus_trader.common.component import Logger


class AlpacaTraderHttpClient:
    """
    Synchronous HTTP client for Alpaca trading operations using alpaca-py.

    Parameters
    ----------
    paper : bool, default True
        Whether to use paper trading or live trading.
    """

    def __init__(self, paper: bool = True):
        api_key, api_secret = get_alpaca_key_and_secret(paper=paper)
        self.client = TradingClient(api_key, api_secret, paper=paper)
        self.log = Logger(name=self.__class__.__name__)

    def get_order(self, order_id: str):
        return self.client.get_order_by_id(order_id)

    def get_orders(self, symbol: str | None = None, status: str = "open"):
        match status:
            case "open":
                query_status = QueryOrderStatus.OPEN
            case "closed":
                query_status = QueryOrderStatus.CLOSED
            case "all":
                query_status = QueryOrderStatus.ALL
            case _:
                raise ValueError(f"Invalid status: {status}")

        # Build request parameters
        request_params = GetOrdersRequest(
            status=query_status,
            symbols=[symbol] if symbol else None,
        )

        # Get orders from Alpaca
        orders = self.client.get_orders(filter=request_params)

        return orders

    def cancel_order(self, order_id: str):
        return self.client.cancel_order_by_id(order_id)

    def get_position(self, symbol):
        try:
            return self.client.get_open_position(symbol)
        except APIError as e:
            if e.message == "position does not exist":
                return None

    def limit_order(self, side: str, symbol: str, qty: float, price: float):
        side = OrderSide.SELL if side == "sell" else OrderSide.BUY
        limit_order_data = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=price,
            extended_hours=True,
        )
        self.log.info(f"Submitting limit order: {limit_order_data}")
        return self.client.submit_order(order_data=limit_order_data)


class AlpacaTraderHelper:
    def __init__(self, paper: bool = True):
        self.client = AlpacaTraderHttpClient(paper=paper)
        self._log = Logger(name=self.__class__.__name__)

    def cancel_open_orders_and_close_position(self, symbol):
        """
        Cancels all open orders and closes any existing position for a specified symbol.

        This function interacts with a trading client to cancel all open orders and sell the
        existing position for the provided trading symbol. It ensures no leftover positions
        or unfulfilled orders remain, and attempts to sell existing positions at a reduced
        price if required.

        Returns:
        bool
            Returns True if there were open orders or positions that needed canceling or selling,
            and False otherwise.
        """
        fill_wait_max_secs = 10

        canceling_or_flattening_needed = False
        open_orders = self.client.get_orders(symbol=symbol, status="open")
        for open_order in open_orders:
            canceling_or_flattening_needed = True
            self._log.warning(f"Cancelling existing open order for {symbol}: {open_order}")
            self.client.cancel_order(open_order.id)
        position = self.client.get_position(symbol)
        qty = int(position.qty) if position is not None else 0
        if qty != 0:
            canceling_or_flattening_needed = True
            fill_order_sent_at = pd.Timestamp.now()
            if qty > 0:
                limit_price = float(position.current_price) * 0.90
                side = "sell"
            elif qty < 0:
                self._log.error(f"Negative position for {symbol} of {qty}. Not good!")
                limit_price = float(position.current_price) * 1.10
                side = "buy"
            self._log.warning(f"Existing position for {symbol} of {qty}. Submitting {side} order at {limit_price}")
            limit_sell_order = self.client.limit_order(side, symbol, abs(qty), price=round(limit_price, 2))

            while limit_sell_order.status != "filled":
                if (pd.Timestamp.now() - fill_order_sent_at) > pd.Timedelta(seconds=fill_wait_max_secs):
                    self._log.error(f"Sell order for {symbol} never filled after {fill_wait_max_secs} seconds")
                    break
                limit_sell_order = self.client.get_order(limit_sell_order.id)
                self._log.info(f"Waiting for sell order {limit_sell_order.id} to fill")
                sleep(0.2)
        return canceling_or_flattening_needed

    @retry(max_retries=3, wait_time=2.0)
    def flatten_symbol(self, symbol):
        # Wrapper to run multiple times until we get through `cancel_open_orders_and_close_position` without any operations
        iterations = 0
        while self.cancel_open_orders_and_close_position(symbol):
            iterations += 1
            sleep(1)
        self._log.info(f"{symbol} flat after {iterations} iterations of canceling orders and closing position")
