from pathlib import Path

from custom.utils.load_catalog_data import BACKTESTING_CATALOG
from custom.utils import data_subdir
from custom.nt_extensions.tbbo_data import TBBOData
from custom import ENV
from nautilus_trader.adapters.databento import DatabentoDataLoader
from nautilus_trader.test_kit.providers import TestInstrumentProvider


import databento as db
import pandas as pd

VENUE = "SIM"
SYMBOL = "PAPL"


class _DatabentoClient:

    def __init__(self):
        self.client = db.Historical(ENV.DATABENTO_API_KEY)

    def raw_file_path(self, symbol, schema, start_dt, end_dt):
        filename_prefix = f"{symbol}_{schema.lower()}_{start_dt.strftime("%Y%m%d")}_{end_dt.strftime("%Y%m%d")}"
        return data_subdir("databento", f"{filename_prefix}.dbn.zst")

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
        print(f"Estimated cost: ${cost}")
        return cost

    def get_range_and_save(self, symbol, schema, start_dt, end_dt, dataset):
        path = self.raw_file_path(symbol, schema, start_dt, end_dt)
        if Path(path).exists():
            raise FileExistsError(f"File already exists: {path}")
        params = dict(
            symbols=symbol,
            schema=schema,
            start=start_dt,
            end=end_dt,
            dataset=dataset,
            path=path,
        )
        data = self.client.timeseries.get_range(**params)
        return data

    def load_data(self, symbol, schema, start_dt, end_dt):
        path = self.raw_file_path(symbol, schema, start_dt, end_dt)
        if not Path(path).exists():
            raise FileNotFoundError(f"File does not exist: {path}")
        data = db.DBNStore.from_file(self.raw_file_path(symbol, schema, start_dt, end_dt))
        return data.to_df()


    def mbo_to_tbbo(self, mbo_df):
        # TODO
        pass

    def save_to_catalog(self, mbo_df):
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
            tbbo = TBBOData(instrument_id, ts_event, ts_recv, row["price"], row["size"], row["side"], row["bid_px_00"],
                            row["ask_px_00"], row["bid_sz_00"], row["ask_sz_00"])
            tbbo_list.append(tbbo)

        BACKTESTING_CATALOG.write_data(tbbo_list)

    def load_and_save_to_catalog(self, symbol, schema, start_dt, end_dt):
        loader = DatabentoDataLoader()

        data = loader.from_dbn_file(path=self.raw_file_path(symbol, schema, start_dt, end_dt),
                                    instrument_id=self._instument_id(symbol), include_trades=True)
        BACKTESTING_CATALOG.write_data(data)

    def _instument_id(self, symbol):
        return TestInstrumentProvider.equity(symbol=symbol, venue="SIM").id


DatabentoClient = _DatabentoClient()


if __name__ == "__main__":
    """
    Candidates
    10/1 - PALI
    10/1 - LAC
    9/30 - LAC (after hours)
    9/30 - SPRC
    9/29 - POAI
    9/25 - SPRC
    9/25 - EVAX
    9/24 - TNFA
    9/24 - SHFS
    9/23 - SHFS (after hours)
    9/23 - FLD
    9/19 - ZOOZ
    9/19 - AGMH

    pulled already
    9/29 MSS
    """

    start_dt = pd.Timestamp("2025-09-29", tz="America/New_York")
    end_dt = start_dt + pd.Timedelta(days=1)
    symbol = "MSS"

    # https://databento.com/docs/schemas-and-data-formats?historical=python&live=python&reference=python
    # schema = "trades"
    schema = "tbbo"
    # schema = "mbo"
    schema="mbp-1"

    # dataset="XNAS.PILLAR" # NYSE (equities)
    dataset="XNAS.ITCH" # NASDAQ (equities)

    cost = DatabentoClient.check_data_cost(symbol, schema, start_dt, end_dt, dataset)
    # DatabentoClient.get_range_and_save(symbol, schema, start_dt, end_dt, dataset)
    df = DatabentoClient.load_data(symbol, schema, start_dt, end_dt)
    # DatabentoClient.save_tbbo_catalog(df)
    # dfs = df[['ts_event', 'action', 'side', 'depth', 'price', 'size', 'ts_in_delta', 'bid_px_00', 'ask_px_00', 'bid_sz_00', 'ask_sz_00', 'bid_ct_00', 'ask_ct_00']]
    df = df[df["action"] == "T"]
    DatabentoClient.save_tbbo_catalog(df)
    # DatabentoClient.load_and_save_to_catalog(symbol, schema, start_dt, end_dt)
    print()



# Available schemas for Level 2 data:
# - 'mbo': Market By Order (full order book with individual orders)
# - 'mbp-1': Market By Price (1 level of depth)
# - 'mbp-10': Market By Price (10 levels of depth)
# - 'tbbo': Top of Book Best Bid/Offer
# - 'trades': Trade data
# - 'ohlcv-1s': OHLCV bars (1 second intervals)



