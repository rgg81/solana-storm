"""CryptoPanic scraper via public sitemap (no API, no key, no JS rendering).

Approach:
1. Fetch `sitemap-posts.xml` (10,000 recent articles, monotonic IDs)
2. Filter URL slugs containing any basket-universe keyword
3. For matches: fetch the article page, extract <title> + og:description
4. Sort by article ID (which is chronological)
5. Return last N hits per ticker

Respects: 1s sleep between page fetches, browser UA, cache-aware.

Usage:
    python3 predictions/basket/cryptopanic_scraper.py
    python3 predictions/basket/cryptopanic_scraper.py --max-per-token 5
"""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests
from predictions.basket.universe import UNIVERSE

BROWSER = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://cryptopanic.com/",
}
TIMEOUT = 15
CACHE_DIR = _REPO_ROOT / "predictions" / "basket" / "state" / "cryptopanic_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SITEMAP_CACHE_TTL = 30 * 60  # 30 min — sitemap refreshes hourly-ish
ARTICLE_CACHE_TTL = 6 * 3600  # 6h — articles don't change after publication

# URL-slug keywords (lowercased; slugs are lowercased with hyphens)
# Tighter slug matchers. Disambiguation strategy:
# - "ray" is a common word -> require "raydium" full token
# - "mew" matches MyEtherWallet -> require "cat-in-a-dogs-world" OR Solana context
# - "drift" matches general usage -> require "drift-protocol" or "drift-token"
# - "orca" matches the animal -> require "orca-token" / "orca-dex" / "orca-protocol"
SLUG_TOKENS = {
    "JUP":    ["jupiter-exchange", "jupiter-aggregator", "jupiter-perps", "jupiter-dex", "-jup-token"],
    "BONK":   ["bonk-"],  # bonk-coin, bonk-token, etc. — distinctive token name
    "WIF":    ["dogwifhat", "dogwif-", "-wif-token"],
    "JTO":    ["jito-", "-jto-"],
    "RAY":    ["raydium"],  # exclude bare "-ray-" to avoid "ray of hope"
    "ORCA":   ["orca-token", "orca-dex", "orca-protocol", "orca-finance"],
    "PYTH":   ["pyth-network", "pyth-oracle", "-pyth-"],
    "POPCAT": ["popcat"],
    "MEW":    ["cat-in-a-dogs-world", "mew-token"],  # exclude bare "mew" (MyEtherWallet)
    "DRIFT":  ["drift-protocol", "drift-defi", "drift-dex"],
}
# Solana-context tokens (in article slug or title) — used to disambiguate ambiguous matches
SOLANA_CONTEXT = re.compile(r"\b(solana|sol|spl|saga|jupiter|raydium|orca|backpack|magic-eden|jito|phantom|pyth)\b", re.I)


def _fetch(url: str, cache_path: Path, ttl: int) -> str | None:
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < ttl:
        return cache_path.read_text(encoding="utf-8", errors="replace")
    try:
        r = requests.get(url, headers=BROWSER, timeout=TIMEOUT)
        if r.status_code != 200: return None
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(r.text)
        tmp.rename(cache_path)
        return r.text
    except Exception:
        return None


def _parse_sitemap(xml: str) -> list[tuple[int, str]]:
    """Return list of (article_id, url) tuples."""
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    out = []
    for u in urls:
        m = re.search(r"/news/(\d+)/", u)
        if m:
            out.append((int(m.group(1)), u))
    return out


def _filter_by_slug(items: list[tuple[int, str]]) -> dict[str, list[tuple[int, str]]]:
    """For each universe ticker, find articles whose URL slug matches."""
    hits = {t: [] for t in SLUG_TOKENS}
    for aid, url in items:
        slug = url.lower().split("/", 4)[-1] if "/" in url else url.lower()
        for ticker, keys in SLUG_TOKENS.items():
            if any(k in slug for k in keys):
                hits[ticker].append((aid, url))
                break  # one ticker max per article
    return hits


def _fetch_article_meta(url: str) -> dict | None:
    """Extract title + description from an article page."""
    cache_path = CACHE_DIR / f"art_{hash(url) & 0xFFFFFFFF:08x}.html"
    html = _fetch(url, cache_path, ARTICLE_CACHE_TTL)
    if html is None: return None
    title = re.search(r"<title[^>]*>([^<]+)</title>", html)
    og_title = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    og_desc = re.search(r'property="og:description"\s+content="([^"]+)"', html)
    art_pub = re.search(r'property="article:published_time"\s+content="([^"]+)"', html)
    # Try multiple date patterns
    pub = ""
    for pat in [
        r'property="article:published_time"\s+content="([^"]+)"',
        r'datetime="([^"]+)"',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"pubDate"\s*:\s*"([^"]+)"',
        r'<time[^>]+>([^<]+)</time>',
    ]:
        m = re.search(pat, html)
        if m:
            pub = m.group(1).strip()
            break
    return {
        "url": url,
        "title": (og_title.group(1) if og_title else (title.group(1) if title else "")).strip(),
        "description": (og_desc.group(1) if og_desc else "").strip(),
        "published": pub,
    }


def find_mentions(max_per_token: int = 5, max_articles_fetched: int = 50) -> dict:
    """Main entry. Returns dict with per-ticker article metadata."""
    sm_cache = CACHE_DIR / "sitemap-posts.xml"
    xml = _fetch("https://cryptopanic.com/sitemap-posts.xml", sm_cache, SITEMAP_CACHE_TTL)
    if xml is None:
        return {"error": "sitemap unreachable", "mentions": {}}
    items = _parse_sitemap(xml)
    items.sort(key=lambda x: -x[0])  # newest first by article ID
    
    hits = _filter_by_slug(items)
    
    # Limit per ticker, fetch metadata for top N
    enriched = {}
    total_fetched = 0
    for ticker, slug_matches in hits.items():
        enriched[ticker] = []
        for aid, url in slug_matches[:max_per_token]:
            if total_fetched >= max_articles_fetched:
                break
            meta = _fetch_article_meta(url)
            if meta:
                meta["article_id"] = aid
                enriched[ticker].append(meta)
            total_fetched += 1
            time.sleep(0.5)  # be polite
    
    return {
        "checked_at_unix": int(time.time()),
        "sitemap_items_seen": len(items),
        "articles_fetched": total_fetched,
        "mentions_per_ticker": {t: len(slug_matches) for t, slug_matches in hits.items()},
        "mentions": enriched,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-per-token", type=int, default=5)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()
    
    out = find_mentions(args.max_per_token)
    if args.quiet:
        print(json.dumps(out, indent=2, default=str))
        return 0
    print(f"=== CryptoPanic scraper ===")
    print(f"Sitemap items scanned: {out.get('sitemap_items_seen')}")
    print(f"Article pages fetched: {out.get('articles_fetched')}")
    print(f"Slug-matches per ticker: {out.get('mentions_per_ticker')}")
    print()
    for ticker, articles in (out.get("mentions") or {}).items():
        if not articles: continue
        print(f"--- {ticker} ---")
        for a in articles:
            pub = a.get("published", "")[:10] if a.get("published") else "no-date"
            print(f"  [{pub}] {a['title'][:100]}")
            if a.get("description"):
                print(f"          {a['description'][:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
