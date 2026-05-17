//! `getProgramAccountsV2` — a bounded, incremental program-account scan.
//!
//! Vanilla `getProgramAccounts` returns *every* account a program owns; for a
//! large program (PumpSwap has 5M+ accounts) RPC providers reject the call.
//! `getProgramAccountsV2` is an RPC extension that adds cursor pagination and a
//! `changedSinceSlot` filter — only accounts changed at or after a given slot.
//! It is not in `solana-client`'s typed `RpcClient` API, so this module issues
//! it as a raw JSON-RPC request.
//!
//! The request builder and the response parser are pure and unit-tested; one
//! async function wires them to the live RPC endpoint.

use std::str::FromStr;

use base64::Engine;
use solana_client::rpc_request::RpcRequest;
use solana_sdk::pubkey::Pubkey;
use storm_core::{Result, StormError};

use crate::RpcContext;

/// One account returned by `getProgramAccountsV2`: its address and raw data.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProgramAccountV2 {
    /// The account's address.
    pub pubkey: Pubkey,
    /// The account's raw data bytes (decoded from the response's base64).
    pub data: Vec<u8>,
}

/// One page of a `getProgramAccountsV2` result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProgramAccountsV2Page {
    /// The accounts on this page.
    pub accounts: Vec<ProgramAccountV2>,
    /// The cursor for the next page, or `None` when this is the last page.
    pub pagination_key: Option<String>,
}

/// Build the JSON-RPC `params` array for a `getProgramAccountsV2` call.
///
/// * `program` — the program whose accounts to scan.
/// * `filters` — a JSON array of RPC filter objects (e.g. `dataSize`,
///   `memcmp`); built by the caller so this stays program-agnostic.
/// * `changed_since_slot` — only return accounts changed at or after this slot.
/// * `limit` — max accounts per page (the V2 maximum is 10000).
/// * `pagination_key` — the cursor from the previous page, or `None` for the
///   first page.
pub fn build_gpa_v2_params(
    program: &Pubkey,
    filters: &serde_json::Value,
    changed_since_slot: u64,
    limit: u64,
    pagination_key: Option<&str>,
) -> serde_json::Value {
    let mut config = serde_json::json!({
        "encoding": "base64",
        "filters": filters,
        "limit": limit,
        "changedSinceSlot": changed_since_slot,
    });
    if let Some(key) = pagination_key {
        config["paginationKey"] = serde_json::Value::String(key.to_string());
    }
    serde_json::json!([program.to_string(), config])
}

/// Parse the `result` of a `getProgramAccountsV2` response into a typed page.
///
/// Returns `Err(StormError::Parse)` if the JSON is missing the `accounts`
/// array, an account is missing its `pubkey` or base64 `data`, or a value
/// fails to decode.
pub fn parse_gpa_v2_response(result: &serde_json::Value) -> Result<ProgramAccountsV2Page> {
    let accounts_json = result
        .get("accounts")
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            StormError::Parse("getProgramAccountsV2: missing 'accounts' array".into())
        })?;

    let mut accounts = Vec::with_capacity(accounts_json.len());
    for item in accounts_json {
        let pubkey_str = item.get("pubkey").and_then(|v| v.as_str()).ok_or_else(|| {
            StormError::Parse("getProgramAccountsV2: account missing 'pubkey'".into())
        })?;
        let pubkey = Pubkey::from_str(pubkey_str).map_err(|e| {
            StormError::Parse(format!(
                "getProgramAccountsV2: bad pubkey '{pubkey_str}': {e}"
            ))
        })?;

        // Each account is the standard encoded-account envelope —
        // `{ "pubkey": ..., "account": { "data": [<base64>, "base64"] } }` —
        // verified against Helius's live `getProgramAccountsV2` response.
        let data_b64 = item
            .pointer("/account/data/0")
            .and_then(|v| v.as_str())
            .ok_or_else(|| {
                StormError::Parse(format!(
                    "getProgramAccountsV2: account {pubkey} missing base64 data"
                ))
            })?;
        let data = base64::engine::general_purpose::STANDARD
            .decode(data_b64)
            .map_err(|e| {
                StormError::Parse(format!(
                    "getProgramAccountsV2: account {pubkey} bad base64: {e}"
                ))
            })?;

        accounts.push(ProgramAccountV2 { pubkey, data });
    }

    // An absent or null `paginationKey` means the last page. A present-but-
    // non-string value is a malformed response — error rather than silently
    // truncating pagination, which would drop later pages of graduations.
    let pagination_key = match result.get("paginationKey") {
        None | Some(serde_json::Value::Null) => None,
        Some(serde_json::Value::String(s)) => Some(s.clone()),
        Some(other) => {
            return Err(StormError::Parse(format!(
                "getProgramAccountsV2: 'paginationKey' is not a string: {other}"
            )))
        }
    };

    Ok(ProgramAccountsV2Page {
        accounts,
        pagination_key,
    })
}

