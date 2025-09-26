# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2025 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
from statistics import mean
from typing import Any

from nautilus_trader.analysis.statistic import PortfolioStatistic
from nautilus_trader.model.position import Position
from nautilus_trader.model.events import OrderFilled


class AvgTradeScaled(PortfolioStatistic):

    def __init__(self, per_x_bought: int = 100):
        self.per_x_bought = per_x_bought

    def calculate_from_positions(self, positions: list[Position]) -> Any | None:
        # Preconditions
        if not positions:
            return None

        # Calculate statistic
        pnl_per_x_bought = []
        for pos in positions:
            pnl = pos.realized_pnl
            shares_bought = sum(e.last_qty for e in pos.events if isinstance(e, OrderFilled) and e.is_buy)
            pnl_per_share = pnl / shares_bought * self.per_x_bought
            pnl_per_x_bought.append(pnl_per_share)

        return round(float(mean(pnl_per_x_bought)), 2)
