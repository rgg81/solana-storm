"""Goal-drift surfacing tests (E.1, E.2, E.4).

History: PM/RM summaries praised "discipline holds" while monthly run-rate
quietly decayed from +2.46%/mo -> +2.24%/mo over the streak. Sharpe of 0.86
over 85 cash days + 2 trades was being cited as performance evidence. The
rolling 7d run-rate (0%) was nowhere in the agent prompt.

These tests exercise the new goals.py helpers against synthetic equity
ledgers — the goal block must surface lifetime + 7d-rolling, flag long flat
streaks, and gate Sharpe display until deployment exceeds 30%.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from predictions.fund import goals


@pytest.fixture
def fake_equity(tmp_path, monkeypatch):
    eq_path = tmp_path / "equity.jsonl"
    monkeypatch.setattr(goals, "_EQUITY_PATH", eq_path)
    return eq_path


def _write_equity(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_rolling_7d_returns_none_with_too_few_rows(fake_equity):
    _write_equity(fake_equity, [
        {"timestamp": int(time.time()), "equity_usd": 10_000.0, "deployed_pct": 0.0},
    ])
    assert goals._rolling_runrate_pct(7.0) is None


def test_rolling_7d_returns_flat_zero_for_flat_window(fake_equity):
    now = time.time()
    rows = [
        {"timestamp": int(now - 6 * 86400), "equity_usd": 10_101.78, "deployed_pct": 0.0},
        {"timestamp": int(now - 3 * 86400), "equity_usd": 10_101.78, "deployed_pct": 0.0},
        {"timestamp": int(now), "equity_usd": 10_101.78, "deployed_pct": 0.0},
    ]
    _write_equity(fake_equity, rows)
    assert goals._rolling_runrate_pct(7.0) == 0.0


def test_consecutive_flat_ticks_counts_trailing_run(fake_equity):
    rows = [
        {"timestamp": 1, "equity_usd": 9_900.0, "deployed_pct": 0.10},
        {"timestamp": 2, "equity_usd": 10_000.0, "deployed_pct": 0.05},
        {"timestamp": 3, "equity_usd": 10_101.78, "deployed_pct": 0.0},
        {"timestamp": 4, "equity_usd": 10_101.78, "deployed_pct": 0.0},
        {"timestamp": 5, "equity_usd": 10_101.78, "deployed_pct": 0.0},
    ]
    _write_equity(fake_equity, rows)
    # Trailing 3 are flat.
    assert goals._consecutive_below_floor_ticks() == 3


def test_sharpe_gating_returns_false_when_deployment_below_threshold(fake_equity):
    # 90 ticks all zero deployment -> Sharpe should be suppressed.
    rows = [
        {"timestamp": int(time.time() - (90 - i) * 3600), "equity_usd": 10_000.0, "deployed_pct": 0.0}
        for i in range(90)
    ]
    _write_equity(fake_equity, rows)
    assert goals._sharpe_is_meaningful() is False


def test_sharpe_gating_returns_true_when_deployment_above_threshold(fake_equity):
    # 100 ticks, 40 with non-zero deployment.
    rows = []
    for i in range(100):
        rows.append({
            "timestamp": int(time.time() - (100 - i) * 3600),
            "equity_usd": 10_000.0,
            "deployed_pct": 0.10 if i % 2 == 0 else 0.0,  # 50% deployment
        })
    _write_equity(fake_equity, rows)
    assert goals._sharpe_is_meaningful() is True
