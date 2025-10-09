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

//! HTTP client example for the Alpaca adapter.

use nautilus_alpaca::http::AlpacaHttpClient;

#[tokio::main]
async fn main() {
    println!("Alpaca HTTP Client Example");

    let client = AlpacaHttpClient::new(
        "https://paper-api.alpaca.markets".to_string(),
        Some(30),
        None,
        None,
        None,
    )
    .expect("Failed to create client");

    println!("Client created successfully!");
    println!("Base URL: {}", client.base_url());
    println!("Has credentials: {}", client.has_credentials());
}
