from functools import wraps
from time import sleep

from nautilus_trader.common.component import Logger

log = Logger(name="retry")


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
