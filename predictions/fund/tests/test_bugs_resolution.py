"""bugs.mark_resolved + unresolved_count regression tests.

History: 59 bugs.jsonl entries accumulated resolved=false over the streak
with no resolution mechanism (multi-agent review 2026-06-06). Added
mark_resolved + unresolved_count so the Phase 7 auto-audit (and any future
ops-health surface) can distinguish stale entries from active issues.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from predictions.fund import bugs


@pytest.fixture
def isolated_bugs(tmp_path, monkeypatch):
    bugs_path = tmp_path / "bugs.jsonl"
    monkeypatch.setattr(bugs, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(bugs, "BUGS_PATH", bugs_path)
    return bugs_path


def test_mark_resolved_updates_existing_row(isolated_bugs):
    ev = bugs.log("HIGH", "test", "first bug")
    ts = ev["timestamp"]
    assert bugs.mark_resolved(ts, "fixed in C.X") is True
    rows = [json.loads(l) for l in isolated_bugs.read_text().splitlines() if l.strip()]
    assert rows[0]["resolved"] is True
    assert rows[0]["resolution_note"] == "fixed in C.X"
    assert "resolved_at" in rows[0]


def test_mark_resolved_idempotent_on_repeat(isolated_bugs):
    ev = bugs.log("HIGH", "test", "first bug")
    ts = ev["timestamp"]
    assert bugs.mark_resolved(ts, "fixed") is True
    # Re-calling does not "update" again (it's already resolved).
    assert bugs.mark_resolved(ts) is False


def test_mark_resolved_returns_false_for_unknown_timestamp(isolated_bugs):
    bugs.log("HIGH", "test", "first bug")
    assert bugs.mark_resolved(9_999_999_999) is False


def test_unresolved_count_excludes_resolved(isolated_bugs):
    # Stagger timestamps so mark_resolved can match exactly one row at a time.
    a = bugs.log("CRITICAL", "test", "critical 1")
    time.sleep(1.01)
    b = bugs.log("HIGH", "test", "high 1")
    time.sleep(1.01)
    c = bugs.log("MEDIUM", "test", "medium 1")
    time.sleep(1.01)
    d = bugs.log("LOW", "test", "low 1")
    assert bugs.unresolved_count(min_severity="MEDIUM") == 3
    bugs.mark_resolved(a["timestamp"], "fixed")
    assert bugs.unresolved_count(min_severity="MEDIUM") == 2
    assert bugs.unresolved_count(min_severity="CRITICAL") == 0
    assert bugs.unresolved_count(min_severity="LOW") == 3
