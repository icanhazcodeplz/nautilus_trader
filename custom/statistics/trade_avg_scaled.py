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
from typing import Any

from nautilus_trader.analysis.statistic import PortfolioStatistic
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.position import Position


def _entry_fills(pos: Position) -> list[OrderFilled]:
    """
    The fills that OPENED or increased the position.

    Keyed on `pos.entry` (the order side of the fill that opened the position) rather than on
    BUY, so a short position reports its sells. The opposite side is the exit and must not be
    counted as size entered.
    """
    return [e for e in pos.events if isinstance(e, OrderFilled) and e.order_side == pos.entry]


class PnlPer100(PortfolioStatistic):
    def __init__(self, per_x_entered: int = 100):
        self.per_x_entered = per_x_entered

    def calculate_from_positions(self, positions: list[Position]) -> Any | None:
        if not positions:
            return None

        total_shares_entered = 0
        total_pnl = 0
        for pos in positions:
            total_pnl += pos.realized_pnl
            total_shares_entered += sum(e.last_qty for e in _entry_fills(pos))

        # A position that is still open can have no entry fills at all, so this is not dead code.
        if total_shares_entered == 0:
            return None

        return round(float(total_pnl / total_shares_entered) * self.per_x_entered, 3)


class TotalEntered(PortfolioStatistic):
    def calculate_from_positions(self, positions: list[Position]) -> Any | None:
        if not positions:
            return None

        total_shares_entered = 0
        for pos in positions:
            total_shares_entered += sum(e.last_qty for e in _entry_fills(pos))

        return int(total_shares_entered)


class AverageEntryPrice(PortfolioStatistic):
    """Fill-weighted average price of every entry fill across all positions."""

    def calculate_from_positions(self, positions: list[Position]) -> Any | None:
        if not positions:
            return None

        total_cost = 0.0
        total_shares_entered = 0.0
        for pos in positions:
            for e in _entry_fills(pos):
                total_cost += float(e.last_px) * float(e.last_qty)
                total_shares_entered += float(e.last_qty)

        if total_shares_entered == 0:
            return None

        return round(total_cost / total_shares_entered, 4)
