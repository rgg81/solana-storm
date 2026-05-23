---
name: pump-fund
description: Multi-agent pump.fun fund. Invoke once to start the autonomous loop. Each tick (~4h) runs Phase 1 audits + dispatches 4 specialists in parallel + Fund Manager consolidation. Self-reschedules via ScheduleWakeup chain. Surfaces only on BUY conviction, lesson milestones, dramatic audit outcomes, or infra failures.
---

# pump-fund — v2 autonomous loop

You are operating the v2 multi-agent pump.fun fund. The user invokes you once; you then run continuously via chained ScheduleWakeup calls (~4h between real ticks). Each real tick does: audits due picks → fetches fresh universe → dispatches 4 specialist subagents in parallel → Fund Manager consolidates → writes decision file → reschedules.

## Setup (read this once per session)

- Working dir: `/home/roberto/solana-storm`. Branch: `main`. Never switch branches.
- Source `.env` for `HELIUS_API_KEY`, `DUNE_API_KEY`, `CRYPTOPANIC_API_TOKEN`. Note: the v1 `.env` may have `HELIUS_API_KEY=PASTE_YOUR_API_KEY_HERE` placeholder — `predictions/config.py:60-65` falls back to `SOLANA_RPC_URL` automatically.
- `PUMP_V2_HALT=1` env var halts everything on next tick (kill switch). Check at top of each tick.
- Hard stop conditions: explicit user "stop" / 30+ audits with declining 7d hit rate / past 2026-06-23 with no edge / 5+ consecutive infra failures (Helius + Dune both down).

## Per-tick decision: chain or run?

1. Find newest non-SKIPPED FM decision file: `predictions/diary/decisions/<ts>-fund_manager.md`
2. If less than 4 hours have passed since its mtime: **CHAIN ONLY** — `ScheduleWakeup(3600s, reason="Chain step N/4 toward next pump-fund tick", prompt=<same /pump-fund self-instruction>)` and end the turn.
3. If ≥4 hours have passed (or no FM file exists yet): **RUN THE TICK** as below.

## Phase 1 — Audit (process due picks)

Single Bash invocation:

```bash
cd /home/roberto/solana-storm
set -a; source .env; set +a
python3 predictions/runner.py audit_tick
```

This calls `processor.process_due_audits` which sweeps `predictions/audit/pending.jsonl` AND `predictions/diary/shadow_watches/*-shadow.md` for due items, audits them via Helius (`audit_outcome.py`), writes outcome files, updates per-specialist hit rates in `lessons.md` frontmatter, and rewrites `pending.jsonl` with the remainder.

If `audit_tick: processed N due audits` reports N>0, check whether any outcome was **dramatic** (realized_return ≥ +0.5 OR pool_closed=true) by reading the most recently created outcome file(s) in `predictions/diary/outcomes/`. Hold those for surface-conditions evaluation in Phase 5.

## Phase 2 — Universe fetch + state recording

```bash
python3 predictions/runner.py universe_fetch
```

Pulls the pre-grad universe from pump.fun's `/coins` endpoint, deduplicates against prior cycles, and writes snapshots to `predictions/state/curve_history.db`. Report from stdout: `universe_fetch: recorded N snapshots`.

If N=0 or the command errors with a Dune/pumpfun outage, set the relevant `*_available: false` for downstream context and note the infra-failure count toward the stop condition.

## Phase 3 — Specialist dispatch (4 subagents in parallel)

Read the universe + curve history + lessons.md once, then dispatch 4 specialists in parallel via the `Agent` tool. Each gets its specialist-specific prompt template plus shared context. All 4 should be dispatched in **one tool-call block** so they run concurrently.

### Per-specialist prompt construction (do this in Python via skill_helpers, then pass the string to Agent):

