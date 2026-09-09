"""
State-machine tests for the ``OpenFade`` strategy.

Each test drives the strategy directly with synthetic trade ticks and fires the clock alerts by
hand, so the sequencing (arm -> reprice -> flip -> deadline) is exercised without needing a
``BacktestEngine`` per test (the Rust logger can only be initialized once per process, which makes
multiple engines in one pytest session impossible).

Order submission is captured by stubbing the strategy's submit/modify/cancel commands.
"""

import pandas as pd
import pytest

from custom.strategies.open_fade import ENTRY_TAG
from custom.strategies.open_fade import STOP_LOSS_TAG
from custom.strategies.open_fade import TAKE_PROFIT_TAG
from custom.strategies.open_fade import OpenFade
from custom.strategies.open_fade import OpenFadeConfig
from custom.strategies.open_fade import et_time_today
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import ContingencyType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs


OPEN_ET = pd.Timestamp("2026-09-04 09:30:00", tz="US/Eastern")


class _Recorder:
    """Captures the execution commands the strategy issues."""

    def __init__(self):
        self.auto_accept = True
        self.submitted = []
        self.submitted_lists = []
        self.modified = []
        self.canceled = []


@pytest.fixture
def instrument():
    return TestInstrumentProvider.equity(symbol="AMZN", venue="ALPACA")


@pytest.fixture
def strategy(instrument):
    """A started ``OpenFade`` wired to a TestClock, with execution commands stubbed out."""
    clock = TestClock()
    clock.set_time(int(pd.Timestamp("2026-09-04 08:55", tz="US/Eastern").value))

    logger = TestComponentStubs.logger() if hasattr(TestComponentStubs, "logger") else None  # noqa: F841
    msgbus = MessageBus(trader_id=TestIdStubs.trader_id(), clock=clock)
    cache = TestComponentStubs.cache()
    cache.add_instrument(instrument)
    portfolio = Portfolio(msgbus=msgbus, cache=cache, clock=clock)

    config = OpenFadeConfig(
        instrument_id=instrument.id,
        trade_size=10,
        entry_offsets=(0.50, 1.00),
        stop_offset=3.0,
        flip_threshold=0.30,
    )
    strat = OpenFade(config=config)
    strat.register(
        trader_id=TestIdStubs.trader_id(),
        portfolio=portfolio,
        msgbus=msgbus,
        cache=cache,
        clock=clock,
    )

    rec = _Recorder()
    strat._recorder = rec

    def _submit(order, **kw):
        rec.submitted.append(order)
        if rec.auto_accept:
            _accept(order)

    def _submit_list(order_list, **kw):
        rec.submitted_lists.append(order_list)
        if rec.auto_accept:
            for leg in order_list.orders:
                _accept(leg)

    # Stub the execution commands: we assert on intent, not on venue behaviour.
    strat.submit_order = _submit
    strat.submit_order_list = _submit_list
    strat.modify_order = lambda order, **kw: rec.modified.append((order, kw))
    strat.cancel_orders = lambda orders, **kw: rec.canceled.extend(orders)
    strat.cancel_all_orders = lambda *a, **kw: None
    strat.close_all_positions = lambda *a, **kw: None
    strat.subscribe_trade_ticks = lambda *a, **kw: None
    strat.unsubscribe_trade_ticks = lambda *a, **kw: None

    strat.instrument = instrument
    strat._collecting = True
    return strat


def _tick(instrument, price: float, ts: pd.Timestamp) -> TradeTick:
    return TradeTick(
        instrument_id=instrument.id,
        price=Price.from_str(f"{price:.2f}"),
        size=Quantity.from_int(100),
        aggressor_side=AggressorSide.BUYER,
        trade_id=TradeId(f"T-{ts.value}"),
        ts_event=int(ts.value),
        ts_init=int(ts.value),
    )


def _feed(strategy, instrument, prices, start=OPEN_ET, step_ms=100):
    for i, p in enumerate(prices):
        strategy.on_trade_tick(_tick(instrument, p, start + pd.Timedelta(milliseconds=i * step_ms)))


def _entries(strategy):
    return [o for o in strategy._recorder.submitted if any(t.startswith(ENTRY_TAG) for t in (o.tags or []))]


def _arm_and_open(strategy, instrument, avg_prices, open_price):
    """Run the 09:29:57 arm and the 09:30:00 open, returning the entry orders."""
    _feed(strategy, instrument, avg_prices, start=OPEN_ET - pd.Timedelta(seconds=10))
    strategy._on_arm(None)
    _feed(strategy, instrument, [open_price], start=OPEN_ET - pd.Timedelta(milliseconds=1))
    strategy._on_open(None)
    return _entries(strategy)


