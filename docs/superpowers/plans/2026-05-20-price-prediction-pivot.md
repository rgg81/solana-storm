# Price-Prediction Pivot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retarget Phase 3's `model/` package from survival-prediction to positive-forward-return prediction, with a uniform garbage filter and a return-oriented heuristic baseline.

**Architecture:** Modify the label derivation in `features.py` (binary `positive_return` from the forward log-return ratio), add two engineered features (`curve_sol_to_entry_liq_ratio`, `entry_log_price`), add a new `model/filter.py` that drops clearly-hopeless tokens BEFORE training so all comparisons are apples-to-apples, and re-specify `baselines.heuristic_basket` with three return-oriented rules. The cost model, backtest engine, walk-forward harness, report renderer, and `survival.py` are unchanged.

**Tech Stack:** Python 3.11+, pandas 2.x, NumPy, LightGBM, scikit-learn (IsotonicRegression), pytest. Matplotlib (Agg backend) for the report's calibration plot.

**Spec:** `docs/superpowers/specs/2026-05-20-price-prediction-pivot-design.md` (commit `4688977`).

---

## Workspace

- Branch: `price-prediction-pivot` (already created from `main` at 2026-05-20).
- No worktree — work in the repo root `/home/roberto/solana-storm`.
- Pre-existing branch state: clean; the spec is the only new file on the branch.

## File structure

The package layout is unchanged. Differences from Phase 3:

| Path | Change |
|---|---|
| `model/filter.py` | **NEW** — point-in-time garbage filter; `filter_garbage(df, config) -> df` |
| `model/tests/test_filter.py` | **NEW** — unit tests for each rule + the conjunctive form |
| `model/features.py` | **Modified** — `LABEL_COLUMN = "positive_return"`, new label derivation, 2 new engineered features |
| `model/tests/test_features.py` | **Modified** — fixture + assertions for the new label and features |
| `model/baselines.py` | **Modified** — `heuristic_basket` re-specified with 3 new rules (signature change) |
| `model/tests/test_baselines.py` | **Modified** — fixture + assertions for the new heuristic |
| `model/config.py` | **Modified** — `entry_threshold = 0.5`; +3 filter-threshold fields |
| `model/tests/test_config.py` | **Modified** — defaults assertions updated |
| `model/walkforward.py` | **Modified** — `_HEURISTIC_*` constants replaced; `test_labels` derived from build_features y |
| `model/run.py` | **Modified** — one `filter_garbage` call between `load_graduations` and `run_walkforward`; log lines updated |
| `model/README.md` | **Modified** — run-log section for the pivot result |
| All other `model/*.py` | **Unchanged** — `data.py`, `costs.py`, `regime.py`, `survival.py`, `backtest.py`, `report.py` |

## Tasks

7 tasks. Each is TDD-driven: failing test → minimum implementation → green → full suite → commit. Each task ends with a commit; no task spans a commit boundary.

---

### Task 1: Config — entry_threshold 0.5 and 3 filter-threshold fields

**Files:**
- Modify: `model/config.py` (the `Config` dataclass)
- Modify: `model/tests/test_config.py` (`test_load_config_returns_the_spec_defaults`)

- [ ] **Step 1: Update the defaults test**

In `model/tests/test_config.py`, replace `test_load_config_returns_the_spec_defaults` with:

```python
def test_load_config_returns_the_spec_defaults():
    cfg = load_config()
    # storage
    assert cfg.db_path == "./storm.db"
    assert cfg.table_name == "historical_graduations"
    # the (still-loaded) Phase 2 survival rule -- 5 SOL quote reserve
    assert cfg.survival_min_quote_lamports == 5_000_000_000
    # backtest slots & sizing
    assert cfg.slot_count == 20
    # re-tuned for the post-filter ~10% positive-class base rate (spec 8)
    assert cfg.entry_threshold == 0.5
    # honest costs (0.25% PumpSwap AMM fee per leg)
    assert cfg.dex_fee_rate == 0.0025
    # calibration slice -- last 20% of each training fold
    assert cfg.calibration_fraction == 0.20
    # determinism
    assert cfg.random_seed == 20260519
    # report output
    assert cfg.report_dir == "model/report"
    # NEW: garbage-filter thresholds (spec 4.2)
    assert cfg.min_entry_liq_lamports == 1_000_000_000        # 1 SOL
    assert cfg.max_deployer_prior_launches == 500
    assert cfg.min_curve_sol_lamports == 10_000_000_000       # 10 SOL
```

- [ ] **Step 2: Run the test — verify it fails**

```bash
cd /home/roberto/solana-storm
python3 -m pytest model/tests/test_config.py::test_load_config_returns_the_spec_defaults -v
```

Expected: FAIL — `entry_threshold == 0.55` and `AttributeError` on the three new fields.

- [ ] **Step 3: Update model/config.py**

Open `model/config.py`. Change `entry_threshold` from `0.55` to `0.5` and update its trailing comment to match the new label. After the `entry_threshold` / `initial_bankroll` lines, before the `# --- Honest costs ---` section, insert the three new fields:

```python
    # --- Garbage filter (spec 4.2) -- applied uniformly to model + baselines ---
    min_entry_liq_lamports: int = 1_000_000_000        # 1 SOL
    max_deployer_prior_launches: int = 500
    min_curve_sol_lamports: int = 10_000_000_000       # 10 SOL
```

And change line 26 from:

```python
    entry_threshold: float = 0.55  # min calibrated survival score to enter
```

to:

```python
    entry_threshold: float = 0.5  # min calibrated positive-return score to enter
```

- [ ] **Step 4: Run the test — verify it passes**

```bash
python3 -m pytest model/tests/test_config.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full model test suite — catch regressions**

```bash
python3 -m pytest model/ -v
```

Expected: only the Config tests are affected by this change; everything else still passes (the only test that pinned `entry_threshold == 0.55` lives in test_config.py).

- [ ] **Step 6: Commit**

```bash
git add model/config.py model/tests/test_config.py
git commit -m "$(cat <<'EOF'
Task 1: Lower entry_threshold to 0.5 and add filter-threshold fields

The pivot target's positive-class base rate is ~10% post-filter (vs Phase 3
survival's 68%), so a 0.5 calibrated probability is already a strong signal
-- expect a small high-conviction basket. Three new fields hold the garbage
filter's thresholds (entry-liq floor 1 SOL; deployer-spam ceiling 500;
curve-SOL floor 10 SOL); they are consumed by model/filter.py in Task 2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Create model/filter.py — the garbage filter

