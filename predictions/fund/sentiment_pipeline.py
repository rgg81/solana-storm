"""End-to-end sentiment pipeline:
1. Collect matched headlines (RSS + CryptoPanic + DexScreener catalysts) per symbol
2. Fetch article bodies (with cache)
3. Score each with VADER + source weight + temporal decay
4. Aggregate per-symbol anchor score
5. Track in sentiment_history.jsonl + detect anomalies
6. Format for agent prompts

Replaces (extends) the news_check.py inline approach. Same data sources,
deterministic scoring layer on top.
"""
from __future__ import annotations
import json, time, re, sys, statistics
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

import requests
from predictions.fund import sentiment

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SENTIMENT_HISTORY = STATE_DIR / "sentiment_history.jsonl"
BODY_CACHE_DIR = STATE_DIR / "article_body_cache"
BODY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
BODY_CACHE_TTL = 6 * 3600  # 6h

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; smaf-research/1.0)",
    "Accept": "text/html,application/xhtml+xml,*/*",
}


def _body_cache_path(url: str) -> Path:
    import hashlib
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    return BODY_CACHE_DIR / f"{h}.txt"


def fetch_article_body(url: str, max_chars: int = 1500) -> str | None:
    """Fetch article HTML, extract main content via heuristics, return text excerpt."""
    if not url: return None
    cp = _body_cache_path(url)
    if cp.exists() and (time.time() - cp.stat().st_mtime) < BODY_CACHE_TTL:
        return cp.read_text(encoding="utf-8", errors="replace")[:max_chars]
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200: return None
        html = r.text
        # Strip script/style
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        # Try <article>, then class*=article|content|post
        body = None
        m = re.search(r"<article[^>]*>(.*?)</article>", html, re.DOTALL | re.IGNORECASE)
        if m: body = m.group(1)
        if not body:
            m = re.search(r'<div[^>]+class="[^"]*(article-body|article-content|post-content|story-body|article__content)[^"]*"[^>]*>(.*?)</div>',
                          html, re.DOTALL | re.IGNORECASE)
            if m: body = m.group(2)
        if not body:
            # og:description as fallback
            m = re.search(r'<meta[^>]+(?:property|name)="og:description"[^>]+content="([^"]+)"', html)
            if m: body = m.group(1)
        if not body: return None
        # Strip remaining HTML
        text = re.sub(r"<[^>]+>", " ", body)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 50: return None
        tmp = cp.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.rename(cp)
        return text[:max_chars]
    except Exception:
        return None


def enrich_headlines_with_bodies(headlines: list[dict], max_fetches: int = 8) -> list[dict]:
    """For each headline with a URL, try to fetch + attach a body excerpt."""
    fetched = 0
    out = []
    for h in headlines:
        body = None
        url = h.get("url") or h.get("link") or ""
        if url and fetched < max_fetches:
            body = fetch_article_body(url)
            if body: fetched += 1
            time.sleep(0.3)  # polite
        h2 = dict(h)
        h2["body_excerpt"] = body
        out.append(h2)
    return out


# === Sentiment history (Item 2) ===

def record_tick_sentiment(per_symbol_anchors: dict) -> None:
    """Append this tick's per-symbol anchors to sentiment_history.jsonl."""
    if not per_symbol_anchors: return
    row = {"timestamp": int(time.time()), "per_symbol": {}}
    for ticker, anchor in per_symbol_anchors.items():
        row["per_symbol"][ticker] = {
            "anchor_score": anchor.get("anchor_score", 0.0),
            "n_headlines": anchor.get("n_headlines", 0),
        }
    existing = SENTIMENT_HISTORY.read_text() if SENTIMENT_HISTORY.exists() else ""
    tmp = SENTIMENT_HISTORY.with_suffix(".tmp")
    tmp.write_text(existing + json.dumps(row) + "\n")
    tmp.rename(SENTIMENT_HISTORY)


def detect_anomalies(per_symbol_anchors: dict, lookback_ticks: int = 14,
                      min_history: int = 5, z_threshold: float = 2.0) -> dict:
    """For each symbol, compare current anchor to trailing avg/stdev.
    
    Returns per-symbol anomaly dict. Flag if |z_score| > z_threshold.
    """
    if not SENTIMENT_HISTORY.exists(): return {}
    rows = [json.loads(l) for l in SENTIMENT_HISTORY.read_text().splitlines() if l.strip()]
    if len(rows) < min_history: return {}
    # Per-ticker historical scores
    history = {}
    for r in rows[-lookback_ticks:]:
        for t, d in (r.get("per_symbol") or {}).items():
            history.setdefault(t, []).append(d.get("anchor_score", 0.0))
    
    out = {}
    for ticker, current in per_symbol_anchors.items():
        hist = history.get(ticker) or []
        if len(hist) < min_history: continue
        avg = sum(hist) / len(hist)
        sd = statistics.stdev(hist) if len(hist) > 1 else 0
        if sd == 0: continue
        cur = current.get("anchor_score", 0.0)
        z = (cur - avg) / sd
        if abs(z) > z_threshold:
            out[ticker] = {
                "z_score": round(z, 2),
                "current": round(cur, 3),
                "baseline_avg": round(avg, 3),
                "baseline_sd": round(sd, 3),
                "n_history": len(hist),
                "type": "spike" if z > 0 else "drop",
            }
    return out


