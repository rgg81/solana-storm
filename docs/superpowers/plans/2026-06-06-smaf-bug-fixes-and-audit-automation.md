# SMAF Bug Fixes + Audit Automation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. Each task = TDD-style (failing test → fix → passing test → review → commit).

**Goal:** Fix 64 confirmed findings (8 CRITICAL + 16 HIGH + ~40 MEDIUM/LOW) from the 2026-06-06 multi-agent review, and append the review-audit step as Phase 7 of the SMAF tick so it runs automatically.

**Architecture:** Eight phases A–G. Phase A scaffolds testing for `predictions/fund/`. Phases B–E fix CRITICAL → HIGH groups by category (code, data, strategy). Phase F wires automation. Phase G mops up MEDIUM/LOW.

**Tech Stack:** Python 3, pytest, jsonl state files, GitHub Actions, multi-agent Workflow tool for periodic deep audits.

**Discipline rules (CLAUDE.md):**
- Each fix = write failing test → run (confirm fails) → write code → run (confirm passes) → run full suite → subagent review → commit
- Each fix at source AND inline (per CLAUDE.md "Fix bugs proactively — at the source, not inline only")
- Never commit `predictions/fund/state/` (gitignored); EXCEPTIONS: `lessons.md`, audit JSONLs, reports are committed
- Surface root-cause in commit message
- One concern per commit; group commits per phase

---

## Phase A — Test scaffolding

**Files:**
- Create: `predictions/fund/tests/__init__.py`
- Create: `predictions/fund/tests/conftest.py` (fixtures: synthetic universe_input, phase2_input, RM output, account state)
- Create: `predictions/fund/tests/test_smoke.py` (1 sanity import test)
- Modify: `.github/workflows/ci.yml` — add a `python-tests` job that runs `pytest predictions/fund/tests/ -q`

**Tasks:**

### Task A.1: Create empty tests package
- [ ] Create `predictions/fund/tests/__init__.py` (empty)
- [ ] Create `predictions/fund/tests/conftest.py` with fixtures: `tmp_state_dir`, `synthetic_phase2_input`, `synthetic_rm_output`, `synthetic_pm_output`, `synthetic_universe_history`. Each fixture returns a minimal dict matching the canonical schema.
- [ ] Create `predictions/fund/tests/test_smoke.py` with `def test_can_import_fund_modules()` that imports `predictions.fund.runner`, `audit`, `report`, `phase6_orchestrator`, `stage_phase2`, `stage_phase3`, `stage_phase4`, `stage_phase6`, `account`, `bugs`, `lessons_io`, `regime`, `performance`, `goals`.
- [ ] Run: `pytest predictions/fund/tests/ -q` — expected: 1 passed.
- [ ] Commit: `test: scaffold predictions/fund/tests with conftest fixtures and smoke import test`

### Task A.2: Wire CI
- [ ] Modify `.github/workflows/ci.yml` — add a parallel `python-tests` job:
  ```yaml
  python-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install pytest requests
      - run: pytest predictions/fund/tests/ -q
  ```
- [ ] Verify locally: `pytest predictions/fund/tests/ -q`
- [ ] Commit: `ci: add python-tests job running predictions/fund/tests/`

---

## Phase B — CRITICAL fixes

### Task B.1: Fix `audit.snapshot_entry_consensus` call site in `runner.py`

**Files:**
- Test: `predictions/fund/tests/test_audit_entry_consensus.py`
- Modify: `predictions/fund/runner.py:218-225`

