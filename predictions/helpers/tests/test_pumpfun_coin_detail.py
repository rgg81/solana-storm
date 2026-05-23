"""Unit tests for predictions.helpers.pumpfun_coin_detail."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "pumpfun_coin_detail.py"
_TEST_MINT = "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9"


def _run(args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_dry_run_returns_canned_fixture():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["error"] is None
    data = result["data"]
    for field in (
        "mint", "symbol", "name", "creator_wallet",
        "bonding_curve_pct", "complete",
        "virtual_sol_reserves_lamports", "virtual_token_reserves",
        "real_sol_reserves_lamports", "real_token_reserves",
        "total_supply", "curve_price_sol_per_token",
        "market_cap_sol", "market_cap_usd", "ath_market_cap_usd",
        "ath_mc_ratio", "reply_count",
        "last_trade_timestamp_unix", "last_trade_age_sec",
        "socials", "nsfw", "is_banned", "fetched_at_unix",
    ):
        assert field in data, f"missing field {field}"


def test_dry_run_echoes_mint():
    result = _run(["DIFFERENT_MINT_xxxxxxx", "--dry-run"])
    assert result["data"]["mint"] == "DIFFERENT_MINT_xxxxxxx"


def test_bonding_curve_pct_in_range():
    result = _run([_TEST_MINT, "--dry-run"])
    pct = result["data"]["bonding_curve_pct"]
    assert 0.0 <= pct <= 100.0, f"bonding_curve_pct out of range: {pct}"


def test_socials_has_expected_keys():
    result = _run([_TEST_MINT, "--dry-run"])
    socials = result["data"]["socials"]
    assert isinstance(socials, dict)
    for k in ("telegram", "twitter", "website"):
        assert k in socials, f"socials missing key {k}"


def test_ath_mc_ratio_is_nonneg():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["data"]["ath_mc_ratio"] >= 0.0


def test_complete_is_bool():
    result = _run([_TEST_MINT, "--dry-run"])
    assert isinstance(result["data"]["complete"], bool)
