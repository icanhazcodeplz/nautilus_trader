# MultipleStocks: run several Momo strategies in one live node

## Context
Today `custom/live_runner.py` runs one `MomoStrategy` per process. Running a second symbol means a
second process, which opens a second Alpaca market-data WebSocket, and the account only allows one.
`MultipleStocks` takes `{"AMZN": MomoStrategyConfig(...), "NVDA": ...}` and runs every strategy in
**one** `TradingNode`. Market data for all symbols then shares one socket: subscriptions are messages
on the data client's single `AlpacaMarketDataWebSocketClient` (`adapters/alpaca/data.py:144`, `:420`).

Decisions made:
- **Plain runner, not a nautilus Controller.** Strategies are added before `node.build()`. That
  avoids the Controller's JSON round trip of the configs and the manual exec-engine wiring.
- **The dict key fills in the symbol.** Configs leave out `instrument_id`.
- **One subfolder per symbol** for every report. Only node-wide files stay in `<run>/`.
- **`live_runner.py` switches to MultipleStocks.** A single-symbol run is a dict with one entry.
- **Fix live performance metrics.** On a live node nothing calls `analyzer.calculate_statistics`
  (only `BacktestEngine` does, `backtest/engine.pyx:2211`), so every live `performance_metrics.json`
  so far is zeros and `NaN`, e.g. `data/runs/20260306_105709`, which has 7 filled orders.

## Run folder layout
```
<run>/                         # data/{runs|paper_runs}/<RUN_START_TIME_AS_STR>/
  DEBUG*.log                   # node logs (one node)
  alpaca_trade_updates.json    # written by the exec client, all symbols
  order_submissions.json       # written by alpaca/http.py, all symbols
  AMZN/
    config.json  ticks_and_metrics.pkl  orders_report.pkl  performance_metrics.json
  NVDA/
    ...
```

## Changes

### 1. Make `instrument_id` optional: `custom/strategies/base.py`, `custom/strategies/momo.py`
- `BaseStrategyConfig`: add `kw_only=True`, and `instrument_id: InstrumentId | None = None`. Without
  `kw_only`, msgspec rejects required fields after an optional one; I checked this.
- `MomoStrategyConfig` (`momo.py:52`, which redeclares the field): same type and default.
- `BaseStrategy.__init__`: raise `ValueError` if `config.instrument_id is None`, so a config that
  never went through MultipleStocks fails at construction, not mid-run.
- While there: `print_update_every_secs: int = None` → `int | None = None` (a mistyped optional).

### 2. New `custom/managers/multiple_stocks.py`
```python
class MultipleStocks:
    def __init__(self, configs: dict[str, MomoStrategyConfig], paper: bool, log_level="INFO", file_log_level="DEBUG")
    def run(self) -> None
```
- **`__init__`:** for each `symbol, config`, uppercase the symbol and build
  `InstrumentId(f"{symbol}.{ALPACA}")`. Set it with
  `msgspec.structs.replace(config, instrument_id=iid, external_order_claims=[iid])` (works on these
  frozen configs; I checked). Raise if a config already has a different `instrument_id`.
  `order_id_tag` is left unset so the Trader numbers the strategies `000`, `001`, … itself
  (`trader.py:409`).
- **Live safety check:** moved from `live_runner.py:127-145`. When `not paper`, run the same blocking
  checks on every config, print each config under its symbol, and ask for confirmation once.
- **Node config:** a module-level `_node_config(instrument_ids, paper, run_dir, log levels)`. This is
  today's `live_runner.py` config, but with `load_ids`, `reconciliation_instrument_ids` and the
  instrument provider set to **all** symbols.
- **`run()`**, following what `run_strategy` does today for one strategy:
  1. Build the node and add the Alpaca data and exec client factories.
  2. Create one `AlpacaTraderHelper(paper=paper)` and call
     `cancel_orders_and_flatten_position_with_retry(symbol)` for each symbol.
  3. For each symbol: `MomoStrategy(config)`, then
     `strategy.initialize(artifacts_io=LiveArtifactsIO(run_dir, symbol), trader_helper=helper)`,
     then `node.trader.add_strategy(strategy)`.
  4. `node.build()`, then set `strategy._exec_engine = node.kernel.exec_engine` on each strategy.
  5. `try: node.run()` with the exception logging `run_strategy` has now.
     `finally:` flatten each symbol, write each symbol's artifacts (step 4), then `node.dispose()`.
     Per-strategy `ticks_and_metrics.pkl` is still written by each strategy's own `on_dispose`.
