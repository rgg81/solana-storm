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
    """Hit pump.fun's v3 frontend-api `/coins/<mint>` endpoint.

    As of 2026-05-21, the older `/comments/<mint>` and `/trades/<mint>` paths
    were retired (404 on v3). The `/coins/` payload now carries enough data
    that we don't need them: `reply_count` replaces our comment count, and
    `helius_trade_flow.py` provides the actual buy/sell flow.

    The v3 payload also exposes the token's `symbol` (ticker) -- the skill's
    Phase 2 uses this to drive `telegram_chatter.py` instead of the mint
    prefix, plus `ath_market_cap` (already-pumped detector) and
    `last_trade_timestamp` (liveness).
    """
    base = config.PUMPFUN_API_BASE.rstrip("/")
    coin = _get_json(f"{base}/coins/{mint}")

    endpoints_failed = []
    if coin is None:
        endpoints_failed.append("coins")
        return {"data": None, "error": "pumpfun /coins/ endpoint failed"}
    # The /comments/ and /trades/ paths are permanently retired on v3; we
    # don't call them but flag them as not-provided so the skill knows.
    endpoints_failed.extend(["comments", "trades"])

    creator_wallet = ""
    symbol = ""
    name = ""
    market_cap_usd = 0.0
    ath_market_cap_usd = 0.0
    ath_market_cap_unix = 0
    last_trade_unix = 0
    reply_count = 0
    creator_telegram = ""
    nsfw = False
    is_banned = False

    if isinstance(coin, dict):
        creator_wallet = str(coin.get("creator") or "")
        symbol = str(coin.get("symbol") or "")
        name = str(coin.get("name") or "")
        try:
            market_cap_usd = float(coin.get("market_cap") or 0)
        except (TypeError, ValueError):
            market_cap_usd = 0.0
        try:
            ath_market_cap_usd = float(coin.get("ath_market_cap") or 0)
        except (TypeError, ValueError):
            ath_market_cap_usd = 0.0
        # ath_market_cap_timestamp and last_trade_timestamp are msec since
        # epoch in v3; convert to seconds.
        try:
            ath_market_cap_unix = int((coin.get("ath_market_cap_timestamp") or 0) // 1000)
        except (TypeError, ValueError):
            ath_market_cap_unix = 0
        try:
            last_trade_unix = int((coin.get("last_trade_timestamp") or 0) // 1000)
        except (TypeError, ValueError):
            last_trade_unix = 0
        try:
            reply_count = int(coin.get("reply_count") or 0)
        except (TypeError, ValueError):
            reply_count = 0
        creator_telegram = str(coin.get("telegram") or "")
        nsfw = bool(coin.get("nsfw"))
        is_banned = bool(coin.get("is_banned"))

    return {
        "data": {
            "mint": mint,
            # legacy fields (kept for skill compatibility; semantics adapted to v3 source)
            "comment_count": reply_count,
            "creator_reply_count": 0,           # /comments/ retired; can't compute
            "creator_wallet": creator_wallet,
            "creator_prior_launches": 0,         # not in /coins/ payload
            "recent_trade_count_60min": 0,       # /trades/ retired; helius_trade_flow has this
            "endpoints_failed": endpoints_failed,
            "fetched_at_unix": int(time.time()),
            # v3 enrichment fields (new -- skill can use these directly)
            "symbol": symbol,                    # the ticker -- enables telegram_chatter
            "name": name,
            "market_cap_usd": market_cap_usd,
            "ath_market_cap_usd": ath_market_cap_usd,
            "ath_market_cap_unix": ath_market_cap_unix,
            "last_trade_unix": last_trade_unix,
            "creator_telegram": creator_telegram,
            "nsfw": nsfw,
            "is_banned": is_banned,
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
