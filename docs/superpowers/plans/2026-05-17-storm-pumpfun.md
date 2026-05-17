# storm-pumpfun Crate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `storm-pumpfun` library crate that parses pump.fun bonding-curve and PumpSwap pool accounts, derives their addresses, and detects token graduation.

**Architecture:** A pure parsing/derivation library — no network I/O. Fixed-offset account parsers modelled on `storm-solana`'s `RaydiumPoolState`/`WhirlpoolState`, plus PDA derivation and graduation-detection predicates. Byte layouts are *verified against fixtures captured from a real graduated token* before any parser is written — no guessed offsets survive.

**Tech Stack:** Rust, `solana-sdk` (`Pubkey`, PDA derivation), `storm-core` (`Result`/`StormError`).

---

## Context

This is sub-plan 1 of Phase 1 (data foundation) for the pump.fun survival
strategy — see `docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md`
and milestone-#7 issue **#36**. `storm-features` and `storm-collector` build on
this crate later.

**On-chain facts (researched 2026-05-17):**

- pump.fun bonding-curve program: `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`
- PumpSwap AMM program: `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` (mainnet + devnet)
- `BondingCurve` account (~120 bytes): 8-byte Anchor discriminator, then
  `virtual_token_reserves`, `virtual_sol_reserves`, `real_token_reserves`,
  `real_sol_reserves`, `token_total_supply` (5×`u64`), `complete` (`bool`),
  `creator` (`Pubkey`). Bonding-curve PDA: `["bonding-curve", mint]`.
- PumpSwap `Pool` account: 8-byte discriminator, then `pool_bump` (`u8`),
  `index` (`u16`), `creator`, `base_mint`, `quote_mint`, `lp_mint`,
  `pool_base_token_account`, `pool_quote_token_account` (6×`Pubkey`),
  `lp_supply` (`u64`), `coin_creator` (`Pubkey`), `is_mayhem_mode` (`bool`).
- A **graduation pool** is identifiable: `index == 0` and `creator ==` the
  token's bonding-curve account.

The exact byte offsets above are the *expected sequential layout*. Task 2
captures real account bytes; Task 3+ parsers are TDD'd against them, so a wrong
offset fails a test immediately rather than shipping.

## Notes for the executor

- If `cargo` is not found, run `. "$HOME/.cargo/env"` first.
- `SOLANA_RPC_URL` is read from `.env` (Helius). For raw `curl` calls, run
  `set -a && . ./.env && set +a` first to load it into the shell.
- Follow the existing parser conventions in `crates/storm-solana/src/pools.rs`:
  exact-size checks, `StormError::Parse` on bad input, captured-bytes fixtures.

## File structure

| Path | Change | Responsibility |
|---|---|---|
| `crates/storm-pumpfun/Cargo.toml` | Create | crate manifest |
| `crates/storm-pumpfun/src/lib.rs` | Create | program-ID consts, byte-read helpers, module exports |
| `crates/storm-pumpfun/src/bonding_curve.rs` | Create | `BondingCurve` parsing + PDA derivation |
| `crates/storm-pumpfun/src/pumpswap.rs` | Create | PumpSwap `Pool` parsing |
| `crates/storm-pumpfun/src/graduation.rs` | Create | graduation-detection predicates |
| `crates/storm-pumpfun/tests/fixtures/bonding_curve.bin` | Create | captured real bonding-curve account bytes |
| `crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin` | Create | captured real PumpSwap pool account bytes |
| `Cargo.toml` | Modify | add `storm-pumpfun` to `[workspace.dependencies]` |

---

### Task 1: Scaffold the `storm-pumpfun` crate

**Files:**
- Create: `crates/storm-pumpfun/Cargo.toml`, `crates/storm-pumpfun/src/lib.rs`
- Modify: `Cargo.toml`

- [ ] **Step 1: Create `crates/storm-pumpfun/Cargo.toml`**

```toml
[package]
name = "storm-pumpfun"
version = "0.1.0"
edition.workspace = true
license.workspace = true
publish.workspace = true
authors.workspace = true
repository.workspace = true

[dependencies]
storm-core.workspace = true
solana-sdk.workspace = true
```

- [ ] **Step 2: Create `crates/storm-pumpfun/src/lib.rs`**

