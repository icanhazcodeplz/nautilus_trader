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

//! Alpaca API credential storage.

use std::fmt::Debug;

use ustr::Ustr;
use zeroize::ZeroizeOnDrop;

/// Alpaca API credentials.
///
/// Alpaca uses simple API key/secret authentication via headers.
/// Secrets are automatically zeroized on drop for security.
#[derive(Clone, ZeroizeOnDrop)]
pub struct Credential {
    #[zeroize(skip)]
    pub api_key: Ustr,
    pub api_secret: String,
}

impl Debug for Credential {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct(stringify!(Credential))
            .field("api_key", &self.api_key)
            .field("api_secret", &"<redacted>")
            .finish()
    }
}

impl Credential {
    /// Creates a new [`Credential`] instance for Alpaca API.
    #[must_use]
    pub fn new(api_key: String, api_secret: String) -> Self {
        Self {
            api_key: api_key.into(),
            api_secret,
        }
    }

    /// Returns the API key as a string slice.
    pub fn api_key(&self) -> &str {
        self.api_key.as_str()
    }

    /// Returns the API secret as a string slice.
    pub fn api_secret(&self) -> &str {
        &self.api_secret
    }
}

////////////////////////////////////////////////////////////////////////////////
// Tests
////////////////////////////////////////////////////////////////////////////////

#[cfg(test)]
mod tests {
    use rstest::rstest;

    use super::*;

    const API_KEY: &str = "PKTEST123456789";
    const API_SECRET: &str = "SecretKey123456789ABCDEF";

    #[rstest]
    fn test_credential_creation() {
        let credential = Credential::new(
            API_KEY.to_string(),
            API_SECRET.to_string(),
        );

        assert_eq!(credential.api_key(), API_KEY);
        assert_eq!(credential.api_secret(), API_SECRET);
    }

    #[rstest]
    fn test_debug_redacts_secret() {
        let credential = Credential::new(
            API_KEY.to_string(),
            API_SECRET.to_string(),
        );
        let dbg_out = format!("{:?}", credential);
        assert!(dbg_out.contains("api_secret: \"<redacted>\""));
        assert!(!dbg_out.contains(API_SECRET));
    }
}
