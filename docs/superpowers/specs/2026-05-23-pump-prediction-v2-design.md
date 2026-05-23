# pump-prediction v2 design — multi-agent fund

**Goal:** rebuild the pump-prediction skill from a single-agent post-graduation picker (v1, structurally negative-edge as proven by 5/5 losing audits and C1 VALIDATED) into a multi-agent fund where four strategy specialists feed a Fund Manager that decides allocation. The system targets the pre-graduation universe (where C1 evidence says actual upside lives) plus catalyst-driven and smart-mirror plays, all under a self-improving memory model.

**Status:** approved 2026-05-23 (Roberto). Implementation plan follows via writing-plans skill.

**Prior context:** see `docs/superpowers/specs/2026-05-21-pump-prediction-skill-design.md` for v1; lessons learned in `predictions/diary/lessons.md` (C1 VALIDATED, C2 candidate, C3 candidate, farmer registry) all carry forward unchanged into v2.

---

## 1. Architecture

```
              ┌──────────────────────────────────────────────┐
              │      Autonomous loop (CronCreate)            │
              │   One cron per specialist + FM + audit-tick  │
              └──────────────────────────────────────────────┘
                              │ (cadence trigger)
                              ▼
       ┌─────────────────────────────────────────────────┐
       │              Universe layer                      │
       │  • pump.fun /coins paginated scrape              │
       │  • Curve-state tracker (SQLite time series)     │
       │  • Existing graduated-tokens query (Dune)        │
       └─────────────────────────────────────────────────┘
              │
              ▼ (fresh universe + curve history)
    ┌──────────────────────────────────────────────────────────┐
    │             4 specialist agents (subagents)              │
    │                                                          │
    │  Late-curve momentum │ Early-curve quality │ Smart-mirror│ Catalyst
    │  cadence 15 min      │ cadence 4h          │ cadence 4h  │ 1h
    │  outputs: 0..N picks per cycle with per-specialist exit  │
    └──────────────────────────────────────────────────────────┘
              │ (all specialists pool their picks)
              ▼
    ┌──────────────────────────────────────────────────────────┐
    │       Pump Fund Manager (decider subagent, cadence 4h)   │
    │  • Consolidates all current-cycle specialist picks       │
    │  • Applies portfolio rules (sizing, diversification cap) │
    │  • Cold-start equal weight → mature audit-rate weighted  │
    │  • Adversarial-skeptic pass (internal to FM)             │
    │  • Outputs final decision file: BUY/WATCH/SKIP + size%   │
    └──────────────────────────────────────────────────────────┘
              │
              ▼
    ┌──────────────────────────────────────────────────────────┐
    │                Diary (refactored)                        │
    │  Per-specialist + FM decision files (gitignored)         │
    │  Per-pick outcome files (gitignored)                     │
    │  Shadow-watches (gitignored)                             │
    │  lessons.md (TRACKED — the only committed memory)        │
    └──────────────────────────────────────────────────────────┘
```

**Key design choices:**
- Specialists run as **subagents** (Claude Agent SDK / `Agent` tool). Isolated context, parallel execution, debuggable per specialist.
- **Universe layer is shared infrastructure** — no specialist owns it.
- **Fund Manager runs only at the 4h cadence** (slowest specialist cycle). Late-curve specialist accumulates ~16 cycles of picks between FM runs; FM picks across all.
- v2 **replaces v1 entirely**. Existing `lessons.md` carries forward — `C1 VALIDATED` continues applying as a strict veto across all specialists.

---

## 2. Universe layer

The shared data substrate every specialist queries.

### 2a. Pre-graduation universe helper

**`predictions/helpers/pumpfun_curve_universe.py`**

Hits `https://frontend-api-v3.pump.fun/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false`. Paginates 5 pages deep (~250 tokens) per call.

Per-token fields: `mint, bonding_curve_pct, market_cap_sol, creator_wallet, created_timestamp, reply_count, recent_trades_count, last_trade_timestamp, name, symbol, nsfw, is_banned`.

Output JSON to stdout (same convention as existing helpers).

