## 1. Ensure my_main is up to date and running
git checkout my_main
git pull
uv sync
uv run python build.py
cp -rf /Users/brent/code/nautilus_trader/catalog /Users/brent/code/nautilus_trader_merge/

uv run python custom/backtest_scripts/strategy_backtest_runner.py

git checkout master
git pull

git checkout my_main
git merge master

# Resolve all conflicts, keeping code from my_main where possible

# Rebuild 
uv sync
uv run python build.py

# Test that code still works
uv run python custom/backtest_runner.py 

# Remove the following folders:
catalog
build
.venv
target


