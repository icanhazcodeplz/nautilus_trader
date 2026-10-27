"""
Tests that a replaced order lingering in Alpaca's open-orders list is not treated as a ghost.

Alpaca's order list can keep returning the pre-replace order as open for several seconds after
the replace. In run 20261001_092727 the reconciliation 5s after a replace saw the old entry order
open while the cached order was FILLED under the replacement id, flagged it as a ghost, and sent a
cancel that Alpaca refused (already replaced). The exec client now remembers replaced venue ids.
"""

import asyncio
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from nautilus_trader.adapters.alpaca.execution import AlpacaExecutionClient
from nautilus_trader.model.identifiers import VenueOrderId
from tests.custom.test_alpaca_pending_new_modify import CLIENT_ID
from tests.custom.test_alpaca_pending_new_modify import VENUE_ID
from tests.custom.test_alpaca_pending_new_modify import _client
from tests.custom.test_alpaca_pending_new_modify import _modify_command


REPLACEMENT_ID = "ac869530-d714-4b2d-9096-acd15d612b1f"


def test_a_successful_replace_records_the_old_venue_id():
    client = _client(pending=False)
    client._replaced_venue_ids = set()

    asyncio.run(AlpacaExecutionClient._modify_order(client, _modify_command()))

    assert client._replaced_venue_ids == {VENUE_ID}


def test_ws_replaced_records_the_old_venue_id():
    client = _client(pending=False)
    client._replaced_venue_ids = set()
    msg = {
        "stream": "trade_updates",
        "data": {
            "event": "replaced",
            "timestamp": "2026-10-01T13:30:02.751Z",
            "order": {
                "id": VENUE_ID,
                "client_order_id": CLIENT_ID,
                "asset_class": "us_equity",
                "replaced_by": REPLACEMENT_ID,
            },
        },
    }

    AlpacaExecutionClient._handle_ws_message(client, msg)

    assert client._replaced_venue_ids == {VENUE_ID}


def _reconcile_with_stale_open_order(replaced_venue_ids):
    """Run status-report generation where Alpaca lists VENUE_ID open but the cache has it FILLED as its replacement."""
    client = MagicMock()
    client._replaced_venue_ids = set(replaced_venue_ids)
    client._handled_ghost_ids = set()
    client.filter_replaced_and_incomplete_orders = lambda orders: orders
    client._http_client.get_orders = AsyncMock(
        return_value=[{"id": VENUE_ID, "client_order_id": CLIENT_ID, "replaced_by": None}],
    )
    client._http_client.cancel_order = AsyncMock()
    cached_order = client._cache.order.return_value
    cached_order.is_closed = True
    cached_order.venue_order_id = VenueOrderId(REPLACEMENT_ID)
    command = MagicMock(open_only=True, start=None)

    reports = asyncio.run(AlpacaExecutionClient.generate_order_status_reports(client, command))
    return client, reports


def test_a_known_replaced_order_listed_as_open_is_skipped_not_canceled():
    client, reports = _reconcile_with_stale_open_order(replaced_venue_ids={VENUE_ID})

    assert reports == []
    client._http_client.cancel_order.assert_not_called()
    client._log.warning.assert_not_called()


def test_an_unknown_order_listed_as_open_is_still_canceled_as_a_ghost():
    client, reports = _reconcile_with_stale_open_order(replaced_venue_ids=set())

    assert reports == []
    client._http_client.cancel_order.assert_awaited_once_with(VENUE_ID)
