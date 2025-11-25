from custom.backtest_utils import BACKTEST_SYMBOL
from custom.catalog_options import CATALOG_OPTIONS
from custom.nt_extensions.tbbo_data import TBBOData

from nautilus_trader import PACKAGE_ROOT
from nautilus_trader.adapters.databento import DATABENTO
from nautilus_trader.persistence.catalog import ParquetDataCatalog

CATALOG_PATH = PACKAGE_ROOT / "catalog"
BACKTESTING_CATALOG = ParquetDataCatalog(CATALOG_PATH)


def get_tbbo_for_viz(venue=DATABENTO):
    params = CATALOG_OPTIONS[BACKTEST_SYMBOL]
    tbbo = BACKTESTING_CATALOG.query(
        data_cls=TBBOData, identifiers=[f"{params['symbol']}.{venue}"], start=params["start"], end=params["end"]
    )
    return [t.data for t in tbbo]


def get_catalog_data(symbol, start, end, data_cls, venue, identifiers=None):
    identifiers_str = f"{symbol}.{venue}"

    if identifiers is not None:
        identifiers_str = f"{identifiers_str}-{identifiers}"

    data_list = BACKTESTING_CATALOG.query(data_cls=data_cls, identifiers=[identifiers_str], start=start, end=end)

    if isinstance(data_cls, TBBOData):
        data_list = [d.data for d in data_list]
    return data_list


# def _get_L3_order_book_delta():
#     # DEprecated
#     return BACKTESTING_CATALOG.query(
#         data_cls=OrderBookDelta,
#         identifiers=[f"{SYMBOL}.{VENUE}"],
#         start=START,
#         end=END
#     )
#
#
# def get_one_min_bars():
#     # deprecated
#     return BACKTESTING_CATALOG.query(
#         data_cls=Bar,
#         identifiers=[f"{SYMBOL}.{VENUE}-1-MINUTE-LAST-INTERNAL"],
#         start=START,
#         end=END
#     )
