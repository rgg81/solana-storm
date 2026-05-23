"""Helius RPC helper: top-10 holder concentration for a mint.

Usage:
    python3 helius_holder_distribution.py <mint> [--dry-run]

Output JSON to stdout:
    {"data": {"mint": ..., "top1_holder_pct": ..., ...}, "error": null}

Hits Helius `getTokenLargestAccounts` (returns up to 20 accounts per mint) and
derives the concentration metrics used by the early_curve specialist:

    top1_holder_pct  > 25  -> concentrated     (C2 SKIP signal in skill spec)
    top10_holder_pct < 40  -> well_distributed (early_curve BUY HIGH gate)

NOTE on `total_supply_estimate`: the JSON-RPC returns only the top-20 token
accounts. The sum of their `uiAmount` will undercount the true mint supply
whenever there is significant dust beyond rank 20. The percentages here are
therefore "share of top-20 supply", not "share of total mint supply" -- but
for high-concentration mints (the case we care about here) the gap is small
and the SKIP/BUY gates remain meaningful.
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

_TIMEOUT = 15

# Concentration thresholds (from skill spec).
_CONCENTRATED_TOP1_PCT = 25.0
_WELL_DISTRIBUTED_TOP10_PCT = 40.0


def _dry_run_payload(mint: str) -> dict:
    fixture = config.DRY_RUN_DIR / "helius_holder_distribution.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["mint"] = mint
    return payload


def _rpc_call(method: str, params: list) -> dict:
    """One JSON-RPC call to Helius with simple retries."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last_err = None
    for delay in [0, 1, 3, 9]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(config.HELIUS_RPC_URL, json=body, timeout=_TIMEOUT)
            if r.status_code == 429:
                last_err = "rate-limited (429), retrying"
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"RPC call {method} failed: {last_err}")


def _live_query(mint: str) -> dict:
    try:
        resp = _rpc_call("getTokenLargestAccounts", [mint])
    except Exception as e:
        return {"data": None, "error": f"getTokenLargestAccounts: {e}"}

    result = resp.get("result") or {}
    accounts_raw = result.get("value") or []
    if not isinstance(accounts_raw, list):
        accounts_raw = []

    # Parse uiAmount; tolerate missing/null.
    parsed = []
    for a in accounts_raw:
        if not isinstance(a, dict):
            continue
        try:
            ui = float(a.get("uiAmount") or 0)
        except (TypeError, ValueError):
            ui = 0.0
        parsed.append({
            "address": str(a.get("address") or ""),
            "ui_amount": ui,
        })

    # Sort descending by ui_amount (RPC already returns sorted, but be defensive).
    parsed.sort(key=lambda x: x["ui_amount"], reverse=True)

    total = sum(a["ui_amount"] for a in parsed)
    holder_count = len(parsed)

    if total <= 0:
        # No holders or all zero -- emit a safe payload.
        return {
            "data": {
                "mint": mint,
                "total_supply_estimate": 0.0,
                "top1_holder_pct": 0.0,
                "top10_holder_pct": 0.0,
                "top20_holder_pct": 0.0,
                "holder_count_in_top20": holder_count,
                "concentrated": False,
                "well_distributed": False,
                "accounts": [],
                "fetched_at_unix": int(time.time()),
            },
            "error": None,
        }

    top1 = parsed[0]["ui_amount"] if parsed else 0.0
    top10_sum = sum(a["ui_amount"] for a in parsed[:10])
    top20_sum = sum(a["ui_amount"] for a in parsed[:20])

    top1_pct = top1 / total * 100.0
    top10_pct = top10_sum / total * 100.0
    top20_pct = top20_sum / total * 100.0

    # First 10 for inspection.
    accounts_out = []
    for a in parsed[:10]:
        pct = a["ui_amount"] / total * 100.0 if total > 0 else 0.0
        accounts_out.append({
            "address": a["address"],
            "ui_amount": a["ui_amount"],
            "pct": pct,
        })

    return {
        "data": {
            "mint": mint,
            "total_supply_estimate": total,
            "top1_holder_pct": top1_pct,
            "top10_holder_pct": top10_pct,
            "top20_holder_pct": top20_pct,
            "holder_count_in_top20": holder_count,
            "concentrated": top1_pct > _CONCENTRATED_TOP1_PCT,
            "well_distributed": top10_pct < _WELL_DISTRIBUTED_TOP10_PCT,
            "accounts": accounts_out,
            "fetched_at_unix": int(time.time()),
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mint", help="Token mint address")
    parser.add_argument("--dry-run", action="store_true",
                        help="Return canned fixture, don't hit Helius")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.mint)
    else:
        payload = _live_query(args.mint)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
