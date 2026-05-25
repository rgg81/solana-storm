"""Reflection agent for SMAF.

Runs ONCE per closed trade (not per tick). Inputs:
  - Entry consensus snapshot (4 specialist scores + disagreements)
  - Realized P&L + exit reason
  - Lessons.md current state
  - Recent ticks' equity curve

Outputs structured reflections to predictions/fund/state/reflections.jsonl:
  - lesson_candidate: a textual pattern observed
  - parameter_suggestion: which risk calibration parameter to nudge
  - per_symbol_observation: insight about a specific symbol

These reflections are DESCRIPTIVE — they do NOT auto-apply. risk_calibration.py
auto-tunes parameters from raw data; the reflector generates HUMAN-READABLE
narratives about what's happening (for the user's review and for next-tick
agent prompts to consume as context).
"""
from __future__ import annotations
import json, time, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_STATE_DIR = Path(__file__).resolve().parent / "state"
REFLECTIONS_PATH = _STATE_DIR / "reflections.jsonl"


def _append_jsonl(path: Path, row: dict) -> None:
    existing = path.read_text() if path.exists() else ""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(existing + json.dumps(row) + "\n")
    tmp.rename(path)


def reflect_on_close(audit_event: dict) -> dict:
    """Given an audit_close event, generate structured reflection.

    Called by audit.audit_close() after scoreboard update.
    """
    ticker = audit_event["ticker"]
    realized_pct = audit_event["realized_pct"]
    was_winner = audit_event["was_winner"]
    entry = audit_event["entry_consensus"]
    correct = audit_event["specialists_correct"]
    bucket = audit_event["disagreement_bucket"]
    exit_reason = audit_event["exit_reason"]
    
    reflection = {
        "timestamp": int(time.time()),
        "ticker": ticker,
        "realized_pct": realized_pct,
        "was_winner": was_winner,
        "exit_reason": exit_reason,
        "lesson_candidates": [],
        "parameter_suggestions": [],
        "per_symbol_observations": [],
        "specialist_observations": [],
    }
    
    # === Heuristic 1: stop-loss outcome → calibration suggestion ===
    if exit_reason in ("stop_loss_verified", "stop_loss") and was_winner is False:
        reflection["parameter_suggestions"].append({
            "param": "stop_vol_multiplier",
            "direction": "neutral",
            "reasoning": f"Stop saved capital: filled at -{abs(realized_pct):.1f}%. Stop discipline appropriate.",
        })
    elif exit_reason == "take_profit" and was_winner:
        reflection["parameter_suggestions"].append({
            "param": "take_profit_ratio",
            "direction": "consider_raising",
            "reasoning": f"TP hit at +{realized_pct:.1f}% — could have run further if TP wider",
        })
    
    # === Heuristic 2: specialist accuracy on this close ===
    ma_opt_correct = correct.get("market_analyst_optimist", {}).get("correct")
    ma_pes_correct = correct.get("market_analyst_pessimist", {}).get("correct")
    se_opt_correct = correct.get("solana_expert_optimist", {}).get("correct")
    se_pes_correct = correct.get("solana_expert_pessimist", {}).get("correct")
    
    if ma_pes_correct and not ma_opt_correct:
        reflection["specialist_observations"].append({
            "pattern": "ma_pes_right_ma_opt_wrong",
            "ticker": ticker,
            "note": "Pessimist correct on direction, Optimist wrong — this was a 'don't bid the chart' setup",
        })
    elif ma_opt_correct and not ma_pes_correct:
        reflection["specialist_observations"].append({
            "pattern": "ma_opt_right_ma_pes_wrong",
            "ticker": ticker,
            "note": "Optimist correct, Pessimist over-cautious — momentum delivered through their objections",
        })
    
    if se_pes_correct and not se_opt_correct:
        reflection["specialist_observations"].append({
            "pattern": "se_pes_right_se_opt_wrong",
            "ticker": ticker,
            "note": "On-chain Pessimist's rug/concentration concern materialized",
        })
    
    # === Heuristic 3: disagreement bucket × outcome ===
    if bucket == "spread_40_to_70" and not was_winner:
        reflection["lesson_candidates"].append({
            "candidate_id": f"disag_40_70_losses_n{int(time.time())%10000}",
            "pattern": "Moderate-disagreement entries continue to lose",
            "data_point": f"{ticker} closed {realized_pct:+.2f}% with combined_uncertainty in 0.40-0.70 bucket",
            "suggested_action": "Continue or strengthen disagreement penalty for this bucket",
            "status": "candidate",
        })
    if bucket == "spread_0_to_15" and was_winner and realized_pct > 5:
        reflection["lesson_candidates"].append({
            "candidate_id": f"low_disag_winners_n{int(time.time())%10000}",
            "pattern": "Low-disagreement entries deliver — full conviction trades worth it",
            "data_point": f"{ticker} closed +{realized_pct:.1f}% with all 4 specialists aligned",
            "suggested_action": "Maintain or REDUCE 0-0.15 bucket size penalty (already 0)",
            "status": "candidate",
        })
    
    # === Heuristic 4: per-symbol observation ===
    from predictions.fund import lessons_io
    fm = lessons_io.load_frontmatter()
    psa = (fm.get("per_symbol_specialist_accuracy") or {}).get(ticker, {})
    n_closes = psa.get("closed_trades", 1)
    avg_realized = psa.get("avg_realized_pct", realized_pct)
    if n_closes >= 2 and avg_realized < -5:
        reflection["per_symbol_observations"].append({
            "ticker": ticker,
            "observation": "blacklist_candidate",
            "stats": f"{n_closes} closes, avg {avg_realized:+.1f}% — consistently bad bet for this fund",
            "action": "Consider universe blacklist OR require +0.50 consensus for this symbol specifically",
        })
    elif n_closes >= 2 and avg_realized > 5:
        reflection["per_symbol_observations"].append({
            "ticker": ticker,
            "observation": "favorable_symbol",
            "stats": f"{n_closes} closes, avg {avg_realized:+.1f}% — fund has edge here",
            "action": "Continue favoring; consider boosting confidence threshold slightly",
        })
    
    _append_jsonl(REFLECTIONS_PATH, reflection)
    return reflection


def recent_reflections(n: int = 5) -> list[dict]:
    if not REFLECTIONS_PATH.exists(): return []
    lines = REFLECTIONS_PATH.read_text().splitlines()
    return [json.loads(l) for l in lines[-n:] if l.strip()]


def format_for_agent_prompt(max_items: int = 3) -> str:
    """Compact block — recent reflections for agent context."""
    refs = recent_reflections(max_items)
    if not refs:
        return "RECENT_REFLECTIONS: none yet (no closed trades reflected on)"
    lines = ["RECENT_REFLECTIONS (last closed-trade observations):"]
    for r in refs:
        lines.append(f"  [{r['ticker']} {r['realized_pct']:+.1f}%, {r['exit_reason']}]")
        for lc in r.get("lesson_candidates", [])[:2]:
            lines.append(f"    • {lc.get('pattern')}")
        for so in r.get("specialist_observations", [])[:1]:
            lines.append(f"    • {so.get('note')}")
        for pso in r.get("per_symbol_observations", [])[:1]:
            lines.append(f"    • {pso.get('ticker')}: {pso.get('action')}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_for_agent_prompt())
