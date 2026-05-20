# Stop-Loss Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fixed-rule intra-period stop-loss exit strategy to the `model/` backtest engine, backed by a new `intraperiod_snapshots` table (14 daily liquidity snapshots per token), producing a 4th basket result `stop_loss_buy_everything` alongside the 3 existing baselines.

**Architecture:** Additive against the merged `main`. New `intraperiod_snapshots` SQLite table populated by extending the existing Dune ETL with one new query template (one snapshot index per Dune execution, run 14 times). `model/backtest.py` gains an optional `stop_loss_threshold` parameter that, when set, walks the 14 snapshots and exits early on threshold breach. `model/walkforward.py` gains one additional `run_backtest` call. All other modules unchanged.

**Tech Stack:** Python 3.11+, pandas, pytest, Dune Analytics SQL, SQLite, the existing `bootstrap/` ETL infrastructure.

**Spec:** `docs/superpowers/specs/2026-05-20-stop-loss-strategy-design.md` (commit `60ee587`).

**Implementation note — Dune vs Helius.** Spec §4.2 mentions Helius archival RPC as the data source. During plan-writing we discovered the existing Phase 2 ETL infrastructure (`bootstrap/queries.py`, `bootstrap/dune_client.py`, `bootstrap/cache.py`, `bootstrap/load.py`) ALREADY fetches pool reserves at-a-point-in-time via the `outcome_sql` and `liquidity_sql` Dune queries — each takes (pool, time) pairs and returns "the pool's reserves at the latest swap event in a target window." Extending this pattern with a third query template (one per snapshot day) is dramatically simpler than building a Helius archival RPC layer, reuses tested caching/idempotency code, and stays inside the existing Dune free-tier budget (~70 credits per snapshot-day stage × 14 days ≈ ~1000 credits — well under the 2,500/month free allotment). The plan uses Dune.

---

## Workspace

- Branch: `stop-loss-strategy` (already created from `main` at 2026-05-20).
- No worktree — work in the repo root `/home/roberto/solana-storm`.
- Spec committed at `60ee587`.
- Dune API key already in `.env` as `DUNE_API_KEY=...` (used by the existing ETL).
- 4,755 graduations already in `./storm.db` (Phase 2 deliverable, unchanged).

## File structure

| Path | Change |
|---|---|
| `bootstrap/queries.py` | **Modified** — add `intraperiod_snapshot_sql(pairs, day_offset, window_start)` |
| `bootstrap/transform.py` | **Modified** — add `SnapshotRecord` dataclass + `parse_snapshots(rows, index)` |
| `bootstrap/load.py` | **Modified** — add `create_snapshots_table` + `insert_snapshots` + `existing_snapshots` |
| `bootstrap/run_snapshots.py` | **NEW** — orchestrator: loop 14 days, run+cache each Dune stage, insert |
| `bootstrap/tests/test_queries.py` | **Modified** — assert the new SQL template content for a known input |
| `bootstrap/tests/test_transform.py` | **Modified** — assert `parse_snapshots` parses sample rows into typed records |
| `bootstrap/tests/test_load.py` | **Modified** — assert `create_snapshots_table` + idempotent insert |
| `model/config.py` | **Modified** — one new field `stop_loss_threshold: float = 0.5` |
| `model/tests/test_config.py` | **Modified** — defaults assertion updated |
| `model/features.py` | **Modified** — `LEAKAGE_FORBIDDEN` extended with the 28 snapshot column names |
| `model/tests/test_features.py` | **Modified** — new leakage assertion for the 28 columns |
| `model/data.py` | **Modified** — `load_dataframe` LEFT-JOINs `intraperiod_snapshots` when present |
| `model/tests/test_data.py` | **Modified** — fixture seeds the new table; assert the 28 new columns load |
| `model/backtest.py` | **Modified** — `run_backtest` gains optional `stop_loss_threshold`; helper to compute exit |
| `model/tests/test_backtest.py` | **Modified** — new tests for the stop-loss exit path (trigger / no-trigger / NaN-skip / back-compat) |
| `model/walkforward.py` | **Modified** — `_run_one_fold` adds the 4th `run_backtest` call |
| `model/tests/test_walkforward.py` | **Modified** — assert `stop_loss_buy_everything` is in `baseline_results` |
| `model/report.py` | **Modified** — render the new strategy row in the comparison table |
| `model/run.py` | **Modified** — log the stop-loss threshold |
| `model/README.md` | **Modified** — run-log section for the stop-loss strategy result |
| All other `*.py` | **Unchanged** — `bootstrap/run.py`, `bootstrap/dune_client.py`, `bootstrap/cache.py`, `bootstrap/config.py`, `bootstrap/sample.py`, all the `model/` files not listed above |

## Tasks

10 tasks. Tasks 1–6 are pure model-side code changes with synthetic test fixtures — they don't need the real snapshot data. Tasks 7–8 extend the ETL with full unit coverage (still no real data). Task 9 runs the real ETL against Dune. Task 10 runs the end-to-end backtest and writes the README log.

---

### Task 1: Config — `stop_loss_threshold` field

**Files:**
- Modify: `model/config.py` (the `Config` dataclass)
- Modify: `model/tests/test_config.py` (`test_load_config_returns_the_spec_defaults`)

- [ ] **Step 1: Update the defaults test**

In `model/tests/test_config.py`, find the `test_load_config_returns_the_spec_defaults` function and add this assertion at the bottom of the existing block (after `min_curve_sol_lamports`):

```python
    # NEW: stop-loss strategy threshold (spec 10)
    assert cfg.stop_loss_threshold == 0.5
```

- [ ] **Step 2: Run the test — verify it fails**

```bash
cd /home/roberto/solana-storm
python3 -m pytest model/tests/test_config.py::test_load_config_returns_the_spec_defaults -v
```

Expected: FAIL with `AttributeError` on `stop_loss_threshold`.

- [ ] **Step 3: Update model/config.py**

Open `model/config.py`. Add one new field after `min_curve_sol_lamports`, before the `# --- Honest costs ---` section:

```python
    # --- Stop-loss strategy (spec 10) ---
    stop_loss_threshold: float = 0.5  # exit if quote-reserve falls below 50% of entry
```

- [ ] **Step 4: Run the test — verify it passes**

```bash
python3 -m pytest model/tests/test_config.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: 83 passed (no regressions).

- [ ] **Step 6: Commit**

```bash
git add model/config.py model/tests/test_config.py
git commit -m "$(cat <<'EOF'
Task 1: Add Config.stop_loss_threshold (default 0.5)

One new field for the stop-loss strategy's pre-committed threshold:
exit a position if its pool quote-reserve falls below 50% of entry
at any of the 14 daily intra-period snapshots. Spec section 10.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: features.py — `LEAKAGE_FORBIDDEN` extension

**Files:**
- Modify: `model/features.py`
- Modify: `model/tests/test_features.py`

The 28 future-knowledge snapshot columns (`snap_{i}_base_reserve`, `snap_{i}_quote_reserve` for i ∈ 1..14) must NEVER be features. They're added to `LEAKAGE_FORBIDDEN` now — even though Task 3 will only later make them available in the DataFrame — so the no-leakage assertion stays correct end-to-end.

- [ ] **Step 1: Update the leakage assertion in tests**

In `model/tests/test_features.py`, find `test_no_outcome_or_label_column_leaks_into_X`. After it, add a new test:

```python
def test_intraperiod_snapshot_columns_are_in_leakage_forbidden():
    """Spec 11: all 28 snap_*_*_reserve columns are future data; never features."""
    for i in range(1, 15):
        assert f"snap_{i}_base_reserve" in LEAKAGE_FORBIDDEN, (
            f"snap_{i}_base_reserve missing from LEAKAGE_FORBIDDEN"
        )
        assert f"snap_{i}_quote_reserve" in LEAKAGE_FORBIDDEN, (
            f"snap_{i}_quote_reserve missing from LEAKAGE_FORBIDDEN"
        )
```

- [ ] **Step 2: Run the test — verify it fails**

```bash
python3 -m pytest model/tests/test_features.py::test_intraperiod_snapshot_columns_are_in_leakage_forbidden -v
```

Expected: FAIL on the first assertion — `snap_1_base_reserve missing from LEAKAGE_FORBIDDEN`.

- [ ] **Step 3: Extend `LEAKAGE_FORBIDDEN` in model/features.py**

Find the `LEAKAGE_FORBIDDEN` list (currently 5 entries: `positive_return`, `survived`, and three outcome columns). Replace its definition with:

```python
# Columns that must NEVER appear in the feature matrix -- future data
# (outcome reserves, intra-period snapshots) or the label itself. The
# no-leakage test enforces this list. The 28 snap_* columns are added
# pre-emptively for the stop-loss strategy (spec 4.3 / 11) even though
# they're consumed only by the backtest's exit logic, not by any model.
LEAKAGE_FORBIDDEN: List[str] = [
    "positive_return",
    "survived",
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "outcome_checked_at",
] + [
    f"snap_{i}_{kind}_reserve"
    for i in range(1, 15)
    for kind in ("base", "quote")
]
```

- [ ] **Step 4: Run the test — verify it passes**

```bash
python3 -m pytest model/tests/test_features.py -v
```

Expected: every test_features test passes (10 existing + 1 new = 11).

- [ ] **Step 5: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: 84 passed (83 existing + 1 new test).

- [ ] **Step 6: Commit**

```bash
git add model/features.py model/tests/test_features.py
git commit -m "$(cat <<'EOF'
Task 2: Extend LEAKAGE_FORBIDDEN with 28 intra-period snapshot columns

The stop-loss strategy reads snap_{i}_{base,quote}_reserve columns (i=1..14)
in the backtest's exit logic but they must NEVER reach the feature matrix --
they're future data relative to the T0+12h entry instant. Adding them to
LEAKAGE_FORBIDDEN now means the no-leakage test stays correct when Task 3
exposes the columns in the loaded DataFrame.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: data.py — load `intraperiod_snapshots` if present

**Files:**
- Modify: `model/data.py` (the `load_dataframe` function)
- Modify: `model/tests/test_data.py` (a new test that seeds and asserts the joined columns)

`load_dataframe` is the bridge from SQLite to pandas. It currently loads `historical_graduations` and returns a DataFrame indexed by mint. After this task, when `intraperiod_snapshots` exists, it ALSO loads the 14 snapshots per mint and pivots them into 28 paired columns named `snap_{i}_base_reserve`, `snap_{i}_quote_reserve`. When the table is missing or empty, the function returns exactly the same DataFrame as before (back-compat).

- [ ] **Step 1: Add a new test seeding `intraperiod_snapshots`**

In `model/tests/test_data.py`, find the `_CREATE` SQL constant. Below it (before any test function), add the snapshot table DDL and a helper:

```python
_CREATE_SNAPSHOTS = """
CREATE TABLE intraperiod_snapshots (
    mint TEXT NOT NULL,
    snapshot_index INTEGER NOT NULL,
    snapshot_time INTEGER NOT NULL,
    snapshot_slot INTEGER NOT NULL,
    base_reserve TEXT,
    quote_reserve TEXT,
    PRIMARY KEY (mint, snapshot_index)
)
"""


def _seed_snapshots(conn):
    """Seed 2 snapshots on M1 (days 1 and 7), nothing for M2 / M3."""
    conn.execute(_CREATE_SNAPSHOTS)
    rows = [
        # M1: a healthy day-1, a degraded day-7.
        ("M1", 1, 1086400, 510, "1070000000000000", "63000000000"),
        ("M1", 7, 1604800, 590, "1500000000000000", "30000000000"),
    ]
    conn.executemany(
        "INSERT INTO intraperiod_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
```

Then add two new tests at the bottom of the file:

```python
def test_load_dataframe_returns_nan_snapshots_when_table_missing():
    """Back-compat: if intraperiod_snapshots doesn't exist, the 28 columns
    still appear on the loaded frame as all-NaN, so callers can rely on them.
    """
    conn = sqlite3.connect(":memory:")
    _seed(conn)  # historical_graduations only -- NO snapshots table
    df = load_dataframe(conn)
    for i in range(1, 15):
        for kind in ("base", "quote"):
            col = f"snap_{i}_{kind}_reserve"
            assert col in df.columns, f"{col} missing"
            assert df[col].isna().all(), f"{col} should be all NaN"


def test_load_dataframe_joins_snapshot_rows_when_present():
    """When intraperiod_snapshots exists, the 28 columns are populated where
    data is present and NaN elsewhere. Row M1 has snap 1 and 7 only."""
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    _seed_snapshots(conn)
    df = load_dataframe(conn)
    # M1's snap 1 + 7 are populated.
    assert df.loc["M1", "snap_1_base_reserve"] == 1070000000000000.0
    assert df.loc["M1", "snap_1_quote_reserve"] == 63000000000.0
    assert df.loc["M1", "snap_7_base_reserve"] == 1500000000000000.0
    assert df.loc["M1", "snap_7_quote_reserve"] == 30000000000.0
    # M1's other snapshots are NaN.
    assert np.isnan(df.loc["M1", "snap_2_base_reserve"])
    assert np.isnan(df.loc["M1", "snap_14_quote_reserve"])
    # M2's and M3's snapshots are all NaN (no rows seeded).
    for i in range(1, 15):
        assert np.isnan(df.loc["M2", f"snap_{i}_quote_reserve"])
        assert np.isnan(df.loc["M3", f"snap_{i}_base_reserve"])
```

- [ ] **Step 2: Run the new tests — verify they fail**

```bash
python3 -m pytest model/tests/test_data.py -v
```

Expected: BOTH new tests FAIL — current `load_dataframe` doesn't return any `snap_*` columns.

- [ ] **Step 3: Extend model/data.py**

Open `model/data.py`. Read the current `load_dataframe` body to understand its shape (it executes `SELECT * FROM historical_graduations`, builds a DataFrame, converts u64 reserve columns from strings, sorts by `graduation_time`, sets `mint` as the index, returns).

Replace `load_dataframe` with:

```python
def load_dataframe(conn: sqlite3.Connection) -> pd.DataFrame:
    """Read historical_graduations into a DataFrame indexed by mint, with
    every u64-string reserve column converted to numeric (NaN-safe).

    When the intraperiod_snapshots table is present, also LEFT-JOIN 14 daily
    snapshots per mint into 28 paired columns `snap_{i}_base_reserve` and
    `snap_{i}_quote_reserve` for i in 1..14. Missing snapshots load as NaN.
    When the table is missing, the 28 columns appear as all-NaN so callers
    can rely on a stable column set (spec 4.3).
    """
    df = pd.read_sql_query(
        f"SELECT * FROM historical_graduations", conn
    )
    # u64-string -> numeric (existing behavior).
    _U64_COLUMNS = [
        "outcome_base_reserve",
        "outcome_quote_reserve",
        "liq_base_reserve",
        "liq_quote_reserve",
        "curve_real_sol_reserves",
        "curve_real_token_reserves",
        "curve_token_total_supply",
    ]
    for col in _U64_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("graduation_time", kind="stable")
    df = df.set_index("mint")

    # LEFT-JOIN intraperiod snapshots if the table exists.
    snap_df = _load_intraperiod_snapshots(conn, mints=list(df.index))
    df = df.join(snap_df, how="left")

    return df


def _load_intraperiod_snapshots(
    conn: sqlite3.Connection, mints: list[str]
) -> pd.DataFrame:
    """Return a DataFrame indexed by mint with 28 snap_*_*_reserve columns.

    If the intraperiod_snapshots table is missing OR has no rows for the
    given mints, every column is NaN. Otherwise rows are pivoted: each
    (mint, snapshot_index) becomes snap_{i}_base_reserve / snap_{i}_quote_reserve.
    """
    columns = [
        f"snap_{i}_{kind}_reserve"
        for i in range(1, 15)
        for kind in ("base", "quote")
    ]
    empty = pd.DataFrame(index=pd.Index(mints, name="mint"), columns=columns,
                         dtype=float)
    # Existence check.
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='intraperiod_snapshots'"
    )
    if cur.fetchone() is None:
        return empty

    raw = pd.read_sql_query(
        "SELECT mint, snapshot_index, base_reserve, quote_reserve "
        "FROM intraperiod_snapshots",
        conn,
    )
    if raw.empty:
        return empty

    # u64-string -> numeric.
    raw["base_reserve"] = pd.to_numeric(raw["base_reserve"], errors="coerce")
    raw["quote_reserve"] = pd.to_numeric(raw["quote_reserve"], errors="coerce")

    # Pivot to one row per mint with snap_{i}_{kind} columns.
    pivoted = raw.pivot(index="mint", columns="snapshot_index",
                        values=["base_reserve", "quote_reserve"])
    pivoted.columns = [
        f"snap_{int(idx)}_{kind.split('_')[0]}_reserve"
        for kind, idx in pivoted.columns
    ]
    # Align to the requested mints, fill missing.
    aligned = empty.copy()
    aligned.update(pivoted)
    return aligned