```rust
//! pump.fun bonding-curve and PumpSwap account parsing + graduation detection.
//!
//! A pure parsing/derivation library — no network I/O. Callers fetch raw
//! account bytes (via `storm-solana`) and hand them here.

use solana_sdk::pubkey::Pubkey;

/// pump.fun bonding-curve program.
pub const PUMPFUN_PROGRAM_ID: Pubkey =
    solana_sdk::pubkey!("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");

/// PumpSwap AMM program — graduation destination since ~March 2025.
pub const PUMPSWAP_PROGRAM_ID: Pubkey =
    solana_sdk::pubkey!("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");

// ---- little-endian byte readers (crate-internal) --------------------------

pub(crate) fn read_u16(data: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes(data[offset..offset + 2].try_into().unwrap())
}

pub(crate) fn read_u64(data: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(data[offset..offset + 8].try_into().unwrap())
}

pub(crate) fn read_pubkey(data: &[u8], offset: usize) -> Pubkey {
    Pubkey::try_from(&data[offset..offset + 32]).unwrap()
}
```

- [ ] **Step 3: Register the crate in the workspace**

In the root `Cargo.toml`, add to `[workspace.dependencies]` after the
`storm-store` line:

```toml
storm-pumpfun = { path = "crates/storm-pumpfun" }
```

(The `members = ["crates/*", "bins/*"]` glob already includes the new crate.)

- [ ] **Step 4: Verify it builds**

Run: `cargo build -p storm-pumpfun`
Expected: `Finished`. `cargo` also prints `dead_code` warnings for the three
byte-reader helpers — expected; Tasks 4–5 consume them and the warnings clear.
(Clippy is gated only in Task 7, by which point all three are used.)

- [ ] **Step 5: Commit**

```bash
git add crates/storm-pumpfun/Cargo.toml crates/storm-pumpfun/src/lib.rs Cargo.toml Cargo.lock
git commit -m "Scaffold storm-pumpfun crate

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Capture real account fixtures

This task produces the ground-truth bytes every parser is tested against.
No Rust is written here.

**Files:**
- Create: `crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin`
- Create: `crates/storm-pumpfun/tests/fixtures/bonding_curve.bin`
- Create: `crates/storm-pumpfun/tests/fixtures/NOTES.md`

- [ ] **Step 1: Pick a graduated token and find its PumpSwap pool**

Open a Solana DEX explorer (DexScreener, Solscan, or pump.fun's "graduated"
filter). Choose any token that graduated to PumpSwap **at least 24h ago**
(stable, won't change mid-capture). Record two addresses:
- the token **mint** address
- its **PumpSwap pool** address (the pair/pool account)

Write both into `crates/storm-pumpfun/tests/fixtures/NOTES.md` under headings
`mint:` and `pool:`.

- [ ] **Step 2: Capture the pool account bytes**

```bash
set -a && . ./.env && set +a
POOL=<pool address from Step 1>
curl -s "$SOLANA_RPC_URL" -X POST -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getAccountInfo\",\"params\":[\"$POOL\",{\"encoding\":\"base64\"}]}" \
  | jq -r '.result.value.data[0]' | base64 -d > crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin
wc -c < crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin
```

Expected: a non-zero byte count (record it in `NOTES.md` as `pool bytes:`).

- [ ] **Step 3: Extract the bonding-curve address from the pool bytes**

The pool's `creator` field is the bonding-curve account. It is the 32 bytes at
offset 11 (after 8-byte discriminator + `pool_bump` u8 + `index` u16):

```bash
python3 -c "import base58; d=open('crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin','rb').read(); print('bonding curve:', base58.b58encode(d[11:43]).decode())"
```

If `base58` is unavailable: `pip install base58` (or use any base58 tool).
Record the printed address in `NOTES.md` as `bonding_curve:`.

- [ ] **Step 4: Capture the bonding-curve account bytes**

```bash
set -a && . ./.env && set +a
BC=<bonding curve address from Step 3>
curl -s "$SOLANA_RPC_URL" -X POST -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"getAccountInfo\",\"params\":[\"$BC\",{\"encoding\":\"base64\"}]}" \
  | jq -r '.result.value.data[0]' | base64 -d > crates/storm-pumpfun/tests/fixtures/bonding_curve.bin
wc -c < crates/storm-pumpfun/tests/fixtures/bonding_curve.bin
```

Expected: ~120 bytes (record it in `NOTES.md` as `bonding_curve bytes:`).

- [ ] **Step 5: Record the verified layout**

`xxd crates/storm-pumpfun/tests/fixtures/bonding_curve.bin` and confirm the
field order matches the Context section. In `NOTES.md`, write the confirmed
offset of each field. The parsers in Tasks 4–5 use these confirmed offsets.

- [ ] **Step 6: Commit**

```bash
git add crates/storm-pumpfun/tests/fixtures/
git commit -m "Add captured pump.fun + PumpSwap account fixtures

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Bonding-curve PDA derivation

