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


# A real established token in our universe does not 10x in a day; a |chg| above
# this is a wrong/bridged/mis-reported pool, not a signal. JUP surfaced a
# +529,119% h24 from its deepest Solana pool for 2 consecutive ticks (tick-141/142).
_MAX_PLAUSIBLE_CHG_PCT = 900.0
# A near-total collapse (approaching -100%) in a SHORT window is corruption for our
# established-token universe: no >$300M-mcap Solana token drops ~95%+ in <=6h, and
# such a value is internally inconsistent with a mild h24 (tick-171: JTO phantom pool
# chg_h6 -99.98% while chg_h24 -6.16%). The dexscreener -99.98 sentinel sits inside
# the 900% band, so a dedicated short-window collapse floor is needed to reject it.
_MAX_SHORT_WINDOW_COLLAPSE_PCT = 95.0


def _chg_plausible(chg) -> bool:
    try:
        return abs(float(chg)) <= _MAX_PLAUSIBLE_CHG_PCT
    except (TypeError, ValueError):
        return False


def _short_window_collapse(chg) -> bool:
    """True when a short-window (h1/h6) change is a near-total collapse (|chg| >= 95%)."""
    try:
        return abs(float(chg)) >= _MAX_SHORT_WINDOW_COLLAPSE_PCT
    except (TypeError, ValueError):
        return False


def _pool_chg_plausible(pool: dict) -> bool:
    """True unless ANY present price-change window (h1/h6/h24) is impossible.

    Two corruption signatures are rejected:
    1. Absurd magnitude in any window — |chg| > 900% (tick-155: JUP/PUMP corrupt pools
       had h24 ~+10% but h1 ~+491,000%).
    2. Near-total SHORT-window collapse — |h1| or |h6| >= 95% (tick-171: JTO phantom
       Orca pool chg_h6 -99.98% while chg_h24 -6.16% and $817M wash volume). Such a
       value passes the 900% band yet is physically impossible for an established
       >$300M token and internally inconsistent with a mild h24.

    A MISSING window is not evidence of corruption (don't flag it); only a
    PRESENT-and-absurd value rejects the pool."""
    pc = pool.get("priceChange") or {}
    for window in ("h1", "h6", "h24"):
        v = pc.get(window)
        if v is not None and not _chg_plausible(v):
            return False
    for window in ("h1", "h6"):
        v = pc.get(window)
        if v is not None and _short_window_collapse(v):
            return False
    return True


# A dexscreener pool whose price is wildly off the CoinGecko reference close is a
# wrong/bridged/collision pool, not the real market. The _chg_plausible filter
# above catches absurd CHANGE values but not absurd PRICES with a plausible change
# (tick-152: JUP returned price $517.05 vs CG ~$0.19 — a ~2700x error — with a
# "plausible" -44.75% h24 and 0 buys/56 sells). max_ratio=50 is far outside any
# real cross-venue spread yet well inside genuine pool noise.
_MAX_DEX_PRICE_RATIO = 50.0


def _dex_price_sane(dex_price, ref_price, max_ratio: float = _MAX_DEX_PRICE_RATIO) -> bool:
    """True unless dex_price deviates from ref_price by more than max_ratio (either
    direction). Returns True when either price is missing/zero (no reference =
    cannot judge = don't flag, to avoid false-positive corruption)."""
    try:
        d = float(dex_price); r = float(ref_price)
    except (TypeError, ValueError):
        return True
    if d <= 0 or r <= 0:
        return True
    hi, lo = (d, r) if d >= r else (r, d)
    return (hi / lo) <= max_ratio


def _last_prices_from_history(path: str) -> dict:
    """{symbol: most-recent price_usd} from universe_price_history.jsonl.

    The price-sanity reference (CG OHLC close) is absent whenever a token's daily
    history fails to fetch (latest_close_usd=None) — exactly when a wrong/bridged
    pool with a plausible chg slips through (tick-158: PYTH $188.77, JUP $1038.72).
    The last persisted dexscreener price is ALWAYS available and is the robust
    fallback reference. Later rows overwrite earlier ones, so the final value per
    symbol is the most recent. Missing file / malformed / priceless rows are skipped."""
    out: dict = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                sym = row.get("symbol")
                px = row.get("price_usd")
                if sym and isinstance(px, (int, float)):
                    out[sym] = px
    except FileNotFoundError:
        return {}
    return out


