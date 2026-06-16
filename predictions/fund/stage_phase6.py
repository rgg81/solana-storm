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
from predictions.fund import bugs

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

# Sanity clamps on delta_pct: physically implausible single-tick moves indicate
# corrupted DexScreener / wrong-pool quotes, NOT a real move. Drop them from
# trigger classification so they don't fire spurious reflector triggers.
#
# - Positive cap 500%: catches the tick-45 JUP case where the prior corrupt
#   $1026 vs current $0.21 produces a +5000% delta when viewed in reverse.
# - Negative cap 95%: catches the tick-92 TRUMP case where the prior corrupt
#   $7946 (wrong DexScreener pool) vs current $1.57 produces a -99.98% delta.
#   Real $200M+ mcap tokens don't drop 95% in a single 6h-24h window; if a
#   memecoin legitimately rugs that hard within one tick we'd want to know, but
#   the false-positive rate of DexScreener pool-swap artifacts dominates.
ANOMALY_DELTA_PCT_POS_THRESHOLD = 500.0
ANOMALY_DELTA_PCT_NEG_THRESHOLD = -95.0
# Back-compat alias for any external callers / older imports
ANOMALY_DELTA_PCT_THRESHOLD = ANOMALY_DELTA_PCT_POS_THRESHOLD

# Audit-gate: a rejection only "paid off" if the team plausibly could have entered.
# Per conservatism audit 2026-06-01: require max consensus in the rejection-to-now
# window to be within FLOOR_CONTEST_BAND of the regime floor. Otherwise the symbol
# was never an entry candidate; subsequent downside is luck not validated discipline.
GOOD_REJECTION_CONTEST_THRESHOLD = 0.35   # = floor (0.40 strong_bear) - 0.05

# Reflection-symmetry (2026-06-16, desk-forensics wf_8321460b): a rejection that
# ROSE is an *enterable* missed winner (a real opportunity cost the framework
# should learn FROM) only if the team plausibly could have entered it — i.e. its
# optimist_consensus reached the $125 probe bar (>= +0.30) somewhere in the
# window. Below that bar the up-move is uncontested tape-beta (luck), a plain
# missed_winner. This is the for-action counterpart to GOOD_REJECTION_CONTEST.
PROBE_OPTIMIST_BAR = 0.30


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
            # Max consensus observed for this symbol from rejection-tick through now —
            # used to gate "good_rejection" as contested-vs-uncontested (audit gate).
            window_rows = [r for r in rows if prior["tick_id"] <= r.get("tick_id", 0) <= current_tick_id]
            cons_values = [float(r.get("consensus") or 0) for r in window_rows]
            max_consensus_in_window = max(cons_values) if cons_values else float(prior.get("consensus") or 0)
            # Symmetric for-action gate: max optimist_consensus = (ma_opt+se_opt)/2
            # reached in the window — the bar the $125 probe now uses. Computed
            # from stored ma_optimist/se_optimist so it works on historical rows.
            opt_values = [(float(r.get("ma_optimist") or 0) + float(r.get("se_optimist") or 0)) / 2
                          for r in window_rows]
            max_opt_cons_in_window = max(opt_values) if opt_values else \
                (float(prior.get("ma_optimist") or 0) + float(prior.get("se_optimist") or 0)) / 2
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
                "max_consensus_in_window": round(max_consensus_in_window, 3),
                "max_optimist_consensus_in_window": round(max_opt_cons_in_window, 3),
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

        # Sanity clamps: implausible single-tick moves are data corruption,
        # not real signals. Skip them so they don't fire spurious triggers.
        # Symmetric (positive >500% AND negative <-95%) — see ANOMALY_*_THRESHOLD.
        if delta > ANOMALY_DELTA_PCT_POS_THRESHOLD or delta < ANOMALY_DELTA_PCT_NEG_THRESHOLD:
            try:
                bugs.log(
                    "MEDIUM",
                    "phase6.anomaly_delta_clamp",
                    f"{w['symbol']}: delta {delta:.1f}% in {w['window']} clamped — likely DexScreener pool error",
                    context={"symbol": w["symbol"], "window": w["window"],
                             "delta_pct": delta, "prior_price_usd": w.get("prior_price_usd"),
                             "current_price_usd": w.get("current_price_usd"),
                             "prior_tick_id": w.get("prior_tick_id"),
                             "current_tick_id": w.get("current_tick_id")},
                )
            except Exception:
                # Never let the audit log itself crash the trigger classification.
                pass
            continue

        # REJECTIONS — both directions.
        # good_rejection is gated on max_consensus_in_window >= contest threshold:
        # if consensus never approached the floor in the window, the symbol wasn't
        # a real entry candidate and avoiding it isn't a validated discipline win.
        max_cons = w.get("max_consensus_in_window", w.get("prior_consensus") or 0)
        contested = max_cons >= GOOD_REJECTION_CONTEST_THRESHOLD
        # Symmetric for-action gate: did the rejection ever clear the probe bar?
        # If so an up-move is an ENTERABLE miss (real opportunity cost), not luck.
        max_opt = w.get("max_optimist_consensus_in_window", 0) or 0
        enterable = max_opt >= PROBE_OPTIMIST_BAR
        if tag.startswith("REJECT") and ticks == 1:
            if delta >= TRIG_REJECT_6H_PCT:
                kind = "enterable_missed_winner_6h" if enterable else "missed_winner_6h"
                triggers.append({**w, "trigger_kind": kind})
            elif delta <= -TRIG_REJECT_6H_PCT:
                kind = "good_rejection_6h" if contested else "uncontested_rejection_down_6h"
                triggers.append({**w, "trigger_kind": kind})
        elif tag.startswith("REJECT") and ticks == 4:
            if delta >= TRIG_REJECT_24H_PCT:
                kind = "enterable_missed_winner_24h" if enterable else "missed_winner_24h"
                triggers.append({**w, "trigger_kind": kind})
            elif delta <= -TRIG_REJECT_24H_PCT:
                kind = "good_rejection_24h" if contested else "uncontested_rejection_down_24h"
                triggers.append({**w, "trigger_kind": kind})

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