```bash
cd /home/roberto/solana-storm
set -a; source .env; set +a
python3 << 'PY'
import json, re, subprocess, sys
from pathlib import Path
from predictions import universe
from predictions.agents import invoker
from predictions.diary import lessons_io

# Universe slice
pregrad = universe.fetch_pregrad_universe()
graduated = universe.fetch_graduated_universe()
lessons_md = lessons_io.load_body(Path("predictions/diary/lessons.md"))

# Extract up to 10 catalyst-eligible tickers from the combined universe.
# Filter rules: 3+ chars, alphanumeric, skip generic noise like 'joy'/'cl'/'24'.
def _extract_tickers(*sources, max_n=10):
    seen, out = set(), []
    skip = {"JOY", "CL", "24", "TEST", "TOKEN", "MEME", "COIN"}
    for src in sources:
        rows = (src or {}).get("data") or src or []
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("pregrad") or rows.get("graduated") or []
        for r in rows:
            if not isinstance(r, dict):
                continue
            sym = str(r.get("symbol") or "").strip().upper()
            if len(sym) < 3 or not re.fullmatch(r"[A-Z0-9]+", sym):
                continue
            if sym in skip or sym in seen:
                continue
            seen.add(sym); out.append(sym)
            if len(out) >= max_n:
                return out
    return out

tickers = _extract_tickers(pregrad, graduated, max_n=10)
ticker_csv = ",".join(tickers)

# Run cryptopanic + reddit helpers once for the catalyst extras.
catalyst_extras = {}
if ticker_csv:
    try:
        cp = subprocess.run(
            ["python3", "predictions/helpers/cryptopanic_feed.py", "--tickers", ticker_csv],
            capture_output=True, text=True, timeout=60,
        )
        catalyst_extras["cryptopanic_feed"] = json.loads(cp.stdout) if cp.stdout.strip() else {"data": None, "error": "empty stdout"}
    except Exception as e:
        catalyst_extras["cryptopanic_feed"] = {"data": None, "error": f"cryptopanic helper failed: {e}"}
    try:
        rd = subprocess.run(
            ["python3", "predictions/helpers/reddit_hot_posts.py", "--tickers", ticker_csv],
            capture_output=True, text=True, timeout=60,
        )
        catalyst_extras["reddit_hot_posts"] = json.loads(rd.stdout) if rd.stdout.strip() else {"data": None, "error": "empty stdout"}
    except Exception as e:
        catalyst_extras["reddit_hot_posts"] = {"data": None, "error": f"reddit helper failed: {e}"}
else:
    catalyst_extras["cryptopanic_feed"] = {"data": None, "error": "no eligible tickers in universe"}
    catalyst_extras["reddit_hot_posts"] = {"data": None, "error": "no eligible tickers in universe"}

for spec in ("late_curve", "early_curve", "smart_mirror", "catalyst"):
    extras = catalyst_extras if spec == "catalyst" else {}
    ctx = invoker.build_context(
        spec,
        universe={"pregrad": pregrad, "graduated": graduated},
        curve_history={},  # specialists request specific mints' history via the curve_history field as needed
        extras=extras,
    )
    out = (Path("/tmp") / f"prompt_{spec}.txt")
    template = ctx["prompt_template"]
    inputs = json.dumps({"universe": ctx["universe"], "extras": ctx["extras"]}, indent=2)
    prompt = (f"{template}\n\n## Current inputs\n```json\n{inputs}\n```\n\n"
              f"## Current lessons.md\n```markdown\n{lessons_md}\n```\n\n"
              f"Respond with the JSON output object only.")
    out.write_text(prompt)
    print(f"wrote {out}")
PY
```

Then dispatch 4 `Agent` subagents IN ONE BLOCK (parallel):

- Subagent #1 (`general-purpose`): description="Late-curve momentum", prompt=contents of `/tmp/prompt_late_curve.txt`
- Subagent #2 (`general-purpose`): description="Early-curve quality", prompt=contents of `/tmp/prompt_early_curve.txt`
- Subagent #3 (`general-purpose`): description="Smart-mirror", prompt=contents of `/tmp/prompt_smart_mirror.txt` (will return dormant payload if registry empty)
- Subagent #4 (`general-purpose`): description="Catalyst", prompt=contents of `/tmp/prompt_catalyst.txt`

