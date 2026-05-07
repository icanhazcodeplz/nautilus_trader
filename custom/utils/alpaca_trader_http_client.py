from time import sleep
from uuid import UUID

import pandas as pd
from alpaca.common import APIError
from alpaca.trading import Position
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce, PositionSide, AssetExchange, AssetClass

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

        request_params = GetOrdersRequest(status=query_status, symbols=[symbol] if symbol else None)

        orders = self.client.get_orders(filter=request_params)
        return orders

    def cancel_order(self, order_id: str):
        return self.client.cancel_order_by_id(order_id)

    def get_position(self, symbol: str) -> Position:
        try:
            return self.client.get_open_position(symbol)
        except APIError as e:
            if e.message == "position does not exist":
                # Return empty position object
                return Position(
                    asset_id=UUID("a" * 32),
                    symbol=symbol,
                    exchange=AssetExchange.EMPTY,
                    asset_class=AssetClass.US_EQUITY,
                    avg_entry_price="",
                    qty="0",
                    side=PositionSide.LONG,
                    cost_basis="",
                )
            else:
                raise e

    def limit_order(self, side: str, symbol: str, qty: float, price: float):
        side = OrderSide.SELL if side == "sell" else OrderSide.BUY
        price_rounded = round(price, 2 if price > 1 else 4)
        limit_order_data = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=price_rounded,
            extended_hours=True,
        )
        self.log.info(f"Submitting limit order: {limit_order_data}")
        return self.client.submit_order(order_data=limit_order_data)


class AlpacaTraderHelper:
    def __init__(self, paper: bool = True):
        self.client = AlpacaTraderHttpClient(paper=paper)
        self._log = Logger(name=self.__class__.__name__)

    def get_position_obj(self, symbol: str) -> Position:
        return self.client.get_position(symbol)

    def get_open_orders(self, symbol: str):
        return self.client.get_orders(symbol=symbol, status="open")

    def submit_limit_order_and_wait_to_fill(
        self, symbol: str, side: str, qty: float, limit_price: float, max_wait_secs: int = 10
    ):
        fill_order_sent_at = pd.Timestamp.now()
        limit_sell_order = self.client.limit_order(side, symbol=symbol, qty=qty, price=limit_price)
        while limit_sell_order.status != "filled":
            if (pd.Timestamp.now() - fill_order_sent_at) > pd.Timedelta(seconds=max_wait_secs):
                self._log.error(f"Sell order for {symbol} never filled after {max_wait_secs} seconds")
                return False
            limit_sell_order = self.client.get_order(limit_sell_order.id)
            self._log.info(f"Waiting for sell order {limit_sell_order.id} to fill")
            sleep(0.2)
        return True

    def _position_is_short(self, position: Position | None = None) -> bool:
        return int(position.qty) < 0

    @retry(max_retries=3, wait_time=2.0)
    def flatten_if_short_with_retry(self, symbol: str, position: Position):
        position_was_short = False
        if self._position_is_short(position):
            position_was_short = True
            flattened = self.submit_limit_order_and_wait_to_fill(
                symbol,
                "buy",
                abs(int(position.qty)),
                float(position.current_price) * 1.10,
                max_wait_secs=20,
            )
            position = self.get_position_obj(symbol)
            if not flattened or self._position_is_short(position):
                raise RuntimeError(f"Failed to flatten {symbol} position")
        return position_was_short

    def cancel_open_orders_and_flatten(self, symbol: str):
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
        canceling_or_flattening_needed = False
        open_orders = self.client.get_orders(symbol=symbol, status="open")
        for open_order in open_orders:
            canceling_or_flattening_needed = True
            self._log.warning(f"Cancelling existing open order for {symbol}: {open_order}")
            self.client.cancel_order(str(open_order.id))

        position = self.get_position_obj(symbol)
        qty = int(position.qty) if position is not None else 0
        if qty != 0:
            canceling_or_flattening_needed = True
            current_price = float(position.current_price or 0)
            if qty > 0:
                limit_price = current_price * 0.90
                side = "sell"
            else:
                self._log.error(f"Negative position for {symbol} of {qty}. Not good!")
                limit_price = current_price * 1.10
                side = "buy"
            self.submit_limit_order_and_wait_to_fill(symbol, side, abs(qty), limit_price, max_wait_secs=10)
            self._log.warning(f"Existing position for {symbol} of {qty}. Submitting {side} order at {limit_price}")
        return canceling_or_flattening_needed

    @retry(max_retries=2, wait_time=2.0)
    def cancel_orders_and_flatten_position_with_retry(self, symbol: str, max_retries: int = 2):
        # Wrapper to run multiple times until we get through `cancel_open_orders_and_close_position` without any operations
        iterations = 0
        while self.cancel_open_orders_and_flatten(symbol):
            iterations += 1
            if iterations > max_retries:
                raise RuntimeError(
                    f"Failed to cancel orders and flatten position for {symbol} after {iterations - 1} iterations"
                )
            sleep(1)
        self._log.info(f"{symbol} flat after {iterations} iterations of canceling orders and closing position")
