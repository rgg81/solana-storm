# storm-store Schema Extension + storm-collector Daemon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `storm-store` with the survival-strategy schema (`graduations`, `feature_snapshots`, `outcomes`, `collector_state`) and build `storm-collector` — the always-on daemon that discovers newly-graduated pump.fun tokens, snapshots their features at T0+window, and records their outcomes once the outcome window matures.

**Architecture:** Two deliverables in one plan. (1) A `storm-store` extension — one new SQLite migration plus pure, plain-data row structs and `async` CRUD/query methods on the existing `Store`, all round-trip-tested against `sqlite::memory:`. (2) `storm-collector` — a new binary crate (`bins/storm-collector`) whose business logic is split into PURE, unit-tested decision functions (which graduations are due for a snapshot / an outcome, given timestamps) and a thin async I/O layer that wires `storm-pumpfun` graduation discovery, `storm_features::extract_features`, and the `Store`. Graduation discovery polls `getProgramAccounts` on the PumpSwap program for canonical (index-0) pools — a free-tier-friendly batch poll, no WebSocket and no paid indexer. The daemon is a slow batch loop: every cycle it discovers, snapshots due tokens, and records mature outcomes, then sleeps. Idempotency comes from a `UNIQUE` mint key plus a per-graduation lifecycle status persisted in `storm-store`, so a re-run never double-inserts.

**Tech Stack:** Rust, `sqlx` 0.8 (SQLite), `tokio` (async runtime, `signal`, `time`), `solana-client` 2.3 (`RpcClient::get_program_accounts_with_config`, `get_slot`), `solana-sdk` (`Pubkey`), `storm-core` (`Config`, `Result`/`StormError`, `next_backoff`), `storm-solana` (`RpcContext`), `storm-pumpfun` (`PumpSwapPool`, `PUMPSWAP_PROGRAM_ID`, `bonding_curve_pda`), `storm-features` (`extract_features`, `FeatureVector`), `clap`, `dotenvy`, `tracing`.

---

## Context

This is **Phase 1, sub-plan 3 of 3** (data foundation) for the pump.fun survival
strategy — see `docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md`,
especially section 5 (the collection loop), section 6 (data & features), and
section 9 (architecture & build phases). Sub-plans 1 (`storm-pumpfun`) and 2
(`storm-features`) are merged to `main`; this plan builds directly on both.

The spec's section-9 component table is explicit: `storm-collector` is the
"always-on daemon: watch graduations → snapshot features at T0+window → record
outcomes", and `storm-store` is "extended: new migrations — `graduations`,
`feature_snapshots`, `outcomes`". SQLite is "the contract" between the Rust
collector and the (future) Python model layer: Rust writes feature snapshots and
outcomes, Python reads them. This plan delivers the Rust write side.

### Scope note — one plan, both deliverables

The `superpowers:writing-plans` scope check asks whether a schema extension AND a
daemon are too large for one plan. They are kept together here deliberately: the
collector's row types and the `Store` methods are a single tight contract, and
splitting them across two plans would force a fragile cross-plan type interface
(the collector cannot compile without the schema). The schema tasks (1–4) land
first and are independently testable; the collector tasks (5–11) build on them.
Each task is self-contained.

### The collection loop (spec section 5, made concrete)

Each daemon cycle runs three phases, then sleeps `cycle_interval` (default 30
minutes — the strategy is slow; latency is irrelevant):

1. **Discover** — `getProgramAccounts` on the PumpSwap program, filtered to
   canonical index-0 pools, yields candidate graduation pools. For each pool not
   already in `graduations`, insert a row with status `pending_snapshot`. T0 (the
   graduation time) is approximated by the pool's on-chain creation — see the
   "graduation discovery" section below.
2. **Snapshot** — for every `graduations` row in status `pending_snapshot` whose
   `detected_at + snapshot_window_secs` has elapsed (the spec's T0 + 6–24h
   observation window), call `storm_features::extract_features`, persist a
   `feature_snapshots` row, and advance the graduation to status
   `snapshot_done`. On extraction failure, log and leave the row `pending_snapshot`
   (it retries next cycle) — "skip-and-log" per spec section 9.
3. **Outcome** — for every `graduations` row in status `snapshot_done` whose
   `detected_at + outcome_window_secs` has elapsed (the spec's horizon N, ~1–4
   weeks; default 14 days), read the pool's current liquidity, classify the token
   as `survived` or `rugged`, persist an `outcomes` row, and advance the
   graduation to status `outcome_done` (terminal).

### Graduation discovery — the design decision

`storm-pumpfun` can *confirm* a known pool is a canonical graduation
(`is_canonical_graduation`), but the collector must *discover* freshly-graduated
tokens with no prior knowledge. Three mechanisms were evaluated:

| Option | How | Verdict |
|---|---|---|
| **A. `getProgramAccounts` poll on the PumpSwap program** | One RPC call per cycle: list PumpSwap `Pool` accounts, filtered server-side (`DataSize` + a `Memcmp` on the index-0 field) to canonical graduation pools. Diff against the `graduations` table to find new ones. | **Chosen.** No WebSocket, no paid indexer, no extra infra. One bounded call per 30-min cycle fits the free RPC budget. Idempotency is trivial — re-listing the same pools and `UNIQUE`-keying on mint means re-runs never double-insert. Matches the slow-batch nature of the strategy. |
| B. `logsSubscribe` on the pump.fun program (`migrate` instruction) | Persistent WebSocket; parse `migrate`-instruction logs in real time. | Rejected for v1. Real-time precision the strategy does not need (it snapshots at T0+6–24h anyway); a dropped connection loses graduations unless backfilled, reintroducing a poll. `storm-solana::ws` only supports `accountSubscribe`, so this needs new WS plumbing. Keep as a possible v2 latency optimization. |
| C. Public indexer / data API (Dune, Bitquery, Flipside) | Query a hosted dataset of recent graduations. | Rejected for the *live* collector. Adds an external dependency, an API key, and rate limits the project does not control. Section 9 already earmarks indexers for the *separate* historical-bootstrap effort — not the day-one live collector. |

**Chosen: Option A.** The discovery query uses two server-side filters so the RPC
node returns only graduation pools, keeping the response small:

- `RpcFilterType::DataSize(PUMPSWAP_POOL_ACCOUNT_LEN)` — only PumpSwap `Pool`
  accounts of the on-chain account size. **This is `301`, not
  `PumpSwapPool::MIN_LEN` (244).** `storm-pumpfun`'s `MIN_LEN` is the *minimum
  parseable* length (defined fields only); the real on-chain account is 301 bytes
  — 244 defined-field bytes plus 57 trailing reserved bytes — verified in
  `crates/storm-pumpfun/tests/fixtures/NOTES.md` (the captured fixture is exactly
  301 bytes). Filtering on `MIN_LEN` would match **zero** accounts, so the plan
  defines its own `PUMPSWAP_POOL_ACCOUNT_LEN = 301` constant for the filter.
- `RpcFilterType::Memcmp(Memcmp::new_raw_bytes(9, vec![0, 0]))` — the `index`
  field is a little-endian `u16` at offset 9 (8-byte discriminator + 1-byte
  `pool_bump`); canonical graduation pools have `index == 0`, i.e. bytes
  `[0x00, 0x00]`. (Offsets confirmed by `storm-pumpfun`'s `PumpSwapPool::unpack`:
  `pool_bump` at 8, `index` at 9.)

Every returned account is still parsed with `PumpSwapPool::unpack` and
re-validated by a pure helper before being accepted — the filters narrow the
result set; the parser is the source of truth.

**Important — what "canonical" can and cannot check.** `storm-pumpfun`'s
`is_canonical_graduation(&pool, &bonding_curve)` tests `pool.index == 0 &&
pool.creator == bonding_curve`. But — documented in `storm-pumpfun`'s
`tests/fixtures/NOTES.md` after capturing a real graduated token — **the pool's
`creator` field is the EOA wallet that created the pool, NOT the token's
bonding-curve PDA.** So `is_canonical_graduation` *cannot* be satisfied by passing
the derived `bonding_curve_pda(base_mint)`; that equality never holds for a real
graduation. (The existing `storm-pumpfun` graduation test only passes because it
feeds `&pool.creator` back as the bonding-curve argument, making the second
clause trivially true.) The collector therefore identifies a canonical
graduation pool by the signals that *are* genuine on the pool account itself:
`index == 0` **and** `quote_mint == wrapped SOL` (every pump.fun graduation pairs
the new token against wSOL — confirmed in NOTES.md). The token mint is the pool's
`base_mint`. The bonding-curve account is still derived as
`bonding_curve_pda(base_mint)` and stored on the `graduations` row — it is the
correct PDA and is what `storm_features::extract_features` re-derives internally;
it is simply *not* equal to `pool.creator`. (`extract_features` itself fetches the
bonding curve and only computes a sensible `FeatureVector` for a graduated
token — `curve.graduated` reflects the bonding curve's `complete` flag — so a
non-graduated pool that slipped through is still caught at snapshot time.)

**Honest caveat — T0 precision.** `getProgramAccounts` returns account *state*,
not creation slots. The collector approximates T0 with `detected_at` (the wall
clock when the discovery cycle first saw the pool). Because the daemon polls
continuously from day one, a pool is typically detected within one cycle (≤30
min) of graduating, so `detected_at` lags true T0 by at most one cycle. The
snapshot- and outcome-window timers are all measured from `detected_at`, so they
are internally consistent. The `graduations` row also stores `graduation_slot`
(the `getSlot` value at detection) for coarse provenance. This is good enough for
a slow strategy and is documented as a known approximation; a v2 could refine T0
by reading the pool-creation transaction.

### Outcome classification — the v1 rule

Spec section 11 lists the precise "survival" label as an open decision resolved
during implementation. This plan commits a **simple, explicit v1 rule**, kept as a
pure function so it is trivially tunable later:

> A token **survived** if, at the outcome check, its PumpSwap pool's quote
> reserve (wrapped-SOL lamports) is at least `survival_min_quote_lamports`
> (default `5_000_000_000` = 5 SOL). Otherwise it **rugged**.

5 SOL of pooled quote liquidity is a coarse "still honestly tradeable" floor —
a rugged pool is drained to near-zero. The threshold is config-driven and the
classifier is a pure, unit-tested function, so validation (Phase 3) can revisit
it without touching I/O code.

### `storm-store` schema being added

One new migration, `0002_survival.sql`, adds four tables. Following the existing
`0001_initial.sql` conventions: `TEXT` for `Pubkey`, `TEXT` for any `u64` (SQLite's
max integer is `i64`, and reserves/supply can exceed it), `INTEGER` Unix seconds
for timestamps, `unixepoch()` defaults for "recorded-at" columns.

- **`graduations`** — one row per discovered graduation. `mint TEXT UNIQUE` is the
  idempotency key; `status TEXT` drives the lifecycle (`pending_snapshot` →
  `snapshot_done` → `outcome_done`). Columns: `mint`, `pool_address`,
  `bonding_curve_address`, `graduation_slot`, `detected_at`, `status`.
- **`feature_snapshots`** — one row per graduation, foreign-keyed to it, holding
  the `storm_features::FeatureVector` flattened into columns plus the
  `snapshot_at` timestamp. One snapshot per graduation (`graduation_id UNIQUE`).
- **`outcomes`** — one row per graduation, foreign-keyed to it: the `survived`
  verdict plus the pool's `quote_reserve` / `base_reserve` at the outcome check
  and the `outcome_at` timestamp. One outcome per graduation
  (`graduation_id UNIQUE`).
- **`collector_state`** — a tiny single-row key/value table for daemon progress
  metadata (e.g. `last_cycle_at`). Not strictly required for correctness — the
  per-graduation `status` already makes re-runs idempotent — but it gives the
  daemon a cheap, queryable heartbeat and a place for future cursors.

### On-chain & API facts (verified against `Cargo.lock` — solana-client 2.3.13)

- `RpcContext::rpc()` returns `&solana_client::nonblocking::rpc_client::RpcClient`.
  The nonblocking client exposes:
  - `get_program_accounts_with_config(&Pubkey, RpcProgramAccountsConfig) -> ClientResult<Vec<(Pubkey, Account)>>`
  - `get_slot() -> ClientResult<u64>`
  - `get_account_with_commitment(&Pubkey, CommitmentConfig) -> ClientResult<Response<Option<Account>>>`
- Config / filter types (re-exported by `solana-client`):
  - `use solana_client::rpc_config::{RpcProgramAccountsConfig, RpcAccountInfoConfig};`
    — `RpcProgramAccountsConfig { filters: Option<Vec<RpcFilterType>>, account_config: RpcAccountInfoConfig, with_context: Option<bool>, sort_results: Option<bool> }`;
    `RpcAccountInfoConfig { encoding: Option<UiAccountEncoding>, data_slice: Option<UiDataSliceConfig>, commitment: Option<CommitmentConfig>, min_context_slot: Option<Slot> }`.
    Both derive `Default`.
  - `use solana_client::rpc_filter::{RpcFilterType, Memcmp};` —
    `RpcFilterType::DataSize(u64)`, `RpcFilterType::Memcmp(Memcmp)`,
    `Memcmp::new_raw_bytes(offset: usize, bytes: Vec<u8>) -> Memcmp`.
