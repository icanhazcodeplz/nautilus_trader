from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import websockets
from websockets import State

from nautilus_trader.common.component import Logger
from nautilus_trader.adapters.alpaca.utils import get_alpaca_key_and_secret


class _AlpacaWebSocketClient:
    """
    WebSocket client for Alpaca trading updates.

    Parameters
    ----------
    url : str
        The WebSocket URL.
    handler : Callable[[dict], None]
        The message handler callback.
    logger : Logger
        The logger for the client.

    """

    def __init__(
        self,
        url:str,
        paper:bool,
        handler: Callable[[dict], None],
        logger: Logger,
    ) -> None:
        self._url = url
        self._api_key, self._api_secret = get_alpaca_key_and_secret(paper=paper)
        self._handler = handler
        self._log = logger
        self._ws = None
        self._task: asyncio.Task | None = None
        self._is_running = False
        self._is_authenticated = False
        self._reconnect_task: asyncio.Task | None = None
        self._should_reconnect = True
        self._max_reconnect_delay = 60.0  # Maximum delay between reconnection attempts (seconds)
        self._reconnect_delay = 1.0  # Initial reconnection delay (seconds)

    @property
    def is_connected(self) -> bool:
        """Return whether the WebSocket is connected."""
        return self._ws is not None and self._ws.state == State.OPEN

    @property
    def is_authenticated(self) -> bool:
        """Return whether the WebSocket is authenticated."""
        return self._is_authenticated

    async def connect(self) -> None:
        """Connect to the WebSocket."""
        if self.is_connected:
            self._log.warning("WebSocket already connected")
            return

        self._log.info(f"Connecting to WebSocket: {self._url}")

        try:
            self._ws = await websockets.connect(self._url)
            self._is_running = True
            self._task = asyncio.create_task(self._run())

            # Wait for connection confirmation
            await asyncio.sleep(0.5)

            # Authenticate
            await self._authenticate()

            self._log.info("WebSocket connected")

        except Exception as e:
            self._log.error(f"Failed to connect to WebSocket: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket."""
        if not self.is_connected:
            return

        self._log.info("Disconnecting from WebSocket...")

        self._is_running = False
        self._is_authenticated = False
        self._should_reconnect = False  # Disable reconnection on manual disconnect

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._log.info("WebSocket disconnected")

    async def _authenticate(self) -> None:
        """Authenticate with the WebSocket."""
        auth_msg = {
            "action": "auth",
            "key": self._api_key,
            "secret": self._api_secret,
        }

        await self._send(auth_msg)
        self._log.debug("Sent authentication request")

    async def _send(self, message: dict[str, Any]) -> None:
        """Send a message to the WebSocket."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        json_msg = json.dumps(message)
        await self._ws.send(json_msg)
        if "secret" not in message:
            self._log.debug(f"Sent: {json_msg}")

    async def _run(self) -> None:
        """Run the WebSocket message loop."""
        if not self._ws:
            return

        self._log.debug("WebSocket message loop started")

        try:
            async for message in self._ws:
                if not self._is_running:
                    break

                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    self._log.error(f"Failed to decode message: {e}")
                except Exception as e:
                    self._log.error(f"Error handling message: {e}")

        except websockets.exceptions.ConnectionClosed:
            self._log.warning("WebSocket connection closed")
            if self._should_reconnect:
                self._log.info("Attempting to reconnect...")
                self._reconnect_task = asyncio.create_task(self._reconnect())
        except Exception as e:
            self._log.error(f"WebSocket error: {e}")
            if self._should_reconnect:
                self._log.info("Attempting to reconnect...")
                self._reconnect_task = asyncio.create_task(self._reconnect())
        finally:
            self._is_running = False
            self._is_authenticated = False
            self._log.debug("WebSocket message loop stopped")

    async def _handle_message(self, data: dict[str, Any] | list[dict[str, Any]]) -> None:
        """
        Handle an incoming WebSocket message.

        Parameters
        ----------
        data : dict or list
            The message data.

        """
        # Alpaca can send arrays of messages
        if isinstance(data, list):
            for msg in data:
                await self._handle_single_message(msg)
        else:
            await self._handle_single_message(data)

    async def _handle_single_message(self, msg: dict[str, Any]) -> None:
        raise NotImplementedError

    async def _reconnect(self) -> None:
        # FIXME: Move into single method and put here
        raise NotImplementedError



class AlpacaWebSocketClient(_AlpacaWebSocketClient):
    def __init__(
            self,
            paper: bool,
            handler: Callable[[dict], None],
            logger: Logger,
    ) -> None:
        url = "wss://paper-api.alpaca.markets/stream" if paper else "wss://api.alpaca.markets/stream"
        super().__init__(url=url, paper=paper, handler=handler, logger=logger)

    async def _handle_single_message(self, msg: dict[str, Any]) -> None:
        """
        Handle a single WebSocket message.

        Parameters
        ----------
        msg : dict
            The message data.

        """
        msg_type = msg.get("T") or msg.get("stream")

        if msg_type == "success":
            # Connection success or authentication success
            message_text = msg.get("msg", "")
            if "authenticated" in message_text.lower():
                self._is_authenticated = True
                self._log.info("WebSocket authenticated")
            else:
                self._log.debug(f"Success message: {message_text}")

        elif msg_type == "subscription":
            # Subscription confirmation
            self._log.debug(f"Subscription: {msg.get('msg')}")

        elif msg_type == "error":
            # Error message
            self._log.error(f"WebSocket error: {msg.get('msg')} (code: {msg.get('code')})")

        elif msg_type == "trade_updates":
            # Trade update message
            self._log.debug(f"Trade update: {msg}")
            self._handler(msg)

        else:
            # Unknown message type
            self._log.debug(f"Unknown message type '{msg_type}': {msg}")

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        while self._should_reconnect:
            delay = min(self._reconnect_delay, self._max_reconnect_delay)
            self._log.info(f"Reconnecting in {delay} seconds...")
            await asyncio.sleep(delay)

            try:
                self._log.info("Attempting to reconnect to WebSocket...")

                # Close existing connection if any
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None

                # Attempt reconnection
                self._ws = await websockets.connect(self._url)
                self._is_running = True
                self._task = asyncio.create_task(self._run())

                # Wait for connection confirmation
                await asyncio.sleep(0.5)

                # Authenticate
                await self._authenticate()

                # Wait for authentication
                await asyncio.sleep(0.5)

                # Resubscribe to trade updates
                await self.subscribe_trade_updates()

                self._log.info("WebSocket reconnected successfully")

                # Reset reconnection delay on success
                self._reconnect_delay = 1.0
                break

            except Exception as e:
                self._log.error(f"Reconnection failed: {e}")
                # Exponential backoff with jitter
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    async def subscribe_trade_updates(self) -> None:
        """Subscribe to trade updates."""
        if not self.is_connected:
            self._log.error("Cannot subscribe: WebSocket not connected")
            return

        subscribe_msg = {
            "action": "listen",
            "data": {
                "streams": ["trade_updates"],
            },
        }

        await self._send(subscribe_msg)
        self._log.info("Subscribed to trade updates")


class AlpacaMarketDataWebSocketClient(_AlpacaWebSocketClient):
    """
    WebSocket client for Alpaca market data streaming.

    Parameters
    ----------
    url : str
        The WebSocket URL.
    api_key : str
        The API key for authentication.
    api_secret : str
        The API secret for authentication.
    handler : Callable[[dict], None]
        The message handler callback.
    logger : Logger
        The logger for the client.

    """

    def __init__(
        self,
        paper:bool,
        feed:str,
        handler: Callable[[dict], None],
        logger: Logger,
    ) -> None:
        url = f"wss://stream.data.alpaca.markets/v2/{feed}"
        super().__init__(url,paper,handler,logger)
        self._subscriptions: dict[str, set[str]] = {
            "trades": set(),
            "quotes": set(),
            "bars": set(),
        }

    async def subscribe(
        self,
        trades: list[str] | None = None,
        quotes: list[str] | None = None,
        bars: list[str] | None = None,
    ) -> None:
        """
        Subscribe to market data streams.

        Parameters
        ----------
        trades : list[str], optional
            List of symbols to subscribe to for trades.
        quotes : list[str], optional
            List of symbols to subscribe to for quotes.
        bars : list[str], optional
            List of symbols to subscribe to for bars.

        """
        if not self.is_connected:
            self._log.error("Cannot subscribe: WebSocket not connected")
            return

        subscribe_msg: dict[str, Any] = {"action": "subscribe"}

        if trades:
            subscribe_msg["trades"] = trades
            self._subscriptions["trades"].update(trades)

        if quotes:
            subscribe_msg["quotes"] = quotes
            self._subscriptions["quotes"].update(quotes)

        if bars:
            subscribe_msg["bars"] = bars
            self._subscriptions["bars"].update(bars)

        await self._send(subscribe_msg)

    async def unsubscribe(
        self,
        trades: list[str] | None = None,
        quotes: list[str] | None = None,
        bars: list[str] | None = None,
    ) -> None:
        """
        Unsubscribe from market data streams.

        Parameters
        ----------
        trades : list[str], optional
            List of symbols to unsubscribe from for trades.
        quotes : list[str], optional
            List of symbols to unsubscribe from for quotes.
        bars : list[str], optional
            List of symbols to unsubscribe from for bars.

        """
        if not self.is_connected:
            self._log.error("Cannot unsubscribe: WebSocket not connected")
            return

        unsubscribe_msg: dict[str, Any] = {"action": "unsubscribe"}

        if trades:
            unsubscribe_msg["trades"] = trades
            self._subscriptions["trades"].difference_update(trades)
            self._log.info(f"Unsubscribing from trades: {trades}")

        if quotes:
            unsubscribe_msg["quotes"] = quotes
            self._subscriptions["quotes"].difference_update(quotes)
            self._log.info(f"Unsubscribing from quotes: {quotes}")

        if bars:
            unsubscribe_msg["bars"] = bars
            self._subscriptions["bars"].difference_update(bars)
            self._log.info(f"Unsubscribing from bars: {bars}")

        await self._send(unsubscribe_msg)

    async def _handle_single_message(self, msg: dict[str, Any]) -> None:
        """
        Handle a single WebSocket message.

        Parameters
        ----------
        msg : dict
            The message data.

        """
        msg_type = msg.get("T")

        if msg_type == "success":
            # Connection success or authentication success
            message_text = msg.get("msg", "")
            if "authenticated" in message_text.lower():
                self._is_authenticated = True
                self._log.info("Market data WebSocket authenticated")
            else:
                self._log.debug(f"Success message: {message_text}")

        elif msg_type == "subscription":
            # Subscription confirmation
            self._log.debug(f"Subscription: {msg.get('msg')}")

        elif msg_type == "error":
            # Error message
            error_msg = msg.get("msg", "Unknown error")
            error_code = msg.get("code", "N/A")
            self._log.error(f"Market data WebSocket error: {error_msg} (code: {error_code})")

        elif msg_type in ("t", "q", "b"):
            # Market data message: trade, quote, or bar
            self._handler(msg)

        else:
            # Unknown message type
            self._log.debug(f"Unknown market data message type '{msg_type}': {msg}")

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        while self._should_reconnect:
            delay = min(self._reconnect_delay, self._max_reconnect_delay)
            self._log.info(f"Reconnecting in {delay} seconds...")
            await asyncio.sleep(delay)

            try:
                self._log.info("Attempting to reconnect to market data WebSocket...")

                # Close existing connection if any
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None

                # Attempt reconnection
                self._ws = await websockets.connect(self._url)
                self._is_running = True
                self._task = asyncio.create_task(self._run())

                # Wait for connection confirmation
                await asyncio.sleep(0.5)

                # Authenticate
                await self._authenticate()

                # Wait for authentication
                await asyncio.sleep(0.5)

                # Resubscribe to all previous subscriptions
                if any(self._subscriptions.values()):
                    await self.subscribe(
                        trades=list(self._subscriptions["trades"]) if self._subscriptions["trades"] else None,
                        quotes=list(self._subscriptions["quotes"]) if self._subscriptions["quotes"] else None,
                        bars=list(self._subscriptions["bars"]) if self._subscriptions["bars"] else None,
                    )

                self._log.info("Market data WebSocket reconnected successfully")

                # Reset reconnection delay on success
                self._reconnect_delay = 1.0
                break

            except Exception as e:
                self._log.error(f"Reconnection failed: {e}")
                # Exponential backoff
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
