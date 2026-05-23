---
name: pump-fund
description: User-invokable status + manual-trigger for the v2 multi-agent pump.fun fund (Late-Curve / Early-Curve / Smart-Mirror / Catalyst specialists + Fund Manager).
---

# pump-fund — v2 multi-agent skill

This skill is the user-facing surface for the v2 pump-prediction system. Most operation is autonomous via cron (see Section 7 of the v2 design spec). This file documents how the user inspects status and manually triggers components when needed.

## Quick status

```bash
cd /home/roberto/solana-storm
python3 -c "
from pathlib import Path
from predictions.diary import lessons_io
fm = lessons_io.load_frontmatter(Path('predictions/diary/lessons.md'))
print(f\"lessons.md version: {fm.get('version')}\")
print(f\"total_picks_audited: {fm.get('total_picks_audited')}\")
print(f\"overall_buy_hit_rate: {fm.get('overall_buy_hit_rate')}\")
for s in ('late_curve','early_curve','smart_mirror','catalyst'):
    st = fm.get(s) or {}
    print(f\"  {s}: audited={st.get('picks_audited')} hr30d={st.get('hit_rate_last_30d')} cold={st.get('cold_start_mode')}\")
"
```

## Manual invocations (debug / explore)

| Command | Purpose |
|---|---|
| `python3 predictions/runner.py universe_fetch` | Pull current pre-grad universe into SQLite |
| `python3 predictions/runner.py late_curve` | Trigger late-curve specialist once |
| `python3 predictions/runner.py early_curve` | Trigger early-curve specialist once |
| `python3 predictions/runner.py smart_mirror` | Trigger smart-mirror (dormant unless seeded) |
| `python3 predictions/runner.py catalyst` | Trigger catalyst once |
| `python3 predictions/runner.py fund_manager` | Trigger FM consolidation once |
| `python3 predictions/runner.py audit_tick` | Process due audits once |

## Kill switch

`export PUMP_V2_HALT=1` — all crons exit immediately on next fire. No cron uninstall needed. Use during build windows, debugging, or vacation.

## Cron schedule (managed via CronCreate)

- `*/15 * * * *` late_curve + universe_fetch
- `0 * * * *` catalyst
- `0 */4 * * *` early_curve
- `15 */4 * * *` smart_mirror
- `30 */4 * * *` fund_manager
- `*/10 * * * *` audit_tick

## Diary structure

- `predictions/diary/lessons.md` (git-tracked) — the team's central memory
- `predictions/diary/decisions/<ts>-<specialist>.md` (gitignored) — per-specialist per-cycle output
- `predictions/diary/outcomes/<pick_id>-outcome.md` (gitignored) — audit results
- `predictions/diary/shadow_watches/<pick_id>-shadow.md` (gitignored) — vetoed-by-C1 tracking

## Verdict horizon

Per spec §9, v2 has a 30-day verdict window from cutover. Re-evaluate viability on 2026-06-23.
