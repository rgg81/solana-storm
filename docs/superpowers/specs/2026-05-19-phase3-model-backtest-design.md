# Design Spec — Phase 3: Survival Model & Backtest

**Date:** 2026-05-19
**Status:** Approved design — pending implementation plan
**Project:** `solana-storm`

## 1. Context

`solana-storm` is an ML-driven pump.fun token *survival-scoring* strategy (see
`2026-05-17-pumpfun-survival-strategy-design.md`). Phase 1 (the `storm-collector`
live daemon) is built, merged, and parked. Phase 2 is complete: a one-time Dune
ETL assembled `historical_graduations` — 4,755 month-stratified PumpSwap-era
graduated tokens, each with a settled `survived` label and a history-native
feature set — into the project SQLite database (`storm.db`).

This spec is **Phase 3**: the survival model and the honest backtest — the
strategy spec's build-phase 3 ("Model + backtest"). Its output is the input to
the **decision gate** (strategy spec Section 8) that determines whether a live
component is worth building. The live component stays parked until that gate is
read.

## 2. Goal & success criteria

A Python `model/` package that:

1. trains a **survival model** on `historical_graduations`,
2. runs a **walk-forward, portfolio-evolution backtest** with honest costs,
3. compares the model's basket against three baselines, split by market regime,
4. emits a **report** — the input to the decision gate.

**Success** is the honest report itself — not a particular result. A verdict of
"no edge — do not deploy" is an acceptable, planned outcome.

**The decision gate (pre-committed, before any results are seen):** reviving the
live component is greenlit only if the model basket **beats all three baselines,
out-of-sample, after costs, across ≥2 distinct market regimes, with a maximum
drawdown ≤ 40%.** Miss the bar → do not deploy. The 40% tolerance is fixed here,
before the backtest is run, so the bar cannot move.

## 3. Scope

**In scope:** the survival model, the backtest simulator, walk-forward and
regime validation, the three baselines, and the report.

The backtest is **hold-to-horizon** — a position is held from entry to a fixed
horizon and exited there. The dataset provides two reserve snapshots per token
(~T0+12h and ~T0+14d), so a per-token horizon return is derivable, but the
intra-hold price *path* is not.

**Out of scope:** trailing stops, stop-losses, and re-score exits (all need the
intra-hold path the dataset lacks); extending the dataset; the live component. A
cohort-style backtest was considered and rejected — only a portfolio-evolution
simulator yields the real equity curve and max drawdown the decision gate is
stated in.

## 4. Data & features

**Source:** the `historical_graduations` table in `storm.db` (4,755 rows). The
`model/` package only reads it; it writes nothing back (there is no live scoring
in Phase 3). The `survived` label — the pool's quote reserve ≥ 5 SOL at the
~T0+14d outcome check — is already settled in the dataset.

**Model features** — strictly point-in-time, everything known at or before
**T0+12h**, so the model never sees the future:

- **Deployer** (100% populated): prior pump.fun launch count, wallet age.
- **Liquidity at ~T0+12h** (100%): pool base/quote reserves, `lp_burned`.
- **Bonding-curve final state** (~92%): real SOL reserves, real token reserves,
  token total supply.

Plus **engineered features** — transforms and ratios of the above (e.g. log
prior-launch count, liquidity-to-curve-SOL ratio). Feature engineering follows
the `feature-engineering` skill.

**Dropped:** `mint_authority_present` and `freeze_authority_present` (constant
across the cohort — zero variance, no signal); the holder-distribution columns
and the `*_fraction` columns (never collected — NULL). Missing values reach the
model as NaN and are handled natively — never fabricated.

**Outcome reserves** (`outcome_base_reserve`, `outcome_quote_reserve`, at
~T0+14d) are **never features** — they are future data, used only to compute
each token's backtest return.

## 5. The survival model

A **LightGBM gradient-boosted classifier** — well suited to a small tabular
dataset with missing values — trained to predict `survived`. Its output is a
**calibrated survival probability**, the per-token *survival score*. Calibration
is an explicit step (isotonic or Platt scaling on a held-out slice of the
training fold) because the decision gate evaluates probability calibration, not
only ranking.

## 6. Baselines

The model's basket is compared against three baselines (strategy spec Section 8).
If the ML cannot beat simple rules, the ML is pointless complexity.

1. **Buy-everything** — the basket is every graduation in the period, equal
   weight.
2. **Random basket** — a random subset, the same size as the model's basket.
3. **Three-rule heuristic** — **re-specified for this dataset.** The strategy
   spec's original rules (LP burned + mint renounced + low holder concentration)
   are not computable here — mint authority is a cohort constant and holder data
   was never collected. The available-feature equivalent: **`lp_burned`** is set,
   **and** the deployer is not a serial re-launcher (`deployer_prior_launches`
   below a threshold), **and** entry liquidity is above a threshold.

## 7. The backtest — portfolio-evolution simulator

