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


def test_live_query_uses_token_vault_lookups():
    """Audit should fetch base + quote vaults via getTokenAccountsByOwner."""
    import predictions.helpers.audit_outcome as ao

    rpc_calls = []

    def fake_rpc(method, params):
        rpc_calls.append((method, params))
        # Method must be getTokenAccountsByOwner.
        assert method == "getTokenAccountsByOwner", f"unexpected method: {method}"
        # params[0] is the owner (pool); params[1] is the filter dict with mint.
        mint_filter = params[1].get("mint")
        # Return a synthetic spl-token account with a known balance.
        # base call: mint == TESTMINT -> 600_000_000_000_000 raw units
        # quote call: mint == WSOL -> 150_000_000_000 lamports
        if mint_filter == "TESTMINT":
            amount = "600000000000000"
        elif mint_filter == ao.WSOL_MINT:
            amount = "150000000000"
        else:
            return {"result": {"value": []}}
        return {"result": {"value": [
            {"account": {"data": {"parsed": {"info": {
                "tokenAmount": {"amount": amount, "decimals": 6, "uiAmountString": "0"}
            }}}}}
        ]}}

    original = ao._rpc_call
    ao._rpc_call = fake_rpc
    try:
        result = ao._live_query("TESTMINT", "POOL_FAKE")
    finally:
        ao._rpc_call = original

    assert result["error"] is None
    data = result["data"]
    assert data["pool_closed"] is False
    assert data["current_base_reserve_lamports"] == 600_000_000_000_000
    assert data["current_quote_reserve_lamports"] == 150_000_000_000
    expected_price = 150_000_000_000 / 600_000_000_000_000
    assert abs(data["current_price"] - expected_price) < 1e-15
    # Exactly two RPC calls -- one per vault.
    assert len(rpc_calls) == 2
    methods = {c[0] for c in rpc_calls}
    assert methods == {"getTokenAccountsByOwner"}


def test_live_query_returns_pool_closed_when_vault_empty():
    """If either vault returns no accounts, the pool is treated as closed."""
    import predictions.helpers.audit_outcome as ao

    def fake_rpc(method, params):
        # Always return empty value (vault closed).
        return {"result": {"value": []}}

    original = ao._rpc_call
    ao._rpc_call = fake_rpc
    try:
        result = ao._live_query("TESTMINT", "POOL_FAKE")
    finally:
        ao._rpc_call = original

    assert result["error"] is None
    assert result["data"]["pool_closed"] is True
    assert result["data"]["current_base_reserve_lamports"] == 0
