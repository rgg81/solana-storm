"""stage_phase3 must refuse to build the risk input when the universe
selected-symbols set diverges from the phase2 per_symbol set.

Bug (tick-182, 2026-07-07): the Universe Scout wrote /tmp/smaf_universe.json with
BONK, stage_phase2 fetched BONK's dex/holder data, and the 4 specialists scored
BONK. The Scout THEN revised its selection and re-wrote the universe file with
ANSEM in BONK's slot. stage_phase3 read the later (ANSEM) universe, looked up
ANSEM's specialist scores — found none, so `_get_score` returned 0.0 for all four
axes — and staged ANSEM as an all-zero consensus row while BONK's real (bearish,
exploit-driven) scores were silently dropped. An all-zero row misleads the Risk
Manager (a phantom neutral candidate) and a dropped row hides a real signal.

Root cause upstream: stage_phase2 was run on universe-file-existence rather than
after the Scout's completion notification, so it consumed an intermediate write.
This guard makes stage_phase3 FAIL LOUDLY on the resulting divergence instead of
silently staging garbage, so the operator realigns the universe (or re-runs the
scout→phase2 chain) before any RM/PM decision consumes the corrupted matrix.
"""
from __future__ import annotations

import pytest

from predictions.fund import stage_phase3 as s3


def _uni(tickers):
    return {"selected_symbols": [{"ticker": t} for t in tickers]}


def _p2(tickers):
    return {t: {"dexscreener": {"price_usd": 1.0}} for t in tickers}


SET = ["SPX", "SOL", "PYTH", "JTO", "JUP", "RAY", "BONK", "PUMP", "GRASS", "PENGU", "FARTCOIN", "VIRTUAL"]


def test_matching_sets_do_not_raise():
    # Happy path: universe and phase2 agree exactly → no error.
    s3._assert_universe_matches_scored(_uni(SET), _p2(SET))


def test_symbol_in_universe_but_not_scored_raises():
    # The ANSEM case: universe has a symbol phase2 never fetched → all-zero row.
    universe = _uni([t if t != "BONK" else "ANSEM" for t in SET])  # ANSEM swapped in
    per_sym = _p2(SET)  # phase2/specialists still have BONK, not ANSEM
    with pytest.raises(s3.UniverseDataMismatchError) as ei:
        s3._assert_universe_matches_scored(universe, per_sym)
    msg = str(ei.value)
    assert "ANSEM" in msg  # named as staged-but-unscored
    assert "BONK" in msg   # named as scored-but-dropped


def test_symbol_scored_but_not_in_universe_raises():
    # Symmetric: phase2 has an extra symbol the (shrunken) universe dropped.
    universe = _uni(SET[:-1])          # VIRTUAL removed from universe
    per_sym = _p2(SET)                 # but phase2 scored it
    with pytest.raises(s3.UniverseDataMismatchError) as ei:
        s3._assert_universe_matches_scored(universe, per_sym)
    assert "VIRTUAL" in str(ei.value)


def test_ordering_and_duplicates_do_not_matter():
    # Set-equality, not list-equality: reordering / dup tickers must not trip it.
    universe = _uni(list(reversed(SET)) + ["SPX"])  # reordered + duplicate SPX
    s3._assert_universe_matches_scored(universe, _p2(SET))


def test_error_is_a_runtime_error_subclass():
    # So a broad `except RuntimeError` in the runner still catches it.
    assert issubclass(s3.UniverseDataMismatchError, RuntimeError)
