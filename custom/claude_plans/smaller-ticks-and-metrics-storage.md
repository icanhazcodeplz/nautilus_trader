# Store backtest ticks_and_metrics in less space

## Context
Every backtest saves `ticks_and_metrics.pkl`: the strategy's `_tick_data_dicts`
(`custom/strategies/base.py:124`, saved in `on_dispose`, `:1258`), a dict keyed by nanosecond time
of dicts. Rows come from three places:
- Each tick (`_save_tick_data`, `base.py:515`), keyed by `ts_event` nudged +1 ns when ticks share a
  nanosecond: `price`, `size`, `ts_event`, plus each `metrics_to_save_on_tick` value (`vwap_value`,
  `vwap_low`, `vwap_high`, `rolling_vwap_value`, `direction_value` for Momo). Backtests no longer
  save `ts_init` / `ts_clock`; live runs still do.
- Sparse rows: `pnl` on each position close (`base.py:830`), `allow_trading` (`base.py:185`), and
  `metrics_to_save_on_1min` values on each bar (`base.py:689`).

`save_ticks_and_metrics` / `load_ticks_and_metrics_file` are now abstract on `_ArtifactsIO`
(`custom/artifacts.py:178`), implemented by `BacktestArtifactsIO` and `LiveArtifactsIO`.

**Scope: backtests only.** `LiveArtifactsIO` keeps writing and reading the pickle. Every reader
other than the app goes through `LiveArtifactsIO` and is untouched:
`replay_live_run` (`strategy_backtest_runner.py:126`), `download_matching_data_from_live_run`
(`alpaca_download.py:211`), `run_latency_report.py:75`, `analyze_latency_run.py:43`. The only
backtest reader is `app/app.py:302`, via `BacktestArtifactsIO`.

A 09:29–09:41 AMZN backtest averages 25k rows and 4.6 MB of pickle, nearly all of its artifacts
folder (the other files are ~15 KB). `optimize_multi_day.py` has `SAVE_ARTIFACTS = False`; turning
it on for the 225 configs x 196 days grid (44,100 backtests) would take ~200 GB as pickles.

Decisions made:
- **Keep every field.** Dropping `price`/`size` (loadable from the catalog) isn't worth the extra
  catalog read.
- **Size on disk matters more than read/write speed**, but reads should stay fast for the app.
- **Live runs stay pickles** for now.

## Measurements (2026-10-28)
20 AMZN backtests from `data/backtest_runs/AMZN/e2dc4368`, saved before the refactor, with
`ts_init` / `ts_clock` dropped so they match what a backtest saves now (25,093 rows/run, 77 of the
105 runs have `pnl` rows). Read times are per 20k rows.

### Lossless
Every format reads back equal to the original (`pd.testing.assert_frame_equal`, and the tuned
Parquet also back to the exact dict).

| Format | MB/run | Smaller | Read |
|---|---|---|---|
| pickle dict (now) | 4.60 | 1x | 12 ms |
| pickle dict + zstd-19 | 0.74 | 6.3x | 13 ms |
| pickle DataFrame | 2.03 | 2.3x | 0.2 ms |
| pickle DataFrame + zstd-19 | 0.62 | 7.5x | 1 ms |
| Feather (Arrow IPC) zstd-22 | 0.69 | 6.7x | 0.8 ms |
| Parquet zstd-22, defaults | 0.97 | 4.8x | 1 ms |
| Parquet zstd-22, tuned | 0.68 | 6.7x | 0.9 ms |
| Parquet tuned + offset/cents | 0.59 | 7.8x | 1.2 ms |

- **Tuned Parquet:** `pq.write_table(..., compression="zstd", compression_level=22,
  use_dictionary=False, column_encoding={int cols: "DELTA_BINARY_PACKED", float cols:
  "BYTE_STREAM_SPLIT"})`, with the dict's key written as a plain `ts` column.
- **Offset/cents transform:** `ts_event` stored as `ts - ts_event` (0 except for nudged ticks, so it
  costs ~nothing), `price` as integer cents (asserted exact on write).
- Converting the tuned Parquet back to the app's dict of dicts takes 26 ms per 20k rows.

Where the tuned Parquet bytes go: the four VWAP float columns are 70% (~120 KB each), then `ts` 9%,
`ts_event` 8%, `price` 8%, `size` 5%. Full float64 indicator values barely compress, so the
lossless formats all land at 7–8x.

### Lossy: narrower indicator columns
Tuned Parquet + offset/cents, with only the four VWAP columns changed (`price`, `size`, times,
`pnl` stay exact):

| VWAP columns stored as | MB/run | Smaller | Max abs error |
|---|---|---|---|
| float64 (lossless) | 0.59 | 7.8x | 0 |
| **float32** | **0.22** | **20.6x** | **$0.000015** |
| int of 1e-4 | 0.19 | 24.9x | $0.00005 |
| int of 1e-3 | 0.14 | 32.6x | $0.0005 |
| int of 1e-2 | 0.12 | 38.1x | $0.005 |

What each piece is worth with float32 VWAP columns (zstd-22 unless noted):

| Variant | MB/run | Smaller | Write |
|---|---|---|---|
| Parquet defaults | 0.56 | 8.2x | 64 ms |
| tuned | 0.32 | 14.5x | 82 ms |
| tuned + `ts_event` offset | 0.27 | 17.4x | 69 ms |
| tuned + offset + cents | 0.22 | 20.6x | 42 ms |
| tuned + offset + cents, zstd-3 | 0.24 | 19.6x | 5 ms |

