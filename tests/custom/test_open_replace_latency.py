"""
Tests for `OpenReplaceLatency`: at the open a modify is held for a sampled latency before reaching
the venue, and a cancel that arrives while it is held is refused the way Alpaca refuses it.
"""

import pandas as pd
import pytest

from custom.backtest_utils.open_replace_latency import EXEC_ENGINE_ENDPOINT
from custom.backtest_utils.open_replace_latency import PENDING_REPLACEMENT_REASON
from custom.backtest_utils.open_replace_latency import OpenReplaceLatency
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import CancelOrder
from nautilus_trader.execution.messages import ModifyOrder
from nautilus_trader.execution.messages import SubmitOrder
from nautilus_trader.model.events import OrderCancelRejected
from nautilus_trader.model.objects import Price
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs

AUDUSD_SIM = TestInstrumentProvider.default_fx_ccy("AUD/USD")
MS = 1_000_000
MODIFY_LATENCY_NS = 55 * MS
CANCEL_LATENCY_NS = 55 * MS


def _et_ns(hhmmss: str) -> int:
    return pd.Timestamp(f"2026-10-30 {hhmmss}", tz="US/Eastern").value


class _FakeExecEngine:
    def __init__(self):
        self.executed = []
        self.processed = []

    def execute(self, command):
        self.executed.append(command)

    def process(self, event):
        self.processed.append(event)


