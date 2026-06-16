"""Reflection must be able to record evidence FOR action, not only against it.

Desk-forensics (wf_8321460b, 2026-06-16): the reflection layer is asymmetric. A
rejected name that FALLS is classified `good_rejection` only when CONTESTED
(max_consensus >= +0.35) — but a rejected name that RISES fires a plain
`missed_winner` with NO contest gate, so the Reflector dismisses every miss as
"uncontested tape-beta / luck." Result: the system can accrue evidence that
rejections were correct, but can NEVER accrue evidence that it missed a TAKEABLE
winner. Its validated memory is therefore 100% cautionary, reinforcing inaction.

Fix: add a symmetric `enterable_missed_winner` classification — a rejection that
both rose AND would have CLEARED THE PROBE BAR (max optimist_consensus in the
window >= +0.30). That is the for-action counterpart to `good_rejection`: a
genuine opportunity cost the framework should learn from, distinct from a plain
uncontested tape-beta bounce.
"""
from __future__ import annotations

from predictions.fund import stage_phase6 as s6


def _row(symbol, tick_id, price, ma_o, se_o, tag="REJECT"):
    return {"symbol": symbol, "tick_id": tick_id, "price_usd": price,
            "ma_optimist": ma_o, "se_optimist": se_o, "ma_pessimist": -0.2,
            "se_pessimist": -0.1, "consensus": 0.0, "decision_tag": tag}


def test_what_if_carries_max_optimist_consensus(monkeypatch):
    # History: JTO rejected at t1 (ma_opt .50/se_opt .30 -> pair .40), price rose t1->t2.
    hist = [_row("JTO", 1, 0.50, 0.50, 0.30), _row("JTO", 2, 0.60, 0.18, 0.05)]
    monkeypatch.setattr(s6.uph, "load_all", lambda: hist)
    wi = s6._build_what_ifs(current_tick_id=2, current_prices={"JTO": 0.60})
    j = [w for w in wi if w["prior_tick_id"] == 1][0]
    # max optimist_consensus across the window = max(.40 at t1, .115 at t2) = .40
    assert j["max_optimist_consensus_in_window"] == 0.40


def test_enterable_missed_winner_when_probe_bar_cleared():
    """Rejection rose >=5% AND cleared the optimist probe bar -> enterable miss."""
    wi = [{"prior_decision_tag": "REJECT", "delta_pct": 9.0, "ticks_ago": 1,
           "max_consensus_in_window": 0.10, "max_optimist_consensus_in_window": 0.41}]
    kinds = [t["trigger_kind"] for t in s6._classify_triggers(wi)]
    assert "enterable_missed_winner_6h" in kinds
    assert "missed_winner_6h" not in kinds  # promoted to the symmetric kind


def test_plain_missed_winner_when_below_probe_bar():
    """Rejection rose but never cleared the probe bar -> uncontested tape-beta."""
    wi = [{"prior_decision_tag": "REJECT", "delta_pct": 9.0, "ticks_ago": 1,
           "max_consensus_in_window": 0.10, "max_optimist_consensus_in_window": 0.18}]
    kinds = [t["trigger_kind"] for t in s6._classify_triggers(wi)]
    assert "missed_winner_6h" in kinds
    assert "enterable_missed_winner_6h" not in kinds


def test_enterable_missed_winner_24h():
    wi = [{"prior_decision_tag": "REJECT", "delta_pct": 14.0, "ticks_ago": 4,
           "max_consensus_in_window": 0.10, "max_optimist_consensus_in_window": 0.35}]
    kinds = [t["trigger_kind"] for t in s6._classify_triggers(wi)]
    assert "enterable_missed_winner_24h" in kinds
