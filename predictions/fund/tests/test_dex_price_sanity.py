"""A dexscreener pool whose price is absurdly far from the CoinGecko reference
must be rejected as a wrong/bridged pool.

Bug (tick-152, 2026-06-19): JUP's dexscreener returned a pool with price $517.05
(real JUP ~$0.19, a ~2700x error), priceChange.h24 -44.75%, 0 buys / 56 sells,
liq/mcap 10.8%. The |chg|>900% filter (test_dexscreener_pool_selection) did NOT
catch it because -44.75% is "plausible" — the corruption was in the PRICE, not the
change. Specialists would have scored JUP on a $517 phantom price.

Fix: `_dex_price_sane(dex_price, ref_price, max_ratio=50)` — at the call site where
the CoinGecko reference close is known, reject a dex block whose price deviates by
more than ~50x (either direction) from the reference.
"""
from __future__ import annotations

from predictions.fund import stage_phase2 as s2


def test_absurd_high_price_is_insane():
    # JUP: dex $517.05 vs CG ref ~$0.19
    assert s2._dex_price_sane(517.049, 0.19) is False


def test_absurd_low_price_is_insane():
    # mirror: dex 50x too LOW vs reference
    assert s2._dex_price_sane(0.001, 1.0) is False


def test_normal_price_is_sane():
    # real pool within a normal band of the CG close
    assert s2._dex_price_sane(0.1881, 0.1880) is True
    assert s2._dex_price_sane(1.72, 1.69) is True


def test_boundary_50x_is_sane_51x_is_not():
    assert s2._dex_price_sane(50.0, 1.0) is True
    assert s2._dex_price_sane(50.5, 1.0) is False
    assert s2._dex_price_sane(1.0, 50.0) is True
    assert s2._dex_price_sane(1.0, 50.5) is False


def test_missing_reference_is_sane_no_false_positive():
    # no CG reference available → cannot judge → do NOT flag (avoid false corruption)
    assert s2._dex_price_sane(517.049, None) is True
    assert s2._dex_price_sane(517.049, 0.0) is True
    assert s2._dex_price_sane(None, 0.19) is True
