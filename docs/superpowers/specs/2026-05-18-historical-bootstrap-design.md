# Design Spec — Phase 2: Historical Bootstrap

**Date:** 2026-05-18
**Status:** Approved design — pending implementation plan
**Project:** `solana-storm`

## 1. Context

`solana-storm`'s Phase 1 (the data foundation) is complete and merged: the
`storm-collector` daemon discovers newly-graduated pump.fun tokens, snapshots
their features at T0+~12h, and records outcomes at the ~14-day horizon —
writing the `graduations`, `feature_snapshots`, and `outcomes` tables of the
`storm-store` SQLite database.

Phase 2 is the **historical bootstrap** — assembling an initial labeled dataset
from *past* graduations so Phase 3's model and walk-forward backtest are not
blocked for months waiting on live data to mature. The strategy design
(`docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md`, §9)
earmarked this: "Pull historical graduations and outcomes from indexed datasets;
reconstruct features via RPC for a sampled subset."

**A finding that reshapes §9's mechanism.** §9 assumed historical features could
be "reconstructed via RPC." They cannot, on free infrastructure: Solana's RPC
`getAccountInfo` returns only an account's *current* state — past account state
is overwritten and unrecoverable without a paid archival node or heavy
transaction-replay ETL. Historical point-in-time features must therefore come
from a **third-party indexer's** already-decoded data. The strategic conclusion
of §9 still holds (a head-start dataset, with live data as the gold standard) —
only the extraction mechanism changes.

## 2. Goal & success criteria

A **one-time ETL** that loads a historical pump.fun graduation dataset —
graduations, best-effort features, and settled outcomes — into the existing
`storm-store` SQLite, so Phase 3 can run an early walk-forward backtest.

**Success:** the `storm-store` database holds several thousand PumpSwap-era
graduations marked `source = 'historical'`, each with an outcome label and as
complete a feature row as the chosen indexer reliably provides.

**An honest non-failure outcome:** if the spike (Section 3) finds that no free or
low-cost indexer delivers usable data, Phase 2 concludes with that finding and
the project falls back to **live-only** — a slower but cleaner path to Phase 3.
"The bootstrap was not worth it" is an acceptable, pre-agreed result.

## 3. Indexer choice & the spike gate

Three indexed-data sources were evaluated. Phase 2 is a **one-time bulk pull**,
not an ongoing feed — so a free trial or free-credit allowance can suffice.

| Source | Assessment |
|---|---|
| **Dune** *(recommended primary)* | Free plan: 2,500 API credits; SQL over decoded Solana data including pump.fun; community pump.fun / PumpSwap dashboards exist whose queries can be adapted. A one-time pull is a handful of large SQL queries exported to CSV — economical on credits. |
| **Bitquery** *(fallback)* | The most pump.fun-native API — explicit graduation, bonding-curve, holder, and migration endpoints. Free tier is a 10K-point, one-month trial; sufficient for a bounded one-time pull or to fill pump.fun-specific gaps Dune cannot cover. |
| **Flipside** *(second fallback)* | Genuinely free and a very large Solana dataset, but it retired its SQL Studio in 2025 — access is now via Data API / Snowflake / MCP, adding friction; pump.fun-specific decoding is less certain. |

**The spike gate.** The implementation plan's **first task is a spike**, not
pipeline code: obtain the account, run a small real extraction (a few dozen
graduations with their features and outcomes), and confirm the source delivers
what Phase 2 needs. Only if the spike passes does the full ETL get built. This
is the lesson from the Phase 1 discovery defect — an unverified data-source
assumption sank that design; Phase 2 verifies first.

**Budget.** A one-time pull is expected to fit within the free tiers. If the
spike shows it does not, a *single month* of a low-cost indexer plan is a
bounded, one-time cost well within the project's ~$50/month ceiling — not a
recurring commitment. No ~$500-tier infrastructure.

## 4. Design

### 4.1 Scope