/// Fetch one page of `getProgramAccountsV2` from the RPC endpoint.
///
/// `getProgramAccountsV2` is an RPC extension, so it is issued via
/// `RpcRequest::Custom`. The caller drives pagination by passing the previous
/// page's `pagination_key` until it comes back `None`.
pub async fn fetch_program_accounts_v2_page(
    rpc: &RpcContext,
    program: &Pubkey,
    filters: &serde_json::Value,
    changed_since_slot: u64,
    limit: u64,
    pagination_key: Option<&str>,
) -> Result<ProgramAccountsV2Page> {
    let params = build_gpa_v2_params(program, filters, changed_since_slot, limit, pagination_key);
    let result: serde_json::Value = rpc
        .rpc()
        .send(
            RpcRequest::Custom {
                method: "getProgramAccountsV2",
            },
            params,
        )
        .await
        .map_err(|e| StormError::Rpc(format!("getProgramAccountsV2: {e}")))?;
    parse_gpa_v2_response(&result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_params_shape_first_page() {
        let program = Pubkey::new_unique();
        let filters = serde_json::json!([{ "dataSize": 301 }]);
        let params = build_gpa_v2_params(&program, &filters, 12345, 10000, None);

        let arr = params.as_array().expect("params is an array");
        assert_eq!(arr.len(), 2);
        assert_eq!(arr[0], serde_json::json!(program.to_string()));
        let cfg = &arr[1];
        assert_eq!(cfg["encoding"], "base64");
        assert_eq!(cfg["limit"], 10000);
        assert_eq!(cfg["changedSinceSlot"], 12345);
        assert_eq!(cfg["filters"], filters);
        // No cursor on the first page.
        assert!(cfg.get("paginationKey").is_none());
    }

    #[test]
    fn build_params_includes_cursor_on_later_pages() {
        let params = build_gpa_v2_params(
            &Pubkey::new_unique(),
            &serde_json::json!([]),
            1,
            10,
            Some("CURSOR123"),
        );
        assert_eq!(params[1]["paginationKey"], "CURSOR123");
    }

    #[test]
    fn parse_response_decodes_accounts_and_cursor() {
        let pk = Pubkey::new_unique();
        // base64 of the bytes [1, 2, 3] is "AQID".
        let resp = serde_json::json!({
            "accounts": [
                { "pubkey": pk.to_string(), "account": { "data": ["AQID", "base64"] } }
            ],
            "paginationKey": "NEXT",
            "count": 1
        });
        let page = parse_gpa_v2_response(&resp).unwrap();
        assert_eq!(page.accounts.len(), 1);
        assert_eq!(page.accounts[0].pubkey, pk);
        assert_eq!(page.accounts[0].data, vec![1, 2, 3]);
        assert_eq!(page.pagination_key, Some("NEXT".to_string()));
    }

    #[test]
    fn parse_response_without_cursor_is_the_last_page() {
        let resp = serde_json::json!({ "accounts": [], "count": 0 });
        let page = parse_gpa_v2_response(&resp).unwrap();
        assert!(page.accounts.is_empty());
        assert_eq!(page.pagination_key, None);
    }

    #[test]
    fn parse_response_missing_accounts_is_an_error() {
        let resp = serde_json::json!({ "paginationKey": "x" });
        match parse_gpa_v2_response(&resp) {
            Err(StormError::Parse(_)) => {}
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn parse_response_rejects_non_string_pagination_key() {
        // A present-but-non-string cursor must error, not silently truncate.
        let resp = serde_json::json!({ "accounts": [], "paginationKey": 42 });
        match parse_gpa_v2_response(&resp) {
            Err(StormError::Parse(_)) => {}
            other => panic!("expected Parse error, got {other:?}"),
        }
    }
}
