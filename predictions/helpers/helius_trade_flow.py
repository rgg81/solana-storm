"""Helius RPC helper: first-hour trade flow on a single mint's PumpSwap pool.

Usage:
    python3 helius_trade_flow.py <mint> --pool <pool> [--window MINUTES] [--dry-run]

Output JSON to stdout:
    {"data": {"mint": ..., "buy_count": N, ...}, "error": null}

Strategy:
1. Use --pool from the caller (Dune row provides it).
2. getSignaturesForAddress(pool, limit=200) -- recent transactions on the pool.
3. For each signature in chronological order within the time window:
   - getTransaction(sig, jsonParsed=True)
   - Inspect tokenBalances pre/post to determine buy vs sell direction.
4. Aggregate counts, unique buyers, net SOL.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402

from predictions import config  # noqa: E402


def _dry_run_payload(mint: str) -> dict:
    fixture = config.DRY_RUN_DIR / "helius_trade_flow.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["mint"] = mint
    return payload


def _rpc_call(method: str, params: list) -> dict:
    """One JSON-RPC call to Helius with simple retries."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    backoffs = [1, 3, 9]
    last_err = None
    for delay in [0, *backoffs]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(config.HELIUS_RPC_URL, json=body, timeout=15)
            if r.status_code == 429:
                last_err = "rate-limited (429), retrying"
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"RPC call {method} failed: {last_err}")


def _live_query(mint: str, pool: str | None, window_minutes: int) -> dict:
    if not pool:
        return {"data": None, "error": "pool address required (--pool)"}
    try:
        sigs_resp = _rpc_call(
            "getSignaturesForAddress",
            [pool, {"limit": 200}],
        )
    except Exception as e:
        return {"data": None, "error": f"getSignaturesForAddress: {e}"}

    sigs = (sigs_resp.get("result") or [])
    # Sort chronologically ascending.
    sigs = sorted(sigs, key=lambda s: int(s.get("blockTime") or 0))
    if not sigs:
        return {"data": {"mint": mint, "window_minutes": window_minutes,
                         "buy_count": 0, "sell_count": 0,
                         "net_sol_lamports": 0, "unique_buyer_count": 0,
                         "buyer_wallets": [], "first_5_buy_timestamps_unix": []},
                "error": None}

    pool_open_time = int(sigs[0].get("blockTime") or 0)
    window_end = pool_open_time + window_minutes * 60

    buy_count = 0
    sell_count = 0
    net_sol = 0
    buyer_wallets: list[str] = []
    seen_buyers: set[str] = set()
    first_buy_ts: list[int] = []

    for s in sigs:
        bt = int(s.get("blockTime") or 0)
        if bt > window_end:
            break
        try:
            tx_resp = _rpc_call(
                "getTransaction",
                [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
        except Exception:
            continue
        tx = tx_resp.get("result")
        if not tx:
            continue
        meta = tx.get("meta") or {}
        pre = {b["accountIndex"]: b for b in meta.get("preTokenBalances") or []
               if b.get("mint") == mint}
        post = {b["accountIndex"]: b for b in meta.get("postTokenBalances") or []
                if b.get("mint") == mint}
        msg = tx.get("transaction", {}).get("message", {})
        account_keys = msg.get("accountKeys", [])
        signers = []
        if account_keys and isinstance(account_keys[0], dict):
            signers = [str(k.get("pubkey")) for k in account_keys if k.get("signer")]
        elif account_keys:
            signers = [str(account_keys[0])]
        if not signers:
            continue
        signer = signers[0]
        net_change = 0
        for idx, p in post.items():
            try:
                p_amt = int((p.get("uiTokenAmount") or {}).get("amount") or 0)
            except Exception:
                p_amt = 0
            pr_amt = 0
            if idx in pre:
                try:
                    pr_amt = int((pre[idx].get("uiTokenAmount") or {}).get("amount") or 0)
                except Exception:
                    pr_amt = 0
            net_change += p_amt - pr_amt
        if net_change > 0:
            buy_count += 1
            if signer not in seen_buyers:
                seen_buyers.add(signer)
                buyer_wallets.append(signer)
            if len(first_buy_ts) < 5:
                first_buy_ts.append(bt)
            sol_in = (meta.get("preBalances") or [0])[0] - (meta.get("postBalances") or [0])[0]
            net_sol += sol_in
        elif net_change < 0:
            sell_count += 1
            sol_out = (meta.get("postBalances") or [0])[0] - (meta.get("preBalances") or [0])[0]
            net_sol -= sol_out

    return {
        "data": {
            "mint": mint,
            "window_minutes": window_minutes,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "net_sol_lamports": net_sol,
            "unique_buyer_count": len(seen_buyers),
            "buyer_wallets": buyer_wallets[:50],
            "first_5_buy_timestamps_unix": first_buy_ts,
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mint", help="Token mint address")
    parser.add_argument("--pool", default=None,
                        help="PumpSwap pool address (skill passes from Dune row)")
    parser.add_argument("--window", type=int, default=60,
                        help="Window in minutes from first transaction")
    parser.add_argument("--dry-run", action="store_true",
                        help="Return canned fixture, don't hit Helius")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.mint)
    else:
        payload = _live_query(args.mint, args.pool, args.window)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
