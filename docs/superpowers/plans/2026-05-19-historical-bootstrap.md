# Phase 2 Historical Dataset (Dune ETL) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-time Python ETL (`bootstrap/`) that pulls a historical pump.fun graduation dataset from the Dune Analytics API into a single self-contained SQLite table, `historical_graduations` — one flat, settled row per graduated token (graduation facts + history-native features + outcome label) — which the Phase 3 ML backtest will run on.

**Architecture:** A standalone Python package in a new top-level `bootstrap/` directory, decoupled from the Rust workspace. The ETL is split into PURE, TDD-tested modules — `config` (a `Config` dataclass + `load_config()`), `queries` (SQL builders per Dune stage), `transform` (Dune result rows → typed records), `sample` (month-stratified sampling), `load` (the `CREATE TABLE` DDL + an idempotent mint-keyed loader) — and one I/O module, `dune_client` (a `urllib`-based Dune API client with an *injectable transport* so it is unit-tested with a fake transport, never a live network call). `run.py` is the orchestrator CLI that wires the stages. The pipeline is **idempotent and resumable**: keyed on `mint`, every Dune stage caches its raw JSON results to `bootstrap/data/` (gitignored), and a re-run skips already-loaded mints and reuses cached stage results so a crash never re-spends Dune credits. Holder distribution is best-effort — small mint-batches, NULL on a Dune 2-minute timeout. A required **pilot run** validates the whole pipeline end-to-end against real Dune on a ~50–100-token sample for minimal credit spend before the full ~5,000-token run.

**Tech Stack:** Python 3.11 (standard library only for the ETL — `urllib`, `json`, `time`, `sqlite3`, `dataclasses`, `datetime`, `os`, `pathlib`, `hashlib`, `argparse`, `logging`); `pytest` for tests (the sole third-party dependency); the Dune Analytics REST API; SQLite (the project DB file `./storm.db`).

---

## Context

This is **Phase 2** of `solana-storm`. The project's critical path is **Phase 2 (assemble a historical dataset) → Phase 3 (backtest it)**. Phase 1 (`storm-collector`, the live daemon) is built, merged, and **parked** — it is not run or modified by this plan. No data is mined live.

The two source-of-truth documents this plan is built from:

- `docs/superpowers/specs/2026-05-18-historical-bootstrap-design.md` — the approved, revised design spec. §3 establishes the principle "features defined by the data"; §4.1 mandates one self-contained Python-owned SQLite table; §4.2 lists the history-native feature set; §4.3 fixes the scope (~5,000 graduations, PumpSwap era ~Nov 2025–present, stratified by month, exclude tokens that graduated < ~16 days ago).
- `docs/superpowers/specs/2026-05-18-historical-bootstrap-spike-findings.md` — the spike findings: the EXACT Dune table names, column names, working SQL query sketches, credit costs, and timeout risks. **Every SQL query in this plan comes directly from that document.** Its §3.6 makes the deployer "prior pump.fun launches" signal a first-class, obtainable feature.

The spike already resolved the go/no-go gate with a **GO**: Dune delivers the data within the free 2,500-credit budget (full run estimated ~341 credits).

### What this plan does NOT do

- **No Rust changes.** No new crate, no edit to any `crates/*` or `bins/*` file, no `Cargo.toml` change.
- **No `storm-store` migration.** `crates/storm-store/migrations/0002_survival.sql` is read **once**, in this plan, only to copy the repo's SQLite conventions (large `u64` values stored as `TEXT`; booleans as `INTEGER` 0/1; the outcome `survived` as `INTEGER`). It is never modified, and `historical_graduations` is **not** part of any `sqlx` migration — the Python ETL creates it itself with `CREATE TABLE IF NOT EXISTS`.
- **No live data collection.** Dune is a historical, one-time source. The parked live collector must never use Dune.

### Storage decision — one self-contained table

The historical dataset is a **single SQLite table, `historical_graduations`**, created and owned by the Python ETL. Unlike the live collector's three-table lifecycle (`graduations` → `feature_snapshots` → `outcomes`), every historical fact is already settled, so **one flat row per graduated token** — graduation facts, all features, and the outcome label together — is the natural shape and is exactly what a Phase 3 ML backtest wants. The table lives in the project SQLite database file. The repo's `DATABASE_URL` default is `sqlite://./storm.db`; the Python ETL uses the corresponding local file path `./storm.db` (configurable). The table is entirely Python-owned and decoupled from the live collector's tables, which are untouched and parked.

### The feature set (history-native, from spec §4.2 + spike findings §3)

The columns of `historical_graduations` are defined by what Dune actually provides — **not** borrowed from the live collector's `feature_snapshots` schema. Feature groups, with their Dune source and obtainability:

| Group | Columns | Dune source | Obtainable? |
|---|---|---|---|
| Graduation facts | `mint`, `pool_address`, `bonding_curve_address`, `lp_mint`, `graduation_time`, `graduation_slot`, `migrator_wallet` | `pump_call_migrate` | Yes — full |
| Outcome label | `survived`, `outcome_base_reserve`, `outcome_quote_reserve`, `outcome_checked_at` | `pump_amm_evt_buyevent` + `pump_amm_evt_sellevent` (last event in `[T0+12d, T0+16d]`) | Yes — full (no events ⇒ `survived=0`) |
| Liquidity at ~T0+12h | `liq_base_reserve`, `liq_quote_reserve`, `lp_burned`, `pool_supply_fraction` | `pump_amm_evt_buyevent`/`sellevent` (last event ≤ T0+12h); `lp_burned` heuristic; `pool_supply_fraction` NOT reliably supplied | Reserves yes; `lp_burned` heuristic; `pool_supply_fraction` **NULL** |
| Bonding-curve final state | `curve_real_sol_reserves`, `curve_real_token_reserves`, `curve_token_total_supply` | `pump_evt_tradeevent` (last trade before graduation slot) | Yes — full |
| Contract flags | `mint_authority_present`, `freeze_authority_present` | `spl_token_call_initializemint2` + `spl_token_call_setauthority` | Yes (`freeze_authority_present=0` constant for this cohort) |
| Holder distribution (best-effort) | `visible_holder_count`, `top10_concentration`, `top20_concentration` | `tokens_solana.spl_token_transfers` (balances ≤ T0+12h) | Best-effort — **NULL** on Dune timeout |
| Creator bag (not reliably supplied) | `creator_bag_fraction` | — | **NULL** |
| Deployer signal (FIRST-CLASS) | `deployer_wallet`, `deployer_prior_launches`, `deployer_age_secs` | `pump_call_create` + `pump_call_create_v2` self-join | Yes — `deployer_age_secs` is pump.fun-relative age, populated, not NULL |

**Critical:** the deployer signal is **first-class** (spec §4.2, findings §3.6). `deployer_wallet`, `deployer_prior_launches` (count of the wallet's prior pump.fun launches), and `deployer_age_secs` (the wallet's age on pump.fun at graduation, in seconds) are all populated from Dune — the deployer group is **not** left NULL. Only the columns Dune genuinely cannot supply are NULL-able: `pool_supply_fraction`, `creator_bag_fraction`, and the holder-distribution group (`visible_holder_count`, `top10_concentration`, `top20_concentration`) when a Dune query times out. NULL is the designed, ML-friendly fallback for missing features.

Note the live collector's `oldest_signature_age_secs` (full Solana wallet-history age) has no Dune equivalent and is **not** a column here — `deployer_age_secs` is the history-native, pump.fun-relative substitute, and is a real signal.

### SQLite column conventions (copied from `0002_survival.sql`)

- Large `u64` on-chain values (token/SOL reserves, total supply) — stored as `TEXT`, because SQLite's max integer is `i64` and these values can exceed it. This is exactly how `feature_snapshots` stores `base_reserve`, `quote_reserve`, `curve_real_sol_reserves`, etc.
- Booleans (`lp_burned`, `mint_authority_present`, `freeze_authority_present`) — `INTEGER` 0/1.
- The outcome `survived` — `INTEGER` 0/1.
- Timestamps — `INTEGER` Unix seconds.
- Counts that fit in `i64` (`visible_holder_count`, `deployer_prior_launches`, `graduation_slot`) — `INTEGER`.
- Fractions / concentrations (`pool_supply_fraction`, `creator_bag_fraction`, `top10_concentration`, `top20_concentration`) — `REAL`.
- Any feature Dune may not supply is declared **nullable** (no `NOT NULL`): `liq_base_reserve`, `liq_quote_reserve`, `pool_supply_fraction`, `creator_bag_fraction`, `visible_holder_count`, `top10_concentration`, `top20_concentration`. All other columns are `NOT NULL`. `mint` is `TEXT PRIMARY KEY` — the idempotency key.

### Scope (spec §4.3)

- **PumpSwap era** — the window `pump_call_migrate` covers: ~Nov 2025 to present (~62,700 graduations at spike time).
- **Target ~5,000 graduations** (configurable — `sample_size`), **stratified across the months** so every month (a distinct market regime) in the window is represented; Phase 3's validation requires ≥2 distinct regimes.
- **Exclude tokens that graduated < ~16 days ago** — their outcome is not yet settled. The cutoff is `now − outcome_settle_days` (default 16); enforced by the graduations-list query's date filter.

### Idempotency, resumability, and the on-disk cache

- **Mint-keyed idempotency.** `historical_graduations.mint` is the PRIMARY KEY. The loader uses `INSERT ... ON CONFLICT(mint) DO NOTHING`. Re-running the ETL never double-inserts and skips mints already loaded.
- **Stage result cache.** Every Dune stage writes its raw, parsed results to a JSON file under `bootstrap/data/` (gitignored). On a re-run, if a stage's cache file exists, the stage loads it from disk instead of re-executing the Dune query — so a crash mid-run never re-spends Dune credits. Cache filenames are deterministic per stage (and per batch index for batched stages). Holder batches that timed out are cached as an explicit `{"timed_out": true}` marker so they are not retried for free but are not mistaken for "no holders".

### Credit safety (spike findings §5)

- The Dune free engine has a hard **2-minute query timeout** — this caps any runaway query automatically.
- The ETL logs each query's `execution_cost_credits`, read from the `GET /api/v1/execution/{execution_id}/status` response, and prints a running total.
- The total free budget is **2,500 credits**; the full ~5,000-token run is estimated at **~341 credits**. Even at 3× the estimate the run uses ~1,000, leaving headroom.
- Holder distribution is the one high-timeout-risk stage (a single-mint query took 57 s in the spike). It is attempted in small mint-batches (pilot batch ~50 mints per findings §6); on a timeout the batch's holder columns are set NULL and the ETL continues.

### The Dune API — facts the client is built on

The Dune REST API flow for a self-authored ("private") SQL query:

1. **Create a query** — `POST /api/v1/query` with `{"name", "query_sql", "is_private": true}`; the response carries `query_id`. This plan creates one query and **patches** its SQL per stage.
2. **Update query SQL** — `PATCH /api/v1/query/{query_id}` with `{"query_sql": "..."}`.
3. **Execute** — `POST /api/v1/query/{query_id}/execute`; the response carries `execution_id`. This plan uses the **free** engine (default performance) so the 2-minute timeout applies.
4. **Poll status** — `GET /api/v1/execution/{execution_id}/status`; the response `state` cycles through `QUERY_STATE_PENDING` / `QUERY_STATE_EXECUTING` → a terminal `QUERY_STATE_COMPLETED`, `QUERY_STATE_FAILED`, or `QUERY_STATE_CANCELLED`. The status response also carries `execution_cost_credits` when completed and, on a timeout, a `FAILED` state with a timeout message.
5. **Fetch results** — `GET /api/v1/execution/{execution_id}/results`; the response has `result.rows` (a list of dicts keyed by SELECT column name).

Authentication: the header `X-Dune-API-Key: <DUNE_API_KEY>`. The key is read from the environment variable `DUNE_API_KEY` — already present in the repo's `.env` (this plan adds a `DUNE_API_KEY=` line to `.env.example`). Base URL: `https://api.dune.com`.

The client wraps these as `create_query`, `update_query_sql`, `execute_query`, `poll_until_done`, `get_results`, plus a convenience `run_sql(sql)` that does (create-once)→patch→execute→poll→fetch and returns `(rows, credits)` — or raises a `DuneTimeout` exception on a `FAILED`-with-timeout state. All HTTP goes through an **injectable transport** (a callable `transport(method, url, headers, body) -> (status_code, response_dict)`) so tests inject a fake transport and never touch the network; the default transport is a thin `urllib.request` wrapper.

### Pilot run vs full run

The spike's holder-batch timeout risk and the multi-stage pipeline are validated by a **pilot run** (Task 9): the entire ETL is run end-to-end against real Dune on a small sample (~50–100 graduations), which exercises every stage including the holder-batch timeout path for minimal credit spend. The **full ~5,000-token run** (Task 10) is a separate, later task that runs only after the pilot succeeds.

## Notes for the executor

- All commands below are run from the repo root `/home/roberto/solana-storm` unless an absolute path is given. The `bootstrap/` directory and everything in it is new.
- Python is at `/home/roberto/miniconda3/bin/python3` (Python 3.11). `pytest` is at `/home/roberto/miniconda3/bin/pytest`. Commands below use bare `python3` / `pytest`; if not on `PATH`, prefix with `/home/roberto/miniconda3/bin/`.
- `pytest` runs entirely offline. The ETL modules have **no third-party imports** except that `pytest` is the test runner. `dune_client.py` is unit-tested with a **fake transport** — `pytest` must never make a network call. The only live-Dune exercise is the pilot run (Task 9), which is a manual CLI invocation, not a `pytest` test.
- Run the test suite with `python3 -m pytest bootstrap/tests -q` from the repo root.
- This plan has **no Rust tasks** and runs none of the `cargo` CI gates. The repo CI only covers the Rust workspace; `bootstrap/` is independent. Each task still ends with a commit.
- Commit only at the end of each task, with the message shown. End every commit message with the `Co-Authored-By` line shown. Do **not** create a PR or push unless the user later asks.
- TDD discipline: for every code task, write the failing test first, run it and SEE it fail, then write the minimal implementation, run it and SEE it pass. Never write implementation before its failing test.
- The pure modules (`config`, `cache`, `queries`, `transform`, `sample`, `load`) get real unit tests with synthetic Dune-shaped inputs. `dune_client` is tested with a fake transport. `load` is tested against a temporary SQLite database file (via `tmp_path`). `run.py` (the orchestrator) and the live-Dune path are exercised by the pilot-run task, not by `pytest`.

