"""Unit tests for predictions.helpers.recent_graduations."""

import json
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "recent_graduations.py"


def _run(args, env_extra=None):
    """Run helper as subprocess; return parsed JSON."""
    import os
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
    """--dry-run flag returns the committed fixture without touching the network."""
    result = _run(["--dry-run"])
    assert result["error"] is None
    assert isinstance(result["data"], list)
    assert len(result["data"]) >= 1
    row = result["data"][0]
    for field in (
        "mint", "pool_address", "graduation_time_unix",
        "deployer_wallet", "deployer_prior_launches", "deployer_age_secs",
        "liq_quote_reserve_lamports", "liq_base_reserve_lamports",
        "curve_real_sol_reserves_lamports", "curve_completion_time_secs",
    ):
        assert field in row, f"missing field {field}"


def test_dry_run_via_env_flag():
    """PUMP_PREDICTION_REHEARSAL=1 forces dry-run even without --dry-run."""
    result = _run([], env_extra={"PUMP_PREDICTION_REHEARSAL": "1"})
    assert result["error"] is None
    assert len(result["data"]) >= 1


def test_output_includes_specific_fixture_token():
    """Sanity: the dry-run fixture must contain the known test mint."""
    result = _run(["--dry-run"])
    mints = {row["mint"] for row in result["data"]}
    assert "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9" in mints
