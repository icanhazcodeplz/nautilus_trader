from pathlib import Path

from examples.backtest_helpers import BACKTESTING_CATALOG, data_subdir
from examples.tbbo_data import TBBOData
from nautilus_trader import ENV
from nautilus_trader.adapters.databento import DatabentoDataLoader
from nautilus_trader.adapters.databento.data import DatabentoDataClient
from nautilus_trader.test_kit.providers import TestInstrumentProvider


import databento as db
import pandas as pd

VENUE = "SIM"
SYMBOL = "PAPL"


class _DatabentoClient:

    def __init__(self):
        self.client = db.Historical(ENV.DATABENTO_API_KEY)

    def raw_file_path(self, symbol, schema, start_dt, end_dt):
        filename_prefix = f"{symbol}_{schema}_{start_dt.strftime("%Y%m%d")}_{end_dt.strftime("%Y%m%d")}"
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
        instrument_id = TestInstrumentProvider.equity(symbol=symbol, venue="SIM").id
        for index, row in tbbo_df.iterrows():
            ts_recv = int(index.timestamp() * 1e9)
            ts_event = int(row["ts_event"].timestamp() * 1e9)
            tbbo = TBBOData(instrument_id, ts_event, ts_recv, row["price"], row["size"], row["side"], row["bid_px_00"],
                            row["ask_px_00"], row["bid_sz_00"], row["ask_sz_00"])
            tbbo_list.append(tbbo)

        BACKTESTING_CATALOG.write_data(tbbo_list)

DatabentoClient = _DatabentoClient()


if __name__ == "__main__":
    start_dt = pd.Timestamp("2025-07-23")
    end_dt = pd.Timestamp("2025-07-24")
    symbol = "PAPL"

    # https://databento.com/docs/schemas-and-data-formats?historical=python&live=python&reference=python
    # schema = "trades"
    schema = "tbbo"
    # schema = "mbo"
    # schema="MBP-10"

    # dataset="XNAS.PILLAR" # NYSE (equities)
    dataset="XNAS.ITCH" # NASDAQ (equities)

    cost = DatabentoClient.check_data_cost(symbol, schema, start_dt, end_dt, dataset)
    # DatabentoClient.get_range_and_save(symbol, schema, start_dt, end_dt, dataset)
    df = DatabentoClient.load_data(symbol, schema, start_dt, end_dt)
    DatabentoClient.save_tbbo_catalog(df)
    print()



    # df.to_parquet(data_subdir("databento", f"{filename_prefix}.parquet"))
    # df.to_csv(data_subdir("databento", f"{filename_prefix}.csv"))

    # df = pd.read_parquet(data_subdir("databento", f"{filename_prefix}.parquet"))

# Available schemas for Level 2 data:
# - 'mbo': Market By Order (full order book with individual orders)
# - 'mbp-1': Market By Price (1 level of depth)
# - 'mbp-10': Market By Price (10 levels of depth)
# - 'tbbo': Top of Book Best Bid/Offer
# - 'trades': Trade data
# - 'ohlcv-1s': OHLCV bars (1 second intervals)



# loader = DatabentoDataLoader()
#
# test_instrument = TestInstrumentProvider.equity(symbol=SYMBOL, venue=VENUE)
#
# data = loader.from_dbn_file(path=Path("data/databento" , "PAPL_20250723_mbo.dbn.zst"),
#                             instrument_id=test_instrument.id, include_trades=True)
# BACKTESTING_CATALOG.write_data(data)