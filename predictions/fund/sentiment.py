"""Deterministic sentiment anchor for SMAF.

Per headline: VADER compound × source authority weight × exp(-age_h / 12) decay
Per symbol: weighted composite + top drivers + body-level VADER if available

This is the ANCHOR. LLM agents MUST produce their own score, but they receive
this anchor as a deterministic reference. If they deviate by more than 0.30,
they must explain why in override_reasoning.

Why an anchor at all:
- Reduces subjective LLM drift
- Gives auditable baseline that doesn't change tick-to-tick
- Lets us validate sentiment→return relationships statistically
- Catches obvious cases (clearly negative headline) without LLM reasoning cost

Why agents still have override authority:
- VADER misses context (sarcasm, irony, crypto-specific connotations)
- An anchor +0.4 might be "exit liquidity for whales" — the agent sees this
- Hallucination risk is the price we pay for divergent insight
"""
from __future__ import annotations
import json, time, math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _vader = SentimentIntensityAnalyzer()
except ImportError:
    _vader = None

# FinBERT lazy-load: only initializes on first use (~400MB model download)
_finbert = None
_finbert_load_attempted = False

def _get_finbert():
    """Lazy-load ProsusAI/finbert. Returns None if unavailable."""
    global _finbert, _finbert_load_attempted
    if _finbert_load_attempted:
        return _finbert
    _finbert_load_attempted = True
    try:
        from transformers import pipeline
        # ProsusAI/finbert returns 3-class: positive / negative / neutral
        _finbert = pipeline("sentiment-analysis", model="ProsusAI/finbert",
                             truncation=True, max_length=512)
        print("[sentiment] FinBERT loaded successfully")
    except Exception as e:
        print(f"[sentiment] FinBERT load failed: {type(e).__name__}: {str(e)[:80]}")
        _finbert = None
    return _finbert

# Source authority weights (manual; can become learned later)
SOURCE_WEIGHTS = {
    "coindesk": 1.00,    "theblock": 1.00,
    "bloomberg": 1.00,
    "decrypt": 0.85,
    "cointelegraph": 0.70,
    "cryptopanic": 0.60,  # aggregator — varying source quality underneath
    "dexscreener": 0.55,  # on-chain proxy, indirect sentiment
    "coingecko_trending": 0.65,
    "reddit_solana": 0.50,
    "reddit_cryptocurrency": 0.40,
    "default": 0.40,
}

# Temporal decay constant — exponential half-life ~12h
# exp(-age_h / DECAY_TAU): at 0h=1.0, 12h=0.37, 24h=0.14, 48h=0.02
DECAY_TAU_HOURS = 12.0

# Override threshold — if |agent_score - anchor| > this, agent must justify
OVERRIDE_THRESHOLD = 0.30


def vader_score(text: str) -> dict | None:
    """Return VADER scores for a text. None if VADER unavailable or text empty."""
    if _vader is None or not text or not isinstance(text, str): return None
    s = _vader.polarity_scores(text)
    return {
        "compound": round(s["compound"], 4),
        "pos": round(s["pos"], 4),
        "neg": round(s["neg"], 4),
        "neu": round(s["neu"], 4),
    }


def finbert_score(text: str) -> dict | None:
    """ProsusAI/finbert: 3-class financial sentiment.
    Converts to continuous score in [-1, +1] where positive label → magnitude positive.
    """
    fb = _get_finbert()
    if fb is None or not text or not isinstance(text, str): return None
    text = text[:512]  # truncate per model limit
    try:
        result = fb(text)[0]
        label = result["label"].lower()
        confidence = result["score"]
        if "positive" in label: signed = confidence
        elif "negative" in label: signed = -confidence
        else: signed = 0.0
        return {"compound": round(signed, 4), "label": label, "confidence": round(confidence, 4)}
    except Exception:
        return None


def composite_score(text: str) -> dict:
    """Combined VADER + FinBERT score. Used by score_headline.
    
    Weighting: when both available, FinBERT 60% (better at financial context),
    VADER 40% (better at social media tone). If only one available, use it.
    """
    v = vader_score(text)
    f = finbert_score(text)
    if v and f:
        composite = 0.4 * v["compound"] + 0.6 * f["compound"]
        return {"compound": round(composite, 4), "vader": v, "finbert": f,
                "method": "vader_finbert_blend"}
    elif f:
        return {"compound": f["compound"], "vader": None, "finbert": f,
                "method": "finbert_only"}
    elif v:
        return {"compound": v["compound"], "vader": v, "finbert": None,
                "method": "vader_only"}
    return {"compound": 0.0, "vader": None, "finbert": None, "method": "no_data"}


def source_weight(source_id: str) -> float:
    """Normalize source identifier → authority weight."""
    if not source_id: return SOURCE_WEIGHTS["default"]
    s = source_id.lower().replace("_", "").replace("-", "")
    for k, w in SOURCE_WEIGHTS.items():
        if k.replace("_", "").replace("-", "") in s: return w
    return SOURCE_WEIGHTS["default"]


