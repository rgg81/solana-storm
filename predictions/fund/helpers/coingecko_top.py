"""Fetch the top Solana-ecosystem tokens from CoinGecko.

CoinGecko has a 'solana-ecosystem' category. We pull by market cap descending,
filter for sane liquidity, and return (cg_id, ticker, mint, mcap, vol_24h, change_24h).

No API key needed for /coins/markets endpoint.
"""
from __future__ import annotations
import requests, time, json
from pathlib import Path

HEADERS = {"User-Agent": "solana-storm-fund/1.0"}
CACHE_DIR = Path(__file__).resolve().parents[1] / "state" / "coingecko_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_SEC = 30 * 60


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.json"


def _cached_get(url: str, params: dict, cache_name: str) -> dict | list | None:
    cp = _cache_path(cache_name)
    if cp.exists() and (time.time() - cp.stat().st_mtime) < CACHE_TTL_SEC:
        return json.loads(cp.read_text())
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        body = r.json()
        tmp = cp.with_suffix(".tmp")
        tmp.write_text(json.dumps(body))
        tmp.rename(cp)
        return body
    except Exception:
        return None


def fetch_top_solana(per_page: int = 100, page: int = 1) -> list[dict]:
    """Top tokens in Solana ecosystem, sorted by market cap descending."""
    body = _cached_get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={"vs_currency": "usd", "category": "solana-ecosystem",
                "order": "market_cap_desc", "per_page": per_page, "page": page,
                "sparkline": "false", "price_change_percentage": "1h,24h,7d"},
        cache_name=f"sol_market_p{page}",
    )
    if not body or not isinstance(body, list): return []
    out = []
    for c in body:
        out.append({
            "cg_id": c.get("id"),
            "ticker": (c.get("symbol") or "").upper(),
            "name": c.get("name"),
            "market_cap_usd": c.get("market_cap") or 0,
            "fdv_usd": c.get("fully_diluted_valuation") or 0,
            "volume_24h_usd": c.get("total_volume") or 0,
            "price_usd": c.get("current_price") or 0,
            "change_1h_pct": c.get("price_change_percentage_1h_in_currency"),
            "change_24h_pct": c.get("price_change_percentage_24h_in_currency"),
            "change_7d_pct": c.get("price_change_percentage_7d_in_currency"),
        })
    return out


def fetch_trending() -> list[str]:
    """CoinGecko trending top-15 — returns list of ticker symbols."""
    body = _cached_get(
        "https://api.coingecko.com/api/v3/search/trending",
        params={}, cache_name="trending",
    )
    if not body or "coins" not in body: return []
    return [(it.get("item") or {}).get("symbol", "").upper() for it in body["coins"]]


def fetch_daily_prices(cg_id: str, days: int = 90) -> list[tuple[int, float]]:
    """Daily prices for one token. Returns [(unix_ts, price_usd), ...]."""
    body = _cached_get(
        f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
        params={"vs_currency": "usd", "days": days, "interval": "daily"},
        cache_name=f"chart_{cg_id}_{days}d",
    )
    if not body or "prices" not in body: return []
    return [(int(ts/1000), float(p)) for ts, p in body["prices"]]


def filter_universe(tokens: list[dict],
                     min_mcap: float = 5_000_000,
                     min_vol_24h: float = 200_000,
                     min_vol_to_mcap: float = 0.005,  # 0.5% turnover/day
                     exclude_stables: bool = True,
                     exclude_wrapped: bool = True,
                     exclude_lst: bool = True) -> list[dict]:
    """Apply liquidity + sanity filters. Keeps only Solana-native tradeables."""
    STABLES = {"USDT","USDC","USDS","DAI","BUSD","FDUSD","PYUSD","USDH","USDR","USDY",
               "USDM","USD1","USDE","USDG","SUSDE","SYRUPUSDC","USDTB","USDS","USDD"}
    # Wrapped / cross-chain assets that aren't Solana-native trades
    WRAPPED = {"WBTC","CBBTC","LBTC","XBTC","TBTC","WETH","CETH","WAVAX","WMATIC",
               "WSOL","CSOL","LINK","AAVE","UNI","SHIB","DOGE"}
    # Liquid-staked SOL — these track SOL price, not a separate market
    LST = {"BNSOL","JITOSOL","JLP","INF","MSOL","BSOL","LSSOL","JUPSOL","DSOL","STSOL"}
    seen_tickers = set()
    out = []
    for t in tokens:
        tk = t.get("ticker", "")
        if not tk: continue
        if tk in seen_tickers: continue  # dedup
        if exclude_stables and tk in STABLES: continue
        if exclude_wrapped and tk in WRAPPED: continue
        if exclude_lst and tk in LST: continue
        if t["market_cap_usd"] < min_mcap: continue
        if t["volume_24h_usd"] < min_vol_24h: continue
        # Turnover check — kills illiquid stables that snuck through
        if t["market_cap_usd"] > 0 and t["volume_24h_usd"] / t["market_cap_usd"] < min_vol_to_mcap:
            continue
        seen_tickers.add(tk)
        out.append(t)
    return out


if __name__ == "__main__":
    tops = fetch_top_solana(per_page=50)
    print(f"Fetched {len(tops)} Solana-ecosystem tokens")
    filt = filter_universe(tops)
    print(f"After filter (mcap>=$5M, vol>=$200k, no stables): {len(filt)}")
    print(f"\n{'#':>3} {'ticker':<8} {'mcap_M':>10} {'vol24h_M':>10} {'24h%':>8}")
    for i, t in enumerate(filt[:25], 1):
        ch = t.get("change_24h_pct")
        print(f"{i:>3} {t['ticker']:<8} {t['market_cap_usd']/1e6:>9.1f} {t['volume_24h_usd']/1e6:>9.2f} {(ch if ch else 0):>+7.1f}%")
