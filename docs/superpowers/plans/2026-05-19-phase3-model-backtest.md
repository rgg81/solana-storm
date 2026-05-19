# Phase 3 Survival Model & Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Python `model/` package that (1) trains a calibrated LightGBM **survival model** on the Phase 2 `historical_graduations` dataset, (2) runs a **walk-forward, portfolio-evolution backtest** with honest DEX-fee + constant-product-slippage costs, (3) compares the model's basket against three baselines split by market regime, and (4) emits a **markdown + plots report** — the input to the project's pre-committed decision gate.

**Architecture:** A standalone Python package in a new top-level `model/` directory, decoupled from the Rust workspace exactly as `bootstrap/` is — no crate, no `Cargo.toml`, no `storm-store` migration. The package only **reads** the `historical_graduations` table from the project SQLite file `./storm.db` (written by the Phase 2 ETL); it writes nothing back to the database. It is split into small, single-responsibility modules: `data` (load the table into a DataFrame), `features` (pure feature engineering — raw columns → a model feature matrix), `costs` (the pure fill model — DEX fee + constant-product slippage), `baselines` (the three pure baseline basket selectors), `regime` (pure market-regime labelling), `survival` (train the LightGBM classifier + probability calibration; calibrated scoring), `backtest` (the portfolio-evolution simulator), `walkforward` (the expanding-window train/test harness), `report` (metrics + markdown + matplotlib plots), and `run.py` (the CLI orchestrator that runs the whole walk-forward backtest and writes the report). The pure modules are TDD-unit-tested with synthetic fixtures; `survival`, `walkforward`, and `report` are exercised by small deterministic tests and a final real backtest run. The model features are **strictly point-in-time** (everything known at or before T0+12h); the outcome reserve columns are **never features** — used only to compute backtest returns — and the plan enforces this no-leakage boundary at every layer.

**Tech Stack:** Python 3.11; third-party libraries `pandas`, `numpy`, `lightgbm`, `scikit-learn`, `matplotlib`, and `pytest` (the test runner) — listed in `model/requirements.txt`. The project SQLite file `./storm.db` is read via the stdlib `sqlite3` module. No network access; no Rust.

---

## Context

This is **Phase 3** of `solana-storm`. The project's critical path is **Phase 2 (assemble a historical dataset) → Phase 3 (model + backtest)**. Phase 1 (`storm-collector`, the live daemon) is built, merged, and **parked** — it is not run or modified by this plan.

The source-of-truth document this plan implements is:

- `docs/superpowers/specs/2026-05-19-phase3-model-backtest-design.md` — the approved Phase 3 design spec. §2 fixes the goal and the **decision gate**; §4 defines the data and the point-in-time feature set; §5 the survival model; §6 the three baselines; §7 the portfolio-evolution simulator and its honest cost model; §8 walk-forward and regime validation; §9 the `model/` package architecture; §10 error handling and the no-leakage testing discipline; §11 the honest caveats; §12 the open decisions this plan resolves.

Two supporting documents inform it but are **not** modified:

- `docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md` — the parent strategy spec. Its §8 states the decision gate the Phase 3 report feeds.
- `docs/superpowers/plans/2026-05-19-historical-bootstrap.md` — the Phase 2 plan; its `bootstrap/` package is the style template this `model/` package mirrors (frozen-dataclass config, small pure-function modules, pytest tests, a package README).

The Phase 2 ETL has already run: `./storm.db` contains a `historical_graduations` table of **4,755 rows** — month-stratified PumpSwap-era graduated tokens, each with a settled `survived` label. The verified per-month counts are: 2025-11 ≈ 715, 2025-12 ≈ 715, 2026-01 ≈ 714, 2026-02 ≈ 714, 2026-03 ≈ 714, 2026-04 ≈ 714, 2026-05 ≈ 469 — **seven distinct calendar months**. The verified `survived` split is 3,245 survived / 1,510 rugged (non-degenerate). These counts let this plan fix concrete fold boundaries and thresholds rather than leaving them open.

### The `historical_graduations` columns this plan reads

`model/data.py` reads the table whose DDL is `bootstrap/load.py`'s `CREATE_TABLE_SQL`. The exact column names (verified against `bootstrap/load.py`'s `_COLUMNS` and a live row of `./storm.db`) are, grouped by Phase 3 role:

- **Identity / facts** — `mint` (TEXT, primary key), `pool_address`, `bonding_curve_address`, `lp_mint`, `migrator_wallet` (TEXT), `graduation_time` (INTEGER, Unix seconds — T0), `graduation_slot` (INTEGER).
- **Outcome label** — `survived` (INTEGER 0/1) — the model's training target.
- **Outcome reserves (NEVER model features — backtest-only)** — `outcome_base_reserve`, `outcome_quote_reserve` (TEXT, u64 strings; `'0'` for an abandoned token), `outcome_checked_at` (INTEGER, Unix seconds — the ~T0+14d horizon time).
- **Liquidity at ~T0+12h (point-in-time features)** — `liq_base_reserve`, `liq_quote_reserve` (TEXT, u64 strings; NULL for ~2 abandoned tokens), `lp_burned` (INTEGER 0/1).
- **Bonding-curve final state (point-in-time features, ~92% populated)** — `curve_real_sol_reserves`, `curve_real_token_reserves`, `curve_token_total_supply` (TEXT, u64 strings; NULL for ~384 rows where Dune had no curve data).
- **Deployer signal (point-in-time features, 100% populated)** — `deployer_wallet` (TEXT), `deployer_prior_launches` (INTEGER — count of the wallet's prior pump.fun launches), `deployer_age_secs` (INTEGER — the wallet's pump.fun-relative age at graduation).
- **Dropped columns — NOT features.** `mint_authority_present` (INTEGER, ~82% NULL and no signal among the populated rows), `freeze_authority_present` (INTEGER, a cohort constant — zero variance); `pool_supply_fraction`, `creator_bag_fraction`, `visible_holder_count`, `top10_concentration`, `top20_concentration` (all 100% NULL — Dune never supplied them). The spec §4 drops every one of these.

### Verified data facts that pin this plan's parameters

A direct inspection of `./storm.db` (run while writing this plan) established:

- Reserve columns are TEXT u64 strings — e.g. `liq_base_reserve = '850938146206890'`. They must be parsed to numbers (`int`, then float for arithmetic) in `data.py`.
- Exactly **2 rows** have NULL `liq_base_reserve` / `liq_quote_reserve` — these are the ~2 tokens the spec §10 says to **exclude from the backtest** (no derivable entry price); they remain in the model's training matrix as NaN-bearing rows.
- **384 rows** (~8%) have NULL `curve_*` columns — they reach the model as NaN, handled natively by LightGBM. They are *not* excluded from the backtest (curve columns are not used to price entries/exits).
- **1,427 rows** have `outcome_quote_reserve = '0'` (an abandoned token) — these realise −100% in the backtest.
- `deployer_prior_launches` and `deployer_age_secs` have **zero NULLs** — the strongest signal is fully present.
- `mint_authority_present` has 2 distinct non-NULL values but is 82% NULL; `freeze_authority_present` has a single value (constant). Both are dropped per the spec.

### Point-in-time discipline and the no-leakage boundary

The spec's central rule: a model feature uses **only** data known at or before **T0+12h**. The point-in-time columns are the deployer group, the liquidity group, and the bonding-curve group, plus engineered transforms of them. The outcome reserve columns (`outcome_base_reserve`, `outcome_quote_reserve`, `outcome_checked_at`) and the `survived` label are **future data**; they are read by `data.py` but `features.py` never places them in the feature matrix, the backtest uses the outcome reserves only to compute realised returns, and the walk-forward harness trains only on tokens whose `graduation_time` strictly precedes the test fold. Three explicit tests enforce this boundary: `test_features.py` asserts no outcome/label column is a feature column; `test_backtest.py` asserts the simulator reads outcome reserves only at exit; `test_walkforward.py` asserts no test token predates its training cutoff.

### The decision gate (from spec §2)

The Phase 3 report states — for a human to evaluate — a pre-committed gate: reviving the parked live component is greenlit only if the **model basket beats all three baselines, out-of-sample, after costs, across ≥2 distinct market regimes, with a maximum drawdown ≤ 40%**. The 40% tolerance is fixed by the spec before any result is seen. A failing gate ("no edge — do not deploy") is an acceptable, planned outcome; `report.py` states the verdict honestly either way. `run.py` and `report.py` only *state* the gate inputs — the gate is **read by a human**, never auto-decided in code.

### Open decisions resolved by this plan (spec §12)

The spec §12 lists decisions to resolve during implementation. This plan fixes them concretely so an executor has no ambiguity:

- **Slot count N** — default **20** (in the spec's ~15–30 range), a `Config` field `slot_count`.
- **Entry score threshold** — default **0.55** calibrated survival probability, a `Config` field `entry_threshold`.
- **Fold boundaries** — expanding-window monthly folds keyed on `graduation_time`'s calendar month. With 7 months, the folds are: train ⟨Nov–Dec⟩ → test Jan; train ⟨Nov–Jan⟩ → test Feb; train ⟨Nov–Feb⟩ → test Mar; train ⟨Nov–Mar⟩ → test Apr; train ⟨Nov–Apr⟩ → test May. That is **5 folds**, each with ≥2 training months; the spec's "~4 folds" is satisfied (`walkforward.py` builds folds generically from whatever months are present, so the count adapts).
- **Calibration split** — within each training fold, the **last 20%** of training tokens by `graduation_time` are held out as the calibration slice (kept time-ordered so calibration itself does not leak).
- **Engineered features** — the concrete list is fixed in Task 4 (`features.py`): log transforms of skewed counts/reserves and three ratios. No leakage-safe deployer track-record aggregate is added — `deployer_prior_launches` already *is* the deployer track record, and adding a re-derived aggregate is out-of-scope YAGNI.
- **Regime-labelling method** — a month is labelled `mania` or `quiet` by comparing that month's **true full-population graduation count** to the median monthly count. The true counts come from the full settled-graduation population (56,850 PumpSwap-era graduations) and are embedded as the fixed `TRUE_MONTHLY_GRADUATIONS` constant in `regime.py` (Task 7). The `historical_graduations` table's own per-month row counts are **not** used: it is a month-stratified sample (`bootstrap/sample.py` draws ~equal tokens per month), so its counts are flat by construction and carry no regime signal. By the true rate, Feb/Mar/Apr 2026 are `mania` and Nov/Dec 2025, Jan/May 2026 are `quiet`.
- **DEX fee + slippage parameters** — a per-leg DEX swap fee of **0.25%** (`Config.dex_fee_rate = 0.0025`, the PumpSwap AMM fee) and a pure constant-product (x·y=k) slippage model with no extra free parameter (Task 5).

### What this plan does NOT do

- **No Rust changes.** No new crate, no edit to any `crates/*` or `bins/*` file, no `Cargo.toml` change.
- **No `storm-store` migration.** `model/` reads `./storm.db` via stdlib `sqlite3`; it is not part of any `sqlx` migration.
- **No writes to `historical_graduations`.** Phase 3 has no live scoring; the table is read-only here.
- **No live data, no Dune.** The dataset is the already-assembled Phase 2 table.
- **No trailing stops / stop-losses / re-score exits, no dataset extension, no cohort-style backtest.** The spec §3 puts all of these out of scope — the dataset lacks the intra-hold price path they need, and only the portfolio-evolution simulator yields the equity curve and max drawdown the gate is stated in.

## Notes for the executor

- All commands below are run from the repo root `/home/roberto/solana-storm` unless an absolute path is given. The `model/` directory and everything in it is new.
- Python is at `/home/roberto/miniconda3/bin/python3` (Python 3.11.15). `pytest` is at `/home/roberto/miniconda3/bin/pytest` (pytest 9.x). Commands below use bare `python3` / `pytest`; if not on `PATH`, prefix with `/home/roberto/miniconda3/bin/`.
- **Task 1 installs the dependencies** (`pip install -r model/requirements.txt`). Every later task assumes `pandas`, `numpy`, `lightgbm`, `scikit-learn`, and `matplotlib` are importable.
- The test suite runs entirely offline — no network, no Dune. Tests build synthetic DataFrames or read a tiny temporary SQLite database via `tmp_path`; they never depend on the real `./storm.db` except the final Task 12 backtest run, which is a manual CLI invocation, not a `pytest` test.
- Run the whole suite with `python3 -m pytest model/tests -q` from the repo root.
- This plan has **no Rust tasks** and runs none of the `cargo` CI gates. The repo CI covers only the Rust workspace; `model/` is independent. Each task still ends with a commit.
- Commit only at the end of each task, with the message shown. End every commit message with the `Co-Authored-By` line shown. Do **not** create a PR or push unless the user later asks.
- TDD discipline: for every code task, write the failing test first, run it and SEE it fail, then write the minimal implementation, run it and SEE it pass. Never write implementation before its failing test.
- The pure modules (`config`, `data`, `features`, `costs`, `baselines`, `regime`, `backtest`, `walkforward`) get real unit tests with synthetic inputs. `survival` is tested on a small synthetic DataFrame with a learnable signal. `report` is tested for the metrics functions and that it writes its files. `run.py` (the orchestrator) is exercised by the Task 12 real backtest run, not by `pytest`.
- Determinism: every place randomness appears (LightGBM training, the random baseline, any train/test shuffle) is seeded from `Config.random_seed`; tests assert reproducibility.

## File structure

| Path | Change | Responsibility |
|---|---|---|
| `model/README.md` | Create | What the package is, prerequisites, how to install deps, how to run the tests and the backtest, where the report lands |
| `model/requirements.txt` | Create | `pandas`, `numpy`, `lightgbm`, `scikit-learn`, `matplotlib`, `pytest` |
| `model/__init__.py` | Create | Marks `model` a package |
| `model/config.py` | Create | A frozen `Config` dataclass + `load_config()` — DB path, slot count, entry threshold, DEX fee, calibration fraction, random seed, report dir |
| `model/data.py` | Create | Load `historical_graduations` from `./storm.db` into a typed pandas DataFrame; parse u64-string reserves to numbers |
| `model/features.py` | Create | Pure feature engineering — raw columns → the model feature matrix `X` and the label `y`; the canonical feature-name list |
| `model/costs.py` | Create | The pure fill model — a DEX swap fee per leg + constant-product (x·y=k) slippage against pool depth; `entry_fill` / `exit_fill` |
| `model/baselines.py` | Create | The three pure baseline basket selectors — buy-everything, seeded random basket, re-specified 3-rule heuristic |
| `model/regime.py` | Create | Pure market-regime labelling — each calendar month tagged `mania` or `quiet` from the true full-population graduation rate |
| `model/survival.py` | Create | Train the LightGBM classifier; isotonic probability calibration on a held-out time-ordered slice; calibrated scoring |
| `model/backtest.py` | Create | The portfolio-evolution simulator — one bankroll, N slots, chronological entries/exits, honest costs, equity curve |
| `model/walkforward.py` | Create | The expanding-window walk-forward harness — builds monthly folds, trains/scores/backtests each, with a no-leakage guard |
| `model/report.py` | Create | Metrics (total return, max drawdown, outcome distribution, calibration) + a markdown report + matplotlib plots |
| `model/run.py` | Create | The CLI entry point — runs the whole walk-forward backtest for the model and all baselines, writes the report |
| `model/tests/__init__.py` | Create | Marks the test package |
| `model/tests/test_scaffold.py` | Create | Smoke test: the package imports, `requirements.txt` lists the libs |
| `model/tests/test_config.py` | Create | Unit tests for `config.py` |
| `model/tests/test_data.py` | Create | Unit tests for `data.py` against a temp SQLite DB |
| `model/tests/test_features.py` | Create | Unit tests for `features.py`, including the no-leakage assertion |
| `model/tests/test_costs.py` | Create | Unit tests for `costs.py` against hand-computed fills |
| `model/tests/test_baselines.py` | Create | Unit tests for `baselines.py` |
| `model/tests/test_regime.py` | Create | Unit tests for `regime.py` |
| `model/tests/test_survival.py` | Create | Unit tests for `survival.py` on a synthetic learnable dataset |
| `model/tests/test_backtest.py` | Create | Unit tests for `backtest.py` on small fixtures, including the no-leakage assertion |
| `model/tests/test_walkforward.py` | Create | Unit tests for `walkforward.py`, including the explicit no-leakage test |
| `model/tests/test_report.py` | Create | Unit tests for `report.py`'s metrics and that it writes its artifacts |

`model/report/` is created at runtime by `report.py` (the markdown + PNG plots); it is a local artifact. The repo `.gitignore` already ignores `__pycache__/`, `*.pyc`, and `storm.db`; this plan adds `model/report/` to it in Task 1. No `model/` source file is gitignored.

---

### Task 1: Scaffold the `model/` package

**Files:**
- Create: `model/__init__.py`, `model/tests/__init__.py`, `model/requirements.txt`, `model/README.md`, `model/tests/test_scaffold.py`
- Modify: `.gitignore`

This task creates the directory skeleton, the dependency file, the README, and the `.gitignore` edit, with one trivial test proving the package is importable. It also **installs the dependencies** so every later task can import `pandas`, `numpy`, `lightgbm`, `scikit-learn`, and `matplotlib`. No model logic yet.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_scaffold.py`:

```python
"""Smoke test: the model package and its test package are importable."""


def test_model_package_imports():
    import model

    assert model is not None


def test_requirements_file_lists_the_core_libs():
    from pathlib import Path

    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    assert req.is_file(), "model/requirements.txt must exist"
    text = req.read_text()
    for lib in ("pandas", "numpy", "lightgbm", "scikit-learn", "matplotlib", "pytest"):
        assert lib in text, f"requirements.txt must list {lib}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_scaffold.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model'` (the package files do not exist yet).

- [ ] **Step 3: Create the package files**

Create `model/__init__.py` (content is exactly one docstring line):

```python
"""solana-storm Phase 3: the survival model and the honest backtest."""
```

Create `model/tests/__init__.py`:

```python
"""Tests for the Phase 3 model package."""
```

Create `model/requirements.txt`:

```
# Phase 3 model + backtest dependencies. Install with:
#   python3 -m pip install -r model/requirements.txt
pandas>=2.0
numpy>=1.24
lightgbm>=4.0
scikit-learn>=1.3
matplotlib>=3.7
# Test runner.
pytest>=8.0
```

Create `model/README.md`:

```markdown
# model — Phase 3 survival model & honest backtest

A standalone Python package that trains an ML **survival model** on the Phase 2
`historical_graduations` dataset, runs a **walk-forward, portfolio-evolution
backtest** with honest DEX-fee and constant-product-slippage costs, compares the
model's basket against three baselines split by market regime, and emits a
report — the input to the project's pre-committed decision gate.

This package is **decoupled from the Rust workspace**: no crate, no `Cargo.toml`,
no `storm-store` migration. It only **reads** the `historical_graduations` table
from `./storm.db` (assembled by the Phase 2 `bootstrap/` ETL) and writes nothing
back.

## Prerequisites

- Python 3.11+.
- A populated `./storm.db` with a `historical_graduations` table — the Phase 2
  ETL deliverable.

## Install

    python3 -m pip install -r model/requirements.txt

## Run the tests

    python3 -m pytest model/tests -q

The test suite is fully offline — it builds synthetic data or uses a temporary
SQLite database; it does not depend on the real `./storm.db`.

## Run the backtest

    python3 -m model.run

This runs the whole walk-forward backtest for the survival model and all three
baselines, then writes the report.

## Output

A report under `model/report/`: a markdown summary (`report.md`) plus matplotlib
plots — the equity curve, the probability calibration curve, and the per-position
outcome distribution. The report states the decision-gate inputs for a human to
evaluate; it never auto-decides the gate.
```

Append to `.gitignore` (the file currently ends with the `storm.db` line under the "Python bytecode and the local ETL database" block):

```
# Phase 3 backtest report artifacts
model/report/
```

- [ ] **Step 4: Install the dependencies**

Run: `python3 -m pip install -r model/requirements.txt`
Expected: pip installs (or confirms already-satisfied) `pandas`, `numpy`, `lightgbm`, `scikit-learn`, `matplotlib`, `pytest`. The command exits 0. If `lightgbm` fails to build, install the prebuilt wheel: `python3 -m pip install --only-binary :all: lightgbm`.

- [ ] **Step 5: Verify the imports work**

Run: `python3 -c "import pandas, numpy, lightgbm, sklearn, matplotlib; print('deps OK')"`
Expected: prints `deps OK` — every library imports cleanly.

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_scaffold.py -q`
Expected: PASS — both tests green.

- [ ] **Step 7: Commit**

```bash
git add model/__init__.py model/tests/__init__.py model/requirements.txt model/README.md model/tests/test_scaffold.py .gitignore
git commit -m "Scaffold model/ package for the Phase 3 survival backtest

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `config.py` — the `Config` dataclass and `load_config()`

**Files:**
- Create: `model/config.py`
- Test: `model/tests/test_config.py`

A frozen `Config` dataclass holds every tunable: the SQLite DB path and table name, the survival outcome threshold, the backtest slot count and entry threshold, the DEX fee rate, the calibration-slice fraction, the random seed, and the report output directory. `load_config()` builds a `Config` with all defaults; it takes optional keyword overrides so a test or the CLI can vary a single field.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_config.py`:

```python
"""Unit tests for model.config."""

import pytest

from model.config import Config, load_config


def test_load_config_returns_the_spec_defaults():
    cfg = load_config()
    # storage
    assert cfg.db_path == "./storm.db"
    assert cfg.table_name == "historical_graduations"
    # the survival label rule -- 5 SOL quote reserve, matches the Phase 2 ETL
    assert cfg.survival_min_quote_lamports == 5_000_000_000
    # backtest slots & sizing (spec 7 / 12 -- N in 15-30, default 20)
    assert cfg.slot_count == 20
    assert cfg.entry_threshold == 0.55
    # honest costs (spec 7 / 12 -- 0.25% PumpSwap AMM fee per leg)
    assert cfg.dex_fee_rate == 0.0025
    # calibration slice -- last 20% of each training fold (spec 5 / 12)
    assert cfg.calibration_fraction == 0.20
    # determinism
    assert cfg.random_seed == 20260519
    # report output
    assert cfg.report_dir == "model/report"


def test_load_config_applies_keyword_overrides():
    cfg = load_config(slot_count=30, entry_threshold=0.7)
    assert cfg.slot_count == 30
    assert cfg.entry_threshold == 0.7
    # untouched fields keep their defaults
    assert cfg.dex_fee_rate == 0.0025


def test_config_is_frozen():
    cfg = load_config()
    with pytest.raises(Exception):
        cfg.slot_count = 1  # frozen dataclass -> FrozenInstanceError


def test_initial_bankroll_is_positive():
    cfg = load_config()
    assert cfg.initial_bankroll > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.config'`.

- [ ] **Step 3: Write `model/config.py`**

Create `model/config.py`:

```python
"""Phase 3 configuration: a frozen Config dataclass and load_config()."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Config:
    """Every tunable for the Phase 3 survival model and the backtest.

    All fields have spec-derived defaults; load_config() builds one and
    accepts keyword overrides for a single field.
    """

    # --- Storage (read-only; the Phase 2 ETL owns the table) ---
    db_path: str = "./storm.db"
    table_name: str = "historical_graduations"

    # --- Survival label rule (matches the Phase 2 ETL's threshold) ---
    survival_min_quote_lamports: int = 5_000_000_000  # 5 SOL

    # --- Backtest: slots & sizing (spec 7 / 12) ---
    slot_count: int = 20  # N equal bankroll slots, spec range 15-30
    entry_threshold: float = 0.55  # min calibrated survival score to enter
    initial_bankroll: float = 100.0  # paper SOL; the equity curve is relative

    # --- Honest costs (spec 7 / 12) ---
    dex_fee_rate: float = 0.0025  # 0.25% PumpSwap AMM swap fee, per leg

    # --- Survival model calibration (spec 5 / 12) ---
    calibration_fraction: float = 0.20  # last 20% of a training fold, time-ordered

    # --- Determinism ---
    random_seed: int = 20260519

    # --- Report output ---
    report_dir: str = "model/report"


def load_config(**overrides: Any) -> Config:
    """Build a Config with the spec defaults, applying keyword overrides.

    Example: load_config(slot_count=30) returns a Config identical to the
    default except slot_count is 30.

    Raises:
        TypeError: if an override names a field Config does not have.
    """
    base = Config()
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    unknown = set(overrides) - valid
    if unknown:
        raise TypeError(f"unknown Config field(s): {sorted(unknown)}")
    return replace(base, **overrides)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_config.py -q`
Expected: PASS — all four tests green.

- [ ] **Step 5: Commit**

```bash
git add model/config.py model/tests/test_config.py
git commit -m "Add Phase 3 model config dataclass and load_config()

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `data.py` — load `historical_graduations` into a DataFrame

**Files:**
- Create: `model/data.py`
- Test: `model/tests/test_data.py`

`data.py` reads the `historical_graduations` table from the project SQLite database into a typed pandas DataFrame via the stdlib `sqlite3` module. It selects the exact column names from `bootstrap/load.py`'s `_COLUMNS`. The TEXT u64-string reserve columns (`liq_*`, `outcome_*`, `curve_*`) are parsed to nullable numeric columns — NULL stays NaN, never fabricated. The function returns the DataFrame indexed by `mint`, sorted by `graduation_time` (so every downstream consumer gets a chronological frame, which the walk-forward harness depends on). A small `load_dataframe(conn)` variant takes an open connection so tests inject a temp DB; `load_graduations(config)` is the convenience that opens `config.db_path`.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_data.py`:

```python
"""Unit tests for model.data -- against a temp-file SQLite DB."""

import sqlite3

import numpy as np
import pandas as pd

from model.data import RAW_COLUMNS, load_dataframe

# A trimmed CREATE TABLE that matches the columns model/data.py reads. The
# real table (bootstrap/load.py) has the same column names; the test only
# needs the columns the loader selects.
_CREATE = """
CREATE TABLE historical_graduations (
    mint TEXT PRIMARY KEY,
    pool_address TEXT, bonding_curve_address TEXT, lp_mint TEXT,
    migrator_wallet TEXT,
    graduation_time INTEGER, graduation_slot INTEGER,
    survived INTEGER,
    outcome_base_reserve TEXT, outcome_quote_reserve TEXT,
    outcome_checked_at INTEGER,
    liq_base_reserve TEXT, liq_quote_reserve TEXT, lp_burned INTEGER,
    curve_real_sol_reserves TEXT, curve_real_token_reserves TEXT,
    curve_token_total_supply TEXT,
    deployer_wallet TEXT, deployer_prior_launches INTEGER,
    deployer_age_secs INTEGER
)
"""


def _seed(conn):
    conn.execute(_CREATE)
    # row 1: fully populated; row 2 (out of order in time) populated;
    # row 3: NULL liquidity + NULL curve columns (the abandoned-token shape).
    rows = [
        ("M1", "P1", "BC1", "LP1", "MIG1", 2000, 500, 1,
         "120000000000000", "92000000000", 3000,
         "1073000000000000", "64000000000", 1,
         "85005359507", "0", "279900000000000",
         "DEP1", 12, 691200),
        ("M2", "P2", "BC2", "LP2", "MIG2", 1000, 400, 0,
         "0", "0", 1000,
         "850938146206890", "20732018898", 1,
         "85000000000", "0", "280000000000000",
         "DEP2", 0, 0),
        ("M3", "P3", "BC3", "LP3", "MIG3", 3000, 600, 0,
         "0", "0", 3000,
         None, None, 1,
         None, None, None,
         "DEP3", 3, 3600),
    ]
    conn.executemany(
        "INSERT INTO historical_graduations VALUES ("
        + ", ".join("?" for _ in range(20)) + ")",
        rows,
    )
    conn.commit()


def test_load_dataframe_returns_every_raw_column():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    # `mint` becomes the frame index; every other RAW_COLUMNS name is a column.
    assert df.index.name == "mint"
    for col in RAW_COLUMNS:
        if col == "mint":
            continue
        assert col in df.columns, f"{col} missing from the loaded frame"


def test_frame_is_indexed_by_mint_and_sorted_by_time():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    assert df.index.name == "mint"
    # seeded times are 2000, 1000, 3000 -> sorted ascending the index is M2,M1,M3
    assert list(df.index) == ["M2", "M1", "M3"]
    assert list(df["graduation_time"]) == [1000, 2000, 3000]


def test_u64_string_reserves_are_parsed_to_numbers():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    # the reserve columns become numeric (float), not object strings.
    for col in ("liq_base_reserve", "liq_quote_reserve",
                "outcome_base_reserve", "outcome_quote_reserve",
                "curve_real_sol_reserves", "curve_token_total_supply"):
        assert pd.api.types.is_numeric_dtype(df[col]), f"{col} not numeric"
    # the large u64 value survives the round-trip exactly.
    assert df.loc["M2", "liq_base_reserve"] == 850938146206890.0


def test_null_reserves_become_nan_not_fabricated():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    # M3 had NULL liquidity and NULL curve columns -> NaN, never zero/imputed.
    assert np.isnan(df.loc["M3", "liq_base_reserve"])
    assert np.isnan(df.loc["M3", "curve_real_sol_reserves"])


def test_label_and_counts_keep_integer_semantics():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    assert int(df.loc["M1", "survived"]) == 1
    assert int(df.loc["M2", "survived"]) == 0
    assert int(df.loc["M1", "deployer_prior_launches"]) == 12
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_data.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.data'`.

- [ ] **Step 3: Write `model/data.py`**

Create `model/data.py`:

```python
"""Load the historical_graduations table from ./storm.db into a DataFrame.

The Phase 2 ETL owns this table (see bootstrap/load.py). Phase 3 only reads
it. Large u64 on-chain values are stored as TEXT strings in SQLite (SQLite's
max integer is i64); they are parsed here to nullable numeric columns -- a
SQL NULL becomes NaN and is never fabricated into a number.
"""

from __future__ import annotations

import sqlite3
from typing import List

import pandas as pd

from model.config import Config

# Exactly the columns Phase 3 reads -- the names match bootstrap/load.py's
# _COLUMNS. The dropped columns (mint_authority_present,
# freeze_authority_present, pool_supply_fraction, creator_bag_fraction, the
# holder group) are intentionally NOT selected: the spec drops them.
RAW_COLUMNS: List[str] = [
    # identity / facts
    "mint",
    "pool_address",
    "bonding_curve_address",
    "lp_mint",
    "migrator_wallet",
    "graduation_time",
    "graduation_slot",
    # outcome label
    "survived",
    # outcome reserves (backtest-only -- NEVER model features)
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "outcome_checked_at",
    # liquidity at ~T0+12h (point-in-time features)
    "liq_base_reserve",
    "liq_quote_reserve",
    "lp_burned",
    # bonding-curve final state (point-in-time features)
    "curve_real_sol_reserves",
    "curve_real_token_reserves",
    "curve_token_total_supply",
    # deployer signal (point-in-time features)
    "deployer_wallet",
    "deployer_prior_launches",
    "deployer_age_secs",
]

# The TEXT u64-string columns -- parsed to numeric (float; NaN-able).
_U64_STRING_COLUMNS: List[str] = [
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "liq_base_reserve",
    "liq_quote_reserve",
    "curve_real_sol_reserves",
    "curve_real_token_reserves",
    "curve_token_total_supply",
]


def load_dataframe(conn: sqlite3.Connection) -> pd.DataFrame:
    """Read historical_graduations from an open connection into a DataFrame.

    The frame is indexed by `mint` and sorted ascending by `graduation_time`
    so every downstream consumer (notably the walk-forward harness) gets a
    chronologically ordered frame. TEXT u64-string reserve columns are
    parsed to numeric; a SQL NULL stays NaN.
    """
    select = ", ".join(RAW_COLUMNS)
    df = pd.read_sql_query(
        f"SELECT {select} FROM historical_graduations", conn
    )
    for col in _U64_STRING_COLUMNS:
        # errors='coerce' turns a NULL/None into NaN; never fabricates a value.
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.set_index("mint")
    df = df.sort_values("graduation_time", kind="stable")
    return df


def load_graduations(config: Config) -> pd.DataFrame:
    """Open config.db_path and load historical_graduations into a DataFrame."""
    conn = sqlite3.connect(config.db_path)
    try:
        return load_dataframe(conn)
    finally:
        conn.close()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_data.py -q`
Expected: PASS — all five tests green.

- [ ] **Step 5: Sanity-check against the real `./storm.db`**

Run: `python3 -c "from model.config import load_config; from model.data import load_graduations; df = load_graduations(load_config()); print('rows', len(df)); print('survived split', df['survived'].value_counts().to_dict()); print('liq NaN', int(df['liq_base_reserve'].isna().sum()), 'curve NaN', int(df['curve_real_sol_reserves'].isna().sum()))"`
Expected: prints `rows 4755`, a `survived split` of roughly `{1: 3245, 0: 1510}`, `liq NaN 2`, `curve NaN 384` — matching the verified data facts. This is a sanity check, not a `pytest` test.

- [ ] **Step 6: Commit**

```bash
git add model/data.py model/tests/test_data.py
git commit -m "Add data loader for historical_graduations into a DataFrame

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `features.py` — engineer the model feature matrix

**Files:**
- Create: `model/features.py`
- Test: `model/tests/test_features.py`

`features.py` is the pure feature-engineering layer. `build_features(df)` takes the raw DataFrame from `data.py` and returns `(X, y)`: `X` is the model feature matrix — **strictly point-in-time** columns only (deployer, liquidity, bonding-curve, plus engineered transforms) — and `y` is the `survived` label. Missing values flow through as NaN (LightGBM handles them natively); nothing is imputed. The engineered features (resolving spec §12's open feature list) are: `log1p` transforms of the skewed counts/reserves and three ratios — entry-liquidity-to-curve-SOL, the bonding-curve token-burn fraction, and the deployer launches-per-day rate. The module exposes `FEATURE_COLUMNS` (the canonical ordered list of `X`'s columns) and `LABEL_COLUMN` so every other module references one source of truth. A dedicated test enforces the no-leakage boundary: no outcome/label column appears in `X`.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_features.py`:

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
    """A tiny raw-shaped frame: one survivor, one rug, one NaN-curve row."""
    return pd.DataFrame(
        {
            "graduation_time": [1000, 2000, 3000],
            "graduation_slot": [400, 500, 600],
            "survived": [1, 0, 1],
            "outcome_base_reserve": [1.2e14, 0.0, 5.0e13],
            "outcome_quote_reserve": [9.2e10, 0.0, 6.0e10],
            "outcome_checked_at": [4000, 5000, 6000],
            "liq_base_reserve": [1.07e15, 8.5e14, 9.0e14],
            "liq_quote_reserve": [6.4e10, 2.0e10, np.nan],
            "lp_burned": [1, 1, 0],
            "curve_real_sol_reserves": [8.5e10, 8.5e10, np.nan],
            "curve_real_token_reserves": [0.0, 0.0, np.nan],
            "curve_token_total_supply": [2.79e14, 2.80e14, np.nan],
            "deployer_prior_launches": [12, 0, 3],
            "deployer_age_secs": [691200, 0, 3600],
        },
        index=pd.Index(["M1", "M2", "M3"], name="mint"),
    )


def test_build_features_returns_X_and_y_aligned_by_mint():
    X, y = build_features(raw_frame())
    assert list(X.index) == ["M1", "M2", "M3"]
    assert list(y.index) == ["M1", "M2", "M3"]
    assert list(y) == [1, 0, 1]
    assert y.name == LABEL_COLUMN


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


def test_raw_point_in_time_columns_are_present_in_X():
    X, _ = build_features(raw_frame())
    for col in ("liq_base_reserve", "liq_quote_reserve", "lp_burned",
                "curve_real_sol_reserves", "deployer_prior_launches",
                "deployer_age_secs"):
        assert col in X.columns


def test_engineered_features_are_computed():
    X, _ = build_features(raw_frame())
    # log1p of a skewed count
    assert "log_deployer_prior_launches" in X.columns
    assert X.loc["M1", "log_deployer_prior_launches"] == np.log1p(12)
    # entry-liquidity to curve-SOL ratio
    assert "liq_to_curve_sol_ratio" in X.columns
    expected = 6.4e10 / 8.5e10
    assert abs(X.loc["M1", "liq_to_curve_sol_ratio"] - expected) < 1e-9


def test_missing_values_flow_through_as_nan_not_imputed():
    X, _ = build_features(raw_frame())
    # M3 had NaN liq_quote and NaN curve columns -> still NaN in X.
    assert np.isnan(X.loc["M3", "liq_quote_reserve"])
    assert np.isnan(X.loc["M3", "curve_real_sol_reserves"])
    # an engineered ratio built from a NaN input is itself NaN, not 0.
    assert np.isnan(X.loc["M3", "liq_to_curve_sol_ratio"])


def test_deployer_launch_rate_handles_zero_age_without_dividing_by_zero():
    X, _ = build_features(raw_frame())
    # M2 has deployer_age_secs == 0; the rate must be finite (NaN), not inf.
    rate = X.loc["M2", "deployer_launch_rate_per_day"]
    assert not np.isinf(rate)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_features.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.features'`.

- [ ] **Step 3: Write `model/features.py`**

Create `model/features.py`:

```python
"""Pure feature engineering: the raw DataFrame -> the model feature matrix.

Every column of the returned X is STRICTLY point-in-time -- known at or
before T0+12h. The outcome reserve columns and the survived label are future
data and are never placed in X (LEAKAGE_FORBIDDEN lists them; a test asserts
none leak). Missing values flow through as NaN; LightGBM handles them
natively, so nothing is imputed or fabricated.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd

# The label the survival model predicts.
LABEL_COLUMN = "survived"

# Columns that must NEVER appear in the feature matrix -- they are future
# data (the outcome) or the label itself. The no-leakage test checks this.
LEAKAGE_FORBIDDEN: List[str] = [
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

# Engineered columns added by _engineer(), in order.
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
    return out


def build_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """Build the model feature matrix X and the label series y.

    Args:
        df: the raw DataFrame from model.data.load_dataframe -- indexed by
            mint, with the historical_graduations columns.

    Returns:
        (X, y) -- X is the point-in-time feature matrix with columns exactly
        FEATURE_COLUMNS; y is the integer survived label. Both are indexed
        by mint, aligned. Missing inputs propagate to X as NaN.
    """
    raw = df[_RAW_FEATURE_COLUMNS].copy()
    engineered = _engineer(df)
    X = pd.concat([raw, engineered], axis=1)
    X = X[FEATURE_COLUMNS]  # enforce the canonical column order
    y = df[LABEL_COLUMN].astype(int)
    y.name = LABEL_COLUMN
    return X, y
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_features.py -q`
Expected: PASS — all seven tests green.

- [ ] **Step 5: Commit**

```bash
git add model/features.py model/tests/test_features.py
git commit -m "Add point-in-time feature engineering with a no-leakage guard

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `costs.py` — the honest fill model

**Files:**
- Create: `model/costs.py`
- Test: `model/tests/test_costs.py`

`costs.py` is the pure honest-cost layer the backtest uses. It models a fill as **a DEX swap fee per leg plus constant-product (x·y=k) slippage** of the order slice against the pool's real depth. `entry_fill(sol_in, base_reserve, quote_reserve, fee_rate)` buys tokens with `sol_in` SOL: the fee is taken off the input, then the constant-product formula gives the token amount out against the pool depth. `exit_fill(tokens_in, base_reserve, quote_reserve, fee_rate)` sells `tokens_in` tokens back for SOL, again constant-product with the fee on the input leg. Selling a slice into a dying token's near-empty pool craters the realised SOL — this is the exit-liquidity problem modelled directly. Both are pure functions tested against hand-computed values. The reserve naming follows the dataset: `base_reserve` is the token side, `quote_reserve` is the SOL side.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_costs.py`:

```python
"""Unit tests for model.costs -- the DEX-fee + constant-product fill model."""

import math

import pytest

from model.costs import exit_fill, entry_fill


def test_entry_fill_constant_product_no_fee_matches_hand_computation():
    # Pool: 1000 token (base), 1000 SOL (quote). Buy with 100 SOL, 0% fee.
    # x*y=k: tokens_out = base - k/(quote + sol_in)
    #      = 1000 - (1000*1000)/(1000+100) = 1000 - 909.0909... = 90.9090...
    tokens_out = entry_fill(
        sol_in=100.0, base_reserve=1000.0, quote_reserve=1000.0, fee_rate=0.0
    )
    assert math.isclose(tokens_out, 1000.0 - 1_000_000.0 / 1100.0, rel_tol=1e-12)


def test_entry_fill_fee_is_taken_off_the_input():
    # 0.25% fee: only 99.75 SOL of the 100 reaches the curve.
    no_fee = entry_fill(100.0, 1000.0, 1000.0, fee_rate=0.0)
    with_fee = entry_fill(100.0, 1000.0, 1000.0, fee_rate=0.0025)
    # the fee reduces the tokens received.
    assert with_fee < no_fee
    effective = entry_fill(99.75, 1000.0, 1000.0, fee_rate=0.0)
    assert math.isclose(with_fee, effective, rel_tol=1e-12)


def test_exit_fill_constant_product_no_fee_matches_hand_computation():
    # Pool: 1000 token, 1000 SOL. Sell 100 token back, 0% fee.
    # sol_out = quote - k/(base + tokens_in)
    #         = 1000 - (1000*1000)/(1000+100) = 90.9090...
    sol_out = exit_fill(
        tokens_in=100.0, base_reserve=1000.0, quote_reserve=1000.0,
        fee_rate=0.0,
    )
    assert math.isclose(sol_out, 1000.0 - 1_000_000.0 / 1100.0, rel_tol=1e-12)


def test_exit_into_a_near_empty_pool_craters_the_fill():
    """The exit-liquidity problem: a thin pool returns almost nothing."""
    # Selling 100 token into a pool with only 0.5 SOL of depth.
    sol_out = exit_fill(100.0, base_reserve=1000.0, quote_reserve=0.5,
                        fee_rate=0.0025)
    # cannot get back more than the pool's entire SOL depth.
    assert 0.0 < sol_out < 0.5


def test_exit_fill_can_never_exceed_pool_quote_depth():
    # Even an enormous sell only ever drains up to the quote reserve.
    sol_out = exit_fill(1e18, base_reserve=1000.0, quote_reserve=42.0,
                        fee_rate=0.0)
    assert sol_out < 42.0


def test_zero_size_order_returns_zero():
    assert entry_fill(0.0, 1000.0, 1000.0, 0.0025) == 0.0
    assert exit_fill(0.0, 1000.0, 1000.0, 0.0025) == 0.0


def test_empty_pool_returns_zero_fill():
    # An abandoned token: zero reserves. Buying or selling yields 0.
    assert entry_fill(10.0, base_reserve=0.0, quote_reserve=0.0,
                      fee_rate=0.0025) == 0.0
    assert exit_fill(10.0, base_reserve=0.0, quote_reserve=0.0,
                     fee_rate=0.0025) == 0.0


def test_fee_rate_out_of_range_raises():
    with pytest.raises(ValueError):
        entry_fill(10.0, 1000.0, 1000.0, fee_rate=1.5)
    with pytest.raises(ValueError):
        exit_fill(10.0, 1000.0, 1000.0, fee_rate=-0.1)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_costs.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.costs'`.

- [ ] **Step 3: Write `model/costs.py`**

Create `model/costs.py`:

```python
"""The honest fill model: a DEX swap fee per leg + constant-product slippage.

Every backtest fill is priced by swapping the order slice against the pool's
real depth using the constant-product (x*y=k) invariant, after a swap fee is
taken off the input leg. `base_reserve` is the token side of the pool;
`quote_reserve` is the SOL side -- the same convention as the dataset's
liq_*/outcome_* reserve columns. Selling a slice into a near-empty pool is
priced honestly: the constant-product curve returns far less than the naive
mark, modelling the exit-liquidity problem directly.
"""

from __future__ import annotations


def _check_fee(fee_rate: float) -> None:
    """Reject a fee rate outside [0, 1)."""
    if fee_rate < 0.0 or fee_rate >= 1.0:
        raise ValueError(f"fee_rate must be in [0, 1), got {fee_rate}")


def entry_fill(
    sol_in: float,
    base_reserve: float,
    quote_reserve: float,
    fee_rate: float,
) -> float:
    """Tokens received for buying with `sol_in` SOL against pool depth.

    The fee is taken off the SOL input; the net SOL is swapped on the
    constant-product curve x*y=k. Returns the token amount out (>= 0). A
    zero-size order or an empty pool returns 0.0.
    """
    _check_fee(fee_rate)
    if sol_in <= 0.0 or base_reserve <= 0.0 or quote_reserve <= 0.0:
        return 0.0
    net_sol = sol_in * (1.0 - fee_rate)
    k = base_reserve * quote_reserve
    new_quote = quote_reserve + net_sol
    new_base = k / new_quote
    return base_reserve - new_base


def exit_fill(
    tokens_in: float,
    base_reserve: float,
    quote_reserve: float,
    fee_rate: float,
) -> float:
    """SOL received for selling `tokens_in` tokens against pool depth.

    The fee is taken off the token input; the net tokens are swapped on the
    constant-product curve x*y=k. Returns the SOL amount out (>= 0), which is
    strictly less than the pool's quote depth. A zero-size order or an empty
    pool returns 0.0 -- an abandoned token's empty pool realises nothing.
    """
    _check_fee(fee_rate)
    if tokens_in <= 0.0 or base_reserve <= 0.0 or quote_reserve <= 0.0:
        return 0.0
    net_tokens = tokens_in * (1.0 - fee_rate)
    k = base_reserve * quote_reserve
    new_base = base_reserve + net_tokens
    new_quote = k / new_base
    return quote_reserve - new_quote
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_costs.py -q`
Expected: PASS — all eight tests green.

- [ ] **Step 5: Commit**

```bash
git add model/costs.py model/tests/test_costs.py
git commit -m "Add honest fill model: DEX fee + constant-product slippage

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `baselines.py` — the three baseline basket selectors

**Files:**
- Create: `model/baselines.py`
- Test: `model/tests/test_baselines.py`

`baselines.py` provides the three baseline basket selectors the model is compared against (spec §6). Each takes a feature/raw frame and returns the **set of mints** that baseline would hold. `buy_everything(df)` returns every mint. `random_basket(df, size, seed)` returns a seeded random subset of `size` mints — the model basket's size is passed in so the comparison is apples-to-apples. `heuristic_basket(df, max_prior_launches, min_liq_quote)` is the spec's **re-specified 3-rule heuristic**: a token is held iff `lp_burned == 1` AND `deployer_prior_launches <= max_prior_launches` AND `liq_quote_reserve >= min_liq_quote`. All three are pure and deterministic (the random one for a fixed seed).

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_baselines.py`:

```python
"""Unit tests for model.baselines -- the three baseline basket selectors."""

import numpy as np
import pandas as pd

from model.baselines import buy_everything, heuristic_basket, random_basket


def basket_frame():
    """Six tokens with varied lp_burned / deployer / liquidity values."""
    return pd.DataFrame(
        {
            "lp_burned": [1, 1, 1, 0, 1, 1],
            "deployer_prior_launches": [0, 2, 50, 0, 1, np.nan],
            "liq_quote_reserve": [
                9.0e10, 6.0e10, 9.0e10, 9.0e10, 1.0e8, 9.0e10,
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
    # a different seed gives (very likely) a different basket.
    c = random_basket(df, size=3, seed=999)
    assert isinstance(c, set)


def test_random_basket_size_larger_than_frame_returns_all():
    df = basket_frame()
    picked = random_basket(df, size=99, seed=1)
    assert picked == set(df.index)


def test_heuristic_basket_applies_all_three_rules():
    df = basket_frame()
    # rules: lp_burned == 1 AND prior_launches <= 5 AND liq_quote >= 1e10.
    held = heuristic_basket(df, max_prior_launches=5, min_liq_quote=1.0e10)
    # A: lp=1, launches=0, liq ok            -> held
    # B: lp=1, launches=2, liq ok            -> held
    # C: lp=1, launches=50 (> 5)             -> excluded
    # D: lp=0                                -> excluded
    # E: liq 1e8 (< 1e10)                    -> excluded
    # F: deployer_prior_launches NaN         -> excluded (rule not satisfied)
    assert held == {"A", "B"}


def test_heuristic_basket_can_be_empty():
    df = basket_frame()
    # an unreachable liquidity floor -> no token qualifies.
    assert heuristic_basket(df, max_prior_launches=5,
                            min_liq_quote=1.0e30) == set()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_baselines.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.baselines'`.

- [ ] **Step 3: Write `model/baselines.py`**

Create `model/baselines.py`:

```python
"""The three baseline basket selectors (spec 6).

The model's basket is only worth its complexity if it beats simple rules.
Each selector takes a raw/feature frame indexed by mint and returns the set
of mints that baseline holds:

  1. buy_everything   -- every graduation, equal weight.
  2. random_basket    -- a seeded random subset, the model basket's size.
  3. heuristic_basket -- the spec's re-specified 3-rule heuristic. The
     original strategy rules (LP burned + mint renounced + low holder
     concentration) are not computable on this dataset -- mint authority is
     a cohort constant and holder data was never collected -- so the
     available-feature equivalent is used: lp_burned set AND the deployer is
     not a serial re-launcher AND entry liquidity clears a floor.
"""

from __future__ import annotations

from typing import Set

import numpy as np
import pandas as pd


def buy_everything(df: pd.DataFrame) -> Set[str]:
    """Baseline 1: hold every mint in the frame."""
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
    max_prior_launches: int,
    min_liq_quote: float,
) -> Set[str]:
    """Baseline 3: the re-specified 3-rule heuristic.

    A token is held iff all three rules hold:
      - lp_burned == 1, AND
      - deployer_prior_launches <= max_prior_launches (not a serial
        re-launcher), AND
      - liq_quote_reserve >= min_liq_quote (entry liquidity clears a floor).

    A NaN in any rule column fails that comparison, so the token is excluded.
    """
    lp_ok = df["lp_burned"] == 1
    deployer_ok = df["deployer_prior_launches"] <= max_prior_launches
    liq_ok = df["liq_quote_reserve"] >= min_liq_quote
    held = lp_ok & deployer_ok & liq_ok
    held = held.fillna(False)
    return set(df.index[held])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_baselines.py -q`
Expected: PASS — all six tests green.

- [ ] **Step 5: Commit**

```bash
git add model/baselines.py model/tests/test_baselines.py
git commit -m "Add the three baseline basket selectors

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `regime.py` — market-regime labelling

**Files:**
- Create: `model/regime.py`
- Test: `model/tests/test_regime.py`

`regime.py` labels each calendar month a market regime — `mania` or `quiet` — from the **true pump.fun graduation rate** (spec §8). The rate is the *full settled-graduation population* (the 56,850 PumpSwap-era graduations of Nov 2025 – May 2026), embedded in the module as the fixed `TRUE_MONTHLY_GRADUATIONS` constant. It is **not** derived from the `historical_graduations` table: that table is a month-stratified sample — Phase 2's `bootstrap/sample.py` deliberately draws ~equal tokens per calendar month — so its per-month row counts are flat by construction (≈714–715 each) and carry no regime signal; counting them would yield a meaningless, sampling-artifact label. `month_of(graduation_time)` returns a token's `'YYYY-MM'` month. `label_regimes()` takes no arguments — it takes the median of the true monthly counts and labels a month `mania` if its true count is strictly above the median, else `quiet`, returning a `{month: regime}` dict. On the real window the counts ramp Nov→Mar then decline (4880, 6007, 7677, 10790, 15175, 11646, 675; median 7677), so Feb/Mar/Apr 2026 are `mania` and Nov/Dec 2025, Jan/May 2026 are `quiet` — both regimes are genuinely present. `assign_regime(df)` returns a per-mint `regime` Series so the report can group performance by regime.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_regime.py`:

```python
"""Unit tests for model.regime -- market-regime labelling."""

import pandas as pd

from model.regime import (
    MANIA,
    QUIET,
    TRUE_MONTHLY_GRADUATIONS,
    assign_regime,
    label_regimes,
    month_of,
)


def _ts(year, month, day=15):
    """A Unix timestamp for a given calendar day, UTC."""
    return int(pd.Timestamp(year=year, month=month, day=day,
                            tz="UTC").timestamp())


def test_month_of_returns_year_month_string():
    assert month_of(_ts(2026, 1, 10)) == "2026-01"
    assert month_of(_ts(2025, 11, 30)) == "2025-11"


def test_label_regimes_uses_the_true_population_rate():
    labels = label_regimes()
    # True monthly counts ramp Nov->Mar then decline; median is 7677.
    # The above-median months Feb/Mar/Apr are mania; the rest quiet.
    assert labels["2026-02"] == MANIA
    assert labels["2026-03"] == MANIA
    assert labels["2026-04"] == MANIA
    assert labels["2025-11"] == QUIET
    assert labels["2025-12"] == QUIET
    assert labels["2026-01"] == QUIET
    assert labels["2026-05"] == QUIET


def test_label_regimes_spans_both_regimes():
    # The closed historical window genuinely contains both regimes.
    assert set(label_regimes().values()) == {MANIA, QUIET}


def test_assign_regime_returns_a_per_mint_series():
    df = pd.DataFrame(
        {"graduation_time": [_ts(2026, 3, 9), _ts(2026, 1, 20),
                             _ts(2025, 11, 8)]},
        index=pd.Index(["A", "B", "C"], name="mint"),
    )
    regimes = assign_regime(df)
    assert list(regimes.index) == ["A", "B", "C"]
    assert regimes["A"] == MANIA   # March
    assert regimes["B"] == QUIET   # January
    assert regimes["C"] == QUIET   # November
    assert regimes.name == "regime"


def test_true_counts_cover_the_seven_month_window():
    assert set(TRUE_MONTHLY_GRADUATIONS) == {
        "2025-11", "2025-12", "2026-01", "2026-02",
        "2026-03", "2026-04", "2026-05",
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_regime.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.regime'`.

- [ ] **Step 3: Write `model/regime.py`**

Create `model/regime.py`:

```python
"""Market-regime labelling (spec 8).

Each calendar month is labelled a regime -- `mania` or `quiet` -- from the
TRUE pump.fun graduation rate: a month whose graduation count is strictly
above the median monthly count is `mania`, every other month is `quiet`.

The rate is the *full settled-graduation population* (the 56,850 PumpSwap-era
graduations of Nov 2025 - May 2026), NOT the `historical_graduations` table.
That table is a month-stratified sample -- Phase 2's `sample.py` deliberately
draws ~equal tokens per calendar month -- so its per-month row counts are flat
by construction and carry no regime signal. The true counts below were
computed from the full graduations population and are fixed, immutable facts
for this closed historical window.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Dict

import pandas as pd

MANIA = "mania"
QUIET = "quiet"

# True graduations per calendar month over the full settled-graduation
# population (56,850 PumpSwap-era graduations); immutable facts for this
# closed window. The historical_graduations TABLE is a month-stratified
# sample with flat per-month counts -- it must not be used for the rate.
TRUE_MONTHLY_GRADUATIONS: Dict[str, int] = {
    "2025-11": 4880,
    "2025-12": 6007,
    "2026-01": 7677,
    "2026-02": 10790,
    "2026-03": 15175,
    "2026-04": 11646,
    "2026-05": 675,   # partial month -- the window cutoff falls on 2026-05-02
}


def month_of(graduation_time: int) -> str:
    """The 'YYYY-MM' calendar month of a Unix timestamp (UTC)."""
    dt = datetime.fromtimestamp(int(graduation_time), tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def label_regimes() -> Dict[str, str]:
    """Map each calendar month to MANIA or QUIET by the true graduation rate.

    A month whose true full-population graduation count is strictly above the
    median monthly count is MANIA; every other month is QUIET.
    """
    median = statistics.median(TRUE_MONTHLY_GRADUATIONS.values())
    return {
        month: (MANIA if count > median else QUIET)
        for month, count in TRUE_MONTHLY_GRADUATIONS.items()
    }


def assign_regime(df: pd.DataFrame) -> pd.Series:
    """A per-mint regime Series (MANIA / QUIET), aligned to df.index."""
    labels = label_regimes()
    regimes = df["graduation_time"].apply(month_of).map(labels)
    regimes.name = "regime"
    return regimes
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_regime.py -q`
Expected: PASS — all five tests green.

- [ ] **Step 5: Commit**

```bash
git add model/regime.py model/tests/test_regime.py
git commit -m "Add market-regime labelling from the true graduation rate

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `survival.py` — the calibrated LightGBM survival model

**Files:**
- Create: `model/survival.py`
- Test: `model/tests/test_survival.py`

`survival.py` trains the survival model: a **LightGBM gradient-boosted classifier** plus an explicit **probability-calibration** step (spec §5). `train_survival_model(X, y, calibration_fraction, random_seed)` splits the training data **time-ordered** — the last `calibration_fraction` of rows (by row order, which `data.py` already sorted by `graduation_time`) is the calibration slice — fits LightGBM on the earlier part, then fits an isotonic calibrator (`sklearn.isotonic.IsotonicRegression`) mapping the model's raw probabilities to calibrated ones on the held-out slice. It returns a `SurvivalModel` object bundling the booster and the calibrator. `SurvivalModel.score(X)` returns a calibrated survival probability per row. The training input `X` already excludes every outcome/label column (it is `features.build_features`'s output), so the model cannot see the future.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_survival.py`:

```python
"""Unit tests for model.survival -- the calibrated LightGBM survival model.

A synthetic dataset with a deliberately learnable signal is used: tokens
whose `liq_quote_reserve` is high mostly survive. The tests assert the model
learns the direction, returns calibrated [0,1] scores, and is deterministic.
"""

import numpy as np
import pandas as pd

from model.features import FEATURE_COLUMNS
from model.survival import SurvivalModel, train_survival_model


def synthetic_xy(n=400, seed=0):
    """A learnable dataset: high liq_quote_reserve -> more likely to survive."""
    rng = np.random.default_rng(seed)
    liq_quote = rng.uniform(0.0, 1.0, size=n)
    # survival probability rises with liq_quote; add noise.
    p = 0.15 + 0.7 * liq_quote
    survived = (rng.uniform(0.0, 1.0, size=n) < p).astype(int)
    data = {col: rng.normal(size=n) for col in FEATURE_COLUMNS}
    data["liq_quote_reserve"] = liq_quote
    X = pd.DataFrame(data, columns=FEATURE_COLUMNS)
    X.index = pd.Index([f"M{i}" for i in range(n)], name="mint")
    y = pd.Series(survived, index=X.index, name="survived")
    return X, y


def test_train_returns_a_survival_model():
    X, y = synthetic_xy()
    model = train_survival_model(
        X, y, calibration_fraction=0.2, random_seed=20260519
    )
    assert isinstance(model, SurvivalModel)


def test_scores_are_probabilities_in_the_unit_interval():
    X, y = synthetic_xy()
    model = train_survival_model(X, y, calibration_fraction=0.2,
                                 random_seed=20260519)
    scores = model.score(X)
    assert len(scores) == len(X)
    assert scores.min() >= 0.0 and scores.max() <= 1.0
    assert list(scores.index) == list(X.index)


def test_model_learns_the_signal_direction():
    """High-liquidity tokens should score higher than low-liquidity ones."""
    X, y = synthetic_xy(n=600, seed=1)
    model = train_survival_model(X, y, calibration_fraction=0.2,
                                 random_seed=20260519)
    scores = model.score(X)
    hi = scores[X["liq_quote_reserve"] > 0.8].mean()
    lo = scores[X["liq_quote_reserve"] < 0.2].mean()
    assert hi > lo, "the model failed to learn the planted signal"


def test_training_is_deterministic_for_a_fixed_seed():
    X, y = synthetic_xy()
    m1 = train_survival_model(X, y, calibration_fraction=0.2,
                              random_seed=20260519)
    m2 = train_survival_model(X, y, calibration_fraction=0.2,
                              random_seed=20260519)
    s1 = m1.score(X)
    s2 = m2.score(X)
    np.testing.assert_allclose(s1.values, s2.values)


def test_score_handles_nan_features():
    """LightGBM handles NaN natively -- a NaN-bearing row still scores."""
    X, y = synthetic_xy()
    model = train_survival_model(X, y, calibration_fraction=0.2,
                                 random_seed=20260519)
    X_nan = X.copy()
    X_nan.iloc[0, 0] = np.nan
    scores = model.score(X_nan)
    assert not np.isnan(scores.iloc[0])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_survival.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.survival'`.

- [ ] **Step 3: Write `model/survival.py`**

Create `model/survival.py`:

```python
"""The survival model: a calibrated LightGBM gradient-boosted classifier.

LightGBM suits a small tabular dataset with missing values -- it handles NaN
natively, so features are never imputed. Calibration is an explicit step
(spec 5): an isotonic regressor maps the booster's raw probabilities to
calibrated ones, fitted on a time-ordered held-out slice of the training
data, because the decision gate evaluates probability calibration, not only
ranking. The model's output is a calibrated survival probability per token.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

# LightGBM hyper-parameters -- conservative for a small dataset, to limit
# overfitting. random_state / deterministic are set per call from the seed.
_LGB_PARAMS = {
    "objective": "binary",
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 30,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "verbose": -1,
}


class SurvivalModel:
    """A trained LightGBM booster plus an isotonic probability calibrator."""

    def __init__(
        self,
        booster: lgb.LGBMClassifier,
        calibrator: IsotonicRegression,
    ) -> None:
        self._booster = booster
        self._calibrator = calibrator

    def score(self, X: pd.DataFrame) -> pd.Series:
        """A calibrated survival probability in [0, 1] for each row of X."""
        raw = self._booster.predict_proba(X)[:, 1]
        calibrated = self._calibrator.predict(raw)
        calibrated = np.clip(calibrated, 0.0, 1.0)
        return pd.Series(calibrated, index=X.index, name="survival_score")


def train_survival_model(
    X: pd.DataFrame,
    y: pd.Series,
    calibration_fraction: float,
    random_seed: int,
) -> SurvivalModel:
    """Train the LightGBM classifier and fit an isotonic calibrator.

    The last `calibration_fraction` of rows (X is already time-ordered by the
    data loader) is held out; LightGBM is fitted on the earlier rows and the
    isotonic calibrator on the booster's predictions over the held-out slice.

    Args:
        X: the point-in-time feature matrix (features.build_features output).
        y: the integer survived label, aligned to X.
        calibration_fraction: fraction of training rows held out to calibrate.
        random_seed: seeds LightGBM for reproducibility.

    Returns:
        A SurvivalModel. If the calibration slice would be empty or the
        training slice would be empty (a tiny fold), the calibrator is fitted
        on the training slice itself -- a graceful degradation, still valid.
    """
    n = len(X)
    n_calib = int(round(n * calibration_fraction))
    n_calib = max(0, min(n_calib, n - 1))  # leave at least 1 training row
    split = n - n_calib

    X_fit, y_fit = X.iloc[:split], y.iloc[:split]
    if n_calib > 0:
        X_calib, y_calib = X.iloc[split:], y.iloc[split:]
    else:
        X_calib, y_calib = X_fit, y_fit  # tiny fold: calibrate on the fit set

    booster = lgb.LGBMClassifier(
        random_state=random_seed,
        deterministic=True,
        n_jobs=1,
        **_LGB_PARAMS,
    )
    booster.fit(X_fit, y_fit)

    raw_calib = booster.predict_proba(X_calib)[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(raw_calib, y_calib.astype(float).values)

    return SurvivalModel(booster, calibrator)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_survival.py -q`
Expected: PASS — all five tests green. (Training five small LightGBM models takes a few seconds.)

- [ ] **Step 5: Commit**

```bash
git add model/survival.py model/tests/test_survival.py
git commit -m "Add calibrated LightGBM survival model

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `backtest.py` — the portfolio-evolution simulator

**Files:**
- Create: `model/backtest.py`
- Test: `model/tests/test_backtest.py`

`backtest.py` is the portfolio-evolution simulator (spec §7) — the heart of the honest backtest. One paper bankroll is divided into `slot_count` equal slots. The simulator walks the dataset chronologically: a token becomes eligible at its entry time (`graduation_time + 12h`, the T0+12h point-in-time instant); if it is in the basket and a slot is free, it **enters** — one equal-weight slice of the current bankroll, priced through `costs.entry_fill` against the entry pool depth (`liq_base_reserve`, `liq_quote_reserve`). It is **held to its horizon** (`outcome_checked_at`) and **exits** there, priced through `costs.exit_fill` against the exit pool depth (`outcome_base_reserve`, `outcome_quote_reserve`); an abandoned token (zero outcome reserves) realises −100%. Capital freed at an exit recycles into the next eligible entry. The simulator returns a `BacktestResult` carrying the equity curve over calendar time and the full list of per-position outcomes. A token with NaN entry liquidity is excluded with a logged count (spec §10). The simulator reads the outcome reserves **only at the exit event** — a test asserts this no-leakage property. The identical simulator runs the model basket and all three baselines.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_backtest.py`:

```python
"""Unit tests for model.backtest -- the portfolio-evolution simulator."""

import numpy as np
import pandas as pd

from model.backtest import BacktestResult, run_backtest


def sim_frame():
    """Four tokens: two clear winners, one mild loser, one abandoned rug.

    Entry pools are deep (low slippage); the rug's outcome pool is empty.
    """
    return pd.DataFrame(
        {
            "graduation_time": [0, 0, 100, 100],
            "outcome_checked_at": [1000, 1000, 1100, 1100],
            # entry depth: token side (base), SOL side (quote).
            "liq_base_reserve": [1e15, 1e15, 1e15, 1e15],
            "liq_quote_reserve": [1e12, 1e12, 1e12, 1e12],
            # exit depth: W1/W2 richer (price up), L mild loss, R abandoned.
            "outcome_base_reserve": [5e14, 5e14, 1.2e15, 0.0],
            "outcome_quote_reserve": [3e12, 3e12, 9e11, 0.0],
        },
        index=pd.Index(["W1", "W2", "L", "R"], name="mint"),
    )


def test_run_backtest_returns_a_result_with_an_equity_curve():
    df = sim_frame()
    result = run_backtest(
        df, basket=set(df.index), slot_count=4,
        initial_bankroll=100.0, dex_fee_rate=0.0025, entry_offset_secs=0,
    )
    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) >= 2  # at least a start and an end point
    assert result.equity_curve.iloc[0] == 100.0  # starts at the bankroll


def test_every_basket_token_with_liquidity_produces_a_position():
    df = sim_frame()
    result = run_backtest(df, basket=set(df.index), slot_count=4,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    assert len(result.positions) == 4
    assert {p.mint for p in result.positions} == {"W1", "W2", "L", "R"}


def test_abandoned_token_realises_a_total_loss():
    df = sim_frame()
    result = run_backtest(df, basket={"R"}, slot_count=1,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    rug = result.positions[0]
    assert rug.mint == "R"
    # an empty outcome pool -> exit_fill returns 0 -> -100% return.
    assert rug.return_pct <= -0.999


def test_a_basket_of_winners_grows_the_bankroll():
    df = sim_frame()
    result = run_backtest(df, basket={"W1", "W2"}, slot_count=2,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    assert result.final_equity > 100.0
    assert result.total_return > 0.0


def test_only_basket_tokens_are_traded():
    df = sim_frame()
    result = run_backtest(df, basket={"W1"}, slot_count=4,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    assert {p.mint for p in result.positions} == {"W1"}


def test_slot_cap_limits_concurrent_positions():
    """With 1 slot, the second simultaneous token cannot enter."""
    df = sim_frame()
    # W1 and W2 are both eligible at t=0; only one slot is free.
    result = run_backtest(df, basket={"W1", "W2"}, slot_count=1,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    # W1/W2 exit at t=1000; only one of them ever held the single slot.
    assert len(result.positions) == 1


def test_capital_recycles_from_an_exit_into_a_later_entry():
    """One slot: the t=0 token exits at t=1000, freeing the slot for the
    t=100 token, which enters at its horizon-free moment."""
    df = sim_frame()
    result = run_backtest(df, basket={"W1", "L"}, slot_count=1,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    # W1 occupies the slot [0,1000]; L is eligible at 100 but the slot is
    # busy -- L can still enter after W1 exits because L's horizon is 1100.
    held = {p.mint for p in result.positions}
    assert "W1" in held


def test_token_with_nan_entry_liquidity_is_excluded_and_counted():
    df = sim_frame()
    df.loc["W2", "liq_quote_reserve"] = np.nan  # no derivable entry price
    result = run_backtest(df, basket=set(df.index), slot_count=4,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    assert "W2" not in {p.mint for p in result.positions}
    assert result.excluded_no_liquidity == 1


def test_simulator_reads_outcome_reserves_only_at_exit():
    """No-leakage: zeroing the outcome reserves must not change which tokens
    enter or when -- the outcome only affects the realised exit value."""
    df = sim_frame()
    base = run_backtest(df, basket=set(df.index), slot_count=4,
                        initial_bankroll=100.0, dex_fee_rate=0.0025,
                        entry_offset_secs=0)
    scrambled = df.copy()
    scrambled["outcome_base_reserve"] = 0.0
    scrambled["outcome_quote_reserve"] = 0.0
    after = run_backtest(scrambled, basket=set(df.index), slot_count=4,
                         initial_bankroll=100.0, dex_fee_rate=0.0025,
                         entry_offset_secs=0)
    # the same set of tokens entered (entry decisions ignore the outcome).
    assert ({p.mint for p in base.positions}
            == {p.mint for p in after.positions})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_backtest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.backtest'`.

- [ ] **Step 3: Write `model/backtest.py`**

Create `model/backtest.py`:

```python
"""The portfolio-evolution backtest simulator (spec 7).

One paper bankroll is split into N equal slots. The simulator walks the
dataset chronologically: a token becomes eligible at its entry instant
(graduation_time + an offset, the T0+12h point-in-time moment); if it is in
the basket and a slot is free it enters one equal-weight slice of the current
bankroll, priced through the honest fill model against the entry pool depth.
It is held to its horizon (outcome_checked_at) and exits there, priced
against the outcome pool depth -- an abandoned token's empty pool realises
-100%. Capital freed at an exit recycles into the next eligible entry. The
output is an equity curve over calendar time plus every per-position outcome.

No leakage: entry decisions use only the entry instant and pool depth; the
outcome reserves are read ONLY at the exit event.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Set

import pandas as pd

from model.costs import exit_fill, entry_fill

log = logging.getLogger("model.backtest")

# The point-in-time entry instant is T0 + 12 hours (spec 4 / 7).
ENTRY_OFFSET_SECS = 12 * 3600


@dataclass
class Position:
    """One completed round-trip trade."""

    mint: str
    entry_time: int
    exit_time: int
    sol_in: float          # SOL committed at entry (the slot slice)
    sol_out: float         # SOL realised at exit
    return_pct: float      # (sol_out - sol_in) / sol_in


@dataclass
class BacktestResult:
    """The outcome of one portfolio-evolution simulation."""

    positions: List[Position]
    equity_curve: pd.Series        # equity indexed by event time, chronological
    final_equity: float
    total_return: float            # (final - initial) / initial
    excluded_no_liquidity: int     # basket tokens dropped for NaN entry depth


def _has_entry_liquidity(row: pd.Series) -> bool:
    """True if the token has a usable (non-NaN, positive) entry pool depth."""
    base = row["liq_base_reserve"]
    quote = row["liq_quote_reserve"]
    if base is None or quote is None:
        return False
    if isinstance(base, float) and math.isnan(base):
        return False
    if isinstance(quote, float) and math.isnan(quote):
        return False
    return base > 0.0 and quote > 0.0


def run_backtest(
    df: pd.DataFrame,
    basket: Set[str],
    slot_count: int,
    initial_bankroll: float,
    dex_fee_rate: float,
    entry_offset_secs: int = ENTRY_OFFSET_SECS,
) -> BacktestResult:
    """Simulate the portfolio-evolution backtest for one basket.

    Args:
        df: the raw frame (model.data output) -- needs graduation_time,
            outcome_checked_at, and the liq_*/outcome_* reserve columns.
        basket: the set of mints this run is allowed to hold.
        slot_count: N equal bankroll slots.
        initial_bankroll: starting paper SOL.
        dex_fee_rate: the per-leg DEX swap fee.
        entry_offset_secs: seconds after graduation_time a token is eligible.

    Returns:
        A BacktestResult with the equity curve, per-position outcomes, the
        final equity, the total return, and the excluded-token count.
    """
    # Build the chronological event list: (time, kind, mint). An ENTRY event
    # is at graduation_time + offset; an EXIT event at outcome_checked_at.
    # Tradeable = in the basket AND has usable entry liquidity.
    excluded = 0
    tradeable: List[str] = []
    for mint in basket:
        if mint not in df.index:
            continue
        row = df.loc[mint]
        if not _has_entry_liquidity(row):
            excluded += 1
            continue
        tradeable.append(mint)
    if excluded:
        log.info("excluded %d basket token(s) with no entry liquidity",
                 excluded)

    events = []
    for mint in tradeable:
        row = df.loc[mint]
        entry_t = int(row["graduation_time"]) + entry_offset_secs
        exit_t = int(row["outcome_checked_at"])
        if exit_t < entry_t:
            exit_t = entry_t  # a degenerate horizon collapses to a flat exit
        events.append((entry_t, 1, mint))   # kind 1 = ENTRY
        events.append((exit_t, 0, mint))    # kind 0 = EXIT
    # Sort by time; at equal time process EXITs (0) before ENTRYs (1) so a
    # slot freed at instant t is reusable by an entry at the same instant.
    events.sort(key=lambda e: (e[0], e[1]))

    bankroll = float(initial_bankroll)
    free_slots = slot_count
    open_positions = {}            # mint -> (entry_time, sol_in, tokens_held)
    positions: List[Position] = []
    equity_points = [(events[0][0] if events else 0, bankroll)]

    for event_time, kind, mint in events:
        row = df.loc[mint]
        if kind == 1:  # ENTRY
            if free_slots <= 0:
                continue  # no slot free -- this token is skipped entirely
            slice_sol = bankroll / max(free_slots, 1)
            if slice_sol <= 0.0:
                continue
            tokens = entry_fill(
                sol_in=slice_sol,
                base_reserve=float(row["liq_base_reserve"]),
                quote_reserve=float(row["liq_quote_reserve"]),
                fee_rate=dex_fee_rate,
            )
            bankroll -= slice_sol
            free_slots -= 1
            open_positions[mint] = (event_time, slice_sol, tokens)
        else:  # EXIT
            if mint not in open_positions:
                continue  # this token never entered (slot was full)
            entry_time, sol_in, tokens = open_positions.pop(mint)
            sol_out = exit_fill(
                tokens_in=tokens,
                base_reserve=float(row["outcome_base_reserve"]),
                quote_reserve=float(row["outcome_quote_reserve"]),
                fee_rate=dex_fee_rate,
            )
            bankroll += sol_out
            free_slots += 1
            return_pct = (sol_out - sol_in) / sol_in if sol_in > 0 else 0.0
            positions.append(
                Position(
                    mint=mint,
                    entry_time=entry_time,
                    exit_time=event_time,
                    sol_in=sol_in,
                    sol_out=sol_out,
                    return_pct=return_pct,
                )
            )
        # equity = idle bankroll + the entry cost basis of open positions.
        held_basis = sum(p[1] for p in open_positions.values())
        equity_points.append((event_time, bankroll + held_basis))

    times = [t for t, _ in equity_points]
    values = [v for _, v in equity_points]
    equity_curve = pd.Series(values, index=times, name="equity")

    final_equity = float(equity_curve.iloc[-1])
    total_return = (final_equity - initial_bankroll) / initial_bankroll
    return BacktestResult(
        positions=positions,
        equity_curve=equity_curve,
        final_equity=final_equity,
        total_return=total_return,
        excluded_no_liquidity=excluded,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_backtest.py -q`
Expected: PASS — all nine tests green.

- [ ] **Step 5: Commit**

```bash
git add model/backtest.py model/tests/test_backtest.py
git commit -m "Add portfolio-evolution backtest simulator with honest costs

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `walkforward.py` — the expanding-window walk-forward harness

**Files:**
- Create: `model/walkforward.py`
- Test: `model/tests/test_walkforward.py`

`walkforward.py` orchestrates the time-split walk-forward validation (spec §8). `build_folds(df)` orders tokens by `graduation_time`'s calendar month and builds expanding-window folds: each fold trains on every token in every month before a cutoff month and tests on the cutoff month; the first fold needs ≥2 training months. `run_walkforward(df, config)` runs every fold: it builds features, trains a `SurvivalModel` on the training tokens, scores the **test** tokens, selects the model basket (test tokens whose calibrated score ≥ `entry_threshold`), and backtests that basket — and, for the same test tokens, the three baselines — through `run_backtest`. It returns a `WalkForwardResult` with per-fold model and baseline `BacktestResult`s plus the held-out test scores (for the calibration plot). A fold with too few training or test tokens is skipped and logged (spec §10). An explicit no-leakage test asserts no fold's test token has a `graduation_time` at or before its training cutoff.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_walkforward.py`:

```python
"""Unit tests for model.walkforward -- the expanding-window harness."""

import numpy as np
import pandas as pd

from model.config import load_config
from model.regime import month_of
from model.walkforward import build_folds, run_walkforward


def _ts(year, month, day):
    return int(pd.Timestamp(year=year, month=month, day=day,
                            tz="UTC").timestamp())


def wf_frame(per_month=60, seed=0):
    """A 4-month synthetic dataset with a learnable survival signal."""
    rng = np.random.default_rng(seed)
    rows = []
    mints = []
    for mi, (yr, mo) in enumerate(
        [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]
    ):
        for k in range(per_month):
            liq_quote = rng.uniform(0.1, 5.0)
            survived = int(rng.uniform(0, 1) < 0.2 + 0.1 * liq_quote)
            grad_t = _ts(yr, mo, 1 + (k % 27))
            rows.append(
                {
                    "graduation_time": grad_t,
                    "graduation_slot": 1000 + mi * 1000 + k,
                    "survived": survived,
                    "outcome_base_reserve": 1e15,
                    "outcome_quote_reserve": (3e12 if survived else 0.0),
                    "outcome_checked_at": grad_t + 14 * 86400,
                    "liq_base_reserve": 1e15,
                    "liq_quote_reserve": liq_quote * 1e11,
                    "lp_burned": 1,
                    "curve_real_sol_reserves": 8.5e10,
                    "curve_real_token_reserves": 0.0,
                    "curve_token_total_supply": 2.8e14,
                    "deployer_prior_launches": int(rng.integers(0, 30)),
                    "deployer_age_secs": int(rng.integers(0, 1_000_000)),
                }
            )
            mints.append(f"M{mi}_{k}")
    df = pd.DataFrame(rows, index=pd.Index(mints, name="mint"))
    return df.sort_values("graduation_time", kind="stable")


def test_build_folds_makes_expanding_windows():
    folds = build_folds(wf_frame())
    # 4 months -> the first fold trains on 2, tests the 3rd; then a 4th fold.
    assert len(folds) >= 2
    first = folds[0]
    # the first fold's training months all precede its test month.
    assert first.test_month > max(first.train_months)


def test_no_test_token_predates_its_training_cutoff():
    """The explicit no-leakage test (spec 8 / 10)."""
    df = wf_frame()
    for fold in build_folds(df):
        cutoff = pd.Timestamp(fold.test_month + "-01", tz="UTC").timestamp()
        train_times = df.loc[fold.train_mints, "graduation_time"]
        test_times = df.loc[fold.test_mints, "graduation_time"]
        # every training token graduated strictly before the test month.
        assert train_times.max() < cutoff
        # every test token graduated in (or after) the test month.
        assert test_times.min() >= cutoff


def test_run_walkforward_produces_per_fold_results():
    df = wf_frame()
    cfg = load_config(entry_threshold=0.5)
    result = run_walkforward(df, cfg)
    assert len(result.folds) >= 2
    for fold_result in result.folds:
        # each fold ran the model and the three baselines.
        assert fold_result.model_result is not None
        assert set(fold_result.baseline_results) == {
            "buy_everything", "random_basket", "heuristic_basket"
        }


def test_walkforward_test_scores_are_held_out():
    """The scores attached to a fold cover only that fold's test tokens."""
    df = wf_frame()
    result = run_walkforward(df, load_config())
    for fold_result in result.folds:
        scored = set(fold_result.test_scores.index)
        assert scored == set(fold_result.fold.test_mints)


def test_a_fold_with_too_few_rows_is_skipped(caplog):
    """A month with fewer than the minimum test rows is skipped, not crashed."""
    df = wf_frame(per_month=2)  # 2 rows/month -> below the min-rows floor
    result = run_walkforward(df, load_config())
    # with such thin months every fold is skipped -> no fold results.
    assert result.folds == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_walkforward.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.walkforward'`.

- [ ] **Step 3: Write `model/walkforward.py`**

Create `model/walkforward.py`:

```python
"""The expanding-window walk-forward harness (spec 8).

Tokens are ordered by graduation month. Each fold trains on every token in
every month before a cutoff month and tests on the cutoff month -- the
training window expands as the cutoff rolls forward. The training cutoff
strictly precedes the test tokens and every feature is point-in-time, so
there is no lookahead leakage (test_walkforward.py asserts this explicitly).

For each fold the harness trains a calibrated SurvivalModel, scores the
held-out test tokens, selects the model basket (test tokens scoring at or
above the entry threshold), and backtests it -- and the three baselines over
the same test tokens -- through the portfolio-evolution simulator. A fold
with too few training or test rows is skipped and logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd

from model.backtest import BacktestResult, run_backtest
from model.baselines import buy_everything, heuristic_basket, random_basket
from model.config import Config
from model.features import build_features
from model.regime import month_of
from model.survival import train_survival_model

log = logging.getLogger("model.walkforward")

# Minimum rows for a fold to run (spec 10: too-small folds are skipped).
_MIN_TRAIN_ROWS = 50
_MIN_TEST_ROWS = 20

# The 3-rule heuristic baseline's thresholds (Task 6 re-specified rules).
# A non-serial deployer and an entry-liquidity floor; chosen to be a
# meaningful but not extreme filter on this dataset.
_HEURISTIC_MAX_PRIOR_LAUNCHES = 10
_HEURISTIC_MIN_LIQ_QUOTE = 5_000_000_000.0  # 5 SOL in lamports


@dataclass
class Fold:
    """One expanding-window fold: train months -> a single test month."""

    train_months: List[str]
    test_month: str
    train_mints: List[str]
    test_mints: List[str]


@dataclass
class FoldResult:
    """The backtest outcomes of one fold -- model + three baselines."""

    fold: Fold
    model_result: BacktestResult
    baseline_results: Dict[str, BacktestResult]
    test_scores: pd.Series          # calibrated score per test token
    test_labels: pd.Series          # the survived label per test token
    model_basket_size: int


@dataclass
class WalkForwardResult:
    """Every fold's results across the whole walk-forward run."""

    folds: List[FoldResult] = field(default_factory=list)


def build_folds(df: pd.DataFrame) -> List[Fold]:
    """Build expanding-window folds from the dataset's calendar months.

    Fold i trains on months[:i+1] and tests on months[i+1], for i from 1 (so
    the first fold has >= 2 training months) up to the last month.
    """
    months_by_mint = df["graduation_time"].apply(month_of)
    months = sorted(months_by_mint.unique())
    folds: List[Fold] = []
    for i in range(1, len(months)):
        train_months = months[:i + 1 - 1]   # months[0 .. i-1]
        test_month = months[i]
        train_mask = months_by_mint.isin(train_months)
        test_mask = months_by_mint == test_month
        folds.append(
            Fold(
                train_months=list(train_months),
                test_month=test_month,
                train_mints=list(df.index[train_mask]),
                test_mints=list(df.index[test_mask]),
            )
        )
    return folds


def _run_one_fold(
    df: pd.DataFrame, fold: Fold, config: Config
) -> FoldResult:
    """Train, score, and backtest one fold's model and baselines."""
    train_df = df.loc[fold.train_mints]
    test_df = df.loc[fold.test_mints]

    X_train, y_train = build_features(train_df)
    X_test, _y_test = build_features(test_df)

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
                max_prior_launches=_HEURISTIC_MAX_PRIOR_LAUNCHES,
                min_liq_quote=_HEURISTIC_MIN_LIQ_QUOTE,
            )
        ),
    }
    return FoldResult(
        fold=fold,
        model_result=model_result,
        baseline_results=baseline_results,
        test_scores=scores,
        test_labels=test_df["survived"].astype(int),
        model_basket_size=len(model_basket),
    )


def run_walkforward(df: pd.DataFrame, config: Config) -> WalkForwardResult:
    """Run the whole expanding-window walk-forward backtest.

    Returns a WalkForwardResult with one FoldResult per runnable fold. A fold
    with fewer than _MIN_TRAIN_ROWS training or _MIN_TEST_ROWS test rows is
    skipped and logged.
    """
    result = WalkForwardResult()
    for fold in build_folds(df):
        if len(fold.train_mints) < _MIN_TRAIN_ROWS:
            log.info(
                "skipping fold (test %s): only %d training rows",
                fold.test_month, len(fold.train_mints),
            )
            continue
        if len(fold.test_mints) < _MIN_TEST_ROWS:
            log.info(
                "skipping fold (test %s): only %d test rows",
                fold.test_month, len(fold.test_mints),
            )
            continue
        log.info(
            "fold: train %s (%d rows) -> test %s (%d rows)",
            fold.train_months, len(fold.train_mints),
            fold.test_month, len(fold.test_mints),
        )
        result.folds.append(_run_one_fold(df, fold, config))
    return result
```

Note on `build_folds`: `months[:i + 1 - 1]` is `months[:i]` — months index `0` through `i-1` — written so the relationship to the test index `i` is explicit. For `i = 1` the training months are `months[:1]`... which is one month; to guarantee the first fold has **≥2** training months, the loop starts at `i = 1` and `months[:i]` for `i=1` is one month — so the loop must start at `i = 2`. Correct this in Step 3: the loop is `for i in range(2, len(months))` and `train_months = months[:i]`. Re-read the corrected version below before writing the file.

- [ ] **Step 3a: Apply the fold-window correction**

In `model/walkforward.py`, the `build_folds` loop must be exactly:

```python
    folds: List[Fold] = []
    for i in range(2, len(months)):
        train_months = months[:i]            # months[0 .. i-1]: >= 2 months
        test_month = months[i]
        train_mask = months_by_mint.isin(train_months)
        test_mask = months_by_mint == test_month
        folds.append(
            Fold(
                train_months=list(train_months),
                test_month=test_month,
                train_mints=list(df.index[train_mask]),
                test_mints=list(df.index[test_mask]),
            )
        )
    return folds
```

With the 4-month synthetic frame (`wf_frame`), this yields 2 folds: train ⟨Jan,Feb⟩→test Mar and train ⟨Jan,Feb,Mar⟩→test Apr — matching `test_build_folds_makes_expanding_windows`'s `>= 2` assertion. With the real 7-month dataset it yields 5 folds (test Jan…May).

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_walkforward.py -q`
Expected: PASS — all six tests green. (Training a `SurvivalModel` per fold takes a few seconds.)

- [ ] **Step 5: Commit**

```bash
git add model/walkforward.py model/tests/test_walkforward.py
git commit -m "Add expanding-window walk-forward harness with a no-leakage test

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: `report.py` — metrics and the report output

**Files:**
- Create: `model/report.py`
- Test: `model/tests/test_report.py`

`report.py` turns a `WalkForwardResult` into the Phase 3 deliverable: a markdown summary plus matplotlib plots (spec §8). Pure metric functions — `max_drawdown(equity_curve)`, `outcome_distribution(positions)` (count, mean, median, the win rate, the −100% count), and `calibration_table(scores, labels)` (binned predicted-vs-observed survival) — are unit-tested directly. `write_report(wf_result, df, config)` aggregates the model and baseline `BacktestResult`s across folds, computes per-regime metrics (joining each fold's test month to its regime via `regime.assign_regime`), writes three PNG plots (the pooled equity curve, the calibration curve, the per-position outcome histogram) and a `report.md` under `config.report_dir`, and states the decision-gate inputs — does the model beat all three baselines on total return after costs, across ≥2 regimes, with max drawdown ≤ 40% — for a human to read. The report never auto-decides the gate.

- [ ] **Step 1: Write the failing test**

Create `model/tests/test_report.py`:

```python
"""Unit tests for model.report -- metrics and report-artifact writing."""

import numpy as np
import pandas as pd

from model.backtest import Position
from model.report import (
    calibration_table,
    max_drawdown,
    outcome_distribution,
)


def test_max_drawdown_of_a_monotonic_curve_is_zero():
    curve = pd.Series([100.0, 110.0, 120.0, 130.0])
    assert max_drawdown(curve) == 0.0


def test_max_drawdown_measures_the_largest_peak_to_trough_drop():
    # peak 200 -> trough 120 is a 40% drawdown.
    curve = pd.Series([100.0, 200.0, 120.0, 160.0])
    assert abs(max_drawdown(curve) - 0.40) < 1e-9


def test_max_drawdown_is_reported_as_a_positive_fraction():
    curve = pd.Series([100.0, 50.0])
    dd = max_drawdown(curve)
    assert dd > 0.0  # a 50% drop -> 0.5, positive


def test_outcome_distribution_summarises_positions():
    positions = [
        Position("A", 0, 1, 10.0, 25.0, 1.5),    # winner
        Position("B", 0, 1, 10.0, 12.0, 0.2),    # small winner
        Position("C", 0, 1, 10.0, 0.0, -1.0),    # total loss
        Position("D", 0, 1, 10.0, 4.0, -0.6),    # loser
    ]
    dist = outcome_distribution(positions)
    assert dist["count"] == 4
    assert dist["win_rate"] == 0.5            # 2 of 4 positive
    assert dist["total_loss_count"] == 1      # one -100%
    # the fat tail: mean != median.
    assert dist["mean_return"] != dist["median_return"]


def test_outcome_distribution_of_an_empty_basket_is_safe():
    dist = outcome_distribution([])
    assert dist["count"] == 0
    assert dist["win_rate"] == 0.0


def test_calibration_table_bins_predicted_vs_observed():
    # 100 tokens; score == true survival probability by construction.
    rng = np.random.default_rng(0)
    scores = pd.Series(rng.uniform(0, 1, size=100))
    labels = pd.Series((rng.uniform(0, 1, size=100) < scores).astype(int))
    table = calibration_table(scores, labels, n_bins=5)
    assert len(table) <= 5
    # each bin row has a predicted mean and an observed survival rate.
    assert {"bin_mid", "predicted_mean", "observed_rate", "count"}.issubset(
        table.columns
    )


def test_write_report_creates_the_markdown_and_plots(tmp_path):
    """write_report writes report.md and the three PNGs under report_dir."""
    from model.config import load_config
    from model.walkforward import run_walkforward
    from model.tests.test_walkforward import wf_frame

    df = wf_frame()
    cfg = load_config(report_dir=str(tmp_path / "report"))
    wf_result = run_walkforward(df, cfg)

    from model.report import write_report
    write_report(wf_result, df, cfg)

    report_dir = tmp_path / "report"
    assert (report_dir / "report.md").is_file()
    assert (report_dir / "equity_curve.png").is_file()
    assert (report_dir / "calibration.png").is_file()
    assert (report_dir / "outcome_distribution.png").is_file()
    # the markdown names the decision gate.
    text = (report_dir / "report.md").read_text()
    assert "decision gate" in text.lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest model/tests/test_report.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'model.report'`.

- [ ] **Step 3: Write `model/report.py`**

Create `model/report.py`:

```python
"""Metrics and the Phase 3 report (spec 8).

Turns a WalkForwardResult into the Phase 3 deliverable: a markdown summary
plus matplotlib plots (the equity curve, the probability-calibration curve,
the per-position outcome distribution), written under config.report_dir. The
metrics are portfolio-level -- total return, max drawdown, the full
fat-tailed outcome distribution, and calibration -- never classifier
accuracy, which is misleading under heavy class imbalance.

The report STATES the decision-gate inputs (does the model basket beat all
three baselines after costs, across >= 2 regimes, with max drawdown <= 40%);
a human reads the gate. The report never auto-decides it.
"""

from __future__ import annotations

import os
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")  # headless: write PNGs, never open a window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from model.backtest import Position  # noqa: E402
from model.config import Config  # noqa: E402
from model.regime import assign_regime  # noqa: E402
from model.walkforward import WalkForwardResult  # noqa: E402

_MAX_DRAWDOWN_GATE = 0.40  # spec 2: the pre-committed drawdown ceiling
_BASELINE_KEYS = ("buy_everything", "random_basket", "heuristic_basket")


def max_drawdown(equity_curve: pd.Series) -> float:
    """The largest peak-to-trough fractional drop, as a positive fraction.

    A monotonically rising curve has a drawdown of 0.0.
    """
    if len(equity_curve) == 0:
        return 0.0
    running_peak = equity_curve.cummax()
    drawdowns = (running_peak - equity_curve) / running_peak
    return float(drawdowns.max())


def outcome_distribution(positions: List[Position]) -> Dict[str, float]:
    """Summary statistics of a list of per-position returns.

    Returns count, mean_return, median_return, win_rate (fraction with a
    positive return), and total_loss_count (positions at -100%).
    """
    if not positions:
        return {
            "count": 0,
            "mean_return": 0.0,
            "median_return": 0.0,
            "win_rate": 0.0,
            "total_loss_count": 0,
        }
    returns = np.array([p.return_pct for p in positions], dtype=float)
    return {
        "count": int(len(returns)),
        "mean_return": float(np.mean(returns)),
        "median_return": float(np.median(returns)),
        "win_rate": float(np.mean(returns > 0.0)),
        "total_loss_count": int(np.sum(returns <= -0.999)),
    }


def calibration_table(
    scores: pd.Series, labels: pd.Series, n_bins: int = 10
) -> pd.DataFrame:
    """Bin predicted survival probabilities against the observed rate.

    Each row: the bin midpoint, the mean predicted probability, the observed
    survival rate, and the bin's token count. Empty bins are dropped.
    """
    df = pd.DataFrame({"score": scores.values, "label": labels.values})
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    df["bin"] = pd.cut(df["score"], bins=edges, include_lowest=True)
    rows = []
    for interval, group in df.groupby("bin", observed=True):
        if len(group) == 0:
            continue
        rows.append(
            {
                "bin_mid": float(interval.mid),
                "predicted_mean": float(group["score"].mean()),
                "observed_rate": float(group["label"].mean()),
                "count": int(len(group)),
            }
        )
    return pd.DataFrame(rows)


def _pool_positions(results) -> List[Position]:
    """Flatten the per-fold positions of a list of BacktestResults."""
    pooled: List[Position] = []
    for result in results:
        pooled.extend(result.positions)
    return pooled


def _chain_equity(results) -> pd.Series:
    """Chain per-fold equity curves into one normalised compounding curve.

    Each fold's curve is rebased to start where the previous fold ended, so
    the pooled curve compounds fold returns -- the walk-forward equity curve.
    """
    chained_values: List[float] = []
    level = 1.0
    for result in results:
        curve = result.equity_curve
        if len(curve) == 0:
            continue
        start = curve.iloc[0]
        if start == 0:
            continue
        normalised = curve / start * level
        chained_values.extend(list(normalised.values))
        level = normalised.iloc[-1]
    if not chained_values:
        return pd.Series([1.0], name="equity")
    return pd.Series(chained_values, name="equity")


def _total_return(results) -> float:
    """The compounded total return across a list of per-fold results."""
    level = 1.0
    for result in results:
        level *= (1.0 + result.total_return)
    return level - 1.0


def _plot_equity(model_curve, baseline_curves, path) -> None:
    plt.figure(figsize=(9, 5))
    plt.plot(model_curve.values, label="model basket", linewidth=2)
    for name, curve in baseline_curves.items():
        plt.plot(curve.values, label=name, alpha=0.7)
    plt.title("Walk-forward equity curve (compounded, normalised to 1.0)")
    plt.xlabel("backtest event")
    plt.ylabel("equity (x initial)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()


def _plot_calibration(table, path) -> None:
    plt.figure(figsize=(6, 6))
    plt.plot([0, 1], [0, 1], "k--", label="perfect calibration")
    if len(table) > 0:
        plt.plot(table["predicted_mean"], table["observed_rate"],
                 "o-", label="model")
    plt.title("Survival-probability calibration")
    plt.xlabel("predicted survival probability")
    plt.ylabel("observed survival rate")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()


def _plot_outcomes(positions, path) -> None:
    plt.figure(figsize=(9, 5))
    returns = [p.return_pct for p in positions]
    if returns:
        plt.hist(returns, bins=40)
    plt.title("Per-position outcome distribution (model basket)")
    plt.xlabel("position return (fraction)")
    plt.ylabel("position count")
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()


def write_report(
    wf_result: WalkForwardResult, df: pd.DataFrame, config: Config
) -> str:
    """Write report.md and the three PNG plots under config.report_dir.

    Returns the path to report.md.
    """
    os.makedirs(config.report_dir, exist_ok=True)

    model_results = [f.model_result for f in wf_result.folds]
    baseline_results = {
        key: [f.baseline_results[key] for f in wf_result.folds]
        for key in _BASELINE_KEYS
    }

    # pooled equity, returns, drawdown.
    model_curve = _chain_equity(model_results)
    baseline_curves = {
        key: _chain_equity(results)
        for key, results in baseline_results.items()
    }
    model_total = _total_return(model_results)
    model_dd = max_drawdown(model_curve)
    baseline_totals = {
        key: _total_return(results)
        for key, results in baseline_results.items()
    }

    # pooled positions + the outcome distribution.
    model_positions = _pool_positions(model_results)
    model_dist = outcome_distribution(model_positions)

    # pooled calibration over every fold's held-out test scores.
    all_scores = pd.concat([f.test_scores for f in wf_result.folds]) \
        if wf_result.folds else pd.Series(dtype=float)
    all_labels = pd.concat([f.test_labels for f in wf_result.folds]) \
        if wf_result.folds else pd.Series(dtype=int)
    cal_table = (
        calibration_table(all_scores, all_labels)
        if len(all_scores) > 0 else pd.DataFrame()
    )

    # per-regime model total return.
    regimes = assign_regime(df)
    per_regime: Dict[str, float] = {}
    for fold in wf_result.folds:
        test_month = fold.fold.test_month
        # every test token of a fold shares the fold's test month/regime.
        sample_mint = fold.fold.test_mints[0]
        regime = regimes.loc[sample_mint]
        per_regime.setdefault(regime, 1.0)
        per_regime[regime] *= (1.0 + fold.model_result.total_return)
    per_regime = {k: v - 1.0 for k, v in per_regime.items()}

    # plots.
    _plot_equity(model_curve, baseline_curves,
                 os.path.join(config.report_dir, "equity_curve.png"))
    _plot_calibration(cal_table,
                      os.path.join(config.report_dir, "calibration.png"))
    _plot_outcomes(model_positions,
                   os.path.join(config.report_dir,
                                "outcome_distribution.png"))

    # the decision-gate inputs (stated, not auto-decided).
    beats_all = all(
        model_total > baseline_totals[key] for key in _BASELINE_KEYS
    )
    enough_regimes = len(per_regime) >= 2
    drawdown_ok = model_dd <= _MAX_DRAWDOWN_GATE

    report_path = os.path.join(config.report_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(_render_markdown(
            config=config,
            n_folds=len(wf_result.folds),
            model_total=model_total,
            model_dd=model_dd,
            baseline_totals=baseline_totals,
            model_dist=model_dist,
            per_regime=per_regime,
            cal_table=cal_table,
            beats_all=beats_all,
            enough_regimes=enough_regimes,
            drawdown_ok=drawdown_ok,
        ))
    return report_path


def _render_markdown(**ctx) -> str:
    """Render the report.md body from the computed context."""
    config: Config = ctx["config"]
    lines: List[str] = []
    lines.append("# Phase 3 — Survival Model & Backtest Report")
    lines.append("")
    lines.append(
        "An honest walk-forward, portfolio-evolution backtest of the "
        "pump.fun token survival strategy. Generated by `model/run.py`."
    )
    lines.append("")
    lines.append("## Configuration")
    lines.append("")
    lines.append(f"- Slots: {config.slot_count}")
    lines.append(f"- Entry score threshold: {config.entry_threshold}")
    lines.append(f"- DEX fee per leg: {config.dex_fee_rate}")
    lines.append(f"- Walk-forward folds run: {ctx['n_folds']}")
    lines.append("")
    lines.append("## Headline results (after costs, out-of-sample)")
    lines.append("")
    lines.append("| Basket | Total return | ")
    lines.append("|---|---|")
    lines.append(f"| **Model** | {ctx['model_total']:+.2%} |")
    for key, value in ctx["baseline_totals"].items():
        lines.append(f"| {key} | {value:+.2%} |")
    lines.append("")
    lines.append(f"- Model basket max drawdown: {ctx['model_dd']:.2%}")
    dist = ctx["model_dist"]
    lines.append(
        f"- Model positions: {dist['count']}, "
        f"win rate {dist['win_rate']:.2%}, "
        f"mean return {dist['mean_return']:+.2%}, "
        f"median return {dist['median_return']:+.2%}, "
        f"total-loss positions {dist['total_loss_count']}"
    )
    lines.append("")
    lines.append("## Per-regime model return")
    lines.append("")
    if ctx["per_regime"]:
        lines.append("| Regime | Model total return |")
        lines.append("|---|---|")
        for regime, value in sorted(ctx["per_regime"].items()):
            lines.append(f"| {regime} | {value:+.2%} |")
    else:
        lines.append("_No folds ran -- per-regime breakdown unavailable._")
    lines.append("")
    lines.append("## Probability calibration")
    lines.append("")
    cal = ctx["cal_table"]
    if len(cal) > 0:
        lines.append("| Predicted mean | Observed rate | Count |")
        lines.append("|---|---|---|")
        for _, row in cal.iterrows():
            lines.append(
                f"| {row['predicted_mean']:.3f} | "
                f"{row['observed_rate']:.3f} | {int(row['count'])} |"
            )
    else:
        lines.append("_No held-out scores -- calibration unavailable._")
    lines.append("")
    lines.append("![equity curve](equity_curve.png)")
    lines.append("")
    lines.append("![calibration](calibration.png)")
    lines.append("")
    lines.append("![outcome distribution](outcome_distribution.png)")
    lines.append("")
    lines.append("## Decision gate")
    lines.append("")
    lines.append(
        "The pre-committed decision gate (spec 2): reviving the parked live "
        "component is greenlit only if the model basket beats all three "
        "baselines, out-of-sample, after costs, across >= 2 distinct market "
        "regimes, with a maximum drawdown <= 40%. This report states the "
        "inputs; a human evaluates the gate."
    )
    lines.append("")
    lines.append(
        f"- Beats all three baselines on total return: "
        f"**{ctx['beats_all']}**"
    )
    lines.append(
        f"- Spans >= 2 distinct market regimes: "
        f"**{ctx['enough_regimes']}**"
    )
    lines.append(
        f"- Max drawdown <= 40%: **{ctx['drawdown_ok']}** "
        f"(measured {ctx['model_dd']:.2%})"
    )
    lines.append("")
    gate_pass = (
        ctx["beats_all"] and ctx["enough_regimes"] and ctx["drawdown_ok"]
    )
    lines.append(
        f"All three gate inputs hold: **{gate_pass}**. A human makes the "
        "final deploy / do-not-deploy decision from this report; a failing "
        "gate ('no edge -- do not deploy') is a valid, planned outcome."
    )
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest model/tests/test_report.py -q`
Expected: PASS — all seven tests green. (`test_write_report_creates_the_markdown_and_plots` runs a small walk-forward, so it takes a few seconds.)

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m pytest model/tests -q`
Expected: PASS — every test from Tasks 1–11 green (scaffold, config, data, features, costs, baselines, regime, survival, backtest, walkforward, report).

- [ ] **Step 6: Commit**

```bash
git add model/report.py model/tests/test_report.py
git commit -m "Add metrics and the markdown + plots report

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: `run.py` — the CLI orchestrator + the real backtest run

**Files:**
- Create: `model/run.py`
- Test: exercised by the real backtest run below (no `pytest` test — see the rationale)

`run.py` wires the whole Phase 3 pipeline: load config → load `historical_graduations` into a DataFrame → run the expanding-window walk-forward backtest (model + three baselines) → write the report. It is thin orchestration glue: every pure function it calls is already TDD-tested and `walkforward`/`report` are tested on synthetic data, so — per the writing-plans guidance that thin glue with no independent logic does not need its own failing-test cycle — `run.py` has no `pytest` test. Its correctness is verified by the **real backtest run** (Step 3 below) against the actual `./storm.db`.

- [ ] **Step 1: Write `model/run.py`**

Create `model/run.py`:

```python
"""The Phase 3 backtest orchestrator.

Wires: config -> load historical_graduations into a DataFrame -> run the
expanding-window walk-forward backtest (the calibrated survival model and the
three baselines) -> write the report (markdown + plots) under the report dir.

Usage:
    python3 -m model.run
    python3 -m model.run --slots 30 --entry-threshold 0.6
"""

from __future__ import annotations

import argparse
import logging

from model.config import load_config
from model.data import load_graduations
from model.report import write_report
from model.walkforward import run_walkforward

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("model.run")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="solana-storm Phase 3 survival-model walk-forward backtest"
    )
    parser.add_argument(
        "--slots", type=int, default=None,
        help="number of equal bankroll slots (default: Config.slot_count=20)",
    )
    parser.add_argument(
        "--entry-threshold", type=float, default=None,
        help="min calibrated survival score to enter (default: 0.55)",
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
    log.info(
        "loaded %d graduations; survived split %s",
        len(df), df["survived"].value_counts().to_dict(),
    )

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

- [ ] **Step 2: Verify the whole suite still passes (no regression)**

Run: `python3 -m pytest model/tests -q`
Expected: PASS — adding `run.py` imports only existing, tested modules; no test changes; every test still green.

- [ ] **Step 3: Run the real backtest against `./storm.db`**

Confirm `./storm.db` exists and has the `historical_graduations` table (the Phase 2 deliverable — ~4,755 rows), then run:

```bash
python3 -m model.run
```

Expected: the run completes in roughly 1–3 minutes (it trains five LightGBM models, one per fold, on a few-thousand-row dataset). The log shows, in order: the loaded row count (~4,755) and the survived split, a per-fold line (`fold: train [...] -> test YYYY-MM (N rows)`) for each of the ~5 folds (test Jan…May 2026), a `walk-forward complete: 5 folds ran` line, and a `report written: model/report/report.md` line. If the run errors or zero folds run, STOP and debug before Step 4.

- [ ] **Step 4: Inspect the report**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("model/report")
print("artifacts:", sorted(x.name for x in p.iterdir()))
print("--- report.md ---")
print((p / "report.md").read_text())
PY
```

Expected: `artifacts` lists `report.md`, `equity_curve.png`, `calibration.png`, `outcome_distribution.png`. The printed `report.md` has the Configuration, Headline results, Per-regime model return, Probability calibration, and Decision gate sections, with real numbers — the model and three baseline total returns, the model's max drawdown, the per-position outcome summary, the per-regime breakdown, and the three gate-input booleans. The headline numbers are whatever the model actually produced — the spec is explicit that a verdict of "no edge — do not deploy" is an acceptable, planned outcome; the deliverable is the **honest report**, not a particular result.

- [ ] **Step 5: Append a results note to `model/README.md`**

Record the run's outcome by appending a "Run log" section to `model/README.md` (replace the bracketed values with the actual numbers from Step 4):

```markdown

## Run log

- Backtest run completed `<DATE>`: `<N>` walk-forward folds, model basket
  total return `<MODEL-RETURN>`, max drawdown `<MODEL-DD>`.
- Baseline total returns: buy-everything `<BE>`, random `<RAND>`,
  heuristic `<HEUR>`.
- Decision-gate inputs — beats all baselines: `<BOOL>`; >= 2 regimes:
  `<BOOL>`; drawdown <= 40%: `<BOOL>`.
- The full report (markdown + plots) is under `model/report/`.
```

- [ ] **Step 6: Commit**

```bash
git add model/run.py model/README.md
git commit -m "Add Phase 3 backtest CLI and run the walk-forward backtest

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

### Spec coverage

Every section of the Phase 3 design spec (`2026-05-19-phase3-model-backtest-design.md`) maps to a task:

| Spec section / requirement | Where it is delivered |
|---|---|
| §2 — a `model/` package: trains a survival model, runs a walk-forward portfolio-evolution backtest, compares vs 3 baselines, emits a report | Tasks 1–12; the whole `model/` package |
| §2 — the report states the decision gate (beat 3 baselines, after costs, ≥2 regimes, max drawdown ≤ 40%); a human evaluates it | Task 11 `report.py` `_render_markdown` "Decision gate" section + `_MAX_DRAWDOWN_GATE = 0.40`; never auto-decided |
| §4 — read `historical_graduations` from `storm.db`, write nothing back | Task 3 `data.py` (read-only stdlib `sqlite3`) |
| §4 — model features strictly point-in-time (≤ T0+12h): deployer, liquidity, bonding-curve groups | Task 4 `features.py` `_RAW_FEATURE_COLUMNS` (exactly those columns) |
| §4 — plus engineered features (ratios, log transforms) | Task 4 `_ENGINEERED_FEATURE_COLUMNS` (6 logs + 3 ratios) |
| §4 — drop `mint_authority_present`, `freeze_authority_present` (constant) and the holder / `*_fraction` columns (NULL) | Task 3 `RAW_COLUMNS` does not select them; Task 4 never features them |
| §4 — missing values reach the model as NaN, never fabricated | Task 3 `pd.to_numeric(..., errors="coerce")`; Task 4 NaN-propagating `_safe_divide`; tested |
| §4 — outcome reserves NEVER features; used only for backtest returns | Task 4 `LEAKAGE_FORBIDDEN` + the no-leakage test; Task 9 reads them only at exit + its no-leakage test |
| §5 — LightGBM classifier predicting `survived` | Task 8 `survival.py` `LGBMClassifier` |
| §5 — explicit probability calibration (isotonic/Platt) on a held-out slice | Task 8 `IsotonicRegression` on the time-ordered last `calibration_fraction` of the training fold |
| §6 — baseline (a) buy-everything | Task 6 `buy_everything` |
| §6 — baseline (b) random basket, same size as the model basket, seeded | Task 6 `random_basket(df, size, seed)`; Task 10 passes `size=len(model_basket)` |
| §6 — baseline (c) re-specified 3-rule heuristic: `lp_burned` set AND `deployer_prior_launches` below a threshold AND entry liquidity above a threshold | Task 6 `heuristic_basket`; Task 10 supplies the two thresholds |
| §7 — one paper bankroll, N equal slots (~15–30, a config param) | Task 2 `Config.slot_count = 20`; Task 9 `run_backtest(slot_count=...)` |
| §7 — a token enters at its T0+12h if score > threshold and a slot is free | Task 9 ENTRY events at `graduation_time + ENTRY_OFFSET_SECS` (12h); Task 10 builds the basket from `score >= entry_threshold` |
| §7 — entry price = liq_quote/liq_base; exit price = outcome_quote/outcome_base; abandoned → −100% | Task 9 `entry_fill`/`exit_fill` against the respective reserves; empty exit pool → 0 SOL → −100% (tested) |
| §7 — honest costs: a DEX swap fee per leg + constant-product slippage vs pool depth | Task 5 `costs.py` (fee on the input leg + x·y=k); Task 9 calls it with `dex_fee_rate` |
| §7 — capital recycles from exits into new entries | Task 9 `bankroll += sol_out; free_slots += 1` then later ENTRYs reuse it (tested) |
| §7 — output: equity curve, total return, max drawdown, per-position outcome distribution | Task 9 `BacktestResult` (equity curve, total return, positions); Task 11 `max_drawdown`, `outcome_distribution` |
| §7 — the SAME simulator runs the model basket and all 3 baselines | Task 10 `_run_one_fold` calls the one `run_backtest` for the model and each baseline |
| §8 — walk-forward: expanding window, ~4 monthly folds ordered by `graduation_time`, training cutoff strictly before test tokens | Task 10 `build_folds` (expanding monthly windows; 5 folds on the real 7-month data) |
| §8 — an explicit no-leakage test in the walk-forward task | Task 10 `test_no_test_token_predates_its_training_cutoff` |
| §8 — regime labelling (mania vs quiet from graduation rate); per-regime reporting | Task 7 `regime.py`; Task 11 the "Per-regime model return" section |
| §8 — metrics: total return, max drawdown, the fat-tailed outcome distribution, calibration; NOT accuracy | Task 11 `max_drawdown`, `outcome_distribution`, `calibration_table` — no accuracy metric anywhere |
| §8 — report = markdown summary + matplotlib plots (equity curve, calibration, outcome distribution), under `model/` | Task 11 `write_report` writes `report.md` + 3 PNGs under `config.report_dir` (`model/report`) |
| §9 — the `model/` module list: `data`, `features`, `survival`, `baselines`, `costs`, `backtest`, `regime`, `walkforward`, `report`, `run`, `tests/`, `requirements.txt` | Tasks 3, 4, 8, 6, 5, 9, 7, 10, 11, 12, every `test_*`, Task 1 — one task per module |
| §9 — `requirements.txt`: pandas, numpy, lightgbm, scikit-learn, matplotlib | Task 1 `model/requirements.txt` (+ pytest, the test runner) |
| §10 — the ~2 tokens with NULL liquidity excluded from the backtest with a logged count | Task 9 `_has_entry_liquidity` + `excluded_no_liquidity` + the log line (tested) |
| §10 — a fold with too few training/test rows is skipped and logged | Task 10 `_MIN_TRAIN_ROWS` / `_MIN_TEST_ROWS` skip + log (tested) |
| §10 — pure functions unit-tested: feature engineering, the cost model vs hand-computed fills, the baseline selectors, the simulator on small fixtures | Tasks 4, 5, 6, 9 — each with a `test_*` of exactly these |
| §11 — honest caveats (hold-to-horizon bias, thin feature set, approximate timing, limited power) | Stated in Context; the `report.md` "Decision gate" section says a failing gate is a planned outcome |
| §12 — resolve: slot count, entry threshold, fold boundaries, engineered-feature list, regime method, fee/slippage params | All fixed in Context's "Open decisions resolved" and in the relevant task (config defaults, Task 4 features, Task 7 regime, Task 5 costs) |

No spec section is unaddressed. The spec's §3 "out of scope" items (trailing stops, stop-losses, re-score exits, dataset extension, the live component, a cohort-style backtest) are deliberately **not** built — stated in Context's "What this plan does NOT do".

### Placeholder scan

No step contains `TODO`, `FIXME`, `...` as elided code, "TBD", "add error handling", "handle edge cases", or "similar to Task N". Every code step shows the complete file or the complete test. Specifically:

- Every module (`config`, `data`, `features`, `costs`, `baselines`, `regime`, `survival`, `backtest`, `walkforward`, `report`, `run`) is shown in full, as is every `test_*.py`.
- Task 10 contains a deliberate two-part presentation: Step 3 shows `build_folds` with a `months[:i + 1 - 1]` expression and an inline note that it must start at `i = 2`, and **Step 3a gives the exact corrected loop**. This is not an elision — the final, correct code is fully present in Step 3a, and the executor is told to use it. (This mirrors the Phase 2 plan's practice of inline notes clarifying a subtlety.)
- The `model/README.md` "Run log" template in Task 12 Step 5 uses `<DATE>`/`<N>`/`<MODEL-RETURN>`/etc. as **deliberate fill-in-the-actual-number placeholders for a human-recorded run outcome** — intentional documentation; the step says explicitly to replace them with the run's real numbers. It is not elided code.
- The `<<<PY` heredoc in Task 12 Step 4 is a complete, runnable inspection script.

### Type, signature, and name consistency across tasks

- **`Config`** (frozen dataclass, Task 2) — every field consumed downstream exists on it: `db_path`, `table_name` (Task 3 `data.py`); `survival_min_quote_lamports` (defined, matches the ETL — not otherwise consumed, the label is already settled in the table); `slot_count`, `initial_bankroll`, `entry_threshold`, `dex_fee_rate` (Tasks 9, 10); `calibration_fraction`, `random_seed` (Tasks 8, 10); `report_dir` (Task 11). `load_config(**overrides)` is called with no args (Tasks 3, 11 sanity checks), and with `slot_count` / `entry_threshold` / `db_path` / `report_dir` overrides (Tasks 2, 11, 12) — every override names a real field.
- **`load_dataframe(conn)` / `load_graduations(config)` / `RAW_COLUMNS`** (Task 3) — `RAW_COLUMNS` names match `bootstrap/load.py`'s `_COLUMNS` exactly; `walkforward.py` and `run.py` consume the loaded frame; `features.py` reads from it the columns in `_RAW_FEATURE_COLUMNS` ∪ the engineering inputs ∪ `LABEL_COLUMN` — every one is in `RAW_COLUMNS`. `backtest.py` reads `graduation_time`, `outcome_checked_at`, `liq_base_reserve`, `liq_quote_reserve`, `outcome_base_reserve`, `outcome_quote_reserve` — all in `RAW_COLUMNS`. `regime.py` reads `graduation_time` — in `RAW_COLUMNS`.
- **`build_features(df) -> (X, y)` / `FEATURE_COLUMNS` / `LABEL_COLUMN` / `LEAKAGE_FORBIDDEN`** (Task 4) — `survival.py`'s `train_survival_model(X, y, ...)` and `SurvivalModel.score(X)` consume exactly this `X`; `walkforward.py` calls `build_features` for train and test frames. `FEATURE_COLUMNS` is the single source of truth; `test_survival.py` builds a synthetic `X` with exactly `FEATURE_COLUMNS`.
- **`entry_fill` / `exit_fill`** (Task 5, signature `(sol_in|tokens_in, base_reserve, quote_reserve, fee_rate)`) — `backtest.py` calls both with exactly these keyword arguments.
- **`buy_everything(df)` / `random_basket(df, size, seed)` / `heuristic_basket(df, max_prior_launches, min_liq_quote)`** (Task 6) — `walkforward.py` `_run_one_fold` calls all three with exactly these signatures; the heuristic thresholds are module constants in `walkforward.py`.
- **`month_of` / `label_regimes` / `assign_regime`** (Task 7) — `walkforward.py` imports `month_of`; `report.py` imports `assign_regime`. (`walkforward.py`'s import of `month_of` is used by `build_folds`.)
- **`SurvivalModel` / `train_survival_model(X, y, calibration_fraction, random_seed)`** (Task 8) — `walkforward.py` `_run_one_fold` calls `train_survival_model` with `calibration_fraction=config.calibration_fraction, random_seed=config.random_seed` and uses `model.score(X_test)`.
- **`Position` / `BacktestResult` / `run_backtest(df, basket, slot_count, initial_bankroll, dex_fee_rate, entry_offset_secs=...)`** (Task 9) — `walkforward.py` calls `run_backtest` with `basket`, `slot_count`, `initial_bankroll`, `dex_fee_rate` (defaulting `entry_offset_secs`); `report.py` imports `Position` and reads `BacktestResult.positions`, `.equity_curve`, `.total_return`. `BacktestResult` fields used by `report.py` — `positions`, `equity_curve`, `total_return` — are all defined; `Position.return_pct` is read by `outcome_distribution`.
- **`Fold` / `FoldResult` / `WalkForwardResult` / `build_folds(df)` / `run_walkforward(df, config)`** (Task 10) — `report.py` imports `WalkForwardResult` and reads `wf_result.folds`, and per fold `f.model_result`, `f.baseline_results` (a dict keyed `buy_everything`/`random_basket`/`heuristic_basket` — matching `report.py`'s `_BASELINE_KEYS`), `f.test_scores`, `f.test_labels`, `f.fold.test_month`, `f.fold.test_mints`. Every attribute exists on the Task 10 dataclasses.
- **`max_drawdown` / `outcome_distribution` / `calibration_table` / `write_report(wf_result, df, config)`** (Task 11) — `run.py` imports and calls `write_report`. `test_report.py` imports the three metric functions and `write_report`.
- **`run.py`** (Task 12) — imports `load_config` (Task 2), `load_graduations` (Task 3), `run_walkforward` (Task 10), `write_report` (Task 11) — every name exists with the used signature.
- Baseline-key strings are consistent everywhere: `walkforward.py` builds `baseline_results` with keys `buy_everything`, `random_basket`, `heuristic_basket`; `report.py` `_BASELINE_KEYS` is exactly that tuple; `test_walkforward.py` asserts that exact set.
- The build order respects dependencies: `config` (Task 2) has no model deps; `data` (3) → `config`; `features` (4) → standalone (pandas/numpy); `costs` (5) → standalone; `baselines` (6) → standalone; `regime` (7) → standalone; `survival` (8) → `features`; `backtest` (9) → `costs`; `walkforward` (10) → `data`,`features`,`baselines`,`regime`,`survival`,`backtest`,`config`; `report` (11) → `backtest`,`config`,`regime`,`walkforward`; `run` (12) → `config`,`data`,`walkforward`,`report`. Every module is created before any module that imports it.

### Items worth flagging to the executor

All resolvable; three are deliberate, spec-sanctioned design choices rather than gaps:

1. **`build_folds` start index.** Task 10 presents the loop twice on purpose: the spec says "~4 folds" and "the first fold needs ≥2 training months", and the naive `range(1, ...)` start gives a 1-month first fold. Step 3a fixes the start to `i = 2`. On the real 7-month dataset this yields **5 folds** (test Jan, Feb, Mar, Apr, May 2026) — within the spec's "~4 folds" and giving every fold ≥2 training months. The executor must use the Step 3a version.
2. **Per-fold equity chaining for the pooled curve.** The walk-forward produces one `BacktestResult` per fold; each fold's equity curve starts fresh. `report.py`'s `_chain_equity` rebases each fold's curve to compound onto the previous fold's end, producing the single walk-forward equity curve the decision gate's max-drawdown is measured on. This is the honest construction — the gate is stated on a portfolio that compounds across folds — and is the deliberate choice over reporting four disjoint curves.
3. **Regime coverage — the walk-forward test folds genuinely span both regimes.** `regime.py` labels a month from the **true full-population graduation rate** (the fixed `TRUE_MONTHLY_GRADUATIONS` constant: 4880, 6007, 7677, 10790, 15175, 11646, 675 for Nov 2025 – May 2026; median 7677). A month strictly above the median is `mania`: so **Feb/Mar/Apr 2026 are `mania`, and Nov/Dec 2025, Jan/May 2026 are `quiet`**. The walk-forward harness (`build_folds` with the Step 3a `i = 2` start) tests on months Jan, Feb, Mar, Apr, May 2026 — that test span covers `quiet` (Jan, May) **and** `mania` (Feb, Mar, Apr). `report.py`'s per-regime breakdown is keyed on `fold.test_month`, so it shows **both `mania` and `quiet`**, and the decision gate's "Spans ≥2 distinct market regimes" input evaluates **`True`**. This is not a limitation: the closed historical window genuinely contains two regimes and the monthly expanding-window folds exercise both. (Note: the `historical_graduations` table is a month-stratified sample with flat per-month row counts — it must never be used to derive the regime rate; `regime.py` uses the true population counts instead, which is why the labelling is correct.)