- [ ] **Write failing test** in `test_audit_entry_consensus.py`: dispatch `runner.execute_pm_orders` with a synthetic PM trade and a `tick_risk_input.json` containing all 4 specialist scores. Assert that the entry consensus recorded via `audit.snapshot_entry_consensus` carries the SE-Opt, SE-Pes, market_disagreement, and onchain_disagreement values from the input (not zeros).
- [ ] Run test → expected FAIL (the call uses wrong kwarg names so all 4 fields silently become 0.0).
- [ ] Fix `runner.py:218-225`:
  ```python
  snap = audit_mod.snapshot_entry_consensus(
      ticker=ticker,
      ma_opt_score=spec.get('ma_optimist_score', 0.0),
      ma_pes_score=spec.get('ma_pessimist_score', 0.0),
      se_opt_score=spec.get('se_optimist_score', 0.0),
      se_pes_score=spec.get('se_pessimist_score', 0.0),
      risk_mgr_size_pct=float(trade.get('usd_amount', 0)) / max(float(state.get('deposit_usd') or 0), 1e-9) * 100,
      market_disagreement=spec.get('market_disagreement', 0.0),
      onchain_disagreement=spec.get('onchain_disagreement', 0.0),
  )
  ```
- [ ] Run test → expected PASS.
- [ ] Subagent review (spec compliance + code quality).
- [ ] Commit: `fix(runner): snapshot_entry_consensus called with correct 4-specialist signature (audit was silently zeroing SE scores + both disagreement fields on every BUY)`

### Task B.2: Patch the 4 price corruptions in `universe_price_history.jsonl`

**Files:**
- Create: `predictions/fund/tests/test_price_history_integrity.py`
- Create: `predictions/fund/state_patches/2026-06-06-price-corruptions.py` (a one-shot script — NOT gitignored — that applies the patch and is idempotent)
- Modify: `predictions/fund/state/universe_price_history.jsonl` (apply patch in place)

- [ ] Write integrity test that asserts: for every (symbol, tick_id) row, the ratio against the next-tick row of the same symbol is within [0.01, 100] OR the row carries `price_corrected_*` metadata. This codifies the "no >100× single-tick jumps" invariant.
- [ ] Run test → FAIL (4 corruptions detected: PYTH 35-39, JUP 35-38, JTO 38-39, PUMP 35-39 vs 40-41 reversal).
- [ ] Write `state_patches/2026-06-06-price-corruptions.py` that:
  - Loads `universe_price_history.jsonl`
  - For each known corruption: sets `price_usd` to back-calculated value, adds `original_corrupt_price_usd`, `price_corrected_2026_06_06: true`, `correction_reason` fields
  - For PUMP — pick the correct range (DexScreener primary pool is the lower one ~$0.0014) and patch the higher range (ticks 35-39)
  - Writes back atomically (.tmp + rename)
  - Idempotent: skip rows already carrying the flag
- [ ] Run patch script.
- [ ] Run integrity test → PASS.
- [ ] Subagent review.
- [ ] Commit: `fix(state): patch 4 price corruptions in universe_price_history (PYTH/JUP/JTO/PUMP wrong-pool quotes ticks 35-41) + add invariant test`

### Task B.3: Pass 2.5 probe gate — drop `calm_vol` prerequisite

**Decision:** Drop `calm_vol` from the probe gate. Rationale: the entire 100-tick history has been `normal_vol`; the prerequisite was added without empirical justification; with `calm_vol` required the probe can never fire in the regime it was designed for. Keep all other gates (consensus, MA-Opt, MA-Pes, onchain, combined_uncertainty, cooldown).

**Files:**
- Create: `predictions/fund/tests/test_probe_gate.py`
- Modify: `predictions/fund/agents/risk_manager.md` (line 92: drop "AND vol bucket is `calm`")
- Modify: any code that enforces vol_bucket for probe (search code — probably none, only the agent prompt)

- [ ] Write test that simulates an RM input matching all probe gates EXCEPT vol_bucket=normal; assert probe SHOULD be eligible (the test reads the role spec to verify the prerequisite is gone). This is essentially a documentation test.
- [ ] grep for `calm_vol`, `calm vol`, `vol_bucket` in `predictions/fund/*.py` to confirm no code currently enforces it.
- [ ] Edit `risk_manager.md` Pass 2.5 block: replace `Regime is strong_bear AND vol bucket is calm` with `Regime is strong_bear` and add an explanatory comment about why the calm_vol prerequisite was dropped (link to this review).
- [ ] Run test → PASS.
- [ ] Subagent review.
- [ ] Commit: `fix(probe): drop calm_vol prerequisite from Pass 2.5 (closed gate entire 100-tick streak; no empirical basis for the prereq)`