```

The `_load_intraperiod_snapshots` helper is the new code. Existing logic in `load_dataframe` is preserved exactly; only the `.join(snap_df, how="left")` line is added at the bottom.

Note: ensure `import pandas as pd` and `import sqlite3` are already at the top (they are, from existing code).

- [ ] **Step 4: Run the tests — verify they pass**

```bash
python3 -m pytest model/tests/test_data.py -v
```

Expected: every existing test still passes, the 2 new tests now pass.

- [ ] **Step 5: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: 86 passed (84 + 2 new).

- [ ] **Step 6: Commit**

```bash
git add model/data.py model/tests/test_data.py
git commit -m "$(cat <<'EOF'
Task 3: load_dataframe LEFT-JOINs intraperiod_snapshots when present

When the intraperiod_snapshots table exists (Task 7 creates it via the
Dune ETL, Task 9 populates it), load_dataframe pivots its rows into 28
paired columns snap_{i}_base_reserve and snap_{i}_quote_reserve for
i in 1..14. When the table is missing, the 28 columns appear as all-NaN
so the rest of the codebase relies on a stable column set.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: backtest.py — `stop_loss_threshold` parameter + exit logic

**Files:**
- Modify: `model/backtest.py` (add the optional parameter; add an exit-computation helper)
- Modify: `model/tests/test_backtest.py` (new tests for the stop-loss path)

When `stop_loss_threshold=None`, behavior is identical to today. When set, each position's exit event is built from the FIRST daily snapshot where `quote_reserve / entry_quote < threshold` (skipping NaN snapshots); if none triggers, the existing outcome-reserve exit is used.

- [ ] **Step 1: Read the existing backtest.py structure**

```bash
sed -n '95,180p' model/backtest.py
```

Note the per-token event-building loop (currently around lines 100–130) where `outcome_checked_at`, `outcome_base_reserve`, `outcome_quote_reserve` are read to form the EXIT event tuple. Also note `exit_fill` is called when the simulator processes an exit event (around lines 160–170). The task adds a HELPER that computes (exit_time, exit_base, exit_quote) per token, replacing the inline reads — and which optionally walks snapshots.

- [ ] **Step 2: Add the failing tests**

In `model/tests/test_backtest.py`, find an existing test that sets up a backtest fixture and study how a "row" is constructed (likely a DataFrame with at least `graduation_time`, `outcome_checked_at`, `outcome_base_reserve`, `outcome_quote_reserve`, `liq_base_reserve`, `liq_quote_reserve`). Then add these new tests (using the same fixture pattern; if no helper exists, copy the simplest pattern from elsewhere in the file):

```python
import numpy as np
import pandas as pd

from model.backtest import run_backtest


def _stop_loss_frame():
    """Three tokens with intra-period snapshots that exercise stop-loss paths.

    T1: snap_3 quote is 40% of entry -> stop-loss triggers on day 3.
    T2: snap_5 quote is 80% of entry -> no trigger; exits at outcome.
    T3: snap_2 is NaN, snap_3 is 30% of entry -> NaN skipped, triggers day 3.
    """
    base_cols = {
        "graduation_time": [1000, 2000, 3000],
        "graduation_slot": [400, 500, 600],
        "outcome_base_reserve": [2.0e15, 1.0e15, 2.0e15],
        "outcome_quote_reserve": [5.0e10, 5.0e10, 5.0e10],
        "outcome_checked_at": [1000 + 14*86400, 2000 + 14*86400, 3000 + 14*86400],
        "liq_base_reserve": [1.0e15, 1.0e15, 1.0e15],
        "liq_quote_reserve": [1.0e11, 1.0e11, 1.0e11],   # entry quote
    }
    # Add 28 snap columns initially all-NaN, then populate the exercised cases.
    for i in range(1, 15):
        base_cols[f"snap_{i}_base_reserve"] = [np.nan, np.nan, np.nan]
        base_cols[f"snap_{i}_quote_reserve"] = [np.nan, np.nan, np.nan]
    df = pd.DataFrame(base_cols, index=pd.Index(["T1", "T2", "T3"], name="mint"))
    # T1: snap_3 quote = 0.4e11 < 0.5 * 1.0e11 -> triggers.
    df.loc["T1", "snap_3_quote_reserve"] = 0.4e11
    df.loc["T1", "snap_3_base_reserve"] = 1.8e15
    # T2: snap_5 quote = 0.8e11 -> no trigger.
    df.loc["T2", "snap_5_quote_reserve"] = 0.8e11
    df.loc["T2", "snap_5_base_reserve"] = 1.2e15
    # T3: snap_2 NaN, snap_3 quote = 0.3e11 < 0.5 -> triggers on day 3, not 2.
    df.loc["T3", "snap_3_quote_reserve"] = 0.3e11
    df.loc["T3", "snap_3_base_reserve"] = 2.0e15
    return df


def test_stop_loss_threshold_none_is_back_compatible():
    """stop_loss_threshold=None -> behavior identical to today (exits at outcome)."""
    df = _stop_loss_frame()
    basket = {"T1", "T2", "T3"}
    result = run_backtest(
        df, basket=basket, slot_count=3,
        initial_bankroll=100.0, dex_fee_rate=0.0025,
        stop_loss_threshold=None,
    )
    for pos in result.positions:
        # Every exit time is at the outcome_checked_at (T0 + 14 days).
        row = df.loc[pos.mint]
        assert pos.exit_time == int(row["outcome_checked_at"])


def test_stop_loss_triggers_at_first_below_threshold_snapshot():
    """T1 should exit on day 3 (snap_3 quote 40% < 50% threshold)."""
    df = _stop_loss_frame()
    result = run_backtest(
        df, basket={"T1"}, slot_count=1,
        initial_bankroll=100.0, dex_fee_rate=0.0025,
        stop_loss_threshold=0.5,
    )
    assert len(result.positions) == 1
    pos = result.positions[0]
    # Exit time is graduation_time + 3 * 86400 (day 3 snapshot).
    assert pos.exit_time == 1000 + 3 * 86400


def test_stop_loss_does_not_trigger_when_no_snapshot_below_threshold():
    """T2 should exit at outcome (snap_5 at 80% of entry -- above threshold)."""
    df = _stop_loss_frame()
    result = run_backtest(
        df, basket={"T2"}, slot_count=1,
        initial_bankroll=100.0, dex_fee_rate=0.0025,
        stop_loss_threshold=0.5,
    )
    pos = result.positions[0]
    assert pos.exit_time == int(df.loc["T2", "outcome_checked_at"])


def test_stop_loss_skips_nan_snapshots():
    """T3: snap_2 is NaN -> skipped; trigger fires on snap_3 (the first valid below threshold)."""
    df = _stop_loss_frame()
    result = run_backtest(
        df, basket={"T3"}, slot_count=1,
        initial_bankroll=100.0, dex_fee_rate=0.0025,
        stop_loss_threshold=0.5,
    )
    pos = result.positions[0]
    assert pos.exit_time == 3000 + 3 * 86400
```

- [ ] **Step 3: Run the tests — verify they fail**

```bash
python3 -m pytest model/tests/test_backtest.py -v -k stop_loss
```

Expected: collection error or argument errors — `run_backtest` doesn't accept `stop_loss_threshold` yet.

- [ ] **Step 4: Update model/backtest.py**

Open `model/backtest.py`. Find the per-token event-building loop (currently around lines 100–135). It reads `outcome_checked_at`, `outcome_base_reserve`, `outcome_quote_reserve` directly per row. Replace those reads with a call to a new `_compute_exit_event` helper, and add a new `stop_loss_threshold` parameter to `run_backtest`.

**Add the helper** (just BEFORE the `run_backtest` function, near the bottom of the file):

```python
def _compute_exit_event(
    row: pd.Series,
    entry_quote_reserve: float,
    stop_loss_threshold: float | None,
) -> tuple[int, float, float]:
    """Compute the (exit_time, exit_base, exit_quote) for one position.

    If `stop_loss_threshold` is None, returns the outcome triple as before
    (outcome_checked_at + outcome_*_reserve).

    Otherwise walks the 14 daily snapshots in chronological order. At the
    first snapshot where BOTH `snap_i_base_reserve` and `snap_i_quote_reserve`
    are non-NaN AND `snap_i_quote_reserve < threshold * entry_quote_reserve`,
    returns that snapshot's (time, base, quote). Snapshot-times are derived
    deterministically as `graduation_time + i * 86_400`. If no snapshot triggers,
    falls back to the outcome triple.

    NaN snapshots are skipped -- they neither trigger nor veto.
    """
    if stop_loss_threshold is not None:
        floor = stop_loss_threshold * entry_quote_reserve
        grad_time = int(row["graduation_time"])
        for i in range(1, 15):
            base = row[f"snap_{i}_base_reserve"]
            quote = row[f"snap_{i}_quote_reserve"]
            if math.isnan(base) or math.isnan(quote):
                continue
            if quote < floor:
                return grad_time + i * 86_400, float(base), float(quote)
    # Fall back to the existing outcome triple.
    exit_t = int(row["outcome_checked_at"])
    return exit_t, float(row["outcome_base_reserve"]), float(row["outcome_quote_reserve"])
```

