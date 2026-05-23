import json
from pathlib import Path
from unittest.mock import patch
from predictions.audit import processor


def test_process_due_audits_writes_outcomes_and_updates_lessons(tmp_path, monkeypatch):
    # arrange: stub config dirs
    monkeypatch.setattr(processor.config, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(processor.config, "PENDING_AUDIT_PATH", tmp_path / "pending.jsonl")
    lessons = tmp_path / "predictions" / "diary" / "lessons.md"
    lessons.parent.mkdir(parents=True, exist_ok=True)
    lessons.write_text(
        "---\nversion: 1\ntotal_picks_audited: 0\n"
        "late_curve:\n  picks_audited: 0\n  hit_rate_all_time: null\n---\nbody\n"
    )
    # one due pick from late_curve specialist
    processor.enqueue(processor.config.PENDING_AUDIT_PATH, {
        "pick_id": "1779000000-late_curve-AAAAAAAA",
        "mint": "A" * 44, "pool": "B" * 44,
        "specialist": "late_curve",
        "entry_quote_lamports": 200_000_000,
        "entry_base_lamports": 100_000_000_000,
        "due_unix": 1,
        "recommended_exit": {"rule": "graduation_or_30pct_or_6h"},
    })

    # mock the on-chain fetch
    with patch.object(processor, "_fetch_current_pool_state") as fetcher:
        fetcher.return_value = {
            "current_quote_reserve_lamports": 100_000_000,  # halved
            "current_base_reserve_lamports": 100_000_000_000,
            "pool_closed": False,
        }
        n = processor.process_due_audits(now_unix=10, lessons_path=lessons)

    assert n == 1
    # outcome file written
    out_dir = tmp_path / "predictions" / "diary" / "outcomes"
    outcomes = list(out_dir.glob("*-outcome.md"))
    assert len(outcomes) == 1
    body = outcomes[0].read_text()
    assert "realized_return" in body
    assert "late_curve" in body

    # lessons.md updated
    from predictions.diary import lessons_io
    fm = lessons_io.load_frontmatter(lessons)
    assert fm["total_picks_audited"] == 1
    assert fm["late_curve"]["picks_audited"] == 1