## File structure

| Path | Change | Responsibility |
|---|---|---|
| `bootstrap/README.md` | Create | What the ETL is, prerequisites (`DUNE_API_KEY`), how to install deps, how to run the pilot and the full run, where output lands |
| `bootstrap/requirements.txt` | Create | The sole third-party dependency: `pytest` |
| `bootstrap/__init__.py` | Create | Marks `bootstrap` a package |
| `bootstrap/config.py` | Create | `Config` dataclass + `load_config()` — sample size, batch sizes, date window, settle-days, DB path, cache dir, Dune key/base URL |
| `bootstrap/cache.py` | Create | Tiny stage-result disk cache: deterministic JSON read/write under the cache dir |
| `bootstrap/dune_client.py` | Create | `DuneClient` — Dune REST API over `urllib` with an injectable transport; `DuneError` / `DuneTimeout` exceptions; `run_sql()` convenience |
| `bootstrap/queries.py` | Create | Pure SQL-builder functions, one per Dune stage, from the spike findings |
| `bootstrap/transform.py` | Create | Pure functions: raw Dune result rows → typed Python records (`GraduationRecord` + per-stage merge functions) |
| `bootstrap/sample.py` | Create | Pure month-stratified sampling of the graduations list |
| `bootstrap/load.py` | Create | The `historical_graduations` `CREATE TABLE IF NOT EXISTS` DDL + an idempotent, mint-keyed SQLite loader; existing-mint lookup for resumability |
| `bootstrap/run.py` | Create | The orchestrator CLI: wires config → graduations → sample → per-stage Dune queries (cached) → transform → load; `--pilot` flag; credit accounting |
| `bootstrap/tests/__init__.py` | Create | Marks the test package |
| `bootstrap/tests/test_scaffold.py` | Create | Smoke test: the package imports |
| `bootstrap/tests/test_config.py` | Create | Unit tests for `config.py` |
| `bootstrap/tests/test_cache.py` | Create | Unit tests for `cache.py` |
| `bootstrap/tests/test_dune_client.py` | Create | Unit tests for `dune_client.py` with a fake transport |
| `bootstrap/tests/test_queries.py` | Create | Unit tests for `queries.py` |
| `bootstrap/tests/test_transform.py` | Create | Unit tests for `transform.py` |
| `bootstrap/tests/test_sample.py` | Create | Unit tests for `sample.py` |
| `bootstrap/tests/test_load.py` | Create | Unit tests for `load.py` against a temp SQLite DB |
| `.gitignore` | Modify | Add `bootstrap/data/` (gitignored cache + output staging) |
| `.env.example` | Modify | Add a `DUNE_API_KEY=` line under a new "Dune Analytics" comment block |

`bootstrap/data/` is created at runtime by the ETL (the cache); it is gitignored and never committed. The live SQLite output table `historical_graduations` is written into `./storm.db` (the repo-root project DB file) — a local artifact, not source, and not committed.

---

### Task 1: Scaffold the `bootstrap/` package

**Files:**
- Create: `bootstrap/__init__.py`, `bootstrap/tests/__init__.py`, `bootstrap/requirements.txt`, `bootstrap/README.md`, `bootstrap/tests/test_scaffold.py`
- Modify: `.gitignore`, `.env.example`

This task creates the directory skeleton, the dependency file, the README, and the two repo-file edits, with one trivial test proving the package is importable. No ETL logic yet.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/tests/test_scaffold.py`:

```python
"""Smoke test: the bootstrap package and its test package are importable."""


def test_bootstrap_package_imports():
    import bootstrap

    assert bootstrap is not None


def test_requirements_file_exists():
    from pathlib import Path

    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    assert req.is_file(), "bootstrap/requirements.txt must exist"
    assert "pytest" in req.read_text()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest bootstrap/tests/test_scaffold.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap'` (the package files do not exist yet).

- [ ] **Step 3: Create the package files**

Create `bootstrap/__init__.py` (content is exactly one docstring line):

```python
"""solana-storm Phase 2: one-time Dune historical-graduation ETL."""
```

Create `bootstrap/tests/__init__.py`:

```python
"""Tests for the bootstrap ETL package."""
```

Create `bootstrap/requirements.txt`:

```
# Sole third-party dependency. The ETL itself uses only the Python standard
# library (urllib, json, sqlite3, ...); pytest is the test runner.
pytest>=8.0
```

Create `bootstrap/README.md`:

```markdown
# bootstrap — Phase 2 historical-graduation ETL

A one-time Python ETL that pulls a historical pump.fun graduation dataset from
the [Dune Analytics](https://dune.com) API into a single self-contained SQLite
table, `historical_graduations`, which the Phase 3 ML backtest runs on.

This package is **decoupled from the Rust workspace**: no crate, no `Cargo.toml`,
no `storm-store` migration. The table is created by this ETL itself.

## Prerequisites

- Python 3.11+.
- A Dune API key in the repo `.env` as `DUNE_API_KEY=...` (the Dune free plan is
  sufficient — the full run costs ~341 of the 2,500 free credits).

## Install

    python3 -m pip install -r bootstrap/requirements.txt

## Run the tests

    python3 -m pytest bootstrap/tests -q

The test suite is fully offline — the Dune client is exercised with a fake
transport.

## Run the ETL

A **pilot run** validates the whole pipeline end-to-end against real Dune on a
tiny sample before the full extraction:

    python3 -m bootstrap.run --pilot      # ~50-100 graduations, minimal credits

The **full run** assembles the ~5,000-token dataset:

    python3 -m bootstrap.run              # the full month-stratified sample

Both are idempotent and resumable: results are keyed on `mint`, every Dune stage
caches its raw output under `bootstrap/data/` (gitignored), and a re-run skips
already-loaded mints and reuses cached stage results — a crash never re-spends
Dune credits.

## Output

A `historical_graduations` table in the project SQLite database (`./storm.db` by
default — configurable). One flat row per graduated token: graduation facts, the
history-native feature set, and the settled `survived` outcome label.
```

Append to `.gitignore` (the file currently ends with `.claude/settings.local.json`):

```
bootstrap/data/
```

In `.env.example`, append a new block after the existing `JITO_BLOCK_ENGINE_URL` line:

```
# Dune Analytics — Phase 2 historical dataset ETL (bootstrap/)
# Free plan is sufficient. Used one-time by bootstrap/run.py; never by the
# live collector.
DUNE_API_KEY=
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest bootstrap/tests/test_scaffold.py -q`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/__init__.py bootstrap/tests/__init__.py bootstrap/requirements.txt bootstrap/README.md bootstrap/tests/test_scaffold.py .gitignore .env.example
git commit -m "Scaffold bootstrap/ package for the Phase 2 Dune ETL

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `config.py` — the `Config` dataclass and `load_config()`

**Files:**
- Create: `bootstrap/config.py`
- Test: `bootstrap/tests/test_config.py`

A frozen `Config` dataclass holds every tunable: sample size, batch sizes per stage, the date window, settle-days, the SQLite DB path, the cache directory, and the Dune key/base URL. `load_config()` builds a `Config`, reading `DUNE_API_KEY` from the environment and applying defaults for everything else, with an optional `pilot` override that shrinks the sample.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/tests/test_config.py`:

```python
"""Unit tests for bootstrap.config."""

import pytest

from bootstrap.config import Config, load_config


def test_load_config_reads_dune_key_from_env(monkeypatch):
    monkeypatch.setenv("DUNE_API_KEY", "test-key-123")
    cfg = load_config()
    assert cfg.dune_api_key == "test-key-123"
    assert cfg.dune_base_url == "https://api.dune.com"


def test_load_config_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DUNE_API_KEY"):
        load_config()


def test_defaults_match_the_spec(monkeypatch):
    monkeypatch.setenv("DUNE_API_KEY", "k")
    cfg = load_config()
    # spec 4.3: ~5,000 graduations, exclude < ~16 days old.
    assert cfg.sample_size == 5000
    assert cfg.outcome_settle_days == 16
    # findings 3.5/3.6: holder batches small, others larger.
    assert cfg.holder_batch_size == 50
    assert cfg.event_batch_size == 500
    assert cfg.flag_batch_size == 1000
    # the project DB file and the gitignored cache dir.
    assert cfg.db_path == "./storm.db"
    assert cfg.cache_dir == "bootstrap/data"
    # the PumpSwap-era window start (spec 4.3).
    assert cfg.window_start == "2025-11-01"
    assert cfg.is_pilot is False


def test_pilot_overrides_shrink_the_sample(monkeypatch):
    monkeypatch.setenv("DUNE_API_KEY", "k")
    cfg = load_config(pilot=True)
    assert cfg.sample_size == 75
    assert cfg.is_pilot is True
    # batch sizes still valid, just a tiny sample.
    assert cfg.holder_batch_size == 50


def test_config_is_frozen(monkeypatch):
    monkeypatch.setenv("DUNE_API_KEY", "k")
    cfg = load_config()
    with pytest.raises(Exception):
        cfg.sample_size = 1  # frozen dataclass -> FrozenInstanceError
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest bootstrap/tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap.config'`.

- [ ] **Step 3: Write `bootstrap/config.py`**

Create `bootstrap/config.py`:

```python
"""ETL configuration: a frozen Config dataclass and load_config()."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Every tunable for the Dune historical-graduation ETL.

    All fields have spec/findings-derived defaults; load_config() builds one.
    """

    # --- Dune API ---
    dune_api_key: str
    dune_base_url: str = "https://api.dune.com"

    # --- Sample (design spec 4.3) ---
    sample_size: int = 5000
    window_start: str = "2025-11-01"  # PumpSwap-era start, ISO date
    outcome_settle_days: int = 16  # exclude tokens younger than this

    # --- Batch sizes (spike findings 3.x / 5) ---
    event_batch_size: int = 500  # outcome / liquidity / bonding-curve queries
    flag_batch_size: int = 1000  # contract-flag and deployer queries
    holder_batch_size: int = 50  # holder distribution: small, timeout-prone

    # --- Outcome / snapshot timing (hours / days) ---
    liquidity_snapshot_hours: int = 12  # ~T0+12h liquidity snapshot
    outcome_window_lo_days: int = 12  # outcome event window low bound
    outcome_window_hi_days: int = 16  # outcome event window high bound

    # --- Storage ---
    db_path: str = "./storm.db"  # project SQLite file (DATABASE_URL local path)
    cache_dir: str = "bootstrap/data"  # gitignored stage-result cache

    # --- Run mode ---
    is_pilot: bool = False

    # --- Outcome rule (same threshold as the live collector) ---
    survival_min_quote_lamports: int = 5_000_000_000  # 5 SOL


def load_config(pilot: bool = False) -> Config:
    """Build a Config. Reads DUNE_API_KEY from the environment.

    Args:
        pilot: when True, shrink sample_size to a pilot-run size and mark
            is_pilot -- the rest of the pipeline behaves identically.

    Raises:
        ValueError: if DUNE_API_KEY is not set in the environment.
    """
    key = os.environ.get("DUNE_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "DUNE_API_KEY is not set. Add it to the repo .env "
            "(see .env.example)."
        )
    if pilot:
        return Config(dune_api_key=key, sample_size=75, is_pilot=True)
    return Config(dune_api_key=key)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest bootstrap/tests/test_config.py -q`
Expected: PASS — all five tests green.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/config.py bootstrap/tests/test_config.py
git commit -m "Add bootstrap config dataclass and load_config()

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `cache.py` — the stage-result disk cache

**Files:**
- Create: `bootstrap/cache.py`
- Test: `bootstrap/tests/test_cache.py`

A tiny helper that gives the ETL its resumability: each Dune stage writes its raw parsed result to a deterministically-named JSON file under the cache dir; on a re-run the stage reads it back instead of re-querying Dune. `cache_path()` builds the path, `write_cache()` stores a JSON-serialisable payload, `read_cache()` returns it or `None` if absent, `has_cache()` is the existence check.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/tests/test_cache.py`:

```python
"""Unit tests for bootstrap.cache."""

from bootstrap.cache import cache_path, has_cache, read_cache, write_cache


def test_round_trip_a_payload(tmp_path):
    payload = {"rows": [{"mint": "abc", "n": 1}], "credits": 2.0}
    write_cache(str(tmp_path), "graduations", payload)
    assert has_cache(str(tmp_path), "graduations") is True
    assert read_cache(str(tmp_path), "graduations") == payload


def test_missing_cache_reads_none(tmp_path):
    assert has_cache(str(tmp_path), "nope") is False
    assert read_cache(str(tmp_path), "nope") is None


def test_batch_index_makes_a_distinct_file(tmp_path):
    write_cache(str(tmp_path), "holders", {"v": 0}, batch=0)
    write_cache(str(tmp_path), "holders", {"v": 1}, batch=1)
    assert read_cache(str(tmp_path), "holders", batch=0) == {"v": 0}
    assert read_cache(str(tmp_path), "holders", batch=1) == {"v": 1}
    # no-batch and batch-0 are different files.
    assert has_cache(str(tmp_path), "holders") is False


def test_cache_path_is_deterministic(tmp_path):
    p1 = cache_path(str(tmp_path), "outcome", batch=3)
    p2 = cache_path(str(tmp_path), "outcome", batch=3)
    assert p1 == p2
    assert p1.endswith("outcome_batch003.json")


def test_write_creates_the_cache_dir(tmp_path):
    nested = tmp_path / "deep" / "data"
    write_cache(str(nested), "stage", {"ok": True})
    assert (nested / "stage.json").is_file()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest bootstrap/tests/test_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap.cache'`.

- [ ] **Step 3: Write `bootstrap/cache.py`**

Create `bootstrap/cache.py`:

