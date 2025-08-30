from pathlib import Path

from examples.tbbo_data import TBBOData
from nautilus_trader.model import OrderBookDelta, TradeTick, Bar

from nautilus_trader import PACKAGE_ROOT
from nautilus_trader.persistence.catalog import ParquetDataCatalog

CATALOG_PATH = PACKAGE_ROOT / "catalog"
BACKTESTING_CATALOG = ParquetDataCatalog(CATALOG_PATH)
VENUE = "SIM"
SYMBOL = "PAPL"
START = "2025-07-23T00:00:00Z"
END = "2025-07-24T00:00:00Z"

def repo_path(*dirs):
    directory = Path(PACKAGE_ROOT)
    for dir in dirs:
        directory = Path(directory, dir)
        if isinstance(dir, str) and "." in dir:
            return directory
        if not directory.exists():
            directory.mkdir()
    return directory


def data_subdir(*dirs):
    return repo_path("data", *dirs)


def get_order_book_delta():
    return BACKTESTING_CATALOG.query(
        data_cls=OrderBookDelta,
        identifiers=[f"{SYMBOL}.{VENUE}"],
        start=START,
        end=END
    )


def get_trades():
    return BACKTESTING_CATALOG.query(
        data_cls=TradeTick,
        identifiers=[f"{SYMBOL}.{VENUE}"],
        start=START,
        end=END
    )

def get_one_min_bars():
    return BACKTESTING_CATALOG.query(
        data_cls=Bar,
        identifiers=[f"{SYMBOL}.{VENUE}-1-MINUTE-LAST-INTERNAL"],
        start=START,
        end=END
    )

def get_tbbo():
    t = BACKTESTING_CATALOG.query(
        data_cls=TBBOData,
        identifiers=[f"{SYMBOL}.{VENUE}"],
        start=START,
        end=END
    )
