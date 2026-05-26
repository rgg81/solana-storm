"""Phase 6 orchestrator — runs AFTER Phase 5 (execute + report).

Pipeline:
1. Snapshot universe prices/scores/decisions to universe_price_history.jsonl
2. Compute what-ifs vs prior snapshots
3. Decide whether to dispatch the Reflector LLM
4. (Caller dispatches the LLM if requested; this orchestrator returns the input path)

This is callable from a single line at the end of any tick:
    >>> from predictions.fund import phase6_orchestrator
    >>> r = phase6_orchestrator.run()
    >>> if r['dispatch_llm']: dispatch_reflector_agent(r['reflector_input_path'])

Idempotent: re-running on the same tick is a no-op (the snapshot check returns 0).
"""
from __future__ import annotations
import json, sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from predictions.fund import universe_price_history as uph, stage_phase6, regime

STATE = Path(__file__).resolve().parent / "state"


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
