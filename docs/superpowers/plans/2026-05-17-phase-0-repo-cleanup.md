# Phase 0 — Repo Cleanup & Pivot Setup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the abandoned 22-week MEV plan from GitHub and the codebase, and set up the GitHub milestone + tracking issues for the pump.fun survival-strategy pivot.

**Architecture:** Pure housekeeping — no new functionality. Close every open issue and the obsolete milestones; create one pivot milestone with six phase tracking-issues; delete the three dead CEX crates/bin; commit the already-staged `.env`/`dotenvy` wiring; rewrite the README.

**Tech Stack:** `gh` CLI (GitHub issues/milestones), `git`, `cargo` (Rust workspace).

---

## Context

`solana-storm` is pivoting from a speed-based MEV bot to an ML-driven pump.fun
token survival strategy — see
`docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md`. This
plan clears the old plan's footprint before the new build begins.

- **Open issues to close (21):** #3, #4, #5, #6, #7 (old Phase 0 infra) and
  #14–#29 (old Weeks 7–22).
- **Obsolete milestones to close (5):** 1, 3, 4, 5, 6. Milestone 2 ("Phase 1:
  Rust + Solana Foundations", fully complete) is left as history.
- **Dead code to delete:** `crates/storm-cex`, `crates/storm-engine`,
  `bins/storm-monitor` (Binance/CEX code; unused by the pivot).
- **Surviving crates:** `storm-core`, `storm-solana`, `storm-store`,
  `bins/storm-cli`.

The cleanup is executed directly from this plan (checkbox tracking). GitHub
issues are reserved for the *build* phases (1–6).

## File structure

| Path | Change | Notes |
|---|---|---|
| `crates/storm-cex/` | Delete | was: Binance feed |
| `crates/storm-engine/` | Delete | was: CEX-DEX engine |
| `bins/storm-monitor/` | Delete | was: CEX-DEX monitor daemon |
| `Cargo.toml` | Modify | drop `storm-cex` / `storm-engine` workspace deps |
| `README.md` | Rewrite | describe the pivot, point at the spec |

## Notes for the executor

- `cargo` may not be on `PATH` in a fresh non-interactive shell. If a `cargo`
  command reports "command not found", run `. "$HOME/.cargo/env"` first.
- Every `gh issue` / `gh api` **write** is auto-blocked by the permission
  classifier unless allowed — Task 1 resolves this. Read-only `gh` calls work.
- Tasks are ordered so the pre-existing `.env`/`dotenvy` work is committed
  (Task 5) **before** the dead code is deleted (Task 6) — keeps commits clean.

---

### Task 1: Enable GitHub write permissions

**Files:** `.claude/settings.local.json` (approach A only).

- [ ] **Step 1: Pick an approach**

  - **A (recommended):** allow the agent to run `gh` writes — add
    `"Bash(gh issue:*)"` and `"Bash(gh api:*)"` to `permissions.allow` in
    `.claude/settings.local.json` (or run `/permissions` and allow them).
  - **B:** the agent prints each `gh` command and the user runs it with the
    `!` prefix.

- [ ] **Step 2: Confirm read access works**

  Run: `gh issue list --state open --limit 1`
  Expected: prints one issue line. (The first real write is verified in Task 2.)

---

### Task 2: Create the pivot milestone and six phase tracking-issues

**Files:** none (GitHub state).

- [ ] **Step 1: Create the milestone**

```bash
gh api repos/:owner/:repo/milestones -f title="pump.fun Survival Strategy" \
  -f description="ML-driven survival-scoring strategy on graduated pump.fun tokens. Spec: docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md"
```

Expected: JSON for the new milestone; note its `"number"`.

- [ ] **Step 2: Create the six phase tracking-issues**

```bash
M="pump.fun Survival Strategy"
gh issue create -m "$M" -t "Phase 1 — Data foundation" \
  -b "storm-pumpfun + storm-features crates, storm-store schema, storm-collector daemon. Detailed plan to follow. Spec section 9."
gh issue create -m "$M" -t "Phase 2 — Historical bootstrap" \
  -b "Assemble an initial backtest dataset from indexed sources + sampled RPC reconstruction. Spec section 9."
gh issue create -m "$M" -t "Phase 3 — Model + backtest" \
  -b "Survival model, honest paper-trading simulator, walk-forward validation vs the three baselines. Spec sections 8-9."
gh issue create -m "$M" -t "Phase 4 — Decision gate" \
  -b "Honest go/no-go against the pre-committed validation gate. Spec section 8."
gh issue create -m "$M" -t "Phase 5 — Live paper-trading" \
  -b "Forward, real-time paper-trading — only if the gate passes. Spec sections 8-9."
gh issue create -m "$M" -t "Phase 6 — Real-capital decision" \
  -b "Real-capital deployment decision — only if paper-trading confirms the edge. Spec section 9."
```

Expected: six new issues, all attached to the milestone.

- [ ] **Step 3: Verify**

  Run: `gh issue list --milestone "pump.fun Survival Strategy"`
  Expected: the six Phase issues listed.

---

### Task 3: Close the 21 obsolete issues

**Files:** none (GitHub state).

- [ ] **Step 1: Close every old-plan issue**

```bash
for n in 3 4 5 6 7 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29; do
  gh issue close "$n" --reason "not planned" \
    --comment "Closed — superseded by the pump.fun survival-strategy pivot. See docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md"
done
```

Expected: 21 "Closed issue #N" confirmations.

- [ ] **Step 2: Verify no old issues remain open**

  Run: `gh issue list --state open`
  Expected: only the six new Phase tracking-issues.

---

### Task 4: Close the five obsolete milestones

**Files:** none (GitHub state).

- [ ] **Step 1: Close milestones 1, 3, 4, 5, 6**

```bash
for m in 1 3 4 5 6; do
  gh api --method PATCH repos/:owner/:repo/milestones/"$m" -f state=closed >/dev/null \
    && echo "closed milestone $m"
done
```

Expected: "closed milestone 1/3/4/5/6". Milestone 2 is intentionally left as history.

---

### Task 5: Commit the pre-existing `.env`/`dotenvy` wiring

**Files:** Modify-commit `Cargo.toml`, `bins/storm-cli/Cargo.toml`,
`bins/storm-cli/src/main.rs`, `.env.example` (all already edited in an earlier
session; currently uncommitted).

- [ ] **Step 1: Commit only the surviving-crate wiring**

```bash
git add Cargo.toml bins/storm-cli/Cargo.toml bins/storm-cli/src/main.rs .env.example
git commit -m "Wire .env loading via dotenvy; point DATABASE_URL at SQLite

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Note: `bins/storm-monitor` also has uncommitted `dotenvy` edits — deliberately
*not* committed; that crate is deleted in Task 6.

- [ ] **Step 2: Verify**

  Run: `git status --short`
  Expected: only `bins/storm-monitor/` files remain modified (handled next).

---

### Task 6: Delete the dead CEX crates and bin

**Files:**
- Delete: `crates/storm-cex/`, `crates/storm-engine/`, `bins/storm-monitor/`
- Modify: `Cargo.toml`

- [ ] **Step 1: Remove the directories from git**

```bash
git rm -r --force crates/storm-cex crates/storm-engine bins/storm-monitor
```

Expected: lists the removed files. `--force` covers `storm-monitor`'s
uncommitted edits.

- [ ] **Step 2: Drop the two workspace dependency entries**

In `Cargo.toml`, delete these two lines from `[workspace.dependencies]`:

```toml
storm-cex = { path = "crates/storm-cex" }
storm-engine = { path = "crates/storm-engine" }
```

Leave `storm-core`, `storm-solana`, `storm-store` and all third-party deps —
the surviving crates still use them.

---

### Task 7: Verify the workspace still builds and tests pass

**Files:** none.

- [ ] **Step 1: Build**

  Run: `cargo build`
  Expected: `Finished` — only `storm-core`, `storm-solana`, `storm-store`,
  `storm-cli` compile; no reference to the deleted crates.

- [ ] **Step 2: Test**

  Run: `cargo test`
  Expected: all tests pass — the surviving crates' unit tests are unaffected by
  the deletion.

---

### Task 8: Commit the dead-code removal

**Files:** the deletions from Task 6 and the `Cargo.toml` edit.

- [ ] **Step 1: Commit**

```bash
git add -A
git commit -m "Remove dead CEX code (storm-cex, storm-engine, storm-monitor)

Unused by the pump.fun survival-strategy pivot.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

Expected: `git status` clean afterward.

---

### Task 9: Rewrite the README for the pivot

**Files:** Modify `README.md`

- [ ] **Step 1: Replace `README.md` with:**

```markdown
# Solana Storm

An ML-driven **survival-scoring strategy** for newly-graduated pump.fun tokens
on Solana. The system scores each graduated token's probability of *surviving*
(not rugging) and trades a filtered, diversified basket of the likely survivors
— competing on intelligence, not speed, on minimal infrastructure.

> Pivoted from an earlier speed-based MEV plan. See the design spec:
> [`docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md`](docs/superpowers/specs/2026-05-17-pumpfun-survival-strategy-design.md).

## Status

Phase 0 — repo cleanup & pivot setup. Build phases tracked under the
[pump.fun Survival Strategy milestone](https://github.com/rgg81/solana-storm/milestones).

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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Rewrite README for the pump.fun survival-strategy pivot

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Done criteria

- `gh issue list --state open` shows only the six Phase tracking-issues.
- Milestones 1, 3, 4, 5, 6 are closed; "pump.fun Survival Strategy" exists.
- `crates/storm-cex`, `crates/storm-engine`, `bins/storm-monitor` are gone.
- `cargo build` and `cargo test` pass.
- `git status` is clean; README describes the pivot.
