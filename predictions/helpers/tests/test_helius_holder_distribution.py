"""Unit tests for predictions.helpers.helius_holder_distribution."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "helius_holder_distribution.py"
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
        "mint", "total_supply_estimate",
        "top1_holder_pct", "top10_holder_pct", "top20_holder_pct",
        "holder_count_in_top20", "concentrated", "well_distributed",
        "accounts", "fetched_at_unix",
    ):
        assert field in data, f"missing field {field}"


def test_dry_run_echoes_mint():
    result = _run(["DIFFERENT_MINT_xxxxxxx", "--dry-run"])
    assert result["data"]["mint"] == "DIFFERENT_MINT_xxxxxxx"


def test_dry_run_fixture_is_well_distributed():
    """Fixture must encode concentrated=false, well_distributed=true per spec."""
    result = _run([_TEST_MINT, "--dry-run"])
    data = result["data"]
    assert data["concentrated"] is False
    assert data["well_distributed"] is True


def test_pct_fields_are_bounded():
    result = _run([_TEST_MINT, "--dry-run"])
    data = result["data"]
    for k in ("top1_holder_pct", "top10_holder_pct", "top20_holder_pct"):
        v = data[k]
        assert 0.0 <= v <= 100.0, f"{k} out of range: {v}"


def test_accounts_list_is_top10_max():
    """`accounts` is for inspection -- spec says first 10 entries."""
    result = _run([_TEST_MINT, "--dry-run"])
    accounts = result["data"]["accounts"]
    assert isinstance(accounts, list)
    assert len(accounts) <= 10
    for a in accounts:
        for field in ("address", "ui_amount", "pct"):
            assert field in a, f"account missing field {field}"


def test_concentrated_flag_logic():
    """Live-query logic: top1 > 25% -> concentrated=True."""
    import predictions.helpers.helius_holder_distribution as hd

    # Synthetic: one account holds 30% of top-20 supply.
    fake_resp = {"result": {"value": [
        {"address": "WHALE", "uiAmount": 300.0},
        {"address": "B", "uiAmount": 200.0},
        {"address": "C", "uiAmount": 150.0},
        {"address": "D", "uiAmount": 100.0},
        {"address": "E", "uiAmount": 80.0},
        {"address": "F", "uiAmount": 70.0},
        {"address": "G", "uiAmount": 50.0},
        {"address": "H", "uiAmount": 30.0},
        {"address": "I", "uiAmount": 10.0},
        {"address": "J", "uiAmount": 10.0},
    ]}}

    def fake_rpc(method, params):
        assert method == "getTokenLargestAccounts"
        return fake_resp

    original = hd._rpc_call
    hd._rpc_call = fake_rpc
    try:
        result = hd._live_query("TESTMINT")
    finally:
        hd._rpc_call = original

    assert result["error"] is None
    data = result["data"]
    # Total = 1000; top1 = 300 = 30%; concentrated should be True.
    assert abs(data["top1_holder_pct"] - 30.0) < 0.01
    assert data["concentrated"] is True
    # Top10 sum = 1000 = 100% -> well_distributed = False.
    assert data["well_distributed"] is False


def test_empty_response_does_not_crash():
    """If getTokenLargestAccounts returns no holders, emit zero-valued payload."""
    import predictions.helpers.helius_holder_distribution as hd

    def fake_rpc(method, params):
        return {"result": {"value": []}}

    original = hd._rpc_call
    hd._rpc_call = fake_rpc
    try:
        result = hd._live_query("TESTMINT")
    finally:
        hd._rpc_call = original

    assert result["error"] is None
    assert result["data"]["total_supply_estimate"] == 0.0
    assert result["data"]["top1_holder_pct"] == 0.0
    assert result["data"]["holder_count_in_top20"] == 0
