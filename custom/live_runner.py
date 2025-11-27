#!/usr/bin/env python3
from time import sleep

from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils.paths import run_artifacts_subdir
from custom.utils.run_utils import run_strategy
from custom.utils.alpaca_trader_http_client import AlpacaTraderHttpClient
from nautilus_trader.adapters.alpaca import ALPACA, AlpacaExecClientConfig, AlpacaDataClientConfig, AlpacaHttpClient
from nautilus_trader.adapters.alpaca import AlpacaLiveDataClientFactory
from nautilus_trader.adapters.alpaca import AlpacaLiveExecClientFactory
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveExecEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId


symbol = "foxx".upper()
instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA}")
paper = True

instrument_provider_config = InstrumentProviderConfig(
    load_ids=frozenset([instrument_id]),
    # FIXME: figure out why all instruments are loaded when load_all=False
    load_all=False,
)
log_level = "INFO"
log_level = "DEBUG"
artifacts_directory = run_artifacts_subdir()
config_node = TradingNodeConfig(
    trader_id=TraderId("TESTER-001"),
    logging=LoggingConfig(
        log_level=log_level,
        log_level_file=log_level,
        log_directory=str(artifacts_directory),
        log_file_name=log_level,
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
        purge_closed_orders_interval_mins=10,
        open_check_open_only=False,
        open_check_lookback_mins=10,  # TODO: Reduce this?
        open_check_threshold_ms=2000,
    ),
    cache=CacheConfig(
        # database=DatabaseConfig(),
        encoding="msgpack",
        timestamps_as_iso8601=True,
        buffer_interval_ms=None,
    ),
    data_clients={
        ALPACA: AlpacaDataClientConfig(
            paper=paper,
            feed="sip",  # 'iex' or 'sip' (SIP requires paid subscription)
            instrument_provider=instrument_provider_config,
        ),
    },
    exec_clients={
        ALPACA: AlpacaExecClientConfig(
            paper=paper,
            instrument_provider=instrument_provider_config,
        ),
    },
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
    trade_size=10,
    max_position_multiplier=1,
    stop_loss=0.20,
    take_profit=None,
    take_ratio=0.8,
    vwap_window=100,
    variance_window_ratio=1.5,
    upper_lower_scaler=0.01,
    trailing_buy_order=False,
    use_bracket_orders=False,
    use_oco_sell_orders=False,
    trailing_take=True,
    random_buy=False,
    simple_take=False,
    allow_trades=True,
)
node.add_data_client_factory(ALPACA, AlpacaLiveDataClientFactory)
node.add_exec_client_factory(ALPACA, AlpacaLiveExecClientFactory)


def place_orders_for_testing(paper: bool = True):
    client = AlpacaTraderHttpClient(paper=paper)
    symbol = "ENLV"
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
