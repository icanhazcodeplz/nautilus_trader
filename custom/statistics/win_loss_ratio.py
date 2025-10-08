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

import pandas as pd

from nautilus_trader.analysis.statistic import PortfolioStatistic
from nautilus_trader.core.nautilus_pyo3 import AvgWinner, AvgLoser


class WinLossRatio(PortfolioStatistic):

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        avg_winner = AvgWinner().calculate_from_realized_pnls(realized_pnls)
        avg_loser = AvgLoser().calculate_from_realized_pnls(realized_pnls)
        if avg_loser == 0.0:
            return 0.0
        return -round(avg_winner/avg_loser, 2)
