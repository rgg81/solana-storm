# Design Spec — Price-Prediction Pivot

**Date:** 2026-05-20
**Status:** Approved design — pending implementation plan
**Project:** `solana-storm`

## 1. Context

`solana-storm`'s Phase 3 backtest (the `model/` package, merged to `main` on
2026-05-19) targeted *survival* — `P(token still has ≥5 SOL of pool quote
reserve at ~T0+14d)` — and ran the strategy spec's pre-committed decision gate
against the result. The gate **FAILED**: model basket total return −98.68%,
max drawdown 98.68% (well past the 40% ceiling). The model calibrated survival
well, but **survival is not price retention** — 207 of 225 traded survivors
still lost money. The strategy spec's "no edge — do not deploy" outcome was
reached.

This spec is the **pivot** that the Phase 3 result points at directly:
retarget the model from survival to **forward price/return direction**, on the
same dataset, reusing most of the `model/` package. It is not a fresh project
— the architecture, the backtest engine, the walk-forward harness, the report,
and the cost model all carry over unchanged. Only the parts that *depend on
the prediction target* are touched, plus a small upstream data-quality filter
to fight the severe class imbalance honestly.

## 2. Goal & success criteria

A revised `model/` package that:

1. trains a calibrated LightGBM classifier predicting **`positive_return`** —
   `1` iff a token's forward log return from ~T0+12h to ~T0+14d is positive,
2. runs the same walk-forward portfolio-evolution backtest with honest costs,
3. compares the model's basket against three (return-oriented) baselines,
   split by market regime,
4. emits a fresh **report** under `model/report/` for a human to read against
   the **same pre-committed decision gate**.

**The decision gate (unchanged from Phase 3, pre-committed):** the model
basket beats all three baselines, out-of-sample, after costs, across ≥2
distinct market regimes, with max drawdown ≤ 40%. The 40% ceiling is the
value fixed before Phase 3's run; it stays fixed for this pivot so the
comparison to Phase 3 is meaningful and the discipline (no goalpost-moving
after results) is honored.

**Success** is the honest report — not a particular result. A second
"no edge — do not deploy" verdict remains an acceptable, planned outcome.

## 3. Scope

**In scope:**

- A new prediction target (binary positive forward return).
- A point-in-time **garbage filter** applied uniformly to the model AND every
  baseline.
- A re-tuned entry threshold for the calibrated score (default `0.5`).
- Two return-oriented engineered features added to the existing 9.
- A return-oriented re-specification of the 3-rule heuristic baseline.

**Out of scope:**

- Dune work / dataset extension.
- Changes to the cost model, the backtest engine, the walk-forward harness,
  the report rendering, or the run CLI.
- Changes to the decision gate (drawdown ceiling, baseline-beating rule,
  regime requirement).
- New entry/exit mechanics (still hold-to-horizon — the dataset has no
  intra-period path).

## 4. Data, label, and the garbage filter

**Source:** the same `historical_graduations` table in `./storm.db` (Phase 2
deliverable, 4,755 tokens).

### 4.1 The new label — `positive_return`

In `features.py`, `LABEL_COLUMN` switches from `survived` to a derived
`positive_return`:

```
entry_price        = liq_quote_reserve / liq_base_reserve
exit_price         = outcome_quote_reserve / outcome_base_reserve
forward_log_return = log( exit_price / entry_price )
positive_return    = 1  if  forward_log_return > 0  else  0
```

Token decimals cancel in the ratios. Edge cases:

- **Abandoned tokens** (`outcome_quote_reserve = 0`) → exit price 0 → log
  return −∞ → label `0`. A definite loser, exactly as the backtest realises
  (−100%).
- **NaN entry liquidity** (~2 tokens) → entry price undefined → label `NaN`
  → row dropped by the garbage filter (Rule 1) before training.

The `survived` column stays in the loaded frame (Phase 2 set it) but is no
longer the label and not a feature. The outcome-reserve columns stay in
`LEAKAGE_FORBIDDEN` — never features; used only for label derivation and the
backtest's exit pricing.

### 4.2 The garbage filter — point-in-time, applied uniformly