### 2b. Curve-state tracker (SQLite)

**`predictions/state/curve_history.db`** schema:

```sql
CREATE TABLE curve_snapshots (
  mint TEXT NOT NULL,
  fetched_at_unix INTEGER NOT NULL,
  bonding_curve_pct REAL,
  market_cap_sol REAL,
  reply_count INTEGER,
  recent_trades_count INTEGER,
  PRIMARY KEY (mint, fetched_at_unix)
);
CREATE INDEX idx_mint_time ON curve_snapshots(mint, fetched_at_unix);

CREATE TABLE smart_wallet_seed (
  wallet TEXT PRIMARY KEY,
  first_seen_unix INTEGER,
  last_winner_at_unix INTEGER,
  winner_hits INTEGER,
  total_observations INTEGER,
  precision REAL,
  status TEXT  -- 'seeded' | 'active' | 'retired'
);
```

The universe scrape writes one snapshot row per token per call. Specialists query time-series for velocity/acceleration features:
- Δ `bonding_curve_pct` in last 15 min
- `recent_trades_count` rate-of-change
- Recent `market_cap_sol` threshold crossings

**Retention policy**: drop `curve_snapshots` rows older than 30 days.

### 2c. Existing graduated-tokens query

`recent_graduations.py` kept as-is. Used by smart-mirror specialist + audit machinery to detect post-graduation transitions.

### 2d. Universe orchestrator

**`predictions/universe.py`** exposes `fetch_universe(include_pregrad=True, include_graduated=True) → (CurveTokens, GraduatedTokens)`. Specialists import this, not the raw helpers, so the universe-layer interface is stable.

---

## 3. Specialist agents

Each specialist is a subagent invoked via the `Agent` tool with a tightly-scoped prompt. They share a common interface: receive `(universe_snapshot, lessons_md, agent_state_dir)`, return `picks: list[Pick]` where each pick has `mint, ticker, conviction, recommended_exit, reasoning, citations`.

**Common rules (apply to all 4):**
- MUST consult global VALIDATED lessons (C1, C2, C3) before emitting any pick. If a VALIDATED lesson hard-SKIPs, the specialist returns 0 picks for that token, no exceptions. (Veto can be relaxed via shadow-watch refinement — see §6e.)
- MUST include at least one SKIP citation per run for the diary record.
- Conviction tiers: `BUY HIGH`, `BUY MEDIUM`, `WATCH`, `SKIP`.

### 3a. Late-Curve Momentum agent (cadence: 15 min)

**Thesis:** the bonding curve pumps hardest in its last segment (60–95%). Catch accelerating buy velocity at that stage, exit at or just after graduation.

**Inputs:**
- Pre-grad universe filtered to `bonding_curve_pct ∈ [60, 95]`
- 1h time-series slice from `curve_snapshots` per candidate
- Global lessons + `## Late-Curve Lessons` section of lessons.md

**Reasoning skeleton:**
- Flag tokens with `Δbonding_curve_pct / 15min > 5%` AND `recent_trades_count` accelerating
- Penalize sniper-coordinated launches (C3 pattern: tight first-N spread)
- Veto known-farmer deployers (C2)

**Exit rule:** sell at graduation event OR `-30%` stop OR 6h hard timeout.

**Audit horizons recorded:** 1h, 4h, 6h. Effective audit at first of {graduation, -30%, 6h timeout}.

### 3b. Early-Curve Quality agent (cadence: 4h)

**Thesis:** find tokens at 10-30% curve completion with organic signals (active dev, healthy distribution, non-farmer deployer). Bet on curve completion.

**Inputs:**
- Pre-grad universe filtered to `bonding_curve_pct ∈ [10, 30]` AND `created_timestamp < 6h ago`
- Per-token: `creator_wallet` reputation (known-farmer + smart-wallet registries), `reply_count`, holder distribution via Helius `getTokenLargestAccounts`
- Global lessons + `## Early-Curve Lessons`

