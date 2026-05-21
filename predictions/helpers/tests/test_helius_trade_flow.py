"""Unit tests for predictions.helpers.helius_trade_flow."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "helius_trade_flow.py"


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


_TEST_MINT = "8y45AJzCXLcdtsv3SaR4G6dkj1KMZH9aZS3uPMhqXuY9"


def test_dry_run_returns_canned_fixture():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["error"] is None
    data = result["data"]
    assert data["mint"] == _TEST_MINT
    for field in (
        "window_minutes", "buy_count", "sell_count",
        "net_sol_lamports", "unique_buyer_count", "buyer_wallets",
        "first_5_buy_timestamps_unix",
    ):
        assert field in data, f"missing field {field}"


def test_window_default_is_60_minutes():
    result = _run([_TEST_MINT, "--dry-run"])
    assert result["data"]["window_minutes"] == 60


def test_window_override_via_flag():
    """--window 30 should be honored in live mode; dry-run echoes 60."""
    result = _run([_TEST_MINT, "--window", "30", "--dry-run"])
    # Dry-run returns the fixture as-is; in real mode 30 would be the value.
    # This test just confirms --window doesn't blow up the arg parser.
    assert "data" in result


def test_buyer_wallets_are_unique():
    """Returned buyer_wallets list should not contain duplicates."""
    result = _run([_TEST_MINT, "--dry-run"])
    wallets = result["data"]["buyer_wallets"]
    assert len(wallets) == len(set(wallets))


def test_first_5_timestamps_are_chronologically_ordered():
    result = _run([_TEST_MINT, "--dry-run"])
    ts = result["data"]["first_5_buy_timestamps_unix"]
    assert ts == sorted(ts)


def test_net_sol_decreases_on_sells():
    """Critical accounting: sells must DECREASE net_sol_lamports, not increase it."""
    import predictions.helpers.helius_trade_flow as hf

    # Two synthetic transactions on the same pool:
    # 1. A buy: signer gains tokens, loses SOL (1 SOL).
    # 2. A sell: signer loses tokens, gains SOL (0.5 SOL).
    # Net SOL inflow should be +0.5 SOL (1 - 0.5), not +1.5 SOL.

    fake_sigs = {"result": [
        {"signature": "sig_buy", "blockTime": 1000},
        {"signature": "sig_sell", "blockTime": 1100},
    ]}

    def make_tx(token_change: int, sol_signer_change: int):
        """Build a getTransaction response with a single mint and signer."""
        return {"result": {
            "meta": {
                "preBalances": [10_000_000_000, 0],   # signer pre-SOL: 10 SOL
                "postBalances": [10_000_000_000 + sol_signer_change, 0],
                "preTokenBalances": [{"accountIndex": 1, "mint": "TESTMINT",
                                       "uiTokenAmount": {"amount": str(max(0, -token_change))}}],
                "postTokenBalances": [{"accountIndex": 1, "mint": "TESTMINT",
                                        "uiTokenAmount": {"amount": str(max(0, token_change))}}],
            },
            "transaction": {"message": {"accountKeys": [
                {"pubkey": "SIGNER_FAKE", "signer": True},
            ]}},
        }}

    # +tokens = buy; -tokens = sell. sol_signer_change is signer's SOL delta.
    tx_buy  = make_tx(token_change=+100, sol_signer_change=-1_000_000_000)  # paid 1 SOL
    tx_sell = make_tx(token_change=-100, sol_signer_change=+500_000_000)    # received 0.5 SOL

    responses = {"sig_buy": tx_buy, "sig_sell": tx_sell}

    def fake_rpc(method, params):
        if method == "getSignaturesForAddress":
            return fake_sigs
        elif method == "getTransaction":
            sig = params[0]
            return responses[sig]
        raise RuntimeError(f"unexpected method: {method}")

    original = hf._rpc_call
    hf._rpc_call = fake_rpc
    try:
        result = hf._live_query("TESTMINT", "POOL_FAKE", window_minutes=60)
    finally:
        hf._rpc_call = original

    assert result["error"] is None
    data = result["data"]
    assert data["buy_count"] == 1
    assert data["sell_count"] == 1
    # Net SOL: +1 SOL from buy - 0.5 SOL from sell = +0.5 SOL = +500_000_000 lamports.
    assert data["net_sol_lamports"] == 500_000_000, (
        f"Expected net inflow of +0.5 SOL (500_000_000 lamports); "
        f"got {data['net_sol_lamports']}. If this is ~1.5 SOL the sign bug is back."
    )