- `storm_pumpfun::PumpSwapPool` fields used: `index: u16` (0 for canonical),
  `base_mint: Pubkey` (the graduated token), `quote_mint: Pubkey` (wrapped SOL
  for a graduation), `pool_base_token_account`, `pool_quote_token_account`,
  `lp_supply`. `creator: Pubkey` exists but is the pool-creator EOA, **not** the
  bonding curve (see the discovery section). `PumpSwapPool::MIN_LEN` (244) is the
  parser's *minimum-parseable* length, not the real account size (301) —
  discovery uses its own `PUMPSWAP_POOL_ACCOUNT_LEN = 301` constant for the
  `DataSize` filter. `storm_pumpfun::bonding_curve_pda(&Pubkey) -> Pubkey` derives
  the bonding-curve PDA.
- Wrapped SOL mint: `So11111111111111111111111111111111111111112` — the quote
  mint of every pump.fun graduation pool.
- `storm_features::extract_features(&RpcContext, &Pubkey /*mint*/, &Pubkey /*pool*/, i64 /*now_unix*/) -> Result<FeatureVector>`.
  `FeatureVector` fields: `mint`, `liquidity: LiquidityFeatures`,
  `contract: ContractFlags`, `holders: HolderFeatures`, `curve: CurveSnapshot`,
  `deployer: DeployerSignals` — every sub-field is `pub` (see `storm-features`'s
  `lib.rs` and module files).

## Notes for the executor

- If `cargo` is not found, run `. "$HOME/.cargo/env"` first.
- The repo CI (`.github/workflows/ci.yml`) runs four gates on every push/PR;
  **every commit must keep all four green**: `cargo fmt --all -- --check`,
  `cargo clippy --workspace --all-targets -- -D warnings`,
  `cargo check --workspace --all-targets`, `cargo test --workspace`.
- `cargo test` must NOT require network. PURE logic gets real TDD unit tests:
  `storm-store` round-trips run against `sqlite::memory:` (follow the existing
  `crates/storm-store/src/lib.rs` tests); the collector's pure decision functions
  (snapshot-due / outcome-due / outcome-classification) get synthetic-input unit
  tests. The one end-to-end path that hits Solana RPC lives in
  `bins/storm-collector/tests/integration.rs` and is `#[ignore]`-d — `cargo test`
  skips it; clippy `--all-targets` still type-checks it.
- `SOLANA_RPC_URL` is read from `.env` (gitignored); `Config::load()` reads
  `config/default.toml` and overrides `rpc_url` from that env var. `dotenvy::dotenv()`
  loads `.env` — mirror `bins/storm-cli/src/main.rs`.
- Follow `storm-store`'s style: plain-data row structs, `Pubkey`/`u64` stringified
  for SQLite, `StormError::Rpc(format!("…: {e}"))` for `sqlx` errors,
  `StormError::Parse` for malformed data. Follow `bins/storm-cli` for the binary
  layout (`#[tokio::main]`, `clap` derive, `dotenvy`, `tracing_subscriber`).

## File structure

| Path | Change | Responsibility |
|---|---|---|
| `crates/storm-store/migrations/0002_survival.sql` | Create | new tables: `graduations`, `feature_snapshots`, `outcomes`, `collector_state` |
| `crates/storm-store/src/lib.rs` | Modify | row structs + `Store` methods for the four new tables; the existing `pools`/`prices` code is untouched |
| `crates/storm-store/Cargo.toml` | Modify | no change needed (already depends on what the new code uses) — verified, listed for completeness |
| `bins/storm-collector/Cargo.toml` | Create | binary crate manifest |
| `bins/storm-collector/src/main.rs` | Create | entry point: load config, build deps, run the daemon loop until Ctrl-C |
| `bins/storm-collector/src/config.rs` | Create | `CollectorConfig` — cycle interval + window durations + thresholds, with defaults |
| `bins/storm-collector/src/schedule.rs` | Create | PURE decision functions: is a graduation due for a snapshot / an outcome, given timestamps |
| `bins/storm-collector/src/classify.rs` | Create | PURE outcome classification: pool liquidity → survived/rugged verdict |
| `bins/storm-collector/src/discover.rs` | Create | async: `getProgramAccounts` graduation discovery + pure pool-filtering helper |
| `bins/storm-collector/src/cycle.rs` | Create | async: one collection cycle — discover, snapshot due, record mature outcomes |
| `bins/storm-collector/tests/integration.rs` | Create | one `#[ignore]`-d live-RPC discovery test |
| `Cargo.toml` | Modify | no change — `members = ["crates/*", "bins/*"]` already globs the new bin; listed so the executor does not look for an edit |

The pure modules (`schedule`, `classify`, and the filtering helper in `discover`)
never touch I/O and are unit-tested with synthetic data. `discover` and `cycle`
are the only collector modules that touch RPC or the DB.

---

### Task 1: Add the `0002_survival.sql` migration

**Files:**
- Create: `crates/storm-store/migrations/0002_survival.sql`
- Test: in `crates/storm-store/src/lib.rs` (`#[cfg(test)]`)

`sqlx::migrate!("./migrations")` runs every `*.sql` file in the directory in
filename order; adding `0002_survival.sql` is picked up automatically. This task
adds the file and a test proving `migrate()` still succeeds (it now runs both
migrations) and the new tables exist.

- [ ] **Step 1: Write the failing test**

In `crates/storm-store/src/lib.rs`, add this test to the existing
`#[cfg(test)] mod tests` block (after `latest_price_for_unknown_pool_is_none`):

```rust
    #[tokio::test]
    async fn migration_0002_creates_survival_tables() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();
        // Each new table must exist and be queryable (count of an empty table is 0).
        for table in [
            "graduations",
            "feature_snapshots",
            "outcomes",
            "collector_state",
        ] {
            let count: (i64,) =
                sqlx::query_as(&format!("SELECT COUNT(*) FROM {table}"))
                    .fetch_one(&store.pool)
                    .await
                    .unwrap_or_else(|e| panic!("table {table} not queryable: {e}"));
            assert_eq!(count.0, 0, "{table} should start empty");
        }
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-store migration_0002`
Expected: FAIL — `no such table: graduations` (the migration file does not exist
yet).

- [ ] **Step 3: Create the migration file**

Create `crates/storm-store/migrations/0002_survival.sql`:

```sql
-- Survival-strategy schema (sub-plan 3). pump.fun graduated-token tracking:
-- a graduation is detected, a feature snapshot is taken at T0+window, and an
-- outcome is recorded once the outcome window matures.

-- graduations: one row per discovered pump.fun graduation.
-- `mint` is UNIQUE — the idempotency key; re-discovering a token never inserts
-- a second row. `status` drives the collector lifecycle:
--   'pending_snapshot' -> 'snapshot_done' -> 'outcome_done'.
CREATE TABLE IF NOT EXISTS graduations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    mint                   TEXT NOT NULL UNIQUE,
    pool_address           TEXT NOT NULL,
    bonding_curve_address  TEXT NOT NULL,
    graduation_slot        INTEGER NOT NULL,   -- getSlot value at detection (coarse provenance)
    detected_at            INTEGER NOT NULL,   -- Unix seconds; the collector's T0 approximation
    status                 TEXT NOT NULL DEFAULT 'pending_snapshot',
    created_at             INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS graduations_status ON graduations(status);

-- feature_snapshots: the storm_features FeatureVector for a graduation,
-- captured at T0+snapshot_window. One snapshot per graduation.
-- All raw u64 on-chain values are stored as TEXT (SQLite max integer is i64).
CREATE TABLE IF NOT EXISTS feature_snapshots (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    graduation_id             INTEGER NOT NULL UNIQUE REFERENCES graduations(id),
    snapshot_at               INTEGER NOT NULL,   -- Unix seconds the snapshot was taken
    -- liquidity group
    base_reserve              TEXT NOT NULL,      -- u64
    quote_reserve             TEXT NOT NULL,      -- u64
    lp_burned                 INTEGER NOT NULL,   -- 0 | 1
    pool_supply_fraction      REAL NOT NULL,
    -- contract-flags group
    mint_authority_present    INTEGER NOT NULL,   -- 0 | 1
    freeze_authority_present  INTEGER NOT NULL,   -- 0 | 1
    -- holder-distribution group
    visible_holder_count      INTEGER NOT NULL,
    top10_concentration       REAL NOT NULL,
    top20_concentration       REAL NOT NULL,
    creator_bag_fraction      REAL NOT NULL,
    -- bonding-curve-snapshot group
    curve_graduated           INTEGER NOT NULL,   -- 0 | 1
    curve_real_sol_reserves   TEXT NOT NULL,      -- u64
    curve_real_token_reserves TEXT NOT NULL,      -- u64
    curve_token_total_supply  TEXT NOT NULL,      -- u64
    -- deployer-signal group
    capped_signature_count    INTEGER NOT NULL,
    signature_count_capped    INTEGER NOT NULL,   -- 0 | 1
    oldest_signature_age_secs INTEGER,            -- nullable: None when unknown
    created_at                INTEGER NOT NULL DEFAULT (unixepoch())
);

-- outcomes: the recorded outcome for a graduation, taken once the outcome
-- window matured. One outcome per graduation.
CREATE TABLE IF NOT EXISTS outcomes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    graduation_id  INTEGER NOT NULL UNIQUE REFERENCES graduations(id),
    outcome_at     INTEGER NOT NULL,   -- Unix seconds the outcome was checked
    survived       INTEGER NOT NULL,   -- 0 = rugged, 1 = survived
    base_reserve   TEXT NOT NULL,      -- u64: pool base reserve at the check
    quote_reserve  TEXT NOT NULL,      -- u64: pool quote (wSOL) reserve at the check
    created_at     INTEGER NOT NULL DEFAULT (unixepoch())
);

-- collector_state: tiny key/value heartbeat / progress table for the daemon.
CREATE TABLE IF NOT EXISTS collector_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-store migration_0002`
Expected: PASS. (`migrate()` now runs `0001` then `0002`; the existing
`migrate_then_roundtrip_a_snapshot` test still passes too.)

- [ ] **Step 5: Run the full crate test + clippy**

Run: `cargo test -p storm-store && cargo clippy -p storm-store --all-targets -- -D warnings`
Expected: all `storm-store` tests pass; clippy clean.

- [ ] **Step 6: Commit**

```bash
git add crates/storm-store/migrations/0002_survival.sql crates/storm-store/src/lib.rs
git commit -m "Add survival-strategy schema migration to storm-store

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `graduations` row type + `Store` methods

**Files:**
- Modify: `crates/storm-store/src/lib.rs`
- Test: in `crates/storm-store/src/lib.rs`

Add a `GraduationRow` plain-data struct, a `GraduationStatus` enum, and four
`Store` methods: `insert_graduation` (idempotent — `ON CONFLICT(mint) DO NOTHING`),
`graduations_with_status` (the collector's work queue), `set_graduation_status`,
and `graduation_count`. Idempotency is the key requirement: re-inserting a
known mint must be a no-op, never an error and never a duplicate.

- [ ] **Step 1: Write the failing test**

In `crates/storm-store/src/lib.rs`, add to the `#[cfg(test)] mod tests` block:

```rust
    fn sample_graduation(mint: Pubkey) -> GraduationRow {
        GraduationRow {
            mint,
            pool_address: Pubkey::new_unique(),
            bonding_curve_address: Pubkey::new_unique(),
            graduation_slot: 250_000_000,
            detected_at: 1_779_000_000,
            status: GraduationStatus::PendingSnapshot,
        }
    }

    #[tokio::test]
    async fn graduation_insert_is_idempotent_on_mint() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let mint = Pubkey::new_unique();
        let grad = sample_graduation(mint);

        // First insert returns the new row id.
        let id1 = store.insert_graduation(&grad).await.unwrap();
        assert!(id1.is_some(), "first insert should return an id");

        // Re-inserting the SAME mint is a no-op: returns None, no duplicate row.
        let id2 = store.insert_graduation(&grad).await.unwrap();
        assert!(id2.is_none(), "duplicate mint insert should be a no-op");
        assert_eq!(store.graduation_count().await.unwrap(), 1);
    }

    #[tokio::test]
    async fn graduation_status_queue_and_transition() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let grad = sample_graduation(Pubkey::new_unique());
        let id = store.insert_graduation(&grad).await.unwrap().unwrap();

        // Freshly inserted -> appears in the pending_snapshot queue.
        let pending = store
            .graduations_with_status(GraduationStatus::PendingSnapshot)
            .await
            .unwrap();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].id, id);
        assert_eq!(pending[0].mint, grad.mint);
        assert_eq!(pending[0].pool_address, grad.pool_address);

        // Advance it; it leaves the pending queue and enters snapshot_done.
        store
            .set_graduation_status(id, GraduationStatus::SnapshotDone)
            .await
            .unwrap();
        assert!(store
            .graduations_with_status(GraduationStatus::PendingSnapshot)
            .await
            .unwrap()
            .is_empty());
        let done = store
            .graduations_with_status(GraduationStatus::SnapshotDone)
            .await
            .unwrap();
        assert_eq!(done.len(), 1);
        assert_eq!(done[0].id, id);
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-store graduation`
Expected: FAIL — `GraduationRow` / `GraduationStatus` / the methods are not
defined.