**Reasoning skeleton:**
- Strong positives: `reply_count ≥ 5`, creator previously launched a graduated token, top-10 holders hold <40%
- Strong negatives: top holder >25%, creator on known-farmer registry, `reply_count = 0` at age 6h+

**Exit rule:** sell at +200% OR `-50%` stop OR 7-day hard timeout.

### 3c. Smart-Mirror agent (cadence: 4h, gated)

**Thesis:** profitable curve traders persist. Mirror their entries with delay.

**Inputs:**
- `smart_wallet_seed` table (Section 2b) — auto-discovered at v2 launch, refreshed weekly
- Recent buys by registry wallets on any pre-grad token via Helius `getSignaturesForAddress`
- Universe filter: tokens just bought by ≥1 registry wallet in the last 1h

**Reasoning skeleton:**
- For each (token, registry-wallet) pair: compute registry-wallet's precision (historical winner rate). High-precision wallet buying recently → strong positive.
- Discount if token triggers C1 / C2 / C3.

**Exit rule:** **mirror the followed wallet** — sell when that wallet sells (poll their on-chain activity every 15 min), or `-30%` stop, or 7-day hard timeout.

**Gating:** dormant until `smart_wallet_seed` has ≥5 wallets with `precision ≥ 0.3` AND `total_observations ≥ 10`.

**Position cap:** at most 5 concurrent smart-mirror positions to bound polling cost.

### 3d. Catalyst agent (cadence: 1h)

**Thesis:** narratives move pump.fun prices on hour-timescales. CEX listings, Reddit virality, news mentions create entry windows before on-chain action.

**Inputs:**
- CryptoPanic feed (last 1h, filtered to crypto news + Reddit/Telegram aggregated posts) — see §5a
- Reddit hot posts from the 4 chosen subs — see §5b
- Pre-grad and graduated universe
- Global lessons + `## Catalyst Lessons`

**Reasoning skeleton:**
- For each ticker mentioned in news/Reddit in the last 1h: check if it exists in pump.fun universe. If yes, compute mention velocity + sentiment.
- Strong positive: first mentioned in last 1h, ≥3 sources, positive sentiment, on-chain trades reacting.
- Strong negative: mentioned predominantly in pump-and-dump shill posts.

**Exit rule:** sell at +50% OR `-20%` stop OR 24h hard timeout.

### 3e. Specialist output contract

All specialists write `predictions/diary/decisions/<ts>-<specialist>.md`:

```yaml
---
run_id: <ts>
specialist: late_curve | early_curve | smart_mirror | catalyst
specialist_cadence_minutes: 15 | 60 | 240
universe_size: <int>
shortlist_size: <int>
lessons_version: <int>
picks_count: <int>
---
```

Per-pick section: `mint, ticker, conviction, recommended_exit{rule, take_profit_pct, stop_loss_pct, hard_timeout_hours}, reasoning, lesson_citations`.

The Fund Manager consumes these files at its cadence.

---

## 4. Pump Fund Manager

Runs every 4h. Reads all specialist decision files written since the previous FM run, consolidates them, plays adversarial skeptic on its own picks, emits the FINAL decision file.

### 4a. Allocation algorithm

**Per-specialist weight:**
- **Cold-start mode** (`picks_audited < 30`): weight = 1.0 (equal across specialists)
- **Mature mode** (`picks_audited ≥ 30`): weight = `max(0.1, specialist_hit_rate_last_30d)` — hit_rate = picks with `realized_return ≥ specialist's target` ÷ total picks

Weights pulled fresh from lessons.md frontmatter on every FM run.

**Per-pick score:**

```
score(pick) = specialist_weight(specialist)
            × conviction_multiplier(conviction)
            × (1 - penalty_for_global_lessons(pick))
```

- `conviction_multiplier`: BUY HIGH = 1.0, BUY MEDIUM = 0.6, WATCH = 0.2, SKIP = 0.0
- `penalty_for_global_lessons`: 1.0 if any VALIDATED global lesson fires (strict veto); 0.3 per CANDIDATE lesson firing

**Convergence bonus:** same mint picked by multiple specialists → take highest score + 0.1 bonus + record "N specialists converged" in reasoning.

