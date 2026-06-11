"""Reflection-rule labeling must honor the Reflector's explicit status hold-back.

Bug (tick-122, 2026-06-10): the lessons aggregator promoted a candidate to
"validated" purely on supporting_count >= 3, OVERRIDING the Reflector's explicit
new_status_suggestion="candidate". The Reflector had deliberately held cand_53 as
a candidate because the rule's discriminator has an untested positive arm — but
the count rule mechanically relabeled it "validated", corrupting the fund's
memory (validated rules carry more weight in agent prompts).

Fix: an explicit new_status_suggestion="candidate" on the latest confirmation row
vetoes count-based auto-promotion. Count>=3 still auto-promotes when the Reflector
did NOT hold it back (default preserved). disconfirming>=3 still demotes to
rejected (safety rule, highest priority).
"""
from __future__ import annotations

import pytest

from predictions.fund import lessons_io


def _agg(monkeypatch, rows):
    monkeypatch.setattr(lessons_io, "_load_reflections", lambda: rows)
    return lessons_io._aggregate_reflections()


def _candidate_row(cid, supporting=1):
    return {"kind_row": "new_candidate", "candidate_id": cid, "kind": "good_rejection",
            "pattern": "p", "candidate_lesson": "L", "supporting_count": supporting,
            "tick_id": 1}


def _confirm_row(cid, new_count, status, tick):
    return {"kind_row": "confirmation", "prior_candidate_id": cid, "kind": "confirming",
            "new_supporting_count": new_count, "new_status_suggestion": status, "tick_id": tick}


def test_count3_no_suggestion_auto_promotes(monkeypatch):
    """Default behavior preserved: count>=3 with no explicit hold → validated."""
    rows = [_candidate_row("c1"),
            {"kind_row": "confirmation", "prior_candidate_id": "c1", "kind": "confirming",
             "new_supporting_count": 3, "tick_id": 2}]  # no new_status_suggestion
    agg = _agg(monkeypatch, rows)
    assert any(c["candidate_id"] == "c1" for c in agg["validated"])


def test_count3_explicit_candidate_holds(monkeypatch):
    """THE FIX: Reflector's explicit 'candidate' vetoes count-based promotion."""
    rows = [_candidate_row("c53"),
            _confirm_row("c53", 3, "candidate", 2)]
    agg = _agg(monkeypatch, rows)
    assert any(c["candidate_id"] == "c53" for c in agg["candidates"])
    assert not any(c["candidate_id"] == "c53" for c in agg["validated"])


def test_count3_explicit_validated_promotes(monkeypatch):
    rows = [_candidate_row("c2"),
            _confirm_row("c2", 3, "validated", 2)]
    agg = _agg(monkeypatch, rows)
    assert any(c["candidate_id"] == "c2" for c in agg["validated"])


def test_disconfirming3_rejects_even_if_held_candidate(monkeypatch):
    """Safety demotion wins: 3 disconfirms → rejected regardless of hold-back."""
    rows = [_candidate_row("c3")]
    for t in range(2, 5):
        rows.append({"kind_row": "confirmation", "prior_candidate_id": "c3",
                     "kind": "disconfirming", "new_status_suggestion": "candidate", "tick_id": t})
    agg = _agg(monkeypatch, rows)
    assert any(c["candidate_id"] == "c3" for c in agg["rejected"])


def test_count_below3_stays_candidate(monkeypatch):
    rows = [_candidate_row("c4"), _confirm_row("c4", 2, "candidate", 2)]
    agg = _agg(monkeypatch, rows)
    assert any(c["candidate_id"] == "c4" for c in agg["candidates"])


def test_later_suggestion_overrides_earlier(monkeypatch):
    """The LATEST explicit suggestion governs (Reflector can release a hold)."""
    rows = [_candidate_row("c5"),
            _confirm_row("c5", 3, "candidate", 2),
            _confirm_row("c5", 4, "validated", 3)]
    agg = _agg(monkeypatch, rows)
    assert any(c["candidate_id"] == "c5" for c in agg["validated"])


def test_explicit_rejected_retires_without_three_disconfirms(monkeypatch):
    """THE FIX (tick-125): a single decisive falsification — Reflector's explicit
    'rejected' on a disconfirming row — retires the rule WITHOUT waiting for the
    count rule's 3 mechanical disconfirms. Mirrors the explicit-'candidate' hold:
    the Reflector is the judgment agent. (Bug: a falsified-on-arrival hypothesis,
    e.g. the PYTH catalyst-leg carve-out, was silently kept as a candidate.)"""
    rows = [_candidate_row("c6", supporting=0),
            {"kind_row": "confirmation", "prior_candidate_id": "c6",
             "kind": "disconfirming", "new_status_suggestion": "rejected", "tick_id": 2}]
    agg = _agg(monkeypatch, rows)
    assert any(c["candidate_id"] == "c6" for c in agg["rejected"])
    assert not any(c["candidate_id"] == "c6" for c in agg["candidates"])


def test_explicit_rejected_overrides_supporting_count(monkeypatch):
    """An explicit 'rejected' wins over a high supporting_count (judgment retirement
    of a rule that had accumulated confirmations before being falsified)."""
    rows = [_candidate_row("c7"),
            _confirm_row("c7", 5, "candidate", 2),  # had support, was a live candidate
            {"kind_row": "confirmation", "prior_candidate_id": "c7",
             "kind": "disconfirming", "new_status_suggestion": "rejected", "tick_id": 3}]
    agg = _agg(monkeypatch, rows)
    assert any(c["candidate_id"] == "c7" for c in agg["rejected"])
    assert not any(c["candidate_id"] == "c7" for c in agg["validated"])