**Files:**
- Create: `model/filter.py`
- Create: `model/tests/test_filter.py`

- [ ] **Step 1: Write the failing tests**

Create `model/tests/test_filter.py`:

```python
"""Unit tests for model.filter -- the point-in-time garbage filter."""

import numpy as np
import pandas as pd

from model.config import load_config
from model.filter import filter_garbage


def _df(**overrides):
    """A baseline-good 3-token frame, easily mutable per test."""
    base = {
        "liq_quote_reserve": [5.0e10, 5.0e10, 5.0e10],     # 50 SOL each
        "deployer_prior_launches": [3, 3, 3],
        "curve_real_sol_reserves": [8.5e10, 8.5e10, 8.5e10],
        # other columns the filter does not read -- present for plausibility
        "graduation_time": [1000, 2000, 3000],
        "survived": [1, 1, 1],
    }
    base.update(overrides)
    return pd.DataFrame(base, index=pd.Index(["A", "B", "C"], name="mint"))


def test_filter_keeps_a_fully_good_frame_unchanged():
    df = _df()
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert list(kept.index) == ["A", "B", "C"]


def test_filter_preserves_the_mint_index_and_column_set():
    df = _df()
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert kept.index.name == "mint"
    assert list(kept.columns) == list(df.columns)


def test_rule1_drops_below_entry_liquidity_floor():
    df = _df(liq_quote_reserve=[5.0e10, 5.0e8, 5.0e10])  # B has 0.5 SOL
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # rule 1: liq_quote >= 1 SOL -- B fails.
    assert set(kept.index) == {"A", "C"}


def test_rule1_drops_nan_entry_liquidity():
    df = _df(liq_quote_reserve=[5.0e10, np.nan, 5.0e10])
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # NaN entry liq -> rule 1 fails (NaN not >= floor).
    assert set(kept.index) == {"A", "C"}


def test_rule2_drops_over_deployer_spam_ceiling():
    df = _df(deployer_prior_launches=[3, 3, 1_000_000])  # C is a spam bot
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # rule 2: deployer_prior_launches <= 500 -- C fails.
    assert set(kept.index) == {"A", "B"}


def test_rule3_keeps_nan_curve_real_sol():
    """NaN curve_real_sol is KEPT (LightGBM handles NaN); only known-low fails."""
    df = _df(curve_real_sol_reserves=[8.5e10, np.nan, 8.5e10])
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # rule 3: NaN OR >= floor -- B's NaN keeps it.
    assert set(kept.index) == {"A", "B", "C"}


def test_rule3_drops_below_curve_real_sol_floor():
    df = _df(curve_real_sol_reserves=[8.5e10, 5.0e9, 8.5e10])  # B has 5 SOL
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # rule 3 floor is 10 SOL -- B fails.
    assert set(kept.index) == {"A", "C"}


def test_filter_is_a_conjunction_of_all_three_rules():
    df = pd.DataFrame(
        {
            "liq_quote_reserve": [5.0e10, 5.0e8, 5.0e10, 5.0e10],         # B fails r1
            "deployer_prior_launches": [3, 3, 1_000_000, 3],              # C fails r2
            "curve_real_sol_reserves": [8.5e10, 8.5e10, 8.5e10, 5.0e9],   # D fails r3
            "graduation_time": [1000, 2000, 3000, 4000],
            "survived": [1, 1, 1, 1],
        },
        index=pd.Index(["A", "B", "C", "D"], name="mint"),
    )
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # only A passes every rule.
    assert set(kept.index) == {"A"}


def test_filter_handles_an_empty_frame():
    df = _df().iloc[:0]
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert len(kept) == 0
    assert list(kept.columns) == list(df.columns)


def test_filter_respects_overridden_thresholds():
    df = _df()  # all 3 rows pass the defaults
    # tighten rule 1 to 100 SOL -- now all fail.
    cfg = load_config(min_entry_liq_lamports=100_000_000_000)
    assert len(filter_garbage(df, cfg)) == 0
```

- [ ] **Step 2: Run the tests — verify they fail**

```bash
python3 -m pytest model/tests/test_filter.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'model.filter'`.

- [ ] **Step 3: Create model/filter.py**

```python
"""Point-in-time garbage filter -- drops clearly-hopeless tokens (spec 4.2).

Applied uniformly between data loading and walk-forward training. The filter
is deliberately permissive: only clearly-hopeless tokens are dropped, so the
model and every baseline see the same filtered universe and the backtest
evaluates `filter + picker`, not `picker alone`.

Three conjunctive rules, all on point-in-time columns (no outcome leakage):
  1. liq_quote_reserve is non-NaN AND >= min_entry_liq_lamports
  2. deployer_prior_launches <= max_deployer_prior_launches
  3. curve_real_sol_reserves is NaN (kept -- LightGBM handles NaN) OR
     >= min_curve_sol_lamports

NaN handling differs deliberately per rule: rule 1 requires the value to be
known (a token with unknown entry liquidity cannot be evaluated honestly);
rule 3 allows missing curve data (an absent value carries less information
than a known-low one and is best left to LightGBM's native NaN routing).
"""

from __future__ import annotations

import logging

import pandas as pd

from model.config import Config

log = logging.getLogger("model.filter")


def filter_garbage(df: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Apply the three garbage-filter rules and return the kept frame.

    Args:
        df: the raw graduation frame (model.data.load_graduations output),
            indexed by mint.
        config: the run config; only the three `*_lamports` / `max_*` fields
            are read.

    Returns:
        A subset of `df` keeping only the rows that pass all three rules.
        Columns and index name are preserved; an empty input yields an
        empty output of the same shape.
    """
    rule1 = (
        df["liq_quote_reserve"].notna()
        & (df["liq_quote_reserve"] >= config.min_entry_liq_lamports)
    )
    rule2 = df["deployer_prior_launches"] <= config.max_deployer_prior_launches
    rule3 = (
        df["curve_real_sol_reserves"].isna()
        | (df["curve_real_sol_reserves"] >= config.min_curve_sol_lamports)
    )
    kept = rule1 & rule2 & rule3
    n_in = len(df)
    n_out = int(kept.sum())
    log.info(
        "filter_garbage: kept %d / %d rows "
        "(rule1 entry-liq: %d drop; rule2 deployer-spam: %d drop; "
        "rule3 curve-sol: %d drop)",
        n_out, n_in,
        int((~rule1).sum()), int((~rule2).sum()), int((~rule3).sum()),
    )
    return df[kept]
```

