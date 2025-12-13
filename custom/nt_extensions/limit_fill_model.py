from random import random

from nautilus_trader.backtest.models import FillModel
from nautilus_trader.core.rust.model import BookType
from nautilus_trader.core.rust.model import OrderSide
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.enums import OrderStatus
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
        last_trade: TradeTick,
    ) -> OrderBook | None:
        # Don't generate fills for orders being modified to prevent race condition
        if order.status == OrderStatus.PENDING_UPDATE:
            return None

        # This code is triggered by the current tick, but `quote` and `last_trade` are from the previous tick. As such,
        # we sometimes have nothing to do because the last trade was too high or low for the limit price
        if (
            last_trade is None
            or (order.side == OrderSide.BUY and last_trade.price > order.price)
            or (order.side == OrderSide.SELL and last_trade.price < order.price)
        ):
            return None

        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)

        # Track cumulative fill quantity to prevent exceeding order.leaves_qty
        remaining_qty = int(order.leaves_qty)

        # Add randomness for fill probability
        fill_at_last_trade = True if self.prob_fill_on_limit == 1.0 else self.prob_fill_on_limit > random()

        # HARDCODE: only fill if last trade is 30% of order size
        if fill_at_last_trade and last_trade.size > (order.quantity * 0.30):
            # HARDCODE: fill at 50% of last trade size
            at_last_trade = int(last_trade.size * 0.50)
            # Clamp to remaining quantity to prevent overflow
            at_last_trade = min(at_last_trade, remaining_qty)
            if at_last_trade > 0:
                book_side = OrderSide.SELL if order.side == OrderSide.BUY else OrderSide.BUY
                at_last_trade_order = BookOrder(
                    side=book_side,
                    price=last_trade.price,
                    size=Quantity(at_last_trade, instrument.size_precision),
                    order_id=1,
                )
                book.add(at_last_trade_order, 0, 0)
                remaining_qty -= at_last_trade

        # If the ask is less than the order price, fill half qty of the ask
        if quote is not None:
            if order.side == OrderSide.BUY and quote.ask_price <= order.price:
                # HARDCODE: only fill 50% of quote ask size
                fill_size = int(quote.ask_size / 2)
                # Clamp to remaining quantity to prevent overflow
                fill_size = min(fill_size, remaining_qty)
                if fill_size > 0:
                    book_order = BookOrder(
                        side=OrderSide.SELL,
                        price=quote.ask_price,
                        size=Quantity(fill_size, instrument.size_precision),
                        order_id=2,
                    )
                    book.add(book_order, 0, 0)
                    remaining_qty -= fill_size

            # If the bid is more than the order price, fill half qty of the bid
            if order.side == OrderSide.SELL and quote.bid_price >= order.price:
                # HARDCODE: only fill 50% of quote bid size
                fill_size = int(quote.bid_size / 2)
                # Clamp to remaining quantity to prevent overflow
                fill_size = min(fill_size, remaining_qty)
                if fill_size > 0:
                    book_order = BookOrder(
                        side=OrderSide.BUY,
                        price=quote.bid_price,
                        size=Quantity(fill_size, instrument.size_precision),
                        order_id=2,
                    )
                    book.add(book_order, 0, 0)
                    remaining_qty -= fill_size

        if book.update_count == 0:
            return None
        return book
