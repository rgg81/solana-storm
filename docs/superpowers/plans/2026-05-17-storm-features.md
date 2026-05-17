# storm-features Crate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `storm-features` library crate that, given a graduated pump.fun token mint, fetches the needed on-chain data via Solana RPC and computes a Lean-v1 `FeatureVector` of survival-prediction features.

**Architecture:** Two layers. (1) PURE feature-computation functions — each takes already-fetched plain-data inputs (no RPC types) and returns feature values; these get real TDD unit tests against synthetic data. (2) A thin async RPC-orchestration layer (`fetch.rs`) that calls `solana-client` to fetch accounts, the holder list, and one signature page, converts the RPC response types into the plain-data inputs, and calls the pure functions. The public entry point `extract_features` ties them together. One `#[ignore]`-d integration test exercises the real RPC path; CI never hits the network.

**Tech Stack:** Rust, `tokio` (async), `solana-client` 2.3 (`RpcClient` — `get_token_largest_accounts`, `get_signatures_for_address_with_config`), `solana-sdk` (`Pubkey`), `storm-core` (`Result`/`StormError`), `storm-solana` (`RpcContext`, `MintInfo`, `TokenAccountSnapshot`), `storm-pumpfun` (`BondingCurve`, `PumpSwapPool`, PDA derivation).

---

## Context

This is **Phase 1, sub-plan 2 of 3** (data foundation) for the pump.fun survival
strategy — see `docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md`,
especially section 6 (Data & features) and section 9 (Architecture). Sub-plan 1
(`storm-pumpfun`) is merged; sub-plan 3 (`storm-collector`) builds on this crate.

Unlike `storm-pumpfun` (pure parsing, no network), `storm-features` **does** use
the network: it calls Solana RPC to fetch the data the features are computed
from. The crate is split so that all *computation* stays pure and unit-tested,
and only a thin layer touches RPC.

### Lean v1 scope — the five feature groups

This was explicitly decided as the v1 scope. Implement exactly these, no more:

1. **Liquidity** — PumpSwap pool reserves (the base/quote pool token-account
   balances), LP burn status, pool liquidity relative to market cap.
2. **Contract flags** — is the mint authority present? is the freeze authority
   present? (from the SPL `Mint` account, via `storm-solana`'s `MintInfo`).
3. **Holder distribution** — holder count, top-10 / top-20 concentration, and
   the creator's remaining bag — from `getTokenLargestAccounts` (top 20
   holders, one RPC call).
4. **Bonding-curve snapshot** — the fields available in the pump.fun
   `BondingCurve` account (reserves, `complete`, `creator`).
5. **Deployer signal** — coarse, cheap signals about the deployer/creator
   wallet from a SINGLE bounded `getSignaturesForAddress` page (capped
   signature count, age of the oldest visible signature). No unbounded
   full-history crawl.

**Explicitly OUT of scope for v1** (a future v2): transaction-history-crawl
features — bonding-curve trade microstructure (unique buyers, bundle detection,
volume concentration), deployer prior-token outcomes, post-graduation trade
behavior. Do **not** implement these.

### On-chain facts (researched, verified against the codebase)

- `getTokenLargestAccounts` returns the **top 20** token accounts for a mint in
  one RPC call. `solana-client` 2.3.13 exposes it as
  `RpcClient::get_token_largest_accounts(&Pubkey) -> ClientResult<Vec<RpcTokenAccountBalance>>`.
  `RpcTokenAccountBalance { address: String, amount: UiTokenAmount }`, and
  `UiTokenAmount { ui_amount: Option<f64>, decimals: u8, amount: String, ui_amount_string: String }`
  — the raw integer balance is the **`amount` field, a decimal `String`**.
- `getSignaturesForAddress` returns up to 1000 confirmed signatures (newest
  first) for an address. `solana-client` 2.3.13 exposes it as
  `RpcClient::get_signatures_for_address_with_config(&Pubkey, GetConfirmedSignaturesForAddress2Config) -> ClientResult<Vec<RpcConfirmedTransactionStatusWithSignature>>`.
  `GetConfirmedSignaturesForAddress2Config { before: Option<Signature>, until: Option<Signature>, limit: Option<usize>, commitment: Option<CommitmentConfig> }`.
  `RpcConfirmedTransactionStatusWithSignature { signature: String, slot: u64, err: Option<TransactionError>, memo: Option<String>, block_time: Option<UnixTimestamp>, confirmation_status: Option<TransactionConfirmationStatus> }`
  — `block_time` is `Option<i64>` (Unix seconds). A single bounded page = one
  call with `limit: Some(N)` and no `before`/`until` cursor.
