"""fetch_dexscreener must reject pools reporting impossible price-change.

Bug (tick-141/142, 2026-06-16): JUP's max-liquidity Solana pool returned a
priceChange.h24 of +529,119% (a wrong/bridged/mis-reported pool). The old code
picked purely by liquidity, so it surfaced this garbage as JUP's signal two ticks
running — specialists had to manually flag it and score 0.0.

Fix: among Solana pairs, prefer the DEEPEST pool whose 24h price-change is
PLAUSIBLE (|chg| <= 900% — an established token does not 10x in a day). If every
pool's change is impossible, keep the deepest pool's valid price/liquidity but
null the corrupt change fields and set chg_corrupt=True so nothing downstream
reads false momentum.
"""
from __future__ import annotations

from predictions.fund import stage_phase2 as s2


def _pool(liq, chg24, price=1.0, dex="meteora"):
    return {"chainId": "solana", "priceUsd": price,
            "liquidity": {"usd": liq}, "volume": {"h24": 1000, "h1": 10},
            "priceChange": {"h24": chg24, "h6": chg24 / 2, "h1": chg24 / 4},
            "txns": {"h24": {"buys": 5, "sells": 5}}, "dexId": dex, "pairAddress": "x"}


def test_prefers_plausible_chg_pool_over_deeper_corrupt_one():
    # Deepest pool has garbage +529119% (the JUP case); a shallower pool is sane.
    pools = [_pool(55_000_000, 529119.0, dex="bridged"), _pool(1_200_000, 9.8, dex="meteora")]
    d = s2._build_dex_from_pools(pools)
    assert d["chg_h24"] == 9.8          # picked the sane pool, not the deepest-garbage one
    assert d["dex"] == "meteora"
    assert not d.get("chg_corrupt")


def test_all_corrupt_keeps_price_liq_but_nulls_change():
    pools = [_pool(55_000_000, 529119.0), _pool(2_000_000, -99999.0)]
    d = s2._build_dex_from_pools(pools)
    assert d["chg_corrupt"] is True
    assert d["chg_h24"] is None and d["chg_h6"] is None and d["chg_h1"] is None
    assert d["liq_usd"] == 55_000_000.0   # deepest pool's valid liquidity retained
    assert d["price_usd"] == 1.0


def test_normal_case_picks_deepest():
    pools = [_pool(5_000_000, 4.2), _pool(1_000_000, 3.1)]
    d = s2._build_dex_from_pools(pools)
    assert d["liq_usd"] == 5_000_000.0
    assert d["chg_h24"] == 4.2
    assert not d.get("chg_corrupt")


def test_no_pools_returns_none():
    assert s2._build_dex_from_pools([]) is None


def test_boundary_900_is_plausible_901_is_not():
    assert s2._chg_plausible(900.0) is True
    assert s2._chg_plausible(900.1) is False
    assert s2._chg_plausible(-900.0) is True
    assert s2._chg_plausible(None) is False
