import json
from pathlib import Path


def write_json_single_line_entries(data: dict, path: str):
    """Write JSON with each top-level entry on a single line."""
    lines = ["{"]
    items = sorted(data.items(), key=lambda x: x[0], reverse=True)
    for i, (key, value) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f'  "{key}": {json.dumps(value)}{comma}')
    lines.append("}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


_json_path = Path(__file__).parent / "catalog_options.json"
with open(_json_path) as f:
    CATALOG_OPTIONS = json.load(f)


def extract_dataset_name_info(dataset_name):
    catalog_params = CATALOG_OPTIONS[dataset_name.lower()]
    symbol = catalog_params["symbol"].upper()
    start_str = catalog_params["start"]
    end_str = catalog_params["end"]
    return symbol, start_str, end_str