A new `model/filter.py` exports `filter_garbage(df, config) -> df`, called
from `run.py` between `load_graduations` and `run_walkforward`. Three
conjunctive rules, all on point-in-time columns (no outcome leakage):

1. **Entry liquidity present and adequate.** `liq_quote_reserve` is non-NaN
   AND ≥ `min_entry_liq_lamports` (default 1 SOL = 1e9 lamports). Drops the
   two NaN-liq tokens and any near-zero-pool-depth case.
2. **Deployer not a spam-bot.** `deployer_prior_launches ≤
   max_deployer_prior_launches` (default 500). Drops the extreme-tail serial
   deployers (the dataset's max is ~588k prior launches).
3. **Curve completed normally.** `curve_real_sol_reserves` is NaN (kept —
   LightGBM handles it) OR ≥ `min_curve_sol_lamports` (default 10 SOL = 1e10
   lamports).

The filter is **deliberately permissive** — only clearly-hopeless tokens are
dropped. The active picking happens at the *next* layer (model or heuristic).
Applying it to the dataset uniformly — not just to model training — keeps every
comparison apples-to-apples: model + all three baselines see the same filtered
universe. The backtest evaluates *filter + picker*, not *picker alone*.

**Why this replaces `is_unbalance=True`.** Filtering changes the *prior* the
model sees rather than just reweighting losses. Among the kept tokens, the
positive-class density rises (~7% on the full set → ~10% after filtering,
estimated; pinned by the implementation run), and the model trains on the
harder, more informative distinction. `is_unbalance=True` stays as a deferred
LightGBM lever — applied only if the post-filter calibration plot is visibly
poor.

## 5. Features

Strictly point-in-time (known at or before T0+12h). The 8 raw and 9
engineered features from Phase 3 carry over verbatim. Two new engineered
features specific to this target:

- **`curve_sol_to_entry_liq_ratio`** = `curve_real_sol_reserves /
  liq_quote_reserve`. The fraction of the bonding curve's accumulated SOL
  that's actually in the entry pool. A serious graduated launch retains most
  of it; a churn-out has less. `_safe_divide` keeps NaN propagation honest.
- **`entry_log_price`** = `log(liq_quote_reserve / liq_base_reserve)`. The
  actual entry spot price (log scale). Phase 3 had it only implicitly in
  ratios.

`_ENGINEERED_FEATURE_COLUMNS` grows from 9 to 11; `FEATURE_COLUMNS` from 17 to
19. Missing values still propagate as NaN — never imputed.

## 6. The model

**No code change to `survival.py`.** Same LightGBM gradient-boosted classifier
+ isotonic calibration on a time-ordered held-out slice. Same hyperparameters.
The only changes that reach the model are the new label (`positive_return`)
and the filtered training set.

The class imbalance after filtering is still real (~10% positive) but milder
than the raw ~7%. Calibration is the critical layer — already explicit.

## 7. Baselines

The same three baselines as Phase 3, all operating on the **filtered**
universe:

1. **Buy-everything** — every token in the filtered set, equal weight.
2. **Random basket** — same size as the model basket, seeded. Identical to
   Phase 3.
3. **Three-rule heuristic — return-oriented (re-specified):**
   - **Entry liquidity ≥ floor** (e.g., `liq_quote_reserve ≥ 10 SOL = 1e10
     lamports`) — strong pool depth.
   - **Deployer experience window** — `deployer_prior_launches ∈ [1, 30]` —
     has launched before, not a serial churner.
   - **Curve final SOL ≥ floor** (e.g., `curve_real_sol_reserves ≥ 70 SOL =
     7e10 lamports`) — the curve completed with significant capital.

The point stands: if ML can't beat three transparent rules, the ML is
pointless complexity.

## 8. Strategy — basket from scores

A token enters the model basket at its T0+12h instant iff its calibrated
`positive_return` score ≥ `entry_threshold` AND a slot is free.

**Default `entry_threshold = 0.5`** — re-tuned from Phase 3's 0.55 because
the positive-class base rate dropped from 68% (survival) to ~10% post-filter
(return). With the base rate at ~10%, a 0.5 calibrated probability is already
a strong signal; expect a small high-conviction basket. The threshold is
tunable from `Config` / `run.py --entry-threshold`.

Everything downstream — slot count (N=20), equal weighting, capital recycling,
honest costs, hold-to-horizon exit — is unchanged from Phase 3.

## 9. Walk-forward & regime validation

No change. Expanding-window monthly folds; ~5 folds on the real dataset; the
explicit no-leakage test (`train_times.max() < test_month_start`) still
applies; regime labelling from the true full-population graduation rate
(`TRUE_MONTHLY_GRADUATIONS`); per-regime model return in the report; metrics
still total return, max drawdown, calibration, fat-tailed outcome
distribution. Classifier accuracy is still not used.

## 10. Architecture — touched files

`model/` package structure unchanged. Differences from Phase 3:

| File | Change |
|---|---|
| `model/filter.py` | **NEW** — `filter_garbage(df, config)` and the three filter checks |
| `model/tests/test_filter.py` | **NEW** — unit tests, one per rule + conjunctive + NaN handling |
| `model/features.py` | **Modified** — `LABEL_COLUMN = "positive_return"`; new derivation; +2 engineered features |
| `model/tests/test_features.py` | **Modified** — fixture + assertions for the new label and features |
| `model/baselines.py` | **Modified** — `heuristic_basket` re-specified for the return target |
| `model/tests/test_baselines.py` | **Modified** — fixture + assertions for the new heuristic |
| `model/config.py` | **Modified** — `entry_threshold = 0.5`; 3 new filter-threshold fields |
| `model/tests/test_config.py` | **Modified** — defaults assertions updated |
| `model/run.py` | **Modified** — one `filter_garbage` call between `load_graduations` and `run_walkforward` |
| `model/walkforward.py` | **Modified** — `_HEURISTIC_*` constants updated to match the new heuristic's 3 rules (adds a curve-SOL floor; deployer rule becomes a range) |
| All other `model/*.py` | **Unchanged** — `data.py`, `costs.py`, `regime.py`, `survival.py`, `backtest.py`, `report.py` |

## 11. Error handling & testing

TDD discipline carries over: every change is written test-first, run-red,
implement, run-green, commit. The no-leakage tests still apply unchanged:

- `test_features.py` asserts no outcome/label column reaches `X`.
- `test_backtest.py` asserts the simulator reads outcome reserves only at
  exit.
- `test_walkforward.py` asserts no test token predates its training cutoff.

`test_filter.py` is new — one test per filter rule (drops the wrong side,
keeps the right side, conjunctive AND) plus a NaN-handling test (NaN entry
liq is DROPPED; NaN curve is KEPT).

Pure functions are unit-tested with synthetic frames; `run.py` is exercised
by the real backtest run, not by a `pytest` test (same rationale as Phase
3's `run.py`).

## 12. Risks & honest caveats

Phase 3's caveats carry over verbatim into the new `report.md`'s Caveats
section: hold-to-horizon (no peak capture), cost-basis equity curve (max
drawdown is a lower bound), thin feature set, approximate snapshot timing,
limited statistical power. Two new ones specific to this pivot:

- **The class imbalance is real; the filter is the lever.** ~10% positive
  class after filtering (estimated; pinned by the implementation run).
  Calibration matters. `is_unbalance=True` stays a deferred LightGBM option.
- **The costs are still the costs.** Phase 3's −99% baselines warn that
  honest exit-liquidity on dying pools is crushing. Even a perfect
  direction-predictor has to outweigh the −100% drags with rare fat-tail
  winners. If the filtered dataset's tail isn't fat enough, no amount of
  better classifying saves the basket. The gate surfaces this honestly.

## 13. Open decisions (resolved during implementation)

- The exact thresholds for the three garbage-filter rules (entry-liq floor;
  deployer-launch ceiling; curve-final-SOL floor).
- The exact thresholds for the new heuristic baseline's three rules.
- Exact names and default values for the new `_HEURISTIC_*` constants in
  `model/walkforward.py` (a curve-SOL floor plus the deployer-range bounds).
- Whether to expose any filter threshold as a CLI flag in `run.py` (the entry
  threshold is already exposed; the others may not need to be).
- Whether to enable `is_unbalance=True` in LightGBM — deferred; revisit only
  if the post-filter calibration plot is visibly poor.
