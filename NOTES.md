# Set the library path for the Python interpreter (in this case Python 3.13.4)
export LD_LIBRARY_PATH="/Users/brent/.local/share/uv/python/cpython-3.13.6-macos-aarch64-none/bin/python3.13:$LD_LIBRARY_PATH"

# Set the Python executable path for PyO3
export PYO3_PYTHON=$(pwd)/.venv/bin/python

# After merging in develop branch
From root repo:
`make build`

# To rebuild cython only
- Comment out rust related lines in build.py line 500
- `python build.py` from root dir

# TODO
- Add integration tests
- Optuna optimization
- Move javascript to react project
- write mbo to tbbo converter
- Rethink plotting to get nanosecond resolution?
- Figure out why "positions" avg price is not the same as "orders" avg price
- Add ruff tool
- 