class TestTimeHelper:
    def test_et_time_today_converts_to_utc(self):
        now = pd.Timestamp("2026-09-04 13:30", tz="UTC")
        assert et_time_today(now, "09:30:00") == pd.Timestamp("2026-09-04 13:30:00", tz="UTC")
        assert et_time_today(now, "09:29:57") == pd.Timestamp("2026-09-04 13:29:57", tz="UTC")
        assert et_time_today(now, "15:55:00") == pd.Timestamp("2026-09-04 19:55:00", tz="UTC")


class TestArmAndReprice:
    def test_arms_off_tick_average_then_reprices_to_open(self, strategy, instrument):
        # Ten ticks averaging 100.00, then an opening print of 101.00.
        entries = _arm_and_open(strategy, instrument, [100.0] * 10, 101.0)

        assert len(entries) == 2
        assert all(o.side == OrderSide.SELL for o in entries)
        # Armed off the average...
        assert sorted(float(o.price) for o in entries) == [100.50, 101.00]
        # ...then repriced off the actual open.
        assert strategy.open_price == 101.0
        repriced = sorted(float(kw["price"]) for _, kw in strategy._recorder.modified)
        assert repriced == [101.50, 102.00]

    def test_open_price_is_last_trade_before_open(self, strategy, instrument):
        _feed(strategy, instrument, [100.0] * 10, start=OPEN_ET - pd.Timedelta(seconds=10))
        strategy._on_arm(None)
        _feed(strategy, instrument, [99.5, 100.25], start=OPEN_ET - pd.Timedelta(seconds=1))
        strategy._on_open(None)
        assert strategy.open_price == 100.25

    def test_entries_are_not_post_only(self, strategy, instrument):
        entries = _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        assert all(o.is_post_only is False for o in entries)

    def test_no_ticks_collected_does_not_arm(self, strategy, instrument):
        strategy._recent_prices.clear()
        strategy._on_arm(None)
        assert _entries(strategy) == []


class TestFlip:
    def test_flip_cancels_sells_before_submitting_buys(self, strategy, instrument):
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        rec = strategy._recorder
        n_before = len(rec.submitted)

        _feed(strategy, instrument, [99.69])  # crosses open - 0.30

        # Phase 1: cancels issued, nothing new submitted yet.
        assert len(rec.canceled) == 2
        assert len(rec.submitted) == n_before
        assert strategy._pending_flip_side == OrderSide.BUY

    def test_flip_completes_once_cancels_confirm(self, strategy, instrument):
        entries = _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        _feed(strategy, instrument, [99.69])

        # Simulate the venue confirming both cancels.
        for order in entries:
            order.apply(_canceled_event(order))
        strategy.on_order_canceled(None)

        buys = [o for o in _entries(strategy) if o.side == OrderSide.BUY]
        assert len(buys) == 2
        assert sorted(float(o.price) for o in buys) == [99.00, 99.50]
        assert strategy._pending_flip_side is None
        assert strategy._flips == 1

    def test_flip_back_to_sell(self, strategy, instrument):
        entries = _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        _feed(strategy, instrument, [99.69])
        for order in entries:
            order.apply(_canceled_event(order))
        strategy.on_order_canceled(None)

        buys = [o for o in _entries(strategy) if o.side == OrderSide.BUY]
        _feed(strategy, instrument, [100.31])  # crosses open + 0.30
        for order in buys:
            order.apply(_canceled_event(order))
        strategy.on_order_canceled(None)

        assert strategy._flips == 2
        assert strategy._side == OrderSide.SELL

    def test_max_flips_is_honoured(self, strategy, instrument):
        strategy._flips = strategy.config.max_flips
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        _feed(strategy, instrument, [99.0])
        assert strategy._pending_flip_side is None

    def test_fill_disables_flipping(self, strategy, instrument):
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        strategy._any_fill = True  # As set by on_order_filled

        _feed(strategy, instrument, [99.0])  # Well past the flip threshold

        assert strategy._pending_flip_side is None
        assert strategy._flips == 0

    def test_no_flip_before_open(self, strategy, instrument):
        _feed(strategy, instrument, [100.0] * 10, start=OPEN_ET - pd.Timedelta(seconds=10))
        strategy._on_arm(None)
        _feed(strategy, instrument, [50.0], start=OPEN_ET - pd.Timedelta(seconds=5))
        assert strategy._flips == 0

    def test_no_flip_after_deadline(self, strategy, instrument):
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        strategy._on_deadline(None)
        _feed(strategy, instrument, [99.0])
        assert strategy._flips == 0


