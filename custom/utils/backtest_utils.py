from custom.nt_extensions.tbbo_data import TBBOData
from nautilus_trader.model import OrderBookDelta, TradeTick, Bar, QuoteTick

from nautilus_trader import PACKAGE_ROOT
from nautilus_trader.persistence.catalog import ParquetDataCatalog

CATALOG_PATH = PACKAGE_ROOT / "catalog"
BACKTESTING_CATALOG = ParquetDataCatalog(CATALOG_PATH)
VENUE = "SIM"
SYMBOL = "PAPL"
START = "2025-07-23T00:00:00Z"
END = "2025-07-24T00:00:00Z"


def get_L3_order_book_delta():
    return BACKTESTING_CATALOG.query(
        data_cls=OrderBookDelta,
        identifiers=[f"{SYMBOL}.{VENUE}"],
        start=START,
        end=END
    )

def get_L1_quote_tick():
    return BACKTESTING_CATALOG.query(
        data_cls=QuoteTick,
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
    tbbo =  BACKTESTING_CATALOG.query(
        data_cls=TBBOData,
        identifiers=[f"{SYMBOL}.{VENUE}"],
        start=START,
        end=END
    )
    return [t.data for t in tbbo]

