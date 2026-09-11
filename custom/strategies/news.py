from pathlib import Path
from typing import Optional

from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.alpaca_trader_http_client import AlpacaTraderHelper


class NewsStrategyConfig(MomoStrategyConfig, frozen=True, kw_only=True):
    # No news-specific fields yet. Kept as a distinct type so news_manager has a stable import
    # and so future news-only settings have a home.
    pass


class NewsStrategy(MomoStrategy):
    # FIXME: Deal with warm-up timing. Inheriting MomoStrategy registers VWAPBands, and
    # BaseStrategy.on_trade_tick gates _on_trade_tick behind indicators_initialized(). VWAPBands
    # only initializes once its adjustment_window (default 3000) ticks are filled, and a thin name
    # right after an article may not have 3000 historical ticks to backfill from. The "initial" buy
    # below is unaffected (it fires from the historical-load callback, which is not gated), but the
    # take logic lives in _on_trade_tick, so the position can sit without sell orders until enough
    # live ticks arrive. Either shrink adjustment_window for news or bypass the gate here.
    MIN_TICK_LOOKBACK = 100

    def __init__(self, config: NewsStrategyConfig) -> None:
        super().__init__(config)
        self.article_published_ns: int = 0  # Initialized in self.initialize

    def initialize(
        self,
        article_published_ns: int,
        artifacts_location: Optional[Path],
        trader_helper: Optional[AlpacaTraderHelper] = None,
    ):
        super().initialize(artifacts_location=artifacts_location, trader_helper=trader_helper)
        self.article_published_ns = article_published_ns

    def _on_historical_ticks_loaded(self, request_id) -> None:
        super()._on_historical_ticks_loaded(request_id)
        last_tick = self.cache.trade_tick(self.config.instrument_id)
        if last_tick is None:
            return
        self.buy(self.config.trade_size, last_tick.price, tag="initial", cancel_after_secs=10)