- [ ] **Step 3: Implement the row type, status enum, and methods**

In `crates/storm-store/src/lib.rs`, add after the `LatestPrice` struct (before
`impl Store`):

```rust
/// Lifecycle status of a tracked graduation. Drives the collector's state
/// machine: a graduation moves PendingSnapshot -> SnapshotDone -> OutcomeDone.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GraduationStatus {
    /// Discovered; waiting for the T0+window feature snapshot.
    PendingSnapshot,
    /// Feature snapshot taken; waiting for the outcome window to mature.
    SnapshotDone,
    /// Outcome recorded; terminal state.
    OutcomeDone,
}

impl GraduationStatus {
    /// The string persisted in the `graduations.status` column.
    pub fn as_str(&self) -> &'static str {
        match self {
            GraduationStatus::PendingSnapshot => "pending_snapshot",
            GraduationStatus::SnapshotDone => "snapshot_done",
            GraduationStatus::OutcomeDone => "outcome_done",
        }
    }

    /// Parse a `graduations.status` string back into the enum.
    ///
    /// Named `parse_status`, not `from_str`: an inherent `from_str` trips
    /// clippy's `should_implement_trait` lint (a hard error under
    /// `-D warnings`), and implementing the real `std::str::FromStr` trait would
    /// force an `Err` type other than the crate `Result`.
    pub fn parse_status(s: &str) -> Result<Self> {
        match s {
            "pending_snapshot" => Ok(GraduationStatus::PendingSnapshot),
            "snapshot_done" => Ok(GraduationStatus::SnapshotDone),
            "outcome_done" => Ok(GraduationStatus::OutcomeDone),
            other => Err(StormError::Parse(format!(
                "unknown graduation status '{other}'"
            ))),
        }
    }
}

/// A discovered pump.fun graduation, as stored in the `graduations` table.
#[derive(Debug, Clone)]
pub struct GraduationRow {
    /// The graduated token mint — the unique idempotency key.
    pub mint: Pubkey,
    /// The token's canonical PumpSwap pool address.
    pub pool_address: Pubkey,
    /// The token's bonding-curve account address.
    pub bonding_curve_address: Pubkey,
    /// `getSlot` value observed when the graduation was detected.
    pub graduation_slot: u64,
    /// Unix seconds the collector first detected the graduation (its T0).
    pub detected_at: i64,
    /// Lifecycle status.
    pub status: GraduationStatus,
}

/// A `graduations` row read back from the store, including its row id.
#[derive(Debug, Clone)]
pub struct StoredGraduation {
    /// The `graduations.id` primary key.
    pub id: i64,
    pub mint: Pubkey,
    pub pool_address: Pubkey,
    pub bonding_curve_address: Pubkey,
    pub graduation_slot: u64,
    pub detected_at: i64,
    pub status: GraduationStatus,
}
```

Then add these methods inside the existing `impl Store { … }` block (after
`latest_price`):

```rust
    /// Insert a discovered graduation. Idempotent: if a row with the same
    /// `mint` already exists this is a no-op and returns `Ok(None)`. On a
    /// fresh insert it returns `Ok(Some(new_row_id))`.
    pub async fn insert_graduation(&self, grad: &GraduationRow) -> Result<Option<i64>> {
        let res = sqlx::query(
            "INSERT INTO graduations \
             (mint, pool_address, bonding_curve_address, graduation_slot, detected_at, status) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6) \
             ON CONFLICT(mint) DO NOTHING",
        )
        .bind(grad.mint.to_string())
        .bind(grad.pool_address.to_string())
        .bind(grad.bonding_curve_address.to_string())
        .bind(grad.graduation_slot.to_string())
        .bind(grad.detected_at)
        .bind(grad.status.as_str())
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("insert graduation: {e}")))?;

        // rows_affected() is 0 when the ON CONFLICT clause suppressed the insert.
        if res.rows_affected() == 0 {
            Ok(None)
        } else {
            Ok(Some(res.last_insert_rowid()))
        }
    }

    /// All graduations currently in `status`, oldest-detected first — the
    /// collector's work queue for that lifecycle stage.
    pub async fn graduations_with_status(
        &self,
        status: GraduationStatus,
    ) -> Result<Vec<StoredGraduation>> {
        let rows: Vec<(i64, String, String, String, String, i64, String)> = sqlx::query_as(
            "SELECT id, mint, pool_address, bonding_curve_address, graduation_slot, \
                    detected_at, status \
             FROM graduations WHERE status = ?1 ORDER BY detected_at ASC, id ASC",
        )
        .bind(status.as_str())
        .fetch_all(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("graduations_with_status: {e}")))?;

        rows.into_iter()
            .map(|(id, mint, pool, bc, slot, detected_at, st)| {
                Ok(StoredGraduation {
                    id,
                    mint: parse_pubkey(&mint, "graduation mint")?,
                    pool_address: parse_pubkey(&pool, "graduation pool_address")?,
                    bonding_curve_address: parse_pubkey(&bc, "graduation bonding_curve_address")?,
                    graduation_slot: slot.parse().unwrap_or(0),
                    detected_at,
                    status: GraduationStatus::parse_status(&st)?,
                })
            })
            .collect()
    }

    /// Advance a graduation to a new lifecycle status.
    pub async fn set_graduation_status(
        &self,
        graduation_id: i64,
        status: GraduationStatus,
    ) -> Result<()> {
        sqlx::query("UPDATE graduations SET status = ?1 WHERE id = ?2")
            .bind(status.as_str())
            .bind(graduation_id)
            .execute(&self.pool)
            .await
            .map_err(|e| StormError::Rpc(format!("set_graduation_status: {e}")))?;
        Ok(())
    }

    /// Total number of rows in `graduations`.
    pub async fn graduation_count(&self) -> Result<i64> {
        let row: (i64,) = sqlx::query_as("SELECT COUNT(*) FROM graduations")
            .fetch_one(&self.pool)
            .await
            .map_err(|e| StormError::Rpc(format!("graduation_count: {e}")))?;
        Ok(row.0)
    }
```

Finally add this free function at the end of the file, **above** the
`#[cfg(test)]` module — a shared base58 parser so the read methods do not each
inline the same `map_err`:

```rust
/// Parse a base58 `Pubkey` stored as TEXT, attributing failures to `field`.
fn parse_pubkey(s: &str, field: &str) -> Result<Pubkey> {
    Pubkey::from_str(s)
        .map_err(|e| StormError::Parse(format!("invalid {field} pubkey '{s}': {e}")))
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-store graduation`
Expected: PASS — both `graduation_insert_is_idempotent_on_mint` and
`graduation_status_queue_and_transition`.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-store --all-targets -- -D warnings`
Expected: no output, exit 0. (The status parser is deliberately named
`parse_status`, not `from_str`: clippy's `should_implement_trait` lint flags an
inherent `from_str` as a hard error under `-D warnings`, so the non-trait name
keeps the gate green.)

- [ ] **Step 6: Commit**

```bash
git add crates/storm-store/src/lib.rs
git commit -m "Add graduations table row type and Store methods

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `feature_snapshots` row type + `Store` methods

**Files:**
- Modify: `crates/storm-store/src/lib.rs`
- Test: in `crates/storm-store/src/lib.rs`

Add a `FeatureSnapshotRow` plain-data struct that mirrors every column of the
`feature_snapshots` table (a flattened `storm_features::FeatureVector` — but
`storm-store` does **not** depend on `storm-features`; the collector flattens the
`FeatureVector` into this struct, keeping `storm-store` free of the feature crate).
Add `insert_feature_snapshot` and a `has_feature_snapshot` existence check (a
second idempotency guard so a crash mid-cycle cannot create a duplicate snapshot).

- [ ] **Step 1: Write the failing test**

In `crates/storm-store/src/lib.rs`, add to the `#[cfg(test)] mod tests` block:

```rust
    fn sample_snapshot(graduation_id: i64) -> FeatureSnapshotRow {
        FeatureSnapshotRow {
            graduation_id,
            snapshot_at: 1_779_050_000,
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_burned: true,
            pool_supply_fraction: 0.2,
            mint_authority_present: false,
            freeze_authority_present: false,
            visible_holder_count: 18,
            top10_concentration: 0.31,
            top20_concentration: 0.44,
            creator_bag_fraction: 0.05,
            curve_graduated: true,
            curve_real_sol_reserves: 85_000_000_000,
            curve_real_token_reserves: 0,
            curve_token_total_supply: 1_000_000_000_000_000,
            capped_signature_count: 7,
            signature_count_capped: false,
            oldest_signature_age_secs: Some(123_456),
        }
    }

    #[tokio::test]
    async fn feature_snapshot_round_trips() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let grad = sample_graduation(Pubkey::new_unique());
        let gid = store.insert_graduation(&grad).await.unwrap().unwrap();

        // No snapshot yet.
        assert!(!store.has_feature_snapshot(gid).await.unwrap());

        let snap = sample_snapshot(gid);
        store.insert_feature_snapshot(&snap).await.unwrap();

        // Now the existence guard reports it.
        assert!(store.has_feature_snapshot(gid).await.unwrap());

        // The persisted u64 fields survive the TEXT round-trip exactly.
        let (base, quote, supply, capped): (String, String, String, i64) = sqlx::query_as(
            "SELECT base_reserve, quote_reserve, curve_token_total_supply, \
                    capped_signature_count FROM feature_snapshots WHERE graduation_id = ?1",
        )
        .bind(gid)
        .fetch_one(&store.pool)
        .await
        .unwrap();
        assert_eq!(base, "200000000000000");
        assert_eq!(quote, "85000000000");
        assert_eq!(supply, "1000000000000000");
        assert_eq!(capped, 7);
    }

    #[tokio::test]
    async fn feature_snapshot_nullable_age_persists_as_null() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();
        let gid = store
            .insert_graduation(&sample_graduation(Pubkey::new_unique()))
            .await
            .unwrap()
            .unwrap();
        let mut snap = sample_snapshot(gid);
        snap.oldest_signature_age_secs = None;
        store.insert_feature_snapshot(&snap).await.unwrap();
        let age: (Option<i64>,) =
            sqlx::query_as("SELECT oldest_signature_age_secs FROM feature_snapshots WHERE graduation_id = ?1")
                .bind(gid)
                .fetch_one(&store.pool)
                .await
                .unwrap();
        assert_eq!(age.0, None);
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-store feature_snapshot`
Expected: FAIL — `FeatureSnapshotRow` / the methods are not defined.

- [ ] **Step 3: Implement the row type and methods**

In `crates/storm-store/src/lib.rs`, add after the `StoredGraduation` struct:

```rust
/// A flattened `storm_features::FeatureVector` ready for the `feature_snapshots`
/// table. `storm-store` deliberately does not depend on `storm-features`; the
/// collector flattens the `FeatureVector` into this plain struct.
#[derive(Debug, Clone)]
pub struct FeatureSnapshotRow {
    /// FK to the `graduations` row this snapshot describes.
    pub graduation_id: i64,
    /// Unix seconds the snapshot was taken.
    pub snapshot_at: i64,
    // liquidity group
    pub base_reserve: u64,
    pub quote_reserve: u64,
    pub lp_burned: bool,
    pub pool_supply_fraction: f64,
    // contract-flags group
    pub mint_authority_present: bool,
    pub freeze_authority_present: bool,
    // holder-distribution group
    pub visible_holder_count: i64,
    pub top10_concentration: f64,
    pub top20_concentration: f64,
    pub creator_bag_fraction: f64,
    // bonding-curve-snapshot group
    pub curve_graduated: bool,
    pub curve_real_sol_reserves: u64,
    pub curve_real_token_reserves: u64,
    pub curve_token_total_supply: u64,
    // deployer-signal group
    pub capped_signature_count: i64,
    pub signature_count_capped: bool,
    /// `None` when the deployer's oldest signature age is unknown.
    pub oldest_signature_age_secs: Option<i64>,
}
```

Then add these methods inside `impl Store { … }` (after `graduation_count`):