- [ ] **Step 4: Run the tests — verify they pass**

```bash
python3 -m pytest model/tests/test_filter.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: all green, including the new file's 10 tests.

- [ ] **Step 6: Commit**

```bash
git add model/filter.py model/tests/test_filter.py
git commit -m "$(cat <<'EOF'
Task 2: Add model/filter.py -- the point-in-time garbage filter

filter_garbage(df, config) drops clearly-hopeless tokens before training,
applied uniformly so the model and every baseline see the same universe.
Three conjunctive rules: entry-liquidity floor; deployer-spam ceiling;
curve-SOL floor that tolerates NaN (LightGBM handles NaN natively, so an
unknown curve is kept; a known-low one is dropped). Replaces is_unbalance=
True as the imbalance-fight lever -- the filter changes the prior the model
trains on, not just the loss weighting.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Features — switch label to positive_return, add 2 engineered features

**Files:**
- Modify: `model/features.py`
- Modify: `model/tests/test_features.py`

- [ ] **Step 1: Update the test fixture and assertions**

Replace the entire `model/tests/test_features.py` with:

```python
"""Unit tests for model.features -- pure feature engineering."""

import numpy as np
import pandas as pd

from model.features import (
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    LEAKAGE_FORBIDDEN,
    build_features,
)


def raw_frame():
    """Three post-filter tokens spanning the label outcomes.

    M1: gain (exit/entry ratio = 20)        -> positive_return = 1
    M2: abandoned (outcome 0/0 reserves)    -> positive_return = 0
    M3: gain (exit/entry ratio = 4) with NaN curve (allowed post-filter)
    """
    return pd.DataFrame(
        {
            "graduation_time": [1000, 2000, 3000],
            "graduation_slot": [400, 500, 600],
            "survived": [1, 0, 1],  # still loaded; not the label any more
            "outcome_base_reserve": [1.0e14, 0.0, 5.0e14],
            "outcome_quote_reserve": [1.2e11, 0.0, 1.0e11],
            "outcome_checked_at": [4000, 5000, 6000],
            "liq_base_reserve": [1.0e15, 8.5e14, 1.0e15],
            "liq_quote_reserve": [6.0e10, 2.0e10, 5.0e10],
            "lp_burned": [1, 1, 0],
            "curve_real_sol_reserves": [8.5e10, 8.5e10, np.nan],
            "curve_real_token_reserves": [0.0, 0.0, np.nan],
            "curve_token_total_supply": [2.79e14, 2.80e14, np.nan],
            "deployer_prior_launches": [12, 0, 3],
            "deployer_age_secs": [691200, 0, 3600],
        },
        index=pd.Index(["M1", "M2", "M3"], name="mint"),
    )


def test_label_column_name_and_values():
    _X, y = build_features(raw_frame())
    assert LABEL_COLUMN == "positive_return"
    assert y.name == LABEL_COLUMN
    # M1 exit/entry ratio = (1.2e11/1.0e14) / (6.0e10/1.0e15) = 20  -> 1
    # M2 abandoned -> 0
    # M3 ratio = (1.0e11/5.0e14) / (5.0e10/1.0e15) = 4              -> 1
    assert list(y) == [1, 0, 1]


def test_build_features_returns_X_and_y_aligned_by_mint():
    X, y = build_features(raw_frame())
    assert list(X.index) == ["M1", "M2", "M3"]
    assert list(y.index) == ["M1", "M2", "M3"]


def test_X_columns_are_exactly_the_feature_list():
    X, _ = build_features(raw_frame())
    assert list(X.columns) == FEATURE_COLUMNS


def test_no_outcome_or_label_column_leaks_into_X():
    """Spec 4: outcome reserves and the label are NEVER model features."""
    X, _ = build_features(raw_frame())
    for forbidden in LEAKAGE_FORBIDDEN:
        assert forbidden not in X.columns, (
            f"{forbidden} leaked into the feature matrix"
        )


def test_new_engineered_features_are_present_with_correct_values():
    X, _ = build_features(raw_frame())
    # curve-SOL to entry-liquidity ratio (M1).
    assert "curve_sol_to_entry_liq_ratio" in X.columns
    expected_ratio = 8.5e10 / 6.0e10
    assert abs(
        X.loc["M1", "curve_sol_to_entry_liq_ratio"] - expected_ratio
    ) < 1e-9
    # entry log-price (M1).
    assert "entry_log_price" in X.columns
    expected_log_price = np.log(6.0e10 / 1.0e15)
    assert abs(
        X.loc["M1", "entry_log_price"] - expected_log_price
    ) < 1e-9


def test_curve_sol_to_entry_liq_ratio_is_nan_when_curve_is_nan():
    X, _ = build_features(raw_frame())
    # M3 has NaN curve_real_sol_reserves -- the ratio is NaN, never imputed.
    assert np.isnan(X.loc["M3", "curve_sol_to_entry_liq_ratio"])


def test_existing_engineered_features_carry_over():
    X, _ = build_features(raw_frame())
    # log1p of a skewed count -- unchanged from Phase 3.
    assert X.loc["M1", "log_deployer_prior_launches"] == np.log1p(12)
    # entry-liquidity to curve-SOL ratio -- unchanged from Phase 3.
    expected = 6.0e10 / 8.5e10
    assert abs(X.loc["M1", "liq_to_curve_sol_ratio"] - expected) < 1e-9


def test_raw_point_in_time_columns_are_present_in_X():
    X, _ = build_features(raw_frame())
    for col in ("liq_base_reserve", "liq_quote_reserve", "lp_burned",
                "curve_real_sol_reserves", "deployer_prior_launches",
                "deployer_age_secs"):
        assert col in X.columns


def test_deployer_launch_rate_handles_zero_age_without_dividing_by_zero():
    X, _ = build_features(raw_frame())
    # M2 has deployer_age_secs == 0; the rate must be NaN -- never inf, and
    # never a fabricated finite value.
    rate = X.loc["M2", "deployer_launch_rate_per_day"]
    assert np.isnan(rate)


def test_abandoned_token_has_positive_return_zero():
    """outcome 0/0 -> exit_price NaN -> NaN comparison False -> label 0."""
    _X, y = build_features(raw_frame())
    assert y.loc["M2"] == 0
```

