"""Pass 2.5 probe gate spec — codify the agreed-on prerequisites.

History: the role file historically required `strong_bear AND calm_vol`. SOL
30d daily vol is ~2.91% throughout the regime the probe was designed for and
`calm` is defined as <2.5% in regime.py — meaning the probe gate was
structurally closed for the entire defensive streak independent of any
score-level near-misses (multi-agent review 2026-06-06).

Decision (user-confirmed 2026-06-06): drop the calm_vol prerequisite. Probe
remains gated by strong_bear regime + the score-level thresholds; vol_bucket
is no longer a prerequisite.

This test guards that decision: any future PR that re-adds calm_vol to the
probe gate trips the test.
"""
from __future__ import annotations

import re
from pathlib import Path

ROLE_PATH = Path(__file__).resolve().parents[1] / "agents" / "risk_manager.md"


def _probe_block() -> str:
    """Return the Pass 2.5 block text — between the header and the next ## header."""
    text = ROLE_PATH.read_text()
    m = re.search(r"## Pass 2\.5:.*?(?=\n## )", text, re.DOTALL)
    assert m, "Pass 2.5 section not found in risk_manager.md"
    return m.group(0)


def test_probe_gate_does_not_require_calm_vol():
    block = _probe_block()
    # Look for the prerequisite list. The historical violation:
    #   "1. Regime is `strong_bear` AND vol bucket is `calm`"
    forbidden_patterns = [
        r"vol bucket is `calm`",
        r"vol_bucket\s*=\s*calm",
        r"vol_bucket\s*==\s*['\"]calm['\"]",
        r"AND\s+vol\s+bucket\s+is\s+calm",
    ]
    matches = [p for p in forbidden_patterns if re.search(p, block, re.IGNORECASE)]
    assert not matches, (
        f"risk_manager.md Pass 2.5 prerequisite still references calm_vol "
        f"({matches}). Per the 2026-06-06 review, calm_vol was dropped from "
        f"the gate because SOL 30d vol has been ~2.91% throughout the regime "
        f"and `calm` is <2.5% in regime.py — the gate would never fire."
    )


def test_probe_gate_still_requires_strong_bear():
    block = _probe_block()
    assert re.search(r"strong_bear", block), (
        "Pass 2.5 gate must still require strong_bear regime — that's the "
        "framework's pressure-release valve below the +0.40 floor in a bear."
    )


def test_probe_score_thresholds_preserved():
    block = _probe_block()
    # The score-level gates must remain intact.
    required = [
        (r"consensus\s*≥\s*\+?0\.20", "consensus >= +0.20"),
        (r"ma_optimist\s*≥\s*\+?0\.45", "ma_optimist >= +0.45"),
        (r"ma_pessimist\s*>\s*-0\.50", "no MA-Pes HARD VETO (ma_pessimist > -0.50)"),
        (r"combined_uncertainty\s*<\s*0\.55", "combined_uncertainty < 0.55"),
    ]
    missing = [label for pat, label in required if not re.search(pat, block)]
    assert not missing, f"Pass 2.5 must keep score gates: {missing}"