- A graduated pump.fun token's `BondingCurve::complete == true`; the
  `BondingCurve::creator` field is the deployer wallet. The PumpSwap pool's
  `pool_base_token_account` / `pool_quote_token_account` are SPL token accounts
  whose `amount` (parsed via `storm-solana`'s `TokenAccountSnapshot::unpack`) is
  the pool's base/quote reserve.
- An SPL mint with **no mint authority** is "renounced"; with no freeze
  authority it cannot freeze holder accounts. `storm-solana`'s `MintInfo`
  already exposes `mint_authority: Option<Pubkey>` and
  `freeze_authority: Option<Pubkey>` — `None` means absent.
- LP burn: a PumpSwap graduation pool's `lp_supply` is the LP-token supply held
  by the pool record; for the Lean-v1 signal, treat the pool's `lp_supply == 0`
  as "LP burned" (the migrator burned the LP tokens). This is a coarse v1
  proxy, documented as such.

### Import paths (verified in `Cargo.lock` — `solana-client` 2.3.13)

- `use solana_client::nonblocking::rpc_client::RpcClient;` — already wrapped by
  `storm_solana::RpcContext` (`RpcContext::rpc()` returns `&RpcClient`).
- `use solana_client::rpc_client::GetConfirmedSignaturesForAddress2Config;`
- `use solana_client::rpc_response::{RpcConfirmedTransactionStatusWithSignature, RpcTokenAccountBalance};`
  (re-exported from `solana-rpc-client-api`).

## Notes for the executor

- If `cargo` is not found, run `. "$HOME/.cargo/env"` first.
- The repo CI runs three gates; **every commit must keep all three green**:
  `cargo test`, `cargo clippy --workspace --all-targets -- -D warnings`,
  `cargo fmt --check`.
- `cargo test` must NOT require network. PURE compute functions get real TDD
  unit tests with synthetic inputs. The one test that actually hits RPC lives
  in `tests/integration.rs` and is marked `#[ignore]` — `cargo test` skips it
  by default; clippy `--all-targets` still type-checks it.
- `SOLANA_RPC_URL` is read from `.env` (gitignored). Manual integration run:
  `set -a && . ./.env && set +a` then
  `cargo test -p storm-features --test integration -- --ignored --nocapture`.
- Follow the `storm-pumpfun` module style: small single-responsibility files,
  exact-size / discriminator checks already handled by the parsers it reuses,
  `StormError::Parse` for bad data and `StormError::Rpc` for RPC failures.
- The existing crates' unit tests are pure / fixture-based; `storm-solana` has
  no live-network test (its `tests/account_subscribe.rs` runs against a local
  mock). Follow that convention — keep network strictly out of CI.

## File structure

| Path | Change | Responsibility |
|---|---|---|
| `crates/storm-features/Cargo.toml` | Create | crate manifest — deps on `storm-core`, `storm-solana`, `storm-pumpfun`, `solana-client`, `solana-sdk`, `tokio` |
| `crates/storm-features/src/lib.rs` | Create | crate doc, module exports, `FeatureVector` struct, `extract_features` entry point |
| `crates/storm-features/src/contract.rs` | Create | pure: contract-flag features from `MintInfo` |
| `crates/storm-features/src/curve.rs` | Create | pure: bonding-curve snapshot features from `BondingCurve` |
| `crates/storm-features/src/liquidity.rs` | Create | pure: liquidity features from pool reserves + supply + LP state |
| `crates/storm-features/src/holders.rs` | Create | pure: holder-distribution features from a plain holder-balance list |
| `crates/storm-features/src/deployer.rs` | Create | pure: deployer signals from a plain signature-page summary |
| `crates/storm-features/src/fetch.rs` | Create | thin async RPC layer: fetch accounts / holders / signature page, convert RPC types to plain inputs |
| `crates/storm-features/tests/integration.rs` | Create | one `#[ignore]`-d live-RPC end-to-end test |
| `Cargo.toml` | Modify | register `storm-features` in `[workspace.dependencies]` |

The pure modules (`contract`, `curve`, `liquidity`, `holders`, `deployer`) never
import `solana-client` and never do I/O. `fetch.rs` is the only module that
touches RPC. `lib.rs` defines the aggregate `FeatureVector` and the
`extract_features` orchestrator.

---

### Task 1: Scaffold the `storm-features` crate

**Files:**
- Create: `crates/storm-features/Cargo.toml`, `crates/storm-features/src/lib.rs`
- Modify: `Cargo.toml`

- [ ] **Step 1: Create `crates/storm-features/Cargo.toml`**

```toml
[package]
name = "storm-features"
version = "0.1.0"
edition.workspace = true
license.workspace = true
publish.workspace = true
authors.workspace = true
repository.workspace = true

[dependencies]
storm-core.workspace = true
storm-solana.workspace = true
storm-pumpfun.workspace = true
solana-client.workspace = true
solana-sdk.workspace = true
tokio.workspace = true

[dev-dependencies]
tokio = { workspace = true, features = ["macros", "rt-multi-thread"] }
```

- [ ] **Step 2: Create `crates/storm-features/src/lib.rs`**

```rust
//! Survival-prediction feature extraction for graduated pump.fun tokens.
//!
//! Given a graduated token mint, [`extract_features`] fetches the needed
//! on-chain data via Solana RPC and computes a Lean-v1 [`FeatureVector`].
//!
//! The crate is split in two layers:
//!
//! * **Pure compute** — [`contract`], [`curve`], [`liquidity`], [`holders`],
//!   [`deployer`]: each takes already-fetched plain-data inputs and returns
//!   feature values. No network, no `solana-client` types — unit-tested
//!   against synthetic data.
//! * **RPC orchestration** — [`fetch`]: the only module that touches the
//!   network. It fetches accounts / the holder list / one signature page and
//!   feeds the pure functions.

pub mod contract;
pub mod curve;
pub mod deployer;
pub mod fetch;
pub mod holders;
pub mod liquidity;
```

- [ ] **Step 3: Register the crate in the workspace**

In the root `Cargo.toml`, add to `[workspace.dependencies]` immediately after
the `storm-pumpfun` line:

```toml
storm-features = { path = "crates/storm-features" }
```

The resulting block reads:

```toml
storm-core = { path = "crates/storm-core" }
storm-solana = { path = "crates/storm-solana" }
storm-store = { path = "crates/storm-store" }
storm-pumpfun = { path = "crates/storm-pumpfun" }
storm-features = { path = "crates/storm-features" }
```

(The `members = ["crates/*", "bins/*"]` glob already picks up the new crate.)

- [ ] **Step 4: Create placeholder module files so `lib.rs` compiles**

The `pub mod` lines reference five files that don't exist yet. Create each of
the six module files with a one-line doc comment so the crate compiles after
Task 1; Tasks 2–8 fill them in.

Create `crates/storm-features/src/contract.rs`:

```rust
//! Pure contract-flag feature computation.
```

Create `crates/storm-features/src/curve.rs`:

```rust
//! Pure bonding-curve snapshot feature computation.
```

Create `crates/storm-features/src/liquidity.rs`:

```rust
//! Pure liquidity feature computation.
```

Create `crates/storm-features/src/holders.rs`:

```rust
//! Pure holder-distribution feature computation.
```

Create `crates/storm-features/src/deployer.rs`:

```rust
//! Pure deployer-signal feature computation.
```

Create `crates/storm-features/src/fetch.rs`:

```rust
//! Async RPC orchestration — the only module that touches the network.
```

- [ ] **Step 5: Verify it builds**

Run: `cargo build -p storm-features`
Expected: `Finished`. `solana-client`, `tokio`, and the other deps are already
in the workspace lockfile, so no new network fetch is needed.

- [ ] **Step 6: Commit**

```bash
git add crates/storm-features/Cargo.toml crates/storm-features/src Cargo.toml Cargo.lock
git commit -m "Scaffold storm-features crate

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Contract-flag features (pure)

**Files:**
- Modify: `crates/storm-features/src/contract.rs`
- Test: in `contract.rs` (`#[cfg(test)]` module)

Contract flags come straight from `storm-solana`'s `MintInfo`, which already
exposes `mint_authority: Option<Pubkey>` and `freeze_authority: Option<Pubkey>`.
This module is the thinnest pure mapping: `Option<Pubkey>` presence → `bool`.

- [ ] **Step 1: Write the failing test**

Replace the contents of `crates/storm-features/src/contract.rs` with:

```rust
//! Pure contract-flag feature computation.

use solana_sdk::pubkey::Pubkey;
use storm_solana::MintInfo;

/// SPL-mint authority flags — the Lean-v1 "contract flags" feature group.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ContractFlags {
    /// True if the mint authority is still set (more tokens can be minted).
    pub mint_authority_present: bool,
    /// True if the freeze authority is still set (holder accounts can be
    /// frozen).
    pub freeze_authority_present: bool,
}

/// Derive the contract flags from a fetched SPL `Mint`.
pub fn contract_flags(mint: &MintInfo) -> ContractFlags {
    ContractFlags {
        mint_authority_present: mint.mint_authority.is_some(),
        freeze_authority_present: mint.freeze_authority.is_some(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mint_with(
        mint_authority: Option<Pubkey>,
        freeze_authority: Option<Pubkey>,
    ) -> MintInfo {
        MintInfo {
            address: Pubkey::new_unique(),
            decimals: 6,
            supply: 1_000_000_000_000_000,
            mint_authority,
            freeze_authority,
        }
    }

    #[test]
    fn renounced_mint_has_no_authorities() {
        let flags = contract_flags(&mint_with(None, None));
        assert!(!flags.mint_authority_present);
        assert!(!flags.freeze_authority_present);
    }

    #[test]
    fn both_authorities_present_are_detected() {
        let flags = contract_flags(&mint_with(
            Some(Pubkey::new_unique()),
            Some(Pubkey::new_unique()),
        ));
        assert!(flags.mint_authority_present);
        assert!(flags.freeze_authority_present);
    }

    #[test]
    fn mixed_authorities_map_independently() {
        let only_freeze = contract_flags(&mint_with(None, Some(Pubkey::new_unique())));
        assert!(!only_freeze.mint_authority_present);
        assert!(only_freeze.freeze_authority_present);

        let only_mint = contract_flags(&mint_with(Some(Pubkey::new_unique()), None));
        assert!(only_mint.mint_authority_present);
        assert!(!only_mint.freeze_authority_present);
    }
}
```

- [ ] **Step 2: Run it to verify it passes**

The implementation is included alongside the test in Step 1 (this module is a
trivial pure mapping; a separate red phase would just duplicate the file).

Run: `cargo test -p storm-features contract`
Expected: PASS — all three tests.

- [ ] **Step 3: Run clippy on the crate**

Run: `cargo clippy -p storm-features --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add crates/storm-features/src/contract.rs
git commit -m "Add contract-flag features

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Bonding-curve snapshot features (pure)

**Files:**
- Modify: `crates/storm-features/src/curve.rs`
- Test: in `curve.rs`

The bonding-curve snapshot exposes the fields available in
`storm_pumpfun::BondingCurve` (reserves, `complete`, `creator`). This module
copies the relevant fields into a feature struct and adds the one trivial
derived flag (`graduated`).

- [ ] **Step 1: Write the failing test**

Replace the contents of `crates/storm-features/src/curve.rs` with:

```rust
//! Pure bonding-curve snapshot feature computation.

use solana_sdk::pubkey::Pubkey;
use storm_pumpfun::BondingCurve;

/// Bonding-curve snapshot — the Lean-v1 "bonding-curve" feature group.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CurveSnapshot {
    /// True once the curve has filled and the token has graduated.
    pub graduated: bool,
    /// SOL reserves still recorded on the bonding-curve account (lamports).
    pub real_sol_reserves: u64,
    /// Token reserves still recorded on the bonding-curve account (raw units).
    pub real_token_reserves: u64,
    /// Total token supply minted by the curve (raw units).
    pub token_total_supply: u64,
}