**Files:**
- Create: `crates/storm-pumpfun/src/bonding_curve.rs`
- Modify: `crates/storm-pumpfun/src/lib.rs`
- Test: in `bonding_curve.rs` (`#[cfg(test)]` module)

- [ ] **Step 1: Write the failing test**

In a new file `crates/storm-pumpfun/src/bonding_curve.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    // From tests/fixtures/NOTES.md — replace with the captured mint and its
    // bonding-curve address from Task 2.
    const FIXTURE_MINT: &str = "<mint from NOTES.md>";
    const FIXTURE_BONDING_CURVE: &str = "<bonding_curve from NOTES.md>";

    #[test]
    fn pda_matches_real_bonding_curve() {
        let mint = Pubkey::from_str(FIXTURE_MINT).unwrap();
        let expected = Pubkey::from_str(FIXTURE_BONDING_CURVE).unwrap();
        assert_eq!(bonding_curve_pda(&mint), expected);
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-pumpfun bonding_curve`
Expected: FAIL — `bonding_curve_pda` is not defined.

- [ ] **Step 3: Implement `bonding_curve_pda`**

At the top of `crates/storm-pumpfun/src/bonding_curve.rs`:

```rust
use solana_sdk::pubkey::Pubkey;

use crate::PUMPFUN_PROGRAM_ID;

/// Derive the bonding-curve account address for a token mint.
pub fn bonding_curve_pda(mint: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[b"bonding-curve", mint.as_ref()],
        &PUMPFUN_PROGRAM_ID,
    )
    .0
}
```

Add to `crates/storm-pumpfun/src/lib.rs` (after the program-ID consts, before
the byte readers):

```rust
pub mod bonding_curve;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-pumpfun bonding_curve`
Expected: PASS. If it fails, the seed is wrong — confirm against a second
fixture token before changing it.

- [ ] **Step 5: Commit**

```bash
git add crates/storm-pumpfun/src/bonding_curve.rs crates/storm-pumpfun/src/lib.rs
git commit -m "Add bonding-curve PDA derivation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `BondingCurve` account parser

**Files:**
- Modify: `crates/storm-pumpfun/src/bonding_curve.rs`
- Test: in `bonding_curve.rs`

- [ ] **Step 1: Write the failing test**

Add to the `#[cfg(test)] mod tests` in `bonding_curve.rs`:

```rust
    const BONDING_CURVE_FIXTURE: &[u8] =
        include_bytes!("../tests/fixtures/bonding_curve.bin");

    #[test]
    fn unpacks_real_bonding_curve() {
        let bc = BondingCurve::unpack(BONDING_CURVE_FIXTURE).unwrap();
        // A graduated token's curve is complete.
        assert!(bc.complete);
        // Reserves are non-zero u64s; total supply is the pump.fun standard
        // 1B tokens at 6 decimals = 1_000_000_000_000_000.
        assert_eq!(bc.token_total_supply, 1_000_000_000_000_000);
        // creator is a real (non-default) pubkey.
        assert_ne!(bc.creator, Pubkey::default());
    }

    #[test]
    fn unpack_rejects_short_data() {
        assert!(BondingCurve::unpack(&[0u8; 40]).is_err());
    }
```

If `NOTES.md` shows a different `token_total_supply`, use that value instead.

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-pumpfun bonding_curve`
Expected: FAIL — `BondingCurve` is not defined.

- [ ] **Step 3: Implement the parser**

Add to `crates/storm-pumpfun/src/bonding_curve.rs` (above the test module),
using the offsets confirmed in Task 2 Step 5:

```rust
use storm_core::{Result, StormError};

use crate::{read_pubkey, read_u64};

/// Parsed pump.fun bonding-curve account.
#[derive(Debug, Clone)]
pub struct BondingCurve {
    pub virtual_token_reserves: u64,
    pub virtual_sol_reserves: u64,
    pub real_token_reserves: u64,
    pub real_sol_reserves: u64,
    pub token_total_supply: u64,
    /// True once the curve has filled and the token has graduated.
    pub complete: bool,
    pub creator: Pubkey,
}

impl BondingCurve {
    /// Minimum meaningful length: 8-byte discriminator + 5×u64 + bool + Pubkey.
    pub const MIN_LEN: usize = 8 + 40 + 1 + 32;