```rust
    /// Persist a feature snapshot for a graduation. The caller is expected to
    /// have checked `has_feature_snapshot` first; the `graduation_id UNIQUE`
    /// constraint is the hard backstop against duplicates.
    pub async fn insert_feature_snapshot(&self, row: &FeatureSnapshotRow) -> Result<()> {
        sqlx::query(
            "INSERT INTO feature_snapshots \
             (graduation_id, snapshot_at, base_reserve, quote_reserve, lp_burned, \
              pool_supply_fraction, mint_authority_present, freeze_authority_present, \
              visible_holder_count, top10_concentration, top20_concentration, \
              creator_bag_fraction, curve_graduated, curve_real_sol_reserves, \
              curve_real_token_reserves, curve_token_total_supply, capped_signature_count, \
              signature_count_capped, oldest_signature_age_secs) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, \
                     ?16, ?17, ?18, ?19)",
        )
        .bind(row.graduation_id)
        .bind(row.snapshot_at)
        .bind(row.base_reserve.to_string())
        .bind(row.quote_reserve.to_string())
        .bind(row.lp_burned as i64)
        .bind(row.pool_supply_fraction)
        .bind(row.mint_authority_present as i64)
        .bind(row.freeze_authority_present as i64)
        .bind(row.visible_holder_count)
        .bind(row.top10_concentration)
        .bind(row.top20_concentration)
        .bind(row.creator_bag_fraction)
        .bind(row.curve_graduated as i64)
        .bind(row.curve_real_sol_reserves.to_string())
        .bind(row.curve_real_token_reserves.to_string())
        .bind(row.curve_token_total_supply.to_string())
        .bind(row.capped_signature_count)
        .bind(row.signature_count_capped as i64)
        .bind(row.oldest_signature_age_secs)
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("insert feature snapshot: {e}")))?;
        Ok(())
    }

    /// True if a feature snapshot already exists for `graduation_id` — the
    /// collector's idempotency check before extracting features.
    pub async fn has_feature_snapshot(&self, graduation_id: i64) -> Result<bool> {
        let row: (i64,) = sqlx::query_as(
            "SELECT COUNT(*) FROM feature_snapshots WHERE graduation_id = ?1",
        )
        .bind(graduation_id)
        .fetch_one(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("has_feature_snapshot: {e}")))?;
        Ok(row.0 > 0)
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-store feature_snapshot`
Expected: PASS — both `feature_snapshot_round_trips` and
`feature_snapshot_nullable_age_persists_as_null`.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-store --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add crates/storm-store/src/lib.rs
git commit -m "Add feature_snapshots table row type and Store methods

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `outcomes` + `collector_state` row types and `Store` methods

**Files:**
- Modify: `crates/storm-store/src/lib.rs`
- Test: in `crates/storm-store/src/lib.rs`

Add `OutcomeRow` and the methods `insert_outcome` / `has_outcome`, plus a
`set_collector_state` / `get_collector_state` key-value pair for the daemon
heartbeat. `set_collector_state` is an upsert so writing the same key twice just
overwrites.

- [ ] **Step 1: Write the failing test**

In `crates/storm-store/src/lib.rs`, add to the `#[cfg(test)] mod tests` block:

```rust
    #[tokio::test]
    async fn outcome_round_trips_and_existence_check() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let gid = store
            .insert_graduation(&sample_graduation(Pubkey::new_unique()))
            .await
            .unwrap()
            .unwrap();

        assert!(!store.has_outcome(gid).await.unwrap());

        let outcome = OutcomeRow {
            graduation_id: gid,
            outcome_at: 1_780_000_000,
            survived: true,
            base_reserve: 150_000_000_000_000,
            quote_reserve: 60_000_000_000,
        };
        store.insert_outcome(&outcome).await.unwrap();
        assert!(store.has_outcome(gid).await.unwrap());

        let (survived, quote): (i64, String) = sqlx::query_as(
            "SELECT survived, quote_reserve FROM outcomes WHERE graduation_id = ?1",
        )
        .bind(gid)
        .fetch_one(&store.pool)
        .await
        .unwrap();
        assert_eq!(survived, 1);
        assert_eq!(quote, "60000000000");
    }

    #[tokio::test]
    async fn collector_state_is_an_upsert() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        // Unknown key -> None.
        assert_eq!(store.get_collector_state("last_cycle_at").await.unwrap(), None);

        store
            .set_collector_state("last_cycle_at", "1779000000")
            .await
            .unwrap();
        assert_eq!(
            store.get_collector_state("last_cycle_at").await.unwrap(),
            Some("1779000000".to_string())
        );

        // Writing the same key again overwrites, never duplicates.
        store
            .set_collector_state("last_cycle_at", "1779999999")
            .await
            .unwrap();
        assert_eq!(
            store.get_collector_state("last_cycle_at").await.unwrap(),
            Some("1779999999".to_string())
        );
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-store outcome collector_state`
Expected: FAIL — `OutcomeRow` / the methods are not defined.

- [ ] **Step 3: Implement the row type and methods**

In `crates/storm-store/src/lib.rs`, add after the `FeatureSnapshotRow` struct:

```rust
/// The recorded outcome for a graduation, as stored in the `outcomes` table.
#[derive(Debug, Clone)]
pub struct OutcomeRow {
    /// FK to the `graduations` row this outcome describes.
    pub graduation_id: i64,
    /// Unix seconds the outcome was checked.
    pub outcome_at: i64,
    /// `true` = the token survived; `false` = it rugged / died.
    pub survived: bool,
    /// Pool base-token reserve (raw units) at the outcome check.
    pub base_reserve: u64,
    /// Pool quote-token (wrapped SOL) reserve (lamports) at the outcome check.
    pub quote_reserve: u64,
}
```

Then add these methods inside `impl Store { … }` (after `has_feature_snapshot`):

```rust
    /// Persist the recorded outcome for a graduation. The `graduation_id UNIQUE`
    /// constraint backstops the collector's `has_outcome` idempotency check.
    pub async fn insert_outcome(&self, row: &OutcomeRow) -> Result<()> {
        sqlx::query(
            "INSERT INTO outcomes \
             (graduation_id, outcome_at, survived, base_reserve, quote_reserve) \
             VALUES (?1, ?2, ?3, ?4, ?5)",
        )
        .bind(row.graduation_id)
        .bind(row.outcome_at)
        .bind(row.survived as i64)
        .bind(row.base_reserve.to_string())
        .bind(row.quote_reserve.to_string())
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("insert outcome: {e}")))?;
        Ok(())
    }

    /// True if an outcome already exists for `graduation_id`.
    pub async fn has_outcome(&self, graduation_id: i64) -> Result<bool> {
        let row: (i64,) =
            sqlx::query_as("SELECT COUNT(*) FROM outcomes WHERE graduation_id = ?1")
                .bind(graduation_id)
                .fetch_one(&self.pool)
                .await
                .map_err(|e| StormError::Rpc(format!("has_outcome: {e}")))?;
        Ok(row.0 > 0)
    }

    /// Upsert a `collector_state` key/value pair (daemon heartbeat / progress).
    pub async fn set_collector_state(&self, key: &str, value: &str) -> Result<()> {
        sqlx::query(
            "INSERT INTO collector_state (key, value, updated_at) \
             VALUES (?1, ?2, unixepoch()) \
             ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        )
        .bind(key)
        .bind(value)
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("set_collector_state: {e}")))?;
        Ok(())
    }

    /// Read a `collector_state` value, or `None` if the key is absent.
    pub async fn get_collector_state(&self, key: &str) -> Result<Option<String>> {
        let row: Option<(String,)> =
            sqlx::query_as("SELECT value FROM collector_state WHERE key = ?1")
                .bind(key)
                .fetch_optional(&self.pool)
                .await
                .map_err(|e| StormError::Rpc(format!("get_collector_state: {e}")))?;
        Ok(row.map(|(v,)| v))
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-store outcome collector_state`
Expected: PASS — both `outcome_round_trips_and_existence_check` and
`collector_state_is_an_upsert`.

- [ ] **Step 5: Run the full crate gate**

Run: `cargo test -p storm-store && cargo clippy -p storm-store --all-targets -- -D warnings`
Expected: every `storm-store` test passes (the original `pools`/`prices` tests
plus all new survival-schema tests); clippy clean.

- [ ] **Step 6: Commit**

```bash
git add crates/storm-store/src/lib.rs
git commit -m "Add outcomes and collector_state Store methods

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Scaffold the `storm-collector` binary crate

**Files:**
- Create: `bins/storm-collector/Cargo.toml`, `bins/storm-collector/src/main.rs`
- Modify: none (`Cargo.toml`'s `members = ["crates/*", "bins/*"]` already globs it)

Create the binary crate with a minimal `main.rs` that compiles and runs — it
loads config and prints a startup line. Tasks 6–10 fill in the real daemon.

- [ ] **Step 1: Create `bins/storm-collector/Cargo.toml`**

```toml
[package]
name = "storm-collector"
version = "0.1.0"
edition.workspace = true
license.workspace = true
publish.workspace = true
authors.workspace = true
repository.workspace = true

[dependencies]
storm-core.workspace = true
storm-solana.workspace = true
storm-store.workspace = true
storm-pumpfun.workspace = true
storm-features.workspace = true

solana-client.workspace = true
solana-sdk.workspace = true
tokio.workspace = true
clap.workspace = true
anyhow.workspace = true
dotenvy.workspace = true
tracing.workspace = true
tracing-subscriber.workspace = true

[dev-dependencies]
tokio = { workspace = true, features = ["macros", "rt-multi-thread"] }
```

- [ ] **Step 2: Create `bins/storm-collector/src/main.rs`**

```rust
//! storm-collector — the always-on pump.fun survival-data daemon.
//!
//! Each cycle it discovers newly-graduated tokens, snapshots their features at
//! T0+window, and records outcomes once the outcome window matures. See
//! `docs/superpowers/plans/2026-05-17-storm-collector.md`.

use clap::Parser;
use storm_core::Config;

/// storm-collector command-line arguments.
#[derive(Parser)]
#[command(name = "storm-collector", about = "pump.fun survival-data collector", version)]
struct Cli {
    /// SQLite database URL.
    #[arg(long, default_value = "sqlite://./storm.db")]
    db: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Load .env (gitignored) so SOLANA_RPC_URL / Helius credentials are picked up.
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cli = Cli::parse();
    let cfg = Config::load()?;

    tracing::info!(db = %cli.db, rpc = %cfg.solana.rpc_url, "storm-collector starting");

    Ok(())
}
```

- [ ] **Step 3: Verify it builds and runs**

Run: `cargo run -p storm-collector -- --help`
Expected: `cargo` builds the crate and `clap` prints the help text showing the
`--db` option.

- [ ] **Step 4: Run clippy**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add bins/storm-collector/Cargo.toml bins/storm-collector/src/main.rs Cargo.lock
git commit -m "Scaffold storm-collector binary crate

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `CollectorConfig` — timings and thresholds

**Files:**
- Create: `bins/storm-collector/src/config.rs`
- Modify: `bins/storm-collector/src/main.rs`
- Test: in `bins/storm-collector/src/config.rs`

The daemon's tunables — cycle interval, the two window durations, and the
survival threshold — live in one struct with sensible defaults. The window
durations come from the spec: snapshot at T0+6–24h (default 12h), outcome at
horizon N ~1–4 weeks (default 14 days). All are overridable via environment
variables so the daemon can be retuned without a rebuild.

- [ ] **Step 1: Write the failing test**

Create `bins/storm-collector/src/config.rs`:

```rust
//! Daemon tunables — cycle interval, observation windows, survival threshold.

use std::time::Duration;

/// Tunable timings and thresholds for the collector daemon. Construct with
/// [`CollectorConfig::from_env`]; every field has a default and an env override.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CollectorConfig {
    /// Delay between collection cycles.
    pub cycle_interval: Duration,
    /// Observation window before a feature snapshot is taken (spec: T0+6–24h).
    pub snapshot_window: Duration,
    /// Window before an outcome is recorded (spec: horizon N, ~1–4 weeks).
    pub outcome_window: Duration,
    /// Minimum pool quote (wrapped-SOL lamports) reserve for a "survived"
    /// verdict at the outcome check.
    pub survival_min_quote_lamports: u64,
}

impl Default for CollectorConfig {
    fn default() -> Self {
        Self {
            cycle_interval: Duration::from_secs(30 * 60), // 30 minutes
            snapshot_window: Duration::from_secs(12 * 3600), // 12 hours
            outcome_window: Duration::from_secs(14 * 24 * 3600), // 14 days
            survival_min_quote_lamports: 5_000_000_000, // 5 SOL
        }
    }
}