```python
"""Stage-result disk cache -- gives the ETL crash-resumability.

Each Dune stage writes its raw parsed result to a deterministically-named JSON
file under the cache dir. On a re-run a stage reads the file instead of
re-querying Dune, so a crash never re-spends Dune credits.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional


def cache_path(cache_dir: str, stage: str, batch: Optional[int] = None) -> str:
    """Deterministic JSON path for a stage (optionally a batch within it)."""
    if batch is None:
        name = f"{stage}.json"
    else:
        name = f"{stage}_batch{batch:03d}.json"
    return os.path.join(cache_dir, name)


def has_cache(cache_dir: str, stage: str, batch: Optional[int] = None) -> bool:
    """True if a cache file for this stage/batch already exists."""
    return os.path.isfile(cache_path(cache_dir, stage, batch))


def write_cache(
    cache_dir: str,
    stage: str,
    payload: Any,
    batch: Optional[int] = None,
) -> None:
    """Write a JSON-serialisable payload to the stage's cache file.

    Creates the cache directory if it does not exist.
    """
    os.makedirs(cache_dir, exist_ok=True)
    path = cache_path(cache_dir, stage, batch)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def read_cache(
    cache_dir: str,
    stage: str,
    batch: Optional[int] = None,
) -> Optional[Any]:
    """Return the cached payload for a stage/batch, or None if absent."""
    path = cache_path(cache_dir, stage, batch)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest bootstrap/tests/test_cache.py -q`
Expected: PASS — all five tests green.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/cache.py bootstrap/tests/test_cache.py
git commit -m "Add bootstrap stage-result disk cache for resumability

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `dune_client.py` — the Dune API client

**Files:**
- Create: `bootstrap/dune_client.py`
- Test: `bootstrap/tests/test_dune_client.py`

`DuneClient` wraps the Dune REST flow (create → patch SQL → execute → poll → fetch results) over `urllib`, reading the API key from a `Config`. Every HTTP call goes through an **injectable transport** so the tests use a fake transport — `pytest` makes no network call. Exceptions: `DuneError` for an API error / a non-timeout `FAILED`; `DuneTimeout` (a `DuneError` subclass) for a `FAILED` state whose message indicates a free-engine timeout. `run_sql()` is the convenience method: (create-once)→patch SQL→execute→poll→fetch, returning `(rows, credits)`.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/tests/test_dune_client.py`:

```python
"""Unit tests for bootstrap.dune_client, using a fake transport.

The fake transport is a callable with the same signature as the real one:
    transport(method, url, headers, body) -> (status_code, response_dict)
It is scripted with a queue of canned responses so no network call happens.
"""

import pytest

from bootstrap.config import Config
from bootstrap.dune_client import DuneClient, DuneError, DuneTimeout


def make_config():
    return Config(dune_api_key="test-key")


class FakeTransport:
    """A scripted transport: returns queued responses, records every call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if not self._responses:
            raise AssertionError(f"unexpected extra call: {method} {url}")
        return self._responses.pop(0)


def test_run_sql_happy_path_returns_rows_and_credits():
    rows = [{"mint": "M1", "x": 1}, {"mint": "M2", "x": 2}]
    transport = FakeTransport(
        [
            (200, {"query_id": 42}),  # create_query
            (200, {}),  # update_query_sql (PATCH)
            (200, {"execution_id": "EX1"}),  # execute_query
            (200, {"state": "QUERY_STATE_EXECUTING"}),  # poll #1
            (
                200,
                {"state": "QUERY_STATE_COMPLETED",
                 "execution_cost_credits": 3.5},
            ),  # poll #2 -> done
            (200, {"result": {"rows": rows}}),  # get_results
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    got_rows, credits = client.run_sql("SELECT 1")
    assert got_rows == rows
    assert credits == 3.5
    # the API key rode on every request header.
    for _method, _url, headers, _body in transport.calls:
        assert headers["X-Dune-API-Key"] == "test-key"


def test_run_sql_reuses_an_already_created_query():
    """A second run_sql does not re-create the query -- only PATCH/execute."""
    rows = [{"mint": "M3"}]
    transport = FakeTransport(
        [
            (200, {"query_id": 7}),  # create (first call only)
            (200, {}),  # patch #1
            (200, {"execution_id": "E1"}),  # execute #1
            (200, {"state": "QUERY_STATE_COMPLETED",
                   "execution_cost_credits": 1.0}),
            (200, {"result": {"rows": rows}}),
            (200, {}),  # patch #2 (no second create)
            (200, {"execution_id": "E2"}),  # execute #2
            (200, {"state": "QUERY_STATE_COMPLETED",
                   "execution_cost_credits": 1.0}),
            (200, {"result": {"rows": rows}}),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    client.run_sql("SELECT 1")
    client.run_sql("SELECT 2")
    creates = [
        c for c in transport.calls
        if c[0] == "POST" and c[1].endswith("/query")
    ]
    assert len(creates) == 1, "query should be created exactly once"


def test_timeout_state_raises_dune_timeout():
    transport = FakeTransport(
        [
            (200, {"query_id": 1}),
            (200, {}),
            (200, {"execution_id": "EX"}),
            (
                200,
                {
                    "state": "QUERY_STATE_FAILED",
                    "error": {"message": "Query exceeded maximum execution "
                                         "time of 120 seconds"},
                },
            ),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    with pytest.raises(DuneTimeout):
        client.run_sql("SELECT slow")


def test_non_timeout_failure_raises_dune_error():
    transport = FakeTransport(
        [
            (200, {"query_id": 1}),
            (200, {}),
            (200, {"execution_id": "EX"}),
            (
                200,
                {"state": "QUERY_STATE_FAILED",
                 "error": {"message": "syntax error near SELCT"}},
            ),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    with pytest.raises(DuneError) as excinfo:
        client.run_sql("SELCT bad")
    assert not isinstance(excinfo.value, DuneTimeout)


def test_http_error_status_raises_dune_error():
    transport = FakeTransport([(401, {"error": "invalid API key"})])
    client = DuneClient(make_config(), transport=transport)
    with pytest.raises(DuneError, match="401"):
        client.create_query("q", "SELECT 1")


def test_poll_treats_pending_then_completed_as_done():
    transport = FakeTransport(
        [
            (200, {"state": "QUERY_STATE_PENDING"}),
            (200, {"state": "QUERY_STATE_PENDING"}),
            (200, {"state": "QUERY_STATE_COMPLETED",
                   "execution_cost_credits": 0.0}),
        ]
    )
    client = DuneClient(make_config(), transport=transport)
    status = client.poll_until_done("EX", poll_interval=0)
    assert status["state"] == "QUERY_STATE_COMPLETED"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest bootstrap/tests/test_dune_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap.dune_client'`.

- [ ] **Step 3: Write `bootstrap/dune_client.py`**

Create `bootstrap/dune_client.py`:

```python
"""Dune Analytics REST API client.

Wraps the create -> patch SQL -> execute -> poll -> fetch-results flow over
urllib. Every HTTP call goes through an injectable `transport` callable so
tests use a fake transport and never touch the network. The free Dune engine
is used (default performance), so its 2-minute timeout applies and caps any
runaway query.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Tuple

from bootstrap.config import Config

# transport(method, url, headers, body_dict_or_None) -> (status_code, resp_dict)
Transport = Callable[[str, str, dict, Optional[dict]], Tuple[int, dict]]

_TERMINAL_OK = "QUERY_STATE_COMPLETED"
_TERMINAL_BAD = ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED")


class DuneError(RuntimeError):
    """A Dune API error: a non-2xx HTTP status or a FAILED/CANCELLED run."""


class DuneTimeout(DuneError):
    """A FAILED execution whose message indicates a free-engine timeout."""


def _urllib_transport(
    method: str, url: str, headers: dict, body: Optional[dict]
) -> Tuple[int, dict]:
    """Default transport: a thin urllib.request wrapper."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"error": raw}
        return exc.code, payload


def _looks_like_timeout(message: str) -> bool:
    """Heuristic: does a FAILED message indicate the free-engine timeout?"""
    low = message.lower()
    return "execution time" in low or "timeout" in low or "timed out" in low


class DuneClient:
    """A minimal Dune API client built on an injectable transport."""

    def __init__(self, config: Config, transport: Optional[Transport] = None):
        self._config = config
        self._transport: Transport = transport or _urllib_transport
        self._query_id: Optional[int] = None  # lazily created, then reused

    # --- low-level request ---------------------------------------------------

    def _headers(self) -> dict:
        return {
            "X-Dune-API-Key": self._config.dune_api_key,
            "Content-Type": "application/json",
        }

    def _request(
        self, method: str, path: str, body: Optional[dict] = None
    ) -> dict:
        url = f"{self._config.dune_base_url}{path}"
        status, payload = self._transport(method, url, self._headers(), body)
        if status < 200 or status >= 300:
            raise DuneError(
                f"Dune API {method} {path} -> HTTP {status}: {payload}"
            )
        return payload

    # --- API surface ---------------------------------------------------------

    def create_query(self, name: str, sql: str) -> int:
        """Create a private Dune query; return its query_id."""
        payload = self._request(
            "POST",
            "/api/v1/query",
            {"name": name, "query_sql": sql, "is_private": True},
        )
        query_id = payload.get("query_id")
        if query_id is None:
            raise DuneError(f"create_query: no query_id in response {payload}")
        return int(query_id)

    def update_query_sql(self, query_id: int, sql: str) -> None:
        """Replace a query's SQL (PATCH)."""
        self._request(
            "PATCH", f"/api/v1/query/{query_id}", {"query_sql": sql}
        )

    def execute_query(self, query_id: int) -> str:
        """Execute a query on the free engine; return the execution_id."""
        payload = self._request(
            "POST", f"/api/v1/query/{query_id}/execute", {}
        )
        execution_id = payload.get("execution_id")
        if execution_id is None:
            raise DuneError(f"execute_query: no execution_id in {payload}")
        return str(execution_id)

    def poll_until_done(
        self, execution_id: str, poll_interval: float = 3.0
    ) -> dict:
        """Poll execution status until a terminal state.

        Returns the final status dict on success. Raises DuneTimeout on a
        timeout-flavoured FAILED, or DuneError on any other FAILED/CANCELLED.
        """
        while True:
            status = self._request(
                "GET", f"/api/v1/execution/{execution_id}/status"
            )
            state = status.get("state")
            if state == _TERMINAL_OK:
                return status
            if state in _TERMINAL_BAD:
                message = ""
                err = status.get("error")
                if isinstance(err, dict):
                    message = str(err.get("message", ""))
                elif err is not None:
                    message = str(err)
                if state == "QUERY_STATE_FAILED" and _looks_like_timeout(
                    message
                ):
                    raise DuneTimeout(
                        f"execution {execution_id} timed out: {message}"
                    )
                raise DuneError(
                    f"execution {execution_id} {state}: {message}"
                )
            if poll_interval:
                time.sleep(poll_interval)

    def get_results(self, execution_id: str) -> List[dict]:
        """Fetch a completed execution's result rows."""
        payload = self._request(
            "GET", f"/api/v1/execution/{execution_id}/results"
        )
        result = payload.get("result") or {}
        return list(result.get("rows", []))

    def run_sql(self, sql: str) -> Tuple[List[dict], float]:
        """Run SQL end-to-end: (create once) -> patch -> execute -> poll ->
        fetch. Returns (rows, credits_spent).

        Raises DuneTimeout on a free-engine timeout (the caller decides
        whether that is fatal or a NULL-fallback).
        """
        if self._query_id is None:
            self._query_id = self.create_query(
                "solana-storm historical bootstrap ETL", sql
            )
        else:
            self.update_query_sql(self._query_id, sql)
        execution_id = self.execute_query(self._query_id)
        status = self.poll_until_done(execution_id)
        credits = float(status.get("execution_cost_credits", 0.0) or 0.0)
        rows = self.get_results(execution_id)
        return rows, credits
```

Note on `test_run_sql_reuses_an_already_created_query`: the first `run_sql` has no `_query_id`, so it calls `create_query` (a `POST /api/v1/query`) then `execute_query` (a `POST .../execute`). The second `run_sql` has the id cached, so it calls `update_query_sql` (a `PATCH`) — no second create. The test asserts exactly one `POST` whose path *ends with* `/query` (the create), distinguishing it from the `/execute` POSTs.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest bootstrap/tests/test_dune_client.py -q`
Expected: PASS — all six tests green.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/dune_client.py bootstrap/tests/test_dune_client.py
git commit -m "Add Dune API client with injectable transport

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `queries.py` — the per-stage SQL builders

**Files:**
- Create: `bootstrap/queries.py`
- Test: `bootstrap/tests/test_queries.py`

Pure functions, one per Dune stage, that build the SQL string for that stage. The SQL bodies come directly from the spike findings. Stages: `graduations_sql` (the date-filtered graduations list), `outcome_sql`, `liquidity_sql`, `bonding_curve_sql`, `contract_flags_sql`, `deployer_sql`, `holders_sql`. The batched stages take a list of mints/pools and embed them as a quoted SQL `IN (...)` list via a shared `_sql_in_list` helper. These functions are pure (input in → string out) and trivially testable.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/tests/test_queries.py`:

```python
"""Unit tests for bootstrap.queries -- pure SQL string builders."""

import pytest

from bootstrap import queries


def test_graduations_sql_filters_window_and_settle_cutoff():
    sql = queries.graduations_sql(
        window_start="2025-11-01", settle_cutoff="2026-05-03"
    )
    low = sql.lower()
    assert "pumpdotfun_solana.pump_call_migrate" in low
    assert "account_mint" in low
    assert "account_pool" in low
    assert "account_bonding_curve" in low
    assert "account_lp_mint" in low
    assert "account_user" in low  # the migrator wallet
    assert "call_block_time" in low
    assert "call_block_slot" in low
    # both date bounds embedded.
    assert "2025-11-01" in sql
    assert "2026-05-03" in sql


def test_outcome_sql_embeds_pools_and_the_event_tables():
    sql = queries.outcome_sql(["POOL_A", "POOL_B"])
    low = sql.lower()
    assert "pump_amm_evt_buyevent" in low
    assert "pump_amm_evt_sellevent" in low
    assert "pool_quote_token_reserves" in low
    assert "pool_base_token_reserves" in low
    assert "union all" in low
    # both pools quoted into the IN list.
    assert "'POOL_A'" in sql and "'POOL_B'" in sql


def test_liquidity_sql_targets_pools_and_the_event_tables():
    sql = queries.liquidity_sql(["P1"])
    low = sql.lower()
    assert "pump_amm_evt_buyevent" in low
    assert "pump_amm_evt_sellevent" in low
    assert "'P1'" in sql


def test_bonding_curve_sql_uses_tradeevent_and_mints():
    sql = queries.bonding_curve_sql(["MINT1", "MINT2"])
    low = sql.lower()
    assert "pumpdotfun_solana.pump_evt_tradeevent" in low
    assert "real_sol_reserves" in low
    assert "real_token_reserves" in low
    assert "virtual_token_reserves" in low
    assert "evt_block_slot" in low
    assert "'MINT1'" in sql and "'MINT2'" in sql