Once the floats shrink, the transforms matter: each is about a fifth of what's left.

### Pitfall found
`pd.DataFrame.from_dict(d, orient="index")` turns an int column with any missing rows (every column,
once a `pnl` or `allow_trading` row exists) into float64, which rounds nanosecond timestamps
(`1779971045259729000` -> `...728896`). Build the DataFrame column by column with explicit dtypes
(nullable `Int64` for int columns).

### Many configs (measured 2026-10-23, before the refactor; not redone)
ticks_and_metrics only depends on the indicator params: runs that differed only in
`entry_distance` were identical in every field, and runs with different `rolling_vwap_window`
differed only in `rolling_vwap_value` and the warm-up rows. 8 configs on one day were 9.5x as one
tuned Parquet file per run, but 45x as one shared file sorted by (ts, run). The current grid has 5
distinct datasets per day (one per `rolling_vwap_window`).

### Not tested
DuckDB, SQLite, HDF5, npz, ArcticDB were all worse or no better than tuned Parquet in the 2026-10-23
round. TimescaleDB, QuestDB, InfluxDB, ClickHouse: servers to run, and their columnar codecs are the
same ideas as tuned Parquet.

## Plan

### 1. Save backtest ticks_and_metrics as tuned Parquet
In `BacktestArtifactsIO` (`custom/artifacts.py:195`); `LiveArtifactsIO` is unchanged.

`save_ticks_and_metrics(metrics)`, writing `ticks_and_metrics.parquet`:
- Build columns from the dict: collect every key across rows, then for each column the values in row
  order with `None` for missing. Columns whose values are all `int`/`bool` become `Int64`, the rest
  `float64` (see the pitfall above). Index: `ts` as `int64`.
- Transform: `ts_event` -> `ts - ts_event`; `price` -> integer cents, asserting the round trip is
  exact (fall back to float64 for that run if it isn't).
- **Decision: float32 for the indicator columns** (every float column except `price` and `pnl`)?
  0.22 MB/run (21x) instead of 0.59 MB (8x), with values off by at most $0.000015 at a $270 price
  (float32 is ~7 significant digits, so ~$0.00006 at $1,000). The app only charts these, and no
  backtest reader feeds them into calculations. Recommended.
- Write with the tuned settings and zstd-22 (42 ms; zstd-3 is 5 ms and 5% bigger).

`load_ticks_and_metrics_file()`:
- If `ticks_and_metrics.parquet` exists: read it, undo the transform, and return the same dict of
  dicts as before (int keys, each row without its missing fields), so `app.py` doesn't change. 26 ms
  per 20k rows, vs 12 ms for the pickle now.
- Else load `ticks_and_metrics.pkl`, so backtests saved before this keep loading. (Or delete them:
  they can be re-run.)
- Later, if the app is moved to DataFrames, return the DataFrame and skip the 26 ms conversion.

Aside, found while reading `app.py:318`: markers look up `str(m["time"])` in a dict whose keys are
ints, so they never merge into an existing tick row and always add a new row. Unchanged by this
plan, as long as the loader keeps int keys.

### 2. Optimizer artifacts
With step 1 (float32), one backtest's ticks_and_metrics is ~0.22 MB, plus ~15 KB of other files.
Saving every backtest of the grid would be ~11 GB in ~265k files, instead of ~200 GB, so turning
`SAVE_ARTIFACTS` on may now be acceptable as is.
- Artifacts aren't linked from `studies.db`; locate them by `<SYMBOL>/<config_hash>/<window>`.
- Workers run with `bypass_logging=True`, so saved folders have no DEBUG log.

If 11 GB is too much:
- Rerun on demand: a `MODE = "save"` that reruns a chosen `params_hash` prefix (config rebuilt with
  `msgspec.json.decode(params_json, type=MomoStrategyConfig)`) with `save_artifacts=True` on all or
  chosen days, with `PYTHONHASHSEED=0` like the workers.
- Content-addressed dedup: hash the dense tick/indicator columns, store each distinct dataset once
  per day, and keep per-run sparse rows (`pnl`, `allow_trading`) separately. ~5 datasets per day
  instead of 225.

## Verification
- Round-trip test: save then load a `_tick_data_dicts` with nudged keys, `pnl`, `allow_trading` and
  bar rows; the loaded dict equals the original exactly (and to float32 precision for the
  indicator columns, if chosen).
- Old backtest `.pkl` folders still load in the app.
- A new 12-minute AMZN backtest writes `ticks_and_metrics.parquet` of ~0.2 MB (float32) or ~0.6 MB
  (lossless).
- App renders a new backtest the same as before, and a live run is unaffected.

## Benchmark scripts
In the session scratchpad (temporary):
`/private/tmp/claude-501/-Users-brent-code-nautilus-trader/05ddae42-58d9-4c0f-b7f4-e0fbb5588b12/scratchpad/`
`bench_tick_storage.py` (lossless formats), `bench_lossy.py` (indicator precision),
`bench_simple.py` (float32 variants and write times), sharing `bench_tick_storage_lib.py`. Run in a
throwaway env so nothing is added to the project:
`uv run --no-project --python 3.13 --with pandas --with pyarrow --with zstandard --with pytz python -I bench_tick_storage.py <runs_dir> <out_dir> [n_runs]`
