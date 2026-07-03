"""A dexscreener pool whose SHORT-window change is a near-total collapse (|h1|/|h6|
approaching -100%) that is internally inconsistent with a mild h24 must be rejected
in pool selection — even though it sits inside the |chg|<=900% band.

Bug (tick-171, 2026-07-03): JTO returned a deep Orca pool with liq $1.70M, vol_h24
$817M (~17x the CG-aggregated $46.6M and ~481x the pool's own liquidity — wash/phantom
volume) and chg_h6 = -99.98% while chg_h24 was only -6.16% and chg_h1 -0.75%. A -99.98%
6h move cannot coexist with a -6.16% 24h move (the token would have had to be ~5000x
higher 6h ago) — it is a dexscreener feed glitch (the -99.98 sentinel hit ~6 JTO pools
that tick). The 900% band passed it (99.98 < 900), the price ($0.7455) was sane vs the
~$0.747 reference, and it was the deepest pool, so it was selected — feeding specialists
a phantom $1.70M liquidity / $817M volume for a token whose REAL pool is Raydium ~$63k.

Fix: `_pool_chg_plausible` also rejects any pool whose h1 or h6 shows a near-total
collapse (|chg| >= 95%). For our established-token universe (all candidates >$300M mcap)
a >=95% move in <=6h is definitionally corruption, so the real ~$63k pool is selected.
"""
from __future__ import annotations

from predictions.fund import stage_phase2 as s2


def test_pool_near_total_h6_collapse_is_implausible():
    # the JTO tick-171 phantom: h6 -99.98 while h24 only -6.16
    bad = {"priceChange": {"h24": -6.16, "h6": -99.98, "h1": -0.75}}
    assert s2._pool_chg_plausible(bad) is False


def test_pool_near_total_h1_collapse_is_implausible():
    bad = {"priceChange": {"h24": -5.0, "h6": -1.0, "h1": -99.98}}
    assert s2._pool_chg_plausible(bad) is False


def test_pool_mild_h6_change_is_ok():
    # a real JTO pool this tick: h6 -0.16, h24 -5.1 — must stay plausible
    good = {"priceChange": {"h24": -5.1, "h6": -0.16, "h1": -0.2}}
    assert s2._pool_chg_plausible(good) is True


def test_build_dex_rejects_h6_collapse_phantom_and_picks_real_pool():
    # deep phantom (JTO Orca $1.70M, h6 -99.98, $817M vol) vs real Raydium $63k pool
    phantom_deep = {
        "priceUsd": "0.7455",
        "liquidity": {"usd": 1_698_140},
        "volume": {"h24": 817_296_050, "h1": 0},
        "priceChange": {"h24": -6.16, "h6": -99.98, "h1": -0.75},
        "txns": {"h24": {"buys": 100, "sells": 120}},
        "dexId": "orca", "pairAddress": "PHANTOM",
    }
    real_shallow = {
        "priceUsd": "0.7478",
        "liquidity": {"usd": 63_386},
        "volume": {"h24": 12_575, "h1": 5},
        "priceChange": {"h24": -5.1, "h6": -0.16, "h1": -0.2},
        "txns": {"h24": {"buys": 40, "sells": 55}},
        "dexId": "raydium", "pairAddress": "REAL",
    }
    out = s2._build_dex_from_pools([phantom_deep, real_shallow])
    assert out["pair_addr"] == "REAL"
    assert abs(out["price_usd"] - 0.7478) < 1e-6
    assert abs(out["liq_usd"] - 63_386) < 1.0
    assert out.get("chg_corrupt") is not True  # a clean pool was found
