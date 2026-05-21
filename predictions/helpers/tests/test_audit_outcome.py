"""Unit tests for predictions.helpers.audit_outcome."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "audit_outcome.py"
_TEST_MINT = "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9"
_TEST_POOL = "5tHRbpyZ3jh6gFhWJZsK1xJ8KqLNQH5kMzPMr8aPK7"


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
    result = _run([_TEST_MINT, "--pool", _TEST_POOL, "--dry-run"])
    assert result["error"] is None
    for field in (
        "mint", "pool_address", "pool_closed",
        "current_base_reserve_lamports", "current_quote_reserve_lamports",
        "current_price", "fetched_at_unix",
    ):
        assert field in result["data"]


def test_current_price_matches_reserve_ratio():
    result = _run([_TEST_MINT, "--pool", _TEST_POOL, "--dry-run"])
    data = result["data"]
    expected = data["current_quote_reserve_lamports"] / data["current_base_reserve_lamports"]
    assert abs(data["current_price"] - expected) < 1e-9


def test_pool_address_is_required():
    """In live mode, --pool is required."""
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, str(HELPER), _TEST_MINT],  # no --pool, no --dry-run
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        return
    payload = json.loads(proc.stdout)
    assert payload["error"] is not None
