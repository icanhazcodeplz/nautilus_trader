from nautilus_trader.model import Quantity, Price

from nautilus_trader.model.enums import OrderStatus, OrderSide
from nautilus_trader.model.events import OrderFilled

from nautilus_trader.model.orders import Order

ONLY_MODIFY_EVERY_NS = 80e6  # e6 converts from ms to ns

CLOSED_STATUS_LIST = {
    OrderStatus.DENIED,
    OrderStatus.FILLED,
    OrderStatus.REJECTED,
    OrderStatus.CANCELED,
    OrderStatus.EXPIRED,
    # OrderStatus.PENDING_CANCEL,
}


class OpenOrder:
    def __init__(self, order: Order, expire_time=None):
        self.order = order
        self.expire_time = expire_time

        self._last_modify_ns = 0
        self._last_modify_qty = None
        self._last_modify_price = None
        self._first_partial_fill_ns = None

    @property
    def is_open(self):
        return self.order.status not in CLOSED_STATUS_LIST

    @property
    def price(self):
        return self._last_modify_price if self._last_modify_price is not None else self.order.price

    @property
    def quantity(self):
        return self._last_modify_qty if self._last_modify_qty is not None else self.order.quantity

    @property
    def first_partial_fill_ns(self):
        if self._first_partial_fill_ns is not None:
            return self._first_partial_fill_ns
        if self.order.filled_qty > 0:
            self._first_partial_fill_ns = min(
                fill.ts_event for fill in self.order.events if isinstance(fill, OrderFilled)
            )
        return self._first_partial_fill_ns

    @property
    def leaves_qty(self):
        return self.quantity - self.filled_qty

    @property
    def filled_qty(self):
        return self.order.filled_qty

    @property
    def venue_order_id(self):
        return self.order.venue_order_id

    @property
    def client_order_id(self):
        return self.order.client_order_id

    def _can_be_modified(self, now_ns):
        if (now_ns - self._last_modify_ns) < ONLY_MODIFY_EVERY_NS:
            return False
        return self.order.status not in [
            OrderStatus.SUBMITTED,
            OrderStatus.PENDING_UPDATE,
            OrderStatus.PENDING_CANCEL,
            OrderStatus.FILLED,
        ]

    def update_last_modify_if_allowed(self, quantity: Quantity, price: Price, now_ns: int):
        if not self._can_be_modified(now_ns):
            return False
        if quantity == self._last_modify_qty and price == self._last_modify_price:
            return False
        self._last_modify_ns = now_ns
        self._last_modify_qty = quantity
        self._last_modify_price = price
        return True

    def __eq__(self, other):
        return self.client_order_id == other.client_order_id

    def __hash__(self):
        return hash(self.order.client_order_id)

    def __repr__(self):
        side_str = "SELL" if self.order.side == OrderSide.SELL else "BUY"
        return f"OpenOrder({side_str} client_id={self.client_order_id}, venue_id={self.venue_order_id} price={self.price}, quantity={self.quantity}, leaves_qty={self.leaves_qty})"
