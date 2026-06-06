"""lessons.md body sync regression test.

Historically the lessons.md body kept showing '_(none yet — cold start)_' for
Validated/Candidate/Disconfirmed sections even after the frontmatter accumulated
16+ validated_rules_count. write() preserved load_body() verbatim and no module
ever rendered the aggregated rules back into the body.

This test exercises lessons_io.refresh_body against a populated reflections
ledger and asserts the body contains the rule text (not the placeholder).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from predictions.fund import lessons_io


@pytest.fixture
def isolated_lessons(tmp_path, monkeypatch):
    lessons = tmp_path / "lessons.md"
    state = tmp_path / "state"
    state.mkdir()
    refl = state / "lessons_reflections.jsonl"
    refl.write_text("")
    lessons.write_text(
        "---\n"
        "version: 1\n"
        "validated_rules_count: 0\n"
        "---\n\n"
        "# SMAF — Rolling Lessons & Specialist Memory\n\n"
        "## Validated lessons (≥3 audit confirms — HARD VETO inputs for all specialists)\n\n"
        "_(none yet — cold start)_\n\n"
        "## Candidate lessons (1-2 confirms — soft signals, awaiting promotion)\n\n"
        "_(none yet)_\n\n"
        "## Disconfirmed lessons (status: DISCONFIRMED — anti-patterns to NOT use)\n\n"
        "_(none yet)_\n"
    )
    monkeypatch.setattr(lessons_io, "LESSONS_PATH", lessons)
    monkeypatch.setattr(lessons_io, "STATE_DIR", state)
    monkeypatch.setattr(lessons_io, "REFL_PATH", refl)
    return lessons, refl


def _write_refl(refl: Path, rows: list[dict]) -> None:
    refl.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_refresh_body_renders_validated_rule(isolated_lessons):
    lessons, refl = isolated_lessons
    _write_refl(refl, [
        {
            "kind_row": "new_candidate",
            "candidate_id": "cand_1",
            "kind": "good_rejection",
            "pattern": "When MA-Pes <= -0.50 in strong_bear, reject regardless of MA-Opt",
            "candidate_lesson": "MA-Pes <= -0.50 HARD VETO directional 8/8",
            "tick_id": 1,
            "supporting_count": 5,
        },
        {
            "kind_row": "confirmation",
            "prior_candidate_id": "cand_1",
            "kind": "confirming",
            "new_supporting_count": 5,
            "new_status_suggestion": "validated",
            "tick_id": 2,
        },
    ])
    result = lessons_io.refresh_body()
    assert result["updated"] is True
    assert result["n_validated"] == 1
    body = lessons.read_text()
    assert "_(none yet — cold start)_" not in body, "cold-start placeholder must be replaced"
    assert "MA-Pes" in body, "validated rule text must appear in body"
    assert "LESSONS_AUTOGEN_BEGIN" in body and "LESSONS_AUTOGEN_END" in body


def test_refresh_body_idempotent(isolated_lessons):
    lessons, refl = isolated_lessons
    _write_refl(refl, [
        {
            "kind_row": "new_candidate",
            "candidate_id": "cand_1",
            "kind": "good_rejection",
            "pattern": "Test pattern",
            "candidate_lesson": "Test lesson",
            "tick_id": 1,
            "supporting_count": 5,
        },
    ])
    lessons_io.refresh_body()
    first = lessons.read_text()
    lessons_io.refresh_body()
    second = lessons.read_text()
    # Idempotent — the second call should not duplicate the autogen block.
    assert first.count("LESSONS_AUTOGEN_BEGIN") == 1
    assert second.count("LESSONS_AUTOGEN_BEGIN") == 1


def test_refresh_body_no_op_when_no_reflections(isolated_lessons):
    lessons, refl = isolated_lessons
    # refl is empty
    result = lessons_io.refresh_body()
    assert result["updated"] is False
