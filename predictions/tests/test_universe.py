import os, subprocess
from predictions import universe

def test_fetch_pregrad_uses_dry_run_when_env_set(monkeypatch):
    monkeypatch.setenv("PUMP_PREDICTION_REHEARSAL", "1")
    result = universe.fetch_pregrad_universe()
    assert result["error"] is None
    assert isinstance(result["data"], list)

def test_fetch_graduated_returns_list(monkeypatch):
    monkeypatch.setenv("PUMP_PREDICTION_REHEARSAL", "1")
    result = universe.fetch_graduated_universe()
    assert result["error"] is None
    assert isinstance(result["data"], list)

def test_record_curve_snapshot_writes_db(tmp_path, monkeypatch):
    monkeypatch.setattr(universe.config, "CURVE_HISTORY_DB", tmp_path / "curve.db")
    universe.record_pregrad_universe([
        {"mint": "A" * 44, "bonding_curve_pct": 50.0, "market_cap_sol": 10.0,
         "reply_count": 1, "recent_trades_count": 2, "fetched_at_unix": 1000}
    ])
    from predictions.state import curve_history
    rows = curve_history.read_snapshots(tmp_path / "curve.db", mint="A" * 44, since_unix=0)
    assert len(rows) == 1
