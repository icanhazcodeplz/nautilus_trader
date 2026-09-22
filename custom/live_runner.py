#!/usr/bin/env python3
import sys

from custom.strategies.momo import DirectionStrategy
from custom.strategies.momo import DirectionThreshold
from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.paths import run_artifacts_subdir, DT_STR
from custom.utils.run_utils import run_strategy
from nautilus_trader import ENV
from nautilus_trader.adapters.alpaca import ALPACA, AlpacaExecClientConfig, AlpacaDataClientConfig
from nautilus_trader.adapters.alpaca import AlpacaLiveDataClientFactory
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.common.config import DatabaseConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.config import LiveDataEngineConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId


symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "amzn".upper()
instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA}")
paper = ENV.PAPER

instrument_provider_config = InstrumentProviderConfig(load_ids=frozenset([instrument_id]), load_all=False)
log_level = "INFO"
# log_level = "DEBUG"
file_log_level = "DEBUG"

artifacts_directory = run_artifacts_subdir()
config_node = TradingNodeConfig(
    trader_id=TraderId(f"T-{DT_STR}"),  # FIXME: DO NOT REMOVE DT_STR, Is there a more robust way?
    logging=LoggingConfig(
        log_level=log_level,
        log_level_file=file_log_level,
        log_directory=str(artifacts_directory),
        log_file_name=file_log_level,
        use_pyo3=True,
        log_file_max_size=int(10e6),
        log_file_max_backup_count=50,
        log_component_levels={
            "nautilus_infrastructure::redis::cache": "INFO",
        },
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        reconciliation_lookback_mins=10,
        reconciliation_instrument_ids=[instrument_id],
        inflight_check_interval_ms=5000,
        reconciliation_startup_delay_secs=3.0,
        open_check_interval_secs=10,
        # purge_closed_orders_interval_mins=None,
        # purge_closed_positions_interval_mins=None,
        open_check_open_only=True,
        open_check_lookback_mins=5,  # TODO: Reduce this?
        open_check_threshold_ms=3000,
        graceful_shutdown_on_exception=True,
        allow_overfills=True,  # FIXME: Do we want this?
    ),
    cache=CacheConfig(
        database=DatabaseConfig(),
        encoding="msgpack",
        timestamps_as_iso8601=True,
        buffer_interval_ms=None,
        flush_on_start=False,
    ),
    data_clients={
        ALPACA: AlpacaDataClientConfig(
            paper=paper,
            feed="sip",  # 'iex' or 'sip'
            instrument_provider=instrument_provider_config,
        ),
    },
    exec_clients={
        ALPACA: AlpacaExecClientConfig(
            paper=paper,
            instrument_provider=instrument_provider_config,
        ),
    },
    data_engine=LiveDataEngineConfig(graceful_shutdown_on_exception=True),
    timeout_connection=60.0,
    timeout_reconciliation=20.0,
    timeout_portfolio=10.0,
    timeout_disconnection=5.0,
    timeout_post_stop=5.0,
)

node = TradingNode(config=config_node)

strategy_config = MomoStrategyConfig(
    instrument_id=instrument_id,
    external_order_claims=[instrument_id],
    allow_trades=True,
    max_position_multiplier=1,
    trade_size=50,
    stop_loss=3.0,
    take_profit=0.5,
    upper_scalar_multiplier=0.5,
    lower_scalar_multiplier=0.5,
    rolling_vwap_window=2000,
    vwap_window=150,
    variance_window=300,
    outer_band_multiplier=2.5,
    pressure_window=25,
    only_buy_if_macd_positive=False,
    direction_strategy=DirectionStrategy.REVERSION,
    direction_threshold=DirectionThreshold.ROLLING_VWAP,
    simple_take=True,
    trailing_take=False,
    num_exit_tiers=2,
    entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
    entry_distance=0.5,
    start_trading_at="09:29",
    stop_entries_after="09:35",
    entry_exclusion_band=None,
    print_update_every_secs=5,
)

if not paper:
    blocking = []
    if strategy_config.trade_size > 3:
        blocking.append(f"trade_size={strategy_config.trade_size} (max 3)")
    if strategy_config.max_position_multiplier > 1:
        blocking.append(f"max_position_multiplier={strategy_config.max_position_multiplier} (max 1)")
    if strategy_config.entry_strategy == EntryStrategy.RANDOM:
        blocking.append(f"entry_strategy={EntryStrategy.RANDOM}")
    if blocking:
        raise ValueError(f"Can't run with these params live: {', '.join(blocking)}")
    print("\n⚠️  You are running LIVE with:")
    for key, value in strategy_config.dict().items():
        if key in ("instrument_id", "oms_type", "external_order_claims"):
            continue
        print(f"  {key}: {value}")
    print(f"  Symbol: {symbol}")
    user_input = input("\nPress 'y' to continue, type any char to exit... ")
    if user_input.lower() != "y":
        raise SystemExit()

node.add_data_client_factory(ALPACA, AlpacaLiveDataClientFactory)
node.add_exec_client_factory(ALPACA, AlpacaLiveExecClientFactory)


if __name__ == "__main__":
    # place_orders_for_testing(paper=paper)
    run_config = strategy_config.dict()
    strategy = MomoStrategy(config=strategy_config)
    performance_stats = run_strategy(strategy, node, artifacts_directory, run_config=run_config, paper=paper)
    print()
