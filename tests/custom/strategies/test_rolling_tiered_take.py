from types import SimpleNamespace
from unittest.mock import MagicMock

from nautilus_trader.model.enums import OrderStatus
from nautilus_trader.model.objects import Price

from custom.strategies.momo import MomoStrategy


class MockOpenOrder:
    """Lightweight stand-in for OpenOrder with the properties _rolling_tiered_take reads."""

    def __init__(self, price_str, leaves_qty, quantity=None, filled_qty=0,
                 status=OrderStatus.ACCEPTED):
        self.price = Price.from_str(price_str)
        self.leaves_qty = leaves_qty
        self.quantity = quantity if quantity is not None else leaves_qty
        self.filled_qty = filled_qty
        self.is_open = True
        # _rolling_tiered_take reads open_order.order.status for its PENDING_UPDATE check
        self.order = SimpleNamespace(status=status)

    def __repr__(self):
        return f"MockOpenOrder(price={self.price}, leaves={self.leaves_qty})"


def _make_strategy(position_qty, open_sells, num_sell_tiers, vwap_high=10.00, mean_variance=0.05):
    """Build a MagicMock that quacks like MomoStrategy for _rolling_tiered_take."""
    s = MagicMock()
    s.position_qty = position_qty
    # _rolling_tiered_take sizes off `exposure` (position in the direction of `side`) rather than
    # the raw signed position. These tests are all long, so the two are the same.
    s.is_short = False
    s.exposure = position_qty
    s.open_exits = set(open_sells)
    s.open_exits_qty = sum(o.leaves_qty for o in open_sells)
    s.config.num_sell_tiers = num_sell_tiers
    s.vwap.high = vwap_high
    s.vwap.mean_variance = mean_variance
    s.instrument.make_price = lambda p: Price.from_str(f"{p:.2f}")
    s.clock.timestamp_ns.return_value = 0
    s.entry_orders_count = 0
    # Start the sell-diff timer unarmed so the fallback order is not placed on the first pass
    s._sell_diff_start_ns = None
    return s


def _run(strategy):
    """Invoke _rolling_tiered_take on the mock strategy."""
    MomoStrategy._rolling_tiered_take(strategy)