Each subagent returns JSON per its spec (see `predictions/agents/<specialist>.md` for the contract). Save each subagent's JSON output to `/tmp/output_<specialist>.json`.

Then persist specialist decision files + shadow-watches:

```bash
cd /home/roberto/solana-storm
python3 << 'PY'
import json, time
from pathlib import Path
from predictions import config
from predictions.diary import shadow_watches

ts = time.strftime("%Y-%m-%d-%H-%M", time.gmtime())
decisions_dir = config._REPO_ROOT / "predictions" / "diary" / "decisions"
decisions_dir.mkdir(parents=True, exist_ok=True)

for spec in ("late_curve", "early_curve", "smart_mirror", "catalyst"):
    src = Path(f"/tmp/output_{spec}.json")
    if not src.exists():
        print(f"{spec}: missing output, skipping")
        continue
    try:
        result = json.loads(src.read_text())
    except Exception as e:
        print(f"{spec}: parse error {e}, skipping")
        continue

    # Write decision file
    out = decisions_dir / f"{ts}-{spec}.md"
    body = "---\n" + "\n".join(
        f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float)) else v}"
        for k, v in result.items()
    ) + "\n---\n"
    tmp = out.with_suffix(".tmp"); tmp.write_text(body); tmp.rename(out)
    print(f"{spec}: wrote {out.name}")

    # Persist shadow-watches
    for sw in (result.get("shadow_watches") or []):
        try:
            shadow_watches.write_shadow_watch(
                specialist=spec,
                mint=str(sw.get("mint","")),
                pool=str(sw.get("pool","")),
                would_be_conviction=str(sw.get("would_be_conviction","WATCH")),
                vetoed_by=str(sw.get("vetoed_by","unknown")),
                entry_quote=int(sw.get("entry_quote_lamports",0)),
                entry_base=int(sw.get("entry_base_lamports",0)),
                recommended_exit=sw.get("recommended_exit") or {"rule":"default","hard_timeout_hours":24},
            )
        except Exception as e:
            print(f"  shadow-watch skip: {e}")

    # Enqueue WATCH/BUY picks for future audit
    from predictions.audit import processor
    for pick in (result.get("picks") or []):
        conv = pick.get("conviction") or "SKIP"
        if conv == "SKIP":
            continue
        ex = pick.get("recommended_exit") or {}
        horizon_h = int(ex.get("hard_timeout_hours") or 24)
        processor.enqueue(config.PENDING_AUDIT_PATH, {
            "pick_id": f"{int(time.time())}-{spec}-{pick.get('mint','')[:8]}",
            "mint": pick.get("mint",""), "pool": pick.get("pool",""),
            "specialist": spec, "conviction": conv,
            "entry_quote_lamports": int(pick.get("entry_pool_quote_reserve") or 0),
            "entry_base_lamports": int(pick.get("entry_pool_base_reserve") or 0),
            "due_unix": int(time.time()) + horizon_h * 3600,
            "recommended_exit": ex,
        })
PY
```

## Phase 4 — Fund Manager consolidation (1 subagent)

Build FM context with computed scored_picks/sizes, then dispatch FM via Agent tool:

