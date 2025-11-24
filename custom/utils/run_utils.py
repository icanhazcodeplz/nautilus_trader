import traceback
from functools import wraps
from time import sleep

from custom.artifacts import ArtifactsIO
from nautilus_trader.backtest.engine import BacktestEngine

from custom.utils.alpaca_trader_http_client import AlpacaTraderHttpClient
from nautilus_trader.live.node import TradingNode
from nautilus_trader.common.component import Logger

log = Logger(name="run_utils")


def retry(max_retries: int = 3, wait_time: float = 1.0):
    """
    Decorator that retries a function if it raises an exception.

    Parameters
    ----------
    max_retries : int, default 3
        Maximum number of retry attempts.
    wait_time : float, default 1.0
        Time to wait (in seconds) between retries.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        log.warning(
                            f"Attempt {attempt + 1}/{max_retries} failed for {func.__name__}: {e}. "
                            f"Retrying in {wait_time}s..."
                        )
                        sleep(wait_time)
                    else:
                        log.error(f"All {max_retries + 1} attempts failed for {func.__name__}. Last error: {e}")
            raise last_exception

        return wrapper

    return decorator


def cancel_open_orders_and_close_position(client, symbol):
    """
    Cancels all open orders and closes any existing position for a specified symbol.

    This function interacts with a trading client to cancel all open orders and sell the
    existing position for the provided trading symbol. It ensures no leftover positions
    or unfulfilled orders remain, and attempts to sell existing positions at a reduced
    price if required.

    Returns:
    bool
        Returns True if there were open orders or positions that needed canceling or selling,
        and False otherwise.
    """
    canceling_or_selling_needed = False
    open_orders = client.get_orders(symbol=symbol, status="open")
    for open_order in open_orders:
        canceling_or_selling_needed = True
        log.warning(f"Cancelling existing open order for {symbol}: {open_order}")
        client.cancel_order(open_order.id)
    position = client.get_position(symbol)
    qty = int(position.qty) if position is not None else 0
    if qty > 0:
        canceling_or_selling_needed = True
        limit_price = round(float(position.current_price) * 0.90, 2)
        log.warning(f"Existing position for {symbol} of {qty}. Selling at {limit_price}")
        limit_sell_order = client.limit_order("sell", symbol, qty, price=limit_price)
        while limit_sell_order.status != "filled":
            # FIXME: Handle case when order never fills
            limit_sell_order = client.get_order(limit_sell_order.id)
            log.info(f"Waiting for sell order {limit_sell_order.id} to fill")
            sleep(0.2)
    return canceling_or_selling_needed


@retry(max_retries=3, wait_time=2.0)
def flatten_symbol(symbol, paper=True):
    # Wrapper to run multiple times until we get through `cancel_open_orders_and_close_position` without any operations
    client = AlpacaTraderHttpClient(paper=paper)
    iterations = 0
    while cancel_open_orders_and_close_position(client, symbol):
        iterations += 1
        sleep(1)
    log.info(f"{symbol} flat after {iterations} iterations of canceling orders and closing position")


def run_strategy(strategy, node_or_engine, artifacts_location=None, run_config=None, paper=True):
    strategy.initialize(artifacts_location=artifacts_location)
    if isinstance(node_or_engine, TradingNode):
        flatten_symbol(symbol=strategy.config.instrument_id.symbol.value, paper=paper)
        node_or_engine.build()
        node_or_engine.trader.add_strategy(strategy=strategy)
    elif isinstance(node_or_engine, BacktestEngine):
        node_or_engine.add_strategy(strategy=strategy)

    try:
        node_or_engine.run()
    except Exception as e:
        log.error(f"Exception during run: {e}")
        log.error(f"Traceback:\n{traceback.format_exc()}")
    finally:
        if isinstance(node_or_engine, TradingNode):
            flatten_symbol(symbol=strategy.config.instrument_id.symbol.value)
        orders_report = node_or_engine.trader.generate_orders_report()
        performance_stats = {
            **node_or_engine.portfolio.analyzer.get_performance_stats_pnls(),
            **node_or_engine.portfolio.analyzer.get_performance_stats_returns(),
            **node_or_engine.portfolio.analyzer.get_performance_stats_general(),
        }
        if artifacts_location is not None:
            artifacts_io = ArtifactsIO(artifacts_location)
            artifacts_io.save_orders_report(orders_report)
            artifacts_io.save_performance_metrics(performance_stats)
            if run_config is not None:
                artifacts_io.save_config(run_config)

            # "buy_signals": num_buy_sells,
            # "buy_signal_wins": long_wins,
            # "buys": total_buys,
            # "wins": wins,

        node_or_engine.dispose()
        return performance_stats
