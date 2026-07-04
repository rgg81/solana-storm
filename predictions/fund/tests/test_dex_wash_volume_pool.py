"""A dexscreener pool whose 24h VOLUME hugely exceeds its own LIQUIDITY is a
wash/phantom pool and must be rejected in pool selection — even when its price is
sane and its price-changes are plausible.

Bug (tick-174, 2026-07-04): JTO returned a deep Orca pool with liq $1.75M and
vol_h24 $334.9M — a vol/liq ratio of ~191 (a pool cannot honestly turn over its
entire book 191x in a day; real pools run <5x). Its price ($0.771) matched the real
market and its h6/h24 changes were mild, so BOTH `_dex_price_sane` and
`_pool_chg_plausible` passed it, and it was selected as the deepest pool — feeding a
phantom $1.75M liquidity (0.463% liq/mcap, near the 0.5% gate) for a token whose REAL
Raydium pool is ~$65k (0.017%). Wash volume also poisons any volume-based signal.

Fix: `_pool_vol_sane(pool)` rejects any pool with vol_h24/liq_usd > 15; wired into the
`_build_dex_from_pools` selector so the real deep pool is chosen. A MISSING volume or
liquidity is not evidence of corruption (don't flag it).
"""
from __future__ import annotations

from predictions.fund import stage_phase2 as s2


def test_pool_with_wash_volume_is_not_vol_sane():
    bad = {"liquidity": {"usd": 1_752_854}, "volume": {"h24": 334_905_875}}
    assert s2._pool_vol_sane(bad) is False


def test_pool_with_normal_turnover_is_vol_sane():
    ok = {"liquidity": {"usd": 65_437}, "volume": {"h24": 17_046}}  # ratio ~0.26
    assert s2._pool_vol_sane(ok) is True


def test_pool_missing_volume_or_liq_is_vol_sane():
    assert s2._pool_vol_sane({"liquidity": {"usd": 100000}}) is True   # no volume
    assert s2._pool_vol_sane({"volume": {"h24": 100000}}) is True      # no liq
    assert s2._pool_vol_sane({}) is True


def test_build_dex_rejects_wash_pool_and_picks_real_pool():
    # deep WASH phantom (sane price, plausible chg, but vol/liq ~191) vs real shallow pool
    wash_deep = {
        "priceUsd": "0.7710",
        "liquidity": {"usd": 1_752_854},
        "volume": {"h24": 334_905_875, "h1": 0},
        "priceChange": {"h24": 2.1, "h6": 0.34, "h1": 0.5},
        "txns": {"h24": {"buys": 100, "sells": 100}},
        "dexId": "orca", "pairAddress": "WASH",
    }
    real_shallow = {
        "priceUsd": "0.7699",
        "liquidity": {"usd": 65_437},
        "volume": {"h24": 17_046, "h1": 5},
        "priceChange": {"h24": 2.96, "h6": -0.63, "h1": 0.2},
        "txns": {"h24": {"buys": 40, "sells": 55}},
        "dexId": "raydium", "pairAddress": "REAL",
    }
    out = s2._build_dex_from_pools([wash_deep, real_shallow], ref_price=0.77)
    assert out["pair_addr"] == "REAL"
    assert abs(out["liq_usd"] - 65_437) < 1.0
    assert out.get("chg_corrupt") is not True
