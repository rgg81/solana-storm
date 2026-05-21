"""pump.fun frontend-api helper: per-mint comments, creator, recent trades.

Usage:
    python3 pumpfun_scrape.py <mint> [--dry-run]

Output JSON to stdout:
    {"data": {...}, "error": null}

Hits three pump.fun frontend-api endpoints in sequence (1s sleep between):
    GET /coins/<mint>             -- coin metadata + creator
    GET /comments/<mint>?limit=100 -- comments + creator replies
    GET /trades/<mint>?limit=200   -- recent trades (filtered to last 60min)
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

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
_TIMEOUT = 15


def _dry_run_payload(mint: str) -> dict:
    fixture = config.DRY_RUN_DIR / "pumpfun_scrape.dry_run.json"
    payload = json.loads(fixture.read_text())
    payload["data"]["mint"] = mint
    return payload


def _get_json(url: str) -> dict | list | None:
    """GET with 3-retry exponential backoff. Returns parsed JSON or None on failure."""
    for delay in [0, 1, 3, 9]:
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "5"))
                time.sleep(retry_after)
                continue
            if r.status_code >= 400:
                return None
            return r.json()
        except Exception:
            continue
    return None


def _live_query(mint: str) -> dict:
    base = config.PUMPFUN_API_BASE.rstrip("/")
    coin = _get_json(f"{base}/coins/{mint}")
    time.sleep(1)
    comments = _get_json(f"{base}/comments/{mint}?limit=100")
    time.sleep(1)
    trades = _get_json(f"{base}/trades/{mint}?limit=200")

    endpoints_failed = []
    if coin is None:
        endpoints_failed.append("coins")
    if comments is None:
        endpoints_failed.append("comments")
    if trades is None:
        endpoints_failed.append("trades")

    if len(endpoints_failed) == 3:
        return {"data": None, "error": "all pumpfun endpoints failed"}

    creator_wallet = ""
    creator_prior_launches = 0
    if isinstance(coin, dict):
        creator_wallet = str(coin.get("creator") or coin.get("creator_wallet") or "")
        creator_prior_launches = int(coin.get("creator_prior_launches") or 0)

    comment_count = 0
    creator_reply_count = 0
    if isinstance(comments, list):
        comment_count = len(comments)
        if creator_wallet:
            creator_reply_count = sum(
                1 for c in comments if str(c.get("user") or "") == creator_wallet
            )

    now_ts = int(time.time())
    cutoff = now_ts - 60 * 60
    recent_trade_count_60min = 0
    if isinstance(trades, list):
        recent_trade_count_60min = sum(
            1 for t in trades if int(t.get("timestamp") or 0) >= cutoff
        )

    return {
        "data": {
            "mint": mint,
            "comment_count": comment_count,
            "creator_reply_count": creator_reply_count,
            "creator_wallet": creator_wallet,
            "creator_prior_launches": creator_prior_launches,
            "recent_trade_count_60min": recent_trade_count_60min,
            "endpoints_failed": endpoints_failed,
            "fetched_at_unix": now_ts,
        },
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mint")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload(args.mint)
    else:
        payload = _live_query(args.mint)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
