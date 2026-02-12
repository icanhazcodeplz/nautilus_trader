#!/usr/bin/env python3
from time import sleep

from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.paths import run_artifacts_subdir, DT_STR
from custom.utils.run_utils import run_strategy
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


symbol = "pets".upper()
instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA}")
paper = True

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
        log_file_max_size=int(5e6),
    ),
    exec_engine=LiveExecEngineConfig(
        reconciliation=True,
        reconciliation_lookback_mins=0,
        reconciliation_instrument_ids=[instrument_id],
        inflight_check_interval_ms=5000,
        reconciliation_startup_delay_secs=3.0,
        open_check_interval_secs=5,
        # purge_closed_orders_interval_mins=None,
        # purge_closed_positions_interval_mins=None,
        open_check_open_only=False,
        open_check_lookback_mins=10,  # TODO: Reduce this?
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
    trade_size=100,
    max_position_multiplier=10,
    stop_loss=1.0,
    take_profit=None,
    upper_scalar_multiplier=1.0,
    lower_scalar_multiplier=1.5,
    vwap_window=150,
    variance_window=300,
    outer_band_multiplier=3.0,
    pressure_window=100,
    trailing_buy_order=False,
    trailing_take=True,
    num_sell_tiers=3,
    random_buy=True,
    simple_take=False,
    allow_trades=True,
    print_update_every_secs=5,
    only_buy_if_macd_positive=False,
)
node.add_data_client_factory(ALPACA, AlpacaLiveDataClientFactory)
node.add_exec_client_factory(ALPACA, AlpacaLiveExecClientFactory)


def place_orders_for_testing(paper: bool = True):
    from custom.utils.alpaca_trader_http_client import AlpacaTraderHttpClient

    client = AlpacaTraderHttpClient(paper=paper)
    client.limit_order(side="buy", symbol=symbol, qty=1000, price=2.50)
    sleep(3)
    client.limit_order(side="sell", symbol=symbol, qty=100, price=3.00)
    sleep(1)


if __name__ == "__main__":
    # place_orders_for_testing(paper=paper)
    run_config = {"symbol": symbol, "strategy": strategy_config.dict()}
    strategy = MomoStrategy(config=strategy_config)
    performance_stats = run_strategy(strategy, node, artifacts_directory, run_config=run_config, paper=paper)
    print()
