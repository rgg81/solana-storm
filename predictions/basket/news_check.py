"""News + momentum surface for the Solana basket universe.

Sources (free, no API key):
- DexScreener token pairs: current price, 24h volume, multi-timeframe price changes
- Reddit (broad subreddit scan): mentions in titles within last 24h
- CoinGecko trending: are any of our tokens in CG's trending list?

Output: JSON to stdout listing any "catalyst" events worth surfacing.

A catalyst is: (1) 24h price move > ±10%, OR (2) 1h price move > ±5%, OR
(3) 24h volume > 3× the trailing 7d median, OR (4) appears in CG trending.

Usage:
    python3 predictions/basket/news_check.py
"""
from __future__ import annotations
import sys, json, time, re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests
from predictions.basket.universe import UNIVERSE, TICKERS, TICKER_TO_MINT, TICKER_TO_CGID

_HEADERS = {"User-Agent": "solana-storm-basket/1.0", "Accept": "application/json"}
_TIMEOUT = 15

# Catalyst thresholds
PRICE_24H_THRESH = 0.10    # ±10%
PRICE_1H_THRESH = 0.05     # ±5%
VOLUME_SPIKE_MULT = 3.0    # 3× trailing-median


def fetch_dexscreener_pair(mint: str) -> dict | None:
    """DexScreener pairs endpoint by token mint. Returns the most-liquid SOL pair."""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                          headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200: return None
        pairs = r.json().get("pairs") or []
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs: return None
        # Pick the one with highest liquidity USD
        best = max(sol_pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        return {
            "price_usd": float(best.get("priceUsd") or 0),
            "vol_h24": float((best.get("volume") or {}).get("h24") or 0),
            "vol_h1":  float((best.get("volume") or {}).get("h1") or 0),
            "chg_h24": float((best.get("priceChange") or {}).get("h24") or 0) / 100.0,
            "chg_h6":  float((best.get("priceChange") or {}).get("h6") or 0) / 100.0,
            "chg_h1":  float((best.get("priceChange") or {}).get("h1") or 0) / 100.0,
            "chg_m5":  float((best.get("priceChange") or {}).get("m5") or 0) / 100.0,
            "liq_usd": float((best.get("liquidity") or {}).get("usd") or 0),
            "fdv":     float(best.get("fdv") or 0),
            "txns_h24_buys":  int(((best.get("txns") or {}).get("h24") or {}).get("buys") or 0),
            "txns_h24_sells": int(((best.get("txns") or {}).get("h24") or {}).get("sells") or 0),
            "dex": best.get("dexId"),
            "pair_addr": best.get("pairAddress"),
        }
    except Exception as e:
        return None


def fetch_cg_trending() -> list[str]:
    """CoinGecko /search/trending — list of currently-trending coin IDs."""
    try:
        r = requests.get("https://api.coingecko.com/api/v3/search/trending",
                          headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200: return []
        out = []
        for item in (r.json().get("coins") or []):
            it = item.get("item") or {}
            out.append((it.get("id"), it.get("symbol", "").upper(), it.get("name"),
                       it.get("market_cap_rank")))
        return out
    except Exception:
        return []


def main():
    catalysts = []
    raw_data = {}

    # 0) RSS news headlines (free, no key)
    try:
        from predictions.basket.rss_news import find_basket_mentions
        rss = find_basket_mentions(hours=48)
        rss_hits = sum(rss.get("matches_per_ticker", {}).values())
        if rss_hits > 0:
            for ticker, items in (rss.get("matches") or {}).items():
                if items:
                    catalysts.append({
                        "ticker": ticker, "source": "rss",
                        "events": [f"{len(items)} news mention(s) in 48h"],
                        "headlines": [{"feed": m["feed"], "title": m["title"],
                                       "pub_unix": m["pub_unix"]} for m in items[:3]],
                    })
        raw_data["_rss"] = {"items_in_window": rss.get("items_in_window"),
                            "matches_per_ticker": rss.get("matches_per_ticker"),
                            "total_hits": rss_hits}
    except Exception as e:
        raw_data["_rss"] = {"error": f"{type(e).__name__}: {str(e)[:60]}"}
    
    # 0.5) CryptoPanic via sitemap scrape (no API, no key)
    try:
        from predictions.basket.cryptopanic_scraper import find_mentions as cp_find
        cp = cp_find(max_per_token=3, max_articles_fetched=30)
        for ticker, articles in (cp.get("mentions") or {}).items():
            if not articles: continue
            catalysts.append({
                "ticker": ticker, "source": "cryptopanic",
                "events": [f"{len(articles)} CryptoPanic article(s)"],
                "headlines": [{"title": a["title"][:120], "url": a["url"],
                              "article_id": a.get("article_id")} for a in articles[:3]],
            })
        raw_data["_cryptopanic"] = {"sitemap_items": cp.get("sitemap_items_seen"),
                                     "articles_fetched": cp.get("articles_fetched"),
                                     "mentions_per_ticker": cp.get("mentions_per_ticker")}
    except Exception as e:
        raw_data["_cryptopanic"] = {"error": f"{type(e).__name__}: {str(e)[:60]}"}

    # 1) DexScreener per token
    for cg, ticker, mint in UNIVERSE:
        d = fetch_dexscreener_pair(mint)
        if d is None: 
            raw_data[ticker] = {"error": "dexscreener_no_data"}
            continue
        raw_data[ticker] = d
        events = []
        # Price moves
        if abs(d["chg_h24"]) >= PRICE_24H_THRESH:
            events.append(f"24h move {d['chg_h24']*100:+.1f}%")
        if abs(d["chg_h1"]) >= PRICE_1H_THRESH:
            events.append(f"1h move {d['chg_h1']*100:+.1f}%")
        # Volume velocity: rough heuristic — if h1 volume > h24/24*5 (5× per-hour average)
        if d["vol_h24"] > 0:
            avg_h1 = d["vol_h24"] / 24
            if d["vol_h1"] > avg_h1 * 5:
                events.append(f"vol surge: 1h ${d['vol_h1']:,.0f} vs avg ${avg_h1:,.0f} (×{d['vol_h1']/avg_h1:.1f})")
        # Buy/sell imbalance
        if d["txns_h24_buys"] + d["txns_h24_sells"] > 100:
            buy_pct = d["txns_h24_buys"] / (d["txns_h24_buys"] + d["txns_h24_sells"])
            if buy_pct < 0.4: events.append(f"sell-skew: {buy_pct*100:.0f}% buys ({d['txns_h24_buys']}b / {d['txns_h24_sells']}s)")
            elif buy_pct > 0.65: events.append(f"buy-skew: {buy_pct*100:.0f}% buys")
        if events:
            catalysts.append({"ticker": ticker, "source": "dexscreener", "events": events,
                             "price_usd": d["price_usd"], "vol_h24_usd": d["vol_h24"],
                             "liq_usd": d["liq_usd"]})

    # 2) CoinGecko trending
    trending = fetch_cg_trending()
    trending_in_universe = []
    universe_cg_ids = {c for c, _, _ in UNIVERSE}
    universe_tickers = {t.upper() for _, t, _ in UNIVERSE}
    for cg_id, symbol, name, rank in trending:
        if cg_id in universe_cg_ids or symbol.upper() in universe_tickers:
            trending_in_universe.append({"ticker": symbol, "name": name, "rank": rank})
    if trending_in_universe:
        catalysts.append({"source": "coingecko_trending", "members": trending_in_universe})

    out = {
        "checked_at_unix": int(time.time()),
        "catalysts": catalysts,
        "raw": raw_data,
        "trending_top_10": [t[1] for t in trending[:10]],
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
