"""Self-tuning risk parameters for SMAF.

Three parameter families auto-tune based on closed-trade outcomes:
  A. stop_vol_multiplier — stop = max(-0.08, -mult × 30d_vol)
  D. disagreement_penalty_by_bucket — size reduction at each spread bucket
  K. slippage_coefficient — drifts toward observed estimated/realized ratio

All parameters live in lessons.md frontmatter under `risk_calibration:`,
auto-loaded by Risk Mgr / fees_model and citable in agent prompts.

Safety: parameters NEVER auto-loosen risk floors (max position size, DD halt,
min trade size, REJECT-on-split threshold). Only auto-tune within bounded ranges.
"""
from __future__ import annotations
import json, time, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Defaults (used when no calibration data yet)
DEFAULT_STOP_VOL_MULT = 2.5
DEFAULT_SLIPPAGE_COEF = 1.0
DEFAULT_DISAGREEMENT_PENALTIES = {
    "spread_0_to_15":  0.00,  # no penalty (consensus)
    "spread_15_to_40": 0.25,  # -25% size
    "spread_40_to_70": 0.50,  # -50% size
    "spread_70_plus":  1.00,  # REJECT (=100% reduction)
}

# Bounds — never tune outside these
BOUNDS = {
    "stop_vol_multiplier": (1.5, 4.0),
    "slippage_coefficient": (0.5, 5.0),
    "disagreement_penalty": {
        "spread_0_to_15":  (0.00, 0.20),
        "spread_15_to_40": (0.10, 0.40),
        "spread_40_to_70": (0.30, 0.75),
        # spread_70_plus stays at 1.00 (manual only — never auto-loosen REJECT)
    },
}

MIN_OBSERVATIONS = 10  # before auto-tuning kicks in


def _load() -> dict:
    from predictions.fund import lessons_io
    fm = lessons_io.load_frontmatter()
    return fm.get("risk_calibration") or {}


def _save(calib: dict) -> None:
    from predictions.fund import lessons_io
    lessons_io.update_frontmatter({"risk_calibration": calib})


def get_stop_vol_multiplier() -> float:
    return float(_load().get("stop_vol_multiplier", DEFAULT_STOP_VOL_MULT))


def get_slippage_coefficient() -> float:
    return float(_load().get("slippage_coefficient", DEFAULT_SLIPPAGE_COEF))


def get_disagreement_penalty(spread: float) -> float:
    """Returns size reduction fraction (0.0 to 1.0) for a given spread."""
    if spread >= 0.70: return 1.00  # always REJECT (manual lock)
    bucket = (
        "spread_40_to_70" if spread >= 0.40 else
        "spread_15_to_40" if spread >= 0.15 else
        "spread_0_to_15"
    )
    penalties = _load().get("disagreement_penalty_by_bucket") or DEFAULT_DISAGREEMENT_PENALTIES
    return float(penalties.get(bucket, DEFAULT_DISAGREEMENT_PENALTIES[bucket]))


# === Calibration updates (called from audit.audit_close + execute_pm_orders) ===

def update_stop_calibration(was_stop_triggered: bool, was_winner: bool,
                              realized_pct: float, vol_30d: float) -> None:
    """Track stop outcomes per closed trade.

    - stop_triggered + loser: stop saved us (good)
    - stop_triggered + winner (would have been): stop too tight (widen)
    - never_triggered: stop didn't activate (no calibration signal)
    """
    if vol_30d <= 0: return  # avoid div/0
    calib = _load()
    hist = calib.setdefault("stop_outcomes", {
        "n_total": 0, "n_triggered_savings": 0, "n_triggered_winners": 0,
        "n_never_triggered": 0,
    })
    hist["n_total"] += 1
    if was_stop_triggered and not was_winner:
        hist["n_triggered_savings"] += 1
    elif was_stop_triggered and was_winner:
        # Winner that touched stop = stop was too tight (we got lucky in)
        hist["n_triggered_winners"] += 1
    else:
        hist["n_never_triggered"] += 1
    
    # Tune after MIN_OBSERVATIONS
    if hist["n_total"] >= MIN_OBSERVATIONS:
        current = calib.get("stop_vol_multiplier", DEFAULT_STOP_VOL_MULT)
        winner_stop_rate = hist["n_triggered_winners"] / hist["n_total"]
        never_rate = hist["n_never_triggered"] / hist["n_total"]
        new_mult = current
        if winner_stop_rate > 0.40:
            new_mult = current + 0.25  # widen — stops killing winners
            calib["last_adjustment_reason"] = f"winner_stop_rate {winner_stop_rate*100:.0f}% > 40% — widened"
        elif never_rate > 0.60:
            new_mult = current - 0.25  # tighten — stops rarely useful
            calib["last_adjustment_reason"] = f"never_triggered_rate {never_rate*100:.0f}% > 60% — tightened"
        # Clamp to bounds
        lo, hi = BOUNDS["stop_vol_multiplier"]
        new_mult = max(lo, min(hi, new_mult))
        if new_mult != current:
            calib["stop_vol_multiplier"] = new_mult
            calib["last_stop_adjustment_at"] = int(time.time())
    
    calib["stop_outcomes"] = hist
    _save(calib)


