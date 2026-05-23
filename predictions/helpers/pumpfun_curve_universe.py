"""pump.fun bonding-curve universe scraper.

Usage:
    python3 pumpfun_curve_universe.py [--dry-run] [--pages N] [--limit N]

Output JSON to stdout:
    {"data": [...], "fetched_at_unix": int, "pages_fetched": int, "error": null|str}
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402
from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
_TIMEOUT = 15
DEFAULT_PAGES = 5
DEFAULT_LIMIT = 50


def _dry_run_payload() -> dict:
    fixture = config.DRY_RUN_DIR / "pumpfun_curve_universe.dry_run.json"
    return json.loads(fixture.read_text())


def _get(url: str) -> list | dict | None:
    for delay in (0, 1, 3, 9):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", "5")))
                continue
            if r.status_code >= 400:
                return None
            return r.json()
        except Exception:
            continue
    return None


def _live_query(pages: int, limit: int) -> dict:
    base = config.PUMPFUN_CURVE_BASE.rstrip("/")
    rows: list[dict] = []
    pages_fetched = 0
    for page in range(pages):
        # Sort by last_trade_timestamp: returns ACTIVE tokens (recently traded),
        # not just newest-created ones. The created_timestamp sort surfaced
        # mostly-empty just-launched tokens (0% curve, 0 trades, 'MONITORING'-style
        # spam). Active sort surfaces mid-late curve tokens where momentum actually
        # lives. We post-filter to pre-grad below.
        url = (f"{base}/coins?offset={page * limit}&limit={limit}"
               f"&sort=last_trade_timestamp&order=DESC&includeNsfw=false")
        body = _get(url)
        if body is None:
            if pages_fetched == 0:
                return {"data": [], "fetched_at_unix": int(time.time()),
                        "pages_fetched": 0, "error": "pumpfun /coins endpoint failed"}
            break
        if not isinstance(body, list):
            break
        for coin in body:
            if not isinstance(coin, dict):
                continue
            # Skip graduated tokens — universe is meant to be PRE-grad.
            if bool(coin.get("complete")):
                continue
            # bonding_curve_pct: pump.fun graduates a curve at ~85 SOL of real_sol_reserves
            # deposited. The v3 endpoint exposes real_sol_reserves in lamports; there is no
            # standalone bonding_curve_progress field.
            try:
                rsr_lamports = float(coin.get("real_sol_reserves") or 0)
                curve_pct = (rsr_lamports / 1e9) / 85.0 * 100.0
                curve_pct = max(0.0, min(100.0, curve_pct))
            except (TypeError, ValueError):
                curve_pct = 0.0
            # market_cap is reported by pump.fun directly in SOL units (not lamports);
            # market_cap_sol is rarely populated on v3, so prefer market_cap.
            try:
                cap_sol = float(coin.get("market_cap_sol") or coin.get("market_cap") or 0.0)
            except (TypeError, ValueError):
                cap_sol = 0.0
            rows.append({
                "mint": str(coin.get("mint") or ""),
                "bonding_curve_pct": curve_pct,
                "market_cap_sol": cap_sol,
                "creator_wallet": str(coin.get("creator") or ""),
                "created_timestamp_unix": int((coin.get("created_timestamp") or 0) // 1000),
                "reply_count": int(coin.get("reply_count") or 0),
                "recent_trades_count": int(coin.get("recent_trades") or coin.get("buys", 0) + coin.get("sells", 0)),
                "last_trade_timestamp_unix": int((coin.get("last_trade_timestamp") or 0) // 1000),
                "name": str(coin.get("name") or ""),
                "symbol": str(coin.get("symbol") or ""),
                "nsfw": bool(coin.get("nsfw")),
                "is_banned": bool(coin.get("is_banned")),
                "complete": False,
            })
        pages_fetched += 1
        time.sleep(0.5)
    return {"data": rows, "fetched_at_unix": int(time.time()),
            "pages_fetched": pages_fetched, "error": None}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--pages", type=int, default=DEFAULT_PAGES)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = p.parse_args()
    payload = _dry_run_payload() if (args.dry_run or config.is_rehearsal()) else _live_query(args.pages, args.limit)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
