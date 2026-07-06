"""Universe names that CoinGecko's top-Solana list does not return a cg_id for
must still resolve to their real Solana mint via a ticker-keyed override.

Bug (tick-178, 2026-07-06): FARTCOIN (added to the universe tick-177) and SPX both
staged as NO_DATA. Root cause: `cgid = {t["ticker"]: t["cg_id"] for t in tops}` is
built ONLY from CoinGecko's top-Solana list, which does not contain SPX or FARTCOIN,
so `cgid.get(ticker)` is None → `fetch_mint(None)` is skipped → mint None → the
DexScreener/Helius fetches never run. SPX has been chronically NO_DATA (patched by
hand each tick); FARTCOIN broke the same way the tick it became a probe-gate watch.

Fix: `resolve_mint(ticker, cg_id)` checks `TICKER_MINT_OVERRIDES` FIRST (verified real
Raydium pools, NOT boosted-list decoys), then falls back to the cg_id path. So these
names resolve every tick regardless of the top-Solana list.
"""
from __future__ import annotations

from predictions.fund import stage_phase2 as s2


def test_overrides_present_for_spx_and_fartcoin():
    assert s2.TICKER_MINT_OVERRIDES.get("SPX") == "J3NKxxXZcnNiMjKw9hYb2K4LUxgwB6t1FtPtQVsv3KFr"
    assert s2.TICKER_MINT_OVERRIDES.get("FARTCOIN") == "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"


def test_resolve_mint_prefers_override_even_with_a_cg_id():
    # Override must win even when a (possibly wrong) cg_id is supplied.
    assert s2.resolve_mint("SPX", "spx6900") == "J3NKxxXZcnNiMjKw9hYb2K4LUxgwB6t1FtPtQVsv3KFr"
    assert s2.resolve_mint("FARTCOIN", None) == "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"


def test_resolve_mint_none_cg_id_for_unknown_symbol_is_none():
    # A symbol with no override and no cg_id must resolve to None (not crash) —
    # this is exactly the SPX/FARTCOIN pre-fix failure mode for any other such name.
    assert s2.resolve_mint("SOME_NEW_TICKER", None) is None


def test_resolve_mint_uses_known_mints_via_cg_id():
    # Non-overridden names still resolve through the cg_id → KNOWN_MINTS path.
    assert s2.resolve_mint("PUMP", "pump-fun") == s2.KNOWN_MINTS["pump-fun"]


def test_override_mints_look_like_solana_addresses():
    # base58, 32-44 chars — a cheap guard against a truncated/typo'd paste.
    import re
    b58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
    for tkr, mint in s2.TICKER_MINT_OVERRIDES.items():
        assert b58.match(mint), f"{tkr} mint not base58-shaped: {mint}"
