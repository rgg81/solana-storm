# storm-collector Discovery Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `storm-collector`'s broken `getProgramAccounts` graduation discovery with a bounded, incremental `getProgramAccountsV2` + `changedSinceSlot` poll that works on the Helius free tier.

**Architecture:** A new `gpa_v2` module in `storm-solana` issues raw `getProgramAccountsV2` JSON-RPC calls (the method is not in `solana-client`'s typed `RpcClient` API) and exposes pure, unit-tested request-building and response-parsing plus a thin async page-fetch wrapper. `storm-collector`'s `discover.rs` rewrites `discover_graduations` as a cursor-paginated loop over that helper, keeping its existing pure pool validator untouched. `cycle.rs`'s discover phase reads and writes a `last_discovery_slot` cursor in the existing `collector_state` table. No schema change.

**Tech Stack:** Rust, `solana-client` 2 (`RpcClient::send` + `RpcRequest::Custom` for the V2 extension method), `serde_json`, `base64`, `storm-core`, `storm-pumpfun`, `storm-solana`, `storm-store`, `tokio`, `tracing`.

---

## Context

This plan implements `docs/superpowers/specs/2026-05-18-discovery-redesign-design.md` — read it first, especially §3 (the approach decision) and §4 (the design).

The `storm-collector` daemon (Phase 1, merged to `main`) discovers newly-graduated pump.fun tokens, snapshots their features, and records outcomes. A `--once` smoke-test against the Helius free tier showed the **discover phase fails**: it issued one `getProgramAccounts` call against the PumpSwap program, which Helius rejects — the program has 5M+ accounts and the `DataSize`/`index` filters barely narrow that.

The fix, **verified by a probe against the live free tier**, is `getProgramAccountsV2` with a `changedSinceSlot` cursor: each cycle scans only the PumpSwap pools that changed since the previous cycle's slot, paginating with a cursor. The probe returned 67 changed index-0 pools (plus a next-page cursor) for a ~30-minute window — bounded, incremental, same Helius endpoint, no new infrastructure.

Only the discover phase is affected. The schema, snapshot phase, outcome phase, daemon loop, backoff, `--once`, and shutdown are sound and untouched.

## Notes for the executor

- If `cargo` is not found, run `. "$HOME/.cargo/env"` first.
- The repo CI runs four gates; **every commit must keep all four green**: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo check --workspace --all-targets`, `cargo test --workspace`.
- `cargo test` must NOT require network. The pure request-builder and response-parser get real unit tests; the one live-RPC path stays in the `#[ignore]`-d `bins/storm-collector/tests/integration.rs`.
- **The one external-API risk — verify it in Task 1.** `getProgramAccountsV2` is an RPC extension, not a typed `solana-client` method. This plan issues it via `RpcClient::send(RpcRequest::Custom { method: "getProgramAccountsV2" }, params)`. `RpcClient::send` and the `RpcRequest::Custom { method: &'static str }` variant should both be public in `solana-client` 2.x. If the resolved version differs (e.g. `send` is private), the fallback is: add an `rpc_url: String` field to `RpcContext` and issue the call with `reqwest` (a `reqwest` POST of a JSON-RPC body — `storm-solana` already depends on `reqwest`). Report the choice if you take the fallback.
- After capturing fixtures or running anything network-bound: `cargo test` and CI must still pass offline.
- Follow the existing style: `StormError::Rpc(format!("…: {e}"))` for RPC errors, `StormError::Parse` for malformed data, doc-comments on public items.

## File structure

| Path | Change | Responsibility |
|---|---|---|
| `crates/storm-solana/src/gpa_v2.rs` | Create | `getProgramAccountsV2` types, pure request/response helpers, async page fetch |
| `crates/storm-solana/src/lib.rs` | Modify | register the `gpa_v2` module + re-export its public items |
| `bins/storm-collector/Cargo.toml` | Modify | add the `serde_json` dependency |
| `bins/storm-collector/src/discover.rs` | Rewrite | `discover_graduations` becomes a V2 cursor-paginated scan; the pure validator is kept |
| `bins/storm-collector/src/cycle.rs` | Modify | `discover_phase` reads/writes the `last_discovery_slot` cursor in `collector_state` |
| `bins/storm-collector/tests/integration.rs` | Rewrite | the live `#[ignore]`-d test issues the V2 call |
| `bins/storm-collector/src/main.rs` | Modify | redact the API key from the startup log line |

No schema migration — the `collector_state` table already exists.

---

### Task 1: `gpa_v2` module in `storm-solana`

**Files:**
- Create: `crates/storm-solana/src/gpa_v2.rs`
- Modify: `crates/storm-solana/src/lib.rs`
- Test: in `crates/storm-solana/src/gpa_v2.rs`

A new module that turns the `getProgramAccountsV2` RPC extension into a typed,
testable helper: a pure request-builder, a pure response-parser (both
unit-tested), and one thin async function that issues the call.

- [ ] **Step 1: Create `crates/storm-solana/src/gpa_v2.rs`**

```rust
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
        let pubkey_str = item
            .get("pubkey")
            .and_then(|v| v.as_str())
            .ok_or_else(|| {
                StormError::Parse("getProgramAccountsV2: account missing 'pubkey'".into())
            })?;
        let pubkey = Pubkey::from_str(pubkey_str).map_err(|e| {
            StormError::Parse(format!("getProgramAccountsV2: bad pubkey '{pubkey_str}': {e}"))
        })?;

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
                StormError::Parse(format!("getProgramAccountsV2: account {pubkey} bad base64: {e}"))
            })?;

        accounts.push(ProgramAccountV2 { pubkey, data });
    }

    let pagination_key = result
        .get("paginationKey")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());

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
}
```

- [ ] **Step 2: Verify the `RpcClient::send` API compiles**

Run: `cargo check -p storm-solana 2>&1 | tail -20`

`gpa_v2.rs` is not yet a registered module, so this still compiles the rest of
the crate — that is fine. The real check happens in Step 4. But first confirm
the API assumption: `RpcClient::send` and `RpcRequest::Custom { method }` must
exist and be public in the resolved `solana-client`. If Step 4 fails to compile
on either, apply the `reqwest` fallback from the executor notes and report it.

- [ ] **Step 3: Register the module in `crates/storm-solana/src/lib.rs`**

The current `lib.rs` is:

```rust
pub mod accounts;
pub mod pools;
pub mod rpc;
pub mod ws;

pub use accounts::{MintInfo, PortfolioEntry, TokenAccountSnapshot};
pub use pools::{
    sqrt_price_x64_to_price, DexPool, OrcaWhirlpool, RaydiumPool, RaydiumPoolState, WhirlpoolState,
    ORCA_WHIRLPOOL_PROGRAM_ID, RAYDIUM_AMM_V4_PROGRAM_ID,
};
pub use rpc::{AccountSnapshot, RpcContext};
pub use ws::{subscribe_accounts, AccountUpdate};

// Feature-unification anchor: see workspace Cargo.toml.
use reqwest as _;
```

Add `pub mod gpa_v2;` to the module list and a re-export line. The result:

```rust
pub mod accounts;
pub mod gpa_v2;
pub mod pools;
pub mod rpc;
pub mod ws;

pub use accounts::{MintInfo, PortfolioEntry, TokenAccountSnapshot};
pub use gpa_v2::{
    build_gpa_v2_params, fetch_program_accounts_v2_page, parse_gpa_v2_response, ProgramAccountV2,
    ProgramAccountsV2Page,
};
pub use pools::{
    sqrt_price_x64_to_price, DexPool, OrcaWhirlpool, RaydiumPool, RaydiumPoolState, WhirlpoolState,
    ORCA_WHIRLPOOL_PROGRAM_ID, RAYDIUM_AMM_V4_PROGRAM_ID,
};
pub use rpc::{AccountSnapshot, RpcContext};
pub use ws::{subscribe_accounts, AccountUpdate};

// Feature-unification anchor: see workspace Cargo.toml.
use reqwest as _;
```

- [ ] **Step 4: Run the tests**

Run: `cargo test -p storm-solana gpa_v2`
Expected: PASS — all five tests (`build_params_shape_first_page`,
`build_params_includes_cursor_on_later_pages`,
`parse_response_decodes_accounts_and_cursor`,
`parse_response_without_cursor_is_the_last_page`,
`parse_response_missing_accounts_is_an_error`).

If it fails to compile on `RpcClient::send` or `RpcRequest::Custom`, apply the
`reqwest` fallback (executor notes) and report it.

- [ ] **Step 5: Run the crate gate**

Run: `cargo clippy -p storm-solana --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
cargo fmt --all
git add crates/storm-solana/src/gpa_v2.rs crates/storm-solana/src/lib.rs
git commit -m "Add getProgramAccountsV2 helper to storm-solana

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Rewrite discovery — `discover.rs` + `cycle.rs`

**Files:**
- Modify: `bins/storm-collector/Cargo.toml`
- Rewrite: `bins/storm-collector/src/discover.rs`
- Modify: `bins/storm-collector/src/cycle.rs`
- Test: in `bins/storm-collector/src/discover.rs`

`discover_graduations` becomes a cursor-paginated `getProgramAccountsV2` scan
that takes a `changed_since_slot`. `discover_phase` reads that cursor from, and
writes it back to, the `collector_state` table. The pure
`graduation_from_pool_account` validator is unchanged. The signature change to
`discover_graduations` means `discover.rs` and `cycle.rs` must change together —
this is one task so every commit compiles.

- [ ] **Step 1: Add `serde_json` to `bins/storm-collector/Cargo.toml`**

In the `[dependencies]` section of `bins/storm-collector/Cargo.toml`, add this
line (alphabetical order, after `serde`-less deps — place it next to the other
non-storm deps, e.g. directly after `dotenvy.workspace = true`):

```toml
serde_json.workspace = true
```

- [ ] **Step 2: Rewrite `bins/storm-collector/src/discover.rs`**

Replace the **entire file** with:

```rust
//! Graduation discovery — an incremental `getProgramAccountsV2` scan.
//!
//! Each cycle scans only the PumpSwap pools that changed since the previous
//! cycle's slot (`changedSinceSlot`), paginating until the cursor is exhausted.
//! The pure `graduation_from_pool_account` helper turns a candidate pool
//! account into a confirmed graduation; it is unit-tested here. The live scan
//! is exercised by the `#[ignore]`-d integration test.

use solana_sdk::pubkey::Pubkey;
use storm_core::Result;
use storm_pumpfun::{bonding_curve_pda, PumpSwapPool, PUMPSWAP_PROGRAM_ID};
use storm_solana::{fetch_program_accounts_v2_page, RpcContext};

/// On-chain byte length of a PumpSwap `Pool` account: 244 defined-field bytes
/// plus 57 trailing reserved bytes. Verified against the captured fixture in
/// `crates/storm-pumpfun/tests/fixtures/NOTES.md`.
const PUMPSWAP_POOL_ACCOUNT_LEN: u64 = 301;

/// Max accounts per `getProgramAccountsV2` page (the V2 maximum).
const GPA_V2_PAGE_LIMIT: u64 = 10_000;

/// Wrapped SOL — the quote mint of every pump.fun graduation pool.
const WRAPPED_SOL_MINT: Pubkey = solana_sdk::pubkey!("So11111111111111111111111111111111111111112");

/// A graduation discovered on-chain — the data the collector needs to insert a
/// `graduations` row and later run feature extraction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscoveredGraduation {
    /// The graduated token mint (the pool's `base_mint`).
    pub mint: Pubkey,
    /// The canonical PumpSwap pool address.
    pub pool_address: Pubkey,
    /// The token's bonding-curve account — `bonding_curve_pda(mint)`.
    pub bonding_curve: Pubkey,
}