- [ ] **Step 2: Run the test file — verify it fails**

```bash
python3 -m pytest model/tests/test_features.py -v
```

Expected: multiple FAILs — `LABEL_COLUMN` is still `"survived"`, the new engineered features don't exist, the label values don't match.

- [ ] **Step 3: Update model/features.py**

Replace `model/features.py` with:

```python
"""Pure feature engineering: the raw DataFrame -> the model feature matrix.

Every column of the returned X is STRICTLY point-in-time -- known at or
before T0+12h. The outcome reserve columns and the positive_return label are
future data and are never placed in X (LEAKAGE_FORBIDDEN lists them; a test
asserts none leak). Missing values flow through as NaN; LightGBM handles
them natively, so nothing is imputed or fabricated.

The label is the binary positive-return target (spec 4.1): 1 iff
log(exit_price / entry_price) > 0, where prices are the pool ratios
quote/base. Abandoned tokens (outcome reserves = 0) and degenerate-ratio
rows resolve to label 0 via NaN-comparison-is-False; rows with NaN entry
liquidity are dropped upstream by model.filter.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd

# The label the model predicts.
LABEL_COLUMN = "positive_return"

# Columns that must NEVER appear in the feature matrix -- they are future
# data (the outcome reserves) or the (still-loaded but no-longer-label)
# survived column or the label itself. The no-leakage test checks this list.
LEAKAGE_FORBIDDEN: List[str] = [
    "positive_return",
    "survived",
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "outcome_checked_at",
]

# Raw point-in-time columns passed straight through as features.
_RAW_FEATURE_COLUMNS: List[str] = [
    "liq_base_reserve",
    "liq_quote_reserve",
    "lp_burned",
    "curve_real_sol_reserves",
    "curve_real_token_reserves",
    "curve_token_total_supply",
    "deployer_prior_launches",
    "deployer_age_secs",
]

# Engineered columns added by _engineer(), in order. Two new for the pivot
# (spec 5): `curve_sol_to_entry_liq_ratio` and `entry_log_price`.
_ENGINEERED_FEATURE_COLUMNS: List[str] = [
    "log_liq_base_reserve",
    "log_liq_quote_reserve",
    "log_curve_real_sol_reserves",
    "log_curve_token_total_supply",
    "log_deployer_prior_launches",
    "log_deployer_age_secs",
    "liq_to_curve_sol_ratio",
    "curve_token_burn_fraction",
    "deployer_launch_rate_per_day",
    "curve_sol_to_entry_liq_ratio",
    "entry_log_price",
]

# The canonical ordered feature-matrix columns -- one source of truth.
FEATURE_COLUMNS: List[str] = _RAW_FEATURE_COLUMNS + _ENGINEERED_FEATURE_COLUMNS

_SECONDS_PER_DAY = 86_400.0


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise divide; a zero or NaN denominator yields NaN, never inf."""
    denom = denominator.where(denominator != 0)
    return numerator / denom


def _engineer(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame of the engineered feature columns, aligned to df.index."""
    out = pd.DataFrame(index=df.index)
    # log1p transforms of skewed counts / reserves -- NaN stays NaN.
    out["log_liq_base_reserve"] = np.log1p(df["liq_base_reserve"])
    out["log_liq_quote_reserve"] = np.log1p(df["liq_quote_reserve"])
    out["log_curve_real_sol_reserves"] = np.log1p(df["curve_real_sol_reserves"])
    out["log_curve_token_total_supply"] = np.log1p(
        df["curve_token_total_supply"]
    )
    out["log_deployer_prior_launches"] = np.log1p(df["deployer_prior_launches"])
    out["log_deployer_age_secs"] = np.log1p(df["deployer_age_secs"])
    # entry liquidity (SOL side) relative to the bonding curve's final SOL.
    out["liq_to_curve_sol_ratio"] = _safe_divide(
        df["liq_quote_reserve"], df["curve_real_sol_reserves"]
    )
    # fraction of the curve's token supply already sold off the curve.
    sold = df["curve_token_total_supply"] - df["curve_real_token_reserves"]
    out["curve_token_burn_fraction"] = _safe_divide(
        sold, df["curve_token_total_supply"]
    )
    # deployer prior launches per day of pump.fun-relative wallet age.
    age_days = df["deployer_age_secs"] / _SECONDS_PER_DAY
    out["deployer_launch_rate_per_day"] = _safe_divide(
        df["deployer_prior_launches"], age_days
    )
    # NEW (spec 5): curve final SOL relative to entry pool quote -- how much
    # of the curve's accumulated SOL ended up in the entry pool. A serious
    # launch retains most of it; a churn-out has less.
    out["curve_sol_to_entry_liq_ratio"] = _safe_divide(
        df["curve_real_sol_reserves"], df["liq_quote_reserve"]
    )
    # NEW (spec 5): entry spot price on a log scale -- log(quote / base).
    out["entry_log_price"] = np.log(
        _safe_divide(df["liq_quote_reserve"], df["liq_base_reserve"])
    )
    return out


def _derive_positive_return_label(df: pd.DataFrame) -> pd.Series:
    """Compute the binary positive_return label from the raw outcome columns.

    `positive_return = 1` iff `log(exit_price / entry_price) > 0`. Abandoned
    tokens (outcome reserves = 0) yield NaN or -inf via _safe_divide; the
    `> 0` comparison returns False on NaN, so they resolve to 0 -- a definite
    loser, matching the backtest's -100% realisation.
    """
    entry_price = _safe_divide(df["liq_quote_reserve"], df["liq_base_reserve"])
    exit_price = _safe_divide(
        df["outcome_quote_reserve"], df["outcome_base_reserve"]
    )
    forward_log_return = np.log(_safe_divide(exit_price, entry_price))
    label = (forward_log_return > 0).astype(int)
    label.name = LABEL_COLUMN
    return label


def build_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """Build the model feature matrix X and the positive_return label y.

    Args:
        df: the raw DataFrame from model.data.load_dataframe -- indexed by
            mint, with the historical_graduations columns.

    Returns:
        (X, y) -- X is the point-in-time feature matrix with columns exactly
        FEATURE_COLUMNS; y is the integer positive_return label. Both are
        indexed by mint, aligned. Missing inputs propagate to X as NaN.
    """
    raw = df[_RAW_FEATURE_COLUMNS].copy()
    engineered = _engineer(df)
    X = pd.concat([raw, engineered], axis=1)
    X = X[FEATURE_COLUMNS]  # enforce the canonical column order
    y = _derive_positive_return_label(df)
    return X, y
```