def update_slippage_calibration(estimated_pct: float, realized_pct: float) -> None:
    """Drift slippage_coefficient toward observed."""
    calib = _load()
    hist = calib.setdefault("slippage_history", {"n": 0, "sum_ratio": 0.0})
    if estimated_pct > 0:
        ratio = realized_pct / estimated_pct
        hist["n"] += 1
        hist["sum_ratio"] += ratio
    
    if hist["n"] >= 5:
        avg_ratio = hist["sum_ratio"] / hist["n"]
        current = calib.get("slippage_coefficient", DEFAULT_SLIPPAGE_COEF)
        # Drift 10% toward observed each calibration (smooths noise)
        new_coef = current * 0.9 + (current * avg_ratio) * 0.1
        lo, hi = BOUNDS["slippage_coefficient"]
        new_coef = max(lo, min(hi, new_coef))
        if abs(new_coef - current) > 0.02:
            calib["slippage_coefficient"] = round(new_coef, 3)
            calib["last_slippage_adjustment_at"] = int(time.time())
    calib["slippage_history"] = hist
    _save(calib)


def update_disagreement_calibration() -> None:
    """Read disagreement_outcome from lessons.md, tune penalty bucket sizes.

    Theory: if a bucket has avg_return ≤ 0 with n ≥ MIN_OBSERVATIONS, INCREASE penalty
    (more aggressive size cut). If avg_return > +5% and Sharpe good, DECREASE.
    """
    from predictions.fund import lessons_io
    fm = lessons_io.load_frontmatter()
    do = fm.get("disagreement_outcome") or {}
    
    calib = _load()
    penalties = calib.get("disagreement_penalty_by_bucket") or dict(DEFAULT_DISAGREEMENT_PENALTIES)
    bounds = BOUNDS["disagreement_penalty"]
    
    adjusted = False
    for bucket, data in do.items():
        if bucket not in bounds: continue  # don't touch spread_70_plus
        if not isinstance(data, dict): continue
        n = data.get("n", 0)
        avg_ret = data.get("avg_return_pct")
        if n < MIN_OBSERVATIONS or avg_ret is None: continue
        
        current = penalties.get(bucket, DEFAULT_DISAGREEMENT_PENALTIES[bucket])
        lo, hi = bounds[bucket]
        if avg_ret <= 0:
            new = min(hi, current + 0.10)  # more aggressive cut
        elif avg_ret > 5.0:
            new = max(lo, current - 0.10)  # let through more
        else:
            continue
        if abs(new - current) > 0.005:
            penalties[bucket] = round(new, 3)
            adjusted = True
    
    if adjusted:
        calib["disagreement_penalty_by_bucket"] = penalties
        calib["last_disagreement_adjustment_at"] = int(time.time())
        _save(calib)


def format_for_agent_prompt() -> str:
    """Compact block — what Risk Mgr should know about current tuning."""
    calib = _load()
    if not calib:
        return "RISK_CALIBRATION: defaults (no auto-tuning data yet)"
    
    lines = ["RISK_CALIBRATION (auto-tuned from closed trades):"]
    mult = calib.get("stop_vol_multiplier", DEFAULT_STOP_VOL_MULT)
    coef = calib.get("slippage_coefficient", DEFAULT_SLIPPAGE_COEF)
    lines.append(f"  stop_vol_multiplier: {mult} (default {DEFAULT_STOP_VOL_MULT})")
    lines.append(f"  slippage_coefficient: {coef} (default {DEFAULT_SLIPPAGE_COEF})")
    pens = calib.get("disagreement_penalty_by_bucket")
    if pens:
        lines.append(f"  disagreement_penalty_by_bucket: {pens}")
    so = calib.get("stop_outcomes") or {}
    if so.get("n_total", 0) > 0:
        lines.append(f"  stop outcomes: {so}")
    last_reason = calib.get("last_adjustment_reason")
    if last_reason:
        lines.append(f"  last_adjustment: {last_reason}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("=== Current risk calibration ===")
    print(format_for_agent_prompt())
    print()
    print("get_stop_vol_multiplier():", get_stop_vol_multiplier())
    print("get_slippage_coefficient():", get_slippage_coefficient())
    print("get_disagreement_penalty(0.30):", get_disagreement_penalty(0.30))
    print("get_disagreement_penalty(0.55):", get_disagreement_penalty(0.55))
    print("get_disagreement_penalty(0.80):", get_disagreement_penalty(0.80))
