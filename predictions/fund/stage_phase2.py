"""Stage Phase-2 inputs for Market Analyst + Solana Expert subagents.

Reads /tmp/smaf_universe.json (Scout's output).
Fetches per-symbol:
- 90d daily prices → indicators
- DexScreener live (price, vol, buy/sell skew)
- Mint address (from CoinGecko platforms.solana)
- News mentions (RSS + CryptoPanic)
- Helius holder distribution (Solana Expert input)

Writes one merged JSON to predictions/fund/state/tick_phase2_input.json
"""
from __future__ import annotations
import json, sys, time, re
from pathlib import Path
import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from predictions.fund import account, performance
from predictions.fund.helpers import coingecko_top, indicators, onchain_stats

HEADERS = {"User-Agent": "smaf/1.0"}


def fetch_dexscreener(mint: str) -> dict | None:
    """Get top SOL pool for a mint address with full DEX signals."""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                          headers=HEADERS, timeout=10)
        if r.status_code != 200: return None
        pairs = (r.json().get("pairs") or [])
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs: return None
        best = max(sol_pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        return {
            "price_usd": float(best.get("priceUsd") or 0),
            "liq_usd": float((best.get("liquidity") or {}).get("usd") or 0),
            "vol_h24": float((best.get("volume") or {}).get("h24") or 0),
            "vol_h1": float((best.get("volume") or {}).get("h1") or 0),
            "chg_h24": float((best.get("priceChange") or {}).get("h24") or 0),
            "chg_h6": float((best.get("priceChange") or {}).get("h6") or 0),
            "chg_h1": float((best.get("priceChange") or {}).get("h1") or 0),
            "buys_h24": int(((best.get("txns") or {}).get("h24") or {}).get("buys") or 0),
            "sells_h24": int(((best.get("txns") or {}).get("h24") or {}).get("sells") or 0),
            "dex": best.get("dexId"),
            "pair_addr": best.get("pairAddress"),
        }
    except Exception:
        return None


KNOWN_MINTS = {
    "wrapped-solana":          "So11111111111111111111111111111111111111112",
    "jupiter-exchange-solana": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "render-token":            "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
    "pyth-network":            "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "grass":                   "Grass7B4RdKfBCjTKgSqnXkqjwiGvQyFbuSCUJr3XXjs",
    "pump-fun":                "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",
    "pudgy-penguins":          "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv",
    "bonk":                    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "official-trump":          "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",
    "doublezero":              "J6pQQ3FAcJQeWPPGppWRb4nM8jU3wLyYbRrLh7feMfvd",  # 2Z
    "jito-governance-token":   "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "raydium":                 "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
    "dogwifcoin":              "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "virtual-protocol":        "3iQL8BFS2vE7mww4ehAqQHAsbmRNCrPxizWAT2Zfyr9y",  # bridged
}

def fetch_mint(cg_id: str) -> str | None:
    """Get a token's Solana mint address. Hardcoded for common ones; falls back to CG."""
    if cg_id in KNOWN_MINTS:
        return KNOWN_MINTS[cg_id]
    try:
        r = requests.get(f"https://api.coingecko.com/api/v3/coins/{cg_id}",
                          params={"localization":"false","tickers":"false","market_data":"false",
                                  "community_data":"false","developer_data":"false","sparkline":"false"},
                          headers=HEADERS, timeout=15)
        if r.status_code != 200: return None
        plats = (r.json().get("platforms") or {})
        return plats.get("solana") or None
    except Exception:
        return None


def main():
    scout_out = json.loads(Path("/tmp/smaf_universe.json").read_text())
    selected = scout_out.get("selected_symbols") or []
    print(f"=== Staging phase-2 inputs for {len(selected)} symbols ===")
    
    # Load current account state for position review
    acct_state = account.load()
    open_positions_raw = {t: h for t, h in acct_state["holdings"].items() 
                           if h.get("units", 0) > 0}
    
    # Build cgid lookup from cached top-Solana
    tops = coingecko_top.fetch_top_solana(per_page=100)
    cgid = {t["ticker"]: t["cg_id"] for t in tops}
    
    from predictions.fund import lessons_io
    output = {
        "phase": "specialists_input",
        "run_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "universe": [s["ticker"] for s in selected],
        "per_symbol": {},
        "performance_state": performance.format_for_agent_prompt(performance.compute()),
        "lessons_summary": lessons_io.summary_for_agent_prompt(),
        "network_health": onchain_stats.network_health(),
        "regime_notes": scout_out.get("reasoning", ""),
    }
    
    for s in selected:
        ticker = s["ticker"]
        cg = cgid.get(ticker)
        print(f"  [{ticker}] cg_id={cg}...", end=" ", flush=True)
        per = {"ticker": ticker, "bucket": s.get("bucket"), "reason_for_selection": s.get("reason")}
        
        # 90d prices → indicators
        if cg:
            prices = coingecko_top.fetch_daily_prices(cg, days=90)
            if prices and len(prices) >= 30:
                ps = [p for _, p in prices]
                per["indicators"] = indicators.summarize(ps)
                per["latest_close_usd"] = ps[-1]
            else:
                per["indicators"] = {"insufficient_data": True}
        
        # Mint address (needed for DexScreener + Helius)
        mint = fetch_mint(cg) if cg else None
        per["mint"] = mint
        
        # DexScreener live (only if mint known)
        if mint:
            dex = fetch_dexscreener(mint)
            if dex: 
                per["dexscreener"] = dex
                # Buy/sell skew derived
                total_txns = dex["buys_h24"] + dex["sells_h24"]
                if total_txns > 0:
                    per["buy_skew_pct"] = round(dex["buys_h24"]/total_txns*100, 1)
        
        # Helius holder distribution (Solana Expert input)
        if mint:
            hd = onchain_stats.holder_distribution(mint)
            per["holder_distribution"] = hd
        
        output["per_symbol"][ticker] = per
        time.sleep(3.0)  # CG free tier ~10-30/min; we make 2 calls/token so spread out
        print(f"OK")
    
    # ===== Position review block (CRITICAL — Risk Mgr + PM will act on this) =====
    positions_review = []
    stop_triggers_now = []
    for t, h in open_positions_raw.items():
        per = output["per_symbol"].get(t, {})
        cur_price = per.get("dexscreener", {}).get("price_usd") or per.get("latest_close_usd")
        avg_entry = h.get("avg_entry_price_usd", 0)
        units = h.get("units", 0)
        mv = (units * cur_price) if (cur_price and units) else 0
        cost = h.get("cost_basis_usd", 0)
        unrealized_pnl_usd = mv - cost
        unrealized_pnl_pct = (unrealized_pnl_usd / cost) if cost > 0 else 0
        days_held = (int(time.time()) - h.get("first_buy_unix", int(time.time()))) / 86400
        sl = h.get("stop_loss_price_usd")
        tp = h.get("take_profit_price_usd")
        peak = h.get("peak_price_since_entry", 0)
        # Trigger detection (CRITICAL SIM STEP — between-tick price moves)
        triggered = None
        if cur_price and sl and cur_price <= sl:
            triggered = {"type": "stop_loss", "level_usd": sl,
                          "fill_assumption": "stop level (Jupiter limit-stop simulation)",
                          "realized_pct_at_fill": (sl / avg_entry - 1.0) if avg_entry else None}
            stop_triggers_now.append({"ticker": t, **triggered})
        elif cur_price and tp and cur_price >= tp:
            triggered = {"type": "take_profit", "level_usd": tp,
                          "fill_assumption": "tp level",
                          "realized_pct_at_fill": (tp / avg_entry - 1.0) if avg_entry else None}
            stop_triggers_now.append({"ticker": t, **triggered})
        positions_review.append({
            "ticker": t,
            "units": units, "avg_entry_price_usd": avg_entry, "current_price_usd": cur_price,
            "market_value_usd": mv, "cost_basis_usd": cost,
            "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct * 100, 2),
            "days_held": round(days_held, 2),
            "stop_loss_price_usd": sl, "take_profit_price_usd": tp,
            "peak_price_since_entry": peak,
            "distance_to_stop_pct": round((sl / cur_price - 1) * 100, 2) if (sl and cur_price) else None,
            "distance_to_tp_pct": round((tp / cur_price - 1) * 100, 2) if (tp and cur_price) else None,
            "triggered_this_tick": triggered,
        })
    output["open_positions_review"] = positions_review
    output["stop_triggers_this_tick"] = stop_triggers_now
    output["simulation_assumptions"] = {
        "intra_tick_price_path": "unknown — only snapshot at tick boundary visible",
        "stop_loss_fill_assumption": "fill at stop level (simulates Jupiter limit-stop)",
        "take_profit_fill_assumption": "fill at TP level",
        "cadence_hours": 8,
        "between_tick_risk": "stops set at tick T may be breached and refilled before tick T+1; we only see net result",
    }
    
    # News mentions (one batch query for all tickers)
    try:
        from predictions.basket.rss_news import find_basket_mentions
        # Hack: temporarily override SEARCH_TERMS for our dynamic universe
        from predictions.basket import rss_news as rss
        # Extended search terms — match by ticker AND project name where known
        TICKER_NAMES = {
            "RENDER": ["RENDER", "Render Network"], "JUP": ["JUP", "Jupiter Exchange", "Jupiter"],
            "JTO": ["JTO", "Jito"], "RAY": ["RAY", "Raydium"], "ORCA": ["ORCA", "Orca"],
            "PYTH": ["PYTH", "Pyth Network", "Pyth"], "BONK": ["BONK"],
            "WIF": ["WIF", "dogwifhat"], "PENGU": ["PENGU", "Pudgy Penguins"],
            "POPCAT": ["POPCAT"], "MEW": ["MEW"], "DRIFT": ["DRIFT", "Drift Protocol"],
            "SOL": ["SOL", "Solana"], "GRASS": ["GRASS"], "PUMP": ["PUMP", "pump.fun"],
            "TRUMP": ["TRUMP"], "VIRTUAL": ["VIRTUAL", "Virtual Protocol"],
            "2Z": ["2Z", "DoubleZero"],
        }
        dynamic_terms = {}
        for s in selected:
            t = s["ticker"]
            names = TICKER_NAMES.get(t, [t])
            dynamic_terms[t] = [fr"\b{re.escape(n)}\b" for n in names]
        # save & restore
        original = rss.SEARCH_TERMS
        rss.SEARCH_TERMS = dynamic_terms
        try:
            rss_out = find_basket_mentions(hours=168)
            output["rss_news"] = {
                "total_items_in_window": rss_out.get("items_in_window", 0),
                "mentions_per_ticker": rss_out.get("matches_per_ticker", {}),
                "headlines": {t: [{"feed": h["feed"], "title": h["title"], "pub_unix": h["pub_unix"]} 
                                   for h in (rss_out.get("matches") or {}).get(t, [])[:3]] 
                              for t in dynamic_terms},
            }
        finally:
            rss.SEARCH_TERMS = original
    except Exception as e:
        output["rss_news"] = {"error": str(e)[:80]}
    
    out_path = REPO / "predictions" / "fund" / "state" / "tick_phase2_input.json"
    tmp = out_path.with_suffix(".tmp"); tmp.write_text(json.dumps(output, indent=2, default=str))
    tmp.rename(out_path)
    
    # Summary
    print()
    print(f"Per-symbol data coverage:")
    for t in output["universe"]:
        s = output["per_symbol"][t]
        flags = []
        if s.get("indicators") and not s["indicators"].get("insufficient_data"): flags.append("ind")
        if s.get("dexscreener"): flags.append("dex")
        if s.get("holder_distribution") and not s["holder_distribution"].get("error"): flags.append("hold")
        print(f"  {t:<8} {','.join(flags) if flags else 'NO_DATA'}")
    rss_hits = output.get("rss_news", {}).get("mentions_per_ticker") or {}
    rss_total = sum(rss_hits.values()) if isinstance(rss_hits, dict) else 0
    print(f"\nRSS mentions total: {rss_total} ({rss_hits})")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
