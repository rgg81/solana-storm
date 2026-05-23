from pathlib import Path
from predictions.diary import lessons_io

def test_parse_frontmatter(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text("---\nversion: 9\ntotal_picks_audited: 5\nlate_curve:\n  picks_audited: 2\n  hit_rate_last_30d: 0.1\n---\nbody")
    fm = lessons_io.load_frontmatter(f)
    assert fm["version"] == 9
    assert fm["late_curve"]["picks_audited"] == 2

def test_update_frontmatter_preserves_body(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text("---\nversion: 1\n---\n# Body content\nstuff")
    lessons_io.update_frontmatter(f, {"version": 2, "total_picks_audited": 7})
    raw = f.read_text()
    assert "# Body content" in raw
    assert "version: 2" in raw

def test_update_specialist_stats(tmp_path):
    f = tmp_path / "lessons.md"
    f.write_text("---\nversion: 1\nlate_curve:\n  picks_audited: 0\n---\nbody")
    lessons_io.update_specialist_stats(f, "late_curve", {"picks_audited": 1, "hit_rate_last_30d": 0.5})
    fm = lessons_io.load_frontmatter(f)
    assert fm["late_curve"]["picks_audited"] == 1
    assert fm["late_curve"]["hit_rate_last_30d"] == 0.5
