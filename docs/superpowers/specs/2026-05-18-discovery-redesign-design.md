# Design Spec — storm-collector Graduation Discovery Redesign

**Date:** 2026-05-18
**Status:** Approved design — pending implementation plan
**Project:** `solana-storm`

## 1. Context

`storm-collector` (Phase 1, sub-plan 3 — merged to `main`) is the always-on
daemon that discovers newly-graduated pump.fun tokens, snapshots their features
at T0+window, and records outcomes at the horizon. Its cycle has three phases:
**discover → snapshot → outcome**.

A `--once` smoke-test against the Helius free tier revealed that the **discover
phase does not work**. It issued a single `getProgramAccounts` call against the
PumpSwap program; Helius rejected it:

> RPC error -32600: Too many accounts requested (5000001 pubkeys) — use
> getProgramAccountsV2 with pagination.

The PumpSwap program has 5M+ accounts. The original plan's premise — "one
bounded `getProgramAccounts` call per cycle" — was false: the `DataSize(301)` +
`index==0` filters barely narrow the set, because nearly every PumpSwap pool is
a 301-byte index-0 account. The "filtered" result is essentially every graduated
token ever.

**Scope of the defect:** only the discover phase (`discover.rs`). The schema,
the snapshot phase, the outcome phase, the daemon loop, backoff, `--once`, and
shutdown are all sound — `extract_features` does targeted per-account reads,
which are bounded. This is a contained, one-module redesign.

## 2. Goal & success criteria

Replace the discovery mechanism with one that:

- **works on the Helius free tier** — no paid gRPC, no co-location;
- is **bounded** — each cycle transfers only a small, recent slice, not the
  whole program;
- is **incremental** — finds tokens that graduated *since the last cycle*;
- adds **no new infrastructure** — no always-on WebSocket, no third-party
  indexer dependency, no extra API key;
- preserves the existing idempotency and resilience guarantees.

**Success:** the daemon's `--once` smoke-test completes a full discover phase
against the live Helius free tier and inserts the new graduations it finds.

## 3. The approach decision

Three mechanisms were evaluated.

| Option | Verdict |
|---|---|
| **A. `getProgramAccountsV2` + `changedSinceSlot`** | **Chosen.** Pure RPC on the Helius endpoint already in use. `changedSinceSlot` makes each cycle bounded and incremental; cursor pagination absorbs the volume. No WebSocket, no indexer, no new key. Verified by probe (below). Reuses the existing pool-parsing code. |
| B. `logsSubscribe` WebSocket | Rejected. A persistent WebSocket is exactly the always-on infrastructure the project's pivot set out to avoid. `logsSubscribe` on the PumpSwap program also surfaces every swap, not just migrations, so the stream needs filtering; and a dropped connection silently loses graduations unless backfilled by a poll — which reintroduces a poll anyway. The strategy snapshots at T0+6–24h, so real-time precision buys nothing. |
| C. Indexer API (Bitquery / Shyft) | Rejected for the *live* feed. A hosted indexer (Bitquery offers pump.fun→PumpSwap migration tracking) would work, but adds an external dependency, a second API key, and rate limits outside the project's control — none of which Option A needs. The strategy spec already earmarks an indexer for the *historical bootstrap* (Phase 2); that is the right place for it. |

**Verification.** Before committing to Option A, a probe was run against the
actual Helius free-tier endpoint: `getProgramAccountsV2` on the PumpSwap
program, filtered to index-0 301-byte pools, `changedSinceSlot` ≈ 5000 slots
(~30 min) back, `limit` 100. It returned 67 changed pools plus a pagination
cursor — confirming the method, the `changedSinceSlot` parameter, the
`dataSize`/`memcmp` filters, and cursor pagination all work on the free tier.
This design rests on a verified fact, not an assumption — the failure mode that
sank the original design.

## 4. Design

### 4.1 How pump.fun graduation works (the on-chain facts)

When a bonding curve completes (`BondingCurve.complete == true`), anyone may
call the Pump program's `migrate` instruction, which CPIs into PumpSwap's
`create_pool`, creating the **canonical index-0 pool** for the token paired
against wrapped SOL. There is no fixed migration authority — different wallets
call `migrate` — so there is no single low-traffic account to watch. This is why
an incremental program-account scan, not a per-account signature poll, is the
right shape for discovery.

### 4.2 `getProgramAccountsV2` with a slot cursor

`getProgramAccountsV2` is an RPC extension (Helius and others) that adds two
things vanilla `getProgramAccounts` lacks: **cursor pagination** and a
**`changedSinceSlot`** filter. `changedSinceSlot: S` returns only program
accounts whose data or lamports changed at or after slot `S`.

Each discover cycle becomes:

