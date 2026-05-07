from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.backtest.models import LatencyModel

latency_model = LatencyModel(
    base_latency_nanos=30 * 1e6,
    insert_latency_nanos=34 * 1e6,
    update_latency_nanos=25 * 1e6,
    cancel_latency_nanos=25 * 1e6,
)
# latency_model=LatencyModel()
prob_fill_on_limit = 0.5
DATA_VENUE = ALPACA
