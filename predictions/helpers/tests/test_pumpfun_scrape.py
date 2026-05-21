"""Unit tests for predictions.helpers.pumpfun_scrape."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "pumpfun_scrape.py"
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
    for field in (
        "mint", "comment_count", "creator_reply_count",
        "creator_wallet", "creator_prior_launches",
        "recent_trade_count_60min", "endpoints_failed", "fetched_at_unix",
    ):
        assert field in result["data"]


def test_endpoints_failed_is_a_list():
    """The skill uses this to detect partial degradation; must be a list (possibly empty)."""
    result = _run([_TEST_MINT, "--dry-run"])
    assert isinstance(result["data"]["endpoints_failed"], list)


def test_dry_run_echoes_mint():
    result = _run(["DIFFERENT_MINT_xxxxxxx", "--dry-run"])
    assert result["data"]["mint"] == "DIFFERENT_MINT_xxxxxxx"


def test_comment_count_is_non_negative():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["data"]["comment_count"] >= 0
