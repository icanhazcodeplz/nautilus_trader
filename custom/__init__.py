import os
from pathlib import Path

import pytz
from dotenv import load_dotenv

load_dotenv()

# REPO_ROOT = Path(__file__).parent.absolute()
BACKTEST_SYMBOL = "mss"

class ENV:
    # IBKR_ACCT = os.environ["IBKR_ACCT"]
    # IBKR_PAPER_USER = os.environ["IBKR_PAPER_USER"]
    # IBKR_PAPER_ACCT = os.environ["IBKR_PAPER_ACCT"]
    # ALPACA_KEY = os.environ["ALPACA_KEY"]
    # ALPACA_SECRET = os.environ["ALPACA_SECRET"]
    # ALPACA_PAPER_KEY = os.environ["ALPACA_PAPER_KEY"]
    # ALPACA_PAPER_SECRET = os.environ["ALPACA_PAPER_SECRET"]
    # OPERATOR_TZ = os.environ["OPERATOR_TZ"]
    # POLYGON_API_KEY = os.environ["POLYGON_API_KEY"]
    # STOCKNEWS_API_KEY = os.environ["STOCKNEWS_API_KEY"]
    # TRADESTATION_API_KEY = os.environ["TRADESTATION_API_KEY"]
    # TRADESTATION_API_SECRET = os.environ["TRADESTATION_API_SECRET"]
    # TRADESTATION_REFRESH_TOKEN = os.environ["TRADESTATION_REFRESH_TOKEN"]

    DATABENTO_API_KEY = os.environ["DATABENTO_API_KEY"]
    LIVE = os.environ["LIVE"].lower() == "true"
