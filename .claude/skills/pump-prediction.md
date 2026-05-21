---
name: pump-prediction
description: Pick 3-5 pump.fun graduations and audit prior 24h-old decisions for realized return. Use when the user wants fresh memecoin signals or wants to evaluate recent picks against on-chain outcomes.
---

# pump-prediction skill

You are running the `pump-prediction` skill. Your job each invocation:

1. **Phase 1 (audit):** read prior decisions ≥24h old, query current on-chain state, compute realized returns, write outcome files, update `predictions/diary/lessons.md` if patterns confirm/disconfirm.
2. **Phase 2 (decide):** query the last-24h graduation cohort, enrich a 30-50 shortlist with trade flow + pump.fun + Telegram signals, reason against `lessons.md`, write a new decision file with 3-5 picks rated BUY (HIGH/MEDIUM) / WATCH / SKIP.

The diary is the persistent memory across invocations. Always write to it; never leave silent failures.

## Setup

Working directory: `/home/roberto/solana-storm`. Branch should already be checked out.

Detect rehearsal mode: `echo $PUMP_PREDICTION_REHEARSAL`. If set to "1"/"true"/"yes", all helpers return canned data and you write the would-be decision to stdout instead of `predictions/diary/decisions/`. Phase 1 audits are still computed (against the same canned data) so the output looks structurally complete.

## Phase 1 — Audit (run first)

### 1a. Find pending audits

```bash
python3 -c "
from pathlib import Path
import re
from datetime import datetime, timezone, timedelta
d = Path('predictions/diary/decisions')
o = Path('predictions/diary/outcomes')
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
pending = []
for f in sorted(d.glob('*.md')):
    m = re.match(r'(\d{4}-\d{2}-\d{2}-\d{2}-\d{2})\.md$', f.name)
    if not m:
        continue
    ts = datetime.strptime(m.group(1), '%Y-%m-%d-%H-%M').replace(tzinfo=timezone.utc)
    if ts > cutoff:
        continue
    if (o / f'{m.group(1)}-outcome.md').exists():
        continue
    pending.append(f.name)
for p in pending:
    print(p)
"
```

If empty, skip to Phase 2. If 1+ files listed, proceed.

### 1b. For each pending decision file

Read the file. Parse the frontmatter + per-pick sections (mint, pool_address, entry_pool_base_reserve, entry_pool_quote_reserve, conviction).

For each pick, call:

```bash
python3 predictions/helpers/audit_outcome.py <mint> --pool <pool_address>
```

Parse the JSON. Compute realized return:

```
realized_return = (current_quote/current_base) / (entry_quote/entry_base) - 1.0
```

If `pool_closed: true`, set `realized_return = -1.0` (i.e., -100%).

### 1c. Smart-wallet registry maintenance

For EVERY audited pick (winner OR loser), call:

```bash
python3 predictions/helpers/helius_trade_flow.py <mint> --pool <pool_address> --window 60
```

Read `buyer_wallets`. For each wallet:
- Find or add row in `lessons.md`'s smart-wallet table.
- `total_appearances += 1`.
- If `realized_return >= 1.0` (a 2× winner), ALSO `winner_hits += 1`.
- Update `last_seen` to today's date.

Apply inclusion: a wallet appears in the working `smart_wallets` set Phase 2 uses if `winner_hits >= 3` AND `winner_hits/total_appearances >= 0.25`.

Prune: any wallet with `last_seen > 30 days ago` gets removed.

