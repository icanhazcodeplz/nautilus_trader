// -------------------------------------------------------------------------------------------------
//  Copyright (C) 2015-2025 Nautech Systems Pty Ltd. All rights reserved.
//  https://nautechsystems.io
//
//  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
//  You may not use this file except in compliance with the License.
//  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// -------------------------------------------------------------------------------------------------

//! Constants for the Alpaca adapter.

/// HTTP header name for API key ID.
pub const HEADER_API_KEY_ID: &str = "APCA-API-KEY-ID";

/// HTTP header name for API secret key.
pub const HEADER_API_SECRET: &str = "APCA-API-SECRET-KEY";

/// Alpaca venue identifier.
pub const VENUE_ALPACA: &str = "ALPACA";

/// Default heartbeat interval for WebSocket connections (seconds).
pub const DEFAULT_HEARTBEAT_INTERVAL_SECS: u64 = 20;

/// Default HTTP timeout (seconds).
pub const DEFAULT_HTTP_TIMEOUT_SECS: u64 = 30;

/// Default max retry attempts.
pub const DEFAULT_MAX_RETRIES: u32 = 3;

/// Default initial retry delay (milliseconds).
pub const DEFAULT_RETRY_DELAY_INITIAL_MS: u64 = 1_000;

/// Default maximum retry delay (milliseconds).
pub const DEFAULT_RETRY_DELAY_MAX_MS: u64 = 10_000;

/// Maximum number of symbols per WebSocket subscription batch.
pub const MAX_SYMBOLS_PER_SUBSCRIPTION: usize = 500;
