"""SMAF fund goals — shared team objective across all agents.

Targets are visible in every agent prompt via `goal_status` block.
Provides current run-rate vs target so agents can self-calibrate aggression.
"""
from __future__ import annotations
import json, time, sys, datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# === GOALS (calibrated; can be edited as fund matures) ===
MONTHLY_RETURN_TARGET_PCT = 5.0      # Primary target (60% annualized)
MONTHLY_RETURN_FLOOR_PCT = 3.0       # Min acceptable (beats staked-SOL ~7-8% APY)
MONTHLY_RETURN_STRETCH_PCT = 8.0     # Stretch (top decile of crypto funds)
MAX_DRAWDOWN_THRESHOLD_PCT = -15.0   # Risk Mgr halt floor
TARGET_SHARPE = 1.5
TARGET_HIT_RATE_PCT = 50.0           # On closed trades
TARGET_PROFIT_FACTOR = 1.5

# Maturity buckets — what we can credibly judge based on days/trades
MATURITY = {
    "cold_start": "<3 days OR <3 closed trades — sample noise; do not over-extrapolate",
    "early": "3-14 days; signal forming",
    "judgable": "14+ days AND ≥5 closed trades; targets actionable",
}


def _days_running() -> float:
    """Compute days since account creation from equity.jsonl first entry."""
    eq = Path(__file__).resolve().parent / "state" / "equity.jsonl"
    if not eq.exists(): return 0.0
    try:
        first = json.loads(eq.read_text().splitlines()[0])
        return max((time.time() - first["timestamp"]) / 86400, 0.0)
    except Exception:
        return 0.0


def compute_status() -> dict:
    """Return current progress vs goals + maturity assessment."""
    from predictions.fund import performance, account
    perf = performance.compute()
    days = _days_running()
    total_return = perf.get("total_return_pct", 0) or 0
    sharpe = perf.get("sharpe_ratio_annualized", 0) or 0
    max_dd = perf.get("max_drawdown_pct", 0) or 0
    current_dd = perf.get("drawdown_now_pct", 0) or 0
    closed = perf.get("closed_trades", 0) or 0
    hit_rate = perf.get("hit_rate_pct")
    profit_factor = perf.get("profit_factor")
    
    # Maturity bucket
    if days < 3 or closed < 3:
        maturity = "cold_start"
    elif days < 14 or closed < 5:
        maturity = "early"
    else:
        maturity = "judgable"
    
    # Run-rate calculation
    if days >= 0.5:
        monthly_runrate_pct = (total_return / days) * 30
    else:
        monthly_runrate_pct = None
    
    # Status verdict
    status = "neutral"
    posture_recommendation = "standard"
    if monthly_runrate_pct is not None:
        if monthly_runrate_pct >= MONTHLY_RETURN_STRETCH_PCT:
            status = "stretch_pace"
            posture_recommendation = "defensive — protect the lead"
        elif monthly_runrate_pct >= MONTHLY_RETURN_TARGET_PCT:
            status = "on_target"
            posture_recommendation = "standard — keep current discipline"
        elif monthly_runrate_pct >= MONTHLY_RETURN_FLOOR_PCT:
            status = "floor_pace"
            posture_recommendation = "standard — but find more conviction setups"
        elif monthly_runrate_pct >= 0:
            status = "below_floor"
            posture_recommendation = "selective aggression — chasing target requires higher-conviction picks"
        else:
            status = "losing"
            posture_recommendation = "capital preservation — reduce aggression; only highest-conviction setups"
    
    # DD posture override (most important)
    dd_warning = ""
    if current_dd <= -10:
        dd_warning = "⚠ DD approaching halt"
    elif current_dd <= -5:
        dd_warning = "DD elevated"
    
    return {
        "monthly_target_pct": MONTHLY_RETURN_TARGET_PCT,
        "monthly_floor_pct": MONTHLY_RETURN_FLOOR_PCT,
        "monthly_stretch_pct": MONTHLY_RETURN_STRETCH_PCT,
        "max_drawdown_threshold_pct": MAX_DRAWDOWN_THRESHOLD_PCT,
        "target_sharpe": TARGET_SHARPE,
        "target_hit_rate_pct": TARGET_HIT_RATE_PCT,
        "target_profit_factor": TARGET_PROFIT_FACTOR,
        "days_running": round(days, 2),
        "total_return_pct": round(total_return, 2),
        "monthly_runrate_pct": round(monthly_runrate_pct, 2) if monthly_runrate_pct is not None else None,
        "current_sharpe": round(sharpe, 2),
        "current_max_dd_pct": round(max_dd, 2),
        "current_dd_pct": round(current_dd, 2),
        "dd_warning": dd_warning,
        "closed_trades": closed,
        "hit_rate_pct": hit_rate,
        "profit_factor": profit_factor,
        "maturity_bucket": maturity,
        "status": status,
        "posture_recommendation": posture_recommendation,
    }


def format_for_agent_prompt() -> str:
    """Compact ~12-line block injected into every agent's prompt."""
    s = compute_status()
    lines = [
        "FUND GOAL (shared team objective):",
        f"  Target: +{s['monthly_target_pct']:.1f}% monthly (Floor +{s['monthly_floor_pct']:.1f}%, Stretch +{s['monthly_stretch_pct']:.1f}%)",
        f"  Max drawdown threshold: {s['max_drawdown_threshold_pct']:.0f}% (Risk Mgr halts beyond this)",
        f"  Target Sharpe: ≥{s['target_sharpe']:.1f}, Hit-rate ≥{s['target_hit_rate_pct']:.0f}%, Profit-factor ≥{s['target_profit_factor']:.1f}",
    ]
    lines.append("")
    lines.append(f"CURRENT PROGRESS ({s['maturity_bucket']}, {s['days_running']}d running):")
    lines.append(f"  Total return: {s['total_return_pct']:+.2f}%")
    if s.get("monthly_runrate_pct") is not None:
        lines.append(f"  Monthly run-rate: {s['monthly_runrate_pct']:+.2f}% — status: **{s['status']}**")
    lines.append(f"  Current DD: {s['current_dd_pct']:.2f}% {s['dd_warning']}")
    lines.append(f"  Sharpe: {s['current_sharpe']:.2f}  •  Closed trades: {s['closed_trades']}  •  Hit-rate: {s['hit_rate_pct']}%  •  PF: {s['profit_factor']}")
    lines.append("")
    lines.append(f"POSTURE RECOMMENDATION: {s['posture_recommendation']}")
    if s["maturity_bucket"] == "cold_start":
        lines.append("  (NOTE: sample is too small to judge; treat target/floor as direction, not verdict)")
    return "\n".join(lines)


if __name__ == "__main__":
    print("=== Goal status ===")
    import json
    print(json.dumps(compute_status(), indent=2, default=str))
    print()
    print("=== Agent-prompt block ===")
    print(format_for_agent_prompt())
