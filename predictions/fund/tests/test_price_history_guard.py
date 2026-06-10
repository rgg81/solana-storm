"""Price-sanity guard on the universe-price-history write path.

Bug (tick-121, 2026-06-10): a corrupt DexScreener response returned ~4800x
prices for JUP/PYTH/PUMP/BONK (e.g. PYTH $0.0317 → $154.69). snapshot_tick wrote
them straight into the counterfactual ledger, which tripped Phase 7
price_history_jumps (HIGH) and would poison next-tick indicators. Three
specialists independently flagged the corrupt chg_* fields in the same response.

Fix: _guard_price() rejects an implausible single-tick jump (>100x or <1/100x vs
the symbol's prior recorded price — far beyond any real 6h move) and carries the
prior price forward, flagging the row. A 100x threshold leaves real memecoin
volatility untouched (nothing moves 100x in 6h). First-seen symbols pass.
"""
from __future__ import annotations

import json

import pytest

from predictions.fund import universe_price_history as uph


def test_guard_passes_normal_move():
    price, corrupt = uph._guard_price("PYTH", 0.0317, 0.0302)
    assert corrupt is False and price == 0.0317


def test_guard_passes_large_but_real_move():
    """A 5x move (wild memecoin) is still real — must pass."""
    price, corrupt = uph._guard_price("WIF", 0.50, 0.10)
    assert corrupt is False and price == 0.50


def test_guard_catches_upward_corruption():
    """~4800x jump (the tick-121 corruption) is carried forward + flagged."""
    price, corrupt = uph._guard_price("PYTH", 154.69, 0.0317)
    assert corrupt is True and price == 0.0317


def test_guard_catches_downward_corruption():
    price, corrupt = uph._guard_price("PYTH", 0.0001, 0.0317)
    assert corrupt is True and price == 0.0317


def test_guard_passes_first_seen_symbol():
    """No prior price → cannot judge → accept."""
    price, corrupt = uph._guard_price("NEW", 1.23, None)
    assert corrupt is False and price == 1.23


def test_guard_passes_when_prior_zero():
    price, corrupt = uph._guard_price("X", 1.23, 0.0)
    assert corrupt is False and price == 1.23


def test_snapshot_carries_forward_corrupt_price(tmp_path, monkeypatch):
    """Integration: a corrupt current_price_usd in the risk input is carried
    forward from the symbol's last history row, not written raw."""
    monkeypatch.setattr(uph, "HISTORY_PATH", tmp_path / "hist.jsonl")
    monkeypatch.setattr(uph, "_RISK_JSON", tmp_path / "smaf_risk.json")
    # Seed a clean prior row for PYTH at t53.
    (tmp_path / "hist.jsonl").write_text(json.dumps({
        "tick_id": 53, "symbol": "PYTH", "price_usd": 0.0317}) + "\n")
    risk_input = {"specialist_consensus_per_symbol": {
        "PYTH": {"current_price_usd": 154.69, "consensus": 0.09}}}
    n = uph.snapshot_tick(54, risk_input, {"trades": []})
    assert n == 1
    rows = [json.loads(l) for l in (tmp_path / "hist.jsonl").read_text().splitlines()]
    t54 = [r for r in rows if r["tick_id"] == 54][0]
    assert t54["price_usd"] == 0.0317  # carried forward, not 154.69
    assert t54.get("price_corrupt_guard") is True
    assert t54.get("original_corrupt_price_usd") == 154.69
