# Store ticks_and_metrics in less space

## Context
Every backtest and live run saves `ticks_and_metrics.pkl`: the strategy's `_tick_data_dicts`
(`custom/strategies/base.py:124`, saved at `:1260`), a dict keyed by `ts_event` of dicts with
`price`, `size`, `ts_event`, `ts_init`, `ts_clock` (a `pd.Timestamp`), `vwap_value`, `vwap_low`,
`vwap_high`, `rolling_vwap_value`, `direction_value`, plus a sparse `pnl` (written on each position
close, `base.py:832`) and `allow_trading` (`base.py:185`).

A 09:29–09:41 AMZN backtest writes 4.2 MB, nearly all of its artifacts folder (the rest is <10 KB).
Saving artifacts for every optimizer backtest (`optimize_multi_day.py`, currently 225 configs x 196
catalog days = 44,100 backtests) would take ~185 GB.

Decisions made:
- **Keep every field.** Dropping `price`/`size` (loadable from the catalog) only saves 7%, so it
  isn't worth the extra catalog read.
- **Size on disk matters more than read/write speed**, but reads should stay fast for the app.

## Measurements (2026-10-23)
Lossless: every format read back equal to the original (`pd.testing.assert_frame_equal`).

### One backtest (19,241 rows, 4.18 MB as now saved)
| Format | Size | Smaller | Read |
|---|---|---|---|
| pickle dict (now) | 4.18 MB | 1x | 22 ms |
| pickle dict + zstd-19 | 0.55 MB | 7.6x | 22 ms |
| pickle DataFrame | 1.70 MB | 2.5x | 0.1 ms |
| pickle DataFrame + zstd-19 | 0.40 MB | 10.4x | 1 ms |
| pickle DataFrame + xz-9 | 0.38 MB | 11.0x | 34 ms |
| Feather (Arrow IPC) zstd-22 | 0.51 MB | 8.2x | 0.6 ms |
| Parquet zstd-22, defaults | 0.73 MB | 5.7x | 0.9 ms |
| Parquet zstd-22, tuned (below) | 0.50 MB | 8.3x | 1 ms |
| Parquet tuned + offset/cents transform (below) | 0.44 MB | 9.6x | 2 ms |
| HDF5 (PyTables) blosc:zstd-9 | 0.53 MB | 7.9x | 7 ms |
| npz | 0.59 MB | 7.1x | 3 ms |
| DuckDB, one file per run | 1.06 MB | 3.9x | 5 ms |
| SQLite | 2.08 MB | 2.0x | 27 ms |

- **Tuned Parquet:** `pq.write_table(..., compression="zstd", compression_level=22,
  use_dictionary=False, column_encoding={int/timestamp cols: "DELTA_BINARY_PACKED", float cols:
  "BYTE_STREAM_SPLIT"})`, with the index written as a plain `ts` column.
- **Offset/cents transform** (lossless): `ts_event`, `ts_init` and `ts_clock` stored as offsets
  from the row's key (equal to it in ~93% of rows), price as integer cents (every price is exact to
  the cent). Extra code for another 13%.

### 8 configs on the same day (35.4 MB as now saved)
| Layout | Size | Smaller |
|---|---|---|
| Tuned Parquet, one file per run | 3.7 MB | 9.5x |
| DuckDB, one table sorted by (ts, run) | 3.4 MB | 10x |
| ArcticDB (LMDB), one symbol sorted by (ts, run) | 1.8 MB | 20x |
| Tuned Parquet, one file sorted by (ts, run) | 0.78 MB | 45x |

Reading one run back out of the shared (ts, run) Parquet file: 5 ms, exact.

Why sharing compresses so well: **ticks_and_metrics only depends on the indicator params.** Runs that
differed only in `entry_distance` were identical in every field. Runs with different
`rolling_vwap_window` differed only in `rolling_vwap_value`, plus the warm-up rows before the window
(one fewer than the largest indicator window, e.g. 2,999 rows for 3000). None of the 8 sample runs
traded, so none had `pnl` rows; a run that trades differs from its siblings in a few `pnl` rows.

