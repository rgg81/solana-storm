"""Tests must never write into the production state dir.

Bug (2026-06-15): test fixtures (test_fresh_entry_reset.py) called
account.execute_trade() without path isolation, leaking 40 rows (incl. ticker
"NEW") into the live predictions/fund/state/trades.jsonl and firing 2 HIGH
fee/slippage healthcheck mismatches. Fixed by an autouse conftest fixture that
redirects account paths to tmp_path, plus a hard guard in account._atomic_write.
"""
from __future__ import annotations

from pathlib import Path

from predictions.fund import account


_PROD_STATE = Path(account.__file__).resolve().parent / "state"


def test_autouse_fixture_redirects_account_paths():
    """The autouse fixture must have moved every account write-path off prod."""
    for attr in ("ACCOUNT_PATH", "TRADES_PATH", "EQUITY_PATH", "TRIGGERS_LOG_PATH"):
        p = Path(getattr(account, attr)).resolve()
        assert p.parent != _PROD_STATE, f"{attr} still points at production state: {p}"


def test_execute_trade_writes_only_to_tmp():
    """A trade executes against redirected paths; prod trades.jsonl is untouched."""
    prod_trades = _PROD_STATE / "trades.jsonl"
    before = prod_trades.read_text() if prod_trades.exists() else None
    state = {
        "deposit_usd": 10_000.0, "cash_usd": 10_000.0, "holdings": {},
        "created_at": 1_779_000_000, "trade_count": 0,
        "total_fees_paid_usd": 0.0, "total_slippage_usd": 0.0,
        "peak_equity_usd": 10_000.0, "halted": False, "halt_reason": None,
    }
    r = account.execute_trade(state, "ISOLATED_TEST", "buy", 100.0, 1.0, 0.001, 0.003)
    assert r["executed"] is True
    # The redirected (tmp) trades log got the row...
    assert Path(account.TRADES_PATH).exists()
    # ...and the production log is byte-for-byte unchanged.
    after = prod_trades.read_text() if prod_trades.exists() else None
    assert after == before, "production trades.jsonl was modified by a test!"


def test_guard_raises_if_pointed_at_prod(monkeypatch):
    """If a test forgets isolation, the _atomic_write guard raises loudly."""
    monkeypatch.setattr(account, "TRADES_PATH", _PROD_STATE / "trades.jsonl")
    import pytest
    with pytest.raises(RuntimeError, match="production state dir"):
        account._append_jsonl(account.TRADES_PATH, {"ticker": "X"})
