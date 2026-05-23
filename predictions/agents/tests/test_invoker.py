import json, os
from pathlib import Path
from predictions.agents import invoker

def test_load_prompt_returns_string():
    text = invoker.load_prompt("late_curve")
    assert "Late-Curve Momentum Agent" in text
    assert "BUY HIGH" in text

def test_build_context_includes_universe_and_lessons(tmp_path, monkeypatch):
    lessons = tmp_path / "lessons.md"
    lessons.write_text("# lessons\n")
    monkeypatch.setattr(invoker.config, "_REPO_ROOT", tmp_path)
    (tmp_path / "predictions" / "diary").mkdir(parents=True)
    (tmp_path / "predictions" / "diary" / "lessons.md").write_text("# lessons file\nC1 ...")
    ctx = invoker.build_context("late_curve", universe={"data": [{"mint": "A" * 44}]}, curve_history={})
    assert ctx["lessons_md"].startswith("# lessons file")
    assert ctx["universe"]["data"][0]["mint"] == "A" * 44