A single paper bankroll, simulated chronologically across the dataset's ~6-month
window:

- **Slots & sizing** — the bankroll is divided into **N equal slots** (N in
  ~15–30, a parameter); each entered position is one equal-weight slice. No
  conviction sizing — the upside signal is too weak to size on.
- **Entry** — a token becomes eligible at its T0+12h. If its survival score
  clears the entry threshold and a slot is free, it enters. Entry price =
  `liq_quote_reserve / liq_base_reserve` (token decimals cancel in the ratio).
- **Exit** — held to its horizon (`outcome_checked_at`, ~T0+14d) and exited
  there. Exit price = `outcome_quote_reserve / outcome_base_reserve`. An
  abandoned token (outcome reserves 0) realises −100%.
- **Honest costs** — a DEX swap fee on each leg, plus **constant-product
  slippage**: the slice is filled against the pool's real depth (entry depth from
  `liq_*`, exit depth from `outcome_*`). This models the **exit-liquidity
  problem** directly — selling a slice into a dying token's near-empty pool
  craters the realised fill.
- **Capital recycling** — capital freed when a position exits funds the next
  eligible entry; the bankroll stays spread across the slots.
- **Output** — an equity curve over calendar time, and from it total return and
  **max drawdown**, plus the full per-position outcome distribution.

The identical simulator — same slots, same cost model — runs the model's basket
and all three baselines, so every comparison is apples-to-apples.

## 8. Walk-forward & regime validation

- **Walk-forward, time-split** — tokens are ordered by graduation time; an
  expanding window trains on every token before a cutoff and tests on the next
  ~month, rolling the cutoff forward (~4 folds across the window). The training
  cutoff strictly precedes the test tokens and every feature is point-in-time, so
  there is no lookahead leakage. Uses the `walk-forward-validation` skill.
- **Regime split** — each period is labelled by market regime (e.g. mania vs
  quiet, derived from graduation rate and activity) via the `regime-detection`
  skill; performance is reported separately per regime. If the window contains
  fewer than two distinct regimes, that is reported honestly as a limitation of
  the gate.
- **Metrics** — total return, max drawdown, the full fat-tailed outcome
  distribution (median ≠ mean), and probability calibration. **Not** classifier
  accuracy, which is misleading under heavy class imbalance.

The report is a written artifact — a markdown summary plus plots (the equity
curve, calibration, the outcome distribution) — saved under `model/`.

## 9. Architecture — the `model/` package

A Python package, decoupled from the Rust workspace exactly as `bootstrap/` is.
One clear responsibility per module:

| File | Responsibility |
|---|---|
| `model/data.py` | load `historical_graduations` from `storm.db` into a DataFrame |
| `model/features.py` | engineer the feature matrix from raw columns (pure) |
| `model/survival.py` | train the LightGBM model; calibrated scoring |
| `model/baselines.py` | the three baseline basket selectors |
| `model/costs.py` | the fill model — DEX fee + constant-product slippage |
| `model/backtest.py` | the portfolio-evolution simulator |
| `model/regime.py` | market-regime labelling |
| `model/walkforward.py` | the walk-forward harness — orchestrates train/test rolls |
| `model/report.py` | metrics and the report output |
| `model/run.py` | CLI entry point — runs the whole walk-forward backtest |
| `model/tests/` | unit tests |
| `model/requirements.txt` | pandas, numpy, lightgbm, scikit-learn, matplotlib |

## 10. Error handling & testing

- Missing feature values flow through as NaN; the model handles them. The ~2
  tokens with NULL liquidity (no derivable entry price) are excluded from the
  backtest with a logged count. A walk-forward fold with too few training or test
  rows is skipped and logged.
- Pure functions are unit-tested (the `bootstrap/` discipline): feature
  engineering, the cost model against hand-computed fills, the baseline
  selectors, and the simulator on small fixtures. The walk-forward harness has an
  explicit no-leakage test — no test token predates its training cutoff.

## 11. Risks & honest caveats

- **Hold-to-horizon understates the upside** — without the intra-hold path, a
  token that spiked then settled is scored at its horizon value, not its peak.
  This is a *conservative* bias.
- **A thin feature set** — no holder distribution, the contract flags constant.
  The model works with what Phase 2 could reliably source; the deployer signal,
  the strategy's strongest, is fully present.
- **Approximate snapshot timing** — features at ~T0+12h and the outcome at
  ~T0+14d are reconstructed, not exact.
- **Limited statistical power** — 4,755 tokens over ~4 folds is a modest sample;
  the report states confidence honestly and does not over-claim.
- **The edge may not exist** — a failing gate is a valid, planned result, not a
  project failure.

## 12. Open decisions (resolved during implementation)

- The exact slot count N, the entry score threshold, and the fold boundaries.
- The exact engineered-feature list, and whether a leakage-safe deployer
  track-record aggregate is worth adding.
- The regime-labelling method's specifics.
- The DEX fee rate and the slippage model's parameters.
