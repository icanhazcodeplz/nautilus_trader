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

"""HTTP client for Alpaca API."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from nautilus_trader.common.component import Logger


class AlpacaHttpClient:
    """
    HTTP client for Alpaca REST API.

    Parameters
    ----------
    base_url : str
        The base URL for the API.
    api_key : str
        The API key for authentication.
    api_secret : str
        The API secret for authentication.
    timeout : int
        The timeout for HTTP requests (seconds).
    logger : Logger
        The logger for the client.

    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        timeout: int,
        logger: Logger,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret
        self._timeout = timeout
        self._log = logger
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure HTTP session is initialized."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for authentication."""
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """
        Make an HTTP request to the Alpaca API.

        Parameters
        ----------
        method : str
            The HTTP method (GET, POST, DELETE, etc.).
        endpoint : str
            The API endpoint (e.g., "/v2/orders").
        params : dict[str, Any], optional
            Query parameters for the request.
        json_data : dict[str, Any], optional
            JSON body for the request.

        Returns
        -------
        dict[str, Any] or list[dict[str, Any]]
            The JSON response from the API.

        Raises
        ------
        Exception
            If the request fails.

        """
        session = await self._ensure_session()
        url = f"{self._base_url}{endpoint}"
        headers = self._get_headers()

        self._log.debug(f"{method} {url}")

        async with session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_data,
        ) as response:
            if response.status >= 400:
                text = await response.text()
                self._log.error(f"HTTP {response.status}: {text}")
                raise Exception(f"HTTP {response.status}: {text}")

            return await response.json()

    # Account API

    async def get_account(self) -> dict[str, Any]:
        """Get account information."""
        return await self._request("GET", "/v2/account")  # type: ignore

    # Orders API

    async def get_orders(
        self,
        status: str | None = None,
        limit: int | None = None,
        symbols: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get orders.

        Parameters
        ----------
        status : str, optional
            Filter by order status (open, closed, all).
        limit : int, optional
            Maximum number of orders to return.
        symbols : str, optional
            Comma-separated list of symbols to filter.

        Returns
        -------
        list[dict[str, Any]]
            List of orders.

        """
        params = {}
        if status:
            params["status"] = status
        if limit:
            params["limit"] = limit
        if symbols:
            params["symbols"] = symbols

        return await self._request("GET", "/v2/orders", params=params)  # type: ignore

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """
        Get a specific order by ID.

        Parameters
        ----------
        order_id : str
            The order ID.

        Returns
        -------
        dict[str, Any]
            The order details.

        """
        return await self._request("GET", f"/v2/orders/{order_id}")  # type: ignore

    async def submit_order(self, order_request: dict[str, Any]) -> dict[str, Any]:
        """
        Submit a new order.

        Parameters
        ----------
        order_request : dict[str, Any]
            The order request dictionary.

        Returns
        -------
        dict[str, Any]
            The created order details.

        """
        return await self._request("POST", "/v2/orders", json_data=order_request)  # type: ignore

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """
        Cancel an order.

        Parameters
        ----------
        order_id : str
            The order ID to cancel.

        Returns
        -------
        dict[str, Any]
            The cancellation response.

        """
        return await self._request("DELETE", f"/v2/orders/{order_id}")  # type: ignore

    async def cancel_all_orders(self) -> list[dict[str, Any]]:
        """
        Cancel all open orders.

        Returns
        -------
        list[dict[str, Any]]
            List of canceled order details.

        """
        return await self._request("DELETE", "/v2/orders")  # type: ignore

    async def replace_order(
        self,
        order_id: str,
        qty: str | None = None,
        limit_price: str | None = None,
        stop_price: str | None = None,
        trail: str | None = None,
    ) -> dict[str, Any]:
        """
        Replace/modify an order.

        Parameters
        ----------
        order_id : str
            The order ID to replace.
        qty : str, optional
            New quantity.
        limit_price : str, optional
            New limit price.
        stop_price : str, optional
            New stop price.
        trail : str, optional
            New trail amount/percent.

        Returns
        -------
        dict[str, Any]
            The updated order details.

        """
        json_data = {}
        if qty:
            json_data["qty"] = qty
        if limit_price:
            json_data["limit_price"] = limit_price
        if stop_price:
            json_data["stop_price"] = stop_price
        if trail:
            json_data["trail"] = trail

        return await self._request("PATCH", f"/v2/orders/{order_id}", json_data=json_data)  # type: ignore

    # Positions API

    async def get_positions(self) -> list[dict[str, Any]]:
        """
        Get all open positions.

        Returns
        -------
        list[dict[str, Any]]
            List of positions.

        """
        return await self._request("GET", "/v2/positions")  # type: ignore

    async def get_position(self, symbol: str) -> dict[str, Any]:
        """
        Get a specific position.

        Parameters
        ----------
        symbol : str
            The symbol for the position.

        Returns
        -------
        dict[str, Any]
            The position details.

        """
        return await self._request("GET", f"/v2/positions/{symbol}")  # type: ignore

    async def close_position(self, symbol: str) -> dict[str, Any]:
        """
        Close a position.

        Parameters
        ----------
        symbol : str
            The symbol for the position to close.

        Returns
        -------
        dict[str, Any]
            The closure response.

        """
        return await self._request("DELETE", f"/v2/positions/{symbol}")  # type: ignore

    async def close_all_positions(self) -> list[dict[str, Any]]:
        """
        Close all positions.

        Returns
        -------
        list[dict[str, Any]]
            List of closed positions.

        """
        return await self._request("DELETE", "/v2/positions")  # type: ignore
