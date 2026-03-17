from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any

import aiohttp
import pandas as pd

from nautilus_trader.common.component import Logger

from custom.utils.paths import run_artifacts_subdir
from nautilus_trader.adapters.alpaca.utils import get_alpaca_key_and_secret


class RateLimitReserveExhausted(Exception):
    """Raised when a low-priority request is rejected to reserve budget for trading."""

    pass


class AlpacaHttpClient:
    def __init__(
        self,
        paper: bool,
        timeout: int,
        record_orders: bool = False,
        rate_limit: int = 194,  # Slightly below alpaca limit of 200
        rate_limit_reserve: int = 30,  # Reserve for high-priority (trading) requests
    ) -> None:
        self.paper = paper
        self._api_key, self._api_secret = get_alpaca_key_and_secret(paper=self.paper)

        self._base_url = "https://paper-api.alpaca.markets" if self.paper else "https://api.alpaca.markets"
        self._data_base_url = "https://data.alpaca.markets"
        self.timeout = timeout
        self._log = Logger(name="AlpacaHttpClient")
        self._session: aiohttp.ClientSession | None = None
        self.record_orders = record_orders
        if record_orders:
            self._orders_file_buffer = open(run_artifacts_subdir("order_submissions.json"), "w")

        # Rate limiting: track request timestamps in a sliding window
        self._rate_limit = rate_limit
        self._rate_limit_reserve = rate_limit_reserve
        self._rate_window = 60.0  # 60 seconds (1 minute)
        self._request_timestamps: deque[float] = deque()
        self._rate_limit_lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure HTTP session is initialized."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            # if self.record_orders:
            #     self._orders_file_buffer.close()

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for authentication."""
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
            "Content-Type": "application/json",
        }

    async def _wait_for_rate_limit(self, priority: bool = True) -> None:
        """
        Enforce rate limiting using a sliding window approach.

        High-priority requests (trading: POST/PATCH/DELETE) will wait if the
        limit is reached. Low-priority requests (monitoring: GET /v2/orders list,
        positions, fills) are rejected early when the remaining budget drops
        below ``rate_limit_reserve``, so that trading always has headroom.

        Parameters
        ----------
        priority : bool, default True
            True for trading requests, False for monitoring/bulk-query requests.

        Raises
        ------
        RateLimitReserveExhausted
            If a low-priority request is rejected to preserve budget.
        """
        async with self._rate_limit_lock:
            current_time = time.time()

            # Remove timestamps older than the rate window
            while self._request_timestamps and current_time - self._request_timestamps[0] >= self._rate_window:
                self._request_timestamps.popleft()

            current_usage = len(self._request_timestamps)

            # Low-priority requests are rejected when budget is tight
            if not priority and current_usage >= self._rate_limit - self._rate_limit_reserve:
                self._log.warning(
                    f"Rate limit reserve hit: {current_usage}/{self._rate_limit} used, "
                    f"rejecting low-priority request (reserve={self._rate_limit_reserve})",
                )
                raise RateLimitReserveExhausted(
                    f"{current_usage}/{self._rate_limit} requests used, "
                    f"reserve of {self._rate_limit_reserve} preserved for trading"
                )

            # Priority request, but we've hit the rate limit, wait until we can make another request
            if current_usage >= self._rate_limit:
                sleep_time = self._rate_window - (current_time - self._request_timestamps[0])
                if sleep_time > 0:
                    self._log.warning(f"Rate limit {self._rate_limit} reached. Waiting {sleep_time:.2f} seconds.")
                    await asyncio.sleep(sleep_time)
                    current_time = time.time()

            # Record this request
            self._request_timestamps.append(current_time)

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        priority: bool = True,
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
        priority : bool, default True
            High-priority requests wait when rate-limited; low-priority
            requests raise RateLimitReserveExhausted instead.

        Returns
        -------
        dict[str, Any] or list[dict[str, Any]]
            The JSON response from the API.

        Raises
        ------
        Exception
            If the request fails.
        RateLimitReserveExhausted
            If a low-priority request is rejected to preserve budget.

        """
        # Enforce rate limiting before making the request
        await self._wait_for_rate_limit(priority=priority)

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
                self._log.debug(f"HTTP {response.status}: {text}")
                raise Exception(f"HTTP {response.status}: {text}")

            if response.status == 204:
                return {}

            return await response.json()

    # Account API

    async def get_account(self) -> dict[str, Any]:
        """Get account information."""
        return await self._request("GET", "/v2/account")  # type: ignore

    # Assets API

    def get_assets_non_async(
        self,
        status: str | None = None,
        asset_class: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get assets.

        Parameters
        ----------
        status : str, optional
            Filter by asset status (active, inactive).
        asset_class : str, optional
            Filter by asset class (us_equity, crypto).

        Returns
        -------
        list[dict[str, Any]]
            List of assets.

        """
        params = {}
        if status:
            params["status"] = status
        if asset_class:
            params["asset_class"] = asset_class

        return self._request("GET", "/v2/assets", params=params)  # type: ignore

    async def get_assets(
        self,
        status: str | None = None,
        asset_class: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get assets.

        Parameters
        ----------
        status : str, optional
            Filter by asset status (active, inactive).
        asset_class : str, optional
            Filter by asset class (us_equity, crypto).

        Returns
        -------
        list[dict[str, Any]]
            List of assets.

        """
        params = {}
        if status:
            params["status"] = status
        if asset_class:
            params["asset_class"] = asset_class

        return await self._request("GET", "/v2/assets", params=params)  # type: ignore

    async def get_asset(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/assets/{symbol}")  # type: ignore

    # Orders API

    async def _get_orders(
        self,
        status: str | None = None,
        limit: int = 500,
        after: str | None = None,
        until: str | None = None,
        direction: str | None = None,
        nested: bool | None = None,
        symbols: str | None = None,
        side: str | None = None,
        priority: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        if direction:
            params["direction"] = direction
        if nested is not None:
            params["nested"] = nested
        if symbols:
            params["symbols"] = symbols
        if side:
            params["side"] = side
        return await self._request("GET", "/v2/orders", params=params, priority=priority)  # type: ignore

    async def get_orders(
        self,
        status: str | None = None,
        after: str | None = None,
        until: str | None = None,
        direction: str | None = None,
        nested: bool | None = None,
        symbols: str | None = None,
        side: str | None = None,
        priority: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Get orders with automatic pagination.

        Parameters
        ----------
        status : str, optional
            Filter by order status (open, closed, all).
        after : str, optional
            Filter: orders created after this timestamp (RFC-3339).
        until : str, optional
            Filter: orders created before this timestamp (RFC-3339).
        direction : str, optional
            Sort order: "asc" or "desc" (default "desc").
        nested : bool, optional
            If true, include nested multi-leg orders.
        symbols : str, optional
            Comma-separated list of symbols to filter.
        side : str, optional
            Filter by order side: "buy" or "sell".
        priority : bool, default False
            Low-priority by default; stops paginating if rate limit reserve hit.
        """
        page_size = 500
        all_orders: list[dict[str, Any]] = []
        page_after = after
        while True:
            try:
                page = await self._get_orders(
                    status=status,
                    limit=page_size,
                    after=page_after,
                    until=until,
                    direction=direction,
                    nested=nested,
                    symbols=symbols,
                    side=side,
                    priority=priority,
                )
            except RateLimitReserveExhausted:
                self._log.warning(
                    f"Stopping order pagination early: got {len(all_orders)} orders before rate limit reserve was hit",
                )
                break
            if not page:
                break
            all_orders.extend(page)
            if len(page) < page_size:
                break
            page_after = page[-1]["created_at"]
        return all_orders

    async def get_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/orders/{order_id}")  # type: ignore

    def _record_order(self, order_type: str, submit_dt, order_params: dict[str, Any]) -> None:
        if self.record_orders:
            full_order = {"type": order_type, "submit_dt": str(submit_dt), **order_params}
            with open(run_artifacts_subdir("order_submissions.json"), "a") as f:
                f.write(f"{json.dumps(full_order)}\n")

    async def submit_order(self, order_request: dict[str, Any]) -> dict[str, Any]:
        submit_dt = pd.Timestamp.utcnow()
        response = await self._request("POST", "/v2/orders", json_data=order_request)
        self._record_order("submit", submit_dt=submit_dt, order_params={**order_request, "order_id": response["id"]})
        return response

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self._record_order("cancel", pd.Timestamp.utcnow(), {"order_id": order_id})
        return await self._request("DELETE", f"/v2/orders/{order_id}")

    async def cancel_all_orders(self) -> list[dict[str, Any]]:
        # You probably don't actually want this. This cancels for all symbols, not just the one you're trading.
        raise NotImplementedError
        return await self._request("DELETE", "/v2/orders")

    async def replace_order(
        self,
        order_id: str,
        # client_order_id: str,
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
        # Alpaca requires a unique client_order_id for each order submission
        # json_data = {"client_order_id": client_order_id}
        json_data = {}
        if qty:
            json_data["qty"] = qty
        if limit_price:
            json_data["limit_price"] = limit_price
        if stop_price:
            json_data["stop_price"] = stop_price
        if trail:
            json_data["trail"] = trail

        self._record_order("replace", pd.Timestamp.utcnow(), {"order_id": order_id, **json_data})
        return await self._request("PATCH", f"/v2/orders/{order_id}", json_data=json_data)  # type: ignore

    # Positions API

    async def get_positions(self, priority: bool = False) -> list[dict[str, Any]]:
        return await self._request("GET", "/v2/positions", priority=priority)  # type: ignore

    async def get_position(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/positions/{symbol}")  # type: ignore

    async def close_position(self, symbol: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v2/positions/{symbol}")  # type: ignore

    async def close_all_positions(self) -> list[dict[str, Any]]:
        return await self._request("DELETE", "/v2/positions")  # type: ignore

    # Activities API

    async def _get_fills(
        self,
        after: str,
        until: str = None,
        direction: str = "desc",
        page_size: int = 100,
        page_token: str | None = None,
        priority: bool = False,
    ):
        params: dict[str, Any] = {"after": after, "direction": direction, "page_size": page_size}
        if until is not None:
            params["until"] = until
        if page_token is not None:
            params["page_token"] = page_token
        return await self._request("GET", "/v2/account/activities/FILL", params=params, priority=priority)

    async def get_fills(self, after: str, until: str = None, direction: str = "asc") -> list[dict[str, Any]]:
        all_fills: list[dict[str, Any]] = []
        page_token = None
        while True:
            try:
                page = await self._get_fills(
                    after=after, until=until, direction=direction, page_token=page_token, page_size=100
                )
            except RateLimitReserveExhausted:
                self._log.warning(
                    f"Stopping fills pagination early: got {len(all_fills)} fills before rate limit reserve was hit",
                )
                break
            if not page:
                break
            all_fills.extend(page)
            if len(page) < 100:
                break
            page_token = page[-1]["id"]
        return all_fills

    # Market Data API

    async def get_trades(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        feed: str | None = None,
        sort: str | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Get historical trades for a symbol.

        Parameters
        ----------
        symbol : str
            The stock symbol.
        start : str, optional
            Start time in RFC-3339 format.
        end : str, optional
            End time in RFC-3339 format.
        limit : int, optional
            Maximum number of trades to return (max 10000).
        feed : str, optional
            The data feed: "iex" or "sip".
        sort : str, optional
            Sort order: "asc" or "desc".
        page_token : str, optional
            Pagination token for next page.
        """
        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if limit:
            params["limit"] = limit
        if feed:
            params["feed"] = feed
        if sort:
            params["sort"] = sort
        if page_token:
            params["page_token"] = page_token

        # Use data base URL instead of trading API base URL
        # Enforce rate limiting before making the request
        await self._wait_for_rate_limit()

        session = await self._ensure_session()
        url = f"{self._data_base_url}/v2/stocks/{symbol}/trades"
        headers = self._get_headers()

        self._log.debug(f"GET {url}")

        async with session.request(
            "GET",
            url,
            headers=headers,
            params=params,
        ) as response:
            if response.status >= 400:
                text = await response.text()
                self._log.error(f"HTTP {response.status}: {text}")
                raise Exception(f"HTTP {response.status}: {text}")

            return await response.json()  # type: ignore

    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        feed: str | None = None,
        sort: str | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Get historical bars for a symbol.

        Parameters
        ----------
        symbol : str
            The stock symbol.
        timeframe : str
            The bar timeframe (e.g., "1Min", "5Min", "1Hour", "1Day").
        start : str, optional
            Start time in RFC-3339 format.
        end : str, optional
            End time in RFC-3339 format.
        limit : int, optional
            Maximum number of bars to return (max 10000).
        feed : str, optional
            The data feed: "iex" or "sip".
        sort : str, optional
            Sort order: "asc" or "desc".
        page_token : str, optional
            Pagination token for next page.
        """
        params: dict[str, Any] = {"timeframe": timeframe}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if limit:
            params["limit"] = limit
        if feed:
            params["feed"] = feed
        if sort:
            params["sort"] = sort
        if page_token:
            params["page_token"] = page_token

        await self._wait_for_rate_limit()

        session = await self._ensure_session()
        url = f"{self._data_base_url}/v2/stocks/{symbol}/bars"
        headers = self._get_headers()

        self._log.debug(f"GET {url}")

        async with session.request(
            "GET",
            url,
            headers=headers,
            params=params,
        ) as response:
            if response.status >= 400:
                text = await response.text()
                self._log.error(f"HTTP {response.status}: {text}")
                raise Exception(f"HTTP {response.status}: {text}")

            return await response.json()  # type: ignore


#
# @lru_cache(1)
# def get_alpaca_http_client(
#     paper:bool,
#     timeout: int,
# ) -> AlpacaHttpClient:
#
#     return AlpacaHttpClient(paper=paper, timeout=timeout)
#
