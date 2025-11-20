#!/usr/bin/env python3

from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from custom.utils import run_artifacts_subdir
from custom.utils.run_utils import run_strategy
from nautilus_trader.adapters.alpaca import ALPACA, AlpacaExecClientConfig, AlpacaDataClientConfig
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


symbol = "TSLA"
instrument_id = InstrumentId.from_str(f"{symbol}.{ALPACA}")
paper = True

instrument_provider_config = InstrumentProviderConfig(
    load_ids=frozenset([instrument_id]),
    # FIXME: figure out why all instruments are loaded when load_all=False
    load_all=False,
)
log_level = "INFO"
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
    exec_engine=LiveExecEngineConfig(reconciliation=False),
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


if __name__ == "__main__":
    run_config = {"paper": paper, "symbol": symbol, "strategy": strategy_config.dict()}
    strategy = MomoStrategy(config=strategy_config)
    performance_stats = run_strategy(strategy, node, artifacts_directory, run_config=run_config)
    print()
