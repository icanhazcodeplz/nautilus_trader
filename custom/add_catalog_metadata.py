# Load the trades from each dataset in `catalog_options.json`, then add a field to each entry in catalog_options.json for
# `open`, which is the first price in the dataset, `max`, which is the highest price reached, and `close`, which is the final price in the dataset

import json

from custom.backtest_utils.load_catalog_data import get_catalog_data
from custom.catalog_options import write_json_single_line_entries
from custom.utils.paths import repo_path
from nautilus_trader.adapters.alpaca import ALPACA
from nautilus_trader.model.data import TradeTick

CATALOG_JSON_PATH = repo_path("custom", "catalog_options.json")


def add_metadata_to_catalog_options():
    with open(CATALOG_JSON_PATH) as f:
        catalog_options = json.load(f)

    for key, entry in catalog_options.items():
        symbol = entry["symbol"]
        start = entry["start"]
        end = entry["end"]

        trades = get_catalog_data(symbol, start, end, data_cls=TradeTick, venue=ALPACA)

        if not trades:
            print(f"No trades found for {key} ({symbol})")
            continue

        prices = [float(t.price) for t in trades]
        entry["open"] = round(prices[0], 1)
        entry["max"] = round(max(prices), 1)
        entry["close"] = round(prices[-1], 1)

        # Move notes to end
        notes = entry.pop("notes")
        entry["notes"] = notes

        print(f"{key}: open={entry['open']:.2f}, max={entry['max']:.2f}, close={entry['close']:.2f}")

    write_json_single_line_entries(catalog_options, CATALOG_JSON_PATH)
    print(f"\nUpdated {CATALOG_JSON_PATH}")


if __name__ == "__main__":
    add_metadata_to_catalog_options()
