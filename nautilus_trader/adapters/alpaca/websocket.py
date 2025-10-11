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

"""WebSocket client for Alpaca trading updates."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from typing import Callable

import websockets

from nautilus_trader.common.component import Logger
from websockets import State


class AlpacaWebSocketClient:
    """
    WebSocket client for Alpaca trading updates.

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
        url: str,
        api_key: str,
        api_secret: str,
        handler: Callable[[dict], None],
        logger: Logger,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._api_secret = api_secret
        self._handler = handler
        self._log = logger
        self._ws = None
        self._task: asyncio.Task | None = None
        self._is_running = False
        self._is_authenticated = False

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

    async def _send(self, message: dict[str, Any]) -> None:
        """Send a message to the WebSocket."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        json_msg = json.dumps(message)
        await self._ws.send(json_msg)
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
        except Exception as e:
            self._log.error(f"WebSocket error: {e}")
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

        elif msg.get("data", {}).get("stream") == "trade_updates":
            # Alternative trade update format
            trade_data = msg.get("data", {})
            self._log.debug(f"Trade update: {trade_data}")
            self._handler(trade_data)

        else:
            # Unknown message type
            self._log.debug(f"Unknown message type '{msg_type}': {msg}")