- [ ] **Step 4: Run the test file — verify it passes**

```bash
python3 -m pytest model/tests/test_features.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Run the full model test suite — note known regressions**

```bash
python3 -m pytest model/ -v
```

Expected: most of `model/` is green. The walk-forward tests and the harness may now fail because they still hardcode `"survived"`-label references and the old heuristic_basket signature — those are addressed in Task 5. Do NOT fix them here.

- [ ] **Step 6: Commit**

```bash
git add model/features.py model/tests/test_features.py
git commit -m "$(cat <<'EOF'
Task 3: Switch label to positive_return and add 2 engineered features

build_features now returns y = 1 iff log(exit_price / entry_price) > 0,
derived from the existing outcome reserves. Abandoned tokens (outcome 0/0)
resolve to 0 via NaN-comparison-is-False, matching the backtest's -100%
realisation. Adds `curve_sol_to_entry_liq_ratio` (curve's accumulated SOL
relative to the entry-pool quote) and `entry_log_price` (log of the
entry-pool quote/base ratio) -- bringing the engineered feature count from
9 to 11 and FEATURE_COLUMNS from 17 to 19.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Baselines — re-specify heuristic_basket with the 3 new rules

**Files:**
- Modify: `model/baselines.py` (replace `heuristic_basket` + docstring)
- Modify: `model/tests/test_baselines.py`

- [ ] **Step 1: Update the test fixture and the heuristic tests**

Replace `model/tests/test_baselines.py` with:

```python
"""Unit tests for model.baselines -- the three baseline basket selectors."""

import numpy as np
import pandas as pd

from model.baselines import buy_everything, heuristic_basket, random_basket


def basket_frame():
    """Six tokens spanning every combination of the 3 new heuristic rules."""
    return pd.DataFrame(
        {
            # rule 1: liq_quote_reserve >= 10 SOL = 1e10 lamports
            "liq_quote_reserve": [
                2.0e10, 6.0e10, 5.0e10, 5.0e10, 1.0e8, 5.0e10,
            ],
            # rule 2: deployer_prior_launches in [1, 30]
            "deployer_prior_launches": [3, 50, 0, 5, 5, np.nan],
            # rule 3: curve_real_sol_reserves >= 70 SOL = 7e10 lamports
            "curve_real_sol_reserves": [
                8.5e10, 8.5e10, 8.5e10, 3.0e10, 8.5e10, 8.5e10,
            ],
        },
        index=pd.Index(["A", "B", "C", "D", "E", "F"], name="mint"),
    )


def test_buy_everything_holds_every_mint():
    df = basket_frame()
    assert buy_everything(df) == {"A", "B", "C", "D", "E", "F"}


def test_random_basket_has_the_requested_size():
    df = basket_frame()
    picked = random_basket(df, size=3, seed=20260519)
    assert len(picked) == 3
    assert picked.issubset(set(df.index))


def test_random_basket_is_deterministic_for_a_fixed_seed():
    df = basket_frame()
    a = random_basket(df, size=3, seed=20260519)
    b = random_basket(df, size=3, seed=20260519)
    assert a == b


def test_random_basket_size_larger_than_frame_returns_all():
    df = basket_frame()
    picked = random_basket(df, size=99, seed=1)
    assert picked == set(df.index)


def test_heuristic_basket_applies_all_three_new_rules():
    df = basket_frame()
    held = heuristic_basket(
        df,
        min_liq_quote=1.0e10,
        deployer_launches_min=1,
        deployer_launches_max=30,
        min_curve_sol=7.0e10,
    )
    # A: liq 20 SOL ok, deployer 3 in [1,30] ok, curve 85 SOL ok        -> held
    # B: deployer 50 > 30                                                -> excluded
    # C: deployer 0  < 1                                                 -> excluded
    # D: curve 30 SOL < 70                                               -> excluded
    # E: liq 0.1 SOL < 10                                                -> excluded
    # F: deployer NaN -> NaN comparison False                            -> excluded
    assert held == {"A"}


def test_heuristic_basket_can_be_empty():
    df = basket_frame()
    # an unreachable liquidity floor -> no token qualifies.
    assert heuristic_basket(
        df,
        min_liq_quote=1.0e30,
        deployer_launches_min=1,
        deployer_launches_max=30,
        min_curve_sol=7.0e10,
    ) == set()


def test_heuristic_basket_excludes_nan_curve_when_floor_is_required():
    """The heuristic is STRICTER than the garbage filter: NaN curve is OUT."""
    df = basket_frame().copy()
    df.loc["A", "curve_real_sol_reserves"] = np.nan
    held = heuristic_basket(
        df,
        min_liq_quote=1.0e10,
        deployer_launches_min=1,
        deployer_launches_max=30,
        min_curve_sol=7.0e10,
    )
    assert "A" not in held
```

- [ ] **Step 2: Run the test file — verify it fails**

```bash
python3 -m pytest model/tests/test_baselines.py -v
```

Expected: FAIL — the current `heuristic_basket` signature takes `(df, max_prior_launches, min_liq_quote)`; the new tests call it with 4 keyword args.

- [ ] **Step 3: Replace baselines.py**

Replace `model/baselines.py` with:

```python
"""The three baseline basket selectors (spec 7).

The model's basket is only worth its complexity if it beats simple rules.
Each selector takes a raw/feature frame indexed by mint and returns the set
of mints that baseline holds:

  1. buy_everything   -- every (filtered) graduation, equal weight.
  2. random_basket    -- a seeded random subset, the model basket's size.
  3. heuristic_basket -- the return-oriented re-specified 3-rule heuristic:
     entry liquidity clears a floor AND the deployer is in an experience
     window (not too few prior launches, not a serial churner) AND the
     bonding curve completed with significant accumulated capital.

The Phase 3 lp_burned rule is dropped: graduated tokens nearly always have
lp_burned == 1 in this dataset, so the rule provided almost no signal. The
curve-SOL floor replaces it.
"""

from __future__ import annotations

from typing import Set

import numpy as np
import pandas as pd


def buy_everything(df: pd.DataFrame) -> Set[str]:
    """Baseline 1: hold every mint in the (filtered) frame."""
    return set(df.index)


def random_basket(df: pd.DataFrame, size: int, seed: int) -> Set[str]:
    """Baseline 2: a seeded random subset of `size` mints.

    `size` is the model basket's size so the comparison is apples-to-apples.
    If `size` is at least the frame's row count, every mint is returned.
    Deterministic for a fixed seed.
    """
    mints = list(df.index)
    if size >= len(mints):
        return set(mints)
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(mints), size=size, replace=False)
    return {mints[i] for i in chosen}


def heuristic_basket(
    df: pd.DataFrame,
    min_liq_quote: float,
    deployer_launches_min: int,
    deployer_launches_max: int,
    min_curve_sol: float,
) -> Set[str]:
    """Baseline 3: the return-oriented re-specified 3-rule heuristic.

    A token is held iff all three rules hold:
      - liq_quote_reserve >= min_liq_quote (entry liquidity clears a floor),
      - deployer_prior_launches in [deployer_launches_min,
        deployer_launches_max] (a non-zero, non-spam launch history),
      - curve_real_sol_reserves >= min_curve_sol (the curve completed with
        significant capital).

    The heuristic is intentionally STRICTER than the garbage filter: NaN in
    any rule column fails that comparison and excludes the token, so a NaN
    curve-SOL value -- which the upstream filter keeps -- is OUT here.
    """
    liq_ok = df["liq_quote_reserve"] >= min_liq_quote
    deployer_ok = (
        (df["deployer_prior_launches"] >= deployer_launches_min)
        & (df["deployer_prior_launches"] <= deployer_launches_max)
    )
    curve_ok = df["curve_real_sol_reserves"] >= min_curve_sol
    held = liq_ok & deployer_ok & curve_ok
    held = held.fillna(False)
    return set(df.index[held])
```

- [ ] **Step 4: Run the test file — verify it passes**

```bash
python3 -m pytest model/tests/test_baselines.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: `model/tests/test_baselines.py` and `model/tests/test_features.py` are green. `model/tests/test_walkforward.py` is still failing (and the harness itself doesn't yet call the new heuristic signature) — Task 5 fixes that.

- [ ] **Step 6: Commit**

```bash
git add model/baselines.py model/tests/test_baselines.py
git commit -m "$(cat <<'EOF'
Task 4: Re-specify heuristic_basket with the 3 return-oriented rules

heuristic_basket now takes (min_liq_quote, deployer_launches_min,
deployer_launches_max, min_curve_sol) and applies the spec's 3 rules:
entry-liquidity floor; deployer in an experience window [low, high]; and
curve final SOL floor. lp_burned is dropped (graduated tokens are nearly
always lp_burned == 1, so the rule carried almost no signal). The
heuristic remains intentionally STRICTER than the upstream garbage filter:
NaN in any rule column excludes the token here.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Walkforward — update _HEURISTIC_* constants and align test_labels

**Files:**
- Modify: `model/walkforward.py` (constants block + `_run_one_fold` + the FoldResult comment)
- Modify: `model/tests/test_walkforward.py` (any survived-name or old-signature assertions)

- [ ] **Step 1: Identify what to update in test_walkforward.py**

Run the walk-forward tests to see what's broken:

```bash
python3 -m pytest model/tests/test_walkforward.py -v
```

Expected: at least one FAIL — Task 3 changed `LABEL_COLUMN` to `"positive_return"` and Task 4 changed `heuristic_basket`'s signature, both of which the harness uses.

Then grep for label-name and old-signature references:

```bash
grep -n -E 'survived|heuristic_basket|max_prior_launches|min_liq_quote|test_labels' model/tests/test_walkforward.py
```

For each hit, note the line number. Likely findings:
- Assertions on `fr.test_labels` against the test fold's `"survived"` column — should now match `positive_return` (the y_test from build_features).
- Direct `heuristic_basket(df, max_prior_launches=..., min_liq_quote=...)` calls — should use the new keyword args.
- Leakage assertions that check `"survived"` is not in `X.columns` — still valid; do not change.

- [ ] **Step 2: Update model/walkforward.py — heuristic constants block**

Open `model/walkforward.py`. Replace lines 37–41 (the existing `_HEURISTIC_*` block) with:

```python
# The 3-rule return-oriented heuristic baseline's thresholds (spec 7). All
# four are pinned conservatively: a token still has to be reasonably liquid
# at entry, deployed by a non-spam wallet with at least one prior launch,
# and come off a curve that completed with significant capital.
_HEURISTIC_MIN_LIQ_QUOTE_LAMPORTS = 10_000_000_000.0     # 10 SOL
_HEURISTIC_DEPLOYER_LAUNCH_MIN = 1
_HEURISTIC_DEPLOYER_LAUNCH_MAX = 30
_HEURISTIC_MIN_CURVE_SOL_LAMPORTS = 70_000_000_000.0     # 70 SOL
```

- [ ] **Step 3: Update model/walkforward.py — `_run_one_fold` body**

Replace the body of `_run_one_fold` (lines 98–147 in the current file) with:

```python
def _run_one_fold(
    df: pd.DataFrame, fold: Fold, config: Config
) -> FoldResult:
    """Train, score, and backtest one fold's model and baselines."""
    train_df = df.loc[fold.train_mints]
    test_df = df.loc[fold.test_mints]

    X_train, y_train = build_features(train_df)
    X_test, y_test = build_features(test_df)

    model = train_survival_model(
        X_train, y_train,
        calibration_fraction=config.calibration_fraction,
        random_seed=config.random_seed,
    )
    scores = model.score(X_test)

    # the model basket: test tokens whose calibrated score clears the gate.
    model_basket = set(scores.index[scores >= config.entry_threshold])

    def _bt(basket):
        return run_backtest(
            test_df, basket=basket, slot_count=config.slot_count,
            initial_bankroll=config.initial_bankroll,
            dex_fee_rate=config.dex_fee_rate,
        )

    model_result = _bt(model_basket)
    baseline_results = {
        "buy_everything": _bt(buy_everything(test_df)),
        "random_basket": _bt(
            random_basket(test_df, size=len(model_basket),
                          seed=config.random_seed)
        ),
        "heuristic_basket": _bt(
            heuristic_basket(
                test_df,
                min_liq_quote=_HEURISTIC_MIN_LIQ_QUOTE_LAMPORTS,
                deployer_launches_min=_HEURISTIC_DEPLOYER_LAUNCH_MIN,
                deployer_launches_max=_HEURISTIC_DEPLOYER_LAUNCH_MAX,
                min_curve_sol=_HEURISTIC_MIN_CURVE_SOL_LAMPORTS,
            )
        ),
    }
    return FoldResult(
        fold=fold,
        model_result=model_result,
        baseline_results=baseline_results,
        test_scores=scores,
        test_labels=y_test,   # spec 4.1: positive_return label per test mint
        model_basket_size=len(model_basket),
    )
```