1. Read `last_discovery_slot` from the `collector_state` table (the table
   already exists — no schema change). **Cold start:** if the key is absent,
   seed it with the current slot and end the phase — the very first cycle
   discovers nothing. Backfilling history is explicitly Phase 2's job; the live
   collector starts fresh from deployment.
2. `getSlot` → `current_slot`. Captured *before* the scan, so anything that
   graduates mid-scan is simply caught next cycle — no gap.
3. Paginate: call `getProgramAccountsV2(PUMPSWAP_PROGRAM_ID, { encoding:
   base64, filters: [dataSize 301, memcmp(offset 9, index==0)],
   changedSinceSlot: last_discovery_slot, limit: 10000, paginationKey:
   <cursor> })`, following `paginationKey` until it is absent.
4. Parse each returned account with the existing pure
   `graduation_from_pool_account` helper (`PumpSwapPool::unpack` + the
   `index==0` & `quote_mint==wSOL` re-check). Unchanged from today.
5. `insert_graduation` each result. It is already idempotent on `mint`, so the
   already-tracked pools that `changedSinceSlot` also returns are no-ops; only
   genuinely new graduations are inserted.
6. After all pages succeed, write `current_slot` back to `collector_state` as
   the new `last_discovery_slot`.

**Why the DB diff, not the RPC, is the new-token filter.** `changedSinceSlot`
returns every pool that *changed* — including existing pools that merely had a
swap. That is fine and intended: discovery's correctness comes from diffing
against the `graduations` table (the idempotent insert), not from the RPC
returning only-new accounts. The RPC's job is only to keep the per-cycle set
*bounded*.

### 4.3 Code structure

- **`storm-solana`** gains a thin helper that issues one raw
  `getProgramAccountsV2` JSON-RPC request — the method is not in
  `solana-client`'s typed `RpcClient` API — and returns a typed page: the
  `(pubkey, account-data)` pairs plus the optional `paginationKey`. It reuses
  the existing RPC endpoint/transport.
- **`discover.rs`** keeps its pure `graduation_from_pool_account` validator and
  `DiscoveredGraduation` type unchanged. Its `discover_graduations` is
  rewritten: it takes the `changedSinceSlot` cursor, runs the pagination loop
  over the `storm-solana` helper, and returns the validated graduations.
- **`cycle.rs`**'s `discover_phase` is rewritten to read/write the slot cursor
  in `collector_state` around the call, per §4.2.

### 4.4 Error handling

- A failed RPC page → `discover_graduations` returns `Err` → the cycle returns
  `Err` → the daemon backs off (existing behavior). The slot cursor is **not**
  advanced, so the next cycle re-scans the same window; the idempotent insert
  makes the overlap harmless.
- An individual account that fails to parse → skip-and-log (existing behavior).

### 4.5 Testing

- The pure `graduation_from_pool_account` keeps its existing unit tests.
- New pure unit tests: building the `getProgramAccountsV2` request body, and
  parsing a synthetic V2 response (accounts + `paginationKey`) — no network.
- The live path stays in the `#[ignore]`-d integration test, updated to issue
  the V2 call. `cargo test` remains network-free; CI is unaffected.

## 5. Scope

| File | Change |
|---|---|
| `crates/storm-solana/src/` | Add the raw `getProgramAccountsV2` single-page helper |
| `bins/storm-collector/src/discover.rs` | Rewrite `discover_graduations` (pagination loop + cursor); keep the pure validator |
| `bins/storm-collector/src/cycle.rs` | `discover_phase` reads/writes `last_discovery_slot` in `collector_state` |
| `bins/storm-collector/tests/integration.rs` | Update the live test to `getProgramAccountsV2` |
| `bins/storm-collector/src/main.rs` | Redact the API key from the startup log line |

No schema migration — `collector_state` already exists.

## 6. Risks & limitations

- **Long-downtime discovery gap.** `changedSinceSlot` look-back is bounded by
  Helius's retention. If the daemon is down longer than that window,
  graduations in the gap are missed. Acceptable: the strategy is slow and holds
  a diversified basket — a few missed entries cost little. Documented, not
  engineered around in v1.
- **Free-tier dependency.** The approach relies on Helius supporting
  `getProgramAccountsV2` + `changedSinceSlot` on the free tier — now verified by
  probe, but a provider policy change could break it. Fallback if that ever
  happens: the indexer (Option C) remains available.
- **`getProgramAccountsV2` is an extension**, not standard Solana RPC. A
  different RPC provider may not offer it. The collector is already coupled to
  Helius; this redesign does not worsen that coupling.

## 7. Open decisions (resolved during implementation)

- The exact `collector_state` key name (proposed: `last_discovery_slot`).
- Whether the slot cursor uses a small negative overlap margin — harmless either
  way, since the insert is idempotent.
- The precise location of the raw V2 helper within `storm-solana` and its exact
  return type.
