import pandas as pd

from custom.nt_extensions.tbbo_data import TBBOData

from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader import PACKAGE_ROOT
from nautilus_trader.persistence.catalog import ParquetDataCatalog

CATALOG_PATH = PACKAGE_ROOT / "catalog"
BACKTESTING_CATALOG = ParquetDataCatalog(CATALOG_PATH)


def get_catalog_data(symbol, start, end, data_cls, venue, identifiers=None):
    identifiers_str = f"{symbol}.{venue}"

    if identifiers is not None:
        identifiers_str = f"{identifiers_str}-{identifiers}"

    data_list = BACKTESTING_CATALOG.query(data_cls=data_cls, identifiers=[identifiers_str], start=start, end=end)
    if isinstance(data_cls, TBBOData):
        data_list = [d.data for d in data_list]
    return data_list

def load_catalog_data_to_engine(engine, symbol, start_str, end_str, data_venue="ALPACA"):
    test_instrument = TestInstrumentProvider.equity(symbol=symbol, venue=data_venue)
    engine.add_instrument(test_instrument)

    for data_cls in [QuoteTick, TradeTick]:
        engine.add_data(get_catalog_data(symbol, start_str, end_str, data_cls=data_cls, venue=data_venue))


    return test_instrument, engine


def check_catalog_data_available(symbol, day, venue="ALPACA"):
    """Check if trade_tick and quote_tick data exists for the given day.

    day: a date or tz-aware datetime (only the date portion is used).
    """
    identifier = f"{symbol.upper()}.{venue}"
    day_ts = pd.Timestamp(day).normalize()
    for data_cls in [TradeTick, QuoteTick]:
        first = BACKTESTING_CATALOG.query_first_timestamp(data_cls, identifier)
        last = BACKTESTING_CATALOG.query_last_timestamp(data_cls, identifier)
        if first is None or last is None:
            return False
        if not (first.normalize() <= day_ts <= last.normalize()):
            return False
    return True


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
