from pathlib import Path

import pandas as pd

from custom.backtest_utils.alpaca_download import prepare_alpaca_data
from custom.utils.paths import data_subdir

TOP_GAINERS_DIR = "/Users/brent/code/trading_dev/data/top_gainers"
TOP_GAINERS_CANDIDATES_FILE = data_subdir("top_gainers_candidates.txt")


def filter_top_gainers(top_gainers_df, rank_max, vol_30min_min, perc_gain_min, price_min, price_max):
    # Filter out below `rank_max`
    top_gainers_df = top_gainers_df.groupby("timestamp").head(rank_max)

    # Filter by price, volume, and gain thresholds
    candidates = top_gainers_df[
        (top_gainers_df["vol_30min"] >= vol_30min_min)
        & (top_gainers_df["perc_gain"] >= perc_gain_min)
        & (top_gainers_df["price"] >= price_min)
        & (top_gainers_df["price"] <= price_max)
    ]
    return candidates


def get_allow_trading_times_for_candidate(symbol, day_str, rank_max, vol_30min_min, perc_gain_min, price_min, price_max):
    top_gainers_df = pd.read_parquet(Path(TOP_GAINERS_DIR) / f"{day_str}.parquet")
    candidates = filter_top_gainers(top_gainers_df, rank_max, vol_30min_min, perc_gain_min, price_min, price_max)
    allow_trading_times = candidates[candidates["symbol"] == symbol]["timestamp"].dt.tz_convert("UTC")
    return allow_trading_times.to_list()


def make_top_gainers_candidates_txt_and_prepare_catalog(
    date_strs, rank_max, vol_30min_min, perc_gain_min, price_min, price_max
):
    """
    Return True if top_gainers_candidates.txt was edited. False if it remained the same.
    """
    all_candidates = []
    for date_str in date_strs:
        top_gainers_df = pd.read_parquet(Path(TOP_GAINERS_DIR) / f"{date_str}.parquet")
        candidates = filter_top_gainers(top_gainers_df, rank_max, vol_30min_min, perc_gain_min, price_min, price_max)
        symbols = candidates["symbol"].unique()
        day_in_question = candidates["timestamp"].min().normalize()
        for symbol in symbols:
            prepare_alpaca_data(symbol, day_in_question, force=False)
            all_candidates.append(f"{date_str}_{symbol}")

    return _write_top_gainers_candidates(all_candidates)


def _write_top_gainers_candidates(candidates: list[str]) -> bool:
    """
    Return True if file changed. False otherwise.
    """
    existing = read_top_gainers_candidates()
    with open(TOP_GAINERS_CANDIDATES_FILE, "w") as f:
        f.write("\n".join(candidates))
    return set(existing) != set(candidates)


def read_top_gainers_candidates() -> list[str]:
    with open(TOP_GAINERS_CANDIDATES_FILE, "r") as f:
        return f.read().strip().split("\n")


def parse_candidate_str(candidate_str: str) -> tuple[str, str]:
    """
    Return (symbol, date_str)
    """
    # saved as format 'YYYY-MM-DD_SYMBOL'
    return candidate_str.split("_")[1], candidate_str.split("_")[0]


if __name__ == "__main__":
    date_strs = [
        "2026-03-17",
        "2026-03-18",
        # "2026-03-19",
    ]

    make_top_gainers_candidates_txt_and_prepare_catalog(
        date_strs,
        price_min=1.0,
        price_max=20.0,
        vol_30min_min=100_000,
        perc_gain_min=30,
        rank_max=5,
    )