class _Harness:
    def __init__(self, windows, now="09:30:00.100"):
        self.clock = TestClock()
        self.clock.set_time(_et_ns(now))
        self.msgbus = MessageBus(trader_id=TestIdStubs.trader_id(), clock=self.clock)
        self.cache = TestComponentStubs.cache()
        self.exec_engine = _FakeExecEngine()
        self.msgbus.register(EXEC_ENGINE_ENDPOINT, self.exec_engine.execute)
        self.model = OpenReplaceLatency(
            exec_engine=self.exec_engine,
            windows=windows,
            modify_latency_ns=MODIFY_LATENCY_NS,
            cancel_latency_ns=CANCEL_LATENCY_NS,
        )
        portfolio = Portfolio(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.model.register_base(portfolio=portfolio, msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.model.start()
        self.order = TestExecStubs.make_accepted_order(instrument=AUDUSD_SIM)
        self.cache.add_order(self.order)

    def send(self, command):
        self.msgbus.send(EXEC_ENGINE_ENDPOINT, command)

    def modify(self):
        return ModifyOrder(
            trader_id=self.order.trader_id,
            strategy_id=self.order.strategy_id,
            instrument_id=self.order.instrument_id,
            client_order_id=self.order.client_order_id,
            venue_order_id=self.order.venue_order_id,
            quantity=self.order.quantity,
            price=Price.from_str("1.00010"),
            trigger_price=None,
            command_id=UUID4(),
            ts_init=self.clock.timestamp_ns(),
        )

    def cancel(self):
        return CancelOrder(
            trader_id=self.order.trader_id,
            strategy_id=self.order.strategy_id,
            instrument_id=self.order.instrument_id,
            client_order_id=self.order.client_order_id,
            venue_order_id=self.order.venue_order_id,
            command_id=UUID4(),
            ts_init=self.clock.timestamp_ns(),
        )

    def advance_ms(self, ms):
        for handler in self.clock.advance_time(self.clock.timestamp_ns() + int(ms * MS)):
            handler.handle()


OPEN_WINDOW = [("09:30:00", "09:30:02.5", 3.0)]


def test_a_modify_outside_every_window_goes_straight_through():
    h = _Harness(OPEN_WINDOW, now="09:30:03")
    modify = h.modify()

    h.send(modify)

    assert h.exec_engine.executed == [modify]


def test_a_modify_at_the_open_is_held_for_the_windows_latency():
    h = _Harness(OPEN_WINDOW)
    modify = h.modify()

    h.send(modify)
    h.advance_ms(2999)
    assert h.exec_engine.executed == []

    h.advance_ms(1)
    assert h.exec_engine.executed == [modify]  # the same command, so the venue adds no latency of its own


def test_a_latency_no_slower_than_the_venue_latency_goes_straight_through():
    h = _Harness([("09:30:00", "09:30:02.5", MODIFY_LATENCY_NS / 1e9)])
    modify = h.modify()

    h.send(modify)

    assert h.exec_engine.executed == [modify]


def test_a_cancel_while_the_replace_is_pending_is_refused_and_never_reaches_the_venue():
    h = _Harness(OPEN_WINDOW)
    h.send(h.modify())
    h.advance_ms(500)

    h.send(h.cancel())
    h.advance_ms(CANCEL_LATENCY_NS / MS - 1)
    assert h.exec_engine.processed == []  # refused after the cancel's trip to the venue, not at once

    h.advance_ms(1)
    [event] = h.exec_engine.processed
    assert isinstance(event, OrderCancelRejected)
    assert event.client_order_id == h.order.client_order_id
    assert event.venue_order_id == h.order.venue_order_id
    assert event.reason == PENDING_REPLACEMENT_REASON
    assert not any(isinstance(c, CancelOrder) for c in h.exec_engine.executed)


def test_a_cancel_that_lands_after_the_replace_goes_through():
    h = _Harness(OPEN_WINDOW)
    h.send(h.modify())
    h.advance_ms(3000 - CANCEL_LATENCY_NS / MS)
    cancel = h.cancel()

    h.send(cancel)

    assert h.exec_engine.executed == [cancel]
    assert h.exec_engine.processed == []


def test_a_cancel_after_the_held_modify_is_released_goes_through():
    h = _Harness(OPEN_WINDOW)
    modify = h.modify()
    h.send(modify)
    h.advance_ms(3000)
    cancel = h.cancel()

    h.send(cancel)

    assert h.exec_engine.executed == [modify, cancel]


def test_a_cancel_of_an_order_that_filled_while_its_modify_was_held_goes_through():
    h = _Harness(OPEN_WINDOW)
    h.send(h.modify())
    h.order.apply(TestEventStubs.order_filled(order=h.order, instrument=AUDUSD_SIM))
    cancel = h.cancel()

    h.send(cancel)

    assert h.exec_engine.executed == [cancel]


def test_a_cancel_of_another_order_is_not_refused():
    h = _Harness(OPEN_WINDOW)
    h.send(h.modify())
    other = TestExecStubs.make_accepted_order(instrument=AUDUSD_SIM, client_order_id=TestIdStubs.client_order_id(2))
    h.cache.add_order(other)
    cancel = CancelOrder(
        trader_id=other.trader_id,
        strategy_id=other.strategy_id,
        instrument_id=other.instrument_id,
        client_order_id=other.client_order_id,
        venue_order_id=other.venue_order_id,
        command_id=UUID4(),
        ts_init=h.clock.timestamp_ns(),
    )

    h.send(cancel)

    assert h.exec_engine.executed == [cancel]


def test_other_commands_pass_straight_through():
    h = _Harness(OPEN_WINDOW)
    order = TestExecStubs.limit_order(instrument=AUDUSD_SIM, client_order_id=TestIdStubs.client_order_id(3))
    submit = SubmitOrder(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        order=order,
        command_id=UUID4(),
        ts_init=h.clock.timestamp_ns(),
    )

    h.send(submit)

    assert h.exec_engine.executed == [submit]


def test_stopping_restores_the_execution_engine_endpoint():
    h = _Harness(OPEN_WINDOW)
    h.model.stop()
    modify = h.modify()

    h.send(modify)

    assert h.exec_engine.executed == [modify]


def test_overlapping_windows_are_rejected():
    with pytest.raises(ValueError, match="overlap"):
        _Harness([("09:30:00", "09:30:02", 0.1), ("09:30:01", "09:30:03", 0.1)])


def test_a_negative_latency_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        _Harness([("09:30:00", "09:30:02", -0.1)])
