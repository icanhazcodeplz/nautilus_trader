"""
Tests for `live_performance_stats`: each symbol of a live run gets stats from its own positions,
though every symbol trades from the one account.
"""

import pytest

from custom.utils.run_utils import live_performance_stats
from custom.utils.run_utils import register_custom_statistics
from nautilus_trader.accounting.factory import AccountFactory
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs

ACCOUNT_ID = AccountId("ALPACA-001")
AMZN = TestInstrumentProvider.equity("AMZN", venue="ALPACA")
AAPL = TestInstrumentProvider.equity("AAPL", venue="ALPACA")


def _fill(instrument, side, qty, px, n):
    order = TestExecStubs.limit_order(
        instrument=instrument,
        order_side=side,
        price=Price.from_str(px),
        quantity=Quantity.from_int(qty),
        client_order_id=ClientOrderId(f"O-{instrument.id.symbol}-{n}"),
    )
    return TestEventStubs.order_filled(
        order=order,
        instrument=instrument,
        account_id=ACCOUNT_ID,
        trade_id=TradeId(f"E-{instrument.id.symbol}-{n}"),
        position_id=PositionId(f"{instrument.id}-1"),
        last_px=Price.from_str(px),
        commission=Money(0, USD),
        ts_event=n,
    )


def _short_round_trip(instrument, qty, entry_px, exit_px):
    position = Position(instrument, _fill(instrument, OrderSide.SELL, qty, entry_px, 1))
    position.apply(_fill(instrument, OrderSide.BUY, qty, exit_px, 2))
    return position


@pytest.fixture
def cache():
    cache = TestComponentStubs.cache()
    cache.add_account(AccountFactory.create(TestEventStubs.cash_account_state(account_id=ACCOUNT_ID)))
    for instrument in (AMZN, AAPL):
        cache.add_instrument(instrument)
    # 10-29: AMZN shorted 4 @ 248.27 and covered @ 247.73; AAPL shorted 2 @ 349.43 and covered @ 349.08
    cache.add_position(_short_round_trip(AMZN, 4, "248.27", "247.73"), OmsType.NETTING)
    cache.add_position(_short_round_trip(AAPL, 2, "349.43", "349.08"), OmsType.NETTING)
    return cache


@pytest.fixture
def analyzer(cache):
    """The node's analyzer: the Portfolio's statistics plus ours, with every close already recorded."""
    clock = TestClock()
    portfolio = Portfolio(msgbus=MessageBus(trader_id=TestIdStubs.trader_id(), clock=clock), cache=cache, clock=clock)
    analyzer = portfolio.analyzer
    register_custom_statistics(analyzer)
    # As the live portfolio does when each position closes, for every symbol on the one account
    for position in cache.positions():
        analyzer.record_trade(position.id, position.realized_pnl, position.ts_last)
    return analyzer


@pytest.mark.parametrize(("instrument", "pnl"), [(AMZN, 2.16), (AAPL, 0.70)])
def test_stats_count_only_the_symbols_own_trades(analyzer, cache, instrument, pnl):
    stats = live_performance_stats(analyzer, cache, instrument.id)

    assert stats["Num Trades"] == 1
    assert stats["Max Winner"] == pytest.approx(pnl)
    assert stats["Avg Winner"] == pytest.approx(pnl)
    assert stats["PnL (total)"] == pytest.approx(pnl)
    assert stats["PnL% (total)"] == pytest.approx(pnl / 1_000_000 * 100)


def test_the_analyzer_can_be_reused_for_one_symbol_after_another(analyzer, cache):
    first = live_performance_stats(analyzer, cache, AMZN.id)
    live_performance_stats(analyzer, cache, AAPL.id)

    assert live_performance_stats(analyzer, cache, AMZN.id) == pytest.approx(first, nan_ok=True)


def test_stats_for_a_symbol_that_did_not_trade_are_empty(analyzer, cache):
    msft = TestInstrumentProvider.equity("MSFT", venue="ALPACA")
    cache.add_instrument(msft)

    stats = live_performance_stats(analyzer, cache, msft.id)

    assert stats["Num Trades"] == 0
    assert stats["PnL (total)"] == 0.0


def test_no_alpaca_account_gives_no_stats(analyzer):
    assert live_performance_stats(analyzer, TestComponentStubs.cache(), AMZN.id) == {}