/// Derive the bonding-curve snapshot features from a fetched `BondingCurve`.
pub fn curve_snapshot(bc: &BondingCurve) -> CurveSnapshot {
    CurveSnapshot {
        graduated: bc.complete,
        real_sol_reserves: bc.real_sol_reserves,
        real_token_reserves: bc.real_token_reserves,
        token_total_supply: bc.token_total_supply,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn curve(complete: bool) -> BondingCurve {
        BondingCurve {
            virtual_token_reserves: 1_073_000_000_000_000,
            virtual_sol_reserves: 30_000_000_000,
            real_token_reserves: 793_100_000_000_000,
            real_sol_reserves: 85_000_000_000,
            token_total_supply: 1_000_000_000_000_000,
            complete,
            creator: Pubkey::new_unique(),
        }
    }

    #[test]
    fn complete_curve_is_graduated() {
        let snap = curve_snapshot(&curve(true));
        assert!(snap.graduated);
    }

    #[test]
    fn incomplete_curve_is_not_graduated() {
        let snap = curve_snapshot(&curve(false));
        assert!(!snap.graduated);
    }

    #[test]
    fn snapshot_copies_reserves_and_supply() {
        let snap = curve_snapshot(&curve(true));
        assert_eq!(snap.real_sol_reserves, 85_000_000_000);
        assert_eq!(snap.real_token_reserves, 793_100_000_000_000);
        assert_eq!(snap.token_total_supply, 1_000_000_000_000_000);
    }
}
```

- [ ] **Step 2: Run it to verify it passes**

The implementation ships with the test in Step 1 — this module is a pure
field projection; a separate red phase would only duplicate the file.

Run: `cargo test -p storm-features curve`
Expected: PASS — all three tests.

- [ ] **Step 3: Run clippy on the crate**

Run: `cargo clippy -p storm-features --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add crates/storm-features/src/curve.rs
git commit -m "Add bonding-curve snapshot features

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Liquidity features (pure)

**Files:**
- Modify: `crates/storm-features/src/liquidity.rs`
- Test: in `liquidity.rs`

Liquidity features are computed from already-fetched plain numbers: the pool's
base and quote reserves (raw `u64` token-account balances), the PumpSwap pool's
`lp_supply`, the token total supply, and the token's current price implied by
the pool. The pure function never fetches anything — `fetch.rs` (Task 7) reads
the pool account and its two reserve token accounts and hands the numbers here.

The spec's "pool liquidity relative to market cap" signal is implemented as the
field `pool_supply_fraction`, derived oracle-free. Reasoning: liquidity-vs-mcap
is (quote-side liquidity) / (market cap), both in the same quote unit; market
cap = `token_total_supply` priced at the pool spot price. With the pool's own
reserves as the only input, spot price (quote per base) =
`quote_reserve / base_reserve`, market cap (in quote) =
`token_total_supply * quote_reserve / base_reserve`, and the ratio algebraically
simplifies to `quote_reserve / market_cap = base_reserve / token_total_supply`.
That is the **fraction of total supply that sits in the pool** — a clean,
oracle-free liquidity-depth signal, computed as an `f64` in `[0.0, 1.0]`. No
SOL/USD price feed is needed in v1.

- [ ] **Step 1: Write the failing test**

Replace the contents of `crates/storm-features/src/liquidity.rs` with:

```rust
//! Pure liquidity feature computation.

/// Already-fetched pool liquidity inputs (raw, oracle-free).
#[derive(Debug, Clone, Copy)]
pub struct PoolReserves {
    /// Raw balance of the pool's base-token account (the graduated token).
    pub base_reserve: u64,
    /// Raw balance of the pool's quote-token account (wrapped SOL).
    pub quote_reserve: u64,
    /// The PumpSwap pool record's `lp_supply` field.
    pub lp_supply: u64,
    /// The graduated token's total supply (raw units).
    pub token_total_supply: u64,
}

/// Liquidity features — the Lean-v1 "liquidity" feature group.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LiquidityFeatures {
    /// Raw base-token reserve held by the pool.
    pub base_reserve: u64,
    /// Raw quote-token (wrapped SOL) reserve held by the pool.
    pub quote_reserve: u64,
    /// True if the pool's LP supply is zero — a coarse "LP burned" proxy.
    pub lp_burned: bool,
    /// Fraction of total token supply that sits in the pool, in `[0.0, 1.0]`.
    /// An oracle-free proxy for pool liquidity relative to market cap. `0.0`
    /// when total supply is zero (degenerate input).
    pub pool_supply_fraction: f64,
}

impl PoolReserves {
    /// True if the pool holds no LP tokens — the coarse v1 "LP burned" signal.
    fn lp_burned(&self) -> bool {
        self.lp_supply == 0
    }

    /// Fraction of total supply held by the pool. `0.0` if supply is zero.
    fn pool_supply_fraction(&self) -> f64 {
        if self.token_total_supply == 0 {
            return 0.0;
        }
        self.base_reserve as f64 / self.token_total_supply as f64
    }
}

/// Derive the liquidity features from already-fetched pool reserves.
pub fn liquidity_features(reserves: &PoolReserves) -> LiquidityFeatures {
    LiquidityFeatures {
        base_reserve: reserves.base_reserve,
        quote_reserve: reserves.quote_reserve,
        lp_burned: reserves.lp_burned(),
        pool_supply_fraction: reserves.pool_supply_fraction(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lp_burned_when_supply_is_zero() {
        let r = PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 0,
            token_total_supply: 1_000_000_000_000_000,
        };
        assert!(liquidity_features(&r).lp_burned);
    }

    #[test]
    fn lp_not_burned_when_supply_positive() {
        let r = PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 1_000_000,
            token_total_supply: 1_000_000_000_000_000,
        };
        assert!(!liquidity_features(&r).lp_burned);
    }

    #[test]
    fn pool_supply_fraction_is_base_over_total() {
        // 200T base of a 1_000T total supply = 20% of supply in the pool.
        let r = PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 0,
            token_total_supply: 1_000_000_000_000_000,
        };
        let f = liquidity_features(&r);
        assert!((f.pool_supply_fraction - 0.2).abs() < 1e-9);
    }

    #[test]
    fn pool_supply_fraction_is_zero_for_zero_supply() {
        let r = PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 0,
            token_total_supply: 0,
        };
        assert_eq!(liquidity_features(&r).pool_supply_fraction, 0.0);
    }

    #[test]
    fn reserves_are_passed_through() {
        let r = PoolReserves {
            base_reserve: 123,
            quote_reserve: 456,
            lp_supply: 0,
            token_total_supply: 1_000,
        };
        let f = liquidity_features(&r);
        assert_eq!(f.base_reserve, 123);
        assert_eq!(f.quote_reserve, 456);
    }
}
```

- [ ] **Step 2: Run it to verify it passes**

The implementation ships with the test in Step 1.

Run: `cargo test -p storm-features liquidity`
Expected: PASS — all five tests.

- [ ] **Step 3: Run clippy on the crate**

Run: `cargo clippy -p storm-features --all-targets -- -D warnings`
Expected: no output, exit 0. (The `as f64` casts on `u64` are intentional and
clippy-clean; `liquidity_features` and the inherent methods are all used.)

- [ ] **Step 4: Commit**

```bash
git add crates/storm-features/src/liquidity.rs
git commit -m "Add liquidity features

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Holder-distribution features (pure)

**Files:**
- Modify: `crates/storm-features/src/holders.rs`
- Test: in `holders.rs`

`getTokenLargestAccounts` returns the **top 20** token accounts for a mint. The
pure function takes a plain `Vec<HolderBalance>` (already converted from the RPC
type by `fetch.rs`), the token's total supply, and the creator's token-account
balance, and computes: holder count (of the visible top set), top-10 and top-20
concentration as a fraction of total supply, and the creator's remaining-bag
fraction.

`HolderBalance` carries the raw integer `amount` and the owning `address`. The
input list is assumed already sorted largest-first (that is the RPC contract),
but `top_n_concentration` sorts defensively so the function is correct for any
synthetic input.

- [ ] **Step 1: Write the failing test**

Replace the contents of `crates/storm-features/src/holders.rs` with:

```rust
//! Pure holder-distribution feature computation.

use solana_sdk::pubkey::Pubkey;

/// One entry from `getTokenLargestAccounts` — a token account and its raw
/// balance. `address` is the token-account address (not the owner wallet);
/// `getTokenLargestAccounts` does not return owners.
#[derive(Debug, Clone, Copy)]
pub struct HolderBalance {
    /// The token-account address.
    pub address: Pubkey,
    /// Raw integer token balance held by this account.
    pub amount: u64,
}

/// Holder-distribution features — the Lean-v1 "holder distribution" group.
/// All concentration fields are fractions of total supply in `[0.0, 1.0]`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HolderFeatures {
    /// Number of holders visible in the top-N set (at most 20 from one RPC
    /// call). A lower bound on the true holder count.
    pub visible_holder_count: usize,
    /// Combined balance of the top 10 holders / total supply.
    pub top10_concentration: f64,
    /// Combined balance of the top 20 holders / total supply.
    pub top20_concentration: f64,
    /// The creator's remaining token balance / total supply ("dev's bag").
    pub creator_bag_fraction: f64,
}

