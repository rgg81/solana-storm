"""Phase 6 orchestrator — runs AFTER Phase 5 (execute + report).

Pipeline:
1. Snapshot universe prices/scores/decisions to universe_price_history.jsonl
2. Compute what-ifs vs prior snapshots
3. Decide whether to dispatch the Reflector LLM
4. (Caller dispatches the LLM if requested; this orchestrator returns the input path)
5. After Reflector LLM responds, caller calls `persist_reflector_output(path)`
   to append the full Reflector output (summary, watchlist, decision_outcomes)
   to state/reflector_runs.jsonl AND append any candidate/confirmation rows
   to state/lessons_reflections.jsonl.

Idempotent: re-running on the same tick is a no-op (the snapshot check returns 0).
"""
from __future__ import annotations
import json, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from predictions.fund import universe_price_history as uph, stage_phase6, regime, lessons_io

STATE = Path(__file__).resolve().parent / "state"
REFLECTOR_RUNS_PATH = STATE / "reflector_runs.jsonl"


def _append_jsonl(path: Path, row: dict) -> None:
    existing = path.read_text() if path.exists() else ""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(existing + json.dumps(row, default=str) + "\n")
    tmp.rename(path)


def persist_reflector_output(reflector_json_path: str | Path,
                              tick_id: int | None = None) -> dict:
    """Persist the Reflector subagent's full output to:
    - state/reflector_runs.jsonl (full run audit trail — summary, watchlist, decisions)
    - state/lessons_reflections.jsonl (only candidate/confirmation rows for aggregation)

    Idempotent: refuses to persist if the last reflector_runs.jsonl row has the same tick_id.
    Returns: {persisted: bool, n_candidates: int, n_confirmations: int, tick_id: int}
    """
    p = Path(reflector_json_path)
    if not p.exists():
        return {"persisted": False, "error": f"no file at {p}"}
    data = json.loads(p.read_text())
    if tick_id is None:
        tick_id = data.get("tick_id") or (uph.latest_tick_id() or 0)

    # Idempotency guard
    if REFLECTOR_RUNS_PATH.exists():
        for line in reversed(REFLECTOR_RUNS_PATH.read_text().splitlines()):
            if line.strip():
                try:
                    if json.loads(line).get("tick_id") == tick_id:
                        return {"persisted": False, "reason": "already_persisted", "tick_id": tick_id}
                    break
                except Exception: pass

    # Persist full run
    run_row = {
        "tick_id": tick_id,
        "run_time_utc": data.get("run_time_utc"),
        "summary": data.get("summary"),
        "notes_for_watchlist": data.get("notes_for_watchlist", []),
        "decision_outcomes_summary": data.get("decision_outcomes_summary", {}),
        "n_new_candidates": len(data.get("new_candidates", [])),
        "n_confirmations": len(data.get("confirmations", [])),
    }
    _append_jsonl(REFLECTOR_RUNS_PATH, run_row)

    # Persist candidates + confirmations (the aggregation pipeline expects these)
    import uuid
    n_cand = 0
    for c in data.get("new_candidates", []):
        cid = c.get("candidate_id") or f"cand_{tick_id}_{uuid.uuid4().hex[:8]}"
        row = {
            "kind_row": "new_candidate",
            "candidate_id": cid,
            "tick_id": tick_id,
            "kind": c.get("kind"),
            "pattern": c.get("pattern"),
            "candidate_lesson": c.get("candidate_lesson"),
            "affects": c.get("affects", []),
            "supporting_count": c.get("supporting_count", 1),
            "supporting_what_ifs": c.get("supporting_what_ifs", []),
        }
        lessons_io.append_reflection(row)
        n_cand += 1

    n_conf = 0
    for cf in data.get("confirmations", []):
        row = {
            "kind_row": "confirmation",
            "prior_candidate_id": cf.get("prior_candidate_id"),
            "tick_id": tick_id,
            "kind": cf.get("kind"),
            "evidence": cf.get("evidence"),
            "new_supporting_count": cf.get("new_supporting_count"),
            "new_status_suggestion": cf.get("new_status_suggestion"),
        }
        lessons_io.append_reflection(row)
        n_conf += 1

    return {"persisted": True, "tick_id": tick_id, "n_candidates": n_cand,
            "n_confirmations": n_conf, "n_watchlist": len(data.get("notes_for_watchlist", []))}


def latest_reflector_run() -> dict | None:
    """Return the most recent reflector run row (used by report.py Section 8)."""
    if not REFLECTOR_RUNS_PATH.exists(): return None
    for line in reversed(REFLECTOR_RUNS_PATH.read_text().splitlines()):
        if line.strip():
            try: return json.loads(line)
            except Exception: pass
    return None


def run(regime_label: str | None = None) -> dict:
    """Execute Phase 6. Returns a result dict for the orchestrator/caller."""
    # Load this-tick artifacts
    risk_path = STATE / "tick_risk_input.json"
    pm_path = Path("/tmp/smaf_pm.json")
    if not risk_path.exists():
        return {"error": "no tick_risk_input.json — Phase 3 must have run", "dispatch_llm": False}
    if not pm_path.exists():
        return {"error": "no /tmp/smaf_pm.json — Phase 4 must have run", "dispatch_llm": False}

    risk = json.loads(risk_path.read_text())
    pm = json.loads(pm_path.read_text())

    # Pick a fresh, monotonic tick_id (independent of equity.jsonl wiggle)
    new_tick_id = (uph.latest_tick_id() or 0) + 1

    # Regime label — read from cache if not provided
    if regime_label is None:
        try:
            rc = STATE / "regime_cache.json"
            if rc.exists():
                regime_label = json.loads(rc.read_text()).get("sol_regime", {}).get("trend")
        except Exception: regime_label = None
        if regime_label is None:
            try: regime_label = regime.detect_sol_regime().get("trend")
            except Exception: pass

    # Step 1: snapshot
    n_snapped = uph.snapshot_tick(tick_id=new_tick_id, risk_input=risk,
                                    pm_output=pm, regime_label=regime_label)

    # Step 2-3: what-ifs + dispatch decision (built against current prices from risk input)
    per_sym = risk.get("specialist_consensus_per_symbol", {})
    cur_prices = {t: float(d.get("current_price_usd") or 0) for t, d in per_sym.items()}
    stage_result = stage_phase6.stage(new_tick_id, cur_prices)

    return {
        "tick_id": new_tick_id,
        "n_snapshotted": n_snapped,
        "n_whatifs": stage_result["record"]["n_whatifs"],
        "n_triggers": stage_result["record"]["n_triggers"],
        "trigger_kinds": stage_result["record"]["trigger_kinds"],
        "dispatch_llm": stage_result["dispatched"],
        "reflector_input_path": stage_result.get("reflector_input_path"),
        "triggers": stage_result["triggers"],
    }


if __name__ == "__main__":
    r = run()
    print(json.dumps(r, indent=2, default=str))