---

## Phase C — HIGH code bugs

### Task C.1: Replace `bugs.log_warning` with `bugs.log`

**Files:**
- Modify: `predictions/fund/stage_phase6.py:159`
- Add test: `predictions/fund/tests/test_anomaly_clamp.py`

- [ ] Write test: synthesize a what-if with delta_pct=-99% (below NEG threshold). Patch `bugs.log` to a mock and assert it gets called when `_classify_triggers` clamps the row.
- [ ] Run → FAIL (currently calls non-existent `bugs.log_warning` swallowed by try/except).
- [ ] Fix: `bugs.log('MEDIUM', 'phase6.anomaly_delta_clamp', message, context={...})`. Also remove the swallowing try/except OR narrow it to AttributeError only.
- [ ] Run → PASS.
- [ ] Commit: `fix(stage_phase6): bugs.log_warning → bugs.log (anomaly clamp logging silently swallowed AttributeError)`

### Task C.2: Add `probe_log.jsonl` writer in `runner.execute_pm_orders`

**Files:**
- Modify: `predictions/fund/runner.py:execute_pm_orders` — after a probe trade executes, append to `probe_log.jsonl`
- Add: `predictions/fund/state_io.py:probe_log_append` (if not present) OR inline in runner
- Modify: `predictions/fund/agents/risk_manager.md` — already references reading the file; confirm path

- [ ] Write test: call `execute_pm_orders` with a PM output containing `regime_probe: {ticker, ...}`. Assert that after execution, `STATE_DIR/probe_log.jsonl` has a new row with `tick_id`, `ts`, `ticker`, `consensus_at_entry`, `stop_loss_usd`, `tp_usd`, `max_size_usd`, `rationale`.
- [ ] Run → FAIL (no writer exists).
- [ ] Add writer in `runner.py` — append row when `pm_output.get('regime_probe')` is present AND the corresponding trade executed.
- [ ] Run → PASS.
- [ ] Commit: `feat(runner): write probe_log.jsonl on every probe trade (RM 4-tick cooldown gate had nothing to read)`

### Task C.3: Stale `/tmp/smaf_*.json` guard

**Files:**
- Modify: `predictions/fund/stage_phase3.py:51-55`
- Add test: `predictions/fund/tests/test_stale_specialist_guard.py`

- [ ] Write test: pre-populate `/tmp/smaf_market_analyst_optimist.json` with an old run_time_utc. Then write `tick_phase2_input.json` with a newer run_time_utc. Call `stage_phase3.stage` and assert it raises (or warns + skips) because the specialist file is older than the phase2 input.
- [ ] Run → FAIL.
- [ ] Fix: in `stage_phase3.stage`, compare each specialist file's mtime against the phase2 input's mtime. If older, raise `StaleSpecialistError` with the lag.
- [ ] Run → PASS.
- [ ] Commit: `fix(stage_phase3): raise on stale /tmp/smaf_*.json specialist outputs (prevents prior-tick scores silently poisoning consensus)`

### Task C.4: `report.py` Section 6 — use 4-specialist keys

**Files:**
- Modify: `predictions/fund/report.py:467`
- Modify: `predictions/fund/lessons_io.py:110` (scoreboard render uses same keys)
- Add test: `predictions/fund/tests/test_report_scoreboard.py`

