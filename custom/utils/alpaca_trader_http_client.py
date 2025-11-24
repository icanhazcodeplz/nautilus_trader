from alpaca.common import APIError

from nautilus_trader.adapters.alpaca.utils import get_alpaca_key_and_secret
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce


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
            if e.message == 'position does not exist':
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

        return self.client.submit_order(order_data=limit_order_data)