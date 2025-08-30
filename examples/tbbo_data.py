from examples.backtest_helpers import BACKTESTING_CATALOG
from examples.databento_utils import DatabentoClient
from nautilus_trader.core import Data

from nautilus_trader.model.custom import customdataclass
import pyarrow as pa

from nautilus_trader.model import InstrumentId
from nautilus_trader.test_kit.providers import TestInstrumentProvider

@customdataclass
class TBBOData(Data):
    _schema = pa.schema(
            {
                "instrument_id": pa.string(),
                "ts_event": pa.int64(),
                "ts_init": pa.int64(),
                "price": pa.float64(),
                "size": pa.int32(),
                "side": pa.string(),
                "bid": pa.float64(),
                "ask": pa.float64(),
                "bid_size": pa.int32(),
                "ask_size": pa.int32(),
            }
        )


    def __init__(
            self, instrument_id: InstrumentId,
            ts_event: int,
            ts_init: int,
            price: float,
            size: float,
            side: float,
            bid: float,
            ask: float,
            bid_size: float,
            ask_size: float,
    ) -> None:
        self.instrument_id = instrument_id
        self._ts_event = ts_event
        self._ts_init = ts_init
        self.price = price
        self.size = size
        self.side = side
        self.bid = bid
        self.ask = ask
        self.bid_size = bid_size
        self.ask_size = ask_size

    def to_dict(self):
        return {
            "instrument_id": self.instrument_id.value,
            "ts_event": self._ts_event,
            "ts_init": self._ts_init,
            "price": self.price,
            "size": self.size,
            "side": self.side,
            "bid": self.bid,
            "ask": self.ask,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
        }
    # def __repr__(self):
    #     return (f"GreeksData(ts_init={unix_nanos_to_iso8601(self._ts_init)}, instrument_id={self.instrument_id}, delta={self.delta:.2f})")

    # @classmethod
    # def from_dict(cls, data: dict):
    #     return TradeTickBBOData(InstrumentId.from_str(data["instrument_id"]), data["ts_event"], data["ts_init"], data["delta"])



if __name__ == "__main__":
    import pandas as pd

    start_dt = pd.Timestamp("2025-07-23")
    end_dt = pd.Timestamp("2025-07-24")
    symbol = "PAPL"
    schema = "tbbo"

    df = DatabentoClient.load_data(symbol, schema, start_dt, end_dt)
    tbbo_list = []
    instrument_id = TestInstrumentProvider.equity(symbol=symbol, venue="SIM").id
    for index, row in df.iterrows():
        ts_recv = int(index.timestamp() * 1e9)
        ts_event = int(row["ts_event"].timestamp() * 1e9)
        tbbo = TBBOData(instrument_id, ts_event, ts_recv, row["price"], row["size"], row["side"], row["bid_px_00"], row["ask_px_00"], row["bid_sz_00"], row["ask_sz_00"])
        tbbo_list.append(tbbo)

    BACKTESTING_CATALOG.write_data(tbbo_list)

    t = BACKTESTING_CATALOG.query(
        data_cls=TBBOData,
        identifiers=[f"{symbol}.SIM"],
        start=start_dt,
        end=end_dt
    )
    print(t[0].data.price)
    print()
