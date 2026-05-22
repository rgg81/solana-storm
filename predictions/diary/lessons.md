---
version: 5
last_updated: 2026-05-22T09:52:57Z
total_decisions_audited: 1
total_picks_audited: 2
overall_buy_hit_rate: null  # no BUYs yet; cannot compute
watch_hit_rate_all_time: 0.0  # 0 of 2 audited WATCHes hit >=1.0×
buy_hit_rate_last_7d: null
buy_hit_rate_first_7d: null
cohort_avg_return_first_audit: -0.9234  # average across 2 WATCH picks audited
trend: cold_start
---

# pump-prediction skill — rolling lessons & smart-wallet registry

This file is the persistent memory of the `pump-prediction` skill.

Every Phase 1 (audit) invocation may update this file; every Phase 2
(decide) invocation reads it. The frontmatter above tracks the rolling
hit-rate stats that drive the 7-day learning health check.

**During cold-start** (the first ~10 invocations), the Validated lessons
section is empty and the smart-wallet registry has no data. The skill
falls back to the bootstrap heuristics defined in
`.claude/skills/pump-prediction.md`.

# Validated lessons (status: VALIDATED, ≥3 confirms — input to every Phase 2)

_(none yet — populated by Phase 1 audits)_

# Candidate lessons (status: CANDIDATE, 1–2 audits, pending confirmation)

## C1 — Post-peak entry: ATH ≫ current MC means the bonding-curve top has already been printed

`if pumpfun.ath_market_cap_usd / max(pumpfun.market_cap_usd, 1) > 10` → SKIP (or at most WATCH).

- status: CANDIDATE
- confirms: 0 (audit-based, needs 3 to promote VALIDATED)
- disconfirms: 0
- last_confirmed_at: null
- first_observed: 2026-05-21T13:25:17Z
- observation_count: **20** (5 × 4 cohorts: 13-25, 17-40, 22-00, 02-07)
- evidence: 20 of 20 shortlisted tokens triggered this ratio. Min seen: **87.9×** (CR7 in 02:07 run, new low — was the most BUY-attractive candidate yet by other signals). Max seen: **21,847×** (TRUMP in 02:07 run, new high). The bonding curve consistently extracts the price peak; AMM phase is decay. **No counter-example in 20 consecutive observations.**
- why this is novel: The static-features ML iterations ([[phase3-backtest-result]], [[pivot-price-prediction-result]], [[stop-loss-strategy-result]]) had no access to pumpfun's ATH field. This is a dynamic feature unique to the per-invocation skill.

## C2 — Same-deployer multi-graduation farming (expanded)

`if deployer_wallet appears ≥2 times in a single Phase 2 cohort, OR ≥2 times across consecutive 24h windows, OR ≥5 times in the trailing 24h universe` → SKIP all from that deployer.

- status: CANDIDATE (expanded from same-symbol-only after run 17:40; mega-farmer threshold added after run 22:00)
- confirms: 0 (audit-based, needs 3 to promote VALIDATED)
- disconfirms: 0
- last_confirmed_at: null
- first_observed: 2026-05-21T13:25:17Z
- observation_count: **9 cohort-level**, **far more in universe**
- evidence (cohort-level fires):
  - 13:25 run: deployer `6iPahKgz…` migrated 2 `SOCCER` tokens 8 min apart.
  - 17:40 run: deployer `BnnNJJgy…` migrated `FOID` + `GAME` 30 min apart.
  - 17:40 run: deployer `dshAybqF…` migrated `24` + `MESSI` + `NPC` over 110 min (3 tokens).
  - 22:00 run: deployer `28kDW9j4…` migrated `Rick67` + `WRLD` 26 min apart (new farmer flagged).
  - 22:00 run: deployer `BnnNJJgy…` reappeared with `MILHOUSE` (and 11 grads/24h universe-wide).
  - 02:07 run: 3 of 5 picks were from known farmers (`6iPahKgz` CYCLE, `dshAybqF` Mindshare, `BnnNJJgy` ZePIN). Mega-farmer `9C4nRvhh` still dominant at 43 grads/24h.