def temporal_decay(age_hours: float) -> float:
    """Exponential decay weight based on age in hours."""
    if age_hours is None or age_hours < 0: return 1.0
    return math.exp(-age_hours / DECAY_TAU_HOURS)


def score_headline(headline: dict, body_text: str | None = None) -> dict:
    """Score a single matched headline. Input dict has at least: title, feed/source, pub_unix.
    
    Returns enriched dict with: vader_headline, vader_body, source_weight, age_h, decay,
    weighted_contribution = vader_compound × source_w × decay.
    """
    now = int(time.time())
    title = headline.get("title", "")
    source = headline.get("feed") or headline.get("source") or "default"
    pub = headline.get("pub_unix", 0)
    age_h = max(0.0, (now - pub) / 3600.0) if pub > 0 else 0.0
    
    c_head = composite_score(title)
    c_body = composite_score(body_text) if body_text else None
    
    # Use body if available, else headline; body weighted 70/30
    if c_body and c_body["method"] != "no_data":
        compound = 0.30 * c_head["compound"] + 0.70 * c_body["compound"]
    else:
        compound = c_head["compound"]
    
    v_head = c_head.get("vader") or {}
    v_body = c_body.get("vader") if c_body else None
    
    sw = source_weight(source)
    decay = temporal_decay(age_h)
    weight = sw * decay
    contribution = compound * weight
    
    return {
        "title": title[:120],
        "source": source,
        "age_h": round(age_h, 2),
        "source_weight": round(sw, 2),
        "decay": round(decay, 3),
        "effective_weight": round(weight, 3),
        "scoring_method": c_head["method"],
        "vader_headline": v_head,
        "vader_body": v_body,
        "finbert_headline": c_head.get("finbert"),
        "finbert_body": c_body.get("finbert") if c_body else None,
        "vader_compound": round(compound, 4),  # actually composite now, keeping key for back-compat
        "composite_score": round(compound, 4),
        "weighted_contribution": round(contribution, 4),
    }


def aggregate_per_symbol(scored_headlines: list[dict]) -> dict:
    """Aggregate multiple scored headlines into per-symbol anchor."""
    if not scored_headlines:
        return {"anchor_score": 0.0, "n_headlines": 0, "n_recent_24h": 0,
                "top_drivers": [], "method": "no_data"}
    total_weight = sum(h["effective_weight"] for h in scored_headlines)
    if total_weight == 0:
        return {"anchor_score": 0.0, "n_headlines": len(scored_headlines),
                "n_recent_24h": 0, "top_drivers": [], "method": "zero_weight"}
    weighted_sum = sum(h["weighted_contribution"] for h in scored_headlines)
    anchor = weighted_sum / total_weight
    # Top drivers: highest absolute contribution
    top = sorted(scored_headlines, key=lambda h: -abs(h["weighted_contribution"]))[:3]
    recent_24h = [h for h in scored_headlines if h["age_h"] <= 24]
    return {
        "anchor_score": round(anchor, 4),
        "n_headlines": len(scored_headlines),
        "n_recent_24h": len(recent_24h),
        "top_drivers": top,
        "method": "vader_weighted_decayed",
    }


def format_anchor_for_agent_prompt(ticker: str, per_symbol_anchor: dict) -> str:
    """Compact block per ticker — what the LLM sees as the deterministic anchor."""
    n = per_symbol_anchor.get("n_headlines", 0)
    if n == 0:
        return f"  {ticker}: anchor=0.00 (no_data, n=0)"
    anchor = per_symbol_anchor["anchor_score"]
    lines = [f"  {ticker}: anchor={anchor:+.3f} (n={n}, recent24h={per_symbol_anchor.get('n_recent_24h',0)})"]
    for d in per_symbol_anchor.get("top_drivers", [])[:2]:
        lines.append(f"    • [{d['source']}@{d['age_h']}h, vader={d['vader_compound']:+.2f}, wt={d['effective_weight']:.2f}] \"{d['title'][:70]}\"")
    return "\n".join(lines)


if __name__ == "__main__":
    # Self-test
    test_headlines = [
        {"title": "Polymarket Taps Jupiter Exec to Lead Japan Push: Report",
         "feed": "decrypt", "pub_unix": int(time.time()) - 3600},
        {"title": "JUP token plummets as exec departs",  # negative test
         "feed": "cointelegraph", "pub_unix": int(time.time()) - 7200},
        {"title": "Solana ecosystem sees record DeFi growth",
         "feed": "coindesk", "pub_unix": int(time.time()) - 1800},
    ]
    scored = [score_headline(h) for h in test_headlines]
    agg = aggregate_per_symbol(scored)
    print("Per-headline scores:")
    for s in scored:
        print(f"  {s['source']}@{s['age_h']}h vader={s['vader_compound']:+.3f} wt={s['effective_weight']:.2f} contrib={s['weighted_contribution']:+.3f}")
    print(f"\nAggregate anchor: {agg['anchor_score']:+.4f} (n={agg['n_headlines']})")
    print()
    print("Agent-prompt format:")
    print(format_anchor_for_agent_prompt("TEST", agg))