impl CollectorConfig {
    /// Build from environment variables, falling back to [`Default`] for any
    /// unset or unparseable variable:
    ///
    /// * `STORM_CYCLE_INTERVAL_SECS`
    /// * `STORM_SNAPSHOT_WINDOW_SECS`
    /// * `STORM_OUTCOME_WINDOW_SECS`
    /// * `STORM_SURVIVAL_MIN_QUOTE_LAMPORTS`
    pub fn from_env() -> Self {
        let d = Self::default();
        Self {
            cycle_interval: env_secs("STORM_CYCLE_INTERVAL_SECS", d.cycle_interval),
            snapshot_window: env_secs("STORM_SNAPSHOT_WINDOW_SECS", d.snapshot_window),
            outcome_window: env_secs("STORM_OUTCOME_WINDOW_SECS", d.outcome_window),
            survival_min_quote_lamports: env_u64(
                "STORM_SURVIVAL_MIN_QUOTE_LAMPORTS",
                d.survival_min_quote_lamports,
            ),
        }
    }
}

/// Read `var` as a u64 count of seconds into a `Duration`, or `fallback`.
fn env_secs(var: &str, fallback: Duration) -> Duration {
    match std::env::var(var).ok().and_then(|s| s.parse::<u64>().ok()) {
        Some(secs) => Duration::from_secs(secs),
        None => fallback,
    }
}

/// Read `var` as a u64, or `fallback`.
fn env_u64(var: &str, fallback: u64) -> u64 {
    std::env::var(var)
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(fallback)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_the_spec_windows() {
        let c = CollectorConfig::default();
        assert_eq!(c.cycle_interval, Duration::from_secs(1800));
        assert_eq!(c.snapshot_window, Duration::from_secs(43_200)); // 12h
        assert_eq!(c.outcome_window, Duration::from_secs(1_209_600)); // 14d
        assert_eq!(c.survival_min_quote_lamports, 5_000_000_000);
    }

    #[test]
    fn env_secs_parses_or_falls_back() {
        // A junk value falls back to the default.
        assert_eq!(
            env_secs("STORM_TEST_DEFINITELY_UNSET_VAR", Duration::from_secs(99)),
            Duration::from_secs(99)
        );
    }

    #[test]
    fn env_u64_falls_back_when_unset() {
        assert_eq!(env_u64("STORM_TEST_DEFINITELY_UNSET_VAR", 42), 42);
    }
}
```

- [ ] **Step 2: Run it to verify it passes**

The implementation ships with the test in Step 1.

Run: `cargo test -p storm-collector config`
Expected: FAIL to compile — `config` is not yet a module of the crate. Fix in
Step 3, then this passes.

- [ ] **Step 3: Register the module in `main.rs`**

In `bins/storm-collector/src/main.rs`, add the module declaration directly under
the crate doc-comment (above `use clap::Parser;`):

```rust
mod config;
```

Then, so the not-yet-used `CollectorConfig` does not trip `dead_code` before
Task 10 wires it in, add this line at the end of `main`'s body, right before
`Ok(())`:

```rust
    let _collector_cfg = config::CollectorConfig::from_env();
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-collector config`
Expected: PASS — all three tests.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add bins/storm-collector/src/config.rs bins/storm-collector/src/main.rs
git commit -m "Add CollectorConfig timings and thresholds

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Pure scheduling decisions (`schedule.rs`)

**Files:**
- Create: `bins/storm-collector/src/schedule.rs`
- Modify: `bins/storm-collector/src/main.rs`
- Test: in `bins/storm-collector/src/schedule.rs`

The "is this graduation due for a snapshot / an outcome?" decisions are pure
arithmetic over timestamps. Isolating them here makes the daemon's timing logic
fully unit-testable with synthetic inputs — no clock, no network, no DB.

A graduation is **snapshot-due** when `now >= detected_at + snapshot_window`.
It is **outcome-due** when `now >= detected_at + outcome_window`. The window
`Duration`s come from `CollectorConfig`; the function takes plain `i64` seconds.

- [ ] **Step 1: Write the failing test**

Create `bins/storm-collector/src/schedule.rs`:

```rust
//! Pure scheduling decisions for the collector daemon.
//!
//! "Is a graduation due for a snapshot / an outcome?" is pure arithmetic over
//! Unix-second timestamps — no clock, no I/O. Unit-tested with synthetic input.

/// True when the T0+window observation period has elapsed and a feature
/// snapshot should be taken.
///
/// * `detected_at` — Unix seconds the graduation was detected (the collector's T0).
/// * `window_secs` — the observation window length in seconds.
/// * `now` — the current Unix-second timestamp.
pub fn is_snapshot_due(detected_at: i64, window_secs: i64, now: i64) -> bool {
    now >= detected_at.saturating_add(window_secs)
}

