from nautilus_trader.backtest.models import FillModel
from nautilus_trader.core.rust.model import BookType
from nautilus_trader.core.rust.model import OrderSide
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.instruments.base import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders.base import Order
from nautilus_trader.model import BookOrder
from nautilus_trader.model.data import QuoteTick, TradeTick


class LimitFillModel(FillModel):
    """
    LIMITATIONS:
        - Some hardcoded values
        - Using tick and quote from previous time interval
            - Would need to adjust trigger logic in core, because only
        - Only works if running with L1
    """

    def get_orderbook_for_fill_simulation(
        self,
        instrument: Instrument,
        order: Order,
        best_bid: Price,
        best_ask: Price,
        quote: QuoteTick,
        last_trade: TradeTick
    ) -> OrderBook | None:

        # This code is triggered by the current tick, but `quote` and `last_trade` are from the previous tick. As such,
        # we sometimes have nothing to do because the last trade was too high or low for the limit price
        if (last_trade is None or
                (order.side == OrderSide.BUY and last_trade.price > order.price) or
                (order.side == OrderSide.SELL and last_trade.price < order.price)):
            return None

        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)

        # HARDCODE: only fill if last trade is 30% of order size
        if last_trade.size > (order.quantity * 0.30):
            # HARDCODE: fill at 50% of last trade size
            at_last_trade = int(last_trade.size * 0.50)
            book_side = OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
            at_last_trade_order = BookOrder(
                side=book_side,
                price=last_trade.price,
                size=Quantity(at_last_trade, instrument.size_precision),
                order_id=1,
            )
            book.add(at_last_trade_order, 0, 0)

        # If the ask is less than the order price, fill half qty of the ask
        if order.side == OrderSide.BUY and quote.ask_price <= order.price:
            order = BookOrder(
                side=OrderSide.SELL,
                price=quote.ask_price,
                # HARDCODE: only fill 50% of quote ask size
                size=Quantity(int(quote.ask_size / 2), instrument.size_precision),
                order_id=2,
            )
            book.add(order, 0, 0)

        # If the bid is more than the order price, fill half qty of the bid
        if order.side == OrderSide.SELL and quote.bid_price >= order.price:
            order = BookOrder(
                side=OrderSide.BUY,
                price=quote.bid_price,
                # HARDCODE: only fill 50% of quote bid size
                size=Quantity(int(quote.bid_size / 2), instrument.size_precision),
                order_id=2,
            )
            book.add(order, 0, 0)

        if book.update_count == 0:
            return None
        return book