```bash
cd /home/roberto/solana-storm
python3 << 'PY'
import json
from pathlib import Path
from predictions import config
from predictions.agents import dispatch, invoker, fm_allocation
from predictions.diary import lessons_io

# dispatch.dispatch_fund_manager has the scored_picks/sizes math; we want the prompt OUT,
# not the agent call. Re-implement minimally here so we control the Agent invocation.
specialist_files = dispatch._collect_specialist_files()
lessons_path = config._REPO_ROOT / "predictions" / "diary" / "lessons.md"
fm = lessons_io.load_frontmatter(lessons_path)
stats = {s: fm.get(s) or {} for s in ("late_curve","early_curve","smart_mirror","catalyst")}
weights = fm_allocation.specialist_weights(stats)
total = int(fm.get("total_picks_audited") or 0)
cold_start = fm_allocation.is_cold_start_total(total)

scored = []
pick_meta = {}
convergence = {}
for entry in specialist_files:
    try:
        content = Path(entry["path"]).read_text()
        fmd = dispatch._extract_frontmatter_dict(content)
        picks = fmd.get("picks") or []
        for p in picks:
            if not isinstance(p, dict): continue
            if (p.get("conviction") or "SKIP") != "SKIP" and p.get("mint"):
                convergence[p["mint"]] = convergence.get(p["mint"], 0) + 1
    except Exception: continue

for entry in specialist_files:
    spec = entry["specialist"]
    try:
        content = Path(entry["path"]).read_text()
        fmd = dispatch._extract_frontmatter_dict(content)
        for p in (fmd.get("picks") or []):
            if not isinstance(p, dict): continue
            conv = p.get("conviction") or "SKIP"
            mint = p.get("mint","")
            if not mint: continue
            pid = f"{spec}-{mint}"
            s = fm_allocation.score_pick(
                specialist=spec, conviction=conv,
                specialist_weight=weights.get(spec,1.0),
                validated_lesson_fires=False,  # specialists already shadow-watch C1-vetoed picks
                candidate_lesson_fires=0,
                convergence_count=convergence.get(mint,1),
            )
            if s > 0:
                scored.append((pid, s))
                pick_meta[pid] = {
                    "specialist": spec, "mint": mint,
                    "ticker": p.get("ticker",""), "conviction": conv,
                    "exit_rule": (p.get("recommended_exit") or {}).get("rule",""),
                    "convergence_count": convergence.get(mint,1),
                }
    except Exception: continue

sizes = fm_allocation.compute_sizes(scored, cold_start=cold_start)

extras = {
    "specialist_outputs": specialist_files,
    "specialist_weights": weights,
    "scored_picks": scored,
    "recommended_sizes": sizes,
    "pick_metadata": pick_meta,
    "cold_start_mode": cold_start,
    "total_picks_audited": total,
}

ctx = invoker.build_context("fund_manager", universe={"data":[]}, curve_history=None, extras=extras)
template = ctx["prompt_template"]
inputs = json.dumps({"extras": ctx["extras"]}, indent=2)
prompt = (f"{template}\n\n## Current inputs\n```json\n{inputs}\n```\n\n"
          f"## Current lessons.md\n```markdown\n{ctx['lessons_md']}\n```\n\n"
          f"Respond with the JSON output object only.")
Path("/tmp/prompt_fund_manager.txt").write_text(prompt)
print(f"FM prompt ready: {len(prompt)} chars, {len(scored)} scored picks, cold_start={cold_start}")
PY
```

Then dispatch 1 `Agent` (general-purpose) with the FM prompt. Save output to `/tmp/output_fund_manager.json`.

Then persist the FM decision file:

```bash
cd /home/roberto/solana-storm
python3 << 'PY'
import json, time
from pathlib import Path
from predictions import config
ts = time.strftime("%Y-%m-%d-%H-%M", time.gmtime())
src = Path("/tmp/output_fund_manager.json")
result = json.loads(src.read_text())
decisions = config._REPO_ROOT / "predictions" / "diary" / "decisions"
out = decisions / f"{ts}-fund_manager.md"
body = "---\n" + "\n".join(
    f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float)) else v}"
    for k, v in result.items()
) + "\n---\n"
tmp = out.with_suffix(".tmp"); tmp.write_text(body); tmp.rename(out)
config.LAST_FM_CYCLE_PATH.parent.mkdir(parents=True, exist_ok=True)
config.LAST_FM_CYCLE_PATH.write_text(str(int(time.time())))
print(f"FM: wrote {out.name}, buy_high={(result.get('summary_counts') or {}).get('buy_high',0)}, buy_medium={(result.get('summary_counts') or {}).get('buy_medium',0)}")
PY
```

