from custom.optimize.optimize_multi_day import linspace_int
from custom.strategies.momo import DirectionStrategy, DirectionThreshold, ExitStrategy, EntryStrategy

SYMBOL = "AMZN"
FIXED_PARAMS = dict(
    allow_trades=True,
    max_position_multiplier=1,
    trade_size=100,
    band_multiplier=0.5,
    rolling_vwap_window=3000,
    band_window=150,
    band_variance_window=300,
    flip_side_confirm_ticks=150,
    exit_strategy=ExitStrategy.SIMPLE_TAKE,
    num_exit_tiers=1,
    start_trading_at="09:29:57",
    stop_entries_after="09:31",
    force_exit_at="09:35",
    entry_exclusion_band=None,
    entry_strategy=EntryStrategy.SIT_AT_DISTANCE,
    direction_threshold=DirectionThreshold.ROLLING_VWAP,
    opening_range_pred_pull=None,
)

PER_SET_GRID = dict(
    direction_strategy=[DirectionStrategy.SHORT_ONLY, DirectionStrategy.REVERSION],
    skip_same_side_reentry_after_take_profit=[True, False],
    stop_loss=[110, 120],
)

GRID = dict(
    take_profit=linspace_int(low=19, high=30, step=1),
    entry_distance=[29, 30, 31, 32, 33, 34, 35, 36],
)

LOOKBACK = 20
# For each combination in PER_SET_GRID, find the take_profit and entry_distance in the range of GRID
# that maximizes the chosen metric over the average of the chosen take_profit + entry_distance and
# the neighbors. Do not allow the edges of take_profit or entry_distance to be middle of a grid, so the
# averaged value should always have 9 items.
# Use the "best" combination over the previous LOOKBACK trading days as the parameters chosen for the
# following week. Do this once a week, so a given week usese the same parameters. Compare the
# resulting PnL, PnL per 100, and sharpe ratio with the average of all the possible parameters in this script, and
# with the parameters possible in this script that produce the highest PnL overall.
# For the metric, use PnL, PnL per 100, and sharpe ratio as the value we care about to average. Print results
# as dataframes.




