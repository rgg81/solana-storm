"""Phase 7 auto-audit checks regression test."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from predictions.fund import auto_audit


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_audit, "STATE", tmp_path)
    return tmp_path


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_price_history_clean_passes(isolated_state):
    _write_jsonl(isolated_state / "universe_price_history.jsonl", [
        {"tick_id": 1, "symbol": "PYTH", "price_usd": 0.03},
        {"tick_id": 2, "symbol": "PYTH", "price_usd": 0.032},
        {"tick_id": 3, "symbol": "PYTH", "price_usd": 0.031},
    ])
    r = auto_audit.check_price_history_jumps()
    assert r["passed"] is True


def test_price_history_jump_fails(isolated_state):
    _write_jsonl(isolated_state / "universe_price_history.jsonl", [
        {"tick_id": 1, "symbol": "PYTH", "price_usd": 0.03},
        {"tick_id": 2, "symbol": "PYTH", "price_usd": 160.0},  # 5000x jump
    ])
    r = auto_audit.check_price_history_jumps()
    assert r["passed"] is False
    assert "violations" in r["context"]


def test_price_history_jump_tolerated_when_flagged(isolated_state):
    _write_jsonl(isolated_state / "universe_price_history.jsonl", [
        {"tick_id": 1, "symbol": "PYTH", "price_usd": 0.03},
        {"tick_id": 2, "symbol": "PYTH", "price_usd": 160.0,
         "price_corrected_2026_06_06": True, "original_corrupt_price_usd": 160.0},
    ])
    r = auto_audit.check_price_history_jumps()
    assert r["passed"] is True


def test_audit_coverage_passes_when_all_audited(isolated_state):
    _write_jsonl(isolated_state / "trades.jsonl", [
        {"side": "buy", "ticker": "JTO"},
        {"side": "sell", "ticker": "JTO"},
    ])
    _write_jsonl(isolated_state / "closed_trades_audit.jsonl", [
        {"ticker": "JTO"},
    ])
    r = auto_audit.check_audit_coverage()
    assert r["passed"] is True


def test_audit_coverage_fails_when_sell_unaudited(isolated_state):
    _write_jsonl(isolated_state / "trades.jsonl", [
        {"side": "sell", "ticker": "RENDER"},
        {"side": "sell", "ticker": "JTO"},
    ])
    _write_jsonl(isolated_state / "closed_trades_audit.jsonl", [
        {"ticker": "JTO"},
    ])
    r = auto_audit.check_audit_coverage()
    assert r["passed"] is False
    assert any("RENDER" in g for g in r["context"]["gaps"])


def test_run_returns_summary(isolated_state):
    # Empty state — every check should pass (or be a no-op).
    summary = auto_audit.run()
    assert "passed" in summary
    assert "failed" in summary
    assert "results" in summary
