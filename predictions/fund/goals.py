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


_EQUITY_PATH = Path(__file__).resolve().parent / "state" / "equity.jsonl"


def _days_running() -> float:
    """Compute days since account creation from equity.jsonl first entry."""
    if not _EQUITY_PATH.exists(): return 0.0
    try:
        first = json.loads(_EQUITY_PATH.read_text().splitlines()[0])
        return max((time.time() - first["timestamp"]) / 86400, 0.0)
    except Exception:
        return 0.0


def _rolling_runrate_pct(window_days: float) -> float | None:
    """Monthly run-rate computed over the trailing window_days of equity.jsonl.

    Returns None if there are fewer than 2 equity rows inside the window.
    Used by format_for_agent_prompt to expose a 7d run-rate alongside the
    lifetime extrapolation — the lifetime number was hiding 11+ days of flat
    equity behind a single tick-1 winner (multi-agent review 2026-06-06).
    """
    if not _EQUITY_PATH.exists():
        return None
    cutoff = time.time() - window_days * 86400
    rows = []
    for line in _EQUITY_PATH.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            if r.get("timestamp", 0) >= cutoff:
                rows.append(r)
        except Exception:
            pass
    if len(rows) < 2:
        return None
    first_eq = float(rows[0].get("equity_usd") or 0)
    last_eq = float(rows[-1].get("equity_usd") or 0)
    if first_eq <= 0:
        return None
    window_return = (last_eq / first_eq - 1.0) * 100
    span_days = max((rows[-1]["timestamp"] - rows[0]["timestamp"]) / 86400, 0.01)
    return round((window_return / span_days) * 30, 3)


_HISTORY_PATH = Path(__file__).resolve().parent / "state" / "universe_price_history.jsonl"


def _latest_regime_label() -> str | None:
    """Latest regime_label from the price-history snapshot (offline, no network).
    Returns None if unavailable."""
    try:
        if not _HISTORY_PATH.exists():
            return None
        for line in reversed(_HISTORY_PATH.read_text().splitlines()):
            if not line.strip():
                continue
            lbl = json.loads(line).get("regime_label")
            if lbl:
                return lbl
    except Exception:
        return None
    return None


def _apply_regime_conditional_goal(status, posture, monthly_runrate_pct,
                                   current_dd_pct, regime_label):
    """Make the mandate regime-conditional (desk-forensics 2026-06-16).

    Below SMA200 — `regime_label in {strong_bear, bear}` — the +5%/mo growth
    target is structurally unreachable, and capital preservation is the honest
    mandate: a flat-but-not-losing fund protecting capital through a downtrend is
    SUCCEEDING, not "below_floor". Above SMA200 (risk-on) the growth target
    applies unchanged. Returns (status, posture, effective_goal). Losing money or
    drawing down hard in a bear still keeps the cautionary signal — preservation
    means don't-lose, not don't-care."""
    defensive = regime_label in ("strong_bear", "bear")
    if not defensive:
        return status, posture, "growth"
    not_losing = monthly_runrate_pct is not None and monthly_runrate_pct >= 0
    dd_ok = current_dd_pct is None or current_dd_pct > -10.0
    if not_losing and dd_ok:
        return (
            "preservation_ok",
            "defensive regime (SOL < SMA200): capital preservation is the mandate — "
            "the +5%/mo growth target applies in risk-on regimes only. Deploy ONLY on "
            "genuine bull-agreement probes; holding cash through a downtrend is success, "
            "not failure. (Do not force trades to chase an unreachable growth number.)",
            "capital_preservation",
        )
    return status, posture, "capital_preservation"


