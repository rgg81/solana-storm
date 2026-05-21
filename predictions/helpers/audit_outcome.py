"""Helius RPC helper: current pool state for outcome audit.

Usage:
    python3 audit_outcome.py <mint> --pool <pool_address> [--dry-run]

Output JSON to stdout:
    {"data": {"mint": ..., "pool_closed": false, "current_base_reserve_lamports": ...,
              "current_quote_reserve_lamports": ..., "current_price": ...}, "error": null}

If the pool account is closed / not found, returns pool_closed: true
with reserves = 0 and current_price = 0 -- which the skill interprets
as realized return = -100%.
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


def _live_query(mint: str, pool: str) -> dict:
    try:
        resp = _rpc_call(
            "getAccountInfo",
            [pool, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
    except Exception as e:
        return {"data": None, "error": f"getAccountInfo: {e}"}

    result = (resp or {}).get("result", {})
    value = (result or {}).get("value")
    now = int(time.time())

    if value is None:
        # Pool account closed (rug-and-close).
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

    data = value.get("data")
    base_lamports = 0
    quote_lamports = 0
    if isinstance(data, dict) and data.get("program") == "spl-token":
        parsed = data.get("parsed", {}).get("info", {})
        base_lamports = int((parsed.get("baseReserve") or {}).get("amount") or 0)
        quote_lamports = int((parsed.get("quoteReserve") or {}).get("amount") or 0)

    if base_lamports <= 0:
        # Couldn't parse reserves -- treat as pool_closed for safety.
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

    price = quote_lamports / base_lamports if base_lamports > 0 else 0.0
    return {
        "data": {
            "mint": mint, "pool_address": pool, "pool_closed": False,
            "current_base_reserve_lamports": base_lamports,
            "current_quote_reserve_lamports": quote_lamports,
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
