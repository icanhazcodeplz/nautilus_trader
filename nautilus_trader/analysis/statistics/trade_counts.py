from typing import Any

import pandas as pd

from nautilus_trader.analysis.statistic import PortfolioStatistic


class Winners(PortfolioStatistic):

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0.0
        winners = [x for x in realized_pnls if x > 0.0]
        return len(winners)

class Losers(PortfolioStatistic):

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0.0
        losers = [x for x in realized_pnls if x < 0.0]
        return len(losers)

class Scratches(PortfolioStatistic):

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0.0
        match = [x for x in realized_pnls if x == 0.0]
        return len(match)