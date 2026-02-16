import json
from pathlib import Path
from unittest.mock import MagicMock


from nautilus_trader.adapters.alpaca.execution import AlpacaExecutionClient
from nautilus_trader.adapters.alpaca.parsing import AlpacaEnumParser
from nautilus_trader.model.enums import OrderSide, OrderType
from nautilus_trader.model.identifiers import (
    ClientOrderId,
    InstrumentId,
    StrategyId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Price, Quantity

DATA_DIR = Path(__file__).parent / "data"

# Keys that live at msg["data"] level (not inside msg["data"]["order"])
DATA_LEVEL_KEYS = {
    "event", "event_id", "timestamp", "at",
    "qty", "price", "position_qty", "execution_id",
}
# Keys that live at the top level of the raw websocket message
TOP_LEVEL_KEYS = {"stream", "msg_received_dt"}


def unflatten_trade_update(flat: dict) -> dict:
    """Reconstruct the nested websocket message format from the flattened saved format."""
    data_fields = {}
    order_fields = {}
    top_fields = {}

    for k, v in flat.items():
        if k in TOP_LEVEL_KEYS:
            top_fields[k] = v
        elif k in DATA_LEVEL_KEYS:
            data_fields[k] = v
        else:
            order_fields[k] = v

    data_fields["order"] = order_fields
    return {"stream": top_fields.get("stream", "trade_updates"), "data": data_fields}


def load_trade_updates() -> list[dict]:
    with open(DATA_DIR / "alpaca_trade_updates.json") as f:
        return json.load(f)


def make_mock_order(
    client_order_id="O-TEST-001",
    venue_order_id="abc-123",
    instrument_id="RIME.ALPACA",
    side=OrderSide.BUY,
    order_type=OrderType.LIMIT,
    filled_qty=0,
    quantity=100,
    price=4.78,
):
    """Create a mock order object with the properties _handle_ws_message reads."""
    order = MagicMock()
    order.strategy_id = StrategyId("MomoStrategy-001")
    order.instrument_id = InstrumentId.from_str(instrument_id)
    order.side = side
    order.order_type = order_type
    order.filled_qty = Quantity.from_int(filled_qty)
    order.quantity = Quantity.from_int(quantity)
    order.price = Price.from_str(f"{price:.2f}")
    order.has_trigger_price = False
    order.trigger_price = None
    return order


def make_mock_client(order=None):
    """Create a mock AlpacaExecutionClient with the attributes _handle_ws_message needs."""
    client = MagicMock()
    client._enum_parser = AlpacaEnumParser()
    client._clock.timestamp_ns.return_value = 0
    client._processed_execution_ids = set()
    client._pending_modify_params = {}
    client._trade_updates_data = []
    client._trade_updates_output_file_path = "/dev/null"

    if order:
        client._cache.client_order_id.return_value = ClientOrderId(order.get("client_order_id", "O-TEST-001"))
        mock_order = make_mock_order(
            client_order_id=order.get("client_order_id", "O-TEST-001"),
            side=OrderSide.BUY if order.get("side") == "buy" else OrderSide.SELL,
            filled_qty=int(order.get("filled_qty", 0)),
            quantity=int(order.get("qty", 100)),
            price=float(order.get("limit_price", 4.78)),
        )
        client._cache.order.return_value = mock_order

    return client


class TestHandleWsMessage:

    def test_new_event_generates_order_accepted(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "new")
        msg = unflatten_trade_update(flat_msg)

        client = make_mock_client(order=msg["data"]["order"])
        AlpacaExecutionClient._handle_ws_message(client, msg)

        client.generate_order_accepted.assert_called_once()
        call_kwargs = client.generate_order_accepted.call_args
        assert call_kwargs.kwargs["venue_order_id"] == VenueOrderId(flat_msg["id"])

    def test_fill_event_generates_order_filled(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "fill")
        msg = unflatten_trade_update(flat_msg)

        # Set cache filled_qty lower than alpaca filled_qty so the fill is not skipped
        order_data = msg["data"]["order"]
        client = make_mock_client(order=order_data)
        client._cache.order.return_value.filled_qty = Quantity.from_int(0)

        AlpacaExecutionClient._handle_ws_message(client, msg)

        client.generate_order_filled.assert_called_once()
        call_kwargs = client.generate_order_filled.call_args
        assert call_kwargs.kwargs["trade_id"].value == flat_msg["execution_id"]
        assert int(call_kwargs.kwargs["last_qty"]) == int(flat_msg["qty"])
        assert float(call_kwargs.kwargs["last_px"]) == float(flat_msg["price"])

    def test_partial_fill_generates_order_filled(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "partial_fill")
        msg = unflatten_trade_update(flat_msg)

        order_data = msg["data"]["order"]
        client = make_mock_client(order=order_data)
        client._cache.order.return_value.filled_qty = Quantity.from_int(0)

        AlpacaExecutionClient._handle_ws_message(client, msg)

        client.generate_order_filled.assert_called_once()
        call_kwargs = client.generate_order_filled.call_args
        assert int(call_kwargs.kwargs["last_qty"]) == int(flat_msg["qty"])

    def test_canceled_event_generates_order_canceled(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "canceled")
        msg = unflatten_trade_update(flat_msg)

        client = make_mock_client(order=msg["data"]["order"])
        AlpacaExecutionClient._handle_ws_message(client, msg)

        client.generate_order_canceled.assert_called_once()
        call_kwargs = client.generate_order_canceled.call_args
        assert call_kwargs.kwargs["venue_order_id"] == VenueOrderId(flat_msg["id"])

    def test_replaced_event_generates_order_updated(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "replaced")
        msg = unflatten_trade_update(flat_msg)

        order_data = msg["data"]["order"]
        client = make_mock_client(order=order_data)

        # Simulate pending modify params (the normal path)
        coid = ClientOrderId(order_data["client_order_id"])
        new_qty = Quantity.from_int(5)
        new_price = Price.from_str("4.85")
        client._pending_modify_params[coid] = (new_qty, new_price)

        AlpacaExecutionClient._handle_ws_message(client, msg)

        client.generate_order_updated.assert_called_once()
        call_kwargs = client.generate_order_updated.call_args
        assert call_kwargs.kwargs["venue_order_id"] == VenueOrderId(flat_msg["replaced_by"])
        assert call_kwargs.kwargs["quantity"] == new_qty
        assert call_kwargs.kwargs["price"] == new_price

    def test_duplicate_execution_id_skipped(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "fill")
        msg = unflatten_trade_update(flat_msg)

        order_data = msg["data"]["order"]
        client = make_mock_client(order=order_data)
        client._cache.order.return_value.filled_qty = Quantity.from_int(0)

        # Pre-populate the execution id as already processed
        client._processed_execution_ids.add(flat_msg["execution_id"])

        AlpacaExecutionClient._handle_ws_message(client, msg)

        client.generate_order_filled.assert_not_called()

    def test_fill_skipped_when_cache_already_reconciled(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "fill")
        msg = unflatten_trade_update(flat_msg)

        order_data = msg["data"]["order"]
        client = make_mock_client(order=order_data)
        # Set cache filled_qty >= alpaca filled_qty
        alpaca_filled = int(order_data["filled_qty"])
        client._cache.order.return_value.filled_qty = Quantity.from_int(alpaca_filled)

        AlpacaExecutionClient._handle_ws_message(client, msg)

        client.generate_order_filled.assert_not_called()

    def test_execution_id_tracked_after_fill(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "fill")
        msg = unflatten_trade_update(flat_msg)

        order_data = msg["data"]["order"]
        client = make_mock_client(order=order_data)
        client._cache.order.return_value.filled_qty = Quantity.from_int(0)

        AlpacaExecutionClient._handle_ws_message(client, msg)

        assert flat_msg["execution_id"] in client._processed_execution_ids

    def test_pending_new_event_does_not_generate_anything(self):
        flat_data = load_trade_updates()
        flat_msg = next(m for m in flat_data if m["event"] == "pending_new")
        msg = unflatten_trade_update(flat_msg)

        client = make_mock_client(order=msg["data"]["order"])
        AlpacaExecutionClient._handle_ws_message(client, msg)

        client.generate_order_accepted.assert_not_called()
        client.generate_order_filled.assert_not_called()
        client.generate_order_canceled.assert_not_called()
        client.generate_order_updated.assert_not_called()

    def test_message_appended_to_trade_updates_data(self):
        flat_data = load_trade_updates()
        flat_msg = flat_data[0]
        msg = unflatten_trade_update(flat_msg)

        client = make_mock_client(order=msg["data"]["order"])
        AlpacaExecutionClient._handle_ws_message(client, msg)

        assert len(client._trade_updates_data) == 1
