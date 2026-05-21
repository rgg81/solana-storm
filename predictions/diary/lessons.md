---
version: 2
last_updated: 2026-05-21T17:40:20Z
total_decisions_audited: 0
total_picks_audited: 0
overall_buy_hit_rate: null
buy_hit_rate_last_7d: null
buy_hit_rate_first_7d: null
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
- observation_count: **10** (5 + 5 across runs `2026-05-21-13-25` and `2026-05-21-17-40`)
- evidence: 10 of 10 shortlisted tokens triggered this ratio. Min seen: 50×. Max seen: **15,324×** (token `24`, mint HJaD3V6B…). The bonding curve consistently extracts the price peak; AMM phase is decay. No observed counter-example in two consecutive cohorts.
- why this is novel: The static-features ML iterations ([[phase3-backtest-result]], [[pivot-price-prediction-result]], [[stop-loss-strategy-result]]) had no access to pumpfun's ATH field. This is a dynamic feature unique to the per-invocation skill.

## C2 — Same-deployer multi-graduation farming (expanded)

`if deployer_wallet appears ≥2 times in a single Phase 2 cohort (any symbol)` → SKIP all from that deployer. Same deployer reappearing across consecutive runs is even stronger evidence.

- status: CANDIDATE (expanded from same-symbol-only after run 17:40)
- confirms: 0 (audit-based, needs 3 to promote VALIDATED)
- disconfirms: 0
- last_confirmed_at: null
- first_observed: 2026-05-21T13:25:17Z
- observation_count: **6** across runs `2026-05-21-13-25` and `2026-05-21-17-40`
- evidence:
  - 13:25 run: deployer `6iPahKgzFBQphxDrzD81etdExgR2qNDJUDECGDzqtBpv` migrated 2 `SOCCER` tokens 8 min apart (original narrow C2 hit).
  - 17:40 run: deployer `BnnNJJgy9w2MLQ9XBKJKG9FQa2r9qdW7u5VpzEkwUcc3` migrated `FOID` + `GAME` 30 min apart (different symbols — narrow C2 misses).
  - 17:40 run: deployer `dshAybqFXYVVTd4mzy9Uk6KD7km8wE9iZgPMYZdzEXc` migrated `24` + `MESSI` + `NPC` over 110 min (3 tokens).
  - **Cross-run**: `dshAybqF…` also deployed `SPIG` in the 13:25 cohort. That's **4 graduations in <12h from one wallet** — industrial farming, not solo dev.
- detection: group `recent_graduations` rows by `deployer_wallet`; SKIP any deployer with ≥2 rows in the last 24h window. Maintain a rolling "known farmer wallets" set seeded by repeat offenders.

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