def test_contract_flags_sql_joins_initializemint2_and_setauthority():
    sql = queries.contract_flags_sql(["MINTX"])
    low = sql.lower()
    assert "spl_token_call_initializemint2" in low
    assert "spl_token_call_setauthority" in low
    assert "minttokens" in low  # the authority type checked
    assert "mint_authority_present" in low
    assert "'MINTX'" in sql


def test_deployer_sql_self_joins_create_and_create_v2():
    sql = queries.deployer_sql(["MINTD"], max_grad_time="2026-05-03")
    low = sql.lower()
    assert "pump_call_create" in low
    # create_v2 also covered (findings caveat 8).
    assert "pump_call_create_v2" in low
    assert "account_user" in low
    assert "count(*)" in low
    assert "min(call_block_time)" in low
    assert "'MINTD'" in sql
    assert "2026-05-03" in sql


def test_holders_sql_targets_spl_token_transfers_at_a_snapshot():
    sql = queries.holders_sql(["MINTH"], snapshot_time="2026-01-01 12:00:00")
    low = sql.lower()
    assert "tokens_solana.spl_token_transfers" in low
    assert "from_owner" in low
    assert "to_owner" in low
    assert "token_mint_address" in low
    assert "row_number()" in low  # the top-N ranking
    assert "'MINTH'" in sql
    assert "2026-01-01 12:00:00" in sql


def test_in_list_quotes_and_comma_joins():
    assert queries._sql_in_list(["a", "b", "c"]) == "'a', 'b', 'c'"


