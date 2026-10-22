import json

import pytest

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


def test_the_symbol_key_fills_in_instrument_id_and_external_order_claims(run_dir):
    runner = MultipleStocks({"amzn": _config(), "NVDA": _config()})

    assert list(runner.configs) == ["AMZN", "NVDA"]
    amzn = InstrumentId.from_str("AMZN.ALPACA")
    assert runner.configs["AMZN"].instrument_id == amzn
    assert runner.configs["AMZN"].external_order_claims == [amzn]
    assert runner.configs["NVDA"].instrument_id == InstrumentId.from_str("NVDA.ALPACA")


def test_a_config_whose_instrument_id_matches_its_key_is_accepted(run_dir):
    amzn = InstrumentId.from_str("AMZN.ALPACA")

    runner = MultipleStocks({"AMZN": _config(instrument_id=amzn)})

    assert runner.configs["AMZN"].instrument_id == amzn


def test_a_config_whose_instrument_id_contradicts_its_key_raises(run_dir):
    with pytest.raises(ValueError, match="instrument_id"):
        MultipleStocks({"AMZN": _config(instrument_id=InstrumentId.from_str("NVDA.ALPACA"))})


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


def test_a_strategy_without_an_instrument_id_raises_at_construction():
    with pytest.raises(ValueError, match="no instrument_id"):
        MomoStrategy(config=_config())


def test_live_artifacts_io_reads_a_symbol_folder_inside_the_run(tmp_path):
    run_dir = tmp_path / "20261021_092900"
    run_dir.mkdir()

    artifacts_io = LiveArtifactsIO(run_dir, "amzn")

    assert artifacts_io.directory == run_dir / "AMZN"
    assert artifacts_io.run_dir == run_dir
    # The Redis trader ID comes from the run folder, not the symbol folder
    assert artifacts_io._run_dt_str == "20261021_092900"


def test_live_artifacts_io_without_a_symbol_reads_the_run_folder(tmp_path):
    run_dir = tmp_path / "20260306_105709"
    run_dir.mkdir()

    artifacts_io = LiveArtifactsIO(run_dir)

    assert artifacts_io.directory == run_dir
    assert artifacts_io._run_dt_str == "20260306_105709"


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
    every_symbol = LiveArtifactsIO(run_dir).load_alpaca_trade_updates()

    assert list(amzn["id"]) == ["a1", "a2"]
    assert len(every_symbol) == 3


def test_trade_updates_for_a_symbol_with_none_return_none(tmp_path):
    run_dir = tmp_path / "20261021_092900"
    run_dir.mkdir()
    (run_dir / "alpaca_trade_updates.json").write_text(json.dumps([_trade_update("NVDA", "n1")]))

    assert LiveArtifactsIO(run_dir, "AMZN").load_alpaca_trade_updates() is None