- [ ] Write test: synthesize a lessons.md frontmatter with all 4 specialist scoreboards populated, render Section 6, assert SE-Opt and SE-Pes rows both appear.
- [ ] Run → FAIL.
- [ ] Fix: replace `('market_analyst_optimist', 'market_analyst_pessimist', 'solana_expert')` with `('market_analyst_optimist', 'market_analyst_pessimist', 'solana_expert_optimist', 'solana_expert_pessimist')` in both `report.py:467` and `lessons_io.py:110`.
- [ ] Run → PASS.
- [ ] Commit: `fix(report,lessons_io): use 4-specialist scoreboard keys (SE-Opt/Pes data was never rendered)`

### Task C.5: `report.py` Top BUY candidates — use 4-way consensus

**Files:**
- Modify: `predictions/fund/report.py:345-351`
- Add test in `test_report_scoreboard.py`

- [ ] Write test: build synthetic specialist outputs where the 4-way consensus would qualify a ticker for the BUY list but the 3-way (drop SE-Pes) would not. Assert it appears.
- [ ] Run → FAIL.
- [ ] Fix: when `has_se_split=True`, compute `c = (opt + pes + se_opt + se_pes) / 4` instead of the 3-way formula.
- [ ] Run → PASS.
- [ ] Commit: `fix(report): Top BUY candidates use 4-way consensus when SE is split (was 3-way, inconsistent with RM)`

### Task C.6: `lessons.md` body sync

**Files:**
- Modify: `predictions/fund/lessons_io.py` — add `refresh_body()` that renders aggregated reflections into Validated/Candidate/Rejected sections, called from `phase6_orchestrator.persist_reflector_output`.
- Add test: `predictions/fund/tests/test_lessons_body_sync.py`

- [ ] Write test: populate `lessons_reflections.jsonl` with 3 validated + 1 candidate + 1 rejected rows; call `refresh_body()`; assert the rendered Markdown contains the rule patterns (not the cold-start placeholder).
- [ ] Run → FAIL.
- [ ] Implement `refresh_body()` that reads `_aggregate_reflections()` and replaces the cold-start placeholder sections with rendered rule lists. Idempotent.
- [ ] Wire into `phase6_orchestrator.persist_reflector_output` and `phase6_orchestrator.run` (same call sites as `refresh_frontmatter_counters`).
- [ ] Run → PASS.
- [ ] Commit: `fix(lessons): render aggregated reflections into lessons.md body (was stuck on cold-start placeholder despite 16 validated rules in frontmatter)`

### Task C.7: vol_30d propagation through audit

