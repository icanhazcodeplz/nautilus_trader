"""
Tests for holding modifies while an Alpaca order is still pending_new.

Alpaca refuses to replace an order until it leaves pending_new ("cannot replace order in
pending_new status"). Around the 2026-09-24 open, orders sat in pending_new for 11-18s, and the
strategy re-sent a modify every ~1.3s -- each one a 422 plus a full reconciliation. The exec client
now holds such a modify and bounces it back as a rejection once Alpaca confirms the order.
"""

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from nautilus_trader.adapters.alpaca.execution import MODIFY_HELD_PENDING_NEW_REASON
from nautilus_trader.adapters.alpaca.execution import AlpacaExecutionClient
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from tests.custom.test_handle_ws_message import make_mock_client


VENUE_ID = "f49bd63b-7757-4b50-9848-8ffd9fe79553"
CLIENT_ID = "O-20260924-133018-20260924_092629-000-3"


def _client(pending=True):
    order = {"client_order_id": CLIENT_ID, "side": "sell", "qty": "100", "limit_price": "246.23"}
    client = make_mock_client(order=order)
    client._cache.order.return_value.is_closed = False
    client._cache.venue_order_id.return_value = VenueOrderId(VENUE_ID)
    client._pending_new_venue_ids = {VENUE_ID} if pending else set()
    client._ws_new_venue_ids = set()
    client._held_modifies = {}
    client._http_client.replace_order = AsyncMock(return_value={"id": "replacement-id"})
    client._release_pending_new = lambda venue_id: AlpacaExecutionClient._release_pending_new(client, venue_id)
    return client


def _modify_command():
    command = MagicMock()
    command.strategy_id = StrategyId("MomoStrategy-000")
    command.instrument_id = InstrumentId.from_str("AMZN.ALPACA")
    command.client_order_id = ClientOrderId(CLIENT_ID)
    command.venue_order_id = VenueOrderId(VENUE_ID)
    command.quantity = Quantity.from_int(100)
    command.price = Price.from_str("246.25")
    command.trigger_price = None
    return command


def _ws_msg(event):
    return {
        "stream": "trade_updates",
        "data": {
            "event": event,
            "timestamp": "2026-09-24T13:30:36.636095750Z",
            "order": {"id": VENUE_ID, "client_order_id": CLIENT_ID, "asset_class": "us_equity"},
        },
    }


def test_a_modify_for_a_pending_new_order_is_held_not_sent():
    client = _client(pending=True)

    asyncio.run(AlpacaExecutionClient._modify_order(client, _modify_command()))

    client._http_client.replace_order.assert_not_called()
    client.generate_order_modify_rejected.assert_not_called()
    assert VENUE_ID in client._held_modifies
    client._loop.call_later.assert_called_once()


def test_a_modify_for_a_confirmed_order_is_sent():
    client = _client(pending=False)

    asyncio.run(AlpacaExecutionClient._modify_order(client, _modify_command()))

    client._http_client.replace_order.assert_awaited_once()
    assert client._held_modifies == {}


def test_ws_new_bounces_the_held_modify_back_as_a_rejection():
    client = _client(pending=True)
    asyncio.run(AlpacaExecutionClient._modify_order(client, _modify_command()))

    AlpacaExecutionClient._handle_ws_message(client, _ws_msg("new"))

    client.generate_order_modify_rejected.assert_called_once()
    assert client.generate_order_modify_rejected.call_args.kwargs["reason"] == MODIFY_HELD_PENDING_NEW_REASON
    assert VENUE_ID not in client._pending_new_venue_ids
    assert client._held_modifies == {}


def test_ws_pending_new_does_not_release_the_hold():
    client = _client(pending=True)
    asyncio.run(AlpacaExecutionClient._modify_order(client, _modify_command()))

    AlpacaExecutionClient._handle_ws_message(client, _ws_msg("pending_new"))

    client.generate_order_modify_rejected.assert_not_called()
    assert VENUE_ID in client._held_modifies


def test_a_held_modify_on_an_order_that_closed_is_dropped_silently():
    client = _client(pending=True)
    asyncio.run(AlpacaExecutionClient._modify_order(client, _modify_command()))
    client._cache.order.return_value.is_closed = True

    AlpacaExecutionClient._handle_ws_message(client, _ws_msg("canceled"))

    client.generate_order_modify_rejected.assert_not_called()
    assert VENUE_ID not in client._pending_new_venue_ids
    assert client._held_modifies == {}


def test_the_timeout_releases_a_hold_whose_ws_new_never_arrived():
    client = _client(pending=True)
    asyncio.run(AlpacaExecutionClient._modify_order(client, _modify_command()))

    _delay, callback, venue_id = client._loop.call_later.call_args.args
    callback(venue_id)

    client.generate_order_modify_rejected.assert_called_once()
    assert VENUE_ID not in client._pending_new_venue_ids


def test_ws_new_arriving_before_the_submit_response_is_remembered():
    client = _client(pending=False)

    AlpacaExecutionClient._handle_ws_message(client, _ws_msg("new"))

    assert VENUE_ID in client._ws_new_venue_ids
