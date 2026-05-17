# Design Spec — pump.fun Token Survival Strategy

**Date:** 2026-05-17
**Status:** Approved design — pending implementation plan
**Project:** `solana-storm`

## 1. Context

`solana-storm` began as a 22-week curriculum for a speed-based Solana MEV bot —
arbitrage, liquidations, Jito bundles. Weeks 1–6 shipped a working Rust
foundation: Solana RPC access, DEX pool parsing (Raydium, Orca), a Binance feed,
and a SQLite store (the `storm-*` crates).

An honest assessment concluded that speed-based Solana MEV is structurally
unwinnable for a solo builder on commodity infrastructure: it is a
winner-take-all latency race dominated by co-located, well-capitalised teams.

This spec is the **pivot** — compete on *intelligence*, not speed. A slow,
ML-driven strategy operating on a timescale where latency is irrelevant, on
minimal (free-tier) infrastructure, reusing the existing `storm-*` foundation.
It supersedes the original plan from Week 7 onward.

## 2. Goal & success criteria

**Goal:** a strategy that makes money — real, risk-managed returns — by
ML-scoring newly *graduated* pump.fun tokens and trading a filtered basket of
the likely survivors.

**Success is decided by validation, not hope.** The project succeeds if the
strategy clears a pre-committed validation gate (Section 8). A validation verdict
of *"no real edge — do not deploy"* is an **acceptable, non-failure outcome**:
the deliverable is then a proven answer plus a reusable data and research system.

**Honest framing:** this is a speculative *directional portfolio* — not yield,
not passive income. It compounds or it bleeds. Even in the good case, expect a
modest edge with high variance and large drawdowns. Real capital is a later,
conditional decision; v1 is paper only.

## 3. Constraints

- **Minimal infrastructure** — free / very-low-cost tiers only. No paid gRPC, no
  co-location, no latency arms race.
- **Slow pace** — every decision on an hours-to-weeks timescale;
  latency-insensitive by construction.
- **ML-driven** — the edge, if any, comes from a model, not from speed.
- **Reuse** the existing `storm-*` Rust crates wherever possible.

## 4. Scope

**In scope (v1):** pump.fun graduated tokens — tokens whose bonding curve
completed and that migrated to a real AMM (Raydium before ~March 2025, PumpSwap
after). Paper trading only.

**Out of scope (v1), possible v2:** other launchpads (LetsBONK, etc.); live
real-capital trading; social / off-chain features; automated execution.

## 5. Core concept & prediction target

**End-to-end loop:** detect tokens graduating onto a real AMM → after a short
observation window, snapshot a rich on-chain feature set → an ML model scores the
token → tokens above a score threshold enter a paper-traded basket, held and
exited by fixed rules → each position's real outcome is recorded and becomes a
future training label. A slow, batch loop.

**Prediction target — two parts, deliberately separated by how learnable they
are:**

- **Survival model (the defense)** — P(the token still has real, honest
  liquidity and is fairly tradeable ~N days later; did *not* rug or die).
  Genuinely learnable — rugs leave on-chain fingerprints.
- **Upside model (weak)** — conditional on survival, expected forward return.

**The bankable edge is the survival model.** Predicting *which* survivors moon is
close to noise and is not treated as a real signal. The strategy earns by
*avoiding landmines* and holding a diversified basket of vetted survivors.
Survival score is the gate; the upside model is only a soft tiebreaker.

**Horizon N:** approximately 1–4 weeks; the exact value is pinned during
validation.

## 6. Data & features

**Source:** Solana RPC only (Helius free tier is sufficient for a slow batch
job). On-chain data exclusively in v1 — no social / off-chain features (trivially
faked; extra infrastructure for weak signal).

**Feature set:**

| Group | Signals |
|---|---|
| Creator/deployer fingerprint *(strongest)* | wallet age, funding source, prior tokens launched and their outcomes |
| Holder distribution | holder count, top-10/20 concentration, dev's remaining bag, sniper-wallet share |
| Liquidity | pool size at graduation, LP value vs market cap, LP lock/burn status |
| Bonding-curve behavior | time-to-graduate, unique buyers, bundle/coordination detection, volume concentration |
| Contract flags | mint authority, freeze authority status |
| Early post-graduation behavior | dev selling, holder growth, volume sustain vs collapse during the observation window |

**Snapshot timing:** features are captured at **T0 + 6–24h** (T0 = graduation) —
*observe a window, then snapshot*. This exposes post-graduation behavior to the
model and auto-excludes tokens that die within hours. Acceptable because the
strategy is slow: there is no need to buy at the graduation instant.

## 7. Strategy — from scores to a basket

- **Entry:** survival score above a threshold (tuned in validation); entry
  ~6–24h post-graduation, after the observation window.
- **Sizing:** equal-weight, small fixed slices (~3–5% of bankroll per position).
  No conviction sizing — the upside model is too weak to size on.
- **Basket:** ~15–30 concurrent positions. Diversification *is* the defense —
  one rug costs roughly one slice.
- **Exits:**
  - **Stop-loss** — cut landmines the model let through.
  - **No fixed take-profit** — the rare 10–50× winner is the entire P&L; a
    profit cap would kill the strategy. Winners exit only via a *trailing* stop
    or at the horizon.
  - **Horizon exit** — close anything still held at N weeks.
  - **Re-score exit** — if fresh data craters a token's survival score, exit
    early.
- **Capital recycling:** freed capital from exits funds new entries; the
  bankroll stays spread across the basket.
- **Paper simulator:** v1 runs entirely as simulation. Fills are modeled
  honestly — realistic entry/exit prices including DEX fees, slippage, and the
  exit-liquidity problem (a dying token's liquidity can vanish; the marked price
  is not always achievable).

