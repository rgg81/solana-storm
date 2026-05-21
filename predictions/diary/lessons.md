---
version: 1
last_updated: 2026-05-21T13:25:17Z
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
- confirms: 0
- disconfirms: 0
- last_confirmed_at: null
- first_observed: 2026-05-21T13:25:17Z
- evidence: Run `2026-05-21-13-25` shortlist — all 5 tokens hit this ratio (50–5,500×). Pumpfun's `ath_market_cap_timestamp` for graduated tokens corresponds to the bonding-curve peak, not a post-grad rally. The graduation event IS the local maximum; AMM phase is decay.
- why this is novel: The static-features ML iterations ([[phase3-backtest-result]], [[pivot-price-prediction-result]], [[stop-loss-strategy-result]]) had no access to pumpfun's ATH field. This is a dynamic feature unique to the per-invocation skill.

## C2 — Same-deployer same-symbol graduation farming

`if deployer_wallet graduates ≥2 tokens with identical symbol within 1h` → SKIP both.

- status: CANDIDATE
- confirms: 0
- disconfirms: 0
- last_confirmed_at: null
- first_observed: 2026-05-21T13:25:17Z
- evidence: Run `2026-05-21-13-25` — deployer `6iPahKgzFBQphxDrzD81etdExgR2qNDJUDECGDzqtBpv` migrated two `SOCCER` tokens (different mints) 8 minutes apart. One pool drained to 18 SOL almost immediately; the other got 0 first-hour AMM buys. Classic spray-and-pray sniper farming.
- detection: cross-reference `recent_graduations.deployer_wallet` against itself, group by deployer within 1h window. Optionally also check pumpfun `symbol` for collision.

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