def _build_dex_from_pools(sol_pairs: list, ref_price=None) -> dict | None:
    """Build the per-symbol DEX dict from a token's Solana pairs.

    Prefers the DEEPEST pool that is BOTH change-plausible AND (when a reference
    price is known) price-sane — this rejects a wrong/bridged pool that reports
    impossible % moves (the JUP +529,119% corruption) OR an absurd price with a
    plausible change (tick-158 PYTH $188.77 / JUP $1038.72, where CG OHLC was
    missing so the call-site guard had no reference). Passing ref_price here lets
    the selector RECOVER the real pool instead of merely nulling the token. If
    EVERY pool is implausible, fall back to the deepest pool but NULL the corrupt
    change fields and set chg_corrupt=True so no downstream reads false momentum."""
    if not sol_pairs:
        return None

    def _liq(p):
        return float((p.get("liquidity") or {}).get("usd") or 0)

    def _ok(p):
        if not _pool_chg_plausible(p):
            return False
        if ref_price and not _dex_price_sane(p.get("priceUsd"), ref_price):
            return False
        return True

    plausible = [p for p in sol_pairs if _ok(p)]
    corrupt = not plausible
    best = max(plausible or sol_pairs, key=_liq)
    pc = best.get("priceChange") or {}
    d = {
        "price_usd": float(best.get("priceUsd") or 0),
        "liq_usd": float((best.get("liquidity") or {}).get("usd") or 0),
        "vol_h24": float((best.get("volume") or {}).get("h24") or 0),
        "vol_h1": float((best.get("volume") or {}).get("h1") or 0),
        "chg_h24": None if corrupt else float(pc.get("h24") or 0),
        "chg_h6": None if corrupt else float(pc.get("h6") or 0),
        "chg_h1": None if corrupt else float(pc.get("h1") or 0),
        "buys_h24": int(((best.get("txns") or {}).get("h24") or {}).get("buys") or 0),
        "sells_h24": int(((best.get("txns") or {}).get("h24") or {}).get("sells") or 0),
        "dex": best.get("dexId"),
        "pair_addr": best.get("pairAddress"),
    }
    if corrupt:
        d["chg_corrupt"] = True
    return d


def fetch_dexscreener(mint: str, ref_price=None) -> dict | None:
    """Get the best SOL pool for a mint address with full DEX signals.

    ref_price (CG close or prior-tick price) lets the pool selector reject a
    wrong/bridged price-outlier pool and recover the real one."""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                          headers=HEADERS, timeout=10)
        if r.status_code != 200: return None
        pairs = (r.json().get("pairs") or [])
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        return _build_dex_from_pools(sol_pairs, ref_price=ref_price)
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
    
    from predictions.fund import lessons_io, goals, regime, risk_calibration, reflector
    charter_path = REPO / "predictions" / "fund" / "team_charter.md"
    team_charter = charter_path.read_text() if charter_path.exists() else ""
    output = {
        "phase": "specialists_input",
        "run_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "team_charter": team_charter,
        "universe": [s["ticker"] for s in selected],
        "per_symbol": {},
        "performance_state": performance.format_for_agent_prompt(performance.compute()),
        "lessons_summary": lessons_io.summary_for_agent_prompt(),
        "goal_status": goals.format_for_agent_prompt(),
        "regime_status": regime.format_for_agent_prompt(),
        "risk_calibration": risk_calibration.format_for_agent_prompt(),
        "recent_reflections": reflector.format_for_agent_prompt(max_items=3),
        "network_health": onchain_stats.network_health(),
        "regime_notes": scout_out.get("reasoning", ""),
    }
    
    # Prior-tick prices: robust _dex_price_sane reference when CG OHLC is unavailable.
    _hist_path = str(REPO / "predictions" / "fund" / "state" / "universe_price_history.jsonl")
    last_prices = _last_prices_from_history(_hist_path)

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
            # Price-sanity reference: CG OHLC close, else the prior-tick price (which is
            # always available even when CG OHLC fails — tick-158 PYTH/JUP wrong pools).
            price_ref = per.get("latest_close_usd") or last_prices.get(ticker)
            dex = fetch_dexscreener(mint, ref_price=price_ref)
            if dex and not _dex_price_sane(dex.get("price_usd"), price_ref):
                # Wrong/bridged pool — price absurdly far from the reference.
                # Null it so specialists never score a phantom price (tick-152 JUP $517).
                per["dexscreener_corrupt"] = {
                    "reason": f"price {dex.get('price_usd')} vs ref {price_ref} "
                              f"(>{int(_MAX_DEX_PRICE_RATIO)}x deviation — wrong/bridged pool)",
                    "raw": dex,
                }
                dex = None
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

    def _write_output():
        tmp = out_path.with_suffix(".tmp"); tmp.write_text(json.dumps(output, indent=2, default=str))
        tmp.rename(out_path)

    # Persist the core data (dex / holders / indicators / rss) BEFORE the sentiment
    # step. FinBERT/torch can SEGFAULT (C-extension crash, exit 139) — a segfault is
    # NOT a Python exception, so the try/except below cannot catch it and the process
    # dies before any post-sentiment write. Writing first guarantees the staged file
    # is always fresh even if sentiment crashes (the specialists treat sentiment as
    # one optional input; stale dex data would be a correctness failure).
    output["sentiment_anchors"] = {}
    output["sentiment_anomalies"] = {}
    output["sentiment_anchor_block"] = "SENTIMENT_ANCHOR: pending"
    _write_output()

    # === Build sentiment anchors (VADER + FinBERT + source-weight + decay) ===
    try:
        from predictions.fund import sentiment_pipeline
        anchors, anomalies = sentiment_pipeline.build_anchors_from_phase2(output, max_body_fetches=6)
        output["sentiment_anchors"] = anchors
        output["sentiment_anomalies"] = anomalies
        output["sentiment_anchor_block"] = sentiment_pipeline.format_for_agent_prompt(anchors, anomalies)
        _write_output()  # re-write with the sentiment block added
    except Exception as e:
        output["sentiment_anchor_block"] = f"SENTIMENT_ANCHOR: failed ({type(e).__name__}: {str(e)[:80]})"
        _write_output()
    
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
