#!/usr/bin/env python3
from custom.managers.multiple_stocks import MultipleStocks
from custom.strategies.momo import DirectionStrategy
from custom.strategies.momo import DirectionThreshold
from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import ExitStrategy
from custom.strategies.momo import MomoStrategyConfig


log_level = "INFO"
# log_level = "DEBUG"
file_log_level = "DEBUG"

# One strategy per symbol, all in one node. The key fills in each config's instrument_id.
symbol_configs = {
    "AMZN": MomoStrategyConfig(
        allow_trades=True,
        max_position_multiplier=1,
        trade_size=4,
        stop_loss=130,  # bps, ~$3.10 on AMZN
        take_profit=22,  # bps, ~$0.50 on AMZN
        band_multiplier=0.5,
        rolling_vwap_window=3000,
        band_window=150,
        band_variance_window=300,
        direction_strategy=DirectionStrategy.SHORT_ONLY,
        direction_threshold=DirectionThreshold.ROLLING_VWAP,
        exit_strategy=ExitStrategy.SIMPLE_TAKE,
        num_exit_tiers=1,
        entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
        entry_distance=33,  # bps, ~$0.50 on AMZN
        start_trading_at="09:29:57",
        stop_entries_after="09:31",
        force_exit_at="09:40",
        entry_exclusion_band=None,
        print_update_every_secs=5,
    ),
}


if __name__ == "__main__":
    MultipleStocks(symbol_configs, log_level=log_level, file_log_level=file_log_level).run()