- evidence (universe-level dominance, observed at 22:00):
  - `9C4nRvhh…`: **65 graduations/24h** (mega-farmer — newly detected)
  - `dshAybqF…`: 12 grads/24h (cross-day persistence)
  - `BnnNJJgy…`: 11 grads/24h (cross-day persistence)
  - `6iPahKgz…`: 5 grads/24h
  - **Top 5 farmers account for ~72% of all graduations in the trailing 24h window.**
- **Known-farmer registry** (instant SKIP on appearance in any cohort):
  - `6iPahKgzFBQphxDrzD81etdExgR2qNDJUDECGDzqtBpv` (SOCCER-pair, 5 grads/24h)
  - `BnnNJJgy9w2MLQ9XBKJKG9FQa2r9qdW7u5VpzEkwUcc3` (FOID/GAME/MILHOUSE, 11 grads/24h)
  - `dshAybqFXYVVTd4mzy9Uk6KD7km8wE9iZgPMYZdzEXc` (SPIG/24/MESSI/NPC, 12 grads/24h)
  - `28kDW9j49yH1gYmtK3mGsnoejfq5sP8mCy8GAzocWd59` (Rick67/WRLD, 2 grads/24h, NEW)
  - `9C4nRvhhVquCKA…` (MEGA-FARMER, 65 grads/24h, NEW)
- detection: group `recent_graduations` rows by `deployer_wallet`; SKIP any with ≥2 rows in 24h OR any wallet in known-farmer registry. **Future helper improvement**: filter at the Dune query level to avoid wasting Helius credits enriching tokens that will be C2-rejected.

## C3 — High unique_buyer_count is misleading when first-5 spread is tight

`if unique_buyer_count >= 50 AND (first-5 buy timestamps span ≤ 60 seconds)` → SKIP. The "buyers" are sniper-bot wallets owned by a smaller operator pool, racing for the migration-arbitrage opportunity. They are NOT 50+ organic retail buyers.

- status: CANDIDATE
- confirms: 0 (audit-based, needs 3 to promote VALIDATED)
- disconfirms: 0
- last_confirmed_at: null
- first_observed: 2026-05-22T02:07:33Z
- observation_count: **2** (CR7 + TRUMP in 02:07 run)
- evidence:
  - CR7 (mint AgHh16tz...): 84 unique buyer wallets, all first-5 timestamps within 1 second (1779415631–1779415632). 0 sells. 356 SOL pool (deepest ever observed by skill).
  - TRUMP (mint 5mxCrbnh...): 169 unique buyer wallets, all first-5 timestamps at unix 1779413169 (same second). 0 sells. Pool already drained to 19 SOL — sniper rush already played out.
- relationship to existing rules: amplifies bootstrap §2d.3 "Strong negative: first-5 within 60s = sniper coordination". C3's specific contribution: it warns that the **buyer count** signal (bootstrap "Strong positive: >50 buyers") can NOT be trusted in isolation — when spread is tight, sniper coordination DOMINATES the count positive. The signals don't cancel; the negative overrides.
- why this matters: explains why pump.fun graduations with apparently-strong buyer engagement (50, 80, 169 unique wallets) still consistently dump post-grad. The buyers are extractors, not investors.
- pending validation: CR7 (WATCH this run) audited at ~02:07 UTC 2026-05-23 — if it decays or rugs, C3 gets its first confirm.

# Smart-wallet registry (auto-maintained, top-30 by winner_hits)

| wallet | winner_hits | total_appearances | precision | last_seen |
|---|---|---|---|---|
| _(none yet)_ | | | | |

# Disconfirmed signals (status: DISCONFIRMED — anti-patterns)

## D1 — High Telegram mention count alone
Tested as "if mentioned ≥10× across channels in 12h → BUY." Result expected: poor precision (Telegram channels are mostly shillers / pump-and-dump organizers). Status: DISCONFIRMED a priori (pre-seeded based on the spec's risk discussion). The skill MUST corroborate Telegram mentions with on-chain trade flow before raising conviction.

# Notes

- Format: every lesson has a frontmatter triple `status / confirms / disconfirms / last_confirmed_at`.
- State transition rules are defined in the skill file at `.claude/skills/pump-prediction.md`.
- Smart-wallet registry inclusion: `winner_hits >= 3` AND `precision >= 0.25`.
- Pruning: wallets with `last_seen > 30 days ago` are dropped.
- This file is COMMITTED to git; per-run decision/outcome files are gitignored.