/// Parse a candidate PumpSwap pool account and, if it is a canonical
/// graduation, return the [`DiscoveredGraduation`].
///
/// A canonical pump.fun graduation pool is identified by `index == 0` **and**
/// `quote_mint == wrapped SOL`. NOTE: the pool's `creator` field is the
/// pool-creator EOA, *not* the token's bonding-curve PDA (documented in
/// `storm-pumpfun`'s `tests/fixtures/NOTES.md`).
///
/// Returns `Ok(None)` for a parseable pool that is *not* a canonical graduation
/// and `Err` only when the bytes are too short / malformed for `unpack`.
pub fn graduation_from_pool_account(
    pool_address: Pubkey,
    data: &[u8],
) -> Result<Option<DiscoveredGraduation>> {
    let pool = PumpSwapPool::unpack(data)?;
    if pool.index != 0 || pool.quote_mint != WRAPPED_SOL_MINT {
        return Ok(None);
    }
    let mint = pool.base_mint;
    Ok(Some(DiscoveredGraduation {
        mint,
        pool_address,
        bonding_curve: bonding_curve_pda(&mint),
    }))
}

/// The `getProgramAccountsV2` filter array that narrows the scan to canonical
/// (index-0) PumpSwap `Pool` accounts: a `dataSize` filter on the on-chain
/// account length plus a `memcmp` of `[0x00, 0x00]` (base58 `"11"`) on the
/// `index` field — a little-endian `u16` at offset 9 (8-byte discriminator +
/// 1-byte `pool_bump`).
fn pumpswap_v2_filters() -> serde_json::Value {
    serde_json::json!([
        { "dataSize": PUMPSWAP_POOL_ACCOUNT_LEN },
        { "memcmp": { "offset": 9, "bytes": "11", "encoding": "base58" } }
    ])
}

