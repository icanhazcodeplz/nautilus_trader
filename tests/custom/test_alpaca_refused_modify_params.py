"""
Tests that a refused modify does not leave its values in `_pending_modify_params`.

At the 2026-11-02 open, Alpaca accepted a modify of an AAPL sell to 344.41 but held the replace
pending for ~5s. Three further modifies in that window were refused ("order already pending
replacement"), yet each had already overwritten the pending params, so when the WS "replaced"
event finally arrived the replacement was cached at 344.02 -- the last refused price -- while it
actually rested at 344.41.
"""

import asyncio
from unittest.mock import AsyncMock

from nautilus_trader.adapters.alpaca.execution import AlpacaExecutionClient
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import VenueOrderId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from tests.custom.test_alpaca_pending_new_modify import CLIENT_ID
from tests.custom.test_alpaca_pending_new_modify import VENUE_ID
from tests.custom.test_alpaca_pending_new_modify import _client
from tests.custom.test_alpaca_pending_new_modify import _modify_command


REPLACEMENT_ID = "581e79ea-a819-48e3-a175-f4f4e57013b5"
PENDING_REPLACEMENT = Exception('HTTP 422: {"code":42210000,"message":"order already pending replacement"}')
COID = ClientOrderId(CLIENT_ID)


def _setup():
    client = _client(pending=False)
    client._restore_pending_modify_params = lambda *args: AlpacaExecutionClient._restore_pending_modify_params(
        client,
        *args,
    )
    return client


def _modify(client, price, error=None):
    command = _modify_command()
    command.price = Price.from_str(price)
    if error is None:
        client._http_client.replace_order = AsyncMock(return_value={"id": REPLACEMENT_ID})
    else:
        client._http_client.replace_order = AsyncMock(side_effect=error)
    asyncio.run(AlpacaExecutionClient._modify_order(client, command))


def _ws_replaced():
    return {
        "stream": "trade_updates",
        "data": {
            "event": "replaced",
            "timestamp": "2026-11-02T14:30:05.109Z",
            "order": {
                "id": VENUE_ID,
                "client_order_id": "4ec7d1b8-0a23-4f10-b1d2-94f9d2502656",
                "replaced_by": REPLACEMENT_ID,
                "asset_class": "us_equity",
                "qty": "100",
                "limit_price": "246.23",
            },
        },
    }


def test_a_refused_modify_restores_the_pending_replace_params():
    client = _setup()
    _modify(client, "344.41")  # accepted, replace held pending at the venue

    _modify(client, "344.02", error=PENDING_REPLACEMENT)

    assert client._pending_modify_params[COID] == (Quantity.from_int(100), Price.from_str("344.41"))
    client.generate_order_modify_rejected.assert_called_once()


def test_the_replace_landing_after_a_refused_modify_gets_the_accepted_price():
    """End to end over the 2026-11-02 AAPL sequence."""
    client = _setup()
    _modify(client, "344.41")
    for price in ("344.25", "344.11", "344.02"):
        _modify(client, price, error=PENDING_REPLACEMENT)

    AlpacaExecutionClient._handle_ws_message(client, _ws_replaced())

    client.generate_order_updated.assert_called_once()
    kwargs = client.generate_order_updated.call_args.kwargs
    assert kwargs["venue_order_id"] == VenueOrderId(REPLACEMENT_ID)
    assert kwargs["price"] == Price.from_str("344.41")


def test_a_refused_modify_with_nothing_pending_leaves_no_params():
    client = _setup()

    _modify(client, "344.02", error=PENDING_REPLACEMENT)

    assert COID not in client._pending_modify_params


def test_an_accepted_modify_keeps_its_params():
    client = _setup()

    _modify(client, "344.41")

    assert client._pending_modify_params[COID] == (Quantity.from_int(100), Price.from_str("344.41"))


def test_restore_leaves_params_a_replaced_event_already_swapped_in():
    """If the entry is no longer the failed one, something newer owns it; don't clobber that."""
    client = _setup()
    failed = (Quantity.from_int(100), Price.from_str("344.02"))
    newer = (Quantity.from_int(100), Price.from_str("344.50"))
    client._pending_modify_params[COID] = newer

    AlpacaExecutionClient._restore_pending_modify_params(client, COID, failed, None)

    assert client._pending_modify_params[COID] is newer
