"""A fresh entry must reset per-position carry-over fields.

Bug (tick-133, 2026-06-13): the RENDER regime probe re-opened a position in a
holdings dict left over from the prior (fully-closed) RENDER trade. The dict
persists after a full close (dust-sweep zeros units but does not delete the
entry), so the new entry inherited a stale peak_price_since_entry ($2.36, from
the +$133 winner) and an old first_buy_unix. peak_price_since_entry only matters
for trailing-stop logic, but a stale peak well above the new entry would let a
future TRAIL_UP set a stop above the live price — a phantom risk.

Fix: when a BUY opens a position from flat (pre-buy units worth < DUST_USD),
reset first_buy_unix to now and peak_price_since_entry to the entry price. A buy
that ADDS to an already-open position keeps the original first_buy_unix and the
higher running peak.
"""
from __future__ import annotations

from predictions.fund import account as acct


def _flat_state(cash=10_000.0):
    return {
        "deposit_usd": 10_000.0, "cash_usd": cash, "holdings": {},
        "created_at": 1_779_000_000, "trade_count": 0,
        "total_fees_paid_usd": 0.0, "total_slippage_usd": 0.0,
        "peak_equity_usd": 10_000.0, "halted": False, "halt_reason": None,
    }


def test_fresh_entry_resets_stale_peak_and_first_buy():
    """Re-opening a closed position resets the stale carry-over fields."""
    state = _flat_state()
    # Simulate a prior, fully-closed RENDER position whose dict lingered.
    state["holdings"]["RENDER"] = {
        "units": 0.0, "cost_basis_usd": 0.0, "avg_entry_price_usd": 0.0,
        "first_buy_unix": 1_779_576_749, "last_buy_unix": 1_779_600_000,
        "stop_loss_price_usd": None, "take_profit_price_usd": None,
        "stop_set_by": None, "stop_set_at_unix": 0,
        "peak_price_since_entry": 2.36,  # stale, from the prior winner
    }
    r = acct.execute_trade(state, "RENDER", "buy", 125.0, 1.7963, 0.001, 0.003)
    assert r["executed"] is True
    h = state["holdings"]["RENDER"]
    # Peak reset to the new entry price, not the stale $2.36.
    assert h["peak_price_since_entry"] == 1.7963
    # first_buy_unix refreshed away from the old timestamp.
    assert h["first_buy_unix"] != 1_779_576_749
    assert h["first_buy_unix"] == h["last_buy_unix"]


def test_add_to_open_position_keeps_first_buy_and_peak():
    """A buy that adds to an OPEN position must NOT reset its history."""
    state = _flat_state()
    acct.execute_trade(state, "JTO", "buy", 100.0, 0.50, 0.001, 0.003)
    h = state["holdings"]["JTO"]
    first = h["first_buy_unix"]
    # Simulate the price having peaked higher between buys.
    h["peak_price_since_entry"] = 0.80
    acct.execute_trade(state, "JTO", "buy", 50.0, 0.60, 0.001, 0.003)  # add
    assert h["first_buy_unix"] == first          # unchanged
    assert h["peak_price_since_entry"] == 0.80   # keeps the higher running peak


def test_first_ever_entry_sets_peak_to_entry():
    """A brand-new symbol's first buy sets peak to the entry price."""
    state = _flat_state()
    acct.execute_trade(state, "NEW", "buy", 100.0, 5.0, 0.001, 0.003)
    h = state["holdings"]["NEW"]
    assert h["peak_price_since_entry"] == 5.0
    assert h["first_buy_unix"] == h["last_buy_unix"] != 0