## Phase 5 — Surface conditions

Surface to the user (brief message in your reply) ONLY if any of:

- **BUY conviction fired** (HIGH or MEDIUM in `final_decisions`)
- **Lesson milestone**: a candidate lesson promoted to VALIDATED, or a VALIDATED lesson got a refinement
- **Dramatic audit**: any pick this cycle realized ≥+50% OR pool_closed (rug)
- **Infrastructure regression**: pumpfun/cryptopanic/dune/helius unreachable repeatedly (3+ consecutive cycles)
- **3+ consecutive all-SKIP runs** (regress to "still no edge" warning)
- **Health flag**: `buy_hit_rate_last_7d <= buy_hit_rate_first_7d` at 30+ audits — the skill's built-in kill switch

If NONE of those conditions met, end the turn with a one-line acknowledgment (e.g., "Tick complete: 0 BUY / N WATCH / M SKIP, next ~4h").

## Phase 6 — Reschedule

```
ScheduleWakeup(
  delaySeconds=3600,
  reason="Chain step 1/4 toward next pump-fund tick (last run at <ts>)",
  prompt="<the full self-invocation: same content as the user's /pump-fund trigger, parameterized so it re-enters this skill>"
)
```

The wake-up's `prompt` should be a self-contained re-invocation of this skill (e.g., "You own the pump-fund skill (see memory: pump-prediction-skill-owned). On this wake-up, follow `.claude/skills/pump-fund.md` from 'Per-tick decision' onward.").

## Bootstrap heuristics (cold-start: <20 audits total)

When the FM is in cold-start mode (`total_picks_audited < 20`), conservatism dominates:
- Max single position 10% (vs 20% mature)
- Max book deployed 50% (vs 80% mature)
- FM's adversarial skeptic challenge MUST resolve to "kept" — any plausible disconfirm downgrades the conviction

When `picks_audited` for any specialist < 30, that specialist's weight is 1.0 (equal-weight contribution). Past 30, weight = max(0.1, hit_rate_last_30d).

## Stop conditions (end loop, send summary)

Do NOT reschedule, and write a final summary in your reply, when any of:
- User says "stop" / interrupts the loop
- 30+ audits with `buy_hit_rate_last_7d <= buy_hit_rate_first_7d` (skill not learning)
- Current date > 2026-06-23 with no improving trend (verdict horizon reached)
- 5+ consecutive cycles with both Helius AND Dune unreachable
- Any catastrophic data-integrity issue (lessons.md corrupted, etc.)

## Quick reference paths

- Diary lessons (committed): `predictions/diary/lessons.md`
- Decision files (gitignored): `predictions/diary/decisions/<ts>-<specialist>.md` and `<ts>-fund_manager.md`
- Outcome files (gitignored): `predictions/diary/outcomes/<pick_id>-outcome.md`
- Shadow watches (gitignored): `predictions/diary/shadow_watches/<pick_id>-shadow.md`
- Pending audits (gitignored): `predictions/audit/pending.jsonl`
- Curve history (gitignored): `predictions/state/curve_history.db`
- Specialist prompts: `predictions/agents/<specialist>.md` (5 files)
- Helpers: `predictions/helpers/{pumpfun_curve_universe,helius_trade_flow,audit_outcome,pumpfun_scrape,recent_graduations,cryptopanic_feed,reddit_hot_posts}.py`

## Verdict horizon

Spec §9 sets 2026-06-23 as the 30-day v2 viability check. After that date, if no specialist hit ≥20% 7-day hit rate AND no candidate lesson has confirmed AND cumulative paper return < −50%, the skill is failed and should be retired.
