"""Helius RPC helper: current pool state for outcome audit.

Usage:
    python3 audit_outcome.py <mint> --pool <pool_address> [--dry-run]

Output JSON to stdout:
    {"data": {"mint": ..., "pool_closed": false, "current_base_reserve_lamports": ...,
              "current_quote_reserve_lamports": ..., "current_price": ...}, "error": null}

Strategy (post-fix): instead of parsing the PumpSwap pool account's custom
binary layout, fetch the pool's two SPL-token vault accounts directly via
`getTokenAccountsByOwner`. The vault accounts ARE spl-token accounts and
parse cleanly with `encoding=jsonParsed`. This sidesteps the PumpSwap IDL
deserialization problem entirely.

If either vault lookup returns no accounts (rug-and-close: deployer
withdrew + closed the vaults), `pool_closed: true` with zero reserves --
which the skill interprets as realized return = -100%.
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

# Wrapped SOL mint -- PumpSwap pools use this as the quote-side mint.
WSOL_MINT = "So11111111111111111111111111111111111111112"


def _dry_run_payload(mint: str, pool: str) -> dict:
    fixture = config.DRY_RUN_DIR / "audit_outcome.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["mint"] = mint
    payload["data"]["pool_address"] = pool
    return payload


def _rpc_call(method: str, params: list) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last_err = None
    for delay in [0, 1, 3, 9]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(config.HELIUS_RPC_URL, json=body, timeout=15)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "5")))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"RPC {method} failed: {last_err}")


def _vault_balance_lamports(pool: str, mint: str) -> int | None:
    """Look up the SPL-token vault owned by `pool` for `mint`; return amount in raw units.

    Returns None if no vault account exists (account was closed / never existed)
    or if the parsed response shape is unexpected.
    """
    try:
        resp = _rpc_call(
            "getTokenAccountsByOwner",
            [pool, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
    except Exception:
        return None
    value = ((resp or {}).get("result") or {}).get("value")
    if not isinstance(value, list) or not value:
        return None
    # If there are multiple matching accounts (unusual), sum them.
    total = 0
    for acct in value:
        try:
            amount = (
                acct.get("account", {})
                    .get("data", {})
                    .get("parsed", {})
                    .get("info", {})
                    .get("tokenAmount", {})
                    .get("amount")
            )
            total += int(amount or 0)
        except Exception:
            continue
    return total


def _live_query(mint: str, pool: str) -> dict:
    now = int(time.time())

    base_reserve = _vault_balance_lamports(pool, mint)
    quote_reserve = _vault_balance_lamports(pool, WSOL_MINT)

    # If either vault returned None (RPC failure) OR zero reserves, treat as closed.
    if (base_reserve is None or quote_reserve is None
            or base_reserve <= 0 or quote_reserve <= 0):
        return {
            "data": {
                "mint": mint, "pool_address": pool, "pool_closed": True,
                "current_base_reserve_lamports": 0,
                "current_quote_reserve_lamports": 0,
                "current_price": 0.0,
                "fetched_at_unix": now,
            },
            "error": None,
        }

    price = quote_reserve / base_reserve
    return {
        "data": {
            "mint": mint, "pool_address": pool, "pool_closed": False,
            "current_base_reserve_lamports": base_reserve,
            "current_quote_reserve_lamports": quote_reserve,
            "current_price": price,
            "fetched_at_unix": now,
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mint")
    parser.add_argument("--pool", default=None, required=False)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.mint, args.pool or "DRY_RUN_POOL")
    else:
        if not args.pool:
            payload = {"data": None, "error": "--pool required in live mode"}
        else:
            payload = _live_query(args.mint, args.pool)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
