from pathlib import Path
from predictions.diary import shadow_watches


def test_write_and_list(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow_watches.config, "SHADOW_WATCH_DIR", tmp_path)
    pick_id = shadow_watches.write_shadow_watch(
        specialist="late_curve", mint="A" * 44, pool="B" * 44,
        would_be_conviction="BUY MEDIUM", vetoed_by="C1",
        entry_quote=1000, entry_base=2000,
        recommended_exit={"rule": "graduation_or_30pct_or_6h", "hard_timeout_hours": 6}
    )
    assert (tmp_path / f"{pick_id}-shadow.md").exists()
    items = shadow_watches.list_pending(now_unix=999999999999)
    assert any(it["pick_id"] == pick_id for it in items)