class TestRollingTieredTake:
    """Tests for the _rolling_tiered_take tier-management logic.

    Default tier setup (vwap_high=10.00, mean_variance=0.05, step=0.01):
      2 tiers → prices {10.00, 10.01}, max_qty_per_tier = qty/2
      3 tiers → prices {10.00, 10.01, 10.02}
      4 tiers → prices {10.00, 10.01, 10.02, 10.03}
    """

    # ------------------------------------------------------------------ #
    # 1. Single order at higher tier → modified to lowest
    # ------------------------------------------------------------------ #
    def test_single_order_at_higher_tier_modified_to_lowest(self):
        order = MockOpenOrder("10.01", leaves_qty=10)
        s = _make_strategy(position_qty=10, open_sells=[order], num_sell_tiers=2)
        _run(s)

        # Order should be modified to the lowest tier price
        s.modify_open_order.assert_called_once_with(order, quantity=5, price=Price.from_str("10.00"))
        # Remaining qty placed at the higher tier via a new sell
        s.exit.assert_called_once_with(5, Price.from_str("10.01"), cancel_after_secs=None, tag="0")

    # ------------------------------------------------------------------ #
    # 2. Single order at lowest tier → unchanged
    # ------------------------------------------------------------------ #
    def test_single_order_at_lowest_tier_unchanged(self):
        order = MockOpenOrder("10.00", leaves_qty=10)
        s = _make_strategy(position_qty=10, open_sells=[order], num_sell_tiers=2)
        _run(s)

        s.modify_open_order.assert_not_called()
        s.exit.assert_not_called()

    # ------------------------------------------------------------------ #
    # 3. Two orders, lowest tier covered → both unchanged
    # ------------------------------------------------------------------ #
    def test_two_orders_lowest_covered_both_unchanged(self):
        low = MockOpenOrder("10.00", leaves_qty=5)
        high = MockOpenOrder("10.01", leaves_qty=5)
        s = _make_strategy(position_qty=10, open_sells=[low, high], num_sell_tiers=2)
        _run(s)

        s.modify_open_order.assert_not_called()
        s.exit.assert_not_called()

    # ------------------------------------------------------------------ #
    # 4. Two orders, lowest NOT covered → highest moved down
    # ------------------------------------------------------------------ #
    def test_two_orders_lowest_not_covered_highest_moved_down(self):
        mid = MockOpenOrder("10.01", leaves_qty=5)
        high = MockOpenOrder("10.02", leaves_qty=5)
        s = _make_strategy(position_qty=10, open_sells=[mid, high], num_sell_tiers=3)
        _run(s)

        # The highest order (10.02) should be modified to the lowest tier (10.00)
        # 3 tiers, qty 10 → max_qty_per_tier = 4, so new qty = 5 + (4 - 5) = 4
        s.modify_open_order.assert_called_once_with(
            high,
            quantity=4,
            price=Price.from_str("10.00"),
        )
        # Leftover 1 share placed at 10.02
        s.exit.assert_called_once_with(
            1,
            Price.from_str("10.02"),
            cancel_after_secs=None,
            tag="0",
        )

    # ------------------------------------------------------------------ #
    # 5. Single tier, single order → unaffected by force-down logic
    # ------------------------------------------------------------------ #
    def test_single_tier_single_order_unaffected(self):
        order = MockOpenOrder("10.00", leaves_qty=10)
        s = _make_strategy(position_qty=10, open_sells=[order], num_sell_tiers=1)
        _run(s)

        s.modify_open_order.assert_not_called()
        s.exit.assert_not_called()

    # ------------------------------------------------------------------ #
    # 6. Single order at price outside all tiers → modified to lowest
    # ------------------------------------------------------------------ #
    def test_single_order_outside_tiers_modified_to_lowest(self):
        order = MockOpenOrder("10.05", leaves_qty=10)
        s = _make_strategy(position_qty=10, open_sells=[order], num_sell_tiers=2)
        _run(s)

        s.modify_open_order.assert_called_once_with(
            order,
            quantity=5,
            price=Price.from_str("10.00"),
        )
        s.exit.assert_called_once_with(
            5,
            Price.from_str("10.01"),
            cancel_after_secs=None,
            tag="0",
        )

    # ------------------------------------------------------------------ #
    # 7. No open sells → new sell orders created for every tier
    # ------------------------------------------------------------------ #
    def test_no_open_sells_creates_new_orders(self):
        s = _make_strategy(position_qty=10, open_sells=[], num_sell_tiers=2)
        _run(s)

        s.modify_open_order.assert_not_called()
        assert s.exit.call_count == 2
        # Lowest tier first, then highest
        s.exit.assert_any_call(
            5,
            Price.from_str("10.00"),
            cancel_after_secs=None,
            tag="0",
        )
        s.exit.assert_any_call(
            5,
            Price.from_str("10.01"),
            cancel_after_secs=None,
            tag="0",
        )

    # ------------------------------------------------------------------ #
    # 8. Three orders, lowest NOT covered → highest moved down
    # ------------------------------------------------------------------ #
    def test_three_orders_lowest_not_covered_highest_moved_down(self):
        o1 = MockOpenOrder("10.01", leaves_qty=7)
        o2 = MockOpenOrder("10.02", leaves_qty=7)
        o3 = MockOpenOrder("10.03", leaves_qty=6)
        s = _make_strategy(position_qty=20, open_sells=[o1, o2, o3], num_sell_tiers=4)
        _run(s)

        # 4 tiers, qty 20 → max_qty_per_tier = 5
        # o3 (highest, last after sort) triggers force-down to 10.00
        # new qty = 6 + (5 - 6) = 5
        s.modify_open_order.assert_called_once_with(
            o3,
            quantity=5,
            price=Price.from_str("10.00"),
        )
        # Leftover 1 share placed at 10.03
        s.exit.assert_called_once_with(
            1,
            Price.from_str("10.03"),
            cancel_after_secs=None,
            tag="0",
        )
