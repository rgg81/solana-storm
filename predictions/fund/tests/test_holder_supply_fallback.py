"""Holder distribution must survive a getTokenSupply timeout when largestAccounts works.

Bug (tick-138..140, 2026-06-15): the Helius free tier dropped getTokenSupply into
a persistent timeout while getTokenLargestAccounts kept responding. The old
_compute_holder_distribution fetched the (good) largest-accounts amounts, then
called getTokenSupply, got None, and returned {"error":"no_supply"} — DISCARDING
the good read. Result: 0/11 live holder feeds for 3+ ticks, capping both SE
specialists and compressing onchain_consensus toward 0 on EVERY name.

Fix: total supply of an established token is near-constant, and the holder cache
already stores total_supply from the last success (24h TTL). When getTokenSupply
times out but largest-accounts succeeded, fall back to that cached supply so we
still emit real top-N concentration (marked supply_source="cache_fallback").
Adversarially verified (review wf_f40d8759) NOT to change the tick-140 decision —
it only restores coverage that the outage was silently suppressing.
"""
from __future__ import annotations

import predictions.fund.helpers.onchain_stats as oc


def _patch_rpc(monkeypatch, *, largest, supply):
    """Patch _rpc so getTokenLargestAccounts returns `largest` and getTokenSupply
    returns `supply` (None simulates the timeout)."""
    def fake_rpc(method, params, retries=3):
        if method == "getTokenLargestAccounts":
            return largest
        if method == "getTokenSupply":
            return supply
        return None
    monkeypatch.setattr(oc, "_rpc", fake_rpc)


# A largest-accounts response: top holders hold 5,5,5,...(10 of them)=50 units
_LARGEST = {"value": [{"uiAmount": 5.0} for _ in range(10)]}


def test_supply_timeout_uses_cache_fallback(monkeypatch):
    _patch_rpc(monkeypatch, largest=_LARGEST, supply=None)  # supply times out
    d = oc._compute_holder_distribution("MINT", supply_fallback=1000.0)
    assert "top_10_pct" in d, "should still emit concentration from fallback supply"
    assert d["top_10_pct"] == 5.0  # 50 / 1000 * 100
    assert d["top_1_pct"] == 0.5
    assert d["supply_source"] == "cache_fallback"
    assert d["total_supply"] == 1000.0


def test_supply_timeout_no_fallback_still_errors(monkeypatch):
    _patch_rpc(monkeypatch, largest=_LARGEST, supply=None)
    d = oc._compute_holder_distribution("MINT", supply_fallback=0.0)
    assert d == {"error": "no_supply"}  # unchanged graceful behavior


def test_live_supply_preferred_over_fallback(monkeypatch):
    _patch_rpc(monkeypatch, largest=_LARGEST, supply={"value": {"uiAmount": 2000.0}})
    d = oc._compute_holder_distribution("MINT", supply_fallback=1000.0)
    assert d["total_supply"] == 2000.0  # live wins
    assert d["top_10_pct"] == 2.5  # 50 / 2000 * 100
    assert d.get("supply_source") in (None, "live")


def test_holder_distribution_passes_stale_cache_supply(monkeypatch):
    """Integration: a STALE cache entry's total_supply is reused as the fallback
    when a fresh recompute hits a supply timeout."""
    import time as _t
    stale_ts = int(_t.time()) - oc._HOLDER_CACHE_TTL_SEC - 10  # expired
    monkeypatch.setattr(oc, "_load_holder_cache", lambda: {
        "MINT": {"ts": stale_ts, "data": {"top_10_pct": 9.9, "total_supply": 4000.0}}
    })
    monkeypatch.setattr(oc, "_store_holder_cache", lambda c: None)
    _patch_rpc(monkeypatch, largest=_LARGEST, supply=None)  # live supply times out
    d = oc.holder_distribution("MINT")
    assert d["top_10_pct"] == 1.25  # 50 / 4000 * 100 — recomputed from stale supply
    assert d["supply_source"] == "cache_fallback"