def _consecutive_below_floor_ticks() -> int:
    """Count trailing ticks where the per-tick equity change kept the realized
    return below floor. Approximation: count trailing equity rows whose
    equity is exactly equal to the one before (flat) — captures the all-cash
    paralysis pattern. Returns 0 if equity.jsonl is empty/missing.
    """
    if not _EQUITY_PATH.exists():
        return 0
    rows = [json.loads(l) for l in _EQUITY_PATH.read_text().splitlines() if l.strip()]
    if len(rows) < 2:
        return 0
    count = 0
    last = None
    for r in reversed(rows):
        eq = float(r.get("equity_usd") or 0)
        if last is None:
            last = eq
            count = 1
            continue
        # Tolerance: 1 cent
        if abs(eq - last) < 0.01:
            count += 1
        else:
            break
    return count


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
    
    rolling_7d = _rolling_runrate_pct(window_days=7.0)
    consecutive_flat = _consecutive_below_floor_ticks()

    # Regime-conditional mandate: below SMA200, the goal is capital preservation,
    # not the +5%/mo growth target (unreachable while the macro anchor is in
    # defense — desk-forensics 2026-06-16). Reframes a flat-but-not-losing fund
    # away from a false "below_floor" failure signal.
    regime_label = _latest_regime_label()
    status, posture_recommendation, effective_goal = _apply_regime_conditional_goal(
        status, posture_recommendation, monthly_runrate_pct, current_dd, regime_label)

    return {
        "regime_label": regime_label,
        "effective_goal": effective_goal,
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
        "rolling_7d_runrate_pct": rolling_7d,
        "consecutive_flat_ticks": consecutive_flat,
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
    _eg = s.get("effective_goal", "growth")
    _goal_line = (
        f"  Target: +{s['monthly_target_pct']:.1f}% monthly (Floor +{s['monthly_floor_pct']:.1f}%, Stretch +{s['monthly_stretch_pct']:.1f}%)"
        if _eg != "capital_preservation"
        else f"  MANDATE NOW: CAPITAL PRESERVATION (regime {s.get('regime_label')} is below SMA200). The +{s['monthly_target_pct']:.1f}%/mo growth target applies in RISK-ON regimes only — do not force trades to chase it here; deploy only on genuine bull-agreement probes."
    )
    lines = [
        "FUND GOAL (shared team objective):",
        _goal_line,
        f"  Max drawdown threshold: {s['max_drawdown_threshold_pct']:.0f}% (Risk Mgr halts beyond this)",
        f"  Target Sharpe: ≥{s['target_sharpe']:.1f}, Hit-rate ≥{s['target_hit_rate_pct']:.0f}%, Profit-factor ≥{s['target_profit_factor']:.1f}",
    ]
    lines.append("")
    lines.append(f"CURRENT PROGRESS ({s['maturity_bucket']}, {s['days_running']}d running):")
    lines.append(f"  Total return: {s['total_return_pct']:+.2f}%")
    if s.get("monthly_runrate_pct") is not None:
        lines.append(f"  Monthly run-rate (lifetime): {s['monthly_runrate_pct']:+.2f}% — status: **{s['status']}**")
    # 7d-rolling run-rate exposes when the lifetime extrapolation is hiding a
    # long flat streak. Multi-agent review 2026-06-06: lifetime decay was
    # masquerading as "selective aggression" while the actual 7d was exactly 0%.
    if s.get("rolling_7d_runrate_pct") is not None:
        lines.append(f"  Monthly run-rate (7d rolling): {s['rolling_7d_runrate_pct']:+.2f}%")
    if s.get("consecutive_flat_ticks", 0) >= 5:
        lines.append(
            f"  ⚠ {s['consecutive_flat_ticks']} consecutive flat ticks — capital preservation "
            f"is the BASELINE not the GOAL; surface cost of inaction explicitly in your summary."
        )
    lines.append(f"  Current DD: {s['current_dd_pct']:.2f}% {s['dd_warning']}")
    # Sharpe is gated when deployment fraction is too low to be meaningful.
    if _sharpe_is_meaningful():
        lines.append(f"  Sharpe: {s['current_sharpe']:.2f}  •  Closed trades: {s['closed_trades']}  •  Hit-rate: {s['hit_rate_pct']}%  •  PF: {s['profit_factor']}")
    else:
        lines.append(f"  Sharpe: n/a (insufficient deployment)  •  Closed trades: {s['closed_trades']}  •  Hit-rate: {s['hit_rate_pct']}%  •  PF: {s['profit_factor']}")
    lines.append("")
    lines.append(f"POSTURE RECOMMENDATION: {s['posture_recommendation']}")
    if s["maturity_bucket"] == "cold_start":
        lines.append("  (NOTE: sample is too small to judge; treat target/floor as direction, not verdict)")
    return "\n".join(lines)


def _sharpe_is_meaningful(min_deployed_fraction: float = 0.30) -> bool:
    """Sharpe over 85 zero-return cash days + 2 trades is mechanically
    inflated and was being cited as evidence of discipline. Suppress display
    until at least min_deployed_fraction of ticks have non-zero deployment.
    """
    if not _EQUITY_PATH.exists():
        return False
    rows = [json.loads(l) for l in _EQUITY_PATH.read_text().splitlines() if l.strip()]
    if len(rows) < 10:
        return False  # too few ticks to judge
    deployed = sum(1 for r in rows if float(r.get("deployed_pct") or 0) > 0)
    return (deployed / len(rows)) >= min_deployed_fraction


if __name__ == "__main__":
    print("=== Goal status ===")
    import json
    print(json.dumps(compute_status(), indent=2, default=str))
    print()
    print("=== Agent-prompt block ===")
    print(format_for_agent_prompt())
