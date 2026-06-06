# solana-storm — project guidance

## Follow active skills and orchestrations closely

When a project skill, runner, or multi-phase orchestration is in play, follow its sequence exactly — don't shortcut steps, don't skip role files, don't substitute your own logic for an agent's role.

Concrete examples in this repo:

- **SMAF tick** (`predictions/fund/`): always runs Phase 0 → 1 → 2 (4 specialists in parallel) → 3 → 4 → 5a → 6 → 5b → **7 (auto-audit)**. Read each agent's role file in `predictions/fund/agents/*.md` before dispatching. Inject the team charter (`team_charter.md`). Stage inputs via the dedicated module (`stage_phase2.py`, `stage_phase3.py`, `stage_phase4.py`) — not ad-hoc /tmp scripts. Phase 7 runs `python predictions/fund/runner.py audit` — cheap integrity checks (price-history jumps, audit coverage, unresolved CRITICAL bugs, specialist null scores, stale /tmp guards, Helius health, consecutive-flat-tick alarm) that write a HIGH/CRITICAL bug on failure. Always invoke Phase 7 after `report.py` runs; if a CRITICAL check fails, surface it in the next commit message.
- **pump-prediction skill**: invoke via its skill entrypoint; don't reimplement.
- **basket runner** (`predictions/basket/runner.py`): use the documented `{snapshot|rebalance|report}` commands.

If a phase or sub-step seems unnecessary, surface that observation **after** running it. Don't pre-emptively skip.

## Fix bugs proactively — at the source, not inline only

When something fails mid-orchestration:

1. **Diagnose root cause before retrying.** A `SLIPPAGE_CAP` failure with `liq=0` means the liquidity lookup was wrong — not that slippage is actually 10%. A `KeyError` means the schema you're reading from is different than expected — go look at the producer.
2. **Patch inline** so the current run can complete (write corrected JSON to state, mutate the input dict, etc.).
3. **Then fix the source** so the next run inherits the fix. Promote /tmp scripts to permanent modules. Correct field-name typos at the call site. Update the staging helper, not just the patched output. If the bug came from a data-shape mismatch, fix the producer or the normalizer — both ends.
4. **Verify the fix works** before continuing. Re-run the failing step against the corrected source.
5. **Surface what was fixed** in the commit message (root cause + source fix), not just the symptom. Future ticks read these messages.

Same discipline applies to: field-name typos (`liquidity_usd` vs `liq_usd`), data-shape mismatches (`scores` as list vs dict), missing snapshot fields (e.g. `entry_consensus`), stale cache assumptions, etc.

## Audit trail is the fund's memory

Reports (`predictions/fund/reports/*.md`), lessons (`lessons.md`), audit JSONLs, and commit messages are how the SMAF/basket/pump-prediction systems remember themselves across ticks and sessions. Generate the report even if the tick is uneventful. Commit reports + permanent modules. **Never** commit `predictions/fund/state/` contents (gitignored — contains account state, caches, trade logs).

## Honest verification

State plainly when something is done and verified. State plainly when a step failed or was skipped. The fund's stop conditions depend on accurate audits — don't hedge or omit.
