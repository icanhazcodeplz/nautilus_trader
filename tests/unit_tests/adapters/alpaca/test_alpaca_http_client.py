# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2025 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

"""Tests for AlpacaHttpClient."""
from time import sleep

import pytest

from nautilus_trader.adapters.alpaca.http import AlpacaHttpClient

#
# @pytest.fixture
# async def alpaca_http_client():
#     """Create an AlpacaHttpClient instance for testing."""
#     yield client
#     await client.close()
#

@pytest.mark.asyncio
async def test_submit_order():
    """Test order submission via AlpacaHttpClient.submit_order()."""
    # Arrange
    order_request = {
        "side": "buy",
        "symbol": "AAPL",
        "type": "limit",
        "limit_price": "260",
        "qty": "1",
        "time_in_force": "day",
        "order_class": "bracket",
        "take_profit": {
            "limit_price": "300"
        },
        "stop_loss": {
            "stop_price": "259",
        }
    }

    alpaca_http_client = AlpacaHttpClient(paper=True, timeout=30, record_orders=False)
    # Act
    result = await alpaca_http_client.submit_order(order_request)
    # MONDAY TODO: test to see if updating order works
    sleep(4)
    order_id = result["id"]
    json_data = {
        "limit_price":"261",
        "take_profit": {
            "limit_price": "301"
        },
        "stop_loss": {
            "stop_price": "250",
        }
    }
    update = await alpaca_http_client._request("PATCH", f"/v2/orders/{order_id}", json_data=json_data)  # type: ignore
    print(update)

    # Assert
    # assert result is not None
    # assert "id" in result
    # assert result["symbol"] == "AAPL"
    # assert result["qty"] == "5"
    # assert result["side"] == "buy"
    # assert "status" in result

    # Clean up - cancel the order if it's still open
    try:
        await alpaca_http_client.cancel_order(result["id"])
    except Exception:
        # Order may have already filled or been rejected
        pass
    await alpaca_http_client.close()