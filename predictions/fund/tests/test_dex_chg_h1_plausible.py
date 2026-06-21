"""A dexscreener pool whose SHORT-window change (h1/h6) is impossible must be
rejected in pool selection, even when its h24 change looks plausible.

Bug (tick-155, 2026-06-21): JUP and PUMP each returned a deep wrong/bridged pool
with a PLAUSIBLE chg_h24 (+10.29% / +9.79%) but an absurd chg_h1 (~491,186% /
~490,548%) and a price ~5000x off (JUP $1099 vs ~$0.22, PUMP $7.38 vs ~$0.0015).
The h24-only `_chg_plausible` filter selected it as the deepest "plausible" pool;
`_dex_price_sane` couldn't catch it because CG OHLC was insufficient that tick so
there was no reference close. Specialists would have scored $1099 phantom JUP.

Fix: `_pool_chg_plausible(pool)` requires h1 AND h6 AND h24 all within the |chg|<=900%
band, so the corrupt pool is rejected in selection and the real pool is chosen.
"""
from __future__ import annotations

from predictions.fund import stage_phase2 as s2


def test_pool_with_absurd_h1_is_implausible():
    bad = {"priceChange": {"h24": 10.29, "h6": 50.0, "h1": 491186.0}}
    assert s2._pool_chg_plausible(bad) is False


def test_pool_with_absurd_h6_is_implausible():
    bad = {"priceChange": {"h24": 5.0, "h6": 99999.0, "h1": 2.0}}
    assert s2._pool_chg_plausible(bad) is False


def test_pool_with_all_plausible_changes_is_ok():
    good = {"priceChange": {"h24": 10.29, "h6": 4.0, "h1": 1.5}}
    assert s2._pool_chg_plausible(good) is True


def test_pool_missing_change_fields_is_ok():
    # absent short-window fields must NOT trigger a false corruption flag
    assert s2._pool_chg_plausible({"priceChange": {"h24": 3.0}}) is True
    assert s2._pool_chg_plausible({"priceChange": {}}) is True
    assert s2._pool_chg_plausible({}) is True


def test_build_dex_rejects_absurd_h1_pool_and_picks_real_pool():
    # the DEEP pool is corrupt (absurd h1, phantom price); the shallow pool is real
    corrupt_deep = {
        "priceUsd": "1099.18",
        "liquidity": {"usd": 111_380_000},
        "volume": {"h24": 1, "h1": 1},
        "priceChange": {"h24": 10.29, "h6": 50.0, "h1": 491186.0},
        "txns": {"h24": {"buys": 559, "sells": 692}},
        "dexId": "bridged", "pairAddress": "CORRUPT",
    }
    real_shallow = {
        "priceUsd": "0.2204",
        "liquidity": {"usd": 840_000},
        "volume": {"h24": 1000, "h1": 50},
        "priceChange": {"h24": 14.1, "h6": 6.0, "h1": 1.0},
        "txns": {"h24": {"buys": 3632, "sells": 3792}},
        "dexId": "orca", "pairAddress": "REAL",
    }
    out = s2._build_dex_from_pools([corrupt_deep, real_shallow])
    assert out["pair_addr"] == "REAL"
    assert abs(out["price_usd"] - 0.2204) < 1e-6
    assert out.get("chg_corrupt") is not True  # a clean pool was found


def test_build_dex_all_pools_absurd_falls_back_corrupt():
    # if EVERY pool is implausible, keep the deepest but flag chg_corrupt
    only_corrupt = {
        "priceUsd": "1099.18",
        "liquidity": {"usd": 111_380_000},
        "volume": {"h24": 1, "h1": 1},
        "priceChange": {"h24": 10.29, "h6": 50.0, "h1": 491186.0},
        "txns": {"h24": {"buys": 1, "sells": 1}},
        "dexId": "bridged", "pairAddress": "CORRUPT",
    }
    out = s2._build_dex_from_pools([only_corrupt])
    assert out["chg_corrupt"] is True
    assert out["chg_h1"] is None