/// True when the outcome window has matured and an outcome should be recorded.
///
/// * `detected_at` — Unix seconds the graduation was detected (the collector's T0).
/// * `window_secs` — the outcome window length in seconds.
/// * `now` — the current Unix-second timestamp.
pub fn is_outcome_due(detected_at: i64, window_secs: i64, now: i64) -> bool {
    now >= detected_at.saturating_add(window_secs)
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: i64 = 1_779_000_000;
    const TWELVE_HOURS: i64 = 12 * 3600;
    const FOURTEEN_DAYS: i64 = 14 * 24 * 3600;

    #[test]
    fn snapshot_not_due_before_the_window() {
        // One second short of the window.
        assert!(!is_snapshot_due(T0, TWELVE_HOURS, T0 + TWELVE_HOURS - 1));
    }

    #[test]
    fn snapshot_due_exactly_at_the_window_boundary() {
        assert!(is_snapshot_due(T0, TWELVE_HOURS, T0 + TWELVE_HOURS));
    }

    #[test]
    fn snapshot_due_well_after_the_window() {
        assert!(is_snapshot_due(T0, TWELVE_HOURS, T0 + FOURTEEN_DAYS));
    }

    #[test]
    fn outcome_not_due_before_the_window() {
        assert!(!is_outcome_due(T0, FOURTEEN_DAYS, T0 + FOURTEEN_DAYS - 1));
    }

    #[test]
    fn outcome_due_at_and_after_the_window() {
        assert!(is_outcome_due(T0, FOURTEEN_DAYS, T0 + FOURTEEN_DAYS));
        assert!(is_outcome_due(T0, FOURTEEN_DAYS, T0 + FOURTEEN_DAYS + 1));
    }

    #[test]
    fn saturating_add_guards_against_overflow() {
        // A pathological window must not panic; it just means "never due".
        assert!(!is_snapshot_due(i64::MAX, i64::MAX, 0));
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-collector schedule`
Expected: FAIL to compile — `schedule` is not yet a module of the crate.

- [ ] **Step 3: Register the module in `main.rs`**

The pure `schedule` functions are not consumed until `cycle.rs` (Task 10), and
`cycle` itself is not called until `main` (Task 11). In a **binary** crate, an
unused `pub fn` *does* trigger the `dead_code` lint (unlike a library crate, where
`pub` items are the public API), and `clippy -D warnings` turns that into a hard
error. The `dead_code` analysis only treats an item as "used" when it is reachable
from a live entry point — so until `main` calls `run_cycle` (Task 11), the whole
`schedule`/`classify`/`discover`/`cycle` graph counts as dead. The module
declaration therefore carries a temporary `#[allow(dead_code)]` that **Task 11
removes** once the daemon loop wires everything to `main`.

In `bins/storm-collector/src/main.rs`, add under the existing `mod config;` line:

```rust
// `schedule`'s pure functions are reachable only once `main` calls the cycle
// (Task 11); allow dead_code until then. The whole allow is removed in Task 11.
#[allow(dead_code)]
mod schedule;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-collector schedule`
Expected: PASS — all six tests.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0. (The `#[allow(dead_code)]` on the `mod schedule;`
declaration keeps the not-yet-wired pure functions from failing the gate.)

- [ ] **Step 6: Commit**

```bash
git add bins/storm-collector/src/schedule.rs bins/storm-collector/src/main.rs
git commit -m "Add pure scheduling decisions for the collector

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Pure outcome classification (`classify.rs`)

**Files:**
- Create: `bins/storm-collector/src/classify.rs`
- Modify: `bins/storm-collector/src/main.rs`
- Test: in `bins/storm-collector/src/classify.rs`

The v1 survival rule (see the Context section) is a pure function: given the
pool's quote reserve at the outcome check and the survival threshold, decide
`survived` vs `rugged`. Keeping it pure makes the rule trivially tunable in
validation without touching I/O code.

- [ ] **Step 1: Write the failing test**

Create `bins/storm-collector/src/classify.rs`:

```rust
//! Pure outcome classification — the v1 survival rule.
//!
//! A token survived if its pool's quote (wrapped-SOL) reserve at the outcome
//! check is at least the configured threshold; otherwise it rugged. Pure and
//! unit-tested so validation (Phase 3) can retune the rule freely.

/// The recorded outcome verdict for a graduated token.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// The pool still holds at least the threshold quote liquidity.
    Survived,
    /// The pool's quote liquidity fell below the threshold (drained / dead).
    Rugged,
}

impl Verdict {
    /// `true` for [`Verdict::Survived`] — the value persisted to `outcomes.survived`.
    pub fn survived(self) -> bool {
        matches!(self, Verdict::Survived)
    }
}

/// Classify a graduated token's outcome from its pool's quote reserve.
///
/// * `quote_reserve_lamports` — the pool's quote-token (wrapped-SOL) reserve, in
///   lamports, observed at the outcome check.
/// * `min_quote_lamports` — the survival threshold (`CollectorConfig`).
pub fn classify_outcome(quote_reserve_lamports: u64, min_quote_lamports: u64) -> Verdict {
    if quote_reserve_lamports >= min_quote_lamports {
        Verdict::Survived
    } else {
        Verdict::Rugged
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 5 SOL, the default CollectorConfig survival threshold.
    const THRESHOLD: u64 = 5_000_000_000;

    #[test]
    fn well_funded_pool_survives() {
        // 40 SOL of quote liquidity — comfortably survives.
        assert_eq!(
            classify_outcome(40_000_000_000, THRESHOLD),
            Verdict::Survived
        );
    }

    #[test]
    fn drained_pool_rugs() {
        // 0.1 SOL left — rugged.
        assert_eq!(classify_outcome(100_000_000, THRESHOLD), Verdict::Rugged);
    }

    #[test]
    fn exactly_at_threshold_survives() {
        // The boundary is inclusive: exactly the threshold counts as survived.
        assert_eq!(classify_outcome(THRESHOLD, THRESHOLD), Verdict::Survived);
    }

    #[test]
    fn one_lamport_below_threshold_rugs() {
        assert_eq!(classify_outcome(THRESHOLD - 1, THRESHOLD), Verdict::Rugged);
    }

    #[test]
    fn empty_pool_rugs() {
        assert_eq!(classify_outcome(0, THRESHOLD), Verdict::Rugged);
    }

    #[test]
    fn survived_flag_maps_to_bool() {
        assert!(Verdict::Survived.survived());
        assert!(!Verdict::Rugged.survived());
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-collector classify`
Expected: FAIL to compile — `classify` is not yet a module of the crate.

- [ ] **Step 3: Register the module in `main.rs`**

`classify_outcome` / `Verdict` are not reachable from a live entry point until
`main` calls the cycle (Task 11) — same binary-crate `dead_code` situation as
Task 7 — so the module declaration carries a temporary `#[allow(dead_code)]`
removed in Task 11.

In `bins/storm-collector/src/main.rs`, add under the `#[allow(dead_code)] mod
schedule;` block:

```rust
// `classify` is reachable only once `main` calls the cycle (Task 11); allow
// dead_code until then. Removed in Task 11.
#[allow(dead_code)]
mod classify;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-collector classify`
Expected: PASS — all six tests.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add bins/storm-collector/src/classify.rs bins/storm-collector/src/main.rs
git commit -m "Add pure outcome classification

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Graduation discovery (`discover.rs`)

**Files:**
- Create: `bins/storm-collector/src/discover.rs`
- Modify: `bins/storm-collector/src/main.rs`
- Test: in `bins/storm-collector/src/discover.rs` (pure filtering helper only)

`discover.rs` has two parts:

1. A **pure** helper, `graduation_from_pool_account` — given a pool account's
   address and raw bytes, parse it with `PumpSwapPool::unpack`, check it is a
   canonical graduation pool (`index == 0` **and** `quote_mint == wrapped SOL` —
   see the discovery section: the pool's `creator` is *not* the bonding curve, so
   `is_canonical_graduation` cannot be used with a derived PDA), and on success
   return a `DiscoveredGraduation { mint, pool_address, bonding_curve }` where
   `mint` is the pool's `base_mint` and `bonding_curve` is
   `bonding_curve_pda(base_mint)`. Unit-tested against the `storm-pumpfun` pool
   fixture (no network).
2. An **async** function, `discover_graduations` — issue one
   `getProgramAccounts` call against the PumpSwap program with the `DataSize(301)`
   + index-0 `Memcmp` filters, then run every returned account through the pure
   helper. RPC errors surface as `StormError::Rpc`; an account that fails the
   pure check is skipped (the server filter is approximate; the parser is
   authoritative).

The integration test in Task 12 exercises the live RPC path; `cargo test` only
runs the pure-helper tests here.

- [ ] **Step 1: Write the failing test**

Create `bins/storm-collector/src/discover.rs`:

```rust
//! Graduation discovery — poll PumpSwap `getProgramAccounts` for index-0 pools.
//!
//! The pure `graduation_from_pool_account` helper (account bytes -> a confirmed
//! graduation) is unit-tested here. `discover_graduations` issues the single
//! RPC call and is exercised by the `#[ignore]`-d integration test.

use solana_client::rpc_config::{RpcAccountInfoConfig, RpcProgramAccountsConfig};
use solana_client::rpc_filter::{Memcmp, RpcFilterType};
use solana_sdk::pubkey::Pubkey;
use storm_core::{Result, StormError};
use storm_pumpfun::{bonding_curve_pda, PumpSwapPool, PUMPSWAP_PROGRAM_ID};
use storm_solana::RpcContext;

/// On-chain byte length of a PumpSwap `Pool` account: 244 defined-field bytes
/// plus 57 trailing reserved bytes. Verified against the captured fixture in
/// `crates/storm-pumpfun/tests/fixtures/NOTES.md`. This is the value the
/// `DataSize` filter needs — `PumpSwapPool::MIN_LEN` (244) is only the minimum
/// *parseable* length and would match zero accounts as a `DataSize` filter.
const PUMPSWAP_POOL_ACCOUNT_LEN: u64 = 301;

/// Wrapped SOL — the quote mint of every pump.fun graduation pool.
const WRAPPED_SOL_MINT: Pubkey =
    solana_sdk::pubkey!("So11111111111111111111111111111111111111112");

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
/// `storm-pumpfun`'s `tests/fixtures/NOTES.md`), so `is_canonical_graduation`
/// cannot be used here with a derived PDA — that equality never holds.
///
/// Returns `Ok(None)` for a parseable pool that is *not* a canonical graduation
/// (wrong index, or quote mint is not wSOL) and `Err` only when the bytes are
/// too short / malformed for `PumpSwapPool::unpack`.
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

/// The `getProgramAccounts` config that asks the RPC node for only canonical
/// (index-0) PumpSwap `Pool` accounts: a `DataSize` filter on the on-chain
/// account length plus a `Memcmp` of `[0x00, 0x00]` on the `index` field
/// (a little-endian `u16` at offset 9 — 8-byte discriminator + 1-byte `pool_bump`).
fn graduation_pool_filter() -> RpcProgramAccountsConfig {
    RpcProgramAccountsConfig {
        filters: Some(vec![
            RpcFilterType::DataSize(PUMPSWAP_POOL_ACCOUNT_LEN),
            RpcFilterType::Memcmp(Memcmp::new_raw_bytes(9, vec![0, 0])),
        ]),
        account_config: RpcAccountInfoConfig::default(),
        with_context: None,
        sort_results: None,
    }
}

/// Discover canonical pump.fun graduations by polling `getProgramAccounts` on
/// the PumpSwap program. One RPC call; every returned account is re-validated
/// by [`graduation_from_pool_account`] (the server filter is approximate).
pub async fn discover_graduations(rpc: &RpcContext) -> Result<Vec<DiscoveredGraduation>> {
    let accounts = rpc
        .rpc()
        .get_program_accounts_with_config(&PUMPSWAP_PROGRAM_ID, graduation_pool_filter())
        .await
        .map_err(|e| StormError::Rpc(format!("getProgramAccounts pumpswap: {e}")))?;

    let mut found = Vec::new();
    for (address, account) in accounts {
        // A pool that fails to parse is skipped, not fatal — the server filter
        // can in principle return an account the strict parser rejects.
        match graduation_from_pool_account(address, &account.data) {
            Ok(Some(grad)) => found.push(grad),
            Ok(None) => {}
            Err(e) => tracing::debug!(%address, error = %e, "skipping unparseable pool account"),
        }
    }
    Ok(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    // The canonical PumpSwap pool fixture captured by storm-pumpfun (sub-plan 1).
    // NOTES.md records it as a 301-byte index-0 pool with quote_mint = wSOL.
    const POOL_FIXTURE: &[u8] =
        include_bytes!("../../../crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin");

    #[test]
    fn real_fixture_is_recognised_as_a_graduation() {
        let pool_addr = Pubkey::new_unique();
        let grad = graduation_from_pool_account(pool_addr, POOL_FIXTURE)
            .unwrap()
            .expect("the fixture is a canonical graduation pool");
        assert_eq!(grad.pool_address, pool_addr);
        // The discovered mint is the pool's base mint; the fixture's base_mint
        // is the "Pumpfun Pepe" token (see storm-pumpfun NOTES.md).
        assert_eq!(
            grad.mint,
            Pubkey::from_str_const("5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump"),
        );
        // The bonding curve is the PDA of that mint.
        assert_eq!(grad.bonding_curve, bonding_curve_pda(&grad.mint));
    }

    #[test]
    fn short_data_is_a_parse_error() {
        match graduation_from_pool_account(Pubkey::new_unique(), &[0u8; 40]) {
            Err(StormError::Parse(_)) => {}
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn filter_pins_account_size_and_index() {
        let cfg = graduation_pool_filter();
        let filters = cfg.filters.expect("filters set");
        assert_eq!(filters.len(), 2);
        // First filter pins the on-chain account size (301 bytes, not MIN_LEN).
        match &filters[0] {
            RpcFilterType::DataSize(n) => assert_eq!(*n, 301),
            other => panic!("expected DataSize, got {other:?}"),
        }
        // Second filter is a memcmp at the index offset.
        match &filters[1] {
            RpcFilterType::Memcmp(_) => {}
            other => panic!("expected Memcmp, got {other:?}"),
        }
    }
}
```

> **Note on `Pubkey::from_str_const`:** `solana-sdk` 2.x provides
> `Pubkey::from_str_const(&str) -> Pubkey`, a const-fn base58 parser that panics
> on a malformed literal — ideal for a test constant. If a future `solana-sdk`
> removes it, replace with `std::str::FromStr`:
> `Pubkey::from_str("5Tfq…pump").unwrap()` and add `use std::str::FromStr;` to
> the test module.

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-collector discover`
Expected: FAIL to compile — `discover` is not yet a module of the crate.

- [ ] **Step 3: Register the module in `main.rs`**

`discover_graduations` (and `DiscoveredGraduation`) are not reachable from a live
entry point until `main` calls the cycle (Task 11) — same binary-crate
`dead_code` situation as Tasks 7–8 — so the module declaration carries a
temporary `#[allow(dead_code)]` removed in Task 11. (`graduation_from_pool_account`
and `graduation_pool_filter` are used within `discover.rs` itself, but the `allow`
on the whole module is simplest and covers everything.)

In `bins/storm-collector/src/main.rs`, add under the `#[allow(dead_code)] mod
classify;` block:

```rust
// `discover` is reachable only once `main` calls the cycle (Task 11); allow
// dead_code until then. Removed in Task 11.
#[allow(dead_code)]
mod discover;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-collector discover`
Expected: PASS — all three tests. (`real_fixture_is_recognised_as_a_graduation`
reuses `storm-pumpfun`'s committed `pumpswap_pool.bin` fixture via a relative
`include_bytes!` path, so no network and no new fixture file.)

If the `include_bytes!` path fails to resolve, confirm the fixture exists at
`crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin` (it was committed by
sub-plan 1, Task 2) and that the `../../../` prefix correctly climbs from
`bins/storm-collector/src/` to the workspace root.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add bins/storm-collector/src/discover.rs bins/storm-collector/src/main.rs
git commit -m "Add PumpSwap getProgramAccounts graduation discovery

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The collection cycle (`cycle.rs`)

**Files:**
- Create: `bins/storm-collector/src/cycle.rs`
- Modify: `bins/storm-collector/src/main.rs`
- Test: in `bins/storm-collector/src/cycle.rs` (the pure `FeatureVector` flattener)

`cycle.rs` wires the pure decisions and the I/O together. It exposes:

- `flatten_feature_vector` — a **pure** helper turning a
  `storm_features::FeatureVector` plus a `graduation_id` and `snapshot_at` into a
  `storm_store::FeatureSnapshotRow`. Unit-tested with a synthetic `FeatureVector`.
- `run_cycle` — one async collection cycle: discover → snapshot due → record
  mature outcomes. It takes `now` as an explicit `i64` argument (not a hidden
  clock read) so its phase logic stays deterministic; `main` passes the real
  wall clock.

Each phase is resilient: a per-graduation failure is logged and skipped (spec
section 9, "skip-and-log"); it does not abort the cycle or the daemon. Phase 1
(discover) inserts via the idempotent `insert_graduation`; phases 2 and 3 guard
with `has_feature_snapshot` / `has_outcome` before doing work, so a crash
mid-cycle and a re-run never double-write.

- [ ] **Step 1: Write the failing test**

Create `bins/storm-collector/src/cycle.rs`:

```rust
//! One collection cycle: discover graduations, snapshot due ones, record
//! mature outcomes. Resilient — a per-graduation failure is logged and skipped.

use storm_core::Result;
use storm_features::{extract_features, FeatureVector};
use storm_solana::RpcContext;
use storm_store::{
    FeatureSnapshotRow, GraduationRow, GraduationStatus, OutcomeRow, Store, StoredGraduation,
};

use crate::classify::classify_outcome;
use crate::config::CollectorConfig;
use crate::discover::discover_graduations;
use crate::schedule::{is_outcome_due, is_snapshot_due};

/// Flatten a [`FeatureVector`] into a [`FeatureSnapshotRow`] for persistence.
/// Pure — no I/O; unit-tested.
pub fn flatten_feature_vector(
    fv: &FeatureVector,
    graduation_id: i64,
    snapshot_at: i64,
) -> FeatureSnapshotRow {
    FeatureSnapshotRow {
        graduation_id,
        snapshot_at,
        base_reserve: fv.liquidity.base_reserve,
        quote_reserve: fv.liquidity.quote_reserve,
        lp_burned: fv.liquidity.lp_burned,
        pool_supply_fraction: fv.liquidity.pool_supply_fraction,
        mint_authority_present: fv.contract.mint_authority_present,
        freeze_authority_present: fv.contract.freeze_authority_present,
        visible_holder_count: fv.holders.visible_holder_count as i64,
        top10_concentration: fv.holders.top10_concentration,
        top20_concentration: fv.holders.top20_concentration,
        creator_bag_fraction: fv.holders.creator_bag_fraction,
        curve_graduated: fv.curve.graduated,
        curve_real_sol_reserves: fv.curve.real_sol_reserves,
        curve_real_token_reserves: fv.curve.real_token_reserves,
        curve_token_total_supply: fv.curve.token_total_supply,
        capped_signature_count: fv.deployer.capped_signature_count as i64,
        signature_count_capped: fv.deployer.count_capped,
        oldest_signature_age_secs: fv.deployer.oldest_signature_age_secs,
    }
}

/// Run one full collection cycle against `rpc` / `store`, using `now`
/// (Unix seconds) as the reference clock for the window decisions.
pub async fn run_cycle(
    rpc: &RpcContext,
    store: &Store,
    cfg: &CollectorConfig,
    now: i64,
) -> Result<()> {
    discover_phase(rpc, store, now).await?;
    snapshot_phase(rpc, store, cfg, now).await?;
    outcome_phase(rpc, store, cfg, now).await?;
    store
        .set_collector_state("last_cycle_at", &now.to_string())
        .await?;
    Ok(())
}

/// Phase 1 — discover graduations and insert any not yet tracked.
async fn discover_phase(rpc: &RpcContext, store: &Store, now: i64) -> Result<()> {
    let slot = rpc
        .rpc()
        .get_slot()
        .await
        .map_err(|e| storm_core::StormError::Rpc(format!("get_slot: {e}")))?;
    let discovered = discover_graduations(rpc).await?;
    let mut new_count = 0usize;
    for grad in discovered {
        let row = GraduationRow {
            mint: grad.mint,
            pool_address: grad.pool_address,
            bonding_curve_address: grad.bonding_curve,
            graduation_slot: slot,
            detected_at: now,
            status: GraduationStatus::PendingSnapshot,
        };
        // insert_graduation is idempotent on `mint`: Some(id) = newly inserted,
        // None = already tracked.
        if store.insert_graduation(&row).await?.is_some() {
            new_count += 1;
        }
    }
    tracing::info!(new = new_count, "discover phase complete");
    Ok(())
}

/// Phase 2 — snapshot every pending graduation whose observation window elapsed.
async fn snapshot_phase(
    rpc: &RpcContext,
    store: &Store,
    cfg: &CollectorConfig,
    now: i64,
) -> Result<()> {
    let window = cfg.snapshot_window.as_secs() as i64;
    let pending = store
        .graduations_with_status(GraduationStatus::PendingSnapshot)
        .await?;
    for grad in pending {
        if !is_snapshot_due(grad.detected_at, window, now) {
            continue;
        }
        match snapshot_one(rpc, store, &grad, now).await {
            Ok(()) => tracing::info!(mint = %grad.mint, "feature snapshot recorded"),
            Err(e) => tracing::warn!(mint = %grad.mint, error = %e, "snapshot failed; will retry next cycle"),
        }
    }
    Ok(())
}

/// Snapshot a single graduation: extract features, persist, advance status.
async fn snapshot_one(
    rpc: &RpcContext,
    store: &Store,
    grad: &StoredGraduation,
    now: i64,
) -> Result<()> {
    // Idempotency guard: if a snapshot already exists (a prior crash), just
    // advance the status and stop.
    if store.has_feature_snapshot(grad.id).await? {
        store
            .set_graduation_status(grad.id, GraduationStatus::SnapshotDone)
            .await?;
        return Ok(());
    }
    let fv = extract_features(rpc, &grad.mint, &grad.pool_address, now).await?;
    let row = flatten_feature_vector(&fv, grad.id, now);
    store.insert_feature_snapshot(&row).await?;
    store
        .set_graduation_status(grad.id, GraduationStatus::SnapshotDone)
        .await?;
    Ok(())
}

/// Phase 3 — record an outcome for every snapshot_done graduation whose outcome
/// window has matured.
async fn outcome_phase(
    rpc: &RpcContext,
    store: &Store,
    cfg: &CollectorConfig,
    now: i64,
) -> Result<()> {
    let window = cfg.outcome_window.as_secs() as i64;
    let due = store
        .graduations_with_status(GraduationStatus::SnapshotDone)
        .await?;
    for grad in due {
        if !is_outcome_due(grad.detected_at, window, now) {
            continue;
        }
        match outcome_one(rpc, store, cfg, &grad, now).await {
            Ok(()) => tracing::info!(mint = %grad.mint, "outcome recorded"),
            Err(e) => tracing::warn!(mint = %grad.mint, error = %e, "outcome check failed; will retry next cycle"),
        }
    }
    Ok(())
}

/// Record a single graduation's outcome: read pool liquidity, classify, persist.
async fn outcome_one(
    rpc: &RpcContext,
    store: &Store,
    cfg: &CollectorConfig,
    grad: &StoredGraduation,
    now: i64,
) -> Result<()> {
    if store.has_outcome(grad.id).await? {
        store
            .set_graduation_status(grad.id, GraduationStatus::OutcomeDone)
            .await?;
        return Ok(());
    }
    // Re-extract features purely to read the pool's current reserves; only the
    // liquidity group is used. Re-using extract_features keeps the RPC plumbing
    // in one place.
    let fv = extract_features(rpc, &grad.mint, &grad.pool_address, now).await?;
    let verdict = classify_outcome(fv.liquidity.quote_reserve, cfg.survival_min_quote_lamports);
    let row = OutcomeRow {
        graduation_id: grad.id,
        outcome_at: now,
        survived: verdict.survived(),
        base_reserve: fv.liquidity.base_reserve,
        quote_reserve: fv.liquidity.quote_reserve,
    };
    store.insert_outcome(&row).await?;
    store
        .set_graduation_status(grad.id, GraduationStatus::OutcomeDone)
        .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::pubkey::Pubkey;
    use storm_features::{
        ContractFlags, CurveSnapshot, DeployerSignals, HolderFeatures, LiquidityFeatures,
    };

    /// A fully-populated synthetic FeatureVector for the flatten test.
    fn sample_feature_vector() -> FeatureVector {
        FeatureVector {
            mint: Pubkey::new_unique(),
            liquidity: LiquidityFeatures {
                base_reserve: 200_000_000_000_000,
                quote_reserve: 85_000_000_000,
                lp_burned: true,
                pool_supply_fraction: 0.2,
            },
            contract: ContractFlags {
                mint_authority_present: false,
                freeze_authority_present: true,
            },
            holders: HolderFeatures {
                visible_holder_count: 18,
                top10_concentration: 0.31,
                top20_concentration: 0.44,
                creator_bag_fraction: 0.05,
            },
            curve: CurveSnapshot {
                graduated: true,
                real_sol_reserves: 85_000_000_000,
                real_token_reserves: 0,
                token_total_supply: 1_000_000_000_000_000,
            },
            deployer: DeployerSignals {
                capped_signature_count: 7,
                count_capped: false,
                oldest_signature_age_secs: Some(123_456),
            },
        }
    }

    #[test]
    fn flatten_copies_every_group_field() {
        let fv = sample_feature_vector();
        let row = flatten_feature_vector(&fv, 99, 1_779_050_000);

        assert_eq!(row.graduation_id, 99);
        assert_eq!(row.snapshot_at, 1_779_050_000);
        // liquidity
        assert_eq!(row.base_reserve, 200_000_000_000_000);
        assert_eq!(row.quote_reserve, 85_000_000_000);
        assert!(row.lp_burned);
        assert!((row.pool_supply_fraction - 0.2).abs() < 1e-9);
        // contract flags
        assert!(!row.mint_authority_present);
        assert!(row.freeze_authority_present);
        // holders
        assert_eq!(row.visible_holder_count, 18);
        assert!((row.top20_concentration - 0.44).abs() < 1e-9);
        // curve
        assert!(row.curve_graduated);
        assert_eq!(row.curve_token_total_supply, 1_000_000_000_000_000);
        // deployer
        assert_eq!(row.capped_signature_count, 7);
        assert!(!row.signature_count_capped);
        assert_eq!(row.oldest_signature_age_secs, Some(123_456));
    }

    #[test]
    fn flatten_preserves_a_none_signature_age() {
        let mut fv = sample_feature_vector();
        fv.deployer.oldest_signature_age_secs = None;
        let row = flatten_feature_vector(&fv, 1, 0);
        assert_eq!(row.oldest_signature_age_secs, None);
    }
}
```

> **Note on the `Pubkey` import:** `run_cycle` and its phase helpers never name
> `Pubkey` directly — only the `tests` module constructs one — so the
> `use solana_sdk::pubkey::Pubkey;` import lives *inside* the `#[cfg(test)] mod
> tests` block, not at the top of the file. This keeps the non-test build free of
> an unused-import warning.

- [ ] **Step 2: Run it to verify it fails**

Run: `cargo test -p storm-collector cycle`
Expected: FAIL to compile — `cycle` is not yet a module of the crate.

- [ ] **Step 3: Register the module in `main.rs`**

`run_cycle` / `flatten_feature_vector` are not called from `main` until Task 11,
so — like `schedule`, `classify`, `discover` — `mod cycle;` carries a temporary
`#[allow(dead_code)]` removed in Task 11. The three earlier `#[allow(dead_code)]`
attributes stay in place for now: `cycle` *uses* those modules, but because
`cycle` itself is still dead code, everything it reaches is still dead — the
attributes can only be removed once `main` calls `run_cycle` (Task 11).

In `bins/storm-collector/src/main.rs`, add under the `#[allow(dead_code)] mod
discover;` block:

```rust
// `cycle` is called by `main` in Task 11; allow dead_code until then.
// Removed in Task 11.
#[allow(dead_code)]
mod cycle;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cargo test -p storm-collector cycle`
Expected: PASS — `flatten_copies_every_group_field` and
`flatten_preserves_a_none_signature_age`.

If a `FeatureVector` sub-struct field name in `sample_feature_vector` does not
compile, cross-check it against `crates/storm-features/src/{liquidity,contract,
holders,curve,deployer}.rs` — the struct definitions there are the source of
truth.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0. (All four collector modules now carry
`#[allow(dead_code)]`; the gate is green even though nothing reaches `main` yet.)

- [ ] **Step 6: Commit**

```bash
git add bins/storm-collector/src/cycle.rs bins/storm-collector/src/main.rs
git commit -m "Add the collection cycle: discover, snapshot, outcome

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Wire the daemon loop into `main.rs`

**Files:**
- Modify: `bins/storm-collector/src/main.rs`
- Test: in `bins/storm-collector/src/main.rs` (the pure `now_unix` helper)

`main` becomes the real daemon: open the store, run migrations, build the
`RpcContext`, then loop — run a cycle, sleep `cycle_interval`, repeat — until
Ctrl-C. Resilience: a cycle that returns `Err` (e.g. an RPC outage) is logged and
the loop backs off using `storm_core::next_backoff` instead of the flat interval;
a successful cycle resets the backoff. `tokio::select!` on `tokio::signal::ctrl_c`
makes shutdown immediate and clean.

- [ ] **Step 1: Replace `bins/storm-collector/src/main.rs`**

Replace the entire file with:

```rust
//! storm-collector — the always-on pump.fun survival-data daemon.
//!
//! Each cycle it discovers newly-graduated tokens, snapshots their features at
//! T0+window, and records outcomes once the outcome window matures. See
//! `docs/superpowers/plans/2026-05-17-storm-collector.md`.

mod classify;
mod config;
mod cycle;
mod discover;
mod schedule;

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use clap::Parser;
use storm_core::backoff::{next_backoff, INITIAL_BACKOFF};
use storm_core::Config;
use storm_solana::RpcContext;
use storm_store::Store;

use crate::config::CollectorConfig;
use crate::cycle::run_cycle;

/// storm-collector command-line arguments.
#[derive(Parser)]
#[command(name = "storm-collector", about = "pump.fun survival-data collector", version)]
struct Cli {
    /// SQLite database URL.
    #[arg(long, default_value = "sqlite://./storm.db")]
    db: String,
    /// Run exactly one collection cycle and exit (for cron-style scheduling /
    /// manual checks) instead of looping forever.
    #[arg(long)]
    once: bool,
}

/// Current wall-clock time as Unix seconds. Isolated so the daemon's clock read
/// is in one named place; the pure cycle logic takes the value as an argument.
fn now_unix() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Load .env (gitignored) so SOLANA_RPC_URL / Helius credentials are picked up.
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cli = Cli::parse();
    let cfg = Config::load()?;
    let collector_cfg = CollectorConfig::from_env();

    let store = Store::open(&cli.db).await?;
    store.migrate().await?;
    let rpc = RpcContext::from_config(&cfg.solana);

    tracing::info!(
        db = %cli.db,
        rpc = %cfg.solana.rpc_url,
        cycle_secs = collector_cfg.cycle_interval.as_secs(),
        once = cli.once,
        "storm-collector starting",
    );

    if cli.once {
        run_cycle(&rpc, &store, &collector_cfg, now_unix()).await?;
        tracing::info!("single cycle complete; exiting (--once)");
        return Ok(());
    }

    run_daemon(&rpc, &store, &collector_cfg).await;
    Ok(())
}

/// The forever-loop: run a cycle, sleep, repeat — until Ctrl-C. A failing cycle
/// is logged and the next sleep uses exponential backoff; a success resets it.
async fn run_daemon(rpc: &RpcContext, store: &Store, cfg: &CollectorConfig) {
    let mut backoff = INITIAL_BACKOFF;
    loop {
        match run_cycle(rpc, store, cfg, now_unix()).await {
            Ok(()) => {
                backoff = INITIAL_BACKOFF; // healthy cycle — reset the backoff
                tracing::info!("cycle complete");
                if sleep_or_shutdown(cfg.cycle_interval).await {
                    break;
                }
            }
            Err(e) => {
                tracing::error!(error = %e, backoff_secs = backoff.as_secs(), "cycle failed; backing off");
                if sleep_or_shutdown(backoff).await {
                    break;
                }
                backoff = next_backoff(backoff);
            }
        }
    }
    tracing::info!("storm-collector stopped");
}

/// Sleep `dur`, or wake immediately on Ctrl-C. Returns `true` if Ctrl-C fired
/// (the daemon should stop), `false` if the sleep simply elapsed.
async fn sleep_or_shutdown(dur: Duration) -> bool {
    tokio::select! {
        _ = tokio::time::sleep(dur) => false,
        _ = tokio::signal::ctrl_c() => {
            tracing::info!("Ctrl-C received; shutting down");
            true
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn now_unix_is_a_plausible_recent_timestamp() {
        // Sanity bound: after 2025-01-01 and before 2100-01-01. Catches a clock
        // that is wildly wrong without being flaky.
        let now = now_unix();
        assert!(now > 1_735_689_600, "now_unix() should be after 2025-01-01");
        assert!(now < 4_102_444_800, "now_unix() should be before 2100-01-01");
    }
}
```

Note the five `mod` declarations at the top are now **bare** — the temporary
`#[allow(dead_code)]` attributes from Tasks 7–10 are deliberately gone. `main`
now calls `run_cycle`, which makes `cycle` and everything it reaches (`schedule`,
`classify`, `discover`) live code, so `dead_code` no longer fires; the now-stale
attributes are dropped as cleanup (replacing the whole file is the simplest way
to do it). The Task 6 `let _collector_cfg = …` bridge line is likewise gone.

- [ ] **Step 2: Verify it builds**

Run: `cargo build -p storm-collector`
Expected: `Finished` — `CollectorConfig` is now genuinely used by `main`, every
collector module is reachable, and no `#[allow(dead_code)]` remains.

- [ ] **Step 3: Run the test**

Run: `cargo test -p storm-collector`
Expected: PASS — every `storm-collector` test: `config` (3), `schedule` (6),
`classify` (6), `discover` (3), `cycle` (2), and `now_unix` (1). No network.

- [ ] **Step 4: Verify `--help` shows the new flag**

Run: `cargo run -p storm-collector -- --help`
Expected: help text lists both `--db` and `--once`.

- [ ] **Step 5: Run clippy**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add bins/storm-collector/src/main.rs
git commit -m "Wire the storm-collector daemon loop with backoff and Ctrl-C

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Live-RPC integration test (`#[ignore]`-d)

**Files:**
- Create: `bins/storm-collector/tests/integration.rs`

One end-to-end test that actually hits Solana RPC: it runs `discover_graduations`
against the live PumpSwap program and asserts the result is well-formed. It is
`#[ignore]`-d so `cargo test` and CI skip it; clippy `--all-targets` still
type-checks it. The collector's modules are private to the binary crate, so the
test re-implements the one-call discovery against `RpcContext` directly rather
than importing `discover` (a binary crate exposes no library API).

- [ ] **Step 1: Create the integration test**

Create `bins/storm-collector/tests/integration.rs`:

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
//! `SOLANA_RPC_URL` comes from `.env`. The test issues one `getProgramAccounts`
//! call against the PumpSwap program — the same call the daemon's discover
//! phase makes — and asserts the result parses into canonical graduations.

use solana_client::rpc_config::{RpcAccountInfoConfig, RpcProgramAccountsConfig};
use solana_client::rpc_filter::{Memcmp, RpcFilterType};
use solana_sdk::pubkey::Pubkey;
use storm_core::SolanaConfig;
use storm_pumpfun::{PumpSwapPool, PUMPSWAP_PROGRAM_ID};
use storm_solana::RpcContext;

/// On-chain PumpSwap `Pool` account length (see storm-pumpfun NOTES.md) — the
/// value the `DataSize` filter needs; `PumpSwapPool::MIN_LEN` (244) is smaller.
const PUMPSWAP_POOL_ACCOUNT_LEN: u64 = 301;

/// Wrapped SOL — the quote mint of every pump.fun graduation pool.
const WRAPPED_SOL_MINT: Pubkey =
    solana_sdk::pubkey!("So11111111111111111111111111111111111111112");

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

    // The same filtered query the daemon's discover phase issues: index-0
    // PumpSwap Pool accounts of the on-chain account size.
    let config = RpcProgramAccountsConfig {
        filters: Some(vec![
            RpcFilterType::DataSize(PUMPSWAP_POOL_ACCOUNT_LEN),
            RpcFilterType::Memcmp(Memcmp::new_raw_bytes(9, vec![0, 0])),
        ]),
        account_config: RpcAccountInfoConfig::default(),
        with_context: None,
        sort_results: None,
    };

    let accounts = rpc
        .rpc()
        .get_program_accounts_with_config(&PUMPSWAP_PROGRAM_ID, config)
        .await
        .expect("getProgramAccounts call failed");

    // The PumpSwap program has many graduated pools; the filtered set is non-empty.
    assert!(
        !accounts.is_empty(),
        "expected at least one index-0 PumpSwap pool"
    );

    // Every returned account must parse; a clear majority must be a canonical
    // graduation — index 0 with wSOL as the quote mint. The server filter is
    // approximate, so we do not demand 100%, but a real result set is
    // overwhelmingly genuine.
    let mut canonical = 0usize;
    for (address, account) in &accounts {
        let pool = PumpSwapPool::unpack(&account.data)
            .unwrap_or_else(|e| panic!("pool {address} failed to parse: {e}"));
        if pool.index == 0 && pool.quote_mint == WRAPPED_SOL_MINT {
            canonical += 1;
        }
    }
    assert!(
        canonical * 2 >= accounts.len(),
        "at least half of the {} filtered pools should be canonical graduations, got {canonical}",
        accounts.len(),
    );

    println!(
        "discovered {} index-0 pools, {canonical} confirmed canonical graduations",
        accounts.len()
    );
}
```

- [ ] **Step 2: Verify the test compiles but is skipped**

Run: `cargo test -p storm-collector --test integration`
Expected: compiles; the run reports `0 passed; 0 failed; 1 ignored` — CI-safe,
no network touched.

- [ ] **Step 3: Run clippy including the new test target**

Run: `cargo clippy -p storm-collector --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 4: Commit**

```bash
git add bins/storm-collector/tests/integration.rs
git commit -m "Add ignored live-RPC graduation-discovery test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Full workspace verification (the CI gate)

**Files:** none — verification only.

- [ ] **Step 1: Build the whole workspace**

Run: `cargo build --workspace`
Expected: `Finished` — every crate plus the new `storm-collector` binary.

- [ ] **Step 2: Run the full test suite (the CI `cargo test` gate)**

Run: `cargo test --workspace`
Expected: all tests pass — the existing crates' suites, the new `storm-store`
survival-schema tests (migration 1, graduations 2, feature_snapshots 2,
outcomes/collector_state 2), and the `storm-collector` unit tests (config 3,
schedule 6, classify 6, discover 3, cycle 2, main 1). Both `integration` tests
(`storm-features`'s and `storm-collector`'s) report `1 ignored`. **No network is
touched.**

- [ ] **Step 3: Run `cargo check` across all targets (the CI check gate)**

Run: `cargo check --workspace --all-targets`
Expected: `Finished`, no errors.

- [ ] **Step 4: Run clippy across the workspace (the CI clippy gate)**

Run: `cargo clippy --workspace --all-targets -- -D warnings`
Expected: no output, exit 0.

- [ ] **Step 5: Check formatting (the CI fmt gate)**

Run: `cargo fmt --all -- --check`
Expected: no output, exit 0. If it reports diffs, run `cargo fmt --all` and
re-stage.

- [ ] **Step 6: (Optional, manual) Smoke-test the daemon end to end**

Only if a Solana RPC endpoint is available — runs one real cycle against a
throwaway database:

```bash
set -a && . ./.env && set +a
cargo run -p storm-collector -- --once --db sqlite://./storm-smoke.db
```

Expected: it logs `storm-collector starting`, a `discover phase complete` line
with a `new=` count, and `single cycle complete; exiting (--once)`. Inspect the
result, then discard the throwaway DB:

```bash
sqlite3 storm-smoke.db "SELECT status, COUNT(*) FROM graduations GROUP BY status;"
rm -f storm-smoke.db
```

This step is **not** part of CI and not required for the plan's done criteria.

- [ ] **Step 7: Commit (only if Step 5 required a `cargo fmt` fix)**

If `cargo fmt --all` changed any file:

```bash
git add -A
git commit -m "Apply cargo fmt to storm-collector

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Otherwise, no commit — Task 12 was the final code change.

---

## Done criteria

- `storm-store` has a `0002_survival.sql` migration creating `graduations`,
  `feature_snapshots`, `outcomes`, and `collector_state`; `Store` has plain-data
  row types and `async` methods for all four, every one round-trip-tested
  against `sqlite::memory:`.
- `insert_graduation` is idempotent on `mint`; `has_feature_snapshot` /
  `has_outcome` guard against duplicate snapshots / outcomes — proven by tests.
- `bins/storm-collector` is a registered workspace member that builds and runs.
- Graduation discovery uses a filtered `getProgramAccounts` poll on the PumpSwap
  program (free-tier-friendly; no WebSocket, no paid indexer), with the strict
  `storm-pumpfun` parser as the authoritative re-check.
- The daemon runs the three-phase collection loop (discover → snapshot at
  T0+window → outcome at horizon N), is resilient (skip-and-log on per-token
  failure, `storm-core` exponential backoff on a failed cycle), supports a
  `--once` mode, and shuts down cleanly on Ctrl-C.
- All collector business logic that is pure — scheduling decisions, outcome
  classification, the pool-account filter helper, the `FeatureVector` flattener
  — has real TDD unit tests over synthetic data; the one live-RPC test is
  `#[ignore]`-d.
- `cargo test` passes with no network; `cargo build`, `cargo test`,
  `cargo check --workspace --all-targets`, `cargo clippy --workspace
  --all-targets -- -D warnings`, and `cargo fmt --all -- --check` all pass at
  every commit.

## Self-review

Run with fresh eyes after the plan is written; the executor need not repeat it.

**1. Spec coverage.** Section 5 (the collection loop) → the three-phase
`run_cycle` (Task 10) and the daemon loop (Task 11). Section 6 (features captured
at T0+6–24h) → `snapshot_window` default 12h (Task 6) gating Phase 2. Section 9
(`storm-collector` daemon, `storm-store` `graduations`/`feature_snapshots`/
`outcomes` migrations) → Tasks 1–4 (schema) and 5–12 (daemon). Section 9's
resilience requirements (RPC retries, skip-and-log, idempotent re-runs) →
`next_backoff` in Task 11, per-token `match`/`warn` in Task 10, `UNIQUE` mint +
`has_*` guards in Tasks 2–4 and 10. Section 11's open "survival label" decision →
the explicit, config-driven, pure `classify_outcome` rule (Task 8). The
`collector_state` table is an addition beyond the spec's three named tables — a
small daemon heartbeat, justified in the Context section, not a scope creep.

**2. Placeholder scan.** No "TBD"/"TODO"/"implement later" remain. Every code
step shows complete code. Two deliberate keep-clippy-green bridges are *not*
placeholders and are explicitly retired by later tasks: (a) the Task 6
`let _collector_cfg = …` line — a real compiling statement, removed when Task 11
replaces `main.rs`; (b) the `#[allow(dead_code)]` on the `schedule`/`classify`/
`discover`/`cycle` module declarations (Tasks 7–10) — required because an unused
`pub fn` in a *binary* crate does trip `dead_code` (verified), and removed in
Task 11 once `main` calls `run_cycle` and the whole module graph becomes live.
Each is flagged at its task and at the task that removes it.

**3. Type consistency.** `GraduationStatus` / `GraduationRow` / `StoredGraduation`
(Task 2), `FeatureSnapshotRow` (Task 3), `OutcomeRow` (Task 4) are defined once in
`storm-store` and consumed unchanged by `cycle.rs` (Task 10). `DiscoveredGraduation`
(Task 9) is produced by `discover` and consumed by `discover_phase` (Task 10).
`Verdict` / `classify_outcome` (Task 8) are consumed by `outcome_one` (Task 10).
`CollectorConfig` (Task 6) is consumed by `run_cycle` and `main` (Tasks 10–11).
`run_cycle(rpc, store, cfg, now)` has one signature, used identically in `main`'s
`--once` branch and `run_daemon`. `flatten_feature_vector(fv, graduation_id,
snapshot_at)` matches its single call site. The `FeatureVector` sub-struct field
names in Task 10's `sample_feature_vector` (`LiquidityFeatures.base_reserve`,
`ContractFlags.mint_authority_present`, `HolderFeatures.visible_holder_count`,
`CurveSnapshot.graduated`, `DeployerSignals.count_capped`/`oldest_signature_age_secs`)
match `storm-features`'s committed module definitions. The `storm-store` tests in
Tasks 1–4 read columns directly with `sqlx::query_as(...).fetch_one(&store.pool)`:
`pool` is a *private* field of `Store`, but the `#[cfg(test)] mod tests` block is a
child module of `crates/storm-store/src/lib.rs`, and a child module may access a
private field of an ancestor — so this compiles. (The pre-existing `storm-store`
tests only call public methods; the new tests are the first to touch `pool`
directly, which is a deliberate, valid choice for asserting raw persisted values.)

**4. Graduation discovery — corrected against the real account.** The first draft
of Task 9 filtered on `PumpSwapPool::MIN_LEN` and validated with
`is_canonical_graduation(&pool, &bonding_curve_pda(base_mint))`. Re-reading
`storm-pumpfun`'s captured fixture and its `tests/fixtures/NOTES.md` (committed by
sub-plan 1) showed **both were wrong**: (a) the real on-chain PumpSwap `Pool`
account is **301 bytes**, not `MIN_LEN`'s 244 — a `DataSize(244)` filter matches
nothing; (b) the pool's `creator` field is the pool-creator EOA, **not** the
token's bonding-curve PDA, so the `is_canonical_graduation` `creator ==
bonding_curve` clause never holds for a derived PDA. Task 9 is corrected: the
`DataSize` filter uses a `PUMPSWAP_POOL_ACCOUNT_LEN = 301` constant, and the
canonical check is `index == 0 && quote_mint == wrapped SOL` (both genuine pool
signals). The bonding curve is still `bonding_curve_pda(base_mint)` — the correct
PDA, stored for `extract_features` — and a non-graduated pool that slips past the
filter is caught at snapshot time, where `extract_features` reads the bonding
curve's `complete` flag into `curve.graduated`.