    /// Parse a bonding-curve account. Trailing bytes (Anchor reserves the
    /// account at ~120 bytes) are ignored.
    pub fn unpack(data: &[u8]) -> Result<Self> {
        if data.len() < Self::MIN_LEN {
            return Err(StormError::Parse(format!(
                "bonding curve: expected >= {} bytes, got {}",
                Self::MIN_LEN,
                data.len()
            )));
        }
        Ok(Self {
            virtual_token_reserves: read_u64(data, 8),
            virtual_sol_reserves: read_u64(data, 16),
            real_token_reserves: read_u64(data, 24),
            real_sol_reserves: read_u64(data, 32),
            token_total_supply: read_u64(data, 40),
            complete: data[48] != 0,
            creator: read_pubkey(data, 49),
        })
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-pumpfun bonding_curve`
Expected: PASS. If `unpacks_real_bonding_curve` fails, `xxd` the fixture and
correct the offsets against the Task 2 Step 5 layout.

- [ ] **Step 5: Commit**

```bash
git add crates/storm-pumpfun/src/bonding_curve.rs
git commit -m "Parse pump.fun BondingCurve accounts

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: PumpSwap `Pool` account parser

**Files:**
- Create: `crates/storm-pumpfun/src/pumpswap.rs`
- Modify: `crates/storm-pumpfun/src/lib.rs`
- Test: in `pumpswap.rs`

- [ ] **Step 1: Write the failing test**

Create `crates/storm-pumpfun/src/pumpswap.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    const POOL_FIXTURE: &[u8] = include_bytes!("../tests/fixtures/pumpswap_pool.bin");

    #[test]
    fn unpacks_real_pool() {
        let pool = PumpSwapPool::unpack(POOL_FIXTURE).unwrap();
        // A canonical graduation pool has index 0.
        assert_eq!(pool.index, 0);
        // The two reserve token accounts are real pubkeys.
        assert_ne!(pool.pool_base_token_account, Pubkey::default());
        assert_ne!(pool.pool_quote_token_account, Pubkey::default());
    }

    #[test]
    fn unpack_rejects_short_data() {
        assert!(PumpSwapPool::unpack(&[0u8; 50]).is_err());
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-pumpfun pumpswap`
Expected: FAIL — `PumpSwapPool` is not defined.

- [ ] **Step 3: Implement the parser**

Add to the top of `crates/storm-pumpfun/src/pumpswap.rs`, using the offsets
confirmed in Task 2:

```rust
use solana_sdk::pubkey::Pubkey;
use storm_core::{Result, StormError};

use crate::{read_pubkey, read_u16, read_u64};

/// Parsed PumpSwap `Pool` account.
#[derive(Debug, Clone)]
pub struct PumpSwapPool {
    pub pool_bump: u8,
    /// 0 for the canonical pool created by a bonding-curve graduation.
    pub index: u16,
    /// The pool creator — equals the bonding-curve account for graduations.
    pub creator: Pubkey,
    pub base_mint: Pubkey,
    pub quote_mint: Pubkey,
    pub lp_mint: Pubkey,
    pub pool_base_token_account: Pubkey,
    pub pool_quote_token_account: Pubkey,
    pub lp_supply: u64,
    pub coin_creator: Pubkey,
    pub is_mayhem_mode: bool,
}

impl PumpSwapPool {
    /// 8-byte discriminator + u8 + u16 + 6×Pubkey + u64 + Pubkey + bool.
    pub const MIN_LEN: usize = 8 + 1 + 2 + (6 * 32) + 8 + 32 + 1;

    pub fn unpack(data: &[u8]) -> Result<Self> {
        if data.len() < Self::MIN_LEN {
            return Err(StormError::Parse(format!(
                "pumpswap pool: expected >= {} bytes, got {}",
                Self::MIN_LEN,
                data.len()
            )));
        }
        Ok(Self {
            pool_bump: data[8],
            index: read_u16(data, 9),
            creator: read_pubkey(data, 11),
            base_mint: read_pubkey(data, 43),
            quote_mint: read_pubkey(data, 75),
            lp_mint: read_pubkey(data, 107),
            pool_base_token_account: read_pubkey(data, 139),
            pool_quote_token_account: read_pubkey(data, 171),
            lp_supply: read_u64(data, 203),
            coin_creator: read_pubkey(data, 211),
            is_mayhem_mode: data[243] != 0,
        })
    }
}
```

Add `pub mod pumpswap;` to `crates/storm-pumpfun/src/lib.rs`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-pumpfun pumpswap`
Expected: PASS. If `unpacks_real_pool` fails, `xxd` `pumpswap_pool.bin` and
correct the offsets — the field order is fixed; only padding/offsets vary.

- [ ] **Step 5: Commit**

```bash
git add crates/storm-pumpfun/src/pumpswap.rs crates/storm-pumpfun/src/lib.rs
git commit -m "Parse PumpSwap Pool accounts

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Graduation-detection predicates

**Files:**
- Create: `crates/storm-pumpfun/src/graduation.rs`
- Modify: `crates/storm-pumpfun/src/lib.rs`
- Test: in `graduation.rs`

- [ ] **Step 1: Write the failing test**

Create `crates/storm-pumpfun/src/graduation.rs`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::bonding_curve::BondingCurve;
    use crate::pumpswap::PumpSwapPool;

    const BONDING_CURVE_FIXTURE: &[u8] =
        include_bytes!("../tests/fixtures/bonding_curve.bin");
    const POOL_FIXTURE: &[u8] = include_bytes!("../tests/fixtures/pumpswap_pool.bin");

    #[test]
    fn real_fixtures_are_a_graduated_pair() {
        let bc = BondingCurve::unpack(BONDING_CURVE_FIXTURE).unwrap();
        let pool = PumpSwapPool::unpack(POOL_FIXTURE).unwrap();
        // The pool's creator is the bonding-curve account; the captured pair
        // belongs together, so this is a canonical graduation.
        assert!(is_canonical_graduation(&pool, &pool.creator));
        assert!(bc.complete);
    }

    #[test]
    fn non_canonical_pool_is_rejected() {
        let pool = PumpSwapPool::unpack(POOL_FIXTURE).unwrap();
        let other = solana_sdk::pubkey::Pubkey::new_unique();
        assert!(!is_canonical_graduation(&pool, &other));
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-pumpfun graduation`
Expected: FAIL — `is_canonical_graduation` is not defined.

- [ ] **Step 3: Implement the predicates**

Add to the top of `crates/storm-pumpfun/src/graduation.rs`:

```rust
use solana_sdk::pubkey::Pubkey;

use crate::pumpswap::PumpSwapPool;

/// Index of the canonical pool created by a bonding-curve graduation.
pub const CANONICAL_POOL_INDEX: u16 = 0;

/// True if `pool` is the canonical PumpSwap pool created when the token whose
/// bonding curve is `bonding_curve` graduated: index 0, created by that curve.
pub fn is_canonical_graduation(pool: &PumpSwapPool, bonding_curve: &Pubkey) -> bool {
    pool.index == CANONICAL_POOL_INDEX && pool.creator == *bonding_curve
}
```

Add `pub mod graduation;` to `crates/storm-pumpfun/src/lib.rs`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-pumpfun graduation`
Expected: PASS — both tests.

- [ ] **Step 5: Commit**

```bash
git add crates/storm-pumpfun/src/graduation.rs crates/storm-pumpfun/src/lib.rs
git commit -m "Add graduation-detection predicates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Crate exports and full verification

**Files:**
- Modify: `crates/storm-pumpfun/src/lib.rs`

- [ ] **Step 1: Re-export the public types from `lib.rs`**

Add to `crates/storm-pumpfun/src/lib.rs` after the `pub mod` lines:

```rust
pub use bonding_curve::{bonding_curve_pda, BondingCurve};
pub use graduation::{is_canonical_graduation, CANONICAL_POOL_INDEX};
pub use pumpswap::PumpSwapPool;
```

- [ ] **Step 2: Verify the whole workspace builds and tests pass**

Run: `cargo build && cargo test`
Expected: `Finished`; all tests pass — the four existing crates' 38 tests plus
the new `storm-pumpfun` tests (PDA, both parsers, graduation).

- [ ] **Step 3: Run fmt and clippy (the CI gate)**

Run: `cargo fmt --check && cargo clippy --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add crates/storm-pumpfun/src/lib.rs
git commit -m "Export storm-pumpfun public API

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Done criteria

- `crates/storm-pumpfun` builds and is part of the workspace.
- `BondingCurve` and `PumpSwapPool` parse their captured fixtures correctly.
- `bonding_curve_pda` derives the real bonding-curve address for the fixture mint.
- `is_canonical_graduation` correctly identifies the captured graduated pair.
- `cargo build`, `cargo test`, `cargo fmt --check`, `cargo clippy` all pass.
- The crate does no network I/O — callers supply raw account bytes.
