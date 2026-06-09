"""Dust-residual guard for full-position closes.

Bug (tick-117, 2026-06-09): after JTO closed at TP, a floating-point sliver
(0.0012 units / $0.0007) remained in holdings because the sell computed
`units_to_sell = usd_amount / price_usd` and the PM's usd_amount did not exactly
match `holdings_units * price`. The sliver still carried stop_loss=$0.606 and
re-fired a PHANTOM stop_loss trigger the next tick when price dipped below it.

Fix (two ends per CLAUDE.md):
  1. execute_trade: after a sell, if the remaining position's market value is
     below DUST_USD, sweep it (zero units + cost basis, clear stop/TP). A full
     close becomes actually full.
  2. check_stop_triggers / mark_to_market n_positions: treat sub-dust positions
     as flat (defense-in-depth so dust from any source can't fire triggers).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from predictions.fund import account


@pytest.fixture
def isolated_account(tmp_path, monkeypatch):
    """Point account.py state paths at an isolated tmp dir."""
    monkeypatch.setattr(account, "ACCOUNT_PATH", tmp_path / "account.json")
    monkeypatch.setattr(account, "TRADES_PATH", tmp_path / "trades.jsonl")
    monkeypatch.setattr(account, "EQUITY_PATH", tmp_path / "equity.jsonl")
    monkeypatch.setattr(account, "TRIGGERS_LOG_PATH", tmp_path / "triggers.jsonl")
    state = account.initialize(10_000.0)
    return state


def _seed_position(state, units, cost_basis, stop=None, tp=None, entry=0.5642):
    state["holdings"]["JTO"] = {
        "units": units,
        "cost_basis_usd": cost_basis,
        "avg_entry_price_usd": entry,
        "stop_loss_price_usd": stop,
        "take_profit_price_usd": tp,
        "peak_price_since_entry": entry,
    }


def test_full_close_leaves_no_dust(isolated_account):
    """Selling ~all units via a usd_amount that rounds short must NOT leave a
    sub-cent sliver carrying live stop/TP."""
    state = isolated_account
    # 221.5626 units @ entry — sell a usd_amount that maps to slightly FEWER units
    _seed_position(state, units=221.5626, cost_basis=125.0, stop=0.606, tp=0.64653)
    # PM-style order: usd_amount derived from a marginally-off unit count
    # 221.5614 units * 0.6507 = 144.1670 (leaves 0.0012 unit dust pre-fix)
    account.execute_trade(state, "JTO", "sell", usd_amount=144.167, price_usd=0.6507,
                          fee_pct=0.003, slippage_pct=0.0005, reason="take_profit close")
    h = state["holdings"]["JTO"]
    assert h["units"] == 0.0, f"dust remained: {h['units']}"
    assert h.get("stop_loss_price_usd") is None
    assert h.get("take_profit_price_usd") is None


def test_dust_position_does_not_trigger_stop(isolated_account):
    """A sub-dust residual carrying a stale stop must not fire a phantom trigger."""
    state = isolated_account
    _seed_position(state, units=0.00121, cost_basis=0.0007, stop=0.606, tp=0.64653)
    triggers = account.check_stop_triggers(state, {"JTO": 0.6026})
    assert triggers == [], f"phantom trigger fired on dust: {triggers}"


def test_dust_position_not_counted_as_open(isolated_account):
    """n_positions must exclude sub-dust residuals."""
    state = isolated_account
    _seed_position(state, units=0.00121, cost_basis=0.0007, stop=0.606)
    mtm = account.mark_to_market(state, {"JTO": 0.6026})
    assert mtm["n_positions"] == 0, f"dust counted as open position: {mtm['n_positions']}"


def test_real_position_still_triggers(isolated_account):
    """Regression guard: a genuine position above dust must still trigger."""
    state = isolated_account
    _seed_position(state, units=221.56, cost_basis=125.0, stop=0.606, tp=0.64653)
    triggers = account.check_stop_triggers(state, {"JTO": 0.60})  # below stop
    assert len(triggers) == 1 and triggers[0]["trigger"] == "stop_loss"


def test_partial_sell_above_dust_keeps_levels(isolated_account):
    """A partial sell that leaves a real (above-dust) position keeps stop/TP."""
    state = isolated_account
    _seed_position(state, units=200.0, cost_basis=120.0, stop=0.50, tp=0.70)
    account.execute_trade(state, "JTO", "sell", usd_amount=50.0, price_usd=0.60,
                          fee_pct=0.003, slippage_pct=0.0005, reason="partial")
    h = state["holdings"]["JTO"]
    assert h["units"] > 1.0  # still a real position
    assert h.get("stop_loss_price_usd") == 0.50
    assert h.get("take_profit_price_usd") == 0.70