- [ ] **Step 4: Update model/walkforward.py — `FoldResult.test_labels` comment**

Change line 62 (the comment after `test_labels: pd.Series`) from:

```python
    test_labels: pd.Series          # the survived label per test token
```

to:

```python
    test_labels: pd.Series          # positive_return label per test token (spec 4.1)
```

- [ ] **Step 5: Update model/tests/test_walkforward.py**

For each finding from Step 1:
- Replace direct `heuristic_basket(df, max_prior_launches=..., min_liq_quote=...)` calls with the new signature: `heuristic_basket(df, min_liq_quote=..., deployer_launches_min=..., deployer_launches_max=..., min_curve_sol=...)`. Use placeholder threshold values that fit the test's intent (e.g., `min_liq_quote=1.0e10, deployer_launches_min=1, deployer_launches_max=30, min_curve_sol=7.0e10`).
- Replace any assertion that compares `fr.test_labels` to the test fold's `survived` column with: `assert fr.test_labels.name == "positive_return"` and (if helpful) `assert fr.test_labels.dtype.kind in {"i", "u"}` for the integer dtype.
- Leave leakage assertions on `"survived"` alone — the LEAKAGE_FORBIDDEN list still includes it.

If the test file's structural intent (fold building, no-leakage, no-skip behaviour) survives without changes beyond the label-name and signature updates, do not invent extra assertions.

- [ ] **Step 6: Run the walk-forward tests — verify they pass**

```bash
python3 -m pytest model/tests/test_walkforward.py -v
```

Expected: all green.

- [ ] **Step 7: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: every test file green.

- [ ] **Step 8: Commit**

```bash
git add model/walkforward.py model/tests/test_walkforward.py
git commit -m "$(cat <<'EOF'
Task 5: Walkforward -- new _HEURISTIC_* constants and positive_return test_labels

The heuristic-baseline constants in the walk-forward harness now mirror
baselines.heuristic_basket's new signature: a 10 SOL entry-liquidity floor,
a [1, 30] deployer-launches window, and a 70 SOL curve-final-SOL floor.
The fold's `test_labels` is now the y_test that build_features returns
(the positive_return derivation), so report.py's calibration table reads
the same label the model is trained against.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Run.py — wire filter_garbage into the pipeline

**Files:**
- Modify: `model/run.py`

This task has no pure unit test (run.py is exercised by the actual backtest, same as Phase 3). Verification is the smoke import + the cross-module test sweep.

- [ ] **Step 1: Replace model/run.py**

```python
"""The price-prediction-pivot backtest orchestrator.

Wires: config -> load historical_graduations into a DataFrame -> apply the
point-in-time garbage filter -> run the expanding-window walk-forward
backtest (the calibrated positive_return model and the three baselines) ->
write the report (markdown + plots) under the report dir.

Usage:
    python3 -m model.run
    python3 -m model.run --slots 30 --entry-threshold 0.6
"""

from __future__ import annotations

import argparse
import logging

