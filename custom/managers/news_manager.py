from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
import pandas as pd

from custom.artifacts import BACKTEST_RUNS_PATH
from custom.strategies.momo import MomoStrategy, MomoStrategyConfig
from custom.strategies.news import NewsStrategyConfig, NewsStrategy
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.controller import Controller

if TYPE_CHECKING:
    from nautilus_trader.trading.trader import Trader


_PRIVATE_PLACEMENT = "private placement"

_DEFAULT_KWARGS: dict = dict(
    trade_size=10,
    max_position_multiplier=10,
    stop_loss=0.05,
    take_profit=None,
    random_buy=True,
    simple_take=True,
    allow_trades=True,
    print_update_every_secs=5,
)


class _NewsManagerConfig(ActorConfig, frozen=True):
    strategy_overrides: dict = {}


class NewsManagerConfig(_NewsManagerConfig, frozen=True, kw_only=True):
    paper: bool = True
    socket_path: str


class NewsManagerBacktestConfig(_NewsManagerConfig, frozen=True, kw_only=True):
    news_file: str


class _NewsManagerBase(Controller):
    """Shared news-event handling: filter, fetch last trade, buy, launch MomoStrategy."""

    def __init__(self, trader: Trader, config: _NewsManagerConfig) -> None:
        super().__init__(trader=trader, config=config)
        self._strategies: dict[str, MomoStrategy] = {}

    def initialize(self) -> None:
        pass

    def _handle_news_event(self, event: dict) -> None:
        headline = event.get("headline", "")
        if _PRIVATE_PLACEMENT not in headline.lower():
            return

        symbol = event["symbol"].upper()
        if symbol in self._strategies:
            self.log.info(f"Skipping news for {symbol}: strategy already running")
            return

        self._launch_strategy(symbol)

    def _build_strategy(self, symbol: str) -> tuple[MomoStrategy, "AlpacaTraderHelper | None"]:
        """Construct the MomoStrategy and its trader helper. Backtest overrides for no helper."""
        instrument_id = InstrumentId.from_str(f"{symbol}.ALPACA")
        kwargs = {**_DEFAULT_KWARGS, **self.config.strategy_overrides}
        config = MomoStrategyConfig(instrument_id=instrument_id, **kwargs)
        strategy = MomoStrategy(config=config)
        helper = AlpacaTraderHelper(paper=self.config.paper)
        return strategy, helper

    def _launch_strategy(self, symbol: str):
        strategy, helper = self._build_strategy(symbol)
        # FIXME: NOW backtest_runs_path needs new name and location
        strategy.initialize(artifacts_location=BACKTEST_RUNS_PATH, trader_helper=helper)

        # Trader.add_strategy creates a fresh clock at epoch 0; sync it before on_start()
        # runs, otherwise timers registered there will fire ~56 years of missed events
        # the next time the simulator advances this strategy's clock.
        self.create_strategy(strategy, start=False)
        strategy.clock.set_time(self.clock.timestamp_ns())
        self.start_strategy(strategy)
        self._strategies[symbol] = strategy
        self.log.info(f"Launched MomoStrategy for {symbol}")

    def _teardown_all(self) -> None:
        for symbol in list(self._strategies):
            strategy = self._strategies.pop(symbol)
            strategy.on_stop()
            strategy.on_dispose()
            self.remove_strategy(strategy)


class NewsManager(_NewsManagerBase):
    """Live news-trading controller. Listens on a Unix-domain socket for JSON headlines."""

    def __init__(self, trader: Trader, config: NewsManagerConfig) -> None:
        super().__init__(trader=trader, config=config)
        self._listener_task: asyncio.Task | None = None
        self._server: asyncio.AbstractServer | None = None
        self._decoder = msgspec.json.Decoder()

    def on_start(self) -> None:
        loop = asyncio.get_event_loop()
        self._listener_task = loop.create_task(self._listen())

    def on_stop(self) -> None:
        if self._listener_task is not None and not self._listener_task.done():
            self._listener_task.cancel()
        if self._server is not None:
            self._server.close()
        socket_path = self.config.socket_path
        if os.path.exists(socket_path):
            try:
                os.unlink(socket_path)
            except OSError:
                pass
        self._teardown_all()

    async def _listen(self) -> None:
        socket_path = self.config.socket_path
        if os.path.exists(socket_path):
            os.unlink(socket_path)

        self._server = await asyncio.start_unix_server(self._on_connection, path=socket_path)
        self.log.info(f"NewsManager listening on {socket_path}")
        async with self._server:
            await self._server.serve_forever()

    async def _on_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername") or "unknown"
        self.log.info(f"News producer connected: {peer}")
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    event = self._decoder.decode(line)
                except Exception as e:
                    self.log.exception(f"Failed to decode news payload: {line!r}", e)
                    continue
                try:
                    self._handle_news_event(event)
                except Exception as e:
                    self.log.exception(f"News event handler raised for {event!r}", e)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class NewsManagerBacktest(_NewsManagerBase):
    """Backtest replay: schedules news events from a JSON-lines file via clock alerts."""

    def __init__(self, trader: Trader, config: NewsManagerBacktestConfig) -> None:
        super().__init__(trader=trader, config=config)
        self._events_by_alert: dict[str, dict] = {}

    def on_start(self) -> None:
        events = self._load_events(self.config.news_file)
        now = self.clock.utc_now()
        for i, event in enumerate(events):
            received = pd.Timestamp(event["received"])
            if received.tzinfo is None:
                received = received.tz_localize("UTC")
            if received <= now:
                self._handle_news_event(event)
                continue
            alert_name = f"news_{i}"
            self._events_by_alert[alert_name] = event
            self.clock.set_time_alert(alert_name, received, self._on_news_alert)

    def on_stop(self) -> None:
        self._teardown_all()

    def _on_news_alert(self, event) -> None:
        news_event = self._events_by_alert.pop(event.name, None)
        if news_event is None:
            return
        self._handle_news_event(news_event)

    def _build_strategy(self, symbol: str) -> tuple[MomoStrategy, None]:
        instrument_id = InstrumentId.from_str(f"{symbol}.ALPACA")
        kwargs = {**_DEFAULT_KWARGS, **self.config.strategy_overrides}
        config = NewsStrategyConfig(instrument_id=instrument_id, **kwargs)
        strategy = NewsStrategy(config=config)
        return strategy, None

    @staticmethod
    def _load_events(path: str) -> list[dict]:
        decoder = msgspec.json.Decoder()
        events: list[dict] = []
        for raw in Path(path).read_text().splitlines():
            line = raw.strip()
            if not line:
                continue
            events.append(decoder.decode(line.encode()))
        events.sort(key=lambda e: e["received"])
        return events
