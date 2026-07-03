"""A symbol the fund CURRENTLY HOLDS must not have its earlier rejections
classified as missed_winner / enterable_missed_winner.

Bug (tick-173, 2026-07-03): the fund entered SPX as a $125 probe at tick-172 (via the
tick-133 liq-override). The what-if window still contained SPX's EARLIER rejection
rows (tick-169), and the window's peak optimist_consensus (0.375, from the tick-172
probe tick itself) made those old rejections look "enterable". So the reflector was
dispatched with `enterable_missed_winner_24h` for SPX — a name the fund ENTERED and is
HOLDING through the +12.30% move. It is a good_entry, not a miss. Left unguarded this
fires every tick the position is held and up, polluting the enterable_missed_winners
signal (a key calibration metric).

Fix: `_build_what_ifs` tags each row with `symbol_currently_held`, and
`_classify_triggers` skips the REJECT-based classification for currently-held symbols
(their prior rejections are superseded by the actual entry). BUY_EXECUTED/SELL_EXECUTED
paths are unaffected.
"""
from __future__ import annotations

from predictions.fund import stage_phase6 as s6


def _reject_whatif(**over):
    w = {
        "symbol": "SPX", "prior_decision_tag": "REJECT", "ticks_ago": 4,
        "delta_pct": 12.30, "max_optimist_consensus_in_window": 0.375,
        "max_consensus_in_window": 0.125, "prior_consensus": 0.11,
    }
    w.update(over)
    return w


def test_held_symbol_reject_not_flagged_missed_winner():
    held = _reject_whatif(symbol_currently_held=True)
    trigs = s6._classify_triggers([held])
    assert trigs == [], f"held symbol should produce no reject-trigger, got {trigs}"


def test_unheld_symbol_reject_still_flags_enterable_missed_winner():
    # regression: the guard must NOT suppress the genuine case
    unheld = _reject_whatif(symbol_currently_held=False)
    trigs = s6._classify_triggers([unheld])
    kinds = [t["trigger_kind"] for t in trigs]
    assert "enterable_missed_winner_24h" in kinds, kinds


def test_missing_held_flag_defaults_to_unheld():
    # rows without the flag (older callers) behave as before — flagged
    w = _reject_whatif()  # no symbol_currently_held key
    trigs = s6._classify_triggers([w])
    kinds = [t["trigger_kind"] for t in trigs]
    assert "enterable_missed_winner_24h" in kinds, kinds
