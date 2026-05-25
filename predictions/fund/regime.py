"""Market regime detection for SMAF.

Three regime axes:
  E.1 SOL trend: above/below 200d SMA → bull/bear macro
  E.2 SOL vol: 30d daily vol → calm/normal/turbulent
  F.  Cross-token correlation: mean pairwise corr → diversified/concentrated risk

Combined regime classification → posture recommendation.

Surfaced in agent prompts as `regime_status` block. Risk Mgr's hard rules
adapt per regime (max position, BUY floor, stop minimums, gross deployment).
"""
from __future__ import annotations
import json, time, sys, statistics, math
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REGIME_CACHE = Path(__file__).resolve().parent / "state" / "regime_cache.json"
CACHE_TTL = 30 * 60  # 30 min


def _cached_get(name: str, fetch_fn, ttl: int = CACHE_TTL):
    REGIME_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if REGIME_CACHE.exists():
        try:
            cache = json.loads(REGIME_CACHE.read_text())
            if cache.get(name) and (time.time() - cache[name].get("_t", 0)) < ttl:
                return cache[name]["data"]
        except Exception:
            cache = {}
    else:
        cache = {}
    data = fetch_fn()
    if data is not None:
        cache[name] = {"_t": int(time.time()), "data": data}
        REGIME_CACHE.write_text(json.dumps(cache))
    return data


def fetch_sol_daily_200d() -> list[float] | None:
    """200d daily close prices for SOL from CoinGecko."""
    def _fetch():
        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/coins/wrapped-solana/market_chart",
                params={"vs_currency": "usd", "days": 200, "interval": "daily"},
                headers={"User-Agent": "smaf/1.0"}, timeout=20)
            if r.status_code != 200: return None
            prices = (r.json().get("prices") or [])
            return [float(p[1]) for p in prices]
        except Exception:
            return None
    return _cached_get("sol_daily_200d", _fetch)


def fetch_universe_30d_prices() -> dict | None:
    """30d daily close per token in our basket (for correlation calc)."""
    def _fetch():
        from predictions.fund.helpers import coingecko_top
        out = {}
        # Use a fixed 'core' subset for correlation (avoid CG rate limits)
        TICKERS = ["jupiter-exchange-solana", "jito-governance-token", "raydium",
                   "pyth-network", "bonk", "dogwifcoin", "pudgy-penguins"]
        for cg_id in TICKERS:
            prices = coingecko_top.fetch_daily_prices(cg_id, days=30)
            if prices and len(prices) >= 15:
                out[cg_id] = [p for _, p in prices]
        return out
    return _cached_get("universe_30d_prices", _fetch)


def detect_sol_regime() -> dict:
    """Trend + vol regime for SOL (the universe anchor)."""
    prices = fetch_sol_daily_200d()
    if not prices or len(prices) < 50:
        return {"sol_trend": "unknown", "sol_vol_regime": "unknown",
                 "sol_current": None, "sol_sma200": None, "sol_30d_vol_pct": None,
                 "data_status": "insufficient"}
    cur = prices[-1]
    sma200 = sum(prices[-200:]) / min(200, len(prices))
    sma50 = sum(prices[-50:]) / 50
    # Trend
    if cur > sma200 and cur > sma50:
        trend = "strong_bull"
    elif cur > sma200:
        trend = "bull"
    elif cur < sma200 and cur < sma50:
        trend = "strong_bear"
    else:
        trend = "bear"
    # Vol (30d daily stdev of log-returns)
    rets = []
    for i in range(1, min(31, len(prices))):
        if prices[-i-1] > 0:
            rets.append(math.log(prices[-i] / prices[-i-1]))
    vol_30d = statistics.stdev(rets) if len(rets) > 5 else None
    vol_pct = (vol_30d * 100) if vol_30d else None
    if vol_pct is None:
        vol_regime = "unknown"
    elif vol_pct < 2.5: vol_regime = "calm"
    elif vol_pct < 5.0: vol_regime = "normal"
    elif vol_pct < 8.0: vol_regime = "turbulent"
    else: vol_regime = "chaotic"
    return {
        "sol_trend": trend, "sol_vol_regime": vol_regime,
        "sol_current": round(cur, 2), "sol_sma200": round(sma200, 2),
        "sol_sma50": round(sma50, 2),
        "sol_30d_vol_pct": round(vol_pct, 2) if vol_pct else None,
        "data_status": "ok",
    }


