"""Stage Phase 6 — post-tick reflection inputs.

Runs AFTER Phase 5 (execution + report). For each prior-tick symbol, compute the
counterfactual delta over 1-tick (6h) and 4-tick (~24h) windows. Tag "interesting"
movers. Decide whether to fire the Reflector LLM.

Outputs:
- state/reflection_inputs.jsonl — append-only per tick: the what-if rows + dispatch decision
- /tmp/smaf_reflector_input.json — only if dispatching; the Reflector reads this

Dispatch triggers (any of):
- ≥1 REJECTED symbol moved >+5% in 6h window
- ≥1 REJECTED symbol moved >+10% in 24h window
- ≥1 EXECUTED SELL was followed by >+5% continuation in 6h (we exited too early)
- ≥4 ticks (~24h) since the last Reflector LLM dispatch, regardless

Usage:
    python3 -m predictions.fund.stage_phase6
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from predictions.fund import universe_price_history as uph

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
REFL_INPUTS_PATH = STATE / "reflection_inputs.jsonl"
REFL_OUT_PATH = Path("/tmp/smaf_reflector_input.json")
REFL_LESSONS_PATH = STATE / "lessons_reflections.jsonl"  # Reflector writes here

# Trigger thresholds (tunable). All thresholds apply BOTH directions —
# the system reflects on good decisions and revisit-worthy ones symmetrically.
TRIG_REJECT_6H_PCT = 5.0      # |delta| threshold for 6h rejection reflection
TRIG_REJECT_24H_PCT = 10.0    # |delta| threshold for 24h rejection reflection
TRIG_SELL_CONT_6H_PCT = 5.0   # |delta| threshold for 6h sell-follow-up
TRIG_ENTRY_6H_PCT = 5.0       # |delta| threshold for 6h entry-validation
TRIG_FORCE_AFTER_TICKS = 4


def _append_jsonl(path: Path, row: dict) -> None:
    existing = path.read_text() if path.exists() else ""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(existing + json.dumps(row, default=str) + "\n")
    tmp.rename(path)


def _last_llm_dispatch_tick() -> int | None:
    """Read reflection_inputs.jsonl; find the most recent tick that dispatched the LLM."""
    if not REFL_INPUTS_PATH.exists(): return None
    for line in reversed(REFL_INPUTS_PATH.read_text().splitlines()):
        if not line.strip(): continue
        try:
            r = json.loads(line)
            if r.get("dispatched_llm"):
                return r.get("tick_id")
        except Exception: pass
    return None


def _build_what_ifs(current_tick_id: int, current_prices: dict[str, float]) -> list[dict]:
    """For each row in history with tick_id < current and symbol present in current_prices,
    compute the price delta + counterfactual. Returns list of what-if rows."""
    history = uph.load_all()
    by_sym: dict[str, list[dict]] = {}
    for r in history:
        if r.get("tick_id") == current_tick_id: continue  # this tick's own row, skip
        by_sym.setdefault(r["symbol"], []).append(r)

    whatifs = []
    for symbol, rows in by_sym.items():
        cur_price = current_prices.get(symbol)
        if not cur_price: continue
        # Group by (tick_id, decision_tag) — we want one what-if per prior decision
        rows.sort(key=lambda x: x.get("tick_id", 0))
        for prior in rows[-4:]:  # only look at last 4 prior ticks per symbol
            prior_price = float(prior.get("price_usd") or 0)
            if prior_price <= 0: continue
            ticks_ago = current_tick_id - prior["tick_id"]
            window = "6h" if ticks_ago == 1 else f"{ticks_ago * 6}h"
            delta_pct = (cur_price / prior_price - 1.0) * 100
            # Counterfactual P&L: if we had bought at the rejected size_pct (or a default 5%)
            sized_pct = prior.get("risk_mgr_max_size_pct") or 5.0
            # Assume $10k notional account, 5% size = $500
            cf_usd = 500.0 * (delta_pct / 100.0)
            whatifs.append({
                "symbol": symbol,
                "prior_tick_id": prior["tick_id"],
                "current_tick_id": current_tick_id,
                "ticks_ago": ticks_ago,
                "window": window,
                "prior_price_usd": prior_price,
                "current_price_usd": cur_price,
                "delta_pct": round(delta_pct, 3),
                "counterfactual_pnl_usd": round(cf_usd, 2),
                "prior_decision_tag": prior.get("decision_tag"),
                "prior_consensus": prior.get("consensus"),
                "prior_ma_optimist": prior.get("ma_optimist"),
                "prior_ma_pessimist": prior.get("ma_pessimist"),
                "prior_se_optimist": prior.get("se_optimist"),
                "prior_se_pessimist": prior.get("se_pessimist"),
                "prior_market_disagreement": prior.get("market_disagreement"),
                "prior_onchain_disagreement": prior.get("onchain_disagreement"),
                "prior_combined_uncertainty": prior.get("combined_uncertainty"),
                "prior_rm_reason": prior.get("rm_reason"),
                "prior_regime": prior.get("regime_label"),
            })
    return whatifs


def _classify_triggers(whatifs: list[dict]) -> list[dict]:
    """Identify which what-ifs constitute interesting patterns worth LLM reflection.

    Symmetric — surfaces both wins and revisit-worthy outcomes.
    """
    triggers = []
    for w in whatifs:
        tag = (w.get("prior_decision_tag") or "")
        delta = w.get("delta_pct") or 0
        ticks = w.get("ticks_ago")

        # REJECTIONS — both directions
        if tag.startswith("REJECT") and ticks == 1:
            if delta >= TRIG_REJECT_6H_PCT:
                triggers.append({**w, "trigger_kind": "missed_winner_6h"})
            elif delta <= -TRIG_REJECT_6H_PCT:
                triggers.append({**w, "trigger_kind": "good_rejection_6h"})
        elif tag.startswith("REJECT") and ticks == 4:
            if delta >= TRIG_REJECT_24H_PCT:
                triggers.append({**w, "trigger_kind": "missed_winner_24h"})
            elif delta <= -TRIG_REJECT_24H_PCT:
                triggers.append({**w, "trigger_kind": "good_rejection_24h"})

        # SELLS — both directions
        elif tag.startswith("SELL_EXECUTED") and ticks == 1:
            if delta >= TRIG_SELL_CONT_6H_PCT:
                triggers.append({**w, "trigger_kind": "premature_exit_6h"})
            elif delta <= -TRIG_SELL_CONT_6H_PCT:
                triggers.append({**w, "trigger_kind": "good_exit_6h"})

        # ENTRIES — early validation (only up-move flagged; down is captured at SL/TP close)
        elif tag.startswith("BUY_EXECUTED") and ticks == 1:
            if delta >= TRIG_ENTRY_6H_PCT:
                triggers.append({**w, "trigger_kind": "good_entry_6h"})
            elif delta <= -TRIG_ENTRY_6H_PCT:
                triggers.append({**w, "trigger_kind": "entry_underwater_6h"})

    return triggers


def stage(current_tick_id: int, current_prices: dict[str, float]) -> dict:
    """Build what-ifs, decide dispatch, persist inputs."""
    whatifs = _build_what_ifs(current_tick_id, current_prices)
    triggers = _classify_triggers(whatifs)

    # Force trigger after N ticks of silence
    last_llm = _last_llm_dispatch_tick()
    force_dispatch = False
    if last_llm is None:
        # No prior dispatch ever — dispatch on the first tick that has any what-ifs to reflect on
        force_dispatch = bool(whatifs)
    elif current_tick_id - last_llm >= TRIG_FORCE_AFTER_TICKS:
        force_dispatch = True

    should_dispatch = bool(triggers) or force_dispatch
    # Also: never dispatch if there's literally nothing to reflect on (no whatifs yet)
    if not whatifs:
        should_dispatch = False

    record = {
        "tick_id": current_tick_id,
        "ts": int(time.time()),
        "iso_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_whatifs": len(whatifs),
        "n_triggers": len(triggers),
        "trigger_kinds": sorted({t["trigger_kind"] for t in triggers}),
        "force_dispatch": force_dispatch,
        "dispatched_llm": should_dispatch,
        "last_llm_dispatch_tick": last_llm,
    }
    _append_jsonl(REFL_INPUTS_PATH, record)

    if should_dispatch:
        # Read existing validated + candidate lessons so the agent doesn't restate them
        prior_reflections = []
        if REFL_LESSONS_PATH.exists():
            for line in REFL_LESSONS_PATH.read_text().splitlines()[-50:]:
                if line.strip():
                    try: prior_reflections.append(json.loads(line))
                    except Exception: pass

        payload = {
            "phase": "reflector_input",
            "tick_id": current_tick_id,
            "run_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "team_charter": (ROOT / "team_charter.md").read_text(),
            "trigger_kinds": record["trigger_kinds"],
            "force_dispatch": force_dispatch,
            "interesting_what_ifs": triggers,
            "all_what_ifs": whatifs,
            "prior_reflections_last_50": prior_reflections,
        }
        tmp = REFL_OUT_PATH.with_suffix(REFL_OUT_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str))
        tmp.rename(REFL_OUT_PATH)

    return {"record": record, "whatifs": whatifs, "triggers": triggers,
            "dispatched": should_dispatch, "reflector_input_path": str(REFL_OUT_PATH) if should_dispatch else None}


if __name__ == "__main__":
    # CLI: use current prices from latest tick_risk_input.json
    risk = json.load(open(STATE / "tick_risk_input.json"))
    per_sym = risk.get("specialist_consensus_per_symbol", {})
    cur_prices = {t: float(d.get("current_price_usd") or 0) for t, d in per_sym.items()}
    current_tick = uph.latest_tick_id() or 0
    r = stage(current_tick, cur_prices)
    print(json.dumps(r["record"], indent=2))
    print()
    if r["triggers"]:
        print(f"Triggers ({len(r['triggers'])}):")
        for t in r["triggers"]:
            print(f"  [{t['trigger_kind']}] {t['symbol']} {t['delta_pct']:+.1f}% in {t['window']} "
                  f"(was {t['prior_decision_tag']} at consensus {t['prior_consensus']})")
    else:
        print("No triggers fired this tick.")
    if r["dispatched"]:
        print(f"\nDispatched LLM. Input at {r['reflector_input_path']}")
    else:
        print("\nNo LLM dispatch this tick.")