class TestDeadline:
    def test_deadline_cancels_open_entries(self, strategy, instrument):
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        strategy._on_deadline(None)
        assert len(strategy._recorder.canceled) == 2
        assert strategy._entry_deadline_passed is True


class TestOcoExit:
    def test_first_fill_submits_oco(self, strategy, instrument):
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        strategy._side = OrderSide.SELL
        strategy._submit_oco(OrderSide.BUY, 10)

        assert len(strategy._recorder.submitted_lists) == 1
        order_list = strategy._recorder.submitted_lists[0]
        sl, tp = order_list.orders

        assert float(tp.price) == 100.00  # take profit back at the open
        assert float(sl.trigger_price) == 103.00  # stop $3 beyond it
        assert tp.side == sl.side == OrderSide.BUY
        assert tp.is_reduce_only
        assert sl.is_reduce_only
        assert tp.contingency_type == ContingencyType.OUO
        assert sl.contingency_type == ContingencyType.OUO
        assert tp.order_list_id == sl.order_list_id
        assert tp.client_order_id in sl.linked_order_ids
        assert sl.client_order_id in tp.linked_order_ids
        assert TAKE_PROFIT_TAG in tp.tags
        assert STOP_LOSS_TAG in sl.tags

    def test_long_side_stop_is_below_the_open(self, strategy, instrument):
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        strategy._side = OrderSide.BUY
        strategy._submit_oco(OrderSide.SELL, 10)

        sl, tp = strategy._recorder.submitted_lists[0].orders
        assert float(tp.price) == 100.00
        assert float(sl.trigger_price) == 97.00

    def test_second_fill_resizes_rather_than_adding_a_pair(self, strategy, instrument):
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        strategy._net_position = lambda: -10
        strategy._sync_oco()
        assert len(strategy._recorder.submitted_lists) == 1

        # A second entry fill grows the position to 20.
        strategy._net_position = lambda: -20
        strategy._recorder.modified.clear()
        strategy._sync_oco()

        assert len(strategy._recorder.submitted_lists) == 1  # still ONE pair
        resized = [int(kw["quantity"]) for _, kw in strategy._recorder.modified]
        assert resized == [20, 20]  # both legs

    def test_partial_fill_while_legs_in_flight_is_resized_on_accept(self, strategy, instrument):
        """
        Regression: two partials in the same nanosecond left shares unprotected.

        The first partial submits the OCO; the second arrives while both legs are still in
        flight (neither open nor closed) and so cannot be modified. `on_order_accepted` must
        catch up the sizing, otherwise the remainder rides to the flatten with no exit.
        """
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        rec = strategy._recorder

        rec.modified.clear()  # drop the 09:30 entry reprices

        # First partial: 2 shares -> OCO(2). Submitted but NOT yet accepted.
        rec.auto_accept = False
        strategy._net_position = lambda: -2
        strategy._submit_oco(OrderSide.BUY, 2)
        sl, tp = rec.submitted_lists[0].orders
        for leg in (sl, tp):
            leg.apply(_submitted_event(leg))  # in flight: neither open nor closed
        assert not tp.is_open
        assert not tp.is_closed

        # Second partial in the same instant takes the position to 10.
        strategy._net_position = lambda: -10
        strategy._sync_oco()
        assert len(rec.submitted_lists) == 1  # no duplicate pair
        assert rec.modified == []  # cannot modify an in-flight leg

        # The venue accepts the legs: sizing must now catch up to 10.
        rec.modified.clear()
        for leg in (sl, tp):
            leg.apply(_accepted_event(leg))
        strategy.on_order_accepted(_AcceptedStub(tp.client_order_id))

        assert sorted(int(kw["quantity"]) for _, kw in rec.modified) == [10, 10]

    def test_new_pair_submitted_after_previous_pair_completed(self, strategy, instrument):
        """Regression: once a pair reached a terminal state, later fills got no exit at all."""
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        rec = strategy._recorder

        strategy._net_position = lambda: -2
        strategy._sync_oco()
        sl, tp = rec.submitted_lists[0].orders

        # The pair completes: TP filled, SL canceled.
        tp.apply(_filled_event(tp, instrument))
        sl.apply(_canceled_event(sl))
        assert tp.is_closed
        assert sl.is_closed

        # A later partial fill rebuilds the position; it needs a brand new pair.
        strategy._net_position = lambda: -8
        strategy._sync_oco()

        assert len(rec.submitted_lists) == 2
        new_sl, new_tp = rec.submitted_lists[1].orders
        assert int(new_tp.quantity) == 8
        assert float(new_tp.price) == 100.00
        assert float(new_sl.trigger_price) == 103.00

    def test_flat_position_submits_nothing(self, strategy, instrument):
        _arm_and_open(strategy, instrument, [100.0] * 10, 100.0)
        strategy._net_position = lambda: 0
        strategy._sync_oco()
        assert strategy._recorder.submitted_lists == []