/// Sum the `n` largest balances and divide by `total_supply`. Sorts defensively
/// (descending) so the result is correct regardless of input order. Returns
/// `0.0` if `total_supply` is zero.
fn top_n_concentration(holders: &[HolderBalance], n: usize, total_supply: u64) -> f64 {
    if total_supply == 0 {
        return 0.0;
    }
    let mut amounts: Vec<u64> = holders.iter().map(|h| h.amount).collect();
    amounts.sort_unstable_by_key(|&a| std::cmp::Reverse(a));
    let top_sum: u128 = amounts.iter().take(n).map(|&a| a as u128).sum();
    top_sum as f64 / total_supply as f64
}

/// Derive holder-distribution features from the top-holders list.
///
/// * `holders` — entries from `getTokenLargestAccounts` (up to 20).
/// * `total_supply` — the token's total supply (raw units).
/// * `creator_balance` — the raw balance held by the creator's token account,
///   or `0` if the creator holds none.
pub fn holder_features(
    holders: &[HolderBalance],
    total_supply: u64,
    creator_balance: u64,
) -> HolderFeatures {
    let creator_bag_fraction = if total_supply == 0 {
        0.0
    } else {
        creator_balance as f64 / total_supply as f64
    };
    HolderFeatures {
        visible_holder_count: holders.len(),
        top10_concentration: top_n_concentration(holders, 10, total_supply),
        top20_concentration: top_n_concentration(holders, 20, total_supply),
        creator_bag_fraction,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SUPPLY: u64 = 1_000_000_000_000_000;

    /// Build `n` holders each holding `each` raw units.
    fn holders(n: usize, each: u64) -> Vec<HolderBalance> {
        (0..n)
            .map(|_| HolderBalance {
                address: Pubkey::new_unique(),
                amount: each,
            })
            .collect()
    }

    #[test]
    fn visible_holder_count_is_list_length() {
        let f = holder_features(&holders(17, 1_000), SUPPLY, 0);
        assert_eq!(f.visible_holder_count, 17);
    }

    #[test]
    fn top10_sums_only_ten_largest() {
        // 20 holders, each 1% of supply. Top 10 = 10% of supply.
        let one_percent = SUPPLY / 100;
        let f = holder_features(&holders(20, one_percent), SUPPLY, 0);
        assert!((f.top10_concentration - 0.10).abs() < 1e-9);
        assert!((f.top20_concentration - 0.20).abs() < 1e-9);
    }

    #[test]
    fn concentration_uses_largest_regardless_of_input_order() {
        // One whale (50%) plus nine tiny holders, whale listed LAST.
        let mut hs = holders(9, 1);
        hs.push(HolderBalance {
            address: Pubkey::new_unique(),
            amount: SUPPLY / 2,
        });
        let f = holder_features(&hs, SUPPLY, 0);
        // Top 10 includes the whale → ~50% of supply.
        assert!(f.top10_concentration > 0.49 && f.top10_concentration < 0.51);
    }

    #[test]
    fn creator_bag_fraction_is_creator_balance_over_supply() {
        // Creator holds 5% of supply.
        let f = holder_features(&holders(5, 1_000), SUPPLY, SUPPLY / 20);
        assert!((f.creator_bag_fraction - 0.05).abs() < 1e-9);
    }

    #[test]
    fn empty_holders_give_zero_concentration() {
        let f = holder_features(&[], SUPPLY, 0);
        assert_eq!(f.visible_holder_count, 0);
        assert_eq!(f.top10_concentration, 0.0);
        assert_eq!(f.top20_concentration, 0.0);
        assert_eq!(f.creator_bag_fraction, 0.0);
    }

    #[test]
    fn zero_supply_gives_zero_fractions() {
        let f = holder_features(&holders(5, 1_000), 0, 1_000);
        assert_eq!(f.top10_concentration, 0.0);
        assert_eq!(f.top20_concentration, 0.0);
        assert_eq!(f.creator_bag_fraction, 0.0);
    }
}
```

- [ ] **Step 2: Run it to verify it passes**

The implementation ships with the test in Step 1.

Run: `cargo test -p storm-features holders`
Expected: PASS — all six tests.

- [ ] **Step 3: Run clippy on the crate**

Run: `cargo clippy -p storm-features --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add crates/storm-features/src/holders.rs
git commit -m "Add holder-distribution features

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Deployer-signal features (pure)

**Files:**
- Modify: `crates/storm-features/src/deployer.rs`
- Test: in `deployer.rs`

The deployer signal is **coarse and cheap**: it summarises a SINGLE bounded
`getSignaturesForAddress` page. The pure function takes a plain
`SignaturePage` — the count of signatures returned (capped by the page limit)
and the oldest visible `block_time` — plus a reference "now" timestamp, and
computes the capped signature count and the age (in seconds) of the oldest
visible signature. No full-history crawl: if the page came back full, the count
is a lower bound and `count_capped` flags that.

`fetch.rs` (Task 7) issues exactly one `get_signatures_for_address_with_config`
call with `limit: Some(SIGNATURE_PAGE_LIMIT)` and converts the result into a
`SignaturePage`.

- [ ] **Step 1: Write the failing test**

Replace the contents of `crates/storm-features/src/deployer.rs` with:

```rust
//! Pure deployer-signal feature computation.

/// The page limit for the single bounded `getSignaturesForAddress` call.
/// Coarse by design — v1 never crawls full deployer history.
pub const SIGNATURE_PAGE_LIMIT: usize = 1000;

/// A summary of one bounded `getSignaturesForAddress` page for a wallet.
#[derive(Debug, Clone, Copy)]
pub struct SignaturePage {
    /// Number of signatures returned by the single page (`<= SIGNATURE_PAGE_LIMIT`).
    pub signature_count: usize,
    /// Unix timestamp (seconds) of the oldest signature in the page, if the
    /// page was non-empty and that signature carried a block time.
    pub oldest_block_time: Option<i64>,
}

/// Deployer signals — the Lean-v1 "deployer signal" feature group. Coarse,
/// derived from a single bounded signature page; not a full-history crawl.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeployerSignals {
    /// Signature count from the single page, capped at `SIGNATURE_PAGE_LIMIT`.
    pub capped_signature_count: usize,
    /// True if the page came back full — the real count is a lower bound.
    pub count_capped: bool,
    /// Age in seconds of the oldest visible signature (`now - oldest_block_time`).
    /// `None` if the page was empty or the oldest signature had no block time.
    /// Clamped to `0` if the oldest block time is in the future (clock skew).
    pub oldest_signature_age_secs: Option<i64>,
}

/// Derive the deployer signals from a bounded signature page.
///
/// * `page` — the summary of one `getSignaturesForAddress` page.
/// * `now_unix` — the reference "now" timestamp in Unix seconds.
pub fn deployer_signals(page: &SignaturePage, now_unix: i64) -> DeployerSignals {
    let capped_signature_count = page.signature_count.min(SIGNATURE_PAGE_LIMIT);
    let count_capped = page.signature_count >= SIGNATURE_PAGE_LIMIT;
    let oldest_signature_age_secs = page
        .oldest_block_time
        .map(|t| (now_unix - t).max(0));
    DeployerSignals {
        capped_signature_count,
        count_capped,
        oldest_signature_age_secs,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 2026-05-17T00:00:00Z, a fixed reference for deterministic tests.
    const NOW: i64 = 1_779_062_400;

    #[test]
    fn small_page_is_not_capped() {
        let page = SignaturePage {
            signature_count: 42,
            oldest_block_time: Some(NOW - 3600),
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.capped_signature_count, 42);
        assert!(!s.count_capped);
    }

    #[test]
    fn full_page_is_capped_and_flagged() {
        let page = SignaturePage {
            signature_count: SIGNATURE_PAGE_LIMIT,
            oldest_block_time: Some(NOW - 86_400),
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.capped_signature_count, SIGNATURE_PAGE_LIMIT);
        assert!(s.count_capped);
    }

    #[test]
    fn over_limit_count_is_clamped_to_the_limit() {
        // The RPC contract caps at 1000, but clamp defensively anyway.
        let page = SignaturePage {
            signature_count: 5000,
            oldest_block_time: Some(NOW - 86_400),
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.capped_signature_count, SIGNATURE_PAGE_LIMIT);
        assert!(s.count_capped);
    }

    #[test]
    fn oldest_signature_age_is_now_minus_block_time() {
        let page = SignaturePage {
            signature_count: 10,
            oldest_block_time: Some(NOW - 7 * 86_400),
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.oldest_signature_age_secs, Some(7 * 86_400));
    }

    #[test]
    fn missing_block_time_gives_no_age() {
        let page = SignaturePage {
            signature_count: 10,
            oldest_block_time: None,
        };
        assert_eq!(deployer_signals(&page, NOW).oldest_signature_age_secs, None);
    }

    #[test]
    fn future_block_time_clamps_age_to_zero() {
        // Clock skew: oldest signature appears 60s in the future.
        let page = SignaturePage {
            signature_count: 10,
            oldest_block_time: Some(NOW + 60),
        };
        assert_eq!(deployer_signals(&page, NOW).oldest_signature_age_secs, Some(0));
    }

    #[test]
    fn empty_page_has_zero_count_and_no_age() {
        let page = SignaturePage {
            signature_count: 0,
            oldest_block_time: None,
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.capped_signature_count, 0);
        assert!(!s.count_capped);
        assert_eq!(s.oldest_signature_age_secs, None);
    }
}
```

- [ ] **Step 2: Run it to verify it passes**

The implementation ships with the test in Step 1.

Run: `cargo test -p storm-features deployer`
Expected: PASS — all seven tests.

- [ ] **Step 3: Run clippy on the crate**

Run: `cargo clippy -p storm-features --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add crates/storm-features/src/deployer.rs
git commit -m "Add deployer-signal features

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: RPC orchestration layer

**Files:**
- Modify: `crates/storm-features/src/fetch.rs`
- Test: in `fetch.rs` (pure-conversion helpers only — no network in CI)

This is the only module that touches the network. It contains:

1. Pure conversion helpers — turn `solana-client` RPC response types into the
   plain inputs the Task 2–6 pure functions consume. These get real unit tests
   (no network).
2. Async fetch functions — each issues exactly one RPC call (via the
   `RpcContext`'s wrapped `RpcClient`) and returns a plain input struct. These
   are exercised only by the `#[ignore]`-d integration test in Task 9.

The conversions: `RpcTokenAccountBalance.amount.amount` is a decimal `String`
of the raw integer balance — parse it with `u64::from_str`. A
`getSignaturesForAddress` page is newest-first, so the **oldest** signature is
the **last** element; its `block_time` is `Option<i64>`.

- [ ] **Step 1: Write the failing test for the conversion helpers**

Replace the contents of `crates/storm-features/src/fetch.rs` with:

```rust
//! Async RPC orchestration — the only module that touches the network.
//!
//! Pure conversion helpers (RPC response types → plain feature inputs) are
//! unit-tested here. The async fetch functions issue exactly one RPC call
//! each and are exercised by the `#[ignore]`-d integration test.

use std::str::FromStr;

use solana_client::rpc_client::GetConfirmedSignaturesForAddress2Config;
use solana_client::rpc_response::{
    RpcConfirmedTransactionStatusWithSignature, RpcTokenAccountBalance,
};
use solana_sdk::pubkey::Pubkey;
use storm_core::{Result, StormError};
use storm_pumpfun::{bonding_curve_pda, BondingCurve, PumpSwapPool};
use storm_solana::{MintInfo, RpcContext, TokenAccountSnapshot};

use crate::deployer::{SignaturePage, SIGNATURE_PAGE_LIMIT};
use crate::holders::HolderBalance;

/// Convert one `getTokenLargestAccounts` entry into a plain [`HolderBalance`].
///
/// `RpcTokenAccountBalance.amount.amount` is the raw integer balance encoded
/// as a decimal string; `.address` is the token-account address.
pub(crate) fn holder_balance_from_rpc(
    entry: &RpcTokenAccountBalance,
) -> Result<HolderBalance> {
    let address = Pubkey::from_str(&entry.address).map_err(|e| {
        StormError::Parse(format!("invalid holder address '{}': {e}", entry.address))
    })?;
    let amount = u64::from_str(&entry.amount.amount).map_err(|e| {
        StormError::Parse(format!(
            "invalid holder amount '{}': {e}",
            entry.amount.amount
        ))
    })?;
    Ok(HolderBalance { address, amount })
}

/// Convert a full `getSignaturesForAddress` page into a [`SignaturePage`].
///
/// The page is newest-first, so the oldest signature is the last element.
pub(crate) fn signature_page_from_rpc(
    sigs: &[RpcConfirmedTransactionStatusWithSignature],
) -> SignaturePage {
    SignaturePage {
        signature_count: sigs.len(),
        oldest_block_time: sigs.last().and_then(|s| s.block_time),
    }
}

/// Fetch the SPL `Mint` account for `mint`.
pub async fn fetch_mint(rpc: &RpcContext, mint: &Pubkey) -> Result<MintInfo> {
    rpc.fetch_mint(mint).await
}

/// Fetch and parse a PumpSwap `Pool` account at `pool`.
pub async fn fetch_pool(rpc: &RpcContext, pool: &Pubkey) -> Result<PumpSwapPool> {
    let acct = rpc
        .rpc()
        .get_account_with_commitment(pool, rpc.commitment())
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?
        .value
        .ok_or_else(|| StormError::Parse(format!("pumpswap pool {pool} not found")))?;
    PumpSwapPool::unpack(&acct.data)
}

/// Fetch and parse the bonding-curve account for `mint`.
pub async fn fetch_bonding_curve(
    rpc: &RpcContext,
    mint: &Pubkey,
) -> Result<BondingCurve> {
    let pda = bonding_curve_pda(mint);
    let acct = rpc
        .rpc()
        .get_account_with_commitment(&pda, rpc.commitment())
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?
        .value
        .ok_or_else(|| StormError::Parse(format!("bonding curve {pda} not found")))?;
    BondingCurve::unpack(&acct.data)
}

/// Fetch the raw token balance held by an SPL token account. Returns `0` if
/// the account does not exist (e.g. the creator never held the token).
pub async fn fetch_token_account_amount(
    rpc: &RpcContext,
    token_account: &Pubkey,
) -> Result<u64> {
    let maybe = rpc
        .rpc()
        .get_account_with_commitment(token_account, rpc.commitment())
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?
        .value;
    match maybe {
        None => Ok(0),
        Some(acct) => Ok(TokenAccountSnapshot::unpack(*token_account, &acct.data)?.amount),
    }
}

/// Fetch the top-20 holder list for `mint` via `getTokenLargestAccounts`
/// (one RPC call) and convert it to plain [`HolderBalance`] entries.
pub async fn fetch_top_holders(
    rpc: &RpcContext,
    mint: &Pubkey,
) -> Result<Vec<HolderBalance>> {
    let raw = rpc
        .rpc()
        .get_token_largest_accounts(mint)
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?;
    raw.iter().map(holder_balance_from_rpc).collect()
}

/// Fetch a single bounded `getSignaturesForAddress` page for `wallet` and
/// summarise it. One RPC call; never a full-history crawl.
pub async fn fetch_signature_page(
    rpc: &RpcContext,
    wallet: &Pubkey,
) -> Result<SignaturePage> {
    let config = GetConfirmedSignaturesForAddress2Config {
        before: None,
        until: None,
        limit: Some(SIGNATURE_PAGE_LIMIT),
        commitment: Some(rpc.commitment()),
    };
    let sigs = rpc
        .rpc()
        .get_signatures_for_address_with_config(wallet, config)
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?;
    Ok(signature_page_from_rpc(&sigs))
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_account_decoder_client_types::token::UiTokenAmount;

    fn ui_amount(raw: &str) -> UiTokenAmount {
        UiTokenAmount {
            ui_amount: None,
            decimals: 6,
            amount: raw.to_string(),
            ui_amount_string: String::new(),
        }
    }

    #[test]
    fn holder_balance_parses_address_and_amount() {
        let entry = RpcTokenAccountBalance {
            address: "So11111111111111111111111111111111111111112".to_string(),
            amount: ui_amount("123456789"),
        };
        let hb = holder_balance_from_rpc(&entry).unwrap();
        assert_eq!(hb.amount, 123_456_789);
        assert_eq!(
            hb.address,
            Pubkey::from_str("So11111111111111111111111111111111111111112").unwrap()
        );
    }

    #[test]
    fn holder_balance_rejects_bad_address() {
        let entry = RpcTokenAccountBalance {
            address: "not-a-pubkey".to_string(),
            amount: ui_amount("1"),
        };
        match holder_balance_from_rpc(&entry) {
            Err(StormError::Parse(m)) => assert!(m.contains("holder address")),
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn holder_balance_rejects_bad_amount() {
        let entry = RpcTokenAccountBalance {
            address: "So11111111111111111111111111111111111111112".to_string(),
            amount: ui_amount("not-a-number"),
        };
        match holder_balance_from_rpc(&entry) {
            Err(StormError::Parse(m)) => assert!(m.contains("holder amount")),
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    fn sig(slot: u64, block_time: Option<i64>) -> RpcConfirmedTransactionStatusWithSignature {
        RpcConfirmedTransactionStatusWithSignature {
            signature: format!("sig{slot}"),
            slot,
            err: None,
            memo: None,
            block_time,
            confirmation_status: None,
        }
    }

    #[test]
    fn signature_page_counts_and_takes_oldest_block_time() {
        // Newest-first: index 0 is newest, the last element is oldest.
        let sigs = vec![sig(300, Some(3000)), sig(200, Some(2000)), sig(100, Some(1000))];
        let page = signature_page_from_rpc(&sigs);
        assert_eq!(page.signature_count, 3);
        assert_eq!(page.oldest_block_time, Some(1000));
    }

    #[test]
    fn signature_page_handles_empty_input() {
        let page = signature_page_from_rpc(&[]);
        assert_eq!(page.signature_count, 0);
        assert_eq!(page.oldest_block_time, None);
    }

    #[test]
    fn signature_page_handles_missing_oldest_block_time() {
        let sigs = vec![sig(200, Some(2000)), sig(100, None)];
        let page = signature_page_from_rpc(&sigs);
        assert_eq!(page.signature_count, 2);
        assert_eq!(page.oldest_block_time, None);
    }
}
```

- [ ] **Step 2: Add the test-only dependency for `UiTokenAmount`**

The conversion test constructs a `UiTokenAmount`. That type lives in
`solana-account-decoder-client-types` (a transitive dep of `solana-client`);
add it as a `dev-dependency` so the test can name it. In
`crates/storm-features/Cargo.toml`, replace the `[dev-dependencies]` section
with:

```toml
[dev-dependencies]
tokio = { workspace = true, features = ["macros", "rt-multi-thread"] }
solana-account-decoder-client-types = "2"
```

- [ ] **Step 3: Run it to verify it passes**

Run: `cargo test -p storm-features fetch`
Expected: PASS — all six conversion tests. (`cargo` resolves
`solana-account-decoder-client-types` 2.x; it is already in `Cargo.lock` as a
transitive dependency, so no new download.)

If the test fails to compile on the `UiTokenAmount` import, confirm the field
set against `Cargo.lock`: it is
`{ ui_amount: Option<f64>, decimals: u8, amount: String, ui_amount_string: String }`
in `solana-account-decoder-client-types` 2.3.13.

- [ ] **Step 4: Run clippy on the crate**

Run: `cargo clippy -p storm-features --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add crates/storm-features/src/fetch.rs crates/storm-features/Cargo.toml Cargo.lock
git commit -m "Add RPC orchestration layer and conversion helpers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `FeatureVector` and the `extract_features` entry point

**Files:**
- Modify: `crates/storm-features/src/lib.rs`
- Test: in `lib.rs` (pure `FeatureVector` assembly — no network)

`FeatureVector` aggregates the five Lean-v1 feature groups plus the token mint
it describes. `extract_features` is the public async entry point: given the mint
and its PumpSwap pool address, it fetches every account / list / page via
`fetch.rs`, calls the five pure functions, and assembles the `FeatureVector`.

`extract_features` takes the **pool address as an explicit argument** rather
than discovering it. Graduation detection and pool discovery are
`storm-pumpfun` / `storm-collector` concerns; this crate computes features for
an *already-identified* graduated token, and the caller (the collector) already
holds the pool address from graduation detection. The deployer wallet, by
contrast, is *not* a caller input — `extract_features` reads it from
`BondingCurve::creator` (the authoritative deployer field) after fetching the
bonding curve.

A pure `assemble_feature_vector` helper builds the struct from the five
already-computed group structs so it can be unit-tested without network.

- [ ] **Step 1: Write the failing test**

Replace the contents of `crates/storm-features/src/lib.rs` with:

```rust
//! Survival-prediction feature extraction for graduated pump.fun tokens.
//!
//! Given a graduated token mint and its PumpSwap pool, [`extract_features`]
//! fetches the needed on-chain data via Solana RPC and computes a Lean-v1
//! [`FeatureVector`].
//!
//! The crate is split in two layers:
//!
//! * **Pure compute** — [`contract`], [`curve`], [`liquidity`], [`holders`],
//!   [`deployer`]: each takes already-fetched plain-data inputs and returns
//!   feature values. No network, no `solana-client` types — unit-tested
//!   against synthetic data.
//! * **RPC orchestration** — [`fetch`]: the only module that touches the
//!   network. It fetches accounts / the holder list / one signature page and
//!   feeds the pure functions.

pub mod contract;
pub mod curve;
pub mod deployer;
pub mod fetch;
pub mod holders;
pub mod liquidity;

use solana_sdk::pubkey::Pubkey;
use storm_core::Result;
use storm_solana::RpcContext;

pub use contract::ContractFlags;
pub use curve::CurveSnapshot;
pub use deployer::DeployerSignals;
pub use holders::HolderFeatures;
pub use liquidity::LiquidityFeatures;

/// The Lean-v1 survival-prediction feature vector for one graduated token.
#[derive(Debug, Clone)]
pub struct FeatureVector {
    /// The graduated token mint these features describe.
    pub mint: Pubkey,
    /// Liquidity feature group (PumpSwap pool reserves, LP burn).
    pub liquidity: LiquidityFeatures,
    /// Contract-flag feature group (mint / freeze authority presence).
    pub contract: ContractFlags,
    /// Holder-distribution feature group (top-N concentration, dev bag).
    pub holders: HolderFeatures,
    /// Bonding-curve snapshot feature group.
    pub curve: CurveSnapshot,
    /// Deployer-signal feature group (bounded signature page).
    pub deployer: DeployerSignals,
}

/// Assemble a [`FeatureVector`] from the five already-computed group structs.
/// Pure — no I/O; unit-testable.
fn assemble_feature_vector(
    mint: Pubkey,
    liquidity: LiquidityFeatures,
    contract: ContractFlags,
    holders: HolderFeatures,
    curve: CurveSnapshot,
    deployer: DeployerSignals,
) -> FeatureVector {
    FeatureVector {
        mint,
        liquidity,
        contract,
        holders,
        curve,
        deployer,
    }
}

/// Extract the Lean-v1 [`FeatureVector`] for a graduated pump.fun token.
///
/// * `rpc` — a configured [`RpcContext`].
/// * `mint` — the graduated token mint.
/// * `pool` — the token's canonical PumpSwap pool address (held by the caller
///   from graduation detection — see `storm-pumpfun`).
/// * `now_unix` — the reference "now" timestamp (Unix seconds) for age-based
///   features. The caller passes the snapshot instant.
///
/// Issues a bounded set of RPC calls (mint, pool, two pool reserve accounts,
/// bonding curve, top-20 holders, creator token account, one signature page)
/// and computes every feature group. Network errors surface as
/// [`storm_core::StormError::Rpc`]; malformed accounts as `StormError::Parse`.
pub async fn extract_features(
    rpc: &RpcContext,
    mint: &Pubkey,
    pool: &Pubkey,
    now_unix: i64,
) -> Result<FeatureVector> {
    // --- contract flags + bonding curve --------------------------------
    let mint_info = fetch::fetch_mint(rpc, mint).await?;
    let contract = contract::contract_flags(&mint_info);

    let bonding_curve = fetch::fetch_bonding_curve(rpc, mint).await?;
    let curve = curve::curve_snapshot(&bonding_curve);
    let creator = bonding_curve.creator;

    // --- liquidity: pool record + its two reserve token accounts -------
    let pool_record = fetch::fetch_pool(rpc, pool).await?;
    let base_reserve =
        fetch::fetch_token_account_amount(rpc, &pool_record.pool_base_token_account).await?;
    let quote_reserve =
        fetch::fetch_token_account_amount(rpc, &pool_record.pool_quote_token_account).await?;
    let liquidity = liquidity::liquidity_features(&liquidity::PoolReserves {
        base_reserve,
        quote_reserve,
        lp_supply: pool_record.lp_supply,
        token_total_supply: mint_info.supply,
    });

    // --- holder distribution -------------------------------------------
    let top_holders = fetch::fetch_top_holders(rpc, mint).await?;
    let creator_ata =
        spl_associated_token_account_address(&creator, mint);
    let creator_balance =
        fetch::fetch_token_account_amount(rpc, &creator_ata).await?;
    let holders =
        holders::holder_features(&top_holders, mint_info.supply, creator_balance);

    // --- deployer signal -----------------------------------------------
    let page = fetch::fetch_signature_page(rpc, &creator).await?;
    let deployer = deployer::deployer_signals(&page, now_unix);

    Ok(assemble_feature_vector(
        *mint, liquidity, contract, holders, curve, deployer,
    ))
}

/// Derive the associated-token-account address holding `mint` for `owner`.
///
/// The ATA is a PDA of the Associated Token Account program; this matches
/// `spl_associated_token_account::get_associated_token_address` without
/// pulling in that crate. The seeds are `[owner, token_program, mint]`.
fn spl_associated_token_account_address(owner: &Pubkey, mint: &Pubkey) -> Pubkey {
    // Associated Token Account program ID (mainnet + devnet).
    const ATA_PROGRAM_ID: Pubkey =
        solana_sdk::pubkey!("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");
    Pubkey::find_program_address(
        &[
            owner.as_ref(),
            spl_token::id().as_ref(),
            mint.as_ref(),
        ],
        &ATA_PROGRAM_ID,
    )
    .0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    fn sample_liquidity() -> LiquidityFeatures {
        liquidity::liquidity_features(&liquidity::PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 0,
            token_total_supply: 1_000_000_000_000_000,
        })
    }

    fn sample_contract() -> ContractFlags {
        ContractFlags {
            mint_authority_present: false,
            freeze_authority_present: false,
        }
    }

    fn sample_holders() -> HolderFeatures {
        holders::holder_features(&[], 1_000_000_000_000_000, 0)
    }

    fn sample_curve() -> CurveSnapshot {
        CurveSnapshot {
            graduated: true,
            real_sol_reserves: 85_000_000_000,
            real_token_reserves: 0,
            token_total_supply: 1_000_000_000_000_000,
        }
    }

    fn sample_deployer() -> DeployerSignals {
        deployer::deployer_signals(
            &deployer::SignaturePage {
                signature_count: 7,
                oldest_block_time: Some(1_000),
            },
            10_000,
        )
    }

    #[test]
    fn assemble_carries_every_group_and_the_mint() {
        let mint = Pubkey::new_unique();
        let fv = assemble_feature_vector(
            mint,
            sample_liquidity(),
            sample_contract(),
            sample_holders(),
            sample_curve(),
            sample_deployer(),
        );
        assert_eq!(fv.mint, mint);
        assert!(fv.liquidity.lp_burned);
        assert!(!fv.contract.mint_authority_present);
        assert_eq!(fv.holders.visible_holder_count, 0);
        assert!(fv.curve.graduated);
        assert_eq!(fv.deployer.capped_signature_count, 7);
    }

    #[test]
    fn ata_derivation_is_deterministic_and_owner_specific() {
        let usdc =
            Pubkey::from_str("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").unwrap();
        let owner_a = Pubkey::new_unique();
        let owner_b = Pubkey::new_unique();

        // Same (owner, mint) → same ATA every time.
        assert_eq!(
            spl_associated_token_account_address(&owner_a, &usdc),
            spl_associated_token_account_address(&owner_a, &usdc),
        );
        // Different owners → different ATAs.
        assert_ne!(
            spl_associated_token_account_address(&owner_a, &usdc),
            spl_associated_token_account_address(&owner_b, &usdc),
        );
        // The same owner with a different mint also yields a different ATA.
        let other_mint = Pubkey::new_unique();
        assert_ne!(
            spl_associated_token_account_address(&owner_a, &usdc),
            spl_associated_token_account_address(&owner_a, &other_mint),
        );
    }
}
```

- [ ] **Step 2: Add the `spl-token` dependency**

`extract_features`'s ATA helper uses `spl_token::id()`. Add `spl-token` to the
crate's dependencies. In `crates/storm-features/Cargo.toml`, add to
`[dependencies]` after the `solana-sdk.workspace = true` line:

```toml
spl-token.workspace = true
```

The full `[dependencies]` section then reads:

```toml
[dependencies]
storm-core.workspace = true
storm-solana.workspace = true
storm-pumpfun.workspace = true
solana-client.workspace = true
solana-sdk.workspace = true
spl-token.workspace = true
tokio.workspace = true
```

- [ ] **Step 3: Run it to verify it passes**

Run: `cargo test -p storm-features --lib`
Expected: PASS — `assemble_carries_every_group_and_the_mint` and
`ata_derivation_is_deterministic_and_owner_specific`, plus every pure-module
test.

- [ ] **Step 4: Run clippy on the crate**

Run: `cargo clippy -p storm-features --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add crates/storm-features/src/lib.rs crates/storm-features/Cargo.toml Cargo.lock
git commit -m "Add FeatureVector and extract_features entry point

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Live-RPC integration test (`#[ignore]`-d)

**Files:**
- Create: `crates/storm-features/tests/integration.rs`

One end-to-end test that actually hits Solana RPC. It is marked `#[ignore]` so
`cargo test` (and CI) skip it; clippy `--all-targets` still type-checks it. It
is run manually against the graduated token `5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump`
("Pumpfun Pepe"), with `SOLANA_RPC_URL` loaded from `.env`.

The test needs the token's PumpSwap pool address. Pool *discovery* is out of
scope for this crate (it is a `storm-pumpfun` / `storm-collector` concern), and
the pool address is not a simple PDA of the mint alone, so the test takes it
from an environment variable `STORM_TEST_POOL`. The manual-run instructions
below explain how to obtain that address once (e.g. from a Solana explorer's
markets tab for the test mint).

- [ ] **Step 1: Create the integration test**

Create `crates/storm-features/tests/integration.rs`:

```rust
//! Live-RPC end-to-end test for `extract_features`.
//!
//! `#[ignore]`-d: it requires network and is never run by CI. Run it manually:
//!
//! ```text
//! set -a && . ./.env && set +a
//! export STORM_TEST_POOL=<pumpswap pool address for the test mint>
//! cargo test -p storm-features --test integration -- --ignored --nocapture
//! ```
//!
//! `SOLANA_RPC_URL` comes from `.env`. The test mint is the graduated token
//! "Pumpfun Pepe" (`5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump`).

use std::str::FromStr;
use std::time::{SystemTime, UNIX_EPOCH};

use solana_sdk::pubkey::Pubkey;
use storm_core::SolanaConfig;
use storm_features::extract_features;
use storm_solana::RpcContext;

/// Graduated pump.fun token used for the manual integration check.
const TEST_MINT: &str = "5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump";

#[tokio::test]
#[ignore = "hits live Solana RPC; run manually with SOLANA_RPC_URL + STORM_TEST_POOL set"]
async fn extract_features_against_a_real_graduated_token() {
    let rpc_url = std::env::var("SOLANA_RPC_URL")
        .expect("set SOLANA_RPC_URL (see .env) to run this test");
    let pool_str = std::env::var("STORM_TEST_POOL")
        .expect("set STORM_TEST_POOL to the test mint's PumpSwap pool address");

    let cfg = SolanaConfig {
        rpc_url,
        ws_url: String::new(),
        commitment: "confirmed".to_string(),
    };
    let rpc = RpcContext::from_config(&cfg);

    let mint = Pubkey::from_str(TEST_MINT).unwrap();
    let pool = Pubkey::from_str(&pool_str).expect("STORM_TEST_POOL is not a valid pubkey");
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;

    let fv = extract_features(&rpc, &mint, &pool, now)
        .await
        .expect("feature extraction failed");

    // The token graduated, so its bonding curve is complete.
    assert!(fv.curve.graduated, "test token should be graduated");
    // The mint and the feature vector agree.
    assert_eq!(fv.mint, mint);
    // The pool holds a non-zero base reserve (a live graduated pool has tokens).
    assert!(fv.liquidity.base_reserve > 0, "pool base reserve should be > 0");
    // getTokenLargestAccounts returns at most 20 holders.
    assert!(fv.holders.visible_holder_count <= 20);
    // Concentration fractions are well-formed.
    assert!((0.0..=1.0).contains(&fv.holders.top20_concentration));
    // The deployer wallet has at least one signature (it deployed the token).
    assert!(fv.deployer.capped_signature_count > 0);

    println!("FeatureVector for {TEST_MINT}:\n{fv:#?}");
}
```

- [ ] **Step 2: Verify the test compiles but is skipped**

Run: `cargo test -p storm-features --test integration`
Expected: compiles; the run reports `0 passed; 0 failed; 1 ignored` — CI-safe,
no network touched.

- [ ] **Step 3: Run clippy including the new test target**

Run: `cargo clippy -p storm-features --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add crates/storm-features/tests/integration.rs
git commit -m "Add ignored live-RPC integration test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Full workspace verification (the CI gate)

**Files:** none — verification only.

- [ ] **Step 1: Build the whole workspace**

Run: `cargo build`
Expected: `Finished` — all crates, including `storm-features`.

- [ ] **Step 2: Run the full test suite (the CI `cargo test` gate)**

Run: `cargo test`
Expected: all tests pass — the existing crates' suites plus `storm-features`'s
pure-module tests (contract 3, curve 3, liquidity 5, holders 6, deployer 7,
fetch 6, lib 2). The `integration` test reports `1 ignored`. **No network is
touched.**

- [ ] **Step 3: Run clippy across the workspace (the CI clippy gate)**

Run: `cargo clippy --workspace --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Check formatting (the CI fmt gate)**

Run: `cargo fmt --check`
Expected: no output, exit 0. If it reports diffs, run `cargo fmt` and re-stage.

- [ ] **Step 5: (Optional, manual) Run the live integration test**

Only if a Solana RPC endpoint is available:

```bash
set -a && . ./.env && set +a
# Obtain the PumpSwap pool address for the test mint once — e.g. from a
# Solana explorer's "Markets"/"Pools" tab for 5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump
# — and export it:
export STORM_TEST_POOL=<pumpswap pool address>
cargo test -p storm-features --test integration -- --ignored --nocapture
```

Expected: `extract_features_against_a_real_graduated_token` passes and prints
the `FeatureVector`. This step is **not** part of CI and not required for the
plan's done criteria.

- [ ] **Step 6: Commit (only if Step 4 required a `cargo fmt` fix)**

If `cargo fmt` changed any file:

```bash
git add -A
git commit -m "Apply cargo fmt to storm-features

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Otherwise, no commit — Task 9 was the final code change.

---

## Done criteria

- `crates/storm-features` builds and is a registered workspace member.
- Five pure feature-group modules — `contract`, `curve`, `liquidity`,
  `holders`, `deployer` — each with real TDD unit tests over synthetic data,
  no network and no `solana-client` types.
- `fetch.rs` is the only network-touching module: one RPC call per fetch
  function, with pure conversion helpers (RPC types → plain inputs) that are
  unit-tested.
- `FeatureVector` aggregates all five Lean-v1 groups; `extract_features` is the
  public async entry point and assembles it from fetched + computed data.
- The Lean-v1 scope is implemented exactly — no transaction-history-crawl
  features (no trade microstructure, no deployer prior-token outcomes, no
  post-graduation behavior).
- `cargo test` passes without network; the one live-RPC test is `#[ignore]`-d.
- `cargo build`, `cargo test`, `cargo clippy --workspace --all-targets -- -D warnings`,
  and `cargo fmt --check` all pass at every commit.