**Update `run_backtest`** to accept the new parameter. Find the function signature (currently `def run_backtest(test_df, basket, slot_count, initial_bankroll, dex_fee_rate)`) and change to:

```python
def run_backtest(
    test_df: pd.DataFrame,
    basket: Set[str],
    slot_count: int,
    initial_bankroll: float,
    dex_fee_rate: float,
    stop_loss_threshold: float | None = None,
) -> BacktestResult:
```

(If `Set` isn't already imported from typing, add `from typing import Set` at the top.)

**Replace the inline outcome reads in the event-building loop** (around line 116):

Find the section that currently looks like:

```python
        exit_t = int(row["outcome_checked_at"])
        if exit_t <= entry_t:
            # degenerate horizon: force the exit one second after entry...
            exit_t = entry_t + 1
```

Replace with:

```python
        exit_t, exit_base, exit_quote = _compute_exit_event(
            row,
            entry_quote_reserve=float(row["liq_quote_reserve"]),
            stop_loss_threshold=stop_loss_threshold,
        )
        if exit_t <= entry_t:
            # degenerate horizon: force the exit one second after entry so the
            # event ordering invariant (entry strictly precedes exit) holds.
            exit_t = entry_t + 1
```

Then the EXIT-event processing block (which currently reads `outcome_base_reserve`/`outcome_quote_reserve` directly from the row) needs the new triple too. Locate the section near line 163–170 that calls `exit_fill(base_reserve=float(row["outcome_base_reserve"]), quote_reserve=float(row["outcome_quote_reserve"]), ...)`.

This block needs the precomputed exit_base / exit_quote. The cleanest path: store the exit triple in a per-token dict at event-build time, keyed by mint, and look it up at exit-process time. Add the dict:

Near the top of `run_backtest`, before the per-token loop, add:

```python
    exit_triples: dict[str, tuple[int, float, float]] = {}
```

In the per-token event-build loop (where you just added the `_compute_exit_event` call), after the `exit_t` line, add:

```python
        exit_triples[mint] = (exit_t, exit_base, exit_quote)
```

In the EXIT-event processing block (where `exit_fill` is called), replace the `outcome_*_reserve` reads with the looked-up triple. Find:

```python
            sol_out = exit_fill(
                base_amount_in=position_base_amount,
                base_reserve=float(row["outcome_base_reserve"]),
                quote_reserve=float(row["outcome_quote_reserve"]),
                fee_rate=dex_fee_rate,
            )
```

Replace with:

```python
            _, exit_base, exit_quote = exit_triples[mint]
            sol_out = exit_fill(
                base_amount_in=position_base_amount,
                base_reserve=exit_base,
                quote_reserve=exit_quote,
                fee_rate=dex_fee_rate,
            )
```

(If `math` isn't imported, add `import math` near the top — it's already imported.)

- [ ] **Step 5: Run the new tests — verify they pass**

```bash
python3 -m pytest model/tests/test_backtest.py -v
```

Expected: every existing backtest test still passes (back-compat: `stop_loss_threshold=None` is identical to before), and the 4 new stop-loss tests pass.

- [ ] **Step 6: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: 90 passed (86 + 4 new).

- [ ] **Step 7: Commit**

```bash
git add model/backtest.py model/tests/test_backtest.py
git commit -m "$(cat <<'EOF'
Task 4: run_backtest gains optional stop_loss_threshold

When set, each position's exit event is computed from the first daily
snapshot where quote-reserve drops below threshold * entry-quote. NaN
snapshots are skipped. If no snapshot triggers, falls back to the outcome
exit as before. When the parameter is None (the default), behavior is
identical to today -- existing baselines stay unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: walkforward.py — add the 4th `run_backtest` call

**Files:**
- Modify: `model/walkforward.py` (`_run_one_fold` body)
- Modify: `model/tests/test_walkforward.py` (assert the new strategy result)

- [ ] **Step 1: Update the walk-forward test**

In `model/tests/test_walkforward.py`, find `test_run_walkforward_produces_per_fold_results`. The loop body iterates fold_results. Add the following assertion alongside the existing `baseline_results` checks:

```python
        # NEW (spec 6): the stop-loss strategy is a 4th key in baseline_results.
        assert "stop_loss_buy_everything" in fold_result.baseline_results, (
            "stop_loss_buy_everything missing from fold's baseline_results"
        )
```

If the test's `wf_frame()` fixture builds a DataFrame without the `snap_*` columns, extend it to include them (all-NaN — the stop-loss exit will fall through to the outcome triple, so the result is identical to the existing buy_everything). Find the `wf_frame()` helper and add this just before the `return` statement:

```python
    # Add 28 NaN snapshot columns so backtest's stop-loss path is reachable
    # (with all-NaN it falls through to the outcome exit; behavior matches
    # the no-stop-loss buy_everything baseline).
    for i in range(1, 15):
        df[f"snap_{i}_base_reserve"] = float("nan")
        df[f"snap_{i}_quote_reserve"] = float("nan")
```

- [ ] **Step 2: Run the test — verify it fails**

```bash
python3 -m pytest model/tests/test_walkforward.py::test_run_walkforward_produces_per_fold_results -v
```

Expected: FAIL on the new assertion — `stop_loss_buy_everything missing from fold's baseline_results`.

- [ ] **Step 3: Update model/walkforward.py**

Open `model/walkforward.py`. Find the `_run_one_fold` function and locate the `baseline_results = { ... }` dict (currently 3 entries: `buy_everything`, `random_basket`, `heuristic_basket`). Add a 4th entry. The full replacement dict:

```python
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
        # NEW (spec 6): same membership as buy_everything, stop-loss exit.
        "stop_loss_buy_everything": run_backtest(
            test_df, basket=buy_everything(test_df),
            slot_count=config.slot_count,
            initial_bankroll=config.initial_bankroll,
            dex_fee_rate=config.dex_fee_rate,
            stop_loss_threshold=config.stop_loss_threshold,
        ),
    }
```

Note: the existing `_bt` helper doesn't accept `stop_loss_threshold`; the new entry calls `run_backtest` directly. This keeps `_bt` minimal — only one call needs the new parameter.

- [ ] **Step 4: Run the test — verify it passes**

```bash
python3 -m pytest model/tests/test_walkforward.py -v
```

Expected: all walkforward tests pass.

- [ ] **Step 5: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: 90 passed (no regressions; the assertion now passes).

- [ ] **Step 6: Commit**

```bash
git add model/walkforward.py model/tests/test_walkforward.py
git commit -m "$(cat <<'EOF'
Task 5: Add stop_loss_buy_everything to _run_one_fold baselines

One additional run_backtest call: same membership as buy_everything, exit
via the stop-loss rule with threshold from Config.stop_loss_threshold. The
3 existing baselines stay as-is (hold-to-horizon); the 4th entry is the
candidate the decision gate evaluates against the 3 unchanged baselines.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: report.py — render the new strategy line

**Files:**
- Modify: `model/report.py`

The report's comparison table already renders one line per entry in `baseline_results`. Adding `"stop_loss_buy_everything"` to that dict (Task 5) should make it appear automatically IF the report iterates the dict's keys. Verify by reading the relevant section of `report.py` and confirming it does. If yes, the report needs no change for THIS line — the assertion is automatic. If the report hardcodes the 3 baseline names instead of iterating, update it.

The decision-gate logic ALSO needs to switch its candidate from the model basket to `stop_loss_buy_everything`. The gate compares the model basket's total return against the 3 baselines; the new candidate is the stop-loss strategy.

- [ ] **Step 1: Read the report's comparison-table section**

```bash
grep -n "buy_everything\|baseline_results\|heuristic_basket\|gate\|Decision" model/report.py | head -30
```

Note whether the comparison table iterates `baseline_results` (good — automatic) or hardcodes the 3 names (needs editing). Also locate the decision-gate code section.

- [ ] **Step 2: Update `_render_markdown` (or wherever the candidate vs baselines logic lives)**

For the COMPARISON TABLE: if the existing render iterates `baseline_results.items()`, no change is needed — the new strategy appears automatically. If it hardcodes names, add `"stop_loss_buy_everything"` to the list/dict it iterates.

For the DECISION GATE: change the "candidate" from the model basket to `stop_loss_buy_everything`. The candidate's total return must STRICTLY exceed each of the 3 hold-to-horizon baselines' total returns. Find the gate clause that currently compares the model basket and change the LHS to `stop_loss_total_return = baseline_results["stop_loss_buy_everything"].total_return` (or however the report accesses it). Compare against `buy_everything`, `random_basket`, and `heuristic_basket` only — NOT against the model basket (which is reported but not part of the baseline set).

The plan can't show the exact diff without reading the file, but the gate's structure is roughly:

```python
beats_baselines = (
    candidate_total_return > baseline_results["buy_everything"].total_return
    and candidate_total_return > baseline_results["random_basket"].total_return
    and candidate_total_return > baseline_results["heuristic_basket"].total_return
)
```

After this task, `candidate_total_return` is the `stop_loss_buy_everything` result, not the model basket. The model basket still appears in the comparison table (the report renders it from `model_result`, separate from `baseline_results`), labeled as a "comparison point — previous iteration's candidate."

- [ ] **Step 3: Add a clarifying line to the report markdown above the gate verdict**

Wherever the gate verdict is written (the `**Decision gate:** PASS/FAIL.` line), prepend a clarifying preamble:

```python
md.append("**Candidate strategy:** `stop_loss_buy_everything` "
          "(filtered universe + 50% stop-loss exit on 14 daily snapshots).")
md.append("")
md.append("**Baselines (hold-to-horizon):** `buy_everything`, "
          "`random_basket`, `heuristic_basket`.")
md.append("")
md.append("**Comparison-only:** `model_basket` (previous iteration's "
          "calibrated positive_return classifier; not part of this gate).")
md.append("")
```

(Adjust to the report's existing append style — `md.append(...)` vs string concatenation vs whatever it uses.)

- [ ] **Step 4: Run the report-related tests**

```bash
python3 -m pytest model/tests/test_report.py -v
```

Expected: tests still pass. If a test asserts the OLD candidate-is-model-basket gate logic, update it to expect the new candidate.

- [ ] **Step 5: Run the full model test suite**

```bash
python3 -m pytest model/ -v
```

Expected: 90 passed.

- [ ] **Step 6: Commit**

```bash
git add model/report.py model/tests/test_report.py
git commit -m "$(cat <<'EOF'
Task 6: Report renders stop_loss_buy_everything; gate evaluates it

The new strategy line appears in the comparison table; the decision gate's
candidate switches from the model basket to stop_loss_buy_everything. The
model basket is still reported as a comparison point (previous iteration's
candidate) but is NOT in the gate's baseline set -- the gate compares the
stop-loss strategy against the 3 hold-to-horizon baselines.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(If only `model/report.py` was modified, drop `model/tests/test_report.py` from the `git add`.)

---

### Task 7: bootstrap ETL — `intraperiod_snapshot_sql` + `SnapshotRecord` + load functions

**Files:**
- Modify: `bootstrap/queries.py` (add `intraperiod_snapshot_sql`)
- Modify: `bootstrap/transform.py` (add `SnapshotRecord` dataclass + `parse_snapshots`)
- Modify: `bootstrap/load.py` (add `create_snapshots_table`, `insert_snapshots`, `existing_snapshots`)
- Modify: `bootstrap/tests/test_queries.py`
- Modify: `bootstrap/tests/test_transform.py`
- Modify: `bootstrap/tests/test_load.py`

These are the building blocks for the orchestrator in Task 8. Fully unit-testable with synthetic Dune-shaped dicts — no network calls in tests.

- [ ] **Step 1: Write the failing query test**

In `bootstrap/tests/test_queries.py`, after the existing `outcome_sql` tests, add:

```python
def test_intraperiod_snapshot_sql_contains_pool_pair_and_day_offsets():
    """The template should declare the (pool, grad_time) pairs in a VALUES
    clause and BETWEEN the trade-time on (grad + N day, grad + (N+1) day)."""
    from bootstrap.queries import intraperiod_snapshot_sql
    sql = intraperiod_snapshot_sql(
        pairs=[("POOL_A", "2026-01-01 00:00:00")],
        snapshot_day_offset=3,
        window_start="2025-11-01",
    )
    assert "POOL_A" in sql
    assert "2026-01-01 00:00:00" in sql
    assert "INTERVAL '3' DAY" in sql
    assert "INTERVAL '4' DAY" in sql
    assert "2025-11-01" in sql
    # Confirm the same buy/sell event union + ranked CTE structure as outcome_sql.
    assert "pump_amm_evt_buyevent" in sql
    assert "pump_amm_evt_sellevent" in sql
    assert "ROW_NUMBER() OVER" in sql


def test_intraperiod_snapshot_sql_returns_the_expected_columns():
    """SELECT clause must yield pool_address, base_reserve, quote_reserve,
    event_time, event_slot -- matching parse_snapshots' expectation."""
    from bootstrap.queries import intraperiod_snapshot_sql
    sql = intraperiod_snapshot_sql(
        pairs=[("POOL_A", "2026-01-01 00:00:00")],
        snapshot_day_offset=1,
    )
    assert "AS pool_address" in sql
    assert "AS base_reserve" in sql
    assert "AS quote_reserve" in sql
    assert "AS event_time" in sql
    assert "AS event_slot" in sql
```

- [ ] **Step 2: Write the failing transform test**

In `bootstrap/tests/test_transform.py`, after the existing tests, add:

```python
def test_parse_snapshots_builds_typed_records():
    from bootstrap.transform import parse_snapshots
    rows = [
        {
            "pool_address": "POOL_A",
            "base_reserve": "1070000000000000",
            "quote_reserve": "63000000000",
            "event_time": "2026-01-02 03:00:00",
            "event_slot": 510,
        },
        {
            "pool_address": "POOL_B",
            "base_reserve": "85093814600000",
            "quote_reserve": "20732018000",
            "event_time": "2026-01-02 04:00:00",
            "event_slot": 511,
        },
    ]
    records = parse_snapshots(rows, snapshot_index=1)
    assert len(records) == 2
    by_pool = {r.pool_address: r for r in records}
    a = by_pool["POOL_A"]
    assert a.snapshot_index == 1
    assert a.base_reserve == "1070000000000000"
    assert a.quote_reserve == "63000000000"
    assert a.snapshot_slot == 510
    # event_time parsed to Unix seconds.
    assert a.snapshot_time == 1767322800  # 2026-01-02 03:00:00 UTC


def test_parse_snapshots_returns_empty_for_no_rows():
    from bootstrap.transform import parse_snapshots
    assert parse_snapshots([], snapshot_index=5) == []
```

- [ ] **Step 3: Write the failing load test**

In `bootstrap/tests/test_load.py`, after existing tests, add:

```python
def test_create_snapshots_table_is_idempotent():
    import sqlite3
    from bootstrap.load import create_snapshots_table
    conn = sqlite3.connect(":memory:")
    create_snapshots_table(conn)
    create_snapshots_table(conn)  # second call should not raise
    # Confirm schema.
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name='intraperiod_snapshots'"
    )
    assert cur.fetchone() is not None


def test_insert_snapshots_writes_rows_keyed_by_pool_and_mint():
    """insert_snapshots takes mint-keyed records; we look them up via the
    historical_graduations table (mint <-> pool_address)."""
    import sqlite3
    from bootstrap.load import create_snapshots_table, insert_snapshots
    from bootstrap.transform import SnapshotRecord
    conn = sqlite3.connect(":memory:")
    create_snapshots_table(conn)
    records = [
        SnapshotRecord(
            mint="M1", snapshot_index=1, snapshot_time=1086400,
            snapshot_slot=510, base_reserve="1070000000000000",
            quote_reserve="63000000000",
        ),
        SnapshotRecord(
            mint="M2", snapshot_index=1, snapshot_time=2086400,
            snapshot_slot=511, base_reserve=None, quote_reserve=None,
        ),
    ]
    insert_snapshots(conn, records)
    rows = conn.execute(
        "SELECT mint, snapshot_index, base_reserve, quote_reserve "
        "FROM intraperiod_snapshots ORDER BY mint"
    ).fetchall()
    assert rows == [
        ("M1", 1, "1070000000000000", "63000000000"),
        ("M2", 1, None, None),
    ]


def test_existing_snapshots_returns_indices_already_loaded_per_mint():
    """existing_snapshots returns a dict[mint, set[int]] for resumability."""
    import sqlite3
    from bootstrap.load import (
        create_snapshots_table, existing_snapshots, insert_snapshots,
    )
    from bootstrap.transform import SnapshotRecord
    conn = sqlite3.connect(":memory:")
    create_snapshots_table(conn)
    insert_snapshots(conn, [
        SnapshotRecord("M1", 1, 1, 1, "100", "10"),
        SnapshotRecord("M1", 3, 1, 1, "100", "10"),
        SnapshotRecord("M2", 2, 1, 1, "100", "10"),
    ])
    loaded = existing_snapshots(conn)
    assert loaded == {"M1": {1, 3}, "M2": {2}}
```

- [ ] **Step 4: Run the tests — verify they all fail**

```bash
python3 -m pytest bootstrap/tests/test_queries.py bootstrap/tests/test_transform.py bootstrap/tests/test_load.py -v
```

Expected: 5 new tests fail with ImportError / AttributeError on `intraperiod_snapshot_sql`, `parse_snapshots`, `SnapshotRecord`, `create_snapshots_table`, `insert_snapshots`, `existing_snapshots`.

- [ ] **Step 5: Add the new query template to bootstrap/queries.py**

Append after `liquidity_sql`:

```python
def intraperiod_snapshot_sql(
    pairs: Iterable[tuple],
    snapshot_day_offset: int,
    window_start: str = "2025-11-01",
) -> str:
    """Pool reserves at the latest trade inside [T0+Nd, T0+(N+1)d].

    Same shape as `outcome_sql` and `liquidity_sql`: takes (pool, grad_time)
    pairs, returns one row per pool with the LATEST swap event's resulting
    reserves inside the per-token day-N window. A pool with no trade in
    that window does not appear in the result -- the orchestrator inserts
    NULL reserves so the row still exists.

    Args:
        pairs: list of (pool_address, graduation_time_string) pairs.
        snapshot_day_offset: integer N >= 1; the snapshot is day N after
            graduation, in [T0+Nd, T0+(N+1)d].
        window_start: ISO date floor for partition pruning on the (very
            large) event tables.
    """
    values = _sql_values_pairs(pairs)
    next_day = snapshot_day_offset + 1
    return f"""
WITH targets(pool, grad_time) AS (
    VALUES
    {values}
),
events AS (
    SELECT t.pool, e.pool_base_token_reserves, e.pool_quote_token_reserves,
           e.evt_block_time, e.evt_block_slot
    FROM pumpdotfun_solana.pump_amm_evt_buyevent e
    JOIN targets t ON e.pool = t.pool
    WHERE e.evt_block_time >= TIMESTAMP '{window_start}'
      AND e.evt_block_time BETWEEN t.grad_time + INTERVAL '{snapshot_day_offset}' DAY
                               AND t.grad_time + INTERVAL '{next_day}' DAY
    UNION ALL
    SELECT t.pool, e.pool_base_token_reserves, e.pool_quote_token_reserves,
           e.evt_block_time, e.evt_block_slot
    FROM pumpdotfun_solana.pump_amm_evt_sellevent e
    JOIN targets t ON e.pool = t.pool
    WHERE e.evt_block_time >= TIMESTAMP '{window_start}'
      AND e.evt_block_time BETWEEN t.grad_time + INTERVAL '{snapshot_day_offset}' DAY
                               AND t.grad_time + INTERVAL '{next_day}' DAY
),
ranked AS (
    SELECT pool, pool_base_token_reserves, pool_quote_token_reserves,
           evt_block_time, evt_block_slot,
           ROW_NUMBER() OVER (
               PARTITION BY pool ORDER BY evt_block_time DESC
           ) AS rn
    FROM events
)
SELECT pool                       AS pool_address,
       pool_base_token_reserves   AS base_reserve,
       pool_quote_token_reserves  AS quote_reserve,
       evt_block_time             AS event_time,
       evt_block_slot             AS event_slot
FROM ranked
WHERE rn = 1
""".strip()
```

- [ ] **Step 6: Add the SnapshotRecord dataclass + parse function to bootstrap/transform.py**

Add at the top, alongside the `GraduationRecord` dataclass:

```python
@dataclass
class SnapshotRecord:
    """One row of intraperiod_snapshots -- a per-day pool reserve sample."""
    mint: str
    snapshot_index: int               # 1..14
    snapshot_time: int                # Unix seconds
    snapshot_slot: int                # Solana slot of the latest swap event
    base_reserve: Optional[str]       # u64 as str; None if no event in window
    quote_reserve: Optional[str]      # u64 as str; None if no event in window
```

Then add the parse function (after `parse_graduations`):

```python
def parse_snapshots(
    rows: List[dict], snapshot_index: int
) -> List[SnapshotRecord]:
    """Parse Dune-shaped snapshot rows into SnapshotRecords.

    The rows are pool-keyed (Dune returns by pool). The orchestrator joins
    them to mints via the historical_graduations table. This function does
    NOT join -- it only types each row.
    """
    records: List[SnapshotRecord] = []
    for row in rows:
        records.append(SnapshotRecord(
            mint="",  # populated by the orchestrator
            snapshot_index=snapshot_index,
            snapshot_time=parse_dune_time(row["event_time"]),
            snapshot_slot=int(row["event_slot"]),
            base_reserve=str(row["base_reserve"]) if row.get("base_reserve") is not None else None,
            quote_reserve=str(row["quote_reserve"]) if row.get("quote_reserve") is not None else None,
        ))
        records[-1].mint = str(row["pool_address"])  # temporarily pool; orchestrator remaps
    return records
```

Note: `parse_snapshots` initially stores the pool address in `.mint` as a placeholder. The Task-8 orchestrator remaps pool→mint via a dict before insertion. This keeps `parse_snapshots` a pure transformation (no DB lookup).

The test on lines 8-9 of Step 2 asserts `.mint` would be the pool address from the row; update the test accordingly. Actually — looking again at Step 2's test, it checks `r.pool_address`. **Rename the test's `r.pool_address` access**: the dataclass doesn't have a `pool_address` field. The test in Step 2 should be updated:

In `bootstrap/tests/test_transform.py`'s `test_parse_snapshots_builds_typed_records`, replace `by_pool = {r.pool_address: r for r in records}` and the subsequent `.pool_address` accesses with `.mint` (since the placeholder pool address is stored in `.mint`). The relevant lines become:

```python
    by_pool = {r.mint: r for r in records}   # .mint stores the pool address before orchestrator remap
    a = by_pool["POOL_A"]
```

(Apply this rename to the test code from Step 2 before running.)

- [ ] **Step 7: Add the load functions to bootstrap/load.py**

Append after the existing graduation-loading code (at the bottom of the file):

```python
_SNAPSHOTS_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS intraperiod_snapshots (
    mint            TEXT NOT NULL,
    snapshot_index  INTEGER NOT NULL,
    snapshot_time   INTEGER NOT NULL,
    snapshot_slot   INTEGER NOT NULL,
    base_reserve    TEXT,
    quote_reserve   TEXT,
    PRIMARY KEY (mint, snapshot_index)
)
""".strip()

_SNAPSHOTS_INSERT_SQL = (
    "INSERT INTO intraperiod_snapshots "
    "(mint, snapshot_index, snapshot_time, snapshot_slot, base_reserve, quote_reserve) "
    "VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(mint, snapshot_index) DO NOTHING"
)


def create_snapshots_table(conn: sqlite3.Connection) -> None:
    """Create intraperiod_snapshots if it does not already exist."""
    conn.execute(_SNAPSHOTS_CREATE_SQL)


def insert_snapshots(conn: sqlite3.Connection, records):
    """Idempotently insert SnapshotRecord rows; existing rows are kept."""
    rows = [
        (r.mint, r.snapshot_index, r.snapshot_time, r.snapshot_slot,
         r.base_reserve, r.quote_reserve)
        for r in records
    ]
    conn.executemany(_SNAPSHOTS_INSERT_SQL, rows)
    conn.commit()


def existing_snapshots(conn: sqlite3.Connection) -> dict:
    """Return dict[mint, set[int]] of (mint, snapshot_index) pairs already
    in the table -- used by the orchestrator to resume after a crash."""
    cur = conn.execute(
        "SELECT mint, snapshot_index FROM intraperiod_snapshots"
    )
    result: dict = {}
    for mint, idx in cur:
        result.setdefault(mint, set()).add(int(idx))
    return result
```

- [ ] **Step 8: Run all the bootstrap tests — verify they pass**

```bash
python3 -m pytest bootstrap/tests/test_queries.py bootstrap/tests/test_transform.py bootstrap/tests/test_load.py -v
```

Expected: every existing bootstrap test still passes; the 5 new tests now pass.

- [ ] **Step 9: Run the full repo test suite — catch regressions**

```bash
python3 -m pytest bootstrap/ model/ -v
```

Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add bootstrap/queries.py bootstrap/transform.py bootstrap/load.py \
  bootstrap/tests/test_queries.py bootstrap/tests/test_transform.py \
  bootstrap/tests/test_load.py
git commit -m "$(cat <<'EOF'
Task 7: Bootstrap ETL gets intraperiod_snapshot query + transform + load

A new Dune query template intraperiod_snapshot_sql(pairs, day_offset, window_start)
follows the same (pool, grad_time)-pair pattern as outcome_sql / liquidity_sql,
restricted to a per-token day-N window. A SnapshotRecord dataclass + parse_snapshots
transform shape the Dune rows into typed records. New load.py functions create the
intraperiod_snapshots table, idempotently insert rows, and report existing-loaded
(mint, snapshot_index) pairs for resumability.

The orchestrator that ties these together is Task 8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: bootstrap orchestrator — `run_snapshots.py`

**Files:**
- Create: `bootstrap/run_snapshots.py`
- (Optional) `bootstrap/tests/test_run_snapshots.py` — only if the orchestrator has non-trivial pure logic to test

This is the orchestrator that loops over snapshot indices 1..14, runs each Dune query with caching, transforms the rows, joins pools→mints, and inserts. It mirrors the structure of `bootstrap/run.py` (the existing graduations orchestrator).

- [ ] **Step 1: Create the orchestrator file**

Create `bootstrap/run_snapshots.py`:

```python
"""The intraperiod-snapshots ETL orchestrator (Task 8 of the stop-loss plan).

Wires: config -> read existing graduations -> for each snapshot index 1..14,
run one cached Dune query (the intraperiod_snapshot_sql template) -> remap
pools to mints -> idempotent insert into intraperiod_snapshots.

Like bootstrap/run.py, this is idempotent and resumable: each (mint,
snapshot_index) already present in the table is skipped; each per-day Dune
query is disk-cached, so a re-run never re-spends Dune credits on stages
already completed.

Usage:
    python3 -m bootstrap.run_snapshots                # all 14 days, all mints
    python3 -m bootstrap.run_snapshots --pilot        # first 50 mints, all 14 days
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List

from bootstrap import cache, queries, transform
from bootstrap.config import load_config
from bootstrap.dune_client import DuneClient
from bootstrap.load import (
    create_snapshots_table, existing_snapshots, insert_snapshots,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("bootstrap.run_snapshots")

_NUM_SNAPSHOT_DAYS = 14


def _fmt_grad_time(unix_secs: int) -> str:
    """Unix seconds -> Dune 'YYYY-MM-DD HH:MM:SS' UTC string."""
    return datetime.fromtimestamp(int(unix_secs), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _load_pool_to_mint(conn: sqlite3.Connection) -> Dict[str, str]:
    """Build a pool_address -> mint map from historical_graduations."""
    rows = conn.execute(
        "SELECT mint, pool_address FROM historical_graduations"
    ).fetchall()
    return {pool: mint for mint, pool in rows}


def _load_pool_grad_pairs(
    conn: sqlite3.Connection, pilot: bool
) -> List[tuple]:
    """List of (pool_address, grad_time_str) tuples for all (or piloted) graduations."""
    sql = (
        "SELECT pool_address, graduation_time FROM historical_graduations "
        "ORDER BY graduation_time"
    )
    if pilot:
        sql += " LIMIT 50"
    return [
        (pool, _fmt_grad_time(grad_time))
        for pool, grad_time in conn.execute(sql).fetchall()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Intra-period snapshots ETL"
    )
    parser.add_argument("--pilot", action="store_true",
                        help="run on a 50-mint pilot subset")
    parser.add_argument("--db", default=None,
                        help="path to the SQLite DB (default: ./storm.db)")
    args = parser.parse_args()

    config = load_config(db_path=args.db) if args.db else load_config()
    conn = sqlite3.connect(config.db_path)

    log.info("creating intraperiod_snapshots table if missing")
    create_snapshots_table(conn)

    log.info("loading graduation pairs and pool->mint map")
    pairs = _load_pool_grad_pairs(conn, pilot=args.pilot)
    pool_to_mint = _load_pool_to_mint(conn)
    log.info("graduations to query: %d", len(pairs))

    loaded = existing_snapshots(conn)
    log.info("existing snapshots: %d mints already loaded",
             sum(1 for mints in loaded.values() if len(mints) > 0))

    client = DuneClient(api_key=config.dune_api_key)

    for day in range(1, _NUM_SNAPSHOT_DAYS + 1):
        sql = queries.intraperiod_snapshot_sql(
            pairs, snapshot_day_offset=day,
            window_start=config.window_start,
        )
        stage_name = f"snapshot_{day:02d}"
        log.info("running Dune stage %s (day %d / %d)",
                 stage_name, day, _NUM_SNAPSHOT_DAYS)

        cache_key = cache.stage_key(stage_name, sql)
        rows = cache.load_cached(config, cache_key)
        if rows is None:
            rows = client.execute(sql)
            cache.save_cached(config, cache_key, rows)
            log.info("stage %s fetched %d rows (cached)", stage_name, len(rows))
        else:
            log.info("stage %s loaded %d rows from cache", stage_name, len(rows))

        records = transform.parse_snapshots(rows, snapshot_index=day)
        # Remap .mint (currently holds pool address) -> the true mint.
        unmapped = 0
        for r in records:
            mint = pool_to_mint.get(r.mint)
            if mint is None:
                unmapped += 1
                continue
            r.mint = mint
        if unmapped:
            log.warning("stage %s: %d records had no pool->mint mapping (dropped)",
                        stage_name, unmapped)

        records = [r for r in records if r.mint in pool_to_mint.values() or pool_to_mint.get(r.mint) is not None]
        # Filter to records whose mint is in the pool_to_mint map's values.
        valid_mints = set(pool_to_mint.values())
        records = [r for r in records if r.mint in valid_mints]

        insert_snapshots(conn, records)
        log.info("stage %s inserted %d records", stage_name, len(records))

    log.info("intraperiod_snapshots ETL complete")


if __name__ == "__main__":
    main()
```

The `cache.stage_key` / `cache.load_cached` / `cache.save_cached` calls assume the existing `bootstrap/cache.py` exposes those functions (it does — they back `_run_cached_stage` in `bootstrap/run.py`). If the actual function names differ, adapt accordingly.

- [ ] **Step 2: Confirm the orchestrator imports correctly**

```bash
python3 -c "from bootstrap.run_snapshots import main; print('import ok')"
```

Expected: `import ok`. If you get an ImportError on `cache.stage_key` / `cache.load_cached` / `cache.save_cached` — those names are guesses; open `bootstrap/cache.py` and use the actual function names. The Phase 2 `bootstrap/run.py` uses these via `_run_cached_stage`; you can read its internals to confirm the cache API.

- [ ] **Step 3: Run the full test suite — confirm no regressions**

```bash
python3 -m pytest bootstrap/ model/ -v
```

Expected: all green (the new file isn't unit-tested; it's exercised by Task 9's real run).

- [ ] **Step 4: Commit**

```bash
git add bootstrap/run_snapshots.py
git commit -m "$(cat <<'EOF'
Task 8: Add bootstrap/run_snapshots.py orchestrator

Loops over snapshot indices 1..14, runs each Dune query with disk caching,
parses + remaps pool addresses to mints, inserts into intraperiod_snapshots.
Idempotent and resumable -- existing (mint, snapshot_index) pairs are
preserved by the ON CONFLICT DO NOTHING insert; each per-day Dune stage is
cached, so a re-run never re-spends Dune credits on stages already complete.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Run the ETL — populate `intraperiod_snapshots`

This is the real-world data acquisition. It executes 14 Dune queries against the live API.

- [ ] **Step 1: Pilot run — small batch validates the pipeline end-to-end**

```bash
cd /home/roberto/solana-storm
python3 -m bootstrap.run_snapshots --pilot 2>&1 | tee /tmp/storm-snapshots-pilot.log
```

Expected: the pilot runs all 14 Dune queries for ~50 mints; total runtime a few minutes. Log shows "stage snapshot_NN inserted X records" for each day. If any stage errors out (Dune API timeout, syntax error), STOP and investigate before the full run. Note the total credits consumed (should be a small fraction of the 2,500/month free quota).

Inspect the resulting table:

```bash
sqlite3 ./storm.db <<SQL
.headers on
.mode column
SELECT snapshot_index, COUNT(*) AS rows, SUM(CASE WHEN base_reserve IS NULL THEN 1 ELSE 0 END) AS nulls
FROM intraperiod_snapshots
GROUP BY snapshot_index
ORDER BY snapshot_index;
SQL
```

Expected: 14 rows in the output, one per snapshot_index. Each row shows ~50 records (with a fraction of NULLs for pools with no trade in that day's window).

- [ ] **Step 2: Full run — all 4,755 graduations × 14 days**

If the pilot passed:

```bash
python3 -m bootstrap.run_snapshots 2>&1 | tee /tmp/storm-snapshots-full.log
```

Expected: completes in ~30–60 minutes (14 Dune queries × ~30–60s each + processing). The Dune client may need to retry slow queries; the existing `bootstrap/dune_client.py` handles retries.

If a stage hits the Dune query timeout, the log will show the failure; re-run the script — it'll skip cached stages and retry just the failed one.

- [ ] **Step 3: Verify the populated table**

```bash
sqlite3 ./storm.db <<SQL
SELECT 'total snapshots' AS metric, COUNT(*) AS value FROM intraperiod_snapshots
UNION ALL
SELECT 'distinct mints', COUNT(DISTINCT mint) FROM intraperiod_snapshots
UNION ALL
SELECT 'mints with all 14 snapshots',
       (SELECT COUNT(*) FROM (
           SELECT mint FROM intraperiod_snapshots
           GROUP BY mint HAVING COUNT(*) = 14
       ))
UNION ALL
SELECT 'rows with NULL base_reserve',
       SUM(CASE WHEN base_reserve IS NULL THEN 1 ELSE 0 END)
FROM intraperiod_snapshots;
SQL
```

Expected: total snapshots ~66,570 (4,755 × 14), distinct mints ~4,755, mints-with-all-14 close to 4,755 (each token has a row per day; rows with NULL reserves mean "no trade in that day's window" — that's expected for abandoned pools).

- [ ] **Step 4: No commit — `./storm.db` is gitignored**

This task produces data, not code. The DB file is gitignored. The next task (Task 10) uses the populated table directly.

Note in your status message: number of rows inserted, number of NULL-only rows, total Dune credits consumed.

---

### Task 10: End-to-end backtest + README run-log

Run the actual stop-loss strategy backtest against the populated DB, read the report, and write the run-log section.

- [ ] **Step 1: Run the full backtest**

```bash
cd /home/roberto/solana-storm
python3 -m model.run 2>&1 | tee /tmp/storm-stop-loss-run.log
```

Expected: the run completes; log ends with `report written: model/report/report.md`. Note the figures:
- pre-filter / post-filter row counts (same as the pivot)
- folds run
- `stop_loss_buy_everything` total return, max drawdown
- per-regime breakdown
- decision-gate verdict

- [ ] **Step 2: Read the regenerated report**

```bash
cat model/report/report.md
```

Note these EXACT figures (do NOT round):
- `stop_loss_buy_everything` total return, max drawdown
- Each of the 3 hold-to-horizon baselines' total return and max drawdown
- Per-regime model return (for the stop-loss strategy, if the report's regime split now reflects it)
- The gate's PASS / FAIL verdict and the one-sentence reason

The model basket (pivot's empty-basket carry-over) should still be in the comparison table for reference; it does NOT count as a baseline for the gate.

- [ ] **Step 3: Append the run-log section to model/README.md**

Open `model/README.md`. Append a new section AFTER the existing pivot run-log section. Replace every `<FILL>` with the EXACT figure from the report:

```markdown

## Stop-Loss Strategy run — 2026-05-20 (`stop-loss-strategy`)

A no-ML iteration: hold every token in the filtered universe (the pivot's
`buy_everything` membership), exit on the first daily snapshot where pool
quote-reserve drops below 50% of entry quote-reserve. Backed by a new
`intraperiod_snapshots` table populated from Dune Analytics (~67k rows;
spec `2026-05-20-stop-loss-strategy-design.md`).

Produced by commit `<FILL>` (Task 10 HEAD) with `random_seed = 20260519`
and `stop_loss_threshold = 0.5`. Re-run with `python3 -m model.run` to
reproduce, assuming `intraperiod_snapshots` is populated.

- Pre-filter rows: <FILL>
- Post-filter rows: <FILL>
- Folds run: <FILL>

Total return (stop_loss_buy_everything): <FILL>%
Max drawdown (stop_loss_buy_everything): <FILL>%

Hold-to-horizon baselines:
- `buy_everything`   total <FILL>%   max_dd <FILL>%   (or "max_dd not computed" if report.py still omits)
- `random_basket`    total <FILL>%   max_dd <FILL>%
- `heuristic_basket` total <FILL>%   max_dd <FILL>%

Comparison only (not in the gate's baseline set):
- `model_basket` (pivot's calibrated positive_return) total <FILL>%

Per-regime stop-loss return:
- mania (Feb / Mar / Apr): <FILL>%
- quiet (Nov / Dec / Jan / May): <FILL>%

**Decision gate:** `<PASS or FAIL>`. <One sentence explaining which gate
clause was or wasn't met — e.g., "Did not beat random_basket on total
return" or "Max drawdown <X>% exceeded the 40% ceiling".>
```

Same discipline as the pivot's README section: the figures must match the report exactly; the verdict must match the report's verdict.

- [ ] **Step 4: Final test sweep**

```bash
python3 -m pytest bootstrap/ model/ -v
```

Expected: every test still green.

- [ ] **Step 5: Commit**

```bash
git add model/README.md
git commit -m "$(cat <<'EOF'
Task 10: Run the stop-loss strategy end-to-end and log the result

Captures the actual numbers from the regenerated report against the same
pre-committed decision gate: total return and max drawdown for the new
stop_loss_buy_everything strategy and the 3 hold-to-horizon baselines, the
per-regime split, and the gate's PASS/FAIL verdict. The pivot's model
basket appears as a comparison point, not in the baseline set.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## After all tasks complete

Per the subagent-driven-development skill: dispatch a final whole-branch code reviewer over the diff (spec `60ee587` → HEAD). The final review verifies:

- Spec coverage end-to-end (each §3 in-scope item maps to one or more commits).
- TDD discipline (every new behavior was test-driven).
- No leakage regressions (LEAKAGE_FORBIDDEN still complete and now includes the 28 snapshot columns).
- Back-compat: with `stop_loss_threshold=None`, the backtest behaves identically to before.
- The new run-log in README is consistent with `model/report/report.md`.
- The Dune ETL extension is idempotent and resumable.

Then invoke `superpowers:finishing-a-development-branch` for the merge / PR / keep / discard choice.

---

## Self-review notes (resolved inline before saving)

1. **Spec coverage.** Walked §3 of the spec end-to-end against the tasks. New `intraperiod_snapshots` table + bootstrap script (Tasks 7–9). Optional `stop_loss_threshold` on `run_backtest` (Task 4). New strategy alongside 3 unchanged baselines (Task 5). New Config field (Task 1). `LEAKAGE_FORBIDDEN` extension (Task 2). Report rendering (Task 6). End-to-end run (Task 10). All covered. No gaps.

2. **Placeholder scan.** Every code step contains the actual code. The `<FILL>` placeholders in Task 10 are deliberate — they are filled with actual report figures at execution time and the task explicitly instructs the implementer to do so. These are not the skill's forbidden static placeholders.

3. **Type consistency.** `SnapshotRecord` has the same field names in its dataclass definition (Task 7), the load functions (Task 7), and the orchestrator (Task 8). The `intraperiod_snapshots` schema's column names (`mint`, `snapshot_index`, `snapshot_time`, `snapshot_slot`, `base_reserve`, `quote_reserve`) are consistent in Task 7 (load DDL), Task 7 (dataclass), Task 3 (the SELECT in data.py), and Task 9 (the verification SQL). The `snap_{i}_base_reserve` / `snap_{i}_quote_reserve` column names are consistent in Task 2 (LEAKAGE_FORBIDDEN), Task 3 (data.py pivot), Task 4 (backtest.py reads), and Task 5 (walkforward fixture).

4. **Implementation-note transparency.** Spec §4.2 names Helius RPC; this plan uses Dune instead. The plan's preamble calls this out so the implementer knows the deviation is intentional and the user sees it on review.

5. **Risk: Task 6 specificity.** The report.py change depends on what the file's current decision-gate logic looks like — the plan can't show the exact diff without reading the file. The task instructs the implementer to grep, read the relevant sections, and apply the conceptual change (candidate is now `stop_loss_buy_everything`, baseline set is the 3 unchanged baselines, model basket is comparison-only). If the implementer hits ambiguity, they should report it; the reviewer catches any deviation.