- **PumpSwap era only — roughly March 2025 to now (~14 months).** pump.fun
  graduations before PumpSwap launched migrated to Raydium, a different pool
  structure that `storm-pumpfun` does not parse. Restricting the historical
  window to the PumpSwap era keeps the bootstrap dataset structurally consistent
  with what the live collector produces. Fourteen months is ample for a
  multi-thousand-token sample.
- **Target ~3,000–5,000 graduations**, sampled across the window so the dataset
  spans both manic and quiet market periods — Phase 3's validation requires
  performance to be reported across ≥2 distinct regimes.
- **Feature set: best-effort.** Whatever features the chosen indexer reliably
  provides, mapped onto the existing `feature_snapshots` columns. Features it
  cannot supply are stored as `NULL` — standard for ML, which handles missing
  features. The liquidity, holder-distribution, bonding-curve, and
  contract-flag groups are expected to map well; the deployer-wallet signals are
  the hardest to source historically and may be partial or absent.
- **Outcomes are settled.** A token that graduated months ago has a
  long-decided fate; the indexer can report its pool liquidity at (or well past)
  the ~14-day horizon. Historical rows therefore enter the database already
  complete — graduation, features, and outcome together.

### 4.2 Architecture

- **A Python ETL** (a new `bootstrap/` directory), not Rust. It is a one-shot
  extraction, not the always-on daemon; it belongs next to the Phase 3 Python
  code, and it writes the same `storm-store` SQLite — the strategy spec's
  "SQLite is the contract." Keeping it out of the Rust daemon crate avoids
  coupling a throwaway ETL to the production collector.
- **Migration `0003`** to `storm-store`:
  - a `source` column on `graduations` — `'live'` (default) or `'historical'` —
    so Phase 3's walk-forward validation can distinguish rougher historical rows
    from gold-standard live ones and weight or split on them;
  - relaxing the `NOT NULL` constraint on the `feature_snapshots` columns the
    indexer may not supply, so partial historical feature rows are
    representable.
- **Idempotent.** The ETL is keyed on `mint` (like the live collector's
  `insert_graduation`) — re-running it never double-inserts. Historical rows are
  inserted in the terminal `outcome_done` status, so the live collector's phase
  queries naturally skip them.

### 4.3 Data flow

For the chosen indexer: query PumpSwap-era graduations across the sampled
window → for each, pull or derive the available point-in-time features
(best-effort near T0+12h; `snapshot_at` records the actual reconstructed time)
and the settled outcome (survived/rugged by the same liquidity rule the live
collector uses) → write `graduations` + `feature_snapshots` + `outcomes` rows,
all marked `source = 'historical'`.

## 5. Risks & honest caveats

- **Historical features are rougher and partial.** This is the strategy spec's
  §10 "largest practical risk." Reconstructed features will not perfectly match
  live-collected ones — snapshot timing is looser, some feature groups may be
  missing. Phase 3's walk-forward must lean increasingly on live data as it
  accumulates and **report historical-only vs. live-inclusive performance
  separately**; a backtest that only works on rough historical data is not
  trusted.
- **Indexer dependency.** The bootstrap depends on a third-party indexer's
  coverage, decoding accuracy, and free-tier terms — none of which the project
  controls. Mitigated by the one-time nature (no ongoing dependency) and the
  spike gate.
- **The spike may fail.** If no free/low-cost source delivers usable data,
  Phase 2 stops and the project proceeds live-only. This is planned for, not a
  failure.
- **Sampling bias.** A non-representative sample (e.g. skewed toward one regime,
  or toward tokens the indexer happens to cover well) would bias the backtest.
  The sampling method is an explicit design point of the implementation plan,
  and the regime split is the check.

## 6. Open decisions (resolved during implementation)

- The final indexer — resolved by the spike (Section 3).
- The exact sample size and sampling method across the PumpSwap-era window.
- The precise mapping of indexer fields onto `feature_snapshots` columns, and
  which columns end up `NULL` for historical rows.
- Whether `graduation_slot` is sourced from the indexer or left at a sentinel
  for historical rows (migration `0003` accommodates either).