### Not tested
TimescaleDB, QuestDB, InfluxDB, ClickHouse: servers to run and maintain, and their columnar codecs
(delta, Gorilla/XOR floats, zstd) are the same ideas as tuned Parquet, so similar sizes are expected.

## Plan

### 1. Save ticks_and_metrics as tuned Parquet
- `_ArtifactsIO.save_ticks_and_metrics`: build a DataFrame from the dict (`orient="index"`, index
  named `ts`) and write `ticks_and_metrics.parquet` with the tuned settings above.
- `load_ticks_and_metrics_file`: read the Parquet file if present, else fall back to the old `.pkl`,
  so existing runs keep loading. Decide whether it returns the DataFrame (and callers change) or
  converts back to the dict (no caller changes, but slower and loses the point of columns).
- Readers to update if it returns a DataFrame:
  - `app/app.py:214`
  - `custom/backtest_scripts/strategy_backtest_runner.py:122` (`replay_live_run`, uses `ticks.keys()`)
  - `custom/backtest_utils/alpaca_download.py:211`
  - `custom/backtest_scripts/run_latency_report.py:75`
  - `custom/analyze_latency_run.py:43` (already does `pd.DataFrame(...).T`)
  - `custom/app_utils/process_data_DEPRECATED.py:75` (deprecated; maybe leave)
- Sparse fields (`pnl`, `allow_trading`) become mostly-NaN columns; check the app treats NaN like a
  missing key.
- `ts_clock` is tz-aware UTC; check it round-trips (Parquet does; ArcticDB dropped the tz).
- Optional: the offset/cents transform for another ~13%. Probably not worth the code.
- Alternative if Parquet is rejected: pickled DataFrame + zstd-19 (0.40 MB, 1 ms read), at the cost
  of pickles that can break across pandas upgrades and are Python-only.

### 2. Optimizer artifacts (only if saving every backtest is wanted)
Preferred first: add `MODE = "save"` to `optimize_multi_day.py` that reruns a chosen `params_hash`
prefix (config rebuilt with `msgspec.json.decode(params_json, type=MomoStrategyConfig)`) with
`save_artifacts=True` on all or chosen days, with `PYTHONHASHSEED=0` like the workers. One config x
196 days is <1 GB even today.

If every backtest should keep artifacts:
- Content-addressed dedup: hash the dense tick/indicator columns, store each distinct dataset once
  per day, and keep per-run sparse rows (`pnl`, `allow_trading`) separately. The current grid has 5
  distinct datasets per day (one per `rolling_vwap_window`), so ~1 GB instead of ~185 GB.
- Or one shared tuned Parquet per day sorted by (ts, run): 45x, but all of a day's runs have to be
  written together, which fights the worker chunking (25 param sets of one day per chunk).
- Artifacts aren't linked from `studies.db`; locate them by `<SYMBOL>/<config_hash>/<window>`.
- Workers run with `bypass_logging=True`, so saved folders have no DEBUG log.

## Verification
- Round-trip test: save then load a `_tick_data_dicts` with `pnl` and `allow_trading` rows; the
  loaded data equals the original.
- Old `.pkl` runs still load in the app and in `replay_live_run`.
- A new backtest's `ticks_and_metrics.parquet` is ~0.5 MB for a 12-minute AMZN window (vs 4.2 MB).
- App renders a new backtest and an old live run the same as before.

## Benchmark scripts
In the session scratchpad (temporary):
`/private/tmp/claude-501/-Users-brent-code-nautilus-trader/3a23c857-c0a8-45bb-af95-0710c065860a/scratchpad/`
`bench_tick_storage.py` (all formats) and `make_tick_samples.py` (the 8 sample runs). Run in a
throwaway env so nothing is added to the project:
`uv run --no-project --python 3.13 --with duckdb --with arcticdb --with pandas --with pyarrow --with zstandard --with tables python bench_tick_storage.py <scratchpad> <out_dir>`