### 4b. Position sizing

After scoring, FM ranks picks descending:

- `recommended_size_pct = min(score / max_score × MAX_POSITION_PCT, MAX_POSITION_PCT)`
- `MAX_POSITION_PCT = 20%` (no single pick > 20% of book)
- Total committed (sum across BUY picks) capped at `MAX_BOOK_DEPLOYED = 80%`
- Picks below `recommended_size_pct < 2%` get downgraded to WATCH

### 4c. Adversarial skeptic pass

Before writing final decisions, FM re-reads its own picks and runs internal challenge loop:
- For each BUY pick: "What's the strongest argument this is wrong?" — must cite specific lesson, recent audit outcome, or diary pattern
- If challenge is convincing (FM's own judgment), downgrade conviction one tier
- Records the challenge AND why FM kept/downgraded — transparent reasoning trail
- No separate skeptic subagent (saves a Claude call)

### 4d. FM output

**`predictions/diary/decisions/<ts>-fund_manager.md`**:

```yaml
---
run_id: <ts>
run_time_utc: <iso>
specialists_consulted: 4
specialist_files: [list of paths]
specialist_cold_start_status: {late_curve: cold, early_curve: cold, smart_mirror: dormant, catalyst: cold}
total_picks_received: <int>
final_decisions:
  buy_high_count: 0
  buy_medium_count: 1
  watch_count: 2
  skip_count: 7
book_pct_deployed: 0.18
lessons_version: <int>
---
```

Per-pick section includes `specialist_recommendations` (which specialist said what), `specialist_convergence` (count + bonus), `score`, `recommended_size_pct`, `exit_rule` (inherited from originating specialist), `reasoning`, `skeptic_challenge`, `skeptic_resolution`.

### 4e. FM lessons section

`lessons.md` gets `## Fund Manager Lessons`. Tracks:
- Did FM weight a low-hit-rate specialist too heavily? → candidate
- Did FM consistently override specialists in a direction that turned out wrong? → audit catches this
- Did convergence-bonus pay off? → cumulative stats

Updated at audit time alongside specialist-specific lessons.

### 4f. Cold-start FM behavior

Until `total_picks_audited ≥ 20` (sum across specialists), FM runs conservative:
- `MAX_POSITION_PCT = 10%` (not 20%)
- `MAX_BOOK_DEPLOYED = 50%` (not 80%)
- Skeptic-challenge must resolve to "kept" — any plausible disconfirm → downgrade
- Goal: protect early capital while audit data accumulates

---

## 5. Web-scraping foundation

### 5a. CryptoPanic helper

**`predictions/helpers/cryptopanic_feed.py`**

`GET https://cryptopanic.com/api/v1/posts/?auth_token=<key>&currencies=<tickers>&filter=hot&public=true`

- Free tier: 1000 req/day. Usage ~24 req/day per ticker × N tickers — well under cap.
- Cache 10 min in `predictions/.cache/cryptopanic/` (gitignored).
- Per-post: `title, source, published_at_unix, votes (pos/neg/important), currencies_tagged, url`
- Auth: `CRYPTOPANIC_API_TOKEN` in `.env`. Free signup.

### 5b. Reddit helper

**`predictions/helpers/reddit_hot_posts.py`**

Reddit public JSON (auth-free for read):
- `GET https://www.reddit.com/r/<sub>/new.json?limit=100`
- `GET https://www.reddit.com/r/<sub>/hot.json?limit=50`

Subs: `CryptoCurrency, solana, Cryptomoonshots, SatoshiStreetBets`.

Rate limit: 60 req/min unauth. Usage ~192/day — well under.

Per-post: `title, selftext, author, created_utc, score, num_comments, url, permalink`. Filtered to last 1h, matching ticker regex `[\$#]?TICKER\b` case-insensitive.

Cache 10 min.

### 5c. Catalyst-agent integration

Catalyst specialist calls both helpers in parallel per cycle, deduplicates ticker mentions across sources, computes:
- `mention_velocity` = mentions in last 1h ÷ mentions in last 4h
- `source_diversity` = distinct sources mentioning ticker
- `sentiment_proxy` = CryptoPanic positive votes − negative votes

These feed the catalyst specialist's reasoning.

### 5d. Failure modes

Both helpers degrade gracefully (cached return, error field set). Catalyst specialist treats degraded data as "catalyst signal unavailable this cycle" — emits 0 picks and notes degradation. No conviction-cap cascade to other specialists.

### 5e. CryptoPanic ticker-tagging limitation

CryptoPanic's auto-tagger is patchy for fresh memecoins. Mitigation: catalyst specialist supplements tagged feeds with full-text search across recent posts (Reddit + CryptoPanic title/body) for ticker mentions, not just relying on platform tags. More noise, broader coverage.

---

## 6. Diary refactor (the central memory)

The team's persistent brain. Any subagent on any future run can reconstruct what's been learned.

### 6a. File layout

```
predictions/
├── diary/
│   ├── decisions/                    ← gitignored, per-run
│   │   ├── <ts>-late_curve.md
│   │   ├── <ts>-early_curve.md
│   │   ├── <ts>-smart_mirror.md
│   │   ├── <ts>-catalyst.md
│   │   └── <ts>-fund_manager.md
│   ├── outcomes/                     ← gitignored, per-pick
│   │   └── <pick_id>-outcome.md
│   ├── shadow_watches/               ← gitignored, NEW
│   │   └── <pick_id>-shadow.md
│   └── lessons.md                    ← TRACKED, central memory
├── state/                            ← gitignored, NEW
│   ├── curve_history.db
│   ├── smart_wallet_registry.db
│   └── specialist_stats.json
└── helpers/, ...
```

**`lessons.md` is the only tracked file.** Everything else reproducible from re-running.

### 6b. lessons.md schema (refactored)

```yaml
---
version: <int>
last_updated: <iso>
total_decisions_audited: <int, FM decisions>
total_picks_audited: <int, individual picks across all specialists>
total_shadow_watches_audited: <int>
overall_buy_hit_rate: <float | null until ≥30 audits>

late_curve:
  picks_audited: <int>
  hit_rate_all_time: <float>
  hit_rate_last_7d: <float>
  hit_rate_last_30d: <float>  # used by FM allocation weighting
  cold_start_mode: <bool>  # true if picks_audited < 30
early_curve: {same fields}
smart_mirror: {same fields, plus dormant: bool}
catalyst: {same fields}
fund_manager:
  decisions_audited: <int>
  override_hit_rate: <float>

trend: cold_start | improving | flat | degrading
buy_hit_rate_first_7d: <float>
buy_hit_rate_last_7d: <float>
---
```

Body sections:

```markdown
# Validated lessons (apply globally, hard veto unless refinement promoted)
## C1 — Post-peak entry (ATH/MC > 10×) → SKIP
[carries forward from v1]

# Candidate global lessons (≥1 confirm, <3)
## C3 — Mass-sniper overrides buyer-count
[carries forward from v1]

# Candidate VALIDATED-lesson refinements (the evolution path)
## C1-mid-with-organic-arrival — possible exception?
[NEW pattern, populated by shadow-watches]

# Per-specialist lessons
## Late-Curve Lessons
## Early-Curve Lessons
## Smart-Mirror Lessons
## Catalyst Lessons
## Fund Manager Lessons

# Disconfirmed signals (anti-patterns)
## D1 — Telegram mention count alone
## D2 — Deep pool ≠ safer entry

# Smart-wallet registry (auto-maintained, top-30 by precision)
| wallet | precision | total_obs | last_seen | discovered_via |
```

### 6c. Audit machinery → memory updates

When a pick's exit horizon arrives:

1. Compute realized return (existing `audit_outcome.py` logic).
2. Identify attribution: which specialist(s) emitted the pick? FM conviction vs specialist conviction?
3. Update per-specialist stats in lessons.md frontmatter.
4. Update per-specialist lessons (confirm/disconfirm candidate lessons).
5. Update FM lessons (override hit rate, convergence bonus payoff).
6. Update smart-wallet registry (enumerate first-hour buyers, `winner_hits++` if won, `total_observations++` always).
7. Process shadow-watches (§6e).

### 6d. Per-specialist learning isolation

Each specialist's subagent prompt includes ONLY:
- Global VALIDATED lessons (always)
- Global candidate lessons (always)
- ITS OWN per-specialist section
- Disconfirmed-signals section
- Frontmatter stats (so it knows its cold_start status)

Other specialists' lessons NOT in context. Cross-pollination happens through FM (sees all picks) and global lessons.

### 6e. Shadow-watch evolution mechanism

When a specialist's reasoning would have emitted BUY but a VALIDATED veto fired:

1. Specialist writes `predictions/diary/shadow_watches/<pick_id>-shadow.md` with `would_be_conviction, vetoed_by_lesson, specialist, entry_*`
2. Audit machinery audits shadow-watches at the specialist's normal exit horizon
3. If realized outcome contradicts the lesson's prediction, increment a disconfirm in `## Candidate refinement of <lesson_id> — <pattern_observed>` entry
4. After 3 disconfirms in same refinement-pattern, refinement promotes: strict veto becomes soft penalty (0.8) FOR THAT SUBREGIME ONLY

### 6f. Memory recall by subagents

Each subagent invocation receives:
- Full `lessons.md`
- Last 10 outcome files for ITS specialist
- Last 3 decision files from ITS specialist
- Smart-wallet registry (top-30)
- Specialist-specific state file from `predictions/state/`

FM additionally receives:
- Current cycle's specialist decision files
- Last 5 FM decision files + outcomes
- Full frontmatter stats

Context budget target: <30k tokens per subagent invocation.

### 6g. Manual memory hooks

- `lessons.md` is hand-editable: user can add notes, force-promote/demote lessons, mark a specialist dormant.
- Edits committed alongside automated updates; system respects human-edited fields on subsequent runs.

---

## 7. Cadence orchestration

### 7a. Crons (via CronCreate)

| Cron job | Schedule | Trigger fires |
|---|---|---|
| `pump-v2-late-curve` | `*/15 * * * *` | Late-curve momentum agent |
| `pump-v2-catalyst` | `0 * * * *` | Catalyst agent |
| `pump-v2-early-curve` | `0 */4 * * *` | Early-curve quality agent |
| `pump-v2-smart-mirror` | `15 */4 * * *` | Smart-mirror agent (offset 15min) |
| `pump-v2-fund-manager` | `30 */4 * * *` | Fund Manager (offset 30min) |
| `pump-v2-audit-tick` | `*/10 * * * *` | Audit machinery sweep |
| `pump-v2-universe-fetch` | `*/15 * * * *` | Shared universe scrape → SQLite |

### 7b. Cron handler

**`predictions/runner.py`** — single entry point:

```python
import sys
specialist = sys.argv[1]
if specialist == "audit_tick":     run_audit_machinery()
elif specialist == "fund_manager": run_fund_manager()
elif specialist == "universe_fetch": run_universe_fetch()
else:                                run_specialist(specialist)
```

Idempotent: same inputs → same outputs. Re-fires don't corrupt state.

### 7c. State coordination (no races)

1. **File-write atomicity**: write `<path>.tmp`, then `os.rename()`.
2. **FM input snapshot**: FM captures `ls predictions/diary/decisions/*` at start, processes only those.
3. **Cycle marker**: `predictions/state/last_fm_cycle.txt` touched after each FM run.

### 7d. Failure handling

- Each cron handler wrapped try/except → logs to `predictions/state/error_log.jsonl`
- Failed specialist emits degraded decision file (`status: error, picks: []`)
- FM treats degraded specialist as unavailable, proceeds with others
- 3 consecutive failures of any specialist → `specialist_health.<name>: degraded` in lessons.md, user alert
- 5+ total infra failures in 24h → halt FM cron, user alert

### 7e. Kill switch

`PUMP_V2_HALT=1` env var checked top of every cron. Clean stop without uninstalling crons.

### 7f. Fallback (if CronCreate unavailable)

Single 15-min ScheduleWakeup chain with dispatcher that checks `time_since_last_fire` per specialist. Less clean, same functional outcome.

---

## 8. Migration / v1 wind-down

### 8a. v1 cleanup before v2 lands

- **NOAR audit** (last open v1 WATCH, exits ~17:49 UTC 2026-05-23): run manually as one-off, write outcome, commit lessons.md update.
- **Mark v1 deprecated**: top of `.claude/skills/pump-prediction.md` gets banner pointing to v2.
- **Autonomous loop**: confirmed halted. ScheduleWakeup stragglers die when they fire ("loop halted").

### 8b. Helpers: kept, refactored, retired

**Kept**: `audit_outcome.py`, `helius_trade_flow.py`, `recent_graduations.py`, `pumpfun_scrape.py`.
**New**: `pumpfun_curve_universe.py`, `cryptopanic_feed.py`, `reddit_hot_posts.py`.
**Retired**: `telegram_chatter.py` (proven dead-weight in v1).

### 8c. New infrastructure

```
predictions/
├── runner.py                  ← NEW: single cron entry-point
├── universe.py                ← NEW: shared universe API
├── agents/                    ← NEW: subagent prompts as committed files
│   ├── late_curve.md
│   ├── early_curve.md
│   ├── smart_mirror.md
│   ├── catalyst.md
│   └── fund_manager.md
├── state/                     ← NEW (gitignored)
├── audit/                     ← NEW
│   └── pending.jsonl
└── helpers/, diary/
```

`.claude/skills/pump-prediction.md` replaced by `.claude/skills/pump-fund.md`.

### 8d. .env additions

```
CRYPTOPANIC_API_TOKEN=<from free signup>
PUMP_V2_HALT=0
```

### 8e. Smart-wallet registry one-time seed

At v2 first run, migration script:
- Dune historical query: tokens that graduated in last 30 days with realized return ≥ 5× post-grad peak
- For each, find first-hour buyers via Helius
- For wallets across multiple winners with `precision ≥ 0.3` AND `total_observations ≥ 3`, seed into `smart_wallet_registry.db` with status `seeded`
- Smart-mirror specialist dormant-gate passes only if seed yields ≥5 wallets

### 8f. Cutover order

1. Build v2 in feature branch
2. Final code review (subagent-driven-development)
3. Merge to main
4. Run NOAR audit (close v1)
5. Install crons via CronCreate
6. Run smart-wallet seed script once
7. Set `PUMP_V2_HALT=0`, system starts firing
8. Monitor first 24h closely
9. After 30 audits per specialist, FM transitions to mature mode automatically

### 8g. Rollback

- `PUMP_V2_HALT=1` (instant stop)
- Decision/outcome files preserved for forensics
- `lessons.md` git-tracked; revert restores v1 state
- Crons removable via CronDelete

---

## 9. Success criteria

The skill is considered successful in v2 if any of the following emerge within 30 days of cutover:

- Any specialist's `hit_rate_last_7d ≥ 0.20` (i.e., ≥1-in-5 picks reach their target exit)
- Fund Manager makes ≥1 BUY HIGH pick that audits at ≥ specialist target
- Smart-wallet registry produces ≥3 audit-confirmed winners on its mirror picks
- A shadow-watch refinement gets validated (the system disconfirmed its own veto on a real case)

The skill is considered failed and retired if after 30 days:
- All four specialists are stuck cold-start (no audits ≥ target)
- Cumulative paper return across BUY picks is below −50%
- No candidate lesson has reached even 1 audit-based confirmation

**Verdict horizon: 2026-06-23 (30 days from cutover, assuming early-June ship).**

---

## 10. Out of scope (explicitly)

- Real trade execution (still signal-only, no on-chain interaction beyond reads)
- Twitter API integration (rejected per user)
- Discord scraping (rejected per user)
- v3 ideas (e.g., per-mint feature stores, ML over specialist signals) — captured in lessons.md as future-work notes once v2 has audit data
