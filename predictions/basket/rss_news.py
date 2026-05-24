"""Free crypto RSS news aggregator for the basket universe.

Fetches RSS feeds (Decrypt, CoinTelegraph, The Block, CoinDesk, CryptoCompare)
and filters to articles mentioning any of our 10 basket tokens by name or ticker.

No API keys required. Caches feeds for 15min to be polite.

Usage:
    python3 predictions/basket/rss_news.py
    python3 predictions/basket/rss_news.py --hours 24    # last 24h only
"""
from __future__ import annotations
import argparse, json, re, sys, time, hashlib
from pathlib import Path
from xml.etree import ElementTree as ET

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests
from predictions.basket.universe import UNIVERSE

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; solana-storm-research/1.0)",
           "Accept": "application/rss+xml, application/xml, text/xml, */*"}
TIMEOUT = 15
CACHE_TTL = 15 * 60  # 15 min
CACHE_DIR = _REPO_ROOT / "predictions" / "basket" / "state" / "rss_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FEEDS = [
    ("decrypt",       "https://decrypt.co/feed"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("theblock",      "https://www.theblock.co/rss.xml"),
    ("coindesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
]

# Token name + ticker patterns. Each ticker maps to a list of regex patterns.
# Word-bounded so "JUP" doesn't match inside "Jupiter" if we only want the ticker.
SEARCH_TERMS = {
    "JUP":    [r"\bJUP\b", r"\bJupiter\b(?:\s+Exchange)?"],
    "BONK":   [r"\bBONK\b", r"\bBonkcoin\b"],
    "WIF":    [r"\bWIF\b", r"\bdogwifhat\b", r"\bdogwif\b"],
    "JTO":    [r"\bJTO\b", r"\bJito\b"],
    "RAY":    [r"\bRAY\b(?=\s|/|$)", r"\bRaydium\b"],
    "ORCA":   [r"\bORCA\b(?=\s|/|$)", r"\bOrca\b"],  # word-boundary right too
    "PYTH":   [r"\bPYTH\b", r"\bPyth\s+Network\b", r"\bPyth\b"],
    "POPCAT": [r"\bPOPCAT\b", r"\bPopcat\b"],
    "MEW":    [r"\bMEW\b", r"\bcat\s+in\s+a\s+dogs?\s+world\b"],
    "DRIFT":  [r"\bDRIFT\b(?=\s|/|$)", r"\bDrift\s+Protocol\b"],
}


def _cache_path(feed_id: str) -> Path:
    return CACHE_DIR / f"{feed_id}.xml"


def _fetch_feed(feed_id: str, url: str) -> str | None:
    """Fetch with cache. Returns raw XML text or None."""
    cp = _cache_path(feed_id)
    if cp.exists() and (time.time() - cp.stat().st_mtime) < CACHE_TTL:
        return cp.read_text()
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        tmp = cp.with_suffix(".tmp")
        tmp.write_text(r.text)
        tmp.rename(cp)
        return r.text
    except Exception:
        return None


def _parse_items(xml_text: str, feed_id: str) -> list[dict]:
    """Extract <item> nodes from RSS XML."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    # RSS: rss/channel/item; Atom: feed/entry
    for item in root.iter():
        tag = item.tag.lower().split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        rec = {"feed": feed_id, "title": "", "link": "", "summary": "", "pubdate": ""}
        for child in item:
            ctag = child.tag.lower().split("}")[-1]
            text = (child.text or "").strip()
            if ctag == "title": rec["title"] = text
            elif ctag == "link":
                # Atom links are href attribs; RSS links are text
                rec["link"] = text or child.attrib.get("href", "")
            elif ctag in ("description", "summary", "content"): 
                rec["summary"] = text
            elif ctag in ("pubdate", "published", "updated"):
                rec["pubdate"] = text
        if rec["title"]:
            items.append(rec)
    return items


def _parse_pubdate_unix(pubdate: str) -> int | None:
    if not pubdate: return None
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(pubdate)
        return int(dt.timestamp())
    except Exception:
        try:
            # ISO 8601 fallback
            import datetime as dt_m
            dt = dt_m.datetime.fromisoformat(pubdate.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except Exception:
            return None


def find_basket_mentions(hours: int = 48) -> dict:
    cutoff = int(time.time()) - hours * 3600
    all_items = []
    for feed_id, url in FEEDS:
        xml = _fetch_feed(feed_id, url)
        if xml is None:
            continue
        items = _parse_items(xml, feed_id)
        for it in items:
            it["pub_unix"] = _parse_pubdate_unix(it["pubdate"]) or 0
            all_items.append(it)

    # Filter to within window
    recent = [it for it in all_items if it["pub_unix"] >= cutoff]
    
    # Compile regex once
    compiled = {ticker: [re.compile(p, re.IGNORECASE) for p in pats]
                for ticker, pats in SEARCH_TERMS.items()}
    
    # Search each item
    matches_by_ticker = {t: [] for t in SEARCH_TERMS}
    seen_titles = set()
    for it in recent:
        haystack = it["title"] + " " + it["summary"]
        h_norm = re.sub(r"<[^>]+>", " ", haystack)
        for ticker, patterns in compiled.items():
            for pat in patterns:
                if pat.search(h_norm):
                    # Dedupe by title
                    title_key = it["title"][:80]
                    if title_key in seen_titles:
                        continue
                    seen_titles.add(title_key)
                    matches_by_ticker[ticker].append({
                        "feed": it["feed"],
                        "title": it["title"],
                        "link": it["link"],
                        "pub_unix": it["pub_unix"],
                        "summary": it["summary"][:200],
                    })
                    break
    
    summary = {t: len(ms) for t, ms in matches_by_ticker.items()}
    
    return {
        "checked_at_unix": int(time.time()),
        "window_hours": hours,
        "items_in_window": len(recent),
        "total_items_fetched": len(all_items),
        "matches_per_ticker": summary,
        "matches": matches_by_ticker,
        "feed_status": {fid: (_cache_path(fid).exists()) for fid, _ in FEEDS},
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=48,
                   help="Look-back window in hours (default 48)")
    p.add_argument("--quiet", action="store_true", help="JSON only, no human summary")
    args = p.parse_args()

    out = find_basket_mentions(args.hours)
    if args.quiet:
        print(json.dumps(out, indent=2))
        return 0
    print(f"=== RSS news for basket (last {args.hours}h) ===")
    print(f"Items in window: {out['items_in_window']} / {out['total_items_fetched']} fetched")
    print(f"Feeds fetched: {sum(out['feed_status'].values())}/{len(out['feed_status'])}")
    print()
    summary = out["matches_per_ticker"]
    total_hits = sum(summary.values())
    print(f"Mentions per ticker: {summary}")
    print(f"Total mentions: {total_hits}\n")
    for ticker, items in out["matches"].items():
        if not items: continue
        print(f"--- {ticker} ({len(items)} hits) ---")
        for m in items[:5]:
            import datetime as dt
            ts = dt.datetime.utcfromtimestamp(m["pub_unix"]).strftime("%m-%d %H:%MZ") if m["pub_unix"] else "no-date"
            print(f"  [{ts}] [{m['feed']}] {m['title'][:100]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