/// Discover canonical pump.fun graduations by scanning the PumpSwap pools that
/// changed since `changed_since_slot`, paginating until the cursor is
/// exhausted. Every returned account is re-validated by
/// [`graduation_from_pool_account`] — the server filter is approximate, the
/// parser is authoritative. `changedSinceSlot` returns *changed* pools (new
/// ones and ones that merely traded), so the caller deduplicates against the
/// `graduations` table; this function only keeps the per-cycle set bounded.
pub async fn discover_graduations(
    rpc: &RpcContext,
    changed_since_slot: u64,
) -> Result<Vec<DiscoveredGraduation>> {
    let filters = pumpswap_v2_filters();
    let mut found = Vec::new();
    let mut cursor: Option<String> = None;
    loop {
        let page = fetch_program_accounts_v2_page(
            rpc,
            &PUMPSWAP_PROGRAM_ID,
            &filters,
            changed_since_slot,
            GPA_V2_PAGE_LIMIT,
            cursor.as_deref(),
        )
        .await?;
        for account in &page.accounts {
            // A pool that fails to parse is skipped, not fatal — the server
            // filter can return an account the strict parser rejects.
            match graduation_from_pool_account(account.pubkey, &account.data) {
                Ok(Some(grad)) => found.push(grad),
                Ok(None) => {}
                Err(e) => {
                    tracing::debug!(pubkey = %account.pubkey, error = %e, "skipping unparseable pool account")
                }
            }
        }
        match page.pagination_key {
            Some(key) => cursor = Some(key),
            None => break,
        }
    }
    Ok(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    // The canonical PumpSwap pool fixture captured by storm-pumpfun (sub-plan 1).
    const POOL_FIXTURE: &[u8] =
        include_bytes!("../../../crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin");

    #[test]
    fn real_fixture_is_recognised_as_a_graduation() {
        let pool_addr = Pubkey::new_unique();
        let grad = graduation_from_pool_account(pool_addr, POOL_FIXTURE)
            .unwrap()
            .expect("the fixture is a canonical graduation pool");
        assert_eq!(grad.pool_address, pool_addr);
        assert_eq!(
            grad.mint,
            Pubkey::from_str_const("5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump"),
        );
        assert_eq!(grad.bonding_curve, bonding_curve_pda(&grad.mint));
    }

    #[test]
    fn short_data_is_a_parse_error() {
        match graduation_from_pool_account(Pubkey::new_unique(), &[0u8; 40]) {
            Err(storm_core::StormError::Parse(_)) => {}
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn pumpswap_filters_pin_size_and_index() {
        let filters = pumpswap_v2_filters();
        let arr = filters.as_array().expect("filters is an array");
        assert_eq!(arr.len(), 2);
        // First filter pins the on-chain account size (301 bytes).
        assert_eq!(arr[0]["dataSize"], 301);
        // Second filter pins index == 0 via a base58 memcmp at offset 9.
        assert_eq!(arr[1]["memcmp"]["offset"], 9);
        assert_eq!(arr[1]["memcmp"]["bytes"], "11");
    }
}
```

- [ ] **Step 3: Rewrite `discover_phase` in `bins/storm-collector/src/cycle.rs`**

In `bins/storm-collector/src/cycle.rs`, find the current `discover_phase`
function — it begins with the doc-comment `/// Phase 1 — discover graduations`
and runs through its closing `}` (currently the only function between
`run_cycle` and `snapshot_phase`). Replace that whole function with the
following — note it is preceded by a new module-level `const`:

```rust
/// `collector_state` key holding the slot the last successful discover scan
/// reached — the `changedSinceSlot` cursor for the next scan.
const LAST_DISCOVERY_SLOT_KEY: &str = "last_discovery_slot";

/// Phase 1 — discover graduations changed since the last cycle and insert any
/// not yet tracked.
async fn discover_phase(rpc: &RpcContext, store: &Store, now: i64) -> Result<()> {
    let current_slot = rpc
        .rpc()
        .get_slot()
        .await
        .map_err(|e| storm_core::StormError::Rpc(format!("get_slot: {e}")))?;

    // Cold start: the first cycle ever has no cursor. Seed it with the current
    // slot and discover nothing this cycle — backfilling history is Phase 2's
    // job; the live collector starts fresh from here.
    let changed_since_slot = match store.get_collector_state(LAST_DISCOVERY_SLOT_KEY).await? {
        Some(s) => s.parse::<u64>().map_err(|e| {
            storm_core::StormError::Parse(format!("{LAST_DISCOVERY_SLOT_KEY} '{s}': {e}"))
        })?,
        None => {
            store
                .set_collector_state(LAST_DISCOVERY_SLOT_KEY, &current_slot.to_string())
                .await?;
            tracing::info!(
                slot = current_slot,
                "discovery cold start: cursor seeded, no scan this cycle"
            );
            return Ok(());
        }
    };

    let discovered = discover_graduations(rpc, changed_since_slot).await?;
    let mut new_count = 0usize;
    for grad in discovered {
        let row = GraduationRow {
            mint: grad.mint,
            pool_address: grad.pool_address,
            bonding_curve_address: grad.bonding_curve,
            graduation_slot: current_slot,
            detected_at: now,
            status: GraduationStatus::PendingSnapshot,
        };
        // insert_graduation is idempotent on `mint`: Some(id) = newly inserted,
        // None = already tracked (a pool that merely traded since last scan).
        if store.insert_graduation(&row).await?.is_some() {
            new_count += 1;
        }
    }

    // Advance the cursor only after a fully successful scan + insert. A failed
    // cycle leaves it unchanged, so the next cycle safely re-scans the window
    // (the idempotent insert makes the overlap harmless).
    store
        .set_collector_state(LAST_DISCOVERY_SLOT_KEY, &current_slot.to_string())
        .await?;
    tracing::info!(
        new = new_count,
        changed_since_slot,
        through_slot = current_slot,
        "discover phase complete"
    );
    Ok(())
}
```

No other part of `cycle.rs` changes — `run_cycle` still calls
`discover_phase(rpc, store, now)`, and the existing
`use crate::discover::discover_graduations;` import is unchanged.

- [ ] **Step 4: Run the tests**

Run: `cargo test -p storm-collector`
Expected: PASS — all `storm-collector` unit tests, including the three
`discover` tests (`real_fixture_is_recognised_as_a_graduation`,
`short_data_is_a_parse_error`, `pumpswap_filters_pin_size_and_index`). 21 tests
total (the `discover` test count is unchanged at 3; the renamed filter test
replaces the old `filter_pins_account_size_and_index`).

- [ ] **Step 5: Run the workspace gate**

Run: `cargo check --workspace --all-targets && cargo clippy --workspace --all-targets -- -D warnings`
Expected: `Finished`, then no clippy output, exit 0.

- [ ] **Step 6: Commit**

```bash
cargo fmt --all
git add bins/storm-collector/Cargo.toml bins/storm-collector/src/discover.rs bins/storm-collector/src/cycle.rs Cargo.lock
git commit -m "Rewrite discovery as an incremental getProgramAccountsV2 scan

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Update the live-RPC integration test

**Files:**
- Rewrite: `bins/storm-collector/tests/integration.rs`

The `#[ignore]`-d live test must issue the new `getProgramAccountsV2` call
instead of the rejected `getProgramAccounts`. It now calls the public
`storm_solana::fetch_program_accounts_v2_page` directly.

- [ ] **Step 1: Replace `bins/storm-collector/tests/integration.rs`**

Replace the **entire file** with:

```rust
//! Live-RPC end-to-end check for graduation discovery.
//!
//! `#[ignore]`-d: it requires network and is never run by CI. Run it manually:
//!
//! ```text
//! set -a && . ./.env && set +a
//! cargo test -p storm-collector --test integration -- --ignored --nocapture
//! ```
//!
//! `SOLANA_RPC_URL` comes from `.env`. The test issues one
//! `getProgramAccountsV2` page — the same call the daemon's discover phase
//! makes — for PumpSwap pools changed in roughly the last 30 minutes, and
//! asserts the result parses into canonical graduations.

use solana_sdk::pubkey::Pubkey;
use storm_core::SolanaConfig;
use storm_pumpfun::{PumpSwapPool, PUMPSWAP_PROGRAM_ID};
use storm_solana::{fetch_program_accounts_v2_page, RpcContext};

/// On-chain PumpSwap `Pool` account length (see storm-pumpfun NOTES.md).
const PUMPSWAP_POOL_ACCOUNT_LEN: u64 = 301;

/// Wrapped SOL — the quote mint of every pump.fun graduation pool.
const WRAPPED_SOL_MINT: Pubkey = solana_sdk::pubkey!("So11111111111111111111111111111111111111112");

/// Roughly 30 minutes of Solana slots (≈400 ms/slot).
const SLOTS_PER_30_MIN: u64 = 5_000;

#[tokio::test]
#[ignore = "hits live Solana RPC; run manually with SOLANA_RPC_URL set"]
async fn discovers_canonical_graduations_from_pumpswap() {
    let rpc_url =
        std::env::var("SOLANA_RPC_URL").expect("set SOLANA_RPC_URL (see .env) to run this test");
    let cfg = SolanaConfig {
        rpc_url,
        ws_url: String::new(),
        commitment: "confirmed".to_string(),
    };
    let rpc = RpcContext::from_config(&cfg);

    let current_slot = rpc.rpc().get_slot().await.expect("getSlot failed");
    let changed_since_slot = current_slot.saturating_sub(SLOTS_PER_30_MIN);

    // The same filtered query the daemon's discover phase issues: index-0
    // PumpSwap Pool accounts of the on-chain account size.
    let filters = serde_json::json!([
        { "dataSize": PUMPSWAP_POOL_ACCOUNT_LEN },
        { "memcmp": { "offset": 9, "bytes": "11", "encoding": "base58" } }
    ]);

    let page = fetch_program_accounts_v2_page(
        &rpc,
        &PUMPSWAP_PROGRAM_ID,
        &filters,
        changed_since_slot,
        10_000,
        None,
    )
    .await
    .expect("getProgramAccountsV2 call failed");

    // PumpSwap is busy; a ~30-minute window always has changed pools.
    assert!(
        !page.accounts.is_empty(),
        "expected at least one changed index-0 PumpSwap pool in the last ~30 min"
    );

    // Every returned account must parse; a clear majority must be a canonical
    // graduation — index 0 with wSOL as the quote mint.
    let mut canonical = 0usize;
    for account in &page.accounts {
        let pool = PumpSwapPool::unpack(&account.data)
            .unwrap_or_else(|e| panic!("pool {} failed to parse: {e}", account.pubkey));
        if pool.index == 0 && pool.quote_mint == WRAPPED_SOL_MINT {
            canonical += 1;
        }
    }
    assert!(
        canonical * 2 >= page.accounts.len(),
        "at least half of the {} changed pools should be canonical graduations, got {canonical}",
        page.accounts.len(),
    );

    println!(
        "page returned {} changed index-0 pools, {canonical} confirmed canonical; next cursor: {:?}",
        page.accounts.len(),
        page.pagination_key,
    );
}
```

- [ ] **Step 2: Verify it compiles and is skipped**

Run: `cargo test -p storm-collector --test integration`
Expected: compiles; reports `0 passed; 0 failed; 1 ignored` — no network touched.

- [ ] **Step 3: Run clippy on the test target**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
cargo fmt --all
git add bins/storm-collector/tests/integration.rs
git commit -m "Update live discovery test to getProgramAccountsV2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Redact the API key from the startup log

**Files:**
- Modify: `bins/storm-collector/src/main.rs`
- Test: in `bins/storm-collector/src/main.rs`

The daemon currently logs `cfg.solana.rpc_url` verbatim on startup — and that
URL carries the Helius API key as a query parameter. Add a pure helper that
strips the query string and use it in the log line.

- [ ] **Step 1: Add the `redacted_rpc_url` helper to `bins/storm-collector/src/main.rs`**

Add this function directly above the existing `now_unix` function:

```rust
/// The RPC URL with any query string removed — the query carries the API key,
/// which must not be written to logs.
fn redacted_rpc_url(url: &str) -> &str {
    match url.split_once('?') {
        Some((base, _)) => base,
        None => url,
    }
}
```

- [ ] **Step 2: Use it in the startup log line**

In `main`, the startup log currently reads:

```rust
    tracing::info!(
        db = %cli.db,
        rpc = %cfg.solana.rpc_url,
        cycle_secs = collector_cfg.cycle_interval.as_secs(),
        once = cli.once,
        "storm-collector starting",
    );
```

Change the `rpc` field line from `rpc = %cfg.solana.rpc_url,` to:

```rust
        rpc = redacted_rpc_url(&cfg.solana.rpc_url),
```

- [ ] **Step 3: Add a test**

In the existing `#[cfg(test)] mod tests` block in `main.rs` (which already
contains `now_unix_is_a_plausible_recent_timestamp`), add:

```rust
    #[test]
    fn redacted_rpc_url_strips_the_api_key_query() {
        assert_eq!(
            redacted_rpc_url("https://mainnet.helius-rpc.com/?api-key=secret"),
            "https://mainnet.helius-rpc.com/"
        );
        // A URL with no query string is returned unchanged.
        assert_eq!(
            redacted_rpc_url("https://api.mainnet-beta.solana.com"),
            "https://api.mainnet-beta.solana.com"
        );
    }
```

- [ ] **Step 4: Run the tests**

Run: `cargo test -p storm-collector`
Expected: PASS — including the new `redacted_rpc_url_strips_the_api_key_query`.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
cargo fmt --all
git add bins/storm-collector/src/main.rs
git commit -m "Redact the API key from the storm-collector startup log

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Full verification

**Files:** none — verification only.

- [ ] **Step 1: Build the workspace**

Run: `cargo build --workspace`
Expected: `Finished`.

- [ ] **Step 2: Run the full test suite (CI `cargo test` gate)**

Run: `cargo test --workspace`
Expected: all tests pass. `storm-solana` has the 5 new `gpa_v2` tests;
`storm-collector` has its unit tests plus the new `redacted_rpc_url` test. Both
`integration` tests (`storm-features`'s and `storm-collector`'s) report
`1 ignored`. **No network is touched.**

- [ ] **Step 3: `cargo check` across all targets (CI check gate)**

Run: `cargo check --workspace --all-targets`
Expected: `Finished`, no errors.

- [ ] **Step 4: Clippy (CI clippy gate)**

Run: `cargo clippy --workspace --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 5: Formatting (CI fmt gate)**

Run: `cargo fmt --all -- --check`
Expected: no output, exit 0. If it reports diffs, run `cargo fmt --all`, then
`git add -A && git commit -m "Apply cargo fmt"`.

- [ ] **Step 6: Live proof — run the integration test against Helius**

This is the empirical confirmation the redesign actually works. It needs
network and `.env`:

```bash
set -a && . ./.env && set +a
cargo test -p storm-collector --test integration -- --ignored --nocapture
```

Expected: `discovers_canonical_graduations_from_pumpswap` **passes**, printing a
line like `page returned N changed index-0 pools, M confirmed canonical`. This
proves `getProgramAccountsV2` + `changedSinceSlot` works end to end on the live
free tier — the exact thing the old `getProgramAccounts` call failed at.

- [ ] **Step 7: Optional — `--once` daemon smoke test**

Optionally confirm the daemon itself. Against a throwaway database:

```bash
set -a && . ./.env && set +a
# First run: cold start — seeds the discovery cursor, discovers nothing.
cargo run -p storm-collector -- --once --db sqlite://./storm-smoke.db
# A few minutes later, run again — this scan covers the window since the seed.
cargo run -p storm-collector -- --once --db sqlite://./storm-smoke.db
sqlite3 storm-smoke.db "SELECT status, COUNT(*) FROM graduations GROUP BY status;"
rm -f storm-smoke.db storm-smoke.db-shm storm-smoke.db-wal
```

Expected: the first run logs `discovery cold start`; the second logs
`discover phase complete` with a `new=` count. Not part of CI; not required for
the plan's done criteria (Step 6 is the live proof).

---

## Done criteria

- `storm-solana` exposes `getProgramAccountsV2` as a `gpa_v2` module: a pure
  request builder, a pure response parser (both unit-tested), and one async
  page-fetch function.
- `storm-collector`'s `discover_graduations` is a cursor-paginated
  `getProgramAccountsV2` scan keyed on `changedSinceSlot`; the discover phase
  reads and writes the `last_discovery_slot` cursor in `collector_state`, with a
  cold-start seed on the first cycle.
- The pure pool validator and idempotent `insert_graduation` dedup are
  unchanged; a failed cycle leaves the cursor un-advanced for a safe re-scan.
- The startup log no longer prints the API key.
- `cargo build`, `cargo test --workspace`, `cargo check --workspace
  --all-targets`, `cargo clippy --workspace --all-targets -- -D warnings`, and
  `cargo fmt --all -- --check` all pass at every commit, with no network.
- The `#[ignore]`-d live integration test passes against the Helius free tier
  (Task 5, Step 6) — the empirical proof discovery works.

## Self-review

**Spec coverage.** Spec §4.2 (the `getProgramAccountsV2` cycle) → Task 1
(the helper) + Task 2 (`discover_graduations` loop + `discover_phase` cursor).
§4.3 (code structure: storm-solana helper, discover.rs, cycle.rs) → Tasks 1–2.
§4.4 (error handling: failed page → cursor not advanced) → Task 2's
`discover_phase` (cursor written only after success). §4.5 (testing: pure unit
tests, `#[ignore]`-d live test, network-free `cargo test`) → Tasks 1, 3, 5.
§5 scope table → Tasks 1–4 one-for-one. The API-key log redaction in §5 → Task 4.

**Placeholder scan.** No "TBD"/"TODO"/"implement later". Every code step shows
complete code. The one verification point — `RpcClient::send` /
`RpcRequest::Custom` — is a real, flagged check with a concrete fallback in the
executor notes, not a placeholder.

**Type consistency.** `fetch_program_accounts_v2_page(rpc, program, filters,
changed_since_slot, limit, pagination_key)` — defined in Task 1, called in
Task 2 (`discover.rs`) and Task 3 (`integration.rs`) with matching arguments.
`discover_graduations(rpc, changed_since_slot: u64)` — defined in Task 2's
`discover.rs`, called in Task 2's `cycle.rs`. `ProgramAccountsV2Page { accounts,
pagination_key }` and `ProgramAccountV2 { pubkey, data }` — used consistently.
`LAST_DISCOVERY_SLOT_KEY` — defined and used only in `cycle.rs`.
`redacted_rpc_url` — defined and used only in `main.rs`.