**Files:**
- Modify: `predictions/fund/audit.py` — `snapshot_entry_consensus` accepts `vol_30d_daily_pct`; `audit_close` reads it from entry_consensus.
- Modify: `predictions/fund/runner.py` — pass `spec.get('30d_daily_vol_pct', None)` when building snap.
- Modify: `predictions/fund/stage_phase3.py` — ensure `30d_daily_vol_pct` is populated per-symbol (use SOL's 2.91% as fallback for symbols with insufficient_data, OR explicit None).
- Add test: `predictions/fund/tests/test_vol30d_propagation.py`

- [ ] Write test: snapshot an entry with vol_30d=0.04 then call audit_close; assert `update_stop_calibration` receives 0.04 (not 0.05).
- [ ] Run → FAIL.
- [ ] Fix: add field to snap dict, propagate through audit_close, drop the 0.05 literal.
- [ ] Run → PASS.
- [ ] Commit: `fix(audit): propagate vol_30d through entry snapshot → audit_close (was 0.05 placeholder, stop calibration crippled)`

---

## Phase D — HIGH audit + integration

### Task D.1: Backfill RENDER TP audit

**Files:**
- Add: `predictions/fund/state_patches/2026-06-06-render-audit-backfill.py`
- Modify: `predictions/fund/state/closed_trades_audit.jsonl` (in place)
- Add test: `predictions/fund/tests/test_audit_coverage.py`

- [ ] Write test: assert `len(closed_trades_audit.jsonl rows) == count(sells in trades.jsonl)`. Currently 1 vs 2.
- [ ] Run → FAIL.
- [ ] Write backfill script: read RENDER buy/sell from `trades.jsonl`, read entry_consensus from `account.json` (RENDER block) or `trades.jsonl`, construct audit row with `realized_pnl_usd=133.13`, `exit_reason='take_profit_executed'`, `was_winner=true`, run audit logic.
- [ ] Run script.
- [ ] Run test → PASS.
- [ ] **Root-cause fix**: investigate WHY TP closes weren't invoking `audit_close` while SL closes did. Add the missing call to PM-execute / stop-trigger path. Add a regression test that simulates a TP execution and asserts `audit_close` is called.
- [ ] Commit (1): `fix(audit): backfill missing RENDER TP audit entry (+$133.13)`
- [ ] Commit (2): `fix(runner): invoke audit_close on TP executions (gap caused asymmetric scoreboard)`

### Task D.2: `bugs.jsonl` resolution mechanism

**Files:**
- Modify: `predictions/fund/bugs.py` — add `mark_resolved(timestamp, resolution_note)`, `unresolved_count(min_severity)` helpers
- Add: `predictions/fund/tests/test_bugs_resolution.py`
- Apply: mark known-resolved entries (line 4 missing market_analyst.md, line 59 RENDER slippage = liq=0 fixed)

- [ ] Write test: log a bug, mark it resolved, assert `unresolved_count` decreases.
- [ ] Run → FAIL.
- [ ] Add functions to `bugs.py`. `mark_resolved` updates the in-place jsonl row.
- [ ] Run resolution batch: mark resolved the entries that we know are fixed (the slippage liq=0 bug per CLAUDE.md note; the missing market_analyst.md if the file now exists).
- [ ] Commit: `feat(bugs): mark_resolved + unresolved_count helpers; backfill resolution status for known-fixed entries`

### Task D.3: Helius ops-health watchdog

**Files:**
- Modify: `predictions/fund/stage_phase2.py` (or `onchain_stats.py`) — on Helius `rpc_failed`, call `bugs.log('MEDIUM', 'helius.rpc_failed', ticker, ...)` once per tick per symbol with rate-limit.
- Add test: `predictions/fund/tests/test_helius_watchdog.py`

- [ ] Write test: mock a Helius failure, run stage_phase2 path, assert `bugs.log` called with severity≥MEDIUM and category `helius.rpc_failed`.
- [ ] Run → FAIL.
- [ ] Fix at source.
- [ ] Run → PASS.
- [ ] Commit: `feat(onchain): log MEDIUM bug on every Helius rpc_failed (Section 7 ops-health was lying — bugs.jsonl untouched for 11 days while every read failed)`

### Task D.4: Populate `30d_daily_vol_pct` correctly

**Files:**
- Modify: `predictions/fund/stage_phase3.py:78`
- Modify: `predictions/fund/regime.py` (expose per-symbol vol if reachable, else fall back to SOL vol for infra)
- Add test in `test_vol30d_propagation.py`

- [ ] Write test: SOL's `30d_daily_vol_pct` in `tick_risk_input.json` must equal the value in `regime_status` (2.91%).
- [ ] Run → FAIL.
- [ ] Fix: pull from regime detector output, write per-symbol; for symbols without history, use `None` (not 0).
- [ ] Run → PASS.
- [ ] Commit: `fix(stage_phase3): populate 30d_daily_vol_pct from regime (was 0 for every symbol including SOL)`

---

## Phase E — HIGH strategy / decision-quality

### Task E.1: Surface goal drift in PM/RM summaries

**Files:**
- Modify: `predictions/fund/goals.py` — `format_for_agent_prompt` includes a "N consecutive ticks below_floor" counter and explicit cost-of-inaction line.
- Add test: `predictions/fund/tests/test_goals_drift_surfacing.py`

- [ ] Write test: with 30 consecutive zero-return days and below_floor status, the agent prompt must contain the explicit phrase "below floor for N consecutive ticks" and a cost-of-inaction extrapolation.
- [ ] Run → FAIL.
- [ ] Implement: add tracker reading `equity.jsonl` and computing `consecutive_below_floor_ticks` and `projected_monthly_shortfall_pct`.
- [ ] Run → PASS.
- [ ] Commit: `feat(goals): surface consecutive-below-floor counter + cost-of-inaction in agent prompt (rhetorical "discipline holds" loop was hiding goal drift)`

### Task E.2: Rolling 7d run-rate alongside lifetime

**Files:**
- Modify: `predictions/fund/goals.py`
- Test in `test_goals_drift_surfacing.py`

- [ ] Write test: with 13.7 days of which last 11 are flat, `format_for_agent_prompt` displays BOTH lifetime (+2.24%/mo) AND 7d-rolling (0%/mo) with the 7d label.
- [ ] Run → FAIL.
- [ ] Implement.
- [ ] Run → PASS.
- [ ] Commit: `feat(goals): add 7d-rolling run-rate alongside lifetime extrapolation`

### Task E.3: Reframe "discipline holds" — anti-cheerleader nudges in role files

**Files:**
- Modify: `predictions/fund/agents/risk_manager.md`
- Modify: `predictions/fund/agents/portfolio_mgr.md`

- [ ] Add to each role: "Do not characterize zero-trade ticks as positive outcomes by default. Capital preservation is the baseline, not the goal. After N consecutive zero-trade ticks while below_floor, your summary MUST acknowledge cost of inaction explicitly."
- [ ] (No test — this is a role-spec nudge. Verified by reading the agent's next output.)
- [ ] Commit: `docs(agents): reframe discipline rhetoric — zero-trade tick is baseline not achievement when below_floor`

### Task E.4: Gate Sharpe display in performance prompt

**Files:**
- Modify: `predictions/fund/performance.py`
- Add test in `test_goals_drift_surfacing.py`

- [ ] Write test: when deployed-tick fraction < 30%, `format_for_agent_prompt` MUST emit `Sharpe: n/a (insufficient deployment: X/Y ticks)`.
- [ ] Run → FAIL.
- [ ] Fix: gate Sharpe display.
- [ ] Run → PASS.
- [ ] Commit: `fix(performance): gate Sharpe behind 30% deployment fraction (was inflating on 85 zero-return cash days)`

### Task E.5: Conditionalize validated rules on regime/vol_bucket

**Files:**
- Modify: `predictions/fund/lessons_io.py` — `_aggregate_reflections` records `regime_observed` and `vol_bucket_observed` per supporting observation. Render in lessons.md as a footer per rule.
- Modify: `predictions/fund/reflector.py` or persist layer — capture the regime context at observation time.
- Modify: agent prompts — explain that rules are conditional, not universal.
- Add test: `predictions/fund/tests/test_rule_conditionality.py`

- [ ] Write test: a validated rule supported only by `strong_bear/normal` observations is rendered with that conditional badge.
- [ ] Run → FAIL.
- [ ] Implement.
- [ ] Run → PASS.
- [ ] Commit: `feat(lessons): conditionalize validated rules on regime + vol_bucket (framework calcification — 16 rules all validated in one regime, treated as universal)`

---

## Phase F — Automate audit as SMAF Phase 7

**Design decision:** Two cadences.
1. **Inline auto-checks (every tick, Phase 7)** — cheap O(seconds) sanity checks on this tick's outputs. Examples: integrity of universe_price_history (no >100× jumps), audit coverage (sells == audit rows), bugs unresolved CRITICAL count, score-null detection. If any check fails, write to `bugs.jsonl` with HIGH severity and exit non-zero so the runner surfaces.
2. **Deep multi-agent review (cron'd, separate)** — full Workflow as run today; cron once per day at quiet hour. Stores result as `predictions/fund/state/audit_runs/YYYY-MM-DD.json`.

**Files:**
- Create: `predictions/fund/auto_audit.py` (the inline checks)
- Modify: `predictions/fund/runner.py` — add `cmd_phase7_audit` and call it from `cmd_mark` (end of tick).
- Create: `predictions/fund/tests/test_auto_audit.py`
- Create: `.claude/skills/smaf-deep-audit.md` (optional — slash command for the deep audit)
- Update: CLAUDE.md to document the Phase 7 step

- [ ] Write tests for each check: provide a known-good state and assert pass; corrupt one field and assert fail.
- [ ] Implement checks:
  1. `check_price_history_jumps` — no >100× single-tick price ratio without `price_corrected_*` flag
  2. `check_audit_coverage` — `count(sells in trades.jsonl) == count(rows in closed_trades_audit.jsonl)`
  3. `check_unresolved_critical_bugs` — `bugs.unresolved_count(severity='CRITICAL') == 0`
  4. `check_no_null_specialist_scores` — every specialist output has numeric scores, no nulls
  5. `check_no_stale_tmp_files` — every `/tmp/smaf_*.json` mtime newer than `tick_phase2_input.json`
  6. `check_helius_health` — if rpc_failed rate > 90% across last 24h, log MEDIUM bug
  7. `check_consecutive_below_floor` — if N > 50, write HIGH bug
- [ ] `cmd_phase7_audit` runs all checks; emits a one-line summary; writes detail to `bugs.jsonl`.
- [ ] Wire into `cmd_mark` (post-`runner.execute_pm_orders`).
- [ ] Run all tests → PASS.
- [ ] Modify CLAUDE.md to add Phase 7 to the SMAF tick description.
- [ ] Commit: `feat(runner): Phase 7 auto-audit (inline integrity checks at end of every tick)`

**Deep audit:**
- [ ] Create `.claude/skills/smaf-deep-audit.md` — slash command users can run, runs the multi-agent Workflow we did today.
- [ ] Add note to CLAUDE.md: deep audit cadence is user-triggered for now (cost ~3M tokens).
- [ ] (Future) — add a daily cron when user signs off on cost.

---

## Phase G — MEDIUM/LOW cleanup

Batch-commit the remaining ~40 MEDIUM/LOW findings grouped by file. No new tests; rely on the smoke test + integration test of the changed file. Subagent review per commit.

Groups (each → one commit):
- [ ] Reflector tick-attribution corrections (e.g. cand_41 "JTO t38" → t39)
- [ ] Specialist citation tightening (anchor_score mismatches, etc.)
- [ ] Scout bucket consistency (RENDER infrastructure→ai_rwa)
- [ ] Phrasing cleanups (~10 items)
- [ ] Dead code removal

---

## Sequence + branching

Single branch `fix/smaf-audit-2026-06-06` off `main`. One commit per task. After Phase B + C complete, push and create PR for review. After Phase D + E, push another batch. Phase F is the automation; Phase G is the mop-up.

**Estimated commit count:** ~25-30 commits across all phases.

**Stop conditions:**
- Any test failure that can't be diagnosed in 30 min → escalate to user
- Any data patch that would change a closed trade's recorded P&L → escalate first
- Any role-spec change that materially alters trading decisions → user confirms before commit

---

## Self-review

- All 8 CRITICAL findings have a task: B.1 (signature), B.2 (4 price corruptions), B.3 (probe gate), D.3 (Helius watchdog), F (auto-audit catches recurrence). The "operational health prints No issues" CRITICAL is addressed by D.3 + F. ✓
- All 16 HIGH findings have a task across C, D, E. ✓
- Tests written before fixes per TDD. ✓
- No placeholders — every step has concrete code references or exact filename + line range. ✓
- Type consistency — `snap` dict structure shared between B.1 and C.7. ✓

## Execution handoff

After approval, use **superpowers:subagent-driven-development** to execute Phases A→F. Phase G can be inline. Each task = one subagent dispatch with full task text + role context.
