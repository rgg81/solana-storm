"""The mandate is regime-conditional: +5%/mo in risk-on, preservation below SMA200.

Desk-forensics (wf_8321460b, 2026-06-16): the fund was measured against a flat
+5%/mo target that is structurally UNREACHABLE while SOL is below its SMA200 (the
defensive macro anchor). On a tape that fell -26% before recovering, holding cash
was the CORRECT outcome, yet every flat tick was stamped "below_floor" — a false
failure signal that distorted every agent's framing. Fix: below SMA200, the goal
is capital preservation; the +5%/mo growth target applies in risk-on only.
"""
from __future__ import annotations

from predictions.fund import goals


def test_risk_on_keeps_growth_target():
    """Above SMA200 (bull/strong_bull): the +5%/mo growth mandate applies as-is."""
    status, posture, goal = goals._apply_regime_conditional_goal(
        "below_floor", "selective aggression", monthly_runrate_pct=1.5,
        current_dd_pct=-0.15, regime_label="bull")
    assert goal == "growth"
    assert status == "below_floor"  # unchanged in risk-on


def test_defensive_flat_not_losing_is_preservation_ok():
    """Below SMA200, flat-but-not-losing within DD limits = doing its job."""
    status, posture, goal = goals._apply_regime_conditional_goal(
        "below_floor", "selective aggression", monthly_runrate_pct=1.5,
        current_dd_pct=-0.15, regime_label="strong_bear")
    assert goal == "capital_preservation"
    assert status == "preservation_ok"   # NOT the punitive below_floor
    assert "preservation" in posture.lower()


def test_defensive_but_losing_keeps_cautionary_signal():
    """Below SMA200 AND losing money: do NOT whitewash — keep the warning."""
    status, posture, goal = goals._apply_regime_conditional_goal(
        "losing", "capital preservation — reduce aggression", monthly_runrate_pct=-2.0,
        current_dd_pct=-6.0, regime_label="strong_bear")
    assert goal == "capital_preservation"
    assert status == "losing"   # still flagged — losing in a bear is not "ok"


def test_defensive_label_bear_also_triggers():
    """'bear' (cur<SMA200 but >SMA50) is also the defensive band, like strong_bear."""
    status, _, goal = goals._apply_regime_conditional_goal(
        "below_floor", "x", monthly_runrate_pct=0.5, current_dd_pct=-1.0,
        regime_label="bear")
    assert goal == "capital_preservation"
    assert status == "preservation_ok"
