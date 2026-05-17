# Solana Storm

An ML-driven **survival-scoring strategy** for newly-graduated pump.fun tokens
on Solana. The system scores each graduated token's probability of *surviving*
(not rugging) and trades a filtered, diversified basket of the likely survivors
— competing on intelligence, not speed, on minimal infrastructure.

> Pivoted from an earlier speed-based MEV plan. See the design spec:
> [`docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md`](docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md).

## Status

Phase 0 (repo cleanup & pivot setup) complete. Build phases 1–6 are tracked as
issues under the [pump.fun Survival Strategy milestone](https://github.com/rgg81/solana-storm/milestones);
Phase 1 — data foundation — is next.

## Workspace

| Crate | Role |
|---|---|
| `storm-core` | Config, errors, shared math |
| `storm-solana` | Solana RPC + DEX pool parsing |
| `storm-store` | SQLite persistence (sqlx) |
| `storm-cli` | Command-line inspection tool |

## Approach

1. Detect tokens graduating onto a real AMM.
2. Snapshot a rich on-chain feature set a few hours later.
3. Score survival probability with an ML model.
4. Paper-trade a basket of high-scoring tokens; validate honestly before any
   real capital.
