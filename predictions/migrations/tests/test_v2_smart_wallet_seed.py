from unittest.mock import patch
from predictions.migrations import v2_smart_wallet_seed


def test_extract_candidates_filters_by_precision():
    raw = [
        {"wallet": "A" * 44, "winner_hits": 3, "total_observations": 5},  # precision 0.6 ✓
        {"wallet": "B" * 44, "winner_hits": 1, "total_observations": 10},  # precision 0.1 ✗
    ]
    out = v2_smart_wallet_seed.extract_candidates(raw, min_precision=0.3, min_observations=3)
    assert len(out) == 1
    assert out[0]["wallet"] == "A" * 44


def test_seed_writes_to_db(tmp_path, monkeypatch):
    monkeypatch.setattr(v2_smart_wallet_seed.config, "CURVE_HISTORY_DB", tmp_path / "curve.db")
    from predictions.state import curve_history
    curve_history.init_db(tmp_path / "curve.db")
    n = v2_smart_wallet_seed.seed_into_db([
        {"wallet": "X" * 44, "winner_hits": 4, "total_observations": 5}
    ])
    assert n == 1
