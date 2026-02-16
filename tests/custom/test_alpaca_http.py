from unittest.mock import MagicMock

import pytest

from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient
from nautilus_trader.adapters.alpaca.execution import AlpacaExecutionClient
from nautilus_trader.adapters.alpaca.parsing import AlpacaEnumParser
from nautilus_trader.execution.reports import FillReport
from nautilus_trader.model.identifiers import AccountId


@pytest.mark.asyncio
async def test_get_paper_fills_for_feb_13():
    client = AlpacaHttpClient(paper=True, timeout=10)
    try:
        fills = await client.get_fills(
            after="2026-02-13T05:04:00Z",
            until="2026-02-13T19:59:39Z",
            direction="asc",
        )

        print(f"\nTotal fills: {len(fills)}")
        for f in fills[:5]:
            print(f"  {f['transaction_time']} {f['side']} {f['qty']} {f['symbol']} @ {f['price']} exec_id={f['id']}")
        if len(fills) > 5:
            print(f"  ... and {len(fills) - 5} more")

        assert isinstance(fills, list)
        assert len(fills) > 0, "Expected at least one fill in this time range"

        # Verify expected fields are present
        first = fills[0]
        for field in ("id", "symbol", "side", "qty", "price", "transaction_time", "order_id"):
            assert field in first, f"Missing expected field: {field}"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_paper_orders_for_feb_13():
    client = AlpacaHttpClient(paper=True, timeout=10)
    try:
        orders = await client.get_orders(
            status="closed",
            after="2026-02-13T05:04:00Z",
            until="2026-02-13T19:59:39Z",
            direction="asc",
            # after="2026-02-13T15:30:00Z",
            # until="2026-02-13T15:50:00Z",
            # direction="asc",
        )

        print(f"\nTotal orders: {len(orders)}")
        for o in orders[:5]:
            print(
                f"  {o['created_at']} {o['side']} {o['qty']} {o['symbol']} status={o['status']} type={o['order_type']}"
            )
        if len(orders) > 5:
            print(f"  ... and {len(orders) - 5} more")

        assert isinstance(orders, list)
        assert len(orders) > 0, "Expected at least one order in this time range"

        first = orders[0]
        for field in ("id", "symbol", "side", "qty", "status", "order_type", "created_at"):
            assert field in first, f"Missing expected field: {field}"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_alpaca_fill_to_report():
    """Fetch real fills from Alpaca and convert them to FillReport objects."""
    http_client = AlpacaHttpClient(paper=True, timeout=10)
    try:
        fills = await http_client.get_fills(
            after="2026-02-13T06:00:00Z",
            until="2026-02-13T19:59:00Z",
            direction="asc",
        )
        assert len(fills) > 500  # If less than 500, we are not testing pagination

        # Build a minimal mock of AlpacaExecutionClient for _alpaca_fill_to_report
        mock_self = MagicMock()
        mock_self.account_id = AccountId("ALPACA-PAPER")
        mock_self._enum_parser = AlpacaEnumParser()
        mock_self._clock.timestamp_ns.return_value = 0

        reports = []
        for fill in fills:
            report = AlpacaExecutionClient._alpaca_fill_report_to_nt_report(mock_self, fill)
            reports.append(report)

        assert len(reports) == len(fills)

        # Verify all reports are FillReport instances with expected values
        first = reports[0]
        assert isinstance(first, FillReport)
        assert str(first.account_id) == "ALPACA-PAPER"
        assert first.ts_event > 0
        assert first.last_qty > 0
        assert first.last_px > 0

        print(f"\nConverted {len(reports)} fills to FillReports")
        for r in reports[:3]:
            print(f"  {r.instrument_id} {r.order_side.name} {r.last_qty} @ {r.last_px} venue_id={r.venue_order_id}")
    finally:
        await http_client.close()