def format_for_agent_prompt(per_symbol_anchors: dict, anomalies: dict) -> str:
    """Compact block — what agents see as the deterministic sentiment anchor + anomalies."""
    if not per_symbol_anchors:
        return "SENTIMENT_ANCHOR: no data this tick"
    lines = ["SENTIMENT_ANCHOR (deterministic: VADER × source-authority × time-decay):"]
    lines.append("  Agents: this is the deterministic baseline. Your own news_sentiment.score is the LLM verdict.")
    lines.append("  If |your_score - anchor| > 0.30, you MUST include `override_reasoning` citing what the anchor missed.")
    lines.append("")
    for ticker in sorted(per_symbol_anchors.keys()):
        anchor = per_symbol_anchors[ticker]
        lines.append(sentiment.format_anchor_for_agent_prompt(ticker, anchor))
    if anomalies:
        lines.append("")
        lines.append("SENTIMENT ANOMALIES (>2σ from 14-tick baseline):")
        for t, a in anomalies.items():
            arrow = "📈" if a["type"] == "spike" else "📉"
            lines.append(f"  {arrow} {t}: z={a['z_score']:+.2f} (cur {a['current']:+.3f}, baseline {a['baseline_avg']:+.3f}±{a['baseline_sd']:.3f}, n={a['n_history']})")
    return "\n".join(lines)


# === Orchestration: end-to-end per-tick ===

def build_anchors_from_phase2(phase2_data: dict, max_body_fetches: int = 8) -> tuple[dict, dict]:
    """Build per-symbol sentiment anchors from a phase2_input.json structure.
    
    Returns (per_symbol_anchors, anomalies).
    """
    universe = phase2_data.get("universe") or []
    rss_news = phase2_data.get("rss_news") or {}
    headlines_by_ticker = rss_news.get("headlines") or {}
    cp_per_ticker = phase2_data.get("cryptopanic_per_ticker") or {}
    
    per_symbol_anchors = {}
    for ticker in universe:
        # Collect headlines: RSS + CryptoPanic + DexScreener flow proxy
        all_headlines = []
        for h in headlines_by_ticker.get(ticker, []) or []:
            all_headlines.append({
                "title": h.get("title", ""), "feed": h.get("feed", "default"),
                "pub_unix": h.get("pub_unix", 0), "url": h.get("link") or h.get("url"),
            })
        for h in cp_per_ticker.get(ticker, []) or []:
            if not isinstance(h, dict): continue
            all_headlines.append({
                "title": h.get("title", ""), "feed": "cryptopanic",
                "pub_unix": h.get("pub_unix", 0), "url": h.get("url"),
            })
        # DexScreener "catalyst" signals — synthetic headline if buy_skew is extreme
        per_sym = (phase2_data.get("per_symbol") or {}).get(ticker, {})
        bs = per_sym.get("buy_skew_pct")
        if bs is not None and (bs >= 70 or bs <= 30):
            synthetic = f"On-chain flow: {bs}% buys on {ticker} ({'accumulation' if bs >= 70 else 'distribution'})"
            all_headlines.append({
                "title": synthetic, "feed": "dexscreener",
                "pub_unix": int(time.time()), "url": None,
            })
        
        # Fetch bodies (capped)
        all_headlines = enrich_headlines_with_bodies(all_headlines, max_fetches=max_body_fetches)
        max_body_fetches = max(0, max_body_fetches - sum(1 for h in all_headlines if h.get("body_excerpt")))
        
        # Score + aggregate
        scored = [sentiment.score_headline(h, body_text=h.get("body_excerpt")) for h in all_headlines]
        per_symbol_anchors[ticker] = sentiment.aggregate_per_symbol(scored)
    
    # Detect anomalies BEFORE recording (so we use prior history)
    anomalies = detect_anomalies(per_symbol_anchors)
    # Record this tick's anchors
    record_tick_sentiment(per_symbol_anchors)
    
    return per_symbol_anchors, anomalies


if __name__ == "__main__":
    # Self-test on the latest tick_phase2_input.json
    phase2_path = STATE_DIR / "tick_phase2_input.json"
    if not phase2_path.exists():
        print("No tick_phase2_input.json found — run stage_phase2.py first")
        sys.exit(0)
    data = json.loads(phase2_path.read_text())
    anchors, anomalies = build_anchors_from_phase2(data, max_body_fetches=4)
    print(format_for_agent_prompt(anchors, anomalies))
