"""TTL cache for Helius holder_distribution.

Context (tick-118, 2026-06-09): with Helius newly live, the free tier timed out
on ~5/10 symbols per tick (15s × 3 retries each), giving partial coverage and
firing the Phase 7 helius_health audit (16 retry-failure bugs/24h). Holder
concentration is slow-moving, so caching successful reads with a TTL lets
steady-state ticks make few API calls → no burst → coverage converges and the
audit noise stops. Only real successes are cached; rpc_failed fallbacks are not,
so a failed symbol is retried next tick until it succeeds.
"""
from __future__ import annotations

import json
import time

import pytest

from predictions.fund.helpers import onchain_stats


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(onchain_stats, "_HOLDER_CACHE_FILE", tmp_path / "holder_cache.json")
    return tmp_path / "holder_cache.json"


_GOOD = {"n_top_accounts": 20, "top_1_pct": 21.2, "top_5_pct": 39.1,
         "top_10_pct": 50.4, "concentrated": False, "well_distributed": False,
         "total_supply": 1e9}
_FAILED = {"source": "dexscreener_fallback", "helius_status": "rpc_failed",
           "primary_pool_liq_usd": 1.0}


def test_cache_miss_computes_and_stores(isolated_cache, monkeypatch):
    calls = {"n": 0}
    def fake_compute(mint):
        calls["n"] += 1
        return dict(_GOOD)
    monkeypatch.setattr(onchain_stats, "_compute_holder_distribution", fake_compute)
    out = onchain_stats.holder_distribution("MINT1")
    assert out["top_10_pct"] == 50.4
    assert calls["n"] == 1
    assert isolated_cache.exists()


def test_cache_hit_skips_compute(isolated_cache, monkeypatch):
    calls = {"n": 0}
    def fake_compute(mint):
        calls["n"] += 1
        return dict(_GOOD)
    monkeypatch.setattr(onchain_stats, "_compute_holder_distribution", fake_compute)
    onchain_stats.holder_distribution("MINT1")   # miss → compute
    onchain_stats.holder_distribution("MINT1")   # hit → no compute
    assert calls["n"] == 1, "second call within TTL must not recompute"


def test_expired_cache_refetches(isolated_cache, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(onchain_stats, "_compute_holder_distribution",
                        lambda m: (calls.__setitem__("n", calls["n"] + 1) or dict(_GOOD)))
    onchain_stats.holder_distribution("MINT1")
    # Rewrite the cache entry with an old timestamp (older than TTL)
    data = json.loads(isolated_cache.read_text())
    data["MINT1"]["ts"] = int(time.time()) - onchain_stats._HOLDER_CACHE_TTL_SEC - 10
    isolated_cache.write_text(json.dumps(data))
    onchain_stats.holder_distribution("MINT1")
    assert calls["n"] == 2, "expired entry must refetch"


def test_failure_not_cached(isolated_cache, monkeypatch):
    """rpc_failed fallback must NOT be cached — retry next tick."""
    seq = [dict(_FAILED), dict(_GOOD)]
    monkeypatch.setattr(onchain_stats, "_compute_holder_distribution",
                        lambda m: seq.pop(0))
    first = onchain_stats.holder_distribution("MINT1")
    assert first.get("helius_status") == "rpc_failed"
    # cache must be empty/without MINT1 → second call recomputes (gets _GOOD)
    second = onchain_stats.holder_distribution("MINT1")
    assert second["top_10_pct"] == 50.4


def test_corrupt_cache_file_tolerated(isolated_cache, monkeypatch):
    isolated_cache.write_text("{ not valid json")
    monkeypatch.setattr(onchain_stats, "_compute_holder_distribution", lambda m: dict(_GOOD))
    out = onchain_stats.holder_distribution("MINT1")
    assert out["top_10_pct"] == 50.4  # falls back to compute, no crash
