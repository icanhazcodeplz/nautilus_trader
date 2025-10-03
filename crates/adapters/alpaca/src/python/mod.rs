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

//! Python bindings from `pyo3`.

pub mod enums;
pub mod http;
pub mod urls;
pub mod websocket;

use pyo3::prelude::*;

/// Loaded as `nautilus_pyo3.alpaca`.
#[pymodule]
pub fn alpaca(_: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<super::websocket::ALPACAWebSocketClient>()?;
    m.add_class::<super::websocket::messages::ALPACAWebSocketError>()?;
    m.add_class::<super::http::ALPACAHttpClient>()?;
    m.add_class::<crate::common::enums::ALPACAInstrumentType>()?;
    m.add_class::<crate::common::enums::ALPACAContractType>()?;
    m.add_class::<crate::common::enums::ALPACAMarginMode>()?;
    m.add_class::<crate::common::enums::ALPACATradeMode>()?;
    m.add_class::<crate::common::enums::ALPACAPositionMode>()?;
    m.add_class::<crate::common::enums::ALPACAVipLevel>()?;
    m.add_class::<crate::common::urls::ALPACAEndpointType>()?;
    m.add_function(wrap_pyfunction!(urls::get_alpaca_http_base_url, m)?)?;
    m.add_function(wrap_pyfunction!(urls::get_alpaca_ws_url_public, m)?)?;
    m.add_function(wrap_pyfunction!(urls::get_alpaca_ws_url_private, m)?)?;
    m.add_function(wrap_pyfunction!(urls::get_alpaca_ws_url_business, m)?)?;
    m.add_function(wrap_pyfunction!(urls::alpaca_requires_authentication, m)?)?;
    Ok(())
}
