import json
from pathlib import Path

_json_path = Path(__file__).parent / "catalog_options.json"
with open(_json_path) as f:
    CATALOG_OPTIONS = json.load(f)