def test_in_list_rejects_a_value_with_a_quote():
    # defence against a malformed address breaking the SQL string.
    with pytest.raises(ValueError):
        queries._sql_in_list(["ok", "ev'il"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest bootstrap/tests/test_queries.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap.queries'`.

- [ ] **Step 3: Write `bootstrap/queries.py`**

Create `bootstrap/queries.py`:

```python
"""Per-stage Dune SQL builders.

Each function returns the SQL string for one ETL stage. The SQL bodies come
directly from the spike findings (2026-05-18-historical-bootstrap-spike-
findings.md). Batched stages embed a mint/pool list as a quoted SQL IN(...)
list. Solana base58 addresses contain no quote characters; _sql_in_list
rejects any value that does, as defence against a malformed address breaking
the SQL string.
"""

from __future__ import annotations

from typing import Iterable


def _sql_in_list(values: Iterable[str]) -> str:
    """Render values as a quoted, comma-joined SQL IN-list body.

    Raises ValueError if any value contains a single quote.
    """
    out = []
    for value in values:
        text = str(value)
        if "'" in text:
            raise ValueError(f"value contains a quote, refusing: {text!r}")
        out.append(f"'{text}'")
    return ", ".join(out)


def graduations_sql(window_start: str, settle_cutoff: str) -> str:
    """The graduations list: PumpSwap-era migrations whose outcome is settled.

    window_start  -- ISO date, the PumpSwap-era start (e.g. 2025-11-01).
    settle_cutoff -- ISO date, now - outcome_settle_days; tokens that
                     graduated on/after this are excluded (outcome not settled).
    """
    return f"""
SELECT
    account_mint              AS mint,
    account_pool              AS pool_address,
    account_bonding_curve     AS bonding_curve_address,
    account_lp_mint           AS lp_mint,
    account_user              AS migrator_wallet,
    call_block_time           AS graduation_time,
    call_block_slot           AS graduation_slot
FROM pumpdotfun_solana.pump_call_migrate
WHERE call_block_time >= TIMESTAMP '{window_start}'
  AND call_block_time <  TIMESTAMP '{settle_cutoff}'
ORDER BY call_block_time
""".strip()


def outcome_sql(pools: Iterable[str]) -> str:
    """Outcome label: the last pool reserves observed for each pool.

    Unions buy/sell events for the pool batch and keeps the latest event per
    pool. The graduations-list query already excludes tokens younger than the
    settle window, so the latest event is effectively the post-horizon
    (settled) state -- see the plan's self-review for the timing rationale.
    """
    pool_list = _sql_in_list(pools)
    return f"""
WITH events AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_buyevent
    WHERE pool IN ({pool_list})
    UNION ALL
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_sellevent
    WHERE pool IN ({pool_list})
),
ranked AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time,
           ROW_NUMBER() OVER (
               PARTITION BY pool ORDER BY evt_block_time DESC
           ) AS rn
    FROM events
)
SELECT pool                       AS pool_address,
       pool_base_token_reserves   AS outcome_base_reserve,
       pool_quote_token_reserves  AS outcome_quote_reserve,
       evt_block_time             AS outcome_event_time
FROM ranked
WHERE rn = 1
""".strip()


def liquidity_sql(pools: Iterable[str]) -> str:
    """Liquidity at ~T0+12h: the last pool reserves for each pool in the batch.

    Same buy/sell-event tables as the outcome query. snapshot timing is
    approximate (spec 5); the merge step keeps the latest event per pool.
    """
    pool_list = _sql_in_list(pools)
    return f"""
WITH events AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_buyevent
    WHERE pool IN ({pool_list})
    UNION ALL
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time
    FROM pumpdotfun_solana.pump_amm_evt_sellevent
    WHERE pool IN ({pool_list})
),
ranked AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time,
           ROW_NUMBER() OVER (
               PARTITION BY pool ORDER BY evt_block_time DESC
           ) AS rn
    FROM events
)
SELECT pool                       AS pool_address,
       pool_base_token_reserves   AS liq_base_reserve,
       pool_quote_token_reserves  AS liq_quote_reserve,
       evt_block_time             AS liq_event_time
FROM ranked
WHERE rn = 1
""".strip()


def bonding_curve_sql(mints: Iterable[str]) -> str:
    """Bonding-curve final state: all trade events for the mint batch.

    The merge step keeps, per mint, the last row whose evt_block_slot precedes
    the migration slot (findings caveat 4 -- slot, not timestamp, avoids the
    same-tx off-by-one).
    """
    mint_list = _sql_in_list(mints)
    return f"""
SELECT mint,
       real_sol_reserves,
       real_token_reserves,
       virtual_token_reserves,
       evt_block_slot
FROM pumpdotfun_solana.pump_evt_tradeevent
WHERE mint IN ({mint_list})
""".strip()


def contract_flags_sql(mints: Iterable[str]) -> str:
    """Contract flags: whether mint authority was revoked by graduation.

    A setauthority row with authorityType 'MintTokens' and a null newAuthority
    means the mint authority was revoked. freeze_authority_present is a
    constant 0 for the pump.fun cohort (findings 3.4) and is set by transform.
    """
    mint_list = _sql_in_list(mints)
    return f"""
WITH minted AS (
    SELECT account_mint AS mint
    FROM spl_token_solana.spl_token_call_initializemint2
    WHERE account_mint IN ({mint_list})
),
revokes AS (
    SELECT DISTINCT account_mint AS mint
    FROM spl_token_solana.spl_token_call_setauthority
    WHERE account_mint IN ({mint_list})
      AND authorityType LIKE '%MintTokens%'
      AND newAuthority IS NULL
)
SELECT minted.mint AS mint,
       CASE WHEN revokes.mint IS NULL THEN 1 ELSE 0 END
           AS mint_authority_present
FROM minted
LEFT JOIN revokes ON revokes.mint = minted.mint
""".strip()


def deployer_sql(mints: Iterable[str], max_grad_time: str) -> str:
    """Deployer signal (first-class): prior pump.fun launches and wallet age.

    Unions pump_call_create and pump_call_create_v2 (findings caveat 8), then
    self-joins each target token's creator to that creator's full history --
    count of prior creates and earliest create time.
    """
    mint_list = _sql_in_list(mints)
    return f"""
WITH creates AS (
    SELECT account_mint, account_user, call_block_time
    FROM pumpdotfun_solana.pump_call_create
    UNION ALL
    SELECT account_mint, account_user, call_block_time
    FROM pumpdotfun_solana.pump_call_create_v2
),
target AS (
    SELECT account_mint, account_user, call_block_time
    FROM creates
    WHERE account_mint IN ({mint_list})
),
history AS (
    SELECT account_user,
           COUNT(*)              AS total_creates,
           MIN(call_block_time)  AS first_create
    FROM creates
    WHERE call_block_time < TIMESTAMP '{max_grad_time}'
    GROUP BY account_user
)
SELECT target.account_mint AS mint,
       target.account_user AS deployer_wallet,
       history.total_creates AS deployer_prior_launches,
       CAST(
           date_diff('second', history.first_create,
                     target.call_block_time) AS BIGINT
       ) AS deployer_age_secs
FROM target
JOIN history ON history.account_user = target.account_user
""".strip()


def holders_sql(mints: Iterable[str], snapshot_time: str) -> str:
    """Holder distribution (best-effort): holder count + top-10/20 share.

    Reconstructs balances from spl_token_transfers up to a single snapshot
    time. This is the high-timeout-risk stage; run.py batches it small and
    NULLs the columns on a DuneTimeout.
    """
    mint_list = _sql_in_list(mints)
    return f"""
WITH transfers AS (
    SELECT token_mint_address, to_owner AS owner,
           CAST(amount AS DOUBLE) AS amt
    FROM tokens_solana.spl_token_transfers
    WHERE token_mint_address IN ({mint_list})
      AND block_time <= TIMESTAMP '{snapshot_time}'
      AND action = 'transfer'
    UNION ALL
    SELECT token_mint_address, from_owner AS owner,
           -CAST(amount AS DOUBLE) AS amt
    FROM tokens_solana.spl_token_transfers
    WHERE token_mint_address IN ({mint_list})
      AND block_time <= TIMESTAMP '{snapshot_time}'
      AND action = 'transfer'
),
balances AS (
    SELECT token_mint_address, owner, SUM(amt) AS balance
    FROM transfers
    GROUP BY token_mint_address, owner
    HAVING SUM(amt) > 0
),
ranked AS (
    SELECT token_mint_address, balance,
           ROW_NUMBER() OVER (
               PARTITION BY token_mint_address ORDER BY balance DESC
           ) AS rnk
    FROM balances
),
stats AS (
    SELECT token_mint_address,
           COUNT(*)                                        AS holder_count,
           SUM(balance)                                    AS total_supply,
           SUM(CASE WHEN rnk <= 10 THEN balance ELSE 0 END) AS top10_bal,
           SUM(CASE WHEN rnk <= 20 THEN balance ELSE 0 END) AS top20_bal
    FROM ranked
    GROUP BY token_mint_address
)
SELECT token_mint_address                AS mint,
       holder_count                      AS visible_holder_count,
       top10_bal / total_supply          AS top10_concentration,
       top20_bal / total_supply          AS top20_concentration
FROM stats
""".strip()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest bootstrap/tests/test_queries.py -q`
Expected: PASS — all nine tests green.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/queries.py bootstrap/tests/test_queries.py
git commit -m "Add per-stage Dune SQL builders from the spike findings

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `transform.py` — Dune rows to typed records

**Files:**
- Create: `bootstrap/transform.py`
- Test: `bootstrap/tests/test_transform.py`

Pure functions that turn raw Dune result rows (lists of dicts) into typed Python records. The central type is `GraduationRecord` — a dataclass mirroring the `historical_graduations` columns, with NULL-able feature fields defaulting to `None`. `parse_graduations` builds the seed records from the graduations-list rows. Per-stage `merge_*` functions fold a stage's rows into an existing `{mint: GraduationRecord}` map. Time strings are parsed to Unix seconds; large `u64` reserves are kept as strings (per the SQLite convention); the outcome `survived` is derived with the 5-SOL rule; `freeze_authority_present` is the constant 0 for this cohort; `lp_burned` defaults to 1 (the findings heuristic) and is cleared only when `merge_liquidity` is told the pool had a withdraw event. `merge_bonding_curve` picks the last trade *by slot* before the migration slot. All functions are pure and unit-tested with synthetic Dune-shaped inputs.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/tests/test_transform.py`:

```python
"""Unit tests for bootstrap.transform -- pure Dune-row -> record functions."""

from bootstrap import transform
from bootstrap.transform import GraduationRecord


def grad_rows():
    """Two synthetic graduations-list rows, Dune-shaped."""
    return [
        {
            "mint": "MINT_A",
            "pool_address": "POOL_A",
            "bonding_curve_address": "BC_A",
            "lp_mint": "LP_A",
            "migrator_wallet": "MIG_A",
            "graduation_time": "2026-01-10 08:00:00.000 UTC",
            "graduation_slot": 312000000,
        },
        {
            "mint": "MINT_B",
            "pool_address": "POOL_B",
            "bonding_curve_address": "BC_B",
            "lp_mint": "LP_B",
            "migrator_wallet": "MIG_B",
            "graduation_time": "2026-02-01 12:30:00.000 UTC",
            "graduation_slot": 318000000,
        },
    ]


def test_parse_graduations_builds_seed_records():
    recs = transform.parse_graduations(grad_rows())
    assert set(recs.keys()) == {"MINT_A", "MINT_B"}
    a = recs["MINT_A"]
    assert isinstance(a, GraduationRecord)
    assert a.pool_address == "POOL_A"
    assert a.bonding_curve_address == "BC_A"
    assert a.lp_mint == "LP_A"
    assert a.migrator_wallet == "MIG_A"
    assert a.graduation_slot == 312000000
    # graduation_time parsed to Unix seconds (int).
    assert isinstance(a.graduation_time, int) and a.graduation_time > 0
    # all feature fields start as None / unset.
    assert a.survived is None
    assert a.liq_base_reserve is None
    assert a.visible_holder_count is None
    assert a.deployer_prior_launches is None
    # freeze authority is the cohort constant 0.
    assert a.freeze_authority_present == 0
    # lp_burned defaults to the heuristic 1.
    assert a.lp_burned == 1
    # pool_supply_fraction / creator_bag_fraction are never supplied -> None.
    assert a.pool_supply_fraction is None
    assert a.creator_bag_fraction is None


def test_parse_time_handles_dune_timestamp_formats():
    secs = transform.parse_dune_time("2026-01-10 08:00:00.000 UTC")
    assert secs == 1768032000
    # the bare-ISO variant some Dune columns return.
    assert transform.parse_dune_time("2026-01-10T08:00:00Z") == 1768032000


def test_merge_outcome_sets_survived_with_the_5_sol_rule():
    recs = transform.parse_graduations(grad_rows())
    # POOL_A has > 5 SOL quote; POOL_B is drained.
    out_rows = [
        {
            "pool_address": "POOL_A",
            "outcome_base_reserve": "120000000000000",
            "outcome_quote_reserve": "92000000000",
            "outcome_event_time": "2026-01-25 08:00:00.000 UTC",
        },
        {
            "pool_address": "POOL_B",
            "outcome_base_reserve": "999",
            "outcome_quote_reserve": "10000000",
            "outcome_event_time": "2026-02-16 12:30:00.000 UTC",
        },
    ]
    transform.merge_outcome(recs, out_rows,
                            survival_min_quote_lamports=5_000_000_000)
    assert recs["MINT_A"].survived == 1
    assert recs["MINT_A"].outcome_quote_reserve == "92000000000"
    assert recs["MINT_B"].survived == 0


def test_merge_outcome_missing_pool_is_rugged_with_zero_reserves():
    """Findings 3.1: no event row -> abandoned -> survived = 0, reserves 0."""
    recs = transform.parse_graduations(grad_rows())
    transform.merge_outcome(recs, [], survival_min_quote_lamports=5_000_000_000)
    for mint in ("MINT_A", "MINT_B"):
        assert recs[mint].survived == 0
        assert recs[mint].outcome_quote_reserve == "0"
        assert recs[mint].outcome_base_reserve == "0"


def test_merge_liquidity_sets_reserves_and_keeps_string_u64():
    recs = transform.parse_graduations(grad_rows())
    liq_rows = [
        {
            "pool_address": "POOL_A",
            "liq_base_reserve": "1073000000000000",
            "liq_quote_reserve": "64000000000",
            "liq_event_time": "2026-01-10 20:00:00.000 UTC",
        }
    ]
    transform.merge_liquidity(recs, liq_rows, withdrawn_pools=set())
    a = recs["MINT_A"]
    assert a.liq_base_reserve == "1073000000000000"  # kept as str
    assert a.liq_quote_reserve == "64000000000"
    assert a.lp_burned == 1  # not in withdrawn_pools -> stays burned


def test_merge_liquidity_clears_lp_burned_for_a_withdrawn_pool():
    recs = transform.parse_graduations(grad_rows())
    transform.merge_liquidity(recs, [], withdrawn_pools={"POOL_A"})
    assert recs["MINT_A"].lp_burned == 0
    assert recs["MINT_B"].lp_burned == 1


def test_merge_bonding_curve_picks_last_trade_before_migration_slot():
    recs = transform.parse_graduations(grad_rows())
    # MINT_A migrated at slot 312000000. Three trades; one is AT the
    # migration slot and must be ignored (findings caveat 4).
    bc_rows = [
        {
            "mint": "MINT_A",
            "real_sol_reserves": "70000000000",
            "real_token_reserves": "5000000",
            "virtual_token_reserves": "100000000",
            "evt_block_slot": 311999990,
        },
        {
            "mint": "MINT_A",
            "real_sol_reserves": "85005359500",
            "real_token_reserves": "0",
            "virtual_token_reserves": "0",
            "evt_block_slot": 311999999,
        },
        {
            "mint": "MINT_A",
            "real_sol_reserves": "999",
            "real_token_reserves": "999",
            "virtual_token_reserves": "999",
            "evt_block_slot": 312000000,  # the migration slot itself -> skip
        },
    ]
    transform.merge_bonding_curve(recs, bc_rows)
    a = recs["MINT_A"]
    assert a.curve_real_sol_reserves == "85005359500"  # the slot-99 trade
    assert a.curve_real_token_reserves == "0"
    # total supply = virtual + real token reserves at that final trade.
    assert a.curve_token_total_supply == "0"


def test_merge_contract_flags_sets_mint_authority_present():
    recs = transform.parse_graduations(grad_rows())
    flag_rows = [
        {"mint": "MINT_A", "mint_authority_present": 0},
        {"mint": "MINT_B", "mint_authority_present": 1},
    ]
    transform.merge_contract_flags(recs, flag_rows)
    assert recs["MINT_A"].mint_authority_present == 0
    assert recs["MINT_B"].mint_authority_present == 1
    # freeze authority stays the cohort constant 0.
    assert recs["MINT_A"].freeze_authority_present == 0


def test_merge_deployer_populates_the_first_class_signal():
    recs = transform.parse_graduations(grad_rows())
    dep_rows = [
        {
            "mint": "MINT_A",
            "deployer_wallet": "DEP_A",
            "deployer_prior_launches": 443,
            "deployer_age_secs": 691200,
        }
    ]
    transform.merge_deployer(recs, dep_rows)
    a = recs["MINT_A"]
    assert a.deployer_wallet == "DEP_A"
    assert a.deployer_prior_launches == 443
    assert a.deployer_age_secs == 691200
    # MINT_B had no create row (findings caveat 8) -> deployer fields None.
    assert recs["MINT_B"].deployer_wallet is None


def test_merge_holders_populates_when_present_and_skips_when_absent():
    recs = transform.parse_graduations(grad_rows())
    holder_rows = [
        {
            "mint": "MINT_A",
            "visible_holder_count": 137,
            "top10_concentration": 0.42,
            "top20_concentration": 0.61,
        }
    ]
    transform.merge_holders(recs, holder_rows)
    assert recs["MINT_A"].visible_holder_count == 137
    assert recs["MINT_A"].top10_concentration == 0.42
    # MINT_B not in the holder rows -> stays None (the NULL fallback).
    assert recs["MINT_B"].visible_holder_count is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest bootstrap/tests/test_transform.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap.transform'`.

- [ ] **Step 3: Write `bootstrap/transform.py`**

Create `bootstrap/transform.py`:

```python
"""Pure transforms: raw Dune result rows -> typed GraduationRecord objects.

GraduationRecord mirrors the historical_graduations table. Large u64 on-chain
values are kept as strings (the repo SQLite convention -- SQLite's max integer
is i64). Booleans and the outcome are ints (0/1). NULL-able feature fields
default to None.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set


@dataclass
class GraduationRecord:
    """One row of historical_graduations -- all features for one token."""

    # --- graduation facts (always present) ---
    mint: str
    pool_address: str
    bonding_curve_address: str
    lp_mint: str
    migrator_wallet: str
    graduation_time: int  # Unix seconds
    graduation_slot: int

    # --- outcome label (filled by merge_outcome) ---
    survived: Optional[int] = None  # 0 / 1
    outcome_base_reserve: Optional[str] = None  # u64 as str
    outcome_quote_reserve: Optional[str] = None  # u64 as str
    outcome_checked_at: Optional[int] = None  # Unix seconds

    # --- liquidity at ~T0+12h (merge_liquidity) ---
    liq_base_reserve: Optional[str] = None  # u64 as str
    liq_quote_reserve: Optional[str] = None  # u64 as str
    lp_burned: int = 1  # findings heuristic default; cleared if withdrawn
    pool_supply_fraction: Optional[float] = None  # Dune cannot supply -> NULL

    # --- bonding-curve final state (merge_bonding_curve) ---
    curve_real_sol_reserves: Optional[str] = None  # u64 as str
    curve_real_token_reserves: Optional[str] = None  # u64 as str
    curve_token_total_supply: Optional[str] = None  # u64 as str

    # --- contract flags (merge_contract_flags) ---
    mint_authority_present: Optional[int] = None  # 0 / 1
    freeze_authority_present: int = 0  # cohort constant (findings 3.4)

    # --- holder distribution, best-effort (merge_holders) ---
    visible_holder_count: Optional[int] = None
    top10_concentration: Optional[float] = None
    top20_concentration: Optional[float] = None
    creator_bag_fraction: Optional[float] = None  # Dune cannot supply -> NULL

    # --- deployer signal, FIRST-CLASS (merge_deployer) ---
    deployer_wallet: Optional[str] = None
    deployer_prior_launches: Optional[int] = None
    deployer_age_secs: Optional[int] = None


def parse_dune_time(value: str) -> int:
    """Parse a Dune timestamp string to Unix seconds (UTC).

    Handles 'YYYY-MM-DD HH:MM:SS[.fff] UTC' and ISO 'YYYY-MM-DDTHH:MM:SSZ'.
    """
    text = str(value).strip()
    if text.endswith(" UTC"):
        text = text[:-4].strip()
        fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in text else "%Y-%m-%d %H:%M:%S"
        dt = datetime.strptime(text, fmt)
    else:
        # ISO 8601, with or without a trailing Z.
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def parse_graduations(rows: List[dict]) -> Dict[str, GraduationRecord]:
    """Build seed GraduationRecords keyed by mint from graduations-list rows."""
    records: Dict[str, GraduationRecord] = {}
    for row in rows:
        mint = str(row["mint"])
        records[mint] = GraduationRecord(
            mint=mint,
            pool_address=str(row["pool_address"]),
            bonding_curve_address=str(row["bonding_curve_address"]),
            lp_mint=str(row["lp_mint"]),
            migrator_wallet=str(row["migrator_wallet"]),
            graduation_time=parse_dune_time(row["graduation_time"]),
            graduation_slot=int(row["graduation_slot"]),
        )
    return records


def merge_outcome(
    records: Dict[str, GraduationRecord],
    rows: List[dict],
    survival_min_quote_lamports: int,
) -> None:
    """Fold outcome rows into records and derive `survived`.

    A pool with no row in `rows` was abandoned (findings 3.1): survived = 0,
    reserves 0.
    """
    by_pool = {str(r["pool_address"]): r for r in rows}
    for record in records.values():
        row = by_pool.get(record.pool_address)
        if row is None:
            record.survived = 0
            record.outcome_base_reserve = "0"
            record.outcome_quote_reserve = "0"
            record.outcome_checked_at = record.graduation_time
            continue
        quote = str(row["outcome_quote_reserve"])
        base = str(row["outcome_base_reserve"])
        record.outcome_base_reserve = base
        record.outcome_quote_reserve = quote
        record.outcome_checked_at = parse_dune_time(row["outcome_event_time"])
        record.survived = (
            1 if int(quote) >= survival_min_quote_lamports else 0
        )


def merge_liquidity(
    records: Dict[str, GraduationRecord],
    rows: List[dict],
    withdrawn_pools: Set[str],
) -> None:
    """Fold T0+12h liquidity rows into records.

    lp_burned is the findings heuristic: True unless the pool had a withdraw
    event (its pool address is in `withdrawn_pools`).
    """
    by_pool = {str(r["pool_address"]): r for r in rows}
    for record in records.values():
        if record.pool_address in withdrawn_pools:
            record.lp_burned = 0
        row = by_pool.get(record.pool_address)
        if row is None:
            continue
        record.liq_base_reserve = str(row["liq_base_reserve"])
        record.liq_quote_reserve = str(row["liq_quote_reserve"])


def merge_bonding_curve(
    records: Dict[str, GraduationRecord], rows: List[dict]
) -> None:
    """Fold bonding-curve trade rows; keep the last trade before migration.

    "Before migration" is by slot, not timestamp (findings caveat 4): only
    trades whose evt_block_slot is strictly less than the mint's
    graduation_slot count.
    """
    best_by_mint: Dict[str, dict] = {}
    for row in rows:
        mint = str(row["mint"])
        record = records.get(mint)
        if record is None:
            continue
        slot = int(row["evt_block_slot"])
        if slot >= record.graduation_slot:
            continue  # at/after migration -> not the pre-graduation state
        best = best_by_mint.get(mint)
        if best is None or slot > int(best["evt_block_slot"]):
            best_by_mint[mint] = row
    for mint, row in best_by_mint.items():
        record = records[mint]
        real_token = str(row["real_token_reserves"])
        virtual_token = str(row["virtual_token_reserves"])
        record.curve_real_sol_reserves = str(row["real_sol_reserves"])
        record.curve_real_token_reserves = real_token
        # total supply = virtual + real token reserves at the final trade.
        record.curve_token_total_supply = str(
            int(virtual_token) + int(real_token)
        )


def merge_contract_flags(
    records: Dict[str, GraduationRecord], rows: List[dict]
) -> None:
    """Fold contract-flag rows; set mint_authority_present (0/1)."""
    by_mint = {str(r["mint"]): r for r in rows}
    for mint, record in records.items():
        row = by_mint.get(mint)
        if row is None:
            continue
        record.mint_authority_present = int(row["mint_authority_present"])
        # freeze_authority_present stays the cohort constant 0.


def merge_deployer(
    records: Dict[str, GraduationRecord], rows: List[dict]
) -> None:
    """Fold deployer-signal rows -- the FIRST-CLASS deployer fingerprint."""
    by_mint = {str(r["mint"]): r for r in rows}
    for mint, record in records.items():
        row = by_mint.get(mint)
        if row is None:
            continue  # findings caveat 8: not every mint has a create row
        record.deployer_wallet = str(row["deployer_wallet"])
        record.deployer_prior_launches = int(row["deployer_prior_launches"])
        record.deployer_age_secs = int(row["deployer_age_secs"])


def merge_holders(
    records: Dict[str, GraduationRecord], rows: List[dict]
) -> None:
    """Fold holder-distribution rows (best-effort).

    Mints absent from `rows` keep their None holder fields -- the designed
    NULL fallback when a holder batch times out or has no transfers.
    """
    by_mint = {str(r["mint"]): r for r in rows}
    for mint, record in records.items():
        row = by_mint.get(mint)
        if row is None:
            continue
        record.visible_holder_count = int(row["visible_holder_count"])
        record.top10_concentration = float(row["top10_concentration"])
        record.top20_concentration = float(row["top20_concentration"])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest bootstrap/tests/test_transform.py -q`
Expected: PASS — all eleven tests green.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/transform.py bootstrap/tests/test_transform.py
git commit -m "Add pure Dune-row to GraduationRecord transforms

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `sample.py` — month-stratified sampling

**Files:**
- Create: `bootstrap/sample.py`
- Test: `bootstrap/tests/test_sample.py`

A pure function `stratified_sample(records, sample_size, seed)` that picks ~`sample_size` graduations spread across the calendar months present in the input, so every month (a market regime) is represented (spec §4.3). It groups records by `YYYY-MM` of `graduation_time`, computes a per-month quota (the target split evenly across months, with the remainder distributed to the earliest months), and within each month takes a deterministic pseudo-random subset (sorted by a seeded hash of the mint, so it is reproducible and unbiased by Dune's row order). If a month has fewer records than its quota, all of them are taken and the shortfall is **not** redistributed (keeping the per-month logic simple and the result close to even). The function is pure and deterministic given `seed`.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/tests/test_sample.py`:

```python
"""Unit tests for bootstrap.sample -- pure month-stratified sampling."""

from bootstrap.sample import month_key, stratified_sample
from bootstrap.transform import GraduationRecord


def rec(mint: str, ts: int) -> GraduationRecord:
    """A GraduationRecord with only the fields sampling needs."""
    return GraduationRecord(
        mint=mint,
        pool_address=f"pool-{mint}",
        bonding_curve_address="bc",
        lp_mint="lp",
        migrator_wallet="mig",
        graduation_time=ts,
        graduation_slot=1,
    )


# Unix seconds inside specific months.
NOV_2025 = 1763000000  # 2025-11
DEC_2025 = 1765600000  # 2025-12
JAN_2026 = 1768200000  # 2026-01


def test_month_key_formats_year_month():
    assert month_key(NOV_2025) == "2025-11"
    assert month_key(JAN_2026) == "2026-01"


def make_pool(prefix: str, ts: int, n: int):
    return {f"{prefix}{i}": rec(f"{prefix}{i}", ts) for i in range(n)}


def test_every_month_is_represented():
    records = {}
    records.update(make_pool("nov", NOV_2025, 100))
    records.update(make_pool("dec", DEC_2025, 100))
    records.update(make_pool("jan", JAN_2026, 100))
    picked = stratified_sample(records, sample_size=30, seed=7)
    months = {month_key(r.graduation_time) for r in picked}
    assert months == {"2025-11", "2025-12", "2026-01"}
    # 30 across 3 months -> 10 each.
    assert len(picked) == 30


def test_is_deterministic_for_a_fixed_seed():
    records = {}
    records.update(make_pool("nov", NOV_2025, 50))
    records.update(make_pool("dec", DEC_2025, 50))
    a = stratified_sample(records, sample_size=20, seed=42)
    b = stratified_sample(records, sample_size=20, seed=42)
    assert [r.mint for r in a] == [r.mint for r in b]


def test_different_seeds_pick_different_subsets():
    records = make_pool("nov", NOV_2025, 100)
    a = {r.mint for r in stratified_sample(records, sample_size=10, seed=1)}
    b = {r.mint for r in stratified_sample(records, sample_size=10, seed=2)}
    assert a != b


def test_a_thin_month_contributes_all_it_has():
    records = {}
    records.update(make_pool("nov", NOV_2025, 3))  # thin month
    records.update(make_pool("dec", DEC_2025, 100))
    picked = stratified_sample(records, sample_size=20, seed=5)
    nov = [r for r in picked if month_key(r.graduation_time) == "2025-11"]
    # quota per month is 10, but November only has 3 -> all 3 taken.
    assert len(nov) == 3


def test_sample_larger_than_population_returns_everything():
    records = make_pool("nov", NOV_2025, 5)
    picked = stratified_sample(records, sample_size=1000, seed=0)
    assert len(picked) == 5


def test_empty_input_returns_empty_list():
    assert stratified_sample({}, sample_size=100, seed=1) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest bootstrap/tests/test_sample.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap.sample'`.

- [ ] **Step 3: Write `bootstrap/sample.py`**

Create `bootstrap/sample.py`:

```python
"""Pure month-stratified sampling of the graduations list.

Phase 3 validation requires every market regime in the window to be present
(design spec 4.3). Records are grouped by calendar month; a per-month quota is
the target split evenly across months; within a month a deterministic,
seed-driven pseudo-random subset is taken so the result is reproducible and
not biased by Dune's row order.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, List

from bootstrap.transform import GraduationRecord


def month_key(unix_secs: int) -> str:
    """The 'YYYY-MM' calendar month of a Unix timestamp (UTC)."""
    dt = datetime.fromtimestamp(int(unix_secs), tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def _sort_rank(mint: str, seed: int) -> str:
    """A deterministic, seed-salted hash of a mint, used as a sort key."""
    return hashlib.sha256(f"{seed}:{mint}".encode("utf-8")).hexdigest()


def stratified_sample(
    records: Dict[str, GraduationRecord],
    sample_size: int,
    seed: int,
) -> List[GraduationRecord]:
    """Pick ~sample_size records spread evenly across calendar months.

    Deterministic for a fixed seed. A month with fewer records than its quota
    contributes all of them (the shortfall is not redistributed).
    """
    # group by month.
    by_month: Dict[str, List[GraduationRecord]] = {}
    for record in records.values():
        by_month.setdefault(
            month_key(record.graduation_time), []
        ).append(record)

    if not by_month:
        return []

    months = sorted(by_month.keys())
    base_quota = sample_size // len(months)
    remainder = sample_size % len(months)

    picked: List[GraduationRecord] = []
    for index, month in enumerate(months):
        # the earliest `remainder` months get one extra to use the full target.
        quota = base_quota + (1 if index < remainder else 0)
        bucket = sorted(
            by_month[month], key=lambda r: _sort_rank(r.mint, seed)
        )
        picked.extend(bucket[:quota])
    return picked
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest bootstrap/tests/test_sample.py -q`
Expected: PASS — all seven tests green.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/sample.py bootstrap/tests/test_sample.py
git commit -m "Add pure month-stratified graduation sampling

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `load.py` — the `historical_graduations` table and the idempotent loader

**Files:**
- Create: `bootstrap/load.py`
- Test: `bootstrap/tests/test_load.py`

`load.py` owns the `historical_graduations` table. `CREATE_TABLE_SQL` is the `CREATE TABLE IF NOT EXISTS` DDL — the column list derived from the §4.2 feature set, with the `0002_survival.sql` conventions (large `u64` as `TEXT`, booleans/`survived` as `INTEGER`, nullable for the features Dune may not supply, `mint TEXT PRIMARY KEY`). `create_table(conn)` runs it. `load_records(conn, records)` does an idempotent, mint-keyed bulk insert (`INSERT ... ON CONFLICT(mint) DO NOTHING`) and returns the count actually inserted. `existing_mints(conn)` returns the set of already-loaded mints so the orchestrator can skip them. All functions take an open `sqlite3.Connection`; tests run against a real temp-file SQLite DB.

- [ ] **Step 1: Write the failing test**

Create `bootstrap/tests/test_load.py`:

```python
"""Unit tests for bootstrap.load -- against a real temp-file SQLite DB."""

import sqlite3

from bootstrap.load import (
    CREATE_TABLE_SQL,
    create_table,
    existing_mints,
    load_records,
)
from bootstrap.transform import GraduationRecord


def open_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    create_table(conn)
    return conn


def full_record(mint: str) -> GraduationRecord:
    """A record with every feature populated (no NULLs)."""
    return GraduationRecord(
        mint=mint,
        pool_address=f"pool-{mint}",
        bonding_curve_address=f"bc-{mint}",
        lp_mint=f"lp-{mint}",
        migrator_wallet="mig",
        graduation_time=1768032000,
        graduation_slot=312000000,
        survived=1,
        outcome_base_reserve="120000000000000",
        outcome_quote_reserve="92000000000",
        outcome_checked_at=1769241600,
        liq_base_reserve="1073000000000000",
        liq_quote_reserve="64000000000",
        lp_burned=1,
        pool_supply_fraction=None,
        curve_real_sol_reserves="85005359500",
        curve_real_token_reserves="0",
        curve_token_total_supply="1000000000000000",
        mint_authority_present=0,
        freeze_authority_present=0,
        visible_holder_count=137,
        top10_concentration=0.42,
        top20_concentration=0.61,
        creator_bag_fraction=None,
        deployer_wallet="DEP",
        deployer_prior_launches=443,
        deployer_age_secs=691200,
    )


def sparse_record(mint: str) -> GraduationRecord:
    """A record with every NULL-able feature left None (Dune timed out)."""
    return GraduationRecord(
        mint=mint,
        pool_address=f"pool-{mint}",
        bonding_curve_address=f"bc-{mint}",
        lp_mint=f"lp-{mint}",
        migrator_wallet="mig",
        graduation_time=1768032000,
        graduation_slot=312000000,
        survived=0,
        outcome_base_reserve="0",
        outcome_quote_reserve="0",
        outcome_checked_at=1768032000,
        curve_real_sol_reserves="85000000000",
        curve_real_token_reserves="0",
        curve_token_total_supply="1000000000000000",
        mint_authority_present=0,
        deployer_wallet="DEP2",
        deployer_prior_launches=1,
        deployer_age_secs=3600,
    )


def test_create_table_is_idempotent(tmp_path):
    conn = open_db(tmp_path)
    create_table(conn)  # second call must not raise
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='historical_graduations'"
    )
    assert cur.fetchone() is not None


def test_load_a_full_record_round_trips(tmp_path):
    conn = open_db(tmp_path)
    inserted = load_records(conn, [full_record("MINT_A")])
    assert inserted == 1
    row = conn.execute(
        "SELECT mint, survived, curve_real_sol_reserves, "
        "deployer_prior_launches, outcome_quote_reserve, visible_holder_count "
        "FROM historical_graduations WHERE mint = 'MINT_A'"
    ).fetchone()
    assert row[0] == "MINT_A"
    assert row[1] == 1  # survived
    assert row[2] == "85005359500"  # u64 stored as TEXT
    assert row[3] == 443  # deployer_prior_launches
    assert row[4] == "92000000000"
    assert row[5] == 137


def test_sparse_record_stores_nulls_for_missing_features(tmp_path):
    conn = open_db(tmp_path)
    load_records(conn, [sparse_record("MINT_S")])
    row = conn.execute(
        "SELECT visible_holder_count, top10_concentration, "
        "top20_concentration, pool_supply_fraction, creator_bag_fraction "
        "FROM historical_graduations WHERE mint = 'MINT_S'"
    ).fetchone()
    assert row == (None, None, None, None, None)
    # but the first-class deployer signal is NOT null.
    dep = conn.execute(
        "SELECT deployer_wallet, deployer_prior_launches "
        "FROM historical_graduations WHERE mint = 'MINT_S'"
    ).fetchone()
    assert dep == ("DEP2", 1)


def test_load_is_idempotent_on_mint(tmp_path):
    conn = open_db(tmp_path)
    first = load_records(conn, [full_record("MINT_A")])
    assert first == 1
    # re-loading the same mint inserts nothing and does not raise.
    again = load_records(conn, [full_record("MINT_A")])
    assert again == 0
    count = conn.execute(
        "SELECT COUNT(*) FROM historical_graduations"
    ).fetchone()[0]
    assert count == 1


def test_load_a_batch_with_some_already_present(tmp_path):
    conn = open_db(tmp_path)
    load_records(conn, [full_record("M1")])
    inserted = load_records(
        conn, [full_record("M1"), full_record("M2"), full_record("M3")]
    )
    assert inserted == 2  # M1 skipped, M2 + M3 new
    assert conn.execute(
        "SELECT COUNT(*) FROM historical_graduations"
    ).fetchone()[0] == 3


def test_existing_mints_returns_the_loaded_set(tmp_path):
    conn = open_db(tmp_path)
    assert existing_mints(conn) == set()
    load_records(conn, [full_record("M1"), full_record("M2")])
    assert existing_mints(conn) == {"M1", "M2"}


def test_create_table_sql_has_the_spec_columns():
    low = CREATE_TABLE_SQL.lower()
    # mint is the PRIMARY KEY idempotency key.
    assert "mint" in low and "primary key" in low
    # the first-class deployer columns are present.
    assert "deployer_wallet" in low
    assert "deployer_prior_launches" in low
    assert "deployer_age_secs" in low
    # u64 reserves are TEXT.
    assert "curve_real_sol_reserves text" in low
    assert "outcome_quote_reserve text" in low
    # the outcome is an INTEGER.
    assert "survived integer" in low
    # the NULL-able feature columns are declared without NOT NULL.
    for nullable in (
        "pool_supply_fraction",
        "creator_bag_fraction",
        "visible_holder_count",
        "top10_concentration",
        "top20_concentration",
    ):
        assert nullable in low
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest bootstrap/tests/test_load.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'bootstrap.load'`.

- [ ] **Step 3: Write `bootstrap/load.py`**

Create `bootstrap/load.py`:

```python
"""The historical_graduations table: its DDL and an idempotent loader.

This table is created and owned by the Python ETL -- it is NOT part of any
storm-store sqlx migration. Column conventions follow the repo's
0002_survival.sql: large u64 on-chain values as TEXT, booleans and the outcome
as INTEGER 0/1, INTEGER Unix-seconds timestamps. Features Dune may not supply
(liq reserves, pool_supply_fraction, creator_bag_fraction, the holder group)
are nullable. `mint` is the PRIMARY KEY -- the idempotency / resumability key.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, List, Set

from bootstrap.transform import GraduationRecord

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS historical_graduations (
    -- graduation facts ----------------------------------------------------
    mint                      TEXT PRIMARY KEY,        -- idempotency key
    pool_address              TEXT NOT NULL,
    bonding_curve_address     TEXT NOT NULL,
    lp_mint                   TEXT NOT NULL,
    migrator_wallet           TEXT NOT NULL,
    graduation_time           INTEGER NOT NULL,        -- Unix seconds (T0)
    graduation_slot           INTEGER NOT NULL,
    -- outcome label -------------------------------------------------------
    survived                  INTEGER NOT NULL,        -- 0 rugged, 1 survived
    outcome_base_reserve      TEXT NOT NULL,           -- u64
    outcome_quote_reserve     TEXT NOT NULL,           -- u64
    outcome_checked_at        INTEGER NOT NULL,        -- Unix seconds
    -- liquidity at ~T0+12h ------------------------------------------------
    liq_base_reserve          TEXT,                    -- u64; null if abandoned
    liq_quote_reserve         TEXT,                    -- u64; null if abandoned
    lp_burned                 INTEGER NOT NULL,        -- 0 | 1 (heuristic)
    pool_supply_fraction      REAL,                    -- null: Dune cannot supply
    -- bonding-curve final state ------------------------------------------
    curve_real_sol_reserves   TEXT NOT NULL,           -- u64
    curve_real_token_reserves TEXT NOT NULL,           -- u64
    curve_token_total_supply  TEXT NOT NULL,           -- u64
    -- contract flags ------------------------------------------------------
    mint_authority_present    INTEGER NOT NULL,        -- 0 | 1
    freeze_authority_present  INTEGER NOT NULL,        -- 0 | 1 (cohort constant)
    -- holder distribution (best-effort; nullable on a Dune timeout) -------
    visible_holder_count      INTEGER,
    top10_concentration       REAL,
    top20_concentration       REAL,
    creator_bag_fraction      REAL,                    -- null: Dune cannot supply
    -- deployer signal (FIRST-CLASS; populated, not null) -----------------
    deployer_wallet           TEXT NOT NULL,
    deployer_prior_launches   INTEGER NOT NULL,
    deployer_age_secs         INTEGER NOT NULL,
    loaded_at                 INTEGER NOT NULL DEFAULT (unixepoch())
)
""".strip()

# The INSERT column order; `loaded_at` is left to its DEFAULT.
_COLUMNS = [
    "mint",
    "pool_address",
    "bonding_curve_address",
    "lp_mint",
    "migrator_wallet",
    "graduation_time",
    "graduation_slot",
    "survived",
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "outcome_checked_at",
    "liq_base_reserve",
    "liq_quote_reserve",
    "lp_burned",
    "pool_supply_fraction",
    "curve_real_sol_reserves",
    "curve_real_token_reserves",
    "curve_token_total_supply",
    "mint_authority_present",
    "freeze_authority_present",
    "visible_holder_count",
    "top10_concentration",
    "top20_concentration",
    "creator_bag_fraction",
    "deployer_wallet",
    "deployer_prior_launches",
    "deployer_age_secs",
]

_INSERT_SQL = (
    "INSERT INTO historical_graduations ("
    + ", ".join(_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" for _ in _COLUMNS)
    + ") ON CONFLICT(mint) DO NOTHING"
)


def create_table(conn: sqlite3.Connection) -> None:
    """Create historical_graduations if it does not already exist."""
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()


def _record_row(record: GraduationRecord) -> tuple:
    """A GraduationRecord as a tuple in _COLUMNS order."""
    return tuple(getattr(record, column) for column in _COLUMNS)


def load_records(
    conn: sqlite3.Connection, records: Iterable[GraduationRecord]
) -> int:
    """Idempotently insert records keyed on mint.

    Returns the number of rows actually inserted (mints already present are
    skipped via ON CONFLICT DO NOTHING).
    """
    before = conn.execute(
        "SELECT COUNT(*) FROM historical_graduations"
    ).fetchone()[0]
    rows = [_record_row(record) for record in records]
    conn.executemany(_INSERT_SQL, rows)
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) FROM historical_graduations"
    ).fetchone()[0]
    return after - before


def existing_mints(conn: sqlite3.Connection) -> Set[str]:
    """The set of mints already in historical_graduations."""
    cur = conn.execute("SELECT mint FROM historical_graduations")
    return {row[0] for row in cur.fetchall()}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest bootstrap/tests/test_load.py -q`
Expected: PASS — all seven tests green.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m pytest bootstrap/tests -q`
Expected: PASS — every test from Tasks 1–8 green (scaffold, config, cache, dune_client, queries, transform, sample, load).

- [ ] **Step 6: Commit**

```bash
git add bootstrap/load.py bootstrap/tests/test_load.py
git commit -m "Add historical_graduations table DDL and idempotent loader

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `run.py` — the orchestrator + the pilot run

**Files:**
- Create: `bootstrap/run.py`
- Test: exercised by the pilot run below (no `pytest` test — see the rationale)

`run.py` wires the whole ETL: load config → fetch the graduations list (cached) → month-stratified sample → skip already-loaded mints → run each per-stage Dune query in mint/pool batches (each batch cached to disk) → transform → load into `historical_graduations`. It accumulates and prints `execution_cost_credits`. Holder distribution is wrapped in a `try/except DuneTimeout`: on a timeout the batch is cached as a timeout marker and its holder columns stay NULL. The CLI flag `--pilot` selects the pilot config (small sample). This task also **runs the pilot** against real Dune.

Per the writing-plans guidance that thin orchestration glue with no independent logic does not need its own failing-test cycle, `run.py` has no `pytest` test — every pure function it calls (`queries`, `transform`, `sample`, `load`, `cache`) is already TDD-tested, and `dune_client` is fake-transport-tested. `run.py`'s correctness is verified by the pilot run (Step 3 below): a real end-to-end execution against Dune.

- [ ] **Step 1: Write `bootstrap/run.py`**

Create `bootstrap/run.py`:

```python
"""The historical-graduation ETL orchestrator.

Wires: config -> graduations list -> month-stratified sample -> skip
already-loaded mints -> per-stage Dune queries (each batch disk-cached) ->
transform -> load into historical_graduations. Idempotent and resumable: a
re-run skips loaded mints and reuses cached stage results, so a crash never
re-spends Dune credits. Holder distribution is best-effort: a DuneTimeout on a
batch leaves that batch's holder columns NULL and the run continues.

Usage:
    python3 -m bootstrap.run            # full ~5,000-token run
    python3 -m bootstrap.run --pilot    # ~75-token end-to-end pilot
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from bootstrap import cache, queries, transform
from bootstrap.config import Config, load_config
from bootstrap.dune_client import DuneClient, DuneTimeout
from bootstrap.load import create_table, existing_mints, load_records
from bootstrap.sample import stratified_sample
from bootstrap.transform import GraduationRecord

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bootstrap")

# A fixed seed so the stratified sample is reproducible across re-runs.
_SAMPLE_SEED = 20260519


class CreditMeter:
    """Accumulates Dune execution credits and logs a running total."""

    def __init__(self) -> None:
        self.total = 0.0

    def add(self, credits: float, label: str) -> None:
        self.total += credits
        log.info(
            "stage %-20s +%.2f credits (running total %.2f)",
            label, credits, self.total,
        )


def _batched(items: List[str], size: int) -> List[List[str]]:
    """Split a list into chunks of at most `size`."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def _settle_cutoff(config: Config) -> str:
    """ISO date now - outcome_settle_days: tokens after it are not settled."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=config.outcome_settle_days
    )
    return cutoff.strftime("%Y-%m-%d")


def _run_cached_stage(
    client: DuneClient,
    meter: CreditMeter,
    config: Config,
    stage: str,
    sql: str,
    batch: Optional[int] = None,
) -> List[dict]:
    """Run one Dune query, or load it from the disk cache if already run.

    Returns the stage's result rows. Raises DuneTimeout to the caller (the
    holder stage catches it; every other stage treats it as fatal).
    """
    cached = cache.read_cache(config.cache_dir, stage, batch)
    if cached is not None:
        log.info("stage %-20s cache hit (batch %s)", stage, batch)
        return cached.get("rows", [])
    rows, credits = client.run_sql(sql)
    label = f"{stage}[{batch}]" if batch is not None else stage
    meter.add(credits, label)
    cache.write_cache(config.cache_dir, stage, {"rows": rows}, batch)
    return rows


def fetch_graduations(
    client: DuneClient, meter: CreditMeter, config: Config
) -> List[dict]:
    """Stage 1: the full settled graduations list (single cached query)."""
    sql = queries.graduations_sql(
        window_start=config.window_start,
        settle_cutoff=_settle_cutoff(config),
    )
    return _run_cached_stage(client, meter, config, "graduations", sql)


def run_etl(config: Config) -> None:
    """Run the whole ETL end-to-end for the given config."""
    meter = CreditMeter()
    client = DuneClient(config)
    conn = sqlite3.connect(config.db_path)
    create_table(conn)

    # --- stage 1: graduations list, then month-stratified sample ---
    grad_rows = fetch_graduations(client, meter, config)
    log.info("graduations list: %d settled graduations", len(grad_rows))
    all_records = transform.parse_graduations(grad_rows)
    sampled = stratified_sample(
        all_records, sample_size=config.sample_size, seed=_SAMPLE_SEED
    )
    log.info("sample: %d graduations across the month strata", len(sampled))

    # --- resumability: drop mints already in historical_graduations ---
    done = existing_mints(conn)
    records: Dict[str, GraduationRecord] = {
        r.mint: r for r in sampled if r.mint not in done
    }
    log.info("%d already loaded, %d to process", len(done), len(records))
    if not records:
        log.info("nothing to do -- dataset already complete for this sample")
        conn.close()
        return

    mints = sorted(records.keys())
    pools = sorted({r.pool_address for r in records.values()})

    # --- stage 2: outcome label (event-batched) ---
    outcome_rows: List[dict] = []
    for index, pool_batch in enumerate(
        _batched(pools, config.event_batch_size)
    ):
        outcome_rows += _run_cached_stage(
            client, meter, config, "outcome",
            queries.outcome_sql(pool_batch), batch=index,
        )
    transform.merge_outcome(
        records, outcome_rows,
        survival_min_quote_lamports=config.survival_min_quote_lamports,
    )

    # --- stage 3: liquidity at ~T0+12h (event-batched) ---
    liq_rows: List[dict] = []
    for index, pool_batch in enumerate(
        _batched(pools, config.event_batch_size)
    ):
        liq_rows += _run_cached_stage(
            client, meter, config, "liquidity",
            queries.liquidity_sql(pool_batch), batch=index,
        )
    # withdrawn_pools is left empty: the findings heuristic treats every
    # PumpSwap-era graduation as lp_burned unless a withdraw event is seen,
    # and the withdraw-event probe is not part of the bootstrap query set.
    transform.merge_liquidity(records, liq_rows, withdrawn_pools=set())

    # --- stage 4: bonding-curve final state (mint-batched) ---
    bc_rows: List[dict] = []
    for index, mint_batch in enumerate(
        _batched(mints, config.event_batch_size)
    ):
        bc_rows += _run_cached_stage(
            client, meter, config, "bonding_curve",
            queries.bonding_curve_sql(mint_batch), batch=index,
        )
    transform.merge_bonding_curve(records, bc_rows)

    # --- stage 5: contract flags (mint-batched, larger batch) ---
    flag_rows: List[dict] = []
    for index, mint_batch in enumerate(
        _batched(mints, config.flag_batch_size)
    ):
        flag_rows += _run_cached_stage(
            client, meter, config, "contract_flags",
            queries.contract_flags_sql(mint_batch), batch=index,
        )
    transform.merge_contract_flags(records, flag_rows)

    # --- stage 6: deployer signal -- FIRST-CLASS (mint-batched) ---
    max_grad = _settle_cutoff(config)
    dep_rows: List[dict] = []
    for index, mint_batch in enumerate(
        _batched(mints, config.flag_batch_size)
    ):
        dep_rows += _run_cached_stage(
            client, meter, config, "deployer",
            queries.deployer_sql(mint_batch, max_grad_time=max_grad),
            batch=index,
        )
    transform.merge_deployer(records, dep_rows)

    # --- stage 7: holder distribution -- BEST-EFFORT (small batches) ---
    holder_rows: List[dict] = []
    for index, mint_batch in enumerate(
        _batched(mints, config.holder_batch_size)
    ):
        # one snapshot time per batch is an approximation: use the batch's
        # earliest graduation + 12h, good enough for a holder snapshot.
        batch_t0 = min(records[m].graduation_time for m in mint_batch)
        snapshot = datetime.fromtimestamp(
            batch_t0 + config.liquidity_snapshot_hours * 3600,
            tz=timezone.utc,
        ).strftime("%Y-%m-%d %H:%M:%S")
        marker = cache.read_cache(config.cache_dir, "holders", index)
        if marker is not None and marker.get("timed_out"):
            log.info("holder batch %d previously timed out -- skipping", index)
            continue
        try:
            holder_rows += _run_cached_stage(
                client, meter, config, "holders",
                queries.holders_sql(mint_batch, snapshot_time=snapshot),
                batch=index,
            )
        except DuneTimeout:
            log.warning(
                "holder batch %d timed out -- columns NULL for %d mints",
                index, len(mint_batch),
            )
            cache.write_cache(
                config.cache_dir, "holders", {"timed_out": True}, index
            )
    transform.merge_holders(records, holder_rows)

    # --- load ---
    inserted = load_records(conn, list(records.values()))
    conn.close()
    log.info(
        "DONE: inserted %d rows; total Dune credits spent %.2f",
        inserted, meter.total,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="solana-storm Phase 2 Dune historical-graduation ETL"
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="run on a tiny sample end-to-end (validates the pipeline)",
    )
    args = parser.parse_args()
    config = load_config(pilot=args.pilot)
    log.info(
        "starting ETL (%s): sample_size=%d db=%s cache=%s",
        "PILOT" if config.is_pilot else "FULL",
        config.sample_size, config.db_path, config.cache_dir,
    )
    run_etl(config)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the whole suite still passes (no regression)**

Run: `python3 -m pytest bootstrap/tests -q`
Expected: PASS — adding `run.py` imports only the existing modules; no test changes; every test still green.

- [ ] **Step 3: Run the pilot against real Dune**

Confirm `DUNE_API_KEY` is set (it is in the repo `.env`); the ETL reads it from the environment, so export the `.env` values into the shell first:

```bash
set -a && . ./.env && set +a
python3 -m bootstrap.run --pilot
```

Expected: the run completes end-to-end in a few minutes. The log shows, in order: the graduations-list size, a sample of ~75, the per-stage credit lines with a running total, any holder-batch timeout warnings (the pilot's ~75 mints are 2 holder batches of 50/25 — at least one should complete; a timeout on one is acceptable and exercises the NULL-fallback path), and a final `DONE: inserted N rows; total Dune credits spent C` line with `C` well under ~20 credits.

- [ ] **Step 4: Verify the pilot output**

Run:

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("./storm.db")
def one(q):
    return c.execute(q).fetchone()[0]
n = one("SELECT COUNT(*) FROM historical_graduations")
surv = one("SELECT COUNT(*) FROM historical_graduations WHERE survived = 1")
dep = one("SELECT COUNT(*) FROM historical_graduations "
          "WHERE deployer_wallet IS NOT NULL")
hol = one("SELECT COUNT(*) FROM historical_graduations "
          "WHERE visible_holder_count IS NOT NULL")
months = one("SELECT COUNT(DISTINCT strftime('%Y-%m', "
             "datetime(graduation_time, 'unixepoch'))) "
             "FROM historical_graduations")
print(f"rows={n} survived={surv} deployer_populated={dep} "
      f"holders_populated={hol} distinct_months={months}")
c.close()
PY
```

Expected: `rows` is ~75 (the pilot sample, possibly fewer if a thin month had little data); `deployer_populated` equals `rows` minus any mints with no `pump_call_create`/`_v2` row (the deployer signal is first-class and should be populated for the large majority); `holders_populated` is >= 0 (may be 0 if both pilot batches timed out — acceptable); `distinct_months` is >= 1. If `rows` is 0 or the run errored, STOP and debug before the full run — do not proceed to Task 10.

- [ ] **Step 5: Commit**

```bash
git add bootstrap/run.py
git commit -m "Add ETL orchestrator CLI and validate with a Dune pilot run

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The full ~5,000-token run

**Files:**
- None created or modified — this task runs the already-built ETL at full scale, then appends a run-log section to `bootstrap/README.md`.

This task runs the full extraction only after the Task 9 pilot succeeded. It produces the Phase 3 dataset: a `historical_graduations` table of ~5,000 month-stratified graduated tokens.

- [ ] **Step 1: Clear the pilot rows and cache**

The pilot left ~75 rows in `historical_graduations` and a stage cache scoped to the pilot's 75-token sample. The full run computes its stratified sample independently, so the cleanest dataset comes from a fresh table and a fresh cache:

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("./storm.db")
c.execute("DROP TABLE IF EXISTS historical_graduations")
c.commit()
c.close()
print("dropped historical_graduations")
PY
rm -rf bootstrap/data
```

(Skipping the table drop is safe — the full run is idempotent — but the pilot's cache covers a different sample, so removing `bootstrap/data` avoids a stale-cache mismatch.)

- [ ] **Step 2: Run the full ETL**

```bash
set -a && . ./.env && set +a
python3 -m bootstrap.run
```

Expected: the run takes ~30–60 minutes of wall-clock time (the Dune free plan serialises queries — findings §6 caveat 7). The log shows per-stage credit lines; the holder stage runs ~50 batches of 50 mints, of which some may time out (each logs a warning and NULLs that batch). The final `DONE` line reports the inserted row count (~5,000, fewer if thin months had less data) and the total credits — expected ~341, and per the findings under ~1,000 even at 3× the estimate. If the running total approaches ~2,000, STOP — that signals a runaway and the remaining free budget should be preserved.

- [ ] **Step 3: Resume if the run crashed**

If the run was interrupted (network drop, Dune error), re-run the exact same command:

```bash
python3 -m bootstrap.run
```

Expected: cached stages log `cache hit` and cost 0 credits; already-loaded mints are skipped; only the unfinished work runs. Confirm the run reaches the `DONE` line.

- [ ] **Step 4: Verify the full dataset**

Run:

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("./storm.db")
def one(q):
    return c.execute(q).fetchone()[0]
total = one("SELECT COUNT(*) FROM historical_graduations")
survived = one("SELECT COUNT(*) FROM historical_graduations WHERE survived=1")
deployer = one("SELECT COUNT(*) FROM historical_graduations "
               "WHERE deployer_wallet IS NOT NULL")
holders = one("SELECT COUNT(*) FROM historical_graduations "
              "WHERE visible_holder_count IS NOT NULL")
denom = max(total, 1)
print(f"total rows           : {total}")
print(f"survived             : {survived} ({100 * survived // denom}%)")
print(f"deployer populated   : {deployer} ({100 * deployer // denom}%)")
print(f"holders populated    : {holders} ({100 * holders // denom}%)")
print("per-month counts (regime coverage):")
for month, n in c.execute(
    "SELECT strftime('%Y-%m', datetime(graduation_time,'unixepoch')) AS m, "
    "COUNT(*) FROM historical_graduations GROUP BY m ORDER BY m"
):
    print(f"  {month}: {n}")
c.close()
PY
```

Expected: `total` is ~5,000; `survived` is a plausible non-degenerate split (neither 0% nor 100%); `deployer populated` is high (the first-class deployer signal — the large majority of rows; only mints with no `pump_call_create`/`_v2` row are NULL); `holders populated` may be partial (the best-effort group — anywhere from 0% to most, depending on timeouts) and that is acceptable; the per-month breakdown shows **every month** Nov 2025 → present with a non-zero count (the stratification requirement — Phase 3 needs ≥2 distinct regimes). If the dataset looks correct, the Phase 2 deliverable is complete.

- [ ] **Step 5: Commit a short completion note**

The dataset itself (`./storm.db`) is a local artifact and is not committed. Record the run's outcome by appending a "Run log" section to `bootstrap/README.md` (replace the bracketed values with the actual numbers from Step 4):

```markdown

## Run log

- Full run completed `<DATE>`: `<TOTAL>` rows in `historical_graduations`,
  `<CREDITS>` Dune credits spent (of the 2,500 free budget).
- Month coverage: `<FIRST-MONTH>` through `<LAST-MONTH>`, every month non-empty.
- Holder distribution populated for `<HOLDERS-PCT>`% of rows (best-effort group).
```

```bash
git add bootstrap/README.md
git commit -m "Record the full historical-graduation ETL run outcome

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-review

### Spec coverage

Every requirement of the design spec (`2026-05-18-historical-bootstrap-design.md`) and the spike findings (`2026-05-18-historical-bootstrap-spike-findings.md`) maps to a task:

| Spec / findings requirement | Where it is delivered |
|---|---|
| One-time Python ETL in `bootstrap/` (spec §2, §4.4) | Tasks 1–10; `bootstrap/` package |
| Standard library only; `pytest` the sole dependency | Task 1 `requirements.txt`; every module imports only stdlib |
| One self-contained `historical_graduations` table, Python-created via `CREATE TABLE IF NOT EXISTS` (spec §4.1) | Task 8 `load.py` `CREATE_TABLE_SQL` / `create_table` |
| No Rust change, no `storm-store` migration | No Rust task exists; stated in Context; `load.py` owns the DDL |
| One flat settled row per graduated token (spec §4.1) | Task 6 `GraduationRecord`; Task 8 table |
| Graduation facts (spec §4.2) | Task 5 `graduations_sql`; Task 6 `parse_graduations` |
| Outcome label `survived` with the 5-SOL rule + raw reserves (spec §4.2, findings §3.1, §4) | Task 5 `outcome_sql`; Task 6 `merge_outcome` |
| Liquidity at ~T0+12h: base/quote reserves + `lp_burned` heuristic (spec §4.2, findings §3.2) | Task 5 `liquidity_sql`; Task 6 `merge_liquidity` |
| Bonding-curve final state (spec §4.2, findings §3.3) | Task 5 `bonding_curve_sql`; Task 6 `merge_bonding_curve` (last trade by slot — findings caveat 4) |
| Contract flags: mint/freeze authority (spec §4.2, findings §3.4) | Task 5 `contract_flags_sql`; Task 6 `merge_contract_flags` (`freeze_authority_present` cohort constant 0) |
| Holder distribution best-effort, NULL on timeout (spec §4.2, §5, findings §3.5) | Task 5 `holders_sql`; Task 6 `merge_holders`; Task 9 `try/except DuneTimeout` + timeout cache marker |
| Deployer signal FIRST-CLASS — wallet, prior launches, age (spec §4.2, findings §3.6) | Task 5 `deployer_sql` (unions `pump_call_create` + `_v2`); Task 6 `merge_deployer`; columns `deployer_wallet/_prior_launches/_age_secs` NOT NULL in Task 8 |
| `pool_supply_fraction`, `creator_bag_fraction` NULL-able (spec §4.2) | Task 6 `GraduationRecord` (default `None`); Task 8 nullable columns |
| SQLite conventions from `0002_survival.sql`: u64 as TEXT, booleans/`survived` as INTEGER (decision) | Task 8 `CREATE_TABLE_SQL`; Task 6 keeps reserves as `str` |
| ~5,000 graduations, month-stratified, sample size configurable (spec §4.3) | Task 2 `sample_size`; Task 7 `stratified_sample`; Task 10 full run |
| Exclude tokens graduated < ~16 days ago (spec §4.3, findings §4) | Task 2 `outcome_settle_days`; Task 9 `_settle_cutoff` feeds `graduations_sql` |
| PumpSwap-era window ~Nov 2025–present (spec §4.3) | Task 2 `window_start`; Task 5 `graduations_sql` lower bound |
| Idempotent + resumable, keyed on `mint` (spec §4.4) | Task 8 `mint PRIMARY KEY` + `ON CONFLICT DO NOTHING`; Task 9 `existing_mints` skip |
| Each Dune stage caches raw results to disk (spec §4.4, §6) | Task 3 `cache.py`; Task 9 `_run_cached_stage` |
| Holder small mint-batches, ~50-mint pilot batch (findings §3.5, §6) | Task 2 `holder_batch_size=50`; Task 9 holder loop |
| Credit safety: 2-min timeout cap, log `execution_cost_credits`, ~341 of 2,500 (decision, findings §5) | Task 4 `poll_until_done` reads `execution_cost_credits`; Task 9 `CreditMeter` |
| Pilot run before the full run (spec §4.4, decision) | Task 9 Steps 3–4 |
| Full ~5,000-token run as a separate later task (decision) | Task 10 |
| `DUNE_API_KEY` from `.env`; add to `.env.example` (decision) | Task 1 `.env.example` edit; Task 2 `load_config` reads env |
| `bootstrap/data/` gitignored (spec §6, decision) | Task 1 `.gitignore` edit |
| `bootstrap/README.md` (decision) | Task 1 (created), Task 10 Step 5 (run log appended) |

### Placeholder scan

No step contains `TODO`, `FIXME`, `...` as elided code, or "similar to Task N". Every code step shows the complete file or the complete test — `transform.py` is shown in full (no `field` import — only `dataclass`); `run.py` is shown in full. The four `<<<PY` heredocs in Tasks 9–10 are complete, runnable verification scripts. The README "Run log" template in Task 10 Step 5 uses `<DATE>`/`<TOTAL>`/etc. as **deliberate fill-in-the-actual-number placeholders for a human-recorded run outcome** — that is intentional documentation, and the step says explicitly to replace them with the run's real numbers; it is not elided code.

### Type and name consistency

- `Config` (frozen dataclass) — defined in `config.py` (Task 2), consumed by `dune_client.py` (Task 4) and `run.py` (Task 9). Every field name used downstream (`dune_api_key`, `dune_base_url`, `sample_size`, `window_start`, `outcome_settle_days`, `event_batch_size`, `flag_batch_size`, `holder_batch_size`, `liquidity_snapshot_hours`, `db_path`, `cache_dir`, `is_pilot`, `survival_min_quote_lamports`) exists on the Task 2 dataclass.
- `GraduationRecord` (dataclass) — defined in `transform.py` (Task 6). Its field set is the exact set written by `load.py`'s `_COLUMNS` (Task 8) and matches `CREATE_TABLE_SQL`'s columns one-to-one (the table also has `loaded_at`, which has a SQL `DEFAULT` and is intentionally not in `_COLUMNS`). `sample.py` (Task 7) reads only `mint` and `graduation_time` — both present.
- `DuneClient` / `DuneError` / `DuneTimeout` — defined in `dune_client.py` (Task 4); `run.py` (Task 9) imports `DuneClient` and `DuneTimeout` and calls `run_sql`, the public method defined in Task 4.
- `cache.py` functions (`cache_path`, `has_cache`, `write_cache`, `read_cache`) — defined in Task 3; `run.py` (Task 9) calls `read_cache` and `write_cache` with the `(cache_dir, stage, payload?, batch?)` signatures from Task 3.
- `queries.py` builders — `graduations_sql(window_start, settle_cutoff)`, `outcome_sql(pools)`, `liquidity_sql(pools)`, `bonding_curve_sql(mints)`, `contract_flags_sql(mints)`, `deployer_sql(mints, max_grad_time)`, `holders_sql(mints, snapshot_time)` — every signature in Task 5 matches every call site in `run.py` (Task 9).
- `transform.py` functions — `parse_graduations(rows)`, `parse_dune_time(value)`, `merge_outcome(records, rows, survival_min_quote_lamports)`, `merge_liquidity(records, rows, withdrawn_pools)`, `merge_bonding_curve(records, rows)`, `merge_contract_flags(records, rows)`, `merge_deployer(records, rows)`, `merge_holders(records, rows)` — signatures in Task 6 match the call sites in `run.py` (Task 9).
- `load.py` — `create_table(conn)`, `load_records(conn, records)`, `existing_mints(conn)` — signatures in Task 8 match the `run.py` call sites (Task 9).
- Stage names are consistent strings across `run.py`'s `_run_cached_stage` calls and the cache: `graduations`, `outcome`, `liquidity`, `bonding_curve`, `contract_flags`, `deployer`, `holders`.

### Items from the spec/findings that could not become a concrete task

All resolvable. Two are worth flagging as deliberate, spec-sanctioned simplifications rather than gaps:

1. **`oldest_signature_age_secs` (live collector field).** Findings §3.6 / §6.2 state it has no Dune equivalent (no `solana.transactions` table on the free tier). It is **not** a column of `historical_graduations`; `deployer_age_secs` (pump.fun-relative wallet age) is the history-native substitute. This is spec §3's "features defined by the data" principle in action — not a gap. Flagged here so Phase 3 knows the deployer-age semantics are pump.fun-relative.
2. **Per-token vs per-batch snapshot timing.** The outcome and liquidity queries embed one IN-list per pool batch; the exact per-pool time window (`[T0+12d, T0+16d]` for the outcome, `≤T0+12h` for liquidity) is not applied inside the SQL — `merge_outcome` / `merge_liquidity` take the *last* event per pool from the batch. For the outcome this yields the correct settled state: the graduations-list query already excludes tokens younger than `outcome_settle_days` (16), so for every sampled token the latest event is at least ~16 days post-graduation — the settled state. For liquidity at T0+12h it is an **approximation** — the latest event, not strictly the last before T0+12h. Spec §5 explicitly accepts approximate snapshot timing ("snapshot timing is approximate (~T0+12h)... Phase 3 must treat this as the rough-but-usable dataset it is"), so this is a sanctioned simplification, documented in `queries.py`'s `liquidity_sql` docstring. A stricter per-pool-window query would need either one query per pool (the 57s-style cost the findings rule out) or a correlated time filter; the batch approximation is the deliberate, findings-aligned choice. The holder stage similarly uses one snapshot time per batch (the batch's earliest T0+12h), noted in `run.py`. These timing semantics are surfaced here so Phase 3 knows exactly what the dataset's "T0+12h" columns mean.
