# Set the library path for the Python interpreter (in this case Python 3.13.4)
export LD_LIBRARY_PATH="/Users/brent/.local/share/uv/python/cpython-3.13.6-macos-aarch64-none/bin/python3.13:$LD_LIBRARY_PATH"

# Set the Python executable path for PyO3
export PYO3_PYTHON=$(pwd)/.venv/bin/python

# Merging in develop branch from original repo
1. sync develop branch with upstream from github
2.
```bash
git checkout develop
git pull
git checkout dev
git merge develop
```
3. Merge conflicts using pycharm
4. commit and push local `dev` branch
5. 

From root repo:
`make build`

if failures, try
```bash
rusutup update
cargo clean
cargo check # THIS FAILED
```

To undo merge:
Find commit hash from github for the merge
`git revert -m 1 <commit_hash>`

Then rebuild to old environment
```bash
uv sync --all-extras
source .venv/bin/activate
python build.py
```


# To rebuild cython only
- Comment out rust related lines in build.py line 500
- `python build.py` from root dir

# TODO
- FIXME: Fills do not consider other fills in same interval. 
  - Forcibly combine orders if price the same?
  - Adjust core logic?
- Add integration tests
- Optuna optimization
- Move javascript to react project
- write mbo to tbbo converter
- Rethink plotting to get nanosecond resolution?
- Figure out why "positions" avg price is not the same as "orders" avg price
- Add ruff tool