from model.config import load_config
from model.data import load_graduations
from model.filter import filter_garbage
from model.report import write_report
from model.walkforward import run_walkforward

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("model.run")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="solana-storm price-prediction-pivot walk-forward backtest"
    )
    parser.add_argument(
        "--slots", type=int, default=None,
        help="number of equal bankroll slots (default: Config.slot_count=20)",
    )
    parser.add_argument(
        "--entry-threshold", type=float, default=None,
        help="min calibrated positive_return score to enter (default: 0.5)",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="path to the SQLite DB (default: ./storm.db)",
    )
    args = parser.parse_args()

    overrides = {}
    if args.slots is not None:
        overrides["slot_count"] = args.slots
    if args.entry_threshold is not None:
        overrides["entry_threshold"] = args.entry_threshold
    if args.db is not None:
        overrides["db_path"] = args.db
    config = load_config(**overrides)

    log.info(
        "loading historical_graduations from %s", config.db_path
    )
    df = load_graduations(config)
    log.info("loaded %d graduations (pre-filter)", len(df))

    df = filter_garbage(df, config)  # spec 4.2: same universe for all pickers
    log.info("post-filter: %d rows", len(df))

    log.info(
        "running walk-forward backtest: slots=%d entry_threshold=%.2f "
        "dex_fee=%.4f",
        config.slot_count, config.entry_threshold, config.dex_fee_rate,
    )
    wf_result = run_walkforward(df, config)
    log.info("walk-forward complete: %d folds ran", len(wf_result.folds))

    report_path = write_report(wf_result, df, config)
    log.info("report written: %s", report_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-import — verify the module loads**

```bash
python3 -c "from model.run import main; print('import ok')"
```

Expected: `import ok`. Any traceback is a stop-and-fix.

- [ ] **Step 3: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: every test still green (run.py has no unit test).

- [ ] **Step 4: Commit**

```bash
git add model/run.py
git commit -m "$(cat <<'EOF'
Task 6: Wire filter_garbage into model/run.py

One filter call between load_graduations and run_walkforward; matching log
lines for the pre- and post-filter row counts. The full backtest now
operates on the filtered universe (model + every baseline -- apples to
apples by construction).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: End-to-end run + report + README

Run the backtest against the real dataset, read the report, and write up the result in `model/README.md`.

**Files:**
- Read: `model/report/report.md` (regenerated by `python3 -m model.run`)
- Conditionally modify: `model/report.py` (wording only — if any stale "survived"/"survival" sentence misleads the reader)
- Modify: `model/README.md` (append a new run-log section)

- [ ] **Step 1: Run the full backtest**

```bash
cd /home/roberto/solana-storm
python3 -m model.run 2>&1 | tee /tmp/storm-pivot-run.log
```

Expected: the run completes; output ends with `report written: model/report/report.md`. The pre-filter / post-filter row counts and the per-fold counts appear in the log.

Note the following figures from the log (for use in Step 5):
- pre-filter rows
- post-filter rows
- number of folds run

- [ ] **Step 2: Read the report**

```bash
cat model/report/report.md
```

Note these figures (for use in Step 5):
- Model basket total return, max drawdown
- Each baseline's total return and max drawdown
- Per-regime model return (mania vs quiet)
- The decision gate's PASS / FAIL verdict and which clause was/wasn't met

- [ ] **Step 3: Check report.py for any stale `survived` wording**

```bash
grep -n -i 'survived\|survival' model/report.py
```

For each hit, decide:
- **Code reference** (operating on the Phase 2 `survived` column or anything else that's still accurate) — leave it.
- **User-facing text** (section heading, table column label, prose caveat) that would mislead a reader of the new report by claiming the model predicts survival — update the wording to `positive_return` / `positive return` / `forward return`.

If any wording edits are needed, make them, then commit them separately:

```bash
git add model/report.py
git commit -m "$(cat <<'EOF'
Task 7a: Update report.py user-facing wording for the new label

Code references to the Phase 2 `survived` column (a derived bool, still
loaded into the frame) are unchanged; only user-facing text in the
rendered report is updated to match the new positive_return label.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If no wording edits are needed, skip the commit.

- [ ] **Step 4: Re-run the backtest if report.py was edited**

If Step 3 produced a commit, re-run:

```bash
python3 -m model.run
cat model/report/report.md
```

Re-record the figures from Step 2 if they shifted.

- [ ] **Step 5: Append the run-log section to model/README.md**

Open `model/README.md`. Append a new section AFTER the Phase 3 run-log section. Replace every `<FILL>` with the exact figure from the report — do not invent or round. Replace the gate verdict with `PASS` or `FAIL` and add the one-sentence reason from the report:

```markdown

## Price-Prediction Pivot run — 2026-05-20 (`price-prediction-pivot`)

The pivot retargets the model from survival to a binary positive-forward-return
label (spec `2026-05-20-price-prediction-pivot-design.md`). The dataset
(4,755 graduations) is unchanged; a point-in-time garbage filter drops
clearly-hopeless tokens before training so the model and every baseline see
the same universe (filter rules in `model/filter.py`).

- Pre-filter rows: <FILL>
- Post-filter rows: <FILL>
- Folds run: <FILL>

Total return (model basket): <FILL>%
Max drawdown (model basket): <FILL>%

Baselines:
- `buy_everything`   total <FILL>%   max_dd <FILL>%
- `random_basket`    total <FILL>%   max_dd <FILL>%
- `heuristic_basket` total <FILL>%   max_dd <FILL>%

Per-regime model return:
- mania (Feb / Mar / Apr): <FILL>%
- quiet (Nov / Dec / Jan / May): <FILL>%

**Decision gate:** `<PASS or FAIL>`. <One sentence explaining which gate
clause was or wasn't met — e.g., "Did not beat heuristic_basket in the mania
regime" or "Max drawdown <X>% exceeded the 40% ceiling".>
```

- [ ] **Step 6: Final test pass**

```bash
python3 -m pytest model/ -v
```

Expected: every test still green.

- [ ] **Step 7: Commit**

```bash
git add model/README.md
git commit -m "$(cat <<'EOF'
Task 7: Run the pivot end-to-end and log the result in model/README.md

Captures the actual numbers from the regenerated report against the same
pre-committed decision gate: total return and max drawdown for the model
basket and each baseline, the per-regime model return, and the gate's
PASS/FAIL verdict.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## After all tasks complete

Per the subagent-driven-development skill: dispatch a final code reviewer subagent over the whole branch's diff (spec commit `4688977` → HEAD). The final review verifies:

- Spec coverage end-to-end (each §3 in-scope item maps to one or more commits).
- Test discipline (every new behaviour was test-driven; no test-after).
- No leakage regressions (LEAKAGE_FORBIDDEN still complete; no outcome-or-label column appears in `X`).
- The new run-log in README is consistent with `model/report/report.md`.

Then invoke `superpowers:finishing-a-development-branch` to choose merge / PR / keep / discard.

---

## Self-review notes

1. **Spec coverage.** Walked §3 of the spec end-to-end against the tasks: new label (Task 3), garbage filter (Task 2), entry threshold change (Task 1), two new engineered features (Task 3), heuristic re-spec (Task 4). The orchestration glue (Task 5 walkforward + Task 6 run.py) and the end-to-end run / README update (Task 7) close the loop. No gaps.

2. **Placeholder scan.** Every code step contains the exact code or the exact command. The `<FILL>` placeholders in Task 7 Step 5 are deliberate — they are filled with the actual report figures at execution time and the task explicitly instructs the implementer to do so. These are not the skill's forbidden static placeholders (TBD / TODO / "similar to Task N").

3. **Type consistency.** `LABEL_COLUMN` is `"positive_return"` in both Task 3 (features.py) and Task 5 (walkforward.py comment + test_labels semantics). `heuristic_basket`'s new signature `(df, min_liq_quote, deployer_launches_min, deployer_launches_max, min_curve_sol)` is identical in baselines.py (Task 4), in test_baselines.py (Task 4), and in the walk-forward harness call (Task 5). The new Config fields `min_entry_liq_lamports`, `max_deployer_prior_launches`, `min_curve_sol_lamports` are consistent across Task 1 (definition) and Task 2 (consumption in filter). The walkforward-local `_HEURISTIC_*` constants live in a separate namespace and use different default values from the Config filter fields (heuristic floor 10 SOL / 70 SOL vs filter floor 1 SOL / 10 SOL — strictly stricter, by design). The `_ENGINEERED_FEATURE_COLUMNS` list grows to 11 items (Task 3), so `FEATURE_COLUMNS` length goes from 17 to 19 — no test pins the length explicitly (the tests assert `list(X.columns) == FEATURE_COLUMNS`, which auto-tracks).