Cap: keep top 30 by `winner_hits`. Move lower-ranked rows to `predictions/diary/lessons_archive.md` (create if it doesn't exist).

### 1d. Write the outcome file

`predictions/diary/outcomes/<decision-id>-outcome.md`:

```markdown
---
audits_decision: <decision-id>
audited_at_utc: <now in ISO>
elapsed_hours: <hours since decision>
buy_hit_count: <# of BUYs with realized_return > 0>
buy_total: <# of BUYs>
watch_hit_count: ...
watch_total: ...
---

# Outcomes

## <ticker> (<conviction>) → <realized %> <emoji>
- 24h pool_base_reserve: ...
- 24h pool_quote_reserve: ...
- realized_price_at_24h: ...
- realized_return: <percent>

**Did reasoning hold?** <one paragraph>. Confirm / disconfirm any specific lesson IDs.

[repeat per pick]

# Aggregate this run

- BUY hit rate (realized return > 0): X/Y
- WATCH hit rate: X/Y
- SKIP sampling: list 1-2 SKIP'd tokens; report current price; was the SKIP correct?

# Lesson updates

List any specific transitions made to lessons.md (e.g., "L7 confirms+=1 → 5", "CL12 promoted to VALIDATED").
```

### 1e. Apply lesson transitions to lessons.md

For each lesson confirmed or disconfirmed in this audit batch:

| Trigger | Action |
|---|---|
| NEW pattern observed | Add as CANDIDATE, confirms=1, first_observed_at=now |
| CANDIDATE pattern observed again | confirms += 1 |
| CANDIDATE reaches confirms ≥ 3 | Promote to VALIDATED |
| VALIDATED pattern fails | disconfirms += 1 |
| VALIDATED has disconfirms ≥ 3 AND confirms/disconfirms < 2.0 | Demote to CANDIDATE |
| CANDIDATE has confirms=0 AND first_observed_at > 7d ago | Retire to DISCONFIRMED |
| VALIDATED untriggered for 14d | Demote to CANDIDATE (drift safeguard) |

Edit `lessons.md` directly. Bump `version`. Update `last_updated`, `total_decisions_audited += 1`, `total_picks_audited += <picks in this batch>`, `overall_buy_hit_rate`.

### 1f. 7-day learning health check (weekly)

If `total_decisions_audited >= 30` AND we haven't computed the 7-day window stats in the last 24h:

Compute `buy_hit_rate_last_7d` (audits in last 7 days) and `buy_hit_rate_first_7d` (the first 7 days of audits). Update frontmatter:

```
buy_hit_rate_last_7d: 0.22
buy_hit_rate_first_7d: 0.08
trend: improving | flat | declining
```

If `trend = flat` or `declining` after 30+ audits, prepend a section to lessons.md:

```
## ⚠️ Learning-health warning (2026-05-25)
After 30+ audits, buy_hit_rate_last_7d (0.10) <= buy_hit_rate_first_7d (0.11).
The skill is not developing edge. Surface in next Phase 2 prompt; user
should consider retiring this project.
```

## Phase 2 — Decide

### 2a. Read context

Read in this order:
1. `predictions/diary/lessons.md` (all sections)
2. The 3 most-recent files in `predictions/diary/outcomes/` (sorted by name desc)
3. The 3 most-recent files in `predictions/diary/decisions/`

Keep these in your working context.

### 2b. Query the universe

```bash
python3 predictions/helpers/recent_graduations.py
```

Parse the JSON. If `error` is non-null:
- If the source is REQUIRED (Dune): abort Phase 2. Write `predictions/diary/decisions/<ts>-SKIPPED.md` (see §3 below). Stop.

Apply the cheap prefilter (in your reasoning, no extra queries):
- Drop tokens where `curve_real_sol_reserves_lamports < 50_000_000_000` (50 SOL — the curve must have completed substantially before graduating).
- Drop tokens where `deployer_prior_launches > 200`.

NOTE: `liq_quote_reserve_lamports` is currently 0 for all tokens because the source Dune view doesn't expose initial pool reserves. The skill uses `curve_real_sol_reserves_lamports` (which IS populated, in lamports) as a proxy for "tokens that graduated with meaningful capital."

Cap the shortlist at 50 tokens. If more than 50 remain, take the 50 with the highest `curve_real_sol_reserves_lamports`.

### 2c. Deep-enrich the shortlist

For EACH shortlisted token:

```bash
python3 predictions/helpers/helius_trade_flow.py <mint> --pool <pool> --window 60
python3 predictions/helpers/pumpfun_scrape.py <mint>
python3 predictions/helpers/telegram_chatter.py <ticker>   # ticker = pump.fun ticker if you have it; else mint short prefix
```

Track which sources returned errors. Maintain a per-token enrichment dict.

### 2d. Reason + rank

For each token, evaluate against:

**1. Validated lessons** in `lessons.md` (use them in priority order, most-confirmed first).

**2. Smart-wallet registry hits.** Check `helius_trade_flow.buyer_wallets` against the working `smart_wallets` set. Any hit is a strong positive signal.

**3. Bootstrap heuristics** (when fewer than 5 VALIDATED lessons apply):

- **Strong negative:** `deployer_prior_launches > 30` AND `deployer_age_secs < 14 * 86400`.
- **Weak negative:** `curve_completion_time_secs < 30 * 60` (30 min — suggests pre-snipe coordination).
- **Strong positive:** `unique_buyer_count > 50` with steady arrival (consult `first_5_buy_timestamps_unix` — if all 5 are within 60s of each other, that's coordinated; otherwise organic).
- **Weak positive:** `creator_reply_count >= 2` AND `comment_count >= 10`.
- **Strong negative:** all `first_5_buy_timestamps_unix` within 60 seconds of each other (sniper bot coordination).

**4. Telegram caveat:** mention count alone is DISCONFIRMED (D1). Only count it as a confirming signal when on-chain flow is also positive.

Select 3-5 picks. Assign one of: `BUY HIGH`, `BUY MEDIUM`, `WATCH`, `SKIP`. Include at least 1-2 SKIPs in the output for the diary's record.

### 2e. Conviction caps under degradation

If `pumpfun_scrape` failed for this run → cap conviction at `BUY MEDIUM`.
If `telegram_chatter` failed → cap at `BUY MEDIUM`.
If BOTH failed → cap at `WATCH` (no BUY at all this run).

### 2f. Write the decision file

`predictions/diary/decisions/<YYYY-MM-DD-HH-MM>.md`:

```markdown
---
run_id: <timestamp>
run_time_utc: <ISO now>
universe_size: <int>
shortlist_size: <int>
lessons_version: <from lessons.md>
helius_available: true|false
dune_available: true|false
pumpfun_available: true|false
telegram_available: true|false
---

# Picks

## BUY HIGH — <TICKER> (mint: <mint>)

- entry_time_utc: <iso>
- pool_address: <pool>
- entry_pool_base_reserve: <int lamports>
- entry_pool_quote_reserve: <int lamports>
- entry_price: <float>
- exit_criteria: <one line; e.g., "take profit at 2.0×, stop at 50% pool-quote drop, hard exit at <iso 24h from now>">

**Why BUY HIGH:**
- [3-5 bullets citing specific signals: smart-wallet hits, lesson IDs applied, observed counts]

[repeat per pick]

# Data snapshot summary

Brief enrichment table (one line per shortlisted candidate, top 10 by score):

| ticker | mint short | buy/sell | unique buyers | smart hits | comments | tg mentions | decision |
|---|---|---|---|---|---|---|---|
| STORM | 8y45... | 47/3 | 89 | 1 | 22 | 9 | BUY HIGH |
| ... |
```

### 2g. (Rehearsal mode) write to stdout instead

If `PUMP_PREDICTION_REHEARSAL` is set, print the decision-file content to stdout INSTEAD of writing it. The user sees what would have been written without polluting the diary.

## Skipped-run format

If Phase 2 aborts (REQUIRED source down), write:

`predictions/diary/decisions/<YYYY-MM-DD-HH-MM>-SKIPPED.md`:

```markdown
---
run_id: <ts>
status: SKIPPED
reason: <one line>
helius_available: <bool>
dune_available: <bool>
pumpfun_available: <bool>
telegram_available: <bool>
universe_size: null
shortlist_size: null
---

# Skipped

<one paragraph explaining what failed and what the next run should try>
```

Phase 1 audits still run normally — the SKIP only blocks Phase 2.

## Final report to the user (after both phases)

Print a brief summary to stdout:

```
pump-prediction run complete.

Phase 1: audited N picks from M decision files.
  BUY hit rate this batch: X/Y
  Lesson transitions: <list>

Phase 2: <N> picks written to predictions/diary/decisions/<run_id>.md
  BUY HIGH: <count>
  BUY MEDIUM: <count>
  WATCH: <count>
  SKIP (reported for record): <count>
  Sources: helius=ok dune=ok pumpfun=ok telegram=degraded

Health: <trend>. <hit_rate_last_7d> vs first_7d <hit_rate_first_7d>. <warning if any>.
```

If `PUMP_PREDICTION_REHEARSAL` is set, prepend "(REHEARSAL — no diary writes)" to the summary.

## Failure / degradation discipline

- The diary MUST always be written. Either a decision file, a SKIPPED file, or an outcome file. Silent no-op runs corrupt the learning-health stats.
- When a helper returns `error: ...`, never silently use stale data. Treat that source as unavailable for this run and apply the conviction cap.
- Bumping `lessons_version` is mandatory whenever you edit lessons.md. The version number is how the user can see learning happening.
- Never invent picks. If the universe is < 3 tokens after prefilter, output what you have and note in the decision file's frontmatter `note: thin universe`.
