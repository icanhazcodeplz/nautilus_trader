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

"""Enumerations for the Alpaca adapter."""

from enum import Enum


class AlpacaOrderType(str, Enum):
    """Alpaca order types."""

    MARKET = "market"
    LIMIT = "limit"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"


class AlpacaTimeInForce(str, Enum):
    """Alpaca time in force options."""

    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    GTD = "gtd"
    OPG = "opg"
    CLS = "cls"


class AlpacaOrderStatus(str, Enum):
    """Alpaca order status values."""

    NEW = "new"
    ACCEPTED = "accepted"
    PENDING_NEW = "pending_new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    PENDING_CANCEL = "pending_cancel"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    STOPPED = "stopped"
    CANCEL_REJECTED = "cancel_rejected"
    PENDING_REPLACE = "pending_replace"
    REPLACED = "replaced"
    REPLACE_REJECTED = "replace_rejected"
    SUSPENDED = "suspended"
    DONE_FOR_DAY = "done_for_day"
