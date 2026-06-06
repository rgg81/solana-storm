"""Anomaly-clamp logging regression test.

stage_phase6._classify_triggers clamps implausible single-tick price moves
(>500% positive, <-95% negative — physically impossible for >$200M mcap tokens
in 6-24h) and is supposed to log a MEDIUM bug per clamp so the post-mortem
exists. The historic call used bugs.log_warning() — a function that does NOT
exist on bugs.py. The AttributeError was caught by a bare `except Exception:
pass` so the clamp still fired (good) but the bug log was never written (bad).
"""
from __future__ import annotations

from unittest.mock import patch

from predictions.fund import stage_phase6


def _whatif(symbol: str, delta_pct: float, *, tag: str = "REJECT", ticks: int = 1) -> dict:
    return {
        "symbol": symbol,
        "delta_pct": delta_pct,
        "ticks_ago": ticks,
        "window": "6h" if ticks == 1 else f"{ticks * 6}h",
        "prior_decision_tag": tag,
        "prior_consensus": -0.1,
        "max_consensus_in_window": -0.1,
        "prior_price_usd": 1.0,
        "current_price_usd": 0.01 if delta_pct < 0 else 100.0,
        "prior_tick_id": 1,
        "current_tick_id": 2,
    }


def test_anomaly_clamp_negative_logs_bug():
    rows = [_whatif("PYTH", -99.0)]
    with patch.object(stage_phase6.bugs, "log") as mock_log:
        triggers = stage_phase6._classify_triggers(rows)
    assert triggers == [], "anomaly row must not become a trigger"
    assert mock_log.called, "bugs.log must be called when anomaly clamp fires"
    args, kwargs = mock_log.call_args
    # First positional is severity per bugs.log(severity, component, message, context)
    assert args[0] == "MEDIUM"
    assert "anomaly" in args[1].lower() or "anomaly" in args[2].lower()


def test_anomaly_clamp_positive_logs_bug():
    rows = [_whatif("JUP", 5000.0)]  # 5000% jump = wrong-pool artifact
    with patch.object(stage_phase6.bugs, "log") as mock_log:
        triggers = stage_phase6._classify_triggers(rows)
    assert triggers == []
    assert mock_log.called


def test_normal_delta_does_not_log_bug():
    rows = [_whatif("PYTH", -10.0)]  # 10% drop — within clamp
    with patch.object(stage_phase6.bugs, "log") as mock_log:
        stage_phase6._classify_triggers(rows)
    assert not mock_log.called
