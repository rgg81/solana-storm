"""Reddit public-JSON hot/new posts scanner.

Usage:
    python3 reddit_hot_posts.py --tickers STORM,PEPE [--max-age-sec 3600] [--dry-run]
"""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402
from predictions import config  # noqa: E402

_HEADERS = {"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"}
_TIMEOUT = 15
CACHE_TTL_SEC = 600


def _dry_run_payload() -> dict:
    fixture = config.DRY_RUN_DIR / "reddit_hot_posts.dry_run.json"
    return json.loads(fixture.read_text())


def _cache_path(sub: str, sort: str) -> Path:
    cache_dir = config.CACHE_DIR / "reddit"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{sub}_{sort}.json"


def _read_cache(path: Path) -> dict | None:
    if not path.exists() or (time.time() - path.stat().st_mtime) > CACHE_TTL_SEC:
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_cache(path: Path, body: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body))
    tmp.rename(path)


def _fetch_sub(sub: str, sort: str = "new", limit: int = 100) -> dict | None:
    cached = _read_cache(_cache_path(sub, sort))
    if cached is not None:
        return cached
    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={limit}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code >= 400:
            return None
        body = r.json()
        _write_cache(_cache_path(sub, sort), body)
        return body
    except Exception:
        return None


def _extract_posts(body: dict, sub: str, since_unix: int, ticker_patterns: list[tuple[str, re.Pattern]]) -> list[dict]:
    out = []
    for child in (body.get("data", {}).get("children") or []):
        d = child.get("data") or {}
        created = int(d.get("created_utc") or 0)
        if created < since_unix:
            continue
        text = (d.get("title") or "") + " " + (d.get("selftext") or "")
        matched = [t for t, pat in ticker_patterns if pat.search(text)]
        if not matched:
            continue
        out.append({
            "subreddit": sub,
            "title": d.get("title") or "",
            "author": d.get("author") or "",
            "created_utc": created,
            "score": int(d.get("score") or 0),
            "num_comments": int(d.get("num_comments") or 0),
            "permalink": d.get("permalink") or "",
            "matched_tickers": matched,
        })
    return out


def _live_query(tickers: list[str], max_age_sec: int) -> dict:
    since = int(time.time()) - max_age_sec
    patterns = [(t, re.compile(rf"[\$#]?{re.escape(t)}\b", re.IGNORECASE)) for t in tickers]
    all_posts: list[dict] = []
    for sub in config.REDDIT_SUBS:
        for sort in ("new", "hot"):
            body = _fetch_sub(sub, sort)
            if body is None:
                continue
            all_posts.extend(_extract_posts(body, sub, since, patterns))
            time.sleep(1)
    return {"data": {"tickers_queried": tickers, "posts": all_posts,
                     "fetched_at_unix": int(time.time())}, "error": None}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", required=False, default="")
    p.add_argument("--max-age-sec", type=int, default=3600)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if args.dry_run or config.is_rehearsal():
        payload = _dry_run_payload()
    elif not tickers:
        payload = {"data": None, "error": "--tickers required for live query"}
    else:
        payload = _live_query(tickers, args.max_age_sec)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