class TestPreOpenFill:
    def test_pre_open_fill_defers_oco_until_open_price_known(self, strategy, instrument):
        _feed(strategy, instrument, [100.0] * 10, start=OPEN_ET - pd.Timedelta(seconds=10))
        strategy._on_arm(None)
        entries = _entries(strategy)

        # An entry fills before 09:30, when open_price does not exist yet.
        strategy._any_fill = True
        strategy._pending_oco = True
        assert strategy.open_price is None
        assert strategy._recorder.submitted_lists == []

        # The open arrives: no modify for the filled order, and the OCO is now submitted.
        entries[0].apply(_filled_event(entries[0], instrument))
        strategy._net_position = lambda: -10
        _feed(strategy, instrument, [101.0], start=OPEN_ET - pd.Timedelta(milliseconds=1))
        strategy._on_open(None)

        assert strategy.open_price == 101.0
        assert len(strategy._recorder.submitted_lists) == 1
        modified_ids = [o.client_order_id for o, _ in strategy._recorder.modified]
        assert entries[0].client_order_id not in modified_ids  # closed order not repriced
        assert strategy._pending_oco is False


# ----------------------------------------------------------------------
# Event helpers
# ----------------------------------------------------------------------


def _accept(order):
    """Walk an order INITIALIZED -> SUBMITTED -> ACCEPTED so `is_open` is True."""
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import OrderAccepted
    from nautilus_trader.model.events import OrderSubmitted
    from nautilus_trader.model.identifiers import AccountId
    from nautilus_trader.model.identifiers import VenueOrderId

    account_id = AccountId("ALPACA-001")
    order.apply(
        OrderSubmitted(
            trader_id=order.trader_id,
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            account_id=account_id,
            event_id=UUID4(),
            ts_event=0,
            ts_init=0,
        )
    )
    order.apply(
        OrderAccepted(
            trader_id=order.trader_id,
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId(f"V-{order.client_order_id.value}"),
            account_id=account_id,
            event_id=UUID4(),
            ts_event=0,
            ts_init=0,
        )
    )


class _AcceptedStub:
    """Minimal stand-in for OrderAccepted (only client_order_id is read)."""

    def __init__(self, client_order_id):
        self.client_order_id = client_order_id


def _submitted_event(order):
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import OrderSubmitted
    from nautilus_trader.model.identifiers import AccountId

    return OrderSubmitted(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        account_id=AccountId("ALPACA-001"),
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
    )


def _accepted_event(order):
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import OrderAccepted
    from nautilus_trader.model.identifiers import AccountId
    from nautilus_trader.model.identifiers import VenueOrderId

    return OrderAccepted(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        venue_order_id=VenueOrderId(f"V-{order.client_order_id.value}"),
        account_id=AccountId("ALPACA-001"),
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
    )


def _canceled_event(order):
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import OrderCanceled

    return OrderCanceled(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        venue_order_id=None,
        account_id=None,
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
    )


def _filled_event(order, instrument):
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.enums import LiquiditySide
    from nautilus_trader.model.events import OrderFilled
    from nautilus_trader.model.identifiers import AccountId
    from nautilus_trader.model.identifiers import PositionId
    from nautilus_trader.model.identifiers import TradeId

    return OrderFilled(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        venue_order_id=order.venue_order_id,
        account_id=AccountId("ALPACA-001"),
        trade_id=TradeId("E-1"),
        position_id=PositionId("P-1"),
        order_side=order.side,
        order_type=order.order_type,
        last_qty=order.quantity,
        last_px=order.price,
        currency=instrument.quote_currency,
        commission=Money(0, instrument.quote_currency),
        liquidity_side=LiquiditySide.MAKER,
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
    )