def detect_correlation_regime() -> dict:
    """Cross-token mean pairwise correlation over 30d."""
    prices = fetch_universe_30d_prices()
    if not prices or len(prices) < 3:
        return {"mean_pairwise_corr": None, "corr_regime": "unknown",
                 "n_tokens": 0, "data_status": "insufficient"}
    
    # Compute log-returns per token, align by min length
    rets = {}
    min_len = min(len(p) for p in prices.values())
    for cg, ps in prices.items():
        ps = ps[-min_len:]
        rets[cg] = [math.log(ps[i+1]/ps[i]) for i in range(len(ps)-1) if ps[i] > 0]
    
    # Compute pearson correlation for each pair
    def corr(a, b):
        n = min(len(a), len(b))
        if n < 5: return None
        a, b = a[-n:], b[-n:]
        ma = sum(a)/n; mb = sum(b)/n
        sa = (sum((x-ma)**2 for x in a)/n)**0.5
        sb = (sum((x-mb)**2 for x in b)/n)**0.5
        if sa == 0 or sb == 0: return None
        cov = sum((a[i]-ma)*(b[i]-mb) for i in range(n)) / n
        return cov / (sa * sb)
    
    pairs = list(rets.keys())
    corrs = []
    for i in range(len(pairs)):
        for j in range(i+1, len(pairs)):
            c = corr(rets[pairs[i]], rets[pairs[j]])
            if c is not None: corrs.append(c)
    
    if not corrs:
        return {"mean_pairwise_corr": None, "corr_regime": "unknown",
                 "n_tokens": len(pairs), "data_status": "no_corrs"}
    
    mean_c = sum(corrs) / len(corrs)
    if mean_c > 0.75: regime = "highly_correlated"
    elif mean_c > 0.55: regime = "elevated_correlation"
    elif mean_c > 0.30: regime = "moderate_correlation"
    else: regime = "diversified"
    return {
        "mean_pairwise_corr": round(mean_c, 3),
        "corr_regime": regime,
        "n_tokens": len(pairs),
        "n_pairs": len(corrs),
        "data_status": "ok",
    }


def combined_regime() -> dict:
    """Roll-up: SOL trend + vol + correlation → posture."""
    sol = detect_sol_regime()
    cor = detect_correlation_regime()
    
    # Posture rules — encode regime impact on Risk Mgr
    risk_adjustments = {
        "max_position_pct_multiplier": 1.0,
        "buy_floor_adjustment": 0.0,
        "max_deployed_multiplier": 1.0,
        "stop_min_pct_floor": -0.08,
    }
    notes = []
    if sol["sol_trend"] in ("strong_bear", "bear"):
        risk_adjustments["max_position_pct_multiplier"] *= 0.6
        risk_adjustments["buy_floor_adjustment"] += 0.05
        notes.append(f"SOL {sol['sol_trend']} → smaller positions, higher BUY bar")
    if sol["sol_vol_regime"] in ("turbulent", "chaotic"):
        risk_adjustments["max_position_pct_multiplier"] *= 0.7
        risk_adjustments["stop_min_pct_floor"] = -0.10  # widen stop floor
        notes.append(f"SOL vol {sol['sol_vol_regime']} ({sol['sol_30d_vol_pct']}%) → wider stops + smaller size")
    if cor["corr_regime"] in ("highly_correlated", "elevated_correlation"):
        risk_adjustments["max_deployed_multiplier"] *= 0.5
        notes.append(f"corr {cor['corr_regime']} ({cor['mean_pairwise_corr']}) → halve gross deployment")
    if sol["sol_trend"] == "strong_bull" and sol["sol_vol_regime"] in ("calm", "normal"):
        notes.append("favorable regime — standard discipline")
    
    return {
        "sol": sol, "correlation": cor,
        "risk_adjustments": risk_adjustments,
        "notes": notes,
    }


def format_for_agent_prompt() -> str:
    r = combined_regime()
    sol = r["sol"]; cor = r["correlation"]
    lines = ["REGIME_STATUS:"]
    if sol.get("data_status") == "ok":
        lines.append(f"  SOL trend: {sol['sol_trend']} (price ${sol['sol_current']} vs SMA200 ${sol['sol_sma200']})")
        lines.append(f"  SOL 30d vol: {sol['sol_30d_vol_pct']}% daily → {sol['sol_vol_regime']}")
    else:
        lines.append(f"  SOL regime: {sol.get('data_status', 'unknown')}")
    if cor.get("data_status") == "ok":
        lines.append(f"  Universe correlation: {cor['mean_pairwise_corr']} ({cor['n_pairs']} pairs) → {cor['corr_regime']}")
    if r["notes"]:
        lines.append("  Risk adjustments active:")
        for n in r["notes"]: lines.append(f"    • {n}")
    else:
        lines.append("  No regime-based adjustments active")
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_for_agent_prompt())
    print()
    print(json.dumps(combined_regime(), indent=2, default=str))