- **Behaviour to know:** an exception in one strategy shuts down the whole node
  (`graceful_shutdown_on_exception=True`), so every symbol stops and is flattened. That's the same
  safety stance as today.

### 3. Per-symbol reports and the live metrics fix: `custom/utils/run_utils.py`
- `live_performance_stats(cache, instrument_id) -> dict`: a new `PortfolioAnalyzer()` with the
  custom statistics registered, then `calculate_statistics(cache.account_for_venue(Venue(ALPACA)),
  cache.positions(instrument_id=instrument_id))`. Returns the pnls, returns and general stats merged,
  the same keys as now. `PnL%` is relative to the whole account balance, since the account is shared.
- Per-symbol orders report: `ReportProvider.generate_orders_report(cache.orders(instrument_id=iid))`.
- `register_custom_statistics(engine)` (`backtest_utils/backtest_run_utils.py:110`) changes to take an
  analyzer, so backtests and live register the same statistics. Update its backtest caller.
- `run_strategy` loses its `TradingNode` branch. MultipleStocks is now the only live path, so the
  branch would have no caller, and run_strategy becomes backtest-only. The metrics fix still covers
  single-symbol live runs, because those now go through MultipleStocks.

### 4. `LiveArtifactsIO` learns the per-symbol layout: `custom/artifacts.py`
- `LiveArtifactsIO(run_dir, symbol=None)`: `directory = run_dir / symbol`, or `run_dir` when `symbol`
  is `None`. The `None` case keeps runs made before this change loading.
- `_run_dt_str = run_dir.name`. This fixes the "Sloppy" TODO at `:37` for live runs; the trader ID
  used to load orders from Redis comes from the run folder, not the symbol folder.
- `load_alpaca_trade_updates`: read `run_dir / "alpaca_trade_updates.json"`, and keep only rows whose
  `symbol` matches when a symbol is given (each update carries `symbol`).
- `_load_order_udpates`: when a symbol is given, keep only orders whose `instrument_id.symbol` matches.

### 5. `custom/live_runner.py` becomes a thin config file
`symbol_configs = {"AMZN": MomoStrategyConfig(...)}` (today's params, without `instrument_id` and
`external_order_claims`), then `MultipleStocks(symbol_configs, paper=ENV.PAPER).run()`. The `sys.argv`
symbol is dropped; symbols come from the dict. The old `StrategyManager` in
`custom/managers/strategy_manager.py` is left untouched.

### 6. Analysis entry points take an optional symbol
Add a `symbol=None` argument and pass it to `LiveArtifactsIO`:
- `replay_live_run` in `custom/backtest_scripts/strategy_backtest_runner.py:131`
- `custom/backtest_scripts/run_latency_report.py:75`, `:119`
- `download_matching_data_from_live_run` in `custom/backtest_utils/alpaca_download.py:211`
- `app/app.py:24` (add a `symbol` variable next to the hardcoded run)

## Verification
- **Unit tests in `tests/custom/`:**
  - MultipleStocks fills `instrument_id` and `external_order_claims` from the key, uppercases it, and
    raises on a mismatched or missing `instrument_id`. The live blocking checks raise for, e.g.,
    `trade_size > 4`.
  - `LiveArtifactsIO(run_dir, "AMZN")` with a temp folder: the right paths, `_run_dt_str` is the run
    folder, and trade updates are filtered by symbol.
  - `MomoStrategyConfig()` without `instrument_id` makes `MomoStrategy(...)` raise.
- **Regression:** `uv run --active --no-sync pytest tests/custom -q`. The 6 aiohttp-timeout failures
  in `test_handle_ws_message.py` and `test_alpaca_http.py` were already failing. Also run one backtest
  through `strategy_backtest_runner.py`, since `run_strategy` and `register_custom_statistics` change.
- **Paper run with 2 symbols** (`ENV.PAPER`), during market hours:
  - The log shows "WebSocket market data streaming connected" **once**, and ticks arrive for both
    symbols.
  - `<run>/AMZN/` and `<run>/NVDA/` each get the four files.
  - `performance_metrics.json` has a real `PnL (total)` for any symbol that filled; check it against
    that symbol's fills in `orders_report.pkl`.
  - `LiveArtifactsIO(run_dir, "AMZN").get_run_data()` loads.
- **Single-symbol paper run** through the new `live_runner.py`, to confirm the one-entry case.
