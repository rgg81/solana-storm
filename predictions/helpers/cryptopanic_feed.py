"""CryptoPanic feed helper. Free tier: 1000 req/day.

Usage:
    python3 cryptopanic_feed.py --tickers STORM,PEPE [--filter hot] [--dry-run]
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402
from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
_TIMEOUT = 15
CACHE_TTL_SEC = 600  # 10 min


def _dry_run_payload() -> dict:
    fixture = config.DRY_RUN_DIR / "cryptopanic_feed.dry_run.json"
    return json.loads(fixture.read_text())


def _cache_path(tickers: list[str], filter_: str) -> Path:
    cache_dir = config.CACHE_DIR / "cryptopanic"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = "_".join(sorted(tickers)) + f"_{filter_}"
    return cache_dir / f"{key}.json"


def _read_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > CACHE_TTL_SEC:
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_cache(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.rename(path)


def _live_query(tickers: list[str], filter_: str) -> dict:
    if not config.CRYPTOPANIC_API_TOKEN:
        return {"data": None, "error": "CRYPTOPANIC_API_TOKEN missing"}

    cache_path = _cache_path(tickers, filter_)
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    currencies = ",".join(t.upper() for t in tickers)
    url = (f"{config.CRYPTOPANIC_BASE}/posts/?auth_token={config.CRYPTOPANIC_API_TOKEN}"
           f"&currencies={currencies}&filter={filter_}&public=true")
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code >= 400:
            return {"data": None, "error": f"cryptopanic http {r.status_code}"}
        body = r.json()
    except Exception as e:
        return {"data": None, "error": f"cryptopanic error: {e}"}

    posts = []
    for p in (body.get("results") or []):
        try:
            pub = datetime.fromisoformat(p.get("published_at", "").replace("Z", "+00:00"))
            published_unix = int(pub.timestamp())
        except Exception:
            published_unix = 0
        posts.append({
            "title": p.get("title") or "",
            "source_domain": ((p.get("source") or {}).get("domain")) or "",
            "published_at_unix": published_unix,
            "votes": p.get("votes") or {},
            "currencies_tagged": [c.get("code", "") for c in (p.get("currencies") or [])],
            "url": p.get("url") or "",
        })

    payload = {"data": {"tickers_queried": tickers, "posts": posts,
                        "fetched_at_unix": int(time.time())}, "error": None}
    _write_cache(cache_path, payload)
    return payload


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", required=False, default="")
    p.add_argument("--filter", default="hot")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload()
    elif not tickers:
        payload = {"data": None, "error": "--tickers required for live query"}
    else:
        payload = _live_query(tickers, args.filter)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