## 8. Validation & the decision gate

Validation's job is to *try to kill the strategy*; it is trusted only if it
survives.

- **Time-split, walk-forward only** — train on tokens graduated before a cutoff,
  test on tokens after; never random splits (which leak the future). Roll the
  window forward across history. (The `walk-forward-validation` skill supports
  this.)
- **Point-in-time features, no leakage** — every feature uses only data
  available at the snapshot instant.
- **Beat the baselines** — out-of-sample and after costs, the basket must beat:
  (a) buy-everything equally; (b) a random basket of equal size; (c) a 3-rule
  heuristic (LP burned + mint renounced + low top-holder concentration). If ML
  cannot beat three simple rules, the ML is pointless complexity.
- **Honest costs** — fees, slippage, and exit-liquidity modeled against each
  pool's real depth.
- **Metrics** — portfolio-level: total return, max drawdown, the full fat-tailed
  outcome distribution (median ≠ mean); plus probability calibration. *Not*
  classifier accuracy, which is misleading under heavy class imbalance.
- **Regime split** — performance reported separately across mania and quiet
  periods.

**Decision gate (pre-committed, before results are seen):** real capital is
greenlit only if the basket **beats all three baselines, out-of-sample, after
costs, across ≥2 distinct market regimes, with a maximum drawdown the user has
pre-agreed is tolerable.** Miss the bar → do not deploy.

**Phase 2 — live paper-trading:** after a passing historical backtest, the
strategy runs forward in real time on paper, confirming the edge holds on
genuinely unseen data before any real money.

## 9. Architecture & build phases

**Language split:** Rust for data collection and on-chain extraction (reuses
`storm-*`, ideal for an always-on collector); Python for the ML and
backtest/validation (mature ecosystem; the `feature-engineering` /
`regime-detection` / `walk-forward-validation` skills are Python-oriented).
**SQLite (`storm-store`) is the contract** between them — Rust writes features,
Python reads them and writes back scores. Each half is independently testable.

**Components:**

| Component | Type | Responsibility |
|---|---|---|
| `storm-pumpfun` | new crate | graduation detection, bonding-curve parsing, PumpSwap pool reading |
| `storm-features` | new crate | turn a graduated token into the Section-6 feature vector from on-chain data |
| `storm-collector` | new bin | always-on daemon: watch graduations → snapshot features at T0+window → record outcomes |
| `storm-store` | extended | new migrations: `graduations`, `feature_snapshots`, `outcomes` |
| `model/` | new (Python) | training (LightGBM/XGBoost — suited to tabular features), batch scoring, the honest backtest simulator, baselines, walk-forward validation |
| `storm-cex`, `storm-engine` | unchanged | CEX code; not used by this project |

**Data plan:**

- **Day one — the live collector.** Built first and deployed; runs continuously.
  Every graduation is captured with point-in-time-correct features and outcomes.
  ~Tens of thousands of clean labeled examples accumulate within 2–3 months; the
  project is never blocked waiting for data.
- **In parallel — historical bootstrap.** Pull historical graduations and
  outcomes from indexed datasets (Dune / Flipside / Bitquery); reconstruct
  features via RPC for a *sampled* subset (not the full 2 years — keeps within
  the free RPC budget). Provides an early backtest without the wait.
- **Honest caveat:** reconstructed historical features are slightly rougher than
  live-collected ones. Live data is the gold standard; the bootstrap is a
  head-start. Walk-forward validation leans increasingly on live data as it
  accumulates.

**Build phases:**

1. **Data foundation** — `storm-pumpfun`, `storm-features`, `storm-store`
   schema, `storm-collector`. Live collection begins here.
2. **Historical bootstrap** — assemble the initial backtest dataset.
3. **Model + backtest** — survival model, honest simulator, walk-forward
   validation against the three baselines.
4. **Decision gate** — honest go/no-go against the pre-committed bar (Section 8).
5. **Live paper-trading** — *only if the gate passes* — forward, real-time.
6. **Real-capital decision** — *only if paper-trading confirms the edge.*

**Error handling & testing:** the collector is resilient — RPC retries via
`storm-core::backoff`, skip-and-log on tokens that fail extraction, idempotent
re-runs. Feature extraction is unit-tested against fixture tokens (the existing
codebase pattern). The validation framework is itself the strategy's test.

## 10. Risks & honest caveats

- **The edge may not exist.** Validation can return "do not deploy." This is
  planned for, not a failure.
- **Historical feature reconstruction is imperfect** — the largest practical
  risk; mitigated by the day-one live collector.
- **The memecoin long tail is adversarial** — scams, manipulation, extreme
  variance. Even a strong filter may only "lose slower."
- **Crowding** — rug detectors and token scanners exist; the intended novelty is
  rigor (probabilistic survival model + honest validation + portfolio
  construction), not the idea itself.
- **Costs bite** — slippage and exit-liquidity on thin pools are real and must be
  modeled honestly or the backtest lies.
- **Even in the good case** — modest edge, high variance, large drawdowns. Not
  income.

## 11. Open decisions (resolved during implementation / validation)

- Precise definition of the "survival" label (liquidity threshold, tradeability
  criteria).
- Exact horizon N (1–4 weeks).
- Exact survival-score threshold.
- Exact basket size, slice size, stop-loss and trailing-stop levels.
- Which indexed dataset(s) to use for the historical bootstrap.
- The user's pre-agreed maximum-drawdown tolerance (set before the decision
  gate).
- Deployment host for the always-on collector (local machine vs low-cost VPS).
