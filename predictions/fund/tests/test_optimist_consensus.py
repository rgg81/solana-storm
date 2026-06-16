"""Phase 3 must expose optimist_consensus = mean of the two OPTIMIST scores.

Desk-forensics (wf_8321460b, 2026-06-16) found the dominant root cause of the
fund's near-permanent inaction: the probe gate's `consensus >= +0.20` is a flat
4-way mean of two optimists and two structurally-bearish pessimists (pessimist
pair averages -0.24), which mathematically caps blended consensus near +0.167
even when both optimists are pinned at max — so the entry door is arithmetically
unreachable in ~77% of rows regardless of how bullish the bull case is.

Fix (probe-gate only; full-size floor unchanged): expose `optimist_consensus =
(ma_optimist + se_optimist) / 2` so the $125 probe can gate on genuine BULL
AGREEMENT, with the pessimist kept as a separate HARD VETO (ma_pessimist > -0.50)
and the uncertainty cap (combined_uncertainty < 0.55) — pessimists VETO, they no
longer DILUTE. This is the field the RM's restructured Pass 2.5 probe reads.
"""
from __future__ import annotations

from predictions.fund import stage_phase3


def test_optimist_consensus_is_mean_of_optimists():
    # ma_opt=0.52, se_opt=0.30 -> pair 0.41 (the real JTO t43 probe, +$18.67)
    assert stage_phase3._optimist_consensus(0.52, 0.30) == 0.41
    # ma_opt=0.45, se_opt=0.30 -> 0.375 (real RENDER t68 probe)
    assert stage_phase3._optimist_consensus(0.45, 0.30) == 0.375


def test_optimist_consensus_not_dragged_by_pessimists():
    """The whole point: the optimist pair is independent of the bears. Two strong
    bulls clear the bar even when two bears would have dragged the 4-way mean under
    the old +0.20 floor."""
    # 4-way mean of (0.50, -0.40, 0.30, -0.30) = +0.025 (old floor would BLOCK)...
    # ...but the optimist pair is (0.50 + 0.30)/2 = 0.40 (clears a +0.30 bar).
    assert stage_phase3._optimist_consensus(0.50, 0.30) == 0.40


def test_blowoff_optimist_pair_stays_low():
    """JTO t140 +41% blow-off (ma_opt 0.18 / se_opt 0.05): the optimist itself
    refused to chase, so the pair is 0.115 — well below any +0.30 probe bar."""
    assert stage_phase3._optimist_consensus(0.18, 0.05) == 0.115
