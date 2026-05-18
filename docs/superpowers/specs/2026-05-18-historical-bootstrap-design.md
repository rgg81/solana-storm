# Design Spec — Phase 2: Historical Dataset

**Date:** 2026-05-18 (revised 2026-05-19)
**Status:** Approved design — pending implementation plan
**Project:** `solana-storm`

## 1. Context

`solana-storm`'s Phase 1 built `storm-collector`, an always-on daemon to collect
pump.fun graduation data live. Phase 1 is complete and merged.

**A strategic re-prioritisation (2026-05-19):** the historical dataset now
matters more than the live daemon. The project's critical path is **Phase 2
(assemble a historical dataset) → Phase 3 (backtest it)**. The live collector is
**parked** — built and merged, but it will not be run or relied upon now. No
data is mined live. Whether and how a live component is revived is a decision
deferred until *after* the backtest reveals whether a real edge exists.

This spec is therefore the **historical dataset** — the primary dataset the
strategy will be validated on. It supersedes the original "historical bootstrap"
framing in which history merely supplemented a live feed.

Earlier finding, still relevant: historical features cannot be reconstructed via
Solana RPC — past account state is overwritten. They come from a third-party
indexer. The spike confirmed **Dune Analytics** delivers them — see
`2026-05-18-historical-bootstrap-spike-findings.md`.

## 2. Goal & success criteria

A **one-time Python ETL** that pulls a historical pump.fun graduation dataset
from the Dune API into a single self-contained SQLite table — the dataset
Phase 3 will backtest on.

**Success:** a `historical_graduations` table holding several thousand
PumpSwap-era graduated tokens, each with a settled outcome label and the feature
set Dune reliably provides.

The spike already resolved the go/no-go gate with a **GO** — Dune delivers the
data within the free credit budget.

## 3. Principle: features defined by the data

The feature set is **defined by what the historical (Dune) data actually
provides** — not borrowed from the live collector's schema. The live collector's
`feature_snapshots` schema (Lean v1) was shaped by live-RPC constraints; forcing
the historical data into it would mean discarding good signals — for example the
deployer's prior-launch history, which Dune provides cleanly and which the
strategy spec calls the *strongest* signal group — only because the live column
meant something narrower.

So the historical dataset is **self-contained and history-native**. When the
live component is revived post-backtest, *it* will be aligned to whatever
feature set the backtest validated — the correct order.

## 4. Design

### 4.1 Storage — a self-contained table

A single SQLite table, `historical_graduations`, **created and owned by the
Python ETL** (`CREATE TABLE IF NOT EXISTS`). One row per graduated token —
graduation facts, features, and outcome label all together (unlike the live
collector's three-table lifecycle, every historical fact is already settled, so
one flat row is natural and is exactly the shape a Phase 3 ML backtest wants).

The table lives in the project SQLite database but is **decoupled** from the
live collector: no `storm-store` Rust-crate change, no migration, no shared
table. The live collector's tables are untouched and parked.

### 4.2 The feature set (history-native, from Dune)

Per the spike, all sourced from Dune's decoded pump.fun / PumpSwap / SPL-token
tables:

- **Graduation facts** — mint, PumpSwap pool, bonding curve, LP mint, graduation
  time (T0) and slot, the migrator wallet.
- **Outcome label** — `survived`: the pool's quote (wSOL) reserve at ~T0+14d is
  ≥ 5 SOL; plus the raw base/quote reserves at the outcome check.
- **Liquidity at ~T0+12h** — pool base/quote reserves; `lp_burned` (a
  PumpSwap-era heuristic).
- **Bonding-curve final state** — real SOL reserves, real token reserves, token
  total supply at graduation.
- **Contract flags** — mint-authority present, freeze-authority present.
- **Holder distribution (best-effort)** — holder count, top-10 / top-20
  concentration at ~T0+12h. NULL where Dune queries time out — the spike flagged
  this as the one high-timeout-risk group.
- **Deployer signal (now first-class)** — the deployer wallet, its count of
  **prior pump.fun launches**, and its **age on pump.fun** at graduation. This
  is the history-native deployer fingerprint: Dune provides it cleanly, and it
  is closer to the strategy spec's intent ("prior tokens launched and their
  outcomes") than the live collector's recent-transaction-count proxy.

Features Dune cannot reliably supply (e.g. `pool_supply_fraction`, the creator's
remaining bag fraction) are stored NULL — standard for ML, which handles missing
features.

### 4.3 Scope

- **PumpSwap era** — the window Dune's `pump_call_migrate` covers: ~November
  2025 to present (~62,700 graduations as of the spike).
- **Target ~5,000 graduations**, stratified across the months so every market
  regime in the window is represented — Phase 3's validation requires reporting
  performance across ≥2 distinct regimes. Sample size is configurable.
- Tokens that graduated less than ~16 days ago are excluded — their outcome is
  not yet settled.

### 4.4 ETL

A Python package (`bootstrap/`): a Dune API client, the per-stage SQL queries
(from the spike findings), pure transform and sampling functions, and a loader
that writes the `historical_graduations` table. The ETL is **idempotent and
resumable** — keyed on mint, with each Dune stage's results cached to local disk
so a crash never re-spends Dune credits. A **pilot run** on a small sample
validates the pipeline end-to-end before the full extraction. The Dune free
budget is 2,500 credits; the full ~5,000-token run is estimated at ~341.

## 5. Risks & honest caveats

- **Holder distribution timeout risk** — the one feature group at real risk of
  Dune free-engine (2-minute) timeouts; NULL is the accepted fallback.
- **Reconstructed features are imperfect** — snapshot timing is approximate
  (~T0+12h), and some features are NULL. Phase 3 must treat this as the
  rough-but-usable dataset it is.
- **Indexer dependency** — the dataset's quality depends on Dune's decoding
  coverage and accuracy. One-time, so there is no ongoing dependency.
- **Live/historical reconciliation is deferred** — when a live component is
  revived after the backtest, its feature set must be reconciled with the
  validated historical one. That is explicitly a later problem, not this spec's.

## 6. Open decisions (resolved during implementation)

- The exact `historical_graduations` column list and SQLite types.
- The exact sample size and the month-stratification method.
- The on-disk cache layout that gives the ETL its resumability.
