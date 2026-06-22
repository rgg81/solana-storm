"""When CoinGecko OHLC is unavailable (latest_close_usd is None), the price-sanity
guard must fall back to the token's LAST KNOWN price from universe_price_history.jsonl
so a wrong/bridged pool is still rejected.

Bug (tick-158, 2026-06-22): PYTH and JUP each returned a wrong pool with a PLAUSIBLE
chg_h24/h6/h1 (so _pool_chg_plausible passed) but a price ~5000x off (PYTH $188.77 vs
~$0.036, JUP $1038.72 vs ~$0.21). _dex_price_sane could not catch them because CG OHLC
was insufficient that tick (latest_close_usd=None) so there was no reference close, and
the universe candidate carries no price field. The prior-tick price in
universe_price_history.jsonl IS always available and is the robust fallback reference.

Fix: `_last_prices_from_history(path)` returns {symbol: latest price_usd}; the staging
loop uses `latest_close_usd or last_prices[ticker]` as the _dex_price_sane reference.
"""
from __future__ import annotations

from predictions.fund import stage_phase2 as s2


def test_last_prices_picks_latest_per_symbol(tmp_path):
    hist = tmp_path / "universe_price_history.jsonl"
    hist.write_text(
        '{"tick_id": 90, "symbol": "PYTH", "price_usd": 0.040}\n'
        '{"tick_id": 91, "symbol": "JUP", "price_usd": 0.22}\n'
        '{"tick_id": 92, "symbol": "PYTH", "price_usd": 0.036}\n'   # later PYTH wins
        '\n'                                                          # blank line tolerated
        '{"tick_id": 93, "symbol": "BONK", "price_usd": 4.6e-06}\n'
    )
    out = s2._last_prices_from_history(str(hist))
    assert out["PYTH"] == 0.036   # latest, not 0.040
    assert out["JUP"] == 0.22
    assert out["BONK"] == 4.6e-06


def test_last_prices_missing_file_returns_empty(tmp_path):
    out = s2._last_prices_from_history(str(tmp_path / "does_not_exist.jsonl"))
    assert out == {}


def test_last_prices_skips_malformed_and_priceless_rows(tmp_path):
    hist = tmp_path / "h.jsonl"
    hist.write_text(
        '{"tick_id": 1, "symbol": "AAA", "price_usd": 1.0}\n'
        'not json at all\n'
        '{"tick_id": 2, "symbol": "BBB"}\n'          # no price_usd
        '{"tick_id": 3, "symbol": "CCC", "price_usd": null}\n'
    )
    out = s2._last_prices_from_history(str(hist))
    assert out == {"AAA": 1.0}


def test_history_ref_catches_corrupt_when_cg_ohlc_missing():
    # the integration the bug needed: dex $188.77 vs prior-tick $0.036 (CG ref None)
    ref = None or 0.036  # latest_close_usd is None -> fall back to history price
    assert s2._dex_price_sane(188.77, ref) is False
    assert s2._dex_price_sane(1038.72, 0.2143) is False  # JUP
    # a real small move stays sane against the prior price
    assert s2._dex_price_sane(0.0372, 0.036) is True


def test_build_dex_recovers_real_pool_via_price_ref():
    # PYTH-style: deep wrong pool ($188.77) + real shallow pool ($0.0363); ref=prior price
    corrupt_deep = {
        "priceUsd": "188.77",
        "liquidity": {"usd": 218_000_000},
        "volume": {"h24": 1, "h1": 1},
        "priceChange": {"h24": 1.32, "h6": 18.93, "h1": 2.54},  # all plausible!
        "txns": {"h24": {"buys": 5, "sells": 5}},
        "dexId": "bridged", "pairAddress": "CORRUPT",
    }
    real_shallow = {
        "priceUsd": "0.0363",
        "liquidity": {"usd": 260_000},
        "volume": {"h24": 1000, "h1": 50},
        "priceChange": {"h24": 3.22, "h6": 1.0, "h1": 3.15},
        "txns": {"h24": {"buys": 366, "sells": 642}},
        "dexId": "raydium", "pairAddress": "REAL",
    }
    # without a reference, the deep corrupt pool wins (plausible chg, deepest liq)
    assert s2._build_dex_from_pools([corrupt_deep, real_shallow])["pair_addr"] == "CORRUPT"
    # with the prior-tick price as reference, the real pool is recovered
    out = s2._build_dex_from_pools([corrupt_deep, real_shallow], ref_price=0.0363)
    assert out["pair_addr"] == "REAL"
    assert abs(out["price_usd"] - 0.0363) < 1e-9
