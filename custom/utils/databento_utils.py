"""
To get example of creating nbbo
https://databento.com/docs/examples/equities/consolidated-bbo/example

"""

from pathlib import Path
import time

import databento as db
import pandas as pd

from nautilus_trader import ENV
from custom.nt_extensions.tbbo_data import TBBOData
from custom.utils import data_subdir
from custom.utils.load_catalog_data import BACKTESTING_CATALOG
from nautilus_trader.adapters.databento import DatabentoDataLoader, DATABENTO
from nautilus_trader.test_kit.providers import TestInstrumentProvider

# dataset = "EQUS.MINI"
ALL_EQUITY_DATASETS = [
    "XNAS.ITCH",  # Nasdaq
    "XBOS.ITCH",  # Nasdaq BX
    "XPSX.ITCH",  # Nasdaq PSX
    "XNYS.PILLAR",  # NYSE
    "ARCX.PILLAR",  # NYSE Arca
    "XASE.PILLAR",  # NYSE American
    "XCHI.PILLAR",  # NYSE Texas
    "XCIS.TRADESBBO",  # NYSE National
    "MEMX.MEMOIR",  # Members Exchange
    "EPRL.DOM",  # MIAX Pearl
    "IEXG.TOPS",  # IEX
    "BATS.PITCH",  # Cboe BZX
    "BATY.PITCH",  # Cboe BYX
    "EDGA.PITCH",  # Cboe EDGA
    "EDGX.PITCH",  # Cboe EDGX
]


class _DatabentoClient:
    def __init__(self):
        self.client = db.Historical(ENV.DATABENTO_API_KEY)

    def raw_file_path(self, symbol, schema, start_dt, end_dt, dataset):
        date_str = f"{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}"
        dataset_str = dataset.replace(".", "-").upper()
        symbol_str = symbol.upper()
        schema_str = schema.lower()
        filename = f"{date_str}-{symbol_str}-{schema_str}-{dataset_str}.dbn.zst"
        subdir = data_subdir("databento", date_str, symbol_str)
        return subdir / filename

    def check_data_cost(self, symbol, schema, start_dt, end_dt, dataset):
        params = dict(
            symbols=symbol,
            schema=schema,
            start=start_dt,
            end=end_dt,
            dataset=dataset,
            mode="historical-streaming",
        )

        params_copy = params.copy()
        params_copy.pop("path", None)
        cost = self.client.metadata.get_cost(**params_copy)
        return cost

    def get_range_and_save(self, symbol, schema, start_dt, end_dt, dataset):
        path = self.raw_file_path(symbol, schema, start_dt, end_dt, dataset)
        if Path(path).exists():
            print(f"File: {path} already exists. Loading as df")
            return self.load_dbn_as_df(symbol, schema, start_dt, end_dt, dataset)
        params = dict(
            symbols=symbol,
            schema=schema,
            start=start_dt,
            end=end_dt,
            dataset=dataset,
            path=path,
        )
        print(f"DataBento get_range request {params}")
        cost = self.check_data_cost(symbol, schema, start_dt, end_dt, dataset)
        print(f"Estimated cost: ${round(cost, 3)}")
        data = self.client.timeseries.get_range(**params)
        return data.to_df()

    def load_dbn_as_df(self, symbol, schema, start_dt, end_dt, dataset):
        path = self.raw_file_path(symbol, schema, start_dt, end_dt, dataset)
        if not Path(path).exists():
            raise FileNotFoundError(f"File does not exist: {path}")
        data = db.DBNStore.from_file(self.raw_file_path(symbol, schema, start_dt, end_dt, dataset))
        return data.to_df()

    def mbo_to_tbbo(self, mbo_df):
        # TODO
        pass

    def save_tbbo_catalog(self, tbbo_df):
        assert len(tbbo_df["symbol"].unique()) == 1
        symbol = tbbo_df["symbol"].unique()[0]

        tbbo_list = []
        instrument_id = self._instument_id(symbol)
        for index, row in tbbo_df.iterrows():
            ts_recv = int(index.timestamp() * 1e9)
            ts_event = int(row["ts_event"].timestamp() * 1e9)
            tbbo = TBBOData(
                instrument_id,
                ts_event,
                ts_recv,
                row["price"],
                row["size"],
                row["side"],
                row["bid_px_00"],
                row["ask_px_00"],
                row["bid_sz_00"],
                row["ask_sz_00"],
            )
            tbbo_list.append(tbbo)

        BACKTESTING_CATALOG.write_data(tbbo_list)

    def prepare_data(self, symbol, start_dt, end_dt):
        schema = "mbp-1"
        symbol = symbol.upper()
        loader = DatabentoDataLoader()
        all_data = []
        all_tbbo = []
        for dset in ALL_EQUITY_DATASETS:
            data_df = self.get_range_and_save(symbol, schema, start_dt, end_dt, dset)
            tbbo_df = data_df[data_df["action"] == "T"]
            all_tbbo.append(tbbo_df)

            data = loader.from_dbn_file(
                path=self.raw_file_path(symbol, schema, start_dt, end_dt, dset),
                instrument_id=self._instument_id(symbol),
                include_trades=True,
            )
            all_data.append(data)

        tbbo = pd.concat(all_tbbo)
        tbbo = tbbo.sort_index()
        self.save_tbbo_catalog(tbbo)

        # Merge and sort all_data lists by ts_init
        sorted_mbp_data = sorted([item for sublist in all_data for item in sublist], key=lambda x: x.ts_init)

        BACKTESTING_CATALOG.write_data(sorted_mbp_data)

    def _instument_id(self, symbol):
        return TestInstrumentProvider.equity(symbol=symbol, venue=DATABENTO).id


DatabentoClient = _DatabentoClient()

if __name__ == "__main__":
    """
    pulled already
    10/14 - JDZG
    10/14 - GWAV
    10/14 - NVA
    10/13 - CHNR
    9/19 - ZOOZ
    9/19 - AGMH
    """

    symbol = "CHNR"
    for start_dt in [
        pd.Timestamp("2025-10-13", tz="America/New_York"),
    ]:
        end_dt = start_dt + pd.Timedelta(days=1)

        # https://databento.com/docs/schemas-and-data-formats?historical=python&live=python&reference=python
        # schema = "trades"
        # schema = "tbbo"
        # schema = "mbo"
        # schema = "mbp-1"

        start_time = time.time()
        DatabentoClient.prepare_data(symbol, start_dt, end_dt)
        print(f"{time.time() - start_time:.2f} seconds")

# Available schemas for Level 2 data:
# - 'mbo': Market By Order (full order book with individual orders)
# - 'mbp-1': Market By Price (1 level of depth)
# - 'mbp-10': Market By Price (10 levels of depth)
# - 'tbbo': Top of Book Best Bid/Offer
# - 'trades': Trade data
# - 'ohlcv-1s': OHLCV bars (1 second intervals)
