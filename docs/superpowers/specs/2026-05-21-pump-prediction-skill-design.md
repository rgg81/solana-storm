# Design Spec — `pump-prediction` Claude Skill

**Date:** 2026-05-21
**Status:** Approved design — pending implementation plan
**Project:** `solana-storm`
**Branch:** `pump-prediction-skill`

## 1. Context

`solana-storm` has run three honest pump.fun basket-picking strategy iterations against the `historical_graduations` dataset, all FAIL: Phase 3 survival classifier (`6b6baac` — survival ≠ price retention), the price-prediction pivot (`ea83aa7` — 0 positions because the calibrated score never crossed 0.5 above a ~10% prior), and the stop-loss strategy (`cdb1d84` — daily snapshots can't catch sub-day rugs; stop-loss exits paid slippage and netted WORSE than hold-to-horizon). Three FAILs through three independent mechanisms is conclusive evidence that the **static T0+12h feature set** doesn't predict pump.fun winners. The winning features are post-T0 events: social momentum, smart-wallet hits, trade-flow asymmetry — none of which are in our snapshot dataset.

This spec pivots away from "train a model on static features" entirely. Instead, it defines a **Claude Code skill** — `pump-prediction` — that's invoked manually 4–6 times per day and uses Claude's per-invocation reasoning to pick 3–5 graduating tokens, with a **persistent diary** that captures decisions, audits 24h-later outcomes, and synthesizes lessons. The skill **rewrites its own decision criteria** based on what just happened, building a smart-wallet registry and a corpus of validated lessons that compound over time. This is fundamentally different from a baked-in ML model: it operates on fresh data per invocation, against a self-improving knowledge base.

The goal is a research artifact that, over weeks of runs, surfaces whether **dynamic features + reasoning** can produce edge where **static features + ML** could not.

## 2. Goal & success criteria

A Claude Code skill that, when invoked manually:

1. **Audits prior decisions.** Reads `predictions/diary/decisions/*.md` for entries ≥24h old without matching outcome files, queries current pool state via Helius RPC, computes realized return per pick, writes an outcome file with per-pick analysis. Updates `predictions/diary/lessons.md` with confirmed/disconfirmed pattern observations and auto-maintained smart-wallet registry.
2. **Picks 3–5 fresh candidates.** Queries last-24h graduations via Dune, deep-enriches a 30–50 shortlist via Helius + pump.fun scrape + Telegram, reads lessons.md + recent outcomes for context, reasons through candidates, writes a decision file with `BUY` / `WATCH` / `SKIP` ratings + reasoning.
3. **Survives partial source failures.** Skips runs gracefully when REQUIRED sources fail; degrades conviction caps when OPTIONAL sources fail. The diary is always written.

**The success metric is the rolling stats block in `lessons.md`:** if `buy_hit_rate_last_7d` improves materially above `buy_hit_rate_first_7d` after ≥30 audits, the skill is learning. If it stays flat or declines, the approach is failing and the project should be retired honestly. **Success is the honest answer** — either the skill develops edge or it confirms the dataset+platform genuinely doesn't yield to this approach.

**No real capital is committed.** The skill is signal-generation only; the user manually decides whether to act on any pick. The diary's "realized return" comes from on-chain price snapshots, not from paper-trade simulation.

## 3. Scope

**In scope:**
- A new Claude skill at `.claude/skills/pump-prediction.md`.
- A new `predictions/` top-level directory containing helper Python scripts, the diary, and config.
- 5 helper scripts: `recent_graduations.py` (Dune), `helius_trade_flow.py` (Helius RPC), `pumpfun_scrape.py` (HTTP scrape), `telegram_chatter.py` (Telethon polling N public channels), `audit_outcome.py` (Helius RPC for 24h-elapsed pool state).
- The diary format (decision files, outcome files, rolling `lessons.md`).
- Bootstrap heuristics baked into the skill (used when `lessons.md` has no validated lessons).
- Per-helper unit tests using canned `--dry-run` outputs.
- A skill `--rehearsal` mode for manual smoke-testing without burning API calls.
- README in `predictions/` describing how to run + interpret output.

**Out of scope:**
- Automation / cron / scheduler (manual trigger per the brainstorm).
- Real trading or wallet integration.
- Twitter / X integration (paid API over budget).
- Pre-graduation token scanning (bonding-curve program; different data path).
- End-to-end integration tests of the full skill (skill is reasoning-under-uncertainty; quality is measured by the diary's hit rate over time, not assertion-based tests).
- Changes to existing `bootstrap/` ETL or `model/` packages (this is a separate, additive sub-system).

## 4. Architecture

The skill is **instructions** that Claude follows; the helpers are **scripts** that do I/O. The diary is the **persistent state** that bridges invocations.

```
solana-storm/
├── .claude/
│   └── skills/
│       └── pump-prediction.md          # The skill — Claude reads this on invocation
├── predictions/                          # NEW top-level
│   ├── helpers/
│   │   ├── recent_graduations.py         # Dune SQL: last 24h graduations + facts
│   │   ├── helius_trade_flow.py          # Helius RPC: trade-flow + holders per mint
│   │   ├── pumpfun_scrape.py             # HTTP scrape: live trades + comments + creator
│   │   ├── telegram_chatter.py           # Telethon: poll N public channels
│   │   ├── audit_outcome.py              # Helius RPC: 24h-elapsed pool state for one mint
│   │   ├── tests/                        # pytest dir
│   │   │   ├── test_recent_graduations.py
│   │   │   ├── test_helius_trade_flow.py
│   │   │   ├── test_pumpfun_scrape.py
│   │   │   ├── test_telegram_chatter.py
│   │   │   └── test_audit_outcome.py
│   │   └── dry_run_data/                  # Canned outputs for --dry-run + tests
│   ├── diary/
│   │   ├── decisions/                    # per-run decision files (gitignored)
│   │   ├── outcomes/                     # per-run outcome files (gitignored)
│   │   └── lessons.md                    # rolling synthesis (COMMITTED)
│   ├── config.py                          # Constants: universe window, picks per run, etc.
│   ├── requirements.txt                   # telethon, requests, beautifulsoup4
│   └── README.md
└── .gitignore                              # add: predictions/diary/decisions/, outcomes/
```

The skill orchestrates via `Bash` tool calls to helpers; helpers return JSON to stdout; Claude reads + reasons + writes diary files via `Write` tool.

## 5. Data flow

Each invocation runs two phases in order.

### Phase 1 — Audit

Cheap. Touches only tokens with existing decisions.

1. Find pending audits: scan `predictions/diary/decisions/*.md`, filter to entries older than 24h without a matching `predictions/diary/outcomes/<id>-outcome.md`. Typical: 1–2 files.
2. For each pending decision, per pick:
   - Read recorded `mint`, `entry_pool_base_reserve`, `entry_pool_quote_reserve`, `entry_time_utc` from decision frontmatter.
   - Call `helpers/audit_outcome.py <mint>` → current `pool_base_reserve`, `pool_quote_reserve`, or `pool_closed: true`.
   - Compute realized return: `(current_quote/current_base) / (entry_quote/entry_base) - 1.0`, or `-1.0` if `pool_closed`.
3. Write `predictions/diary/outcomes/<decision-id>-outcome.md`: per-pick return + reasoning audit ("did the smart-wallet hit signal hold?", "what feature did I miss on the rug?").
4. **Smart-wallet registry maintenance.** For every successful pick (`realized_return >= 1.0` → 2× or better), call `helpers/helius_trade_flow.py <mint> --window 60min --kind buys` to enumerate first-hour unique buyers; increment their `winner_hits` in `lessons.md`. For every audited pick (winner or loser), increment `total_appearances`.
5. **Lesson state transitions** per §6 below. Apply them inline to `lessons.md`.

### Phase 2 — Decide

Heavier. Touches the full 24h cohort.

1. Read context: `lessons.md`, last 3 outcome files, last 3 decision files. ~5–10 KB.
2. Call `helpers/recent_graduations.py` → Dune query for last 24h graduations with point-in-time pool depths + deployer facts. Returns 100–500 tokens.
3. **Cheap prefilter** (in-memory, no extra queries): drop `liq_quote_reserve < 5 SOL`, drop `deployer_prior_launches > 200`. Yields shortlist of 30–50.
4. **Deep-enrich shortlist**, per token:
   - `helpers/helius_trade_flow.py <mint>` → first-hour buy count, sell count, net SOL, unique buyers, smart-wallet hits (cross-referenced against `lessons.md` registry).
   - `helpers/pumpfun_scrape.py <mint>` → comment count + creator-reply count + live-trade rate.
   - `helpers/telegram_chatter.py <ticker>` → mention count across N polled channels in last 12h.
5. Claude reasons through candidates against `lessons.md`'s VALIDATED lessons + bootstrap heuristics. Selects 3–5 picks with conviction levels (`BUY` / `WATCH` / `SKIP`).
6. Write `predictions/diary/decisions/<run-id>.md`: per-pick entry snapshot + reasoning + suggested exit criteria.

### Cost ledger (per invocation, target)

| Resource | Calls per run | Daily (6 runs) | % of free-tier daily |
|---|---|---|---|
| Helius RPC | ~150 (50 tokens × 3 calls) | ~900 | <1% of 100k/day |
| Dune SQL | 1 | 6 | ~0.1% of 2,500/mo |
| Pump.fun scrape | ~50 GETs | 300 | rate-limit risk above ~1,000/day |
| Telegram poll | N channels × 1 | N × 6 | free |

## 6. Diary file formats

### Decision file — `predictions/diary/decisions/<YYYY-MM-DD-HH-MM>.md`

```markdown
---
run_id: 2026-05-21-08-30
run_time_utc: 2026-05-21T08:30:00Z
universe_size: 187
shortlist_size: 42
lessons_version: 12
helius_available: true
dune_available: true
pumpfun_available: true
telegram_available: true
---

# Picks

## BUY — STORM (mint: 8y45AJzC...PMhqXuY9)

- entry_time_utc: 2026-05-21T08:29:42Z
- entry_pool_base_reserve: 9.85e14
- entry_pool_quote_reserve: 8.7e10
- entry_price: 8.83e-5
- exit_criteria: take profit at 2.0×, stop at 50% pool-quote drop, hard exit at 2026-05-22T08:30Z

**Why BUY (HIGH conviction):**
- [reasoning per signals fired and lessons applied]

## WATCH — MEMECAT (mint: ...)

[similar structure]

## SKIP — RUGBOT (mint: ...)

[similar structure with reasoning for rejection]
```

The `entry_pool_*_reserve` fields are exactly what `audit_outcome.py` needs 24h later to compute realized return identically to how the existing `model/backtest.py` would — no slippage approximation needed.

### Outcome file — `predictions/diary/outcomes/<decision-id>-outcome.md`

```markdown
---
audits_decision: 2026-05-21-08-30
audited_at_utc: 2026-05-22T09:15:00Z
elapsed_hours: 24.75
buy_hit_count: 1
buy_total: 1
watch_hit_count: 0
watch_total: 1
---

# Outcomes

## STORM (BUY — HIGH) → +127% ✅
[per-pick realized state, return, reasoning audit]

## MEMECAT (WATCH — MEDIUM) → −94% ❌
[same shape]

# Aggregate this run
[hit rates, conviction calibration, sampled SKIPs if any]
```

### Rolling lessons — `predictions/diary/lessons.md` (COMMITTED to git)

```markdown
---
version: 12
last_updated: 2026-05-22T09:15:00Z
total_decisions_audited: 47
total_picks_audited: 158
overall_buy_hit_rate: 0.18
buy_hit_rate_last_7d: 0.22
buy_hit_rate_first_7d: 0.08
trend: improving
---

# Validated lessons (status: VALIDATED, ≥3 confirms, used by every Phase 2 run)

## L1 — Smart-wallet hits are the strongest signal
[description, confirm/disconfirm counts, last_confirmed_at]

## L3 — Spam deployer filter
[same]

# Candidate lessons (status: CANDIDATE, 1-2 audits, pending confirmation)

## CL5 — Curve-completion speed + deployer history
[description, status: 1/3 confirms]

# Smart-wallet registry (auto-maintained, top 30 by winner_hits)

| Wallet | winner_hits | total_appearances | last_seen |
|---|---|---|---|
| Dee...P3z | 7 | 12 | 2026-05-21 |
| ...

# Disconfirmed signals (status: DISCONFIRMED, kept as anti-patterns)

## D1 — High Telegram mention count alone
[description, why it failed]
```

## 7. Learning loop mechanics

### Lesson lifecycle

| Trigger | Effect |
|---|---|
| Audit observes a NEW pattern | Add as `CANDIDATE`, confirms=1, first_observed_at=now |
| Audit observes a CANDIDATE pattern again with same direction | confirms += 1 |
| CANDIDATE reaches confirms ≥ 3 | Promote to `VALIDATED` (becomes input to Phase 2 reasoning) |
| Audit observes a VALIDATED pattern fail | disconfirms += 1 |
| VALIDATED has disconfirms ≥ 3 AND `confirms/disconfirms < 2.0` | Demote to `CANDIDATE` |
| CANDIDATE has confirms=0 AND first_observed_at > 7 days ago | Retire to `DISCONFIRMED` (kept as anti-pattern in file) |
| VALIDATED has not been triggered in any audit for 14 days | Demote to `CANDIDATE` (drift safeguard) |

State transitions are applied by Claude during Phase 1 audit, per the explicit rules above.

### Smart-wallet registry algorithm

- **Denominator (`total_appearances`):** on every audit (winner OR loser), enumerate first-hour unique buyers via `helius_trade_flow.py --window 60min --kind buys`; for each buyer (new or existing in the registry), `total_appearances += 1`.
- **Numerator (`winner_hits`):** when the same audited pick is a winner (`realized_return >= 1.0`), ALSO `winner_hits += 1` for each first-hour buyer.
- The two operations happen on the same per-audit enumeration. `total_appearances` is incremented unconditionally; `winner_hits` is incremented only on the winner branch.
- **Inclusion in the `smart_wallets` working list** (used by Phase 2): `winner_hits >= 3` AND `winner_hits/total_appearances >= 0.25`.
- **Pruning:** drop wallets with `last_seen` > 30 days ago.
- **Cap:** top 30 by `winner_hits`; lower-ranked entries archived to a sibling `lessons_archive.md` (not read by skill).

### Bootstrap heuristics

When `lessons.md` has 0 VALIDATED lessons applicable, the skill prompt provides defaults:

- **Strong negative:** `deployer_prior_launches > 30` AND `deployer_age_days < 14`.
- **Weak negative:** `curve_completion_time < 30 min` (suggests coordinated pre-snipe).
- **Strong positive:** first-hour `unique_buyer_count > 50` with steady arrival (not all-at-once).
- **Weak positive:** pump.fun creator replied to ≥2 organic-looking comments.
- **Strong negative:** first 5 buy transactions all within 60 seconds of each other (suggests sniper coordination — bots batch-firing the moment the pool opens).

These get superseded as validated lessons accumulate.

### Learning health check

Every 7 days, Phase 1 also computes:
```
total_decisions_audited
total_picks_audited
overall_buy_hit_rate
buy_hit_rate_last_7d
buy_hit_rate_first_7d
trend: improving | flat | declining
```

**If `buy_hit_rate_last_7d <= buy_hit_rate_first_7d` after ≥30 audits**, the system is not learning. Surface this in the next Phase 2 prompt as a flag for the user — honest signal that the approach should be retired.

## 8. Error handling + graceful degradation

### Per-source failure policy

| Source | Criticality | Behavior on failure |
|---|---|---|
| Helius RPC | REQUIRED | After 3 retries failing: SKIP run, write `<ts>-SKIPPED.md` |
| Dune SQL | REQUIRED | After timeout/error: SKIP run, write `<ts>-SKIPPED.md` |
| Pump.fun scrape | OPTIONAL | Continue with degraded confidence (cap conviction at MEDIUM) |
| Telegram | OPTIONAL | Continue with degraded confidence (cap conviction at MEDIUM) |
| Both OPTIONAL down | — | Continue but cap all conviction at WATCH (no BUY) |
| `lessons.md` corrupt | RECOVERABLE | Save as `lessons.md.broken-<ts>`, fall back to bootstrap heuristics, regenerate `lessons.md` via re-audit of historical outcome files |

The diary is ALWAYS written. Skipped runs get `<ts>-SKIPPED.md` files with frontmatter recording which sources failed and why.

### Helper retry semantics

- HTTP: 3 retries, exponential backoff (1s, 3s, 9s). On 429: honor `Retry-After` header.
- Helius client: built-in retries; on final failure return `{"error": "<reason>", "data": null}`.
- Dune client: built-in polling; on >120s timeout, surface as error not exception.

The skill prompt instructs Claude: "if a helper returns `{error: ...}`, treat that source as unavailable for this run per the policy table."

### Audit-side specifics

| Audit scenario | Behavior |
|---|---|
| Pool exists, reserves readable | Normal compute |
| Pool account closed (`account_not_found`) | `realized_return = -1.0`, record `pool_closed: true` |
| RPC fails on a specific mint after retries | Skip this pick's audit; remains pending. After 3 consecutive skipped audits across runs, flag in lessons.md as "audit health degraded" |

### Conviction caps under degradation

- All required sources up + both optional up: full conviction range (`BUY` HIGH allowed).
- All required up + one optional down: cap at `BUY` MEDIUM.
- All required up + both optional down: cap at `WATCH` (no `BUY` at all).

This is the "honest under degraded conditions" property: quality scales with evidence available.

## 9. Architecture — touched files

| Path | Change |
|---|---|
| `.claude/skills/pump-prediction.md` | **NEW** — the skill (instructions + bootstrap heuristics + error policies) |
| `predictions/helpers/recent_graduations.py` | **NEW** — Dune query helper |
| `predictions/helpers/helius_trade_flow.py` | **NEW** — Helius RPC helper for trade flow + first-hour buyers |
| `predictions/helpers/pumpfun_scrape.py` | **NEW** — pump.fun HTTP scrape helper |
| `predictions/helpers/telegram_chatter.py` | **NEW** — Telethon poll helper |
| `predictions/helpers/audit_outcome.py` | **NEW** — Helius RPC helper for 24h-elapsed pool state |
| `predictions/helpers/tests/test_*.py` | **NEW** — unit tests for each helper (5 files) |
| `predictions/helpers/dry_run_data/*.json` | **NEW** — canned outputs for `--dry-run` and tests |
| `predictions/config.py` | **NEW** — constants (universe window, picks per run, conviction thresholds, channel lists) |
| `predictions/requirements.txt` | **NEW** — telethon, requests, beautifulsoup4 |
| `predictions/README.md` | **NEW** — how to run + interpret output |
| `predictions/diary/lessons.md` | **NEW** — empty initially with bootstrap-only frontmatter; this is COMMITTED |
| `.gitignore` | **Modified** — add `predictions/diary/decisions/` and `predictions/diary/outcomes/` |
| All existing files | **Unchanged** — additive sub-system |

## 10. Pre-committed parameters

- **Cadence target:** 4–6 invocations per day (manual trigger, not enforced by the skill).
- **Outcome horizon:** 24 hours.
- **Universe:** last 24h of graduations.
- **Shortlist size cap:** 50 tokens.
- **Picks per run:** 3–5 (mix of BUY / WATCH / SKIP categories).
- **Conviction tiers:** `BUY` HIGH, `BUY` MEDIUM, `WATCH`, `SKIP`.
- **Smart-wallet inclusion threshold:** `winner_hits >= 3` AND precision `>= 0.25`.
- **Lesson promotion threshold:** `confirms >= 3` for CANDIDATE → VALIDATED.
- **Lesson demotion threshold:** `disconfirms >= 3` AND `confirms/disconfirms < 2.0` for VALIDATED → CANDIDATE.
- **Drift safeguard:** VALIDATED untriggered for 14 days → demoted.
- **Health check threshold:** if `buy_hit_rate_last_7d <= buy_hit_rate_first_7d` after ≥30 audits, surface flag.
- **Telegram starter channels** (mention-count signal only — content is NOT trusted as ground truth):
  - `@PumpFunChannel` — pump.fun-adjacent broadcast channel.
  - `@pumpfunsignal` — whale-wallet activity tracker for pump.fun launches (aligned with our smart-wallet approach).
  - `@SolanaMemeCoinss` — broad Solana memecoin call channel.
  - `@MemeCoinDaily` — large general-memecoin news broadcast (~1M subscribers).
  - `@MemeCoinWhalePumps` — whale-activity tracker (~150k subscribers).
  - The list lives in `predictions/config.py` as `TELEGRAM_CHANNELS = [...]` so the user can prune/extend without re-spec. Any channel that returns `error: not_found` or `error: forbidden` on the first run is logged and silently dropped from subsequent polls until the user re-adds it manually. The skill DOES NOT join private/invite-only groups — public broadcast read-only via Telethon only.

## 11. Testing

### Helper unit tests (pytest, `predictions/helpers/tests/`)

Each helper has a `--dry-run` flag that returns fixed canned output from `predictions/helpers/dry_run_data/`. Tests assert:
- Helper handles the canned input correctly.
- Helper handles known error modes (timeout, 429, malformed response) without raising.
- Helper handles edge cases (empty result, missing fields).

Run: `python3 -m pytest predictions/helpers/tests/ -v`. Target: all green before merge.

### Skill smoke test (manual, `--rehearsal` mode)

A flag baked into `config.py`. When set:
- Helpers return canned data instead of live calls.
- Skill writes its would-be decision to stdout instead of `predictions/diary/decisions/`.
- Lets the user verify prompt structure + reasoning quality without burning API calls.

### No end-to-end automated test

The skill's purpose is reasoning under uncertainty. Quality is measured by the diary's rolling hit rate over time, not by assertion-based tests. The 7-day learning health check IS the quality test.

## 12. Risks & honest caveats

- **The skill may not develop edge.** If `buy_hit_rate_last_7d` stays flat at ~10% (the population baseline) after 30+ audits, the dynamic-feature + reasoning approach also doesn't work on this dataset, and the project should be retired. This is the fourth iteration; it could fail like the prior three.
- **Smart-wallet registry has a cold-start problem.** Bootstrap heuristics carry the first ~10 runs; the registry doesn't get useful until ~20+ audits. Expect noisy results for the first 1–2 weeks.
- **Pump.fun scrape fragility.** Their HTML structure can change without notice. The helper will break silently (return `error: html_parse_failed`); we'll see the source flagged as `unavailable` in decision frontmatters. Mitigation: minimal scraping (one selector each for trades, comments, creator), fail loudly, fix when broken.
- **Telegram noise.** Public memecoin channels are mostly shillers, bots, and pump-and-dump organizers. The `telegram_chatter.py` helper returns mention counts only; it does NOT trust channel content as ground truth, does NOT do sentiment or claim-of-truth analysis (would be unreliable), and does NOT join private groups. The starter list in §10 was assembled from public-search aggregators and may include dead/private channels — the helper handles `not_found` / `forbidden` errors silently and the user prunes after observing first-run output. Conviction must come from corroboration with other signals — Telegram mention count alone is an EXPLICITLY DISCONFIRMED signal (see lessons.md template entry D1).
- **Token efficiency of lessons.md.** Every Phase 2 invocation re-reads it. After ~6 months of audits, the registry could bloat. Mitigation: top-30 cap on smart wallets + archival of older lessons + monthly compact-rewrite by Claude.
- **No paper-trade backend.** Realized returns are computed from on-chain price snapshots assuming you bought at the entry-time pool state. Real-world slippage when YOU actually buy could be worse (front-running). The diary's reported hit rate is an UPPER BOUND on what you'd realize trading manually.
- **The user is in the loop.** This is signal generation; the user manually decides whether to act. No automation of trades. Any losses from acting on signals are the user's risk.

## 13. Open decisions (resolved during implementation)

- ~~The exact list of Telegram channels to poll~~ — **resolved in §10**: starter list of 5 channels assembled from public-search aggregators; user prunes/extends after first run reveals which are accessible and which return useful mention volume.
- The exact CSS selectors / HTML structure for pump.fun scraping (depends on their current site at implementation time).
- Whether to store the smart-wallet registry in a separate file (`registry.md`) or keep inline in `lessons.md` (decided based on size after first run).
- Exact phrasing of the skill's `bootstrap heuristics` block (refined during prompt engineering of the skill file).
- Whether to add a `--rehearsal` mode at the skill level (instructions) or only at the helper level (data sources). Likely both, but TBD per test design.
- Whether `predictions/config.py` should be importable from the skill (via `Bash python3 -c`) or just consulted as documentation by Claude. Likely both.
