import json

import pytest

from custom.artifacts import BacktestArtifactsIO
from custom.artifacts import LiveArtifactsIO
from custom.managers import multiple_stocks
from custom.managers.multiple_stocks import MultipleStocks
from custom.managers.multiple_stocks import live_blocking_reasons
from custom.strategies.momo import EntryStrategy
from custom.strategies.momo import ExitStrategy
from custom.strategies.momo import MomoStrategy
from custom.strategies.momo import MomoStrategyConfig
from nautilus_trader import ENV
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId


def _config(**overrides) -> MomoStrategyConfig:
    params = dict(
        trade_size=1,
        max_position_multiplier=1,
        stop_loss=100,
        take_profit=None,
        exit_strategy=ExitStrategy.SIMPLE_TAKE,
        entry_distance=30,
    )
    return MomoStrategyConfig(**(params | overrides))


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    # Keep MultipleStocks from making a real run folder under data/, and from asking to confirm a
    # live run when the shell has PAPER=false
    monkeypatch.setattr(multiple_stocks, "current_live_run_artifacts_dir", lambda: tmp_path)
    monkeypatch.setattr(ENV, "PAPER", True)
    return tmp_path


def test_symbol_keys_are_uppercased_and_the_configs_stay_symbol_free(run_dir):
    amzn_config = _config()

    runner = MultipleStocks({"amzn": amzn_config, "NVDA": _config()})

    assert list(runner.configs) == ["AMZN", "NVDA"]
    assert runner.configs["AMZN"] is amzn_config
    assert "instrument_id" not in amzn_config.dict()
    assert amzn_config.external_order_claims is None


def test_the_same_symbol_twice_in_different_case_raises(run_dir):
    with pytest.raises(ValueError, match="more than once"):
        MultipleStocks({"amzn": _config(), "AMZN": _config()})


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (dict(trade_size=5), "trade_size=5"),
        (dict(max_position_multiplier=2), "max_position_multiplier=2"),
        (dict(entry_strategy=EntryStrategy.RANDOM), "entry_strategy"),
    ],
)
def test_live_blocking_reasons(overrides, reason):
    reasons = live_blocking_reasons(_config(**overrides))

    assert len(reasons) == 1
    assert reason in reasons[0]


def test_a_live_safe_config_has_no_blocking_reasons():
    assert live_blocking_reasons(_config(trade_size=4)) == []


def test_a_strategy_trades_and_claims_the_instrument_of_its_symbol():
    config = _config()

    strategy = MomoStrategy(config=config, symbol="amzn")

    amzn = InstrumentId.from_str("AMZN.ALPACA")
    assert strategy.instrument_id == amzn
    assert strategy.external_order_claims == [amzn]
    # The claim goes on the strategy's own copy; the caller's config and its hash are untouched
    assert config.external_order_claims is None
    assert strategy.config.config_hash == config.config_hash


def test_live_artifacts_io_reads_a_symbol_folder_inside_the_run(tmp_path):
    run_dir = tmp_path / "20261021_092900"
    run_dir.mkdir()

    artifacts_io = LiveArtifactsIO(run_dir, "amzn")

    assert artifacts_io.directory == run_dir / "AMZN"
    assert artifacts_io.run_dir == run_dir


def test_trader_id_comes_from_run_info_even_after_the_folder_is_renamed(tmp_path):
    run_dir = tmp_path / "20261021_092900"
    run_dir.mkdir()
    LiveArtifactsIO.save_run_info(
        run_dir, trader_id=TraderId("T-20261021_092900"), run_start="20261021_092900", paper=True, symbols=["AMZN"]
    )
    renamed = run_dir.rename(tmp_path / "good_day_copy")

    artifacts_io = LiveArtifactsIO(renamed, "AMZN")

    assert artifacts_io.trader_id() == TraderId("T-20261021_092900")
    assert artifacts_io.load_run_info()["symbols"] == ["AMZN"]


def test_run_info_records_the_git_commit_and_branch(tmp_path):
    LiveArtifactsIO.save_run_info(
        tmp_path, trader_id=TraderId("T-20261021_092900"), run_start="20261021_092900", paper=True, symbols=["AMZN"]
    )

    run_info = LiveArtifactsIO(tmp_path, "AMZN").load_run_info()

    assert len(run_info["git_commit"]) == 40
    assert run_info["git_branch"]


def test_backtest_run_info_mirrors_the_live_one_without_paper_or_trader_id(tmp_path):
    artifacts_io = BacktestArtifactsIO(tmp_path)
    artifacts_io.save_run_info("amzn")

    run_info = artifacts_io.load_run_info()

    assert run_info.keys() == {"run_start", "symbol", "git_commit", "git_branch"}
    assert run_info["symbol"] == "AMZN"


def _trade_update(symbol, order_id):
    return {
        "msg_received_dt": "2026-10-21T13:30:00.000Z",
        "at": "2026-10-21T13:30:00.000Z",
        "timestamp": "2026-10-21T13:30:00.000Z",
        "filled_at": None,
        "event": "new",
        "side": "buy",
        "id": order_id,
        "client_order_id": f"O-{order_id}",
        "qty": "1",
        "filled_qty": "0",
        "status": "new",
        "limit_price": "100",
        "replaced_by": None,
        "replaces": None,
        "symbol": symbol,
    }


def test_trade_updates_are_read_from_the_run_folder_and_filtered_by_symbol(tmp_path):
    run_dir = tmp_path / "20261021_092900"
    run_dir.mkdir()
    updates = [_trade_update("AMZN", "a1"), _trade_update("NVDA", "n1"), _trade_update("AMZN", "a2")]
    (run_dir / "alpaca_trade_updates.json").write_text(json.dumps(updates))

    amzn = LiveArtifactsIO(run_dir, "AMZN").load_alpaca_trade_updates()

    assert list(amzn["id"]) == ["a1", "a2"]


def test_trade_updates_for_a_symbol_with_none_return_none(tmp_path):
    run_dir = tmp_path / "20261021_092900"
    run_dir.mkdir()
    (run_dir / "alpaca_trade_updates.json").write_text(json.dumps([_trade_update("NVDA", "n1")]))

    assert LiveArtifactsIO(run_dir, "AMZN").load_alpaca_trade_updates() is None
