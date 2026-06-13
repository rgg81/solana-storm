"""End-of-execution mark-to-market must price EVERY held position, not just trades.

Bug (tick-134, 2026-06-13): execute_pm_orders built its MTM price map only from
buy-trade tickers. On a stop-update-only tick (the RENDER TIGHTEN_STOP — no
trades), the map was empty, so mark_to_market valued the open RENDER position at
cost basis ($125) instead of market (~$122), snapshotting an inflated equity row.

Fix: _mtm_prices() seeds the map from this tick's risk input (per_sym
current_price_usd) for all symbols, with trade fills overriding.
"""
from __future__ import annotations

from predictions.fund import runner


def test_prices_all_held_symbols_from_risk_input():
    """A stop-update-only tick (no trades) still prices held positions at market."""
    per_sym = {
        "RENDER": {"current_price_usd": 1.75},
        "SOL": {"current_price_usd": 68.0},
    }
    pm = {"trades": [], "stop_updates": [{"ticker": "RENDER", "new_stop_usd": 1.733}]}
    prices = runner._mtm_prices(pm, per_sym)
    assert prices["RENDER"] == 1.75
    assert prices["SOL"] == 68.0


def test_trade_fill_overrides_risk_input_midprice():
    """The actual buy fill price overrides the risk-input mid-price for that name."""
    per_sym = {"JTO": {"current_price_usd": 0.55}}
    pm = {"trades": [{"ticker": "JTO", "side": "buy", "price_usd": 0.5622}]}
    prices = runner._mtm_prices(pm, per_sym)
    assert prices["JTO"] == 0.5622


def test_empty_inputs_yield_empty_map():
    assert runner._mtm_prices({"trades": []}, {}) == {}
    assert runner._mtm_prices({}, None) == {}


def test_zero_or_missing_price_skipped():
    """A symbol with no usable current price is omitted (not priced at 0)."""
    per_sym = {"X": {"current_price_usd": 0}, "Y": {}, "Z": {"current_price_usd": 2.5}}
    prices = runner._mtm_prices({"trades": []}, per_sym)
    assert prices == {"Z": 2.5}
