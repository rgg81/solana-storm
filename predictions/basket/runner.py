"""Solana basket strategy runner — Pivot A.

Inverse-vol-weighted equal-weight basket, weekly rebalance, max-DD circuit breaker.

Usage:
    python3 predictions/basket/runner.py snapshot   # capture current prices + basket value
    python3 predictions/basket/runner.py rebalance  # compute new target weights
    python3 predictions/basket/runner.py report     # show paper P&L

State files (gitignored):
    predictions/basket/state/prices.jsonl       # daily price snapshots
    predictions/basket/state/positions.json     # current paper holdings
    predictions/basket/state/trades.jsonl       # rebalance events
"""
from __future__ import annotations
import argparse, json, sys, time, statistics, math
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests
from predictions.basket.universe import UNIVERSE, TICKERS, TICKER_TO_CGID

STATE_DIR = _REPO_ROOT / "predictions" / "basket" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

PRICES_PATH = STATE_DIR / "prices.jsonl"
POSITIONS_PATH = STATE_DIR / "positions.json"
TRADES_PATH = STATE_DIR / "trades.jsonl"
CATALYSTS_PATH = STATE_DIR / "catalysts.jsonl"

INITIAL_DEPOSIT_USD = 1500.0  # ~10 SOL paper budget
MAX_DRAWDOWN_HALT_PCT = -0.25  # halt new buys if basket down 25%+ from peak
REBALANCE_DAYS = 7
VOL_LOOKBACK_DAYS = 30


def fetch_current_prices() -> dict:
    """Use CoinGecko /simple/price — fast, free, no key needed."""
    ids = ",".join(TICKER_TO_CGID[t] for t in TICKERS)
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ids, "vs_currencies": "usd"},
        headers={"User-Agent": "solana-storm-basket/1.0"},
        timeout=20,
    )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    j = r.json()
    out = {"timestamp": int(time.time()), "prices_usd": {}}
    for ticker in TICKERS:
        cg = TICKER_TO_CGID[ticker]
        if cg in j and "usd" in j[cg]:
            out["prices_usd"][ticker] = float(j[cg]["usd"])
    return out


def append_jsonl(path: Path, row: dict) -> None:
    tmp = path.with_suffix(".tmp")
    existing = path.read_text() if path.exists() else ""
    tmp.write_text(existing + json.dumps(row) + "\n")
    tmp.rename(path)


def load_positions() -> dict:
    if not POSITIONS_PATH.exists():
        return {
            "deposit_usd": INITIAL_DEPOSIT_USD,
            "cash_usd": INITIAL_DEPOSIT_USD,
            "holdings": {t: 0.0 for t in TICKERS},  # token-units (not USD)
            "created_at": int(time.time()),
            "rebalance_count": 0,
            "last_rebalance_unix": 0,
            "peak_value_usd": INITIAL_DEPOSIT_USD,
            "halted": False,
        }
    return json.loads(POSITIONS_PATH.read_text())


def save_positions(p: dict) -> None:
    tmp = POSITIONS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, indent=2))
    tmp.rename(POSITIONS_PATH)


def basket_value(positions: dict, prices: dict) -> float:
    """Cash + sum(holdings * price)."""
    val = positions["cash_usd"]
    for t, units in positions["holdings"].items():
        p = prices.get(t, 0)
        val += units * p
    return val


def cmd_snapshot() -> int:
    """Capture current prices + basket value. Run daily."""
    snap = fetch_current_prices()
    if "error" in snap:
        print(f"snapshot: ERROR {snap['error']}")
        return 1
    positions = load_positions()
    val = basket_value(positions, snap["prices_usd"])
    snap["basket_value_usd"] = val
    snap["return_pct"] = val / positions["deposit_usd"] - 1.0
    if val > positions["peak_value_usd"]:
        positions["peak_value_usd"] = val
    drawdown = val / positions["peak_value_usd"] - 1.0
    snap["drawdown_pct"] = drawdown
    if drawdown <= MAX_DRAWDOWN_HALT_PCT and not positions["halted"]:
        positions["halted"] = True
        print(f"snapshot: HALTED — max-DD circuit breaker fired ({drawdown*100:.1f}%)")
    save_positions(positions)
    append_jsonl(PRICES_PATH, snap)
    print(f"snapshot: basket=${val:.2f} ({snap['return_pct']*100:+.2f}% from deposit, "
          f"DD {drawdown*100:+.1f}%, peak ${positions['peak_value_usd']:.2f})")

    # Also run news_check + log catalysts
    import subprocess
    try:
        res = subprocess.run(
            ["python3", str(_REPO_ROOT / "predictions" / "basket" / "news_check.py")],
            capture_output=True, text=True, timeout=60,
        )
        if res.returncode == 0 and res.stdout:
            news = json.loads(res.stdout)
            catalysts = news.get("catalysts") or []
            append_jsonl(CATALYSTS_PATH, {"timestamp": int(time.time()),
                                          "n_catalysts": len(catalysts),
                                          "catalysts": catalysts,
                                          "trending_top_10": news.get("trending_top_10", [])})
            if catalysts:
                print(f"news_check: {len(catalysts)} catalyst(s):")
                for c in catalysts:
                    if c.get("source") == "dexscreener":
                        print(f"  {c['ticker']} [dex]: {', '.join(c['events'])}")
                    elif c.get("source") == "rss":
                        print(f"  {c['ticker']} [news]: {c['events'][0]}")
                        for h in (c.get("headlines") or [])[:2]:
                            print(f"    - [{h['feed']}] {h['title'][:90]}")
                    elif c.get("source") == "cryptopanic":
                        print(f"  {c['ticker']} [cpanic]: {c['events'][0]}")
                        for h in (c.get("headlines") or [])[:2]:
                            print(f"    - {h['title'][:90]}")
                    elif c.get("source") == "coingecko_trending":
                        members = c.get("members", [])
                        print(f"  CG trending hit: {', '.join(m['ticker'] for m in members)}")
            else:
                print(f"news_check: no catalysts")
    except Exception as e:
        print(f"news_check: SKIPPED ({type(e).__name__}: {str(e)[:60]})")
    return 0


def cmd_rebalance() -> int:
    """Compute inverse-vol-weighted target allocation, execute paper rebalance.
    
    Vol is computed from the trailing 30d of snapshots in prices.jsonl.
    If <30 snapshots exist, falls back to equal-weight.
    """
    positions = load_positions()
    if positions["halted"]:
        print("rebalance: skipped (halted)")
        return 0
    # Load recent prices for vol calc
    if not PRICES_PATH.exists():
        snaps = []
    else:
        snaps = [json.loads(l) for l in PRICES_PATH.read_text().strip().splitlines() if l.strip()]
    snaps = sorted(snaps, key=lambda s: s["timestamp"])[-VOL_LOOKBACK_DAYS:]
    
    # Compute per-token vol from snapshot series
    weights = {}
    if len(snaps) >= 5:
        for t in TICKERS:
            ps = [s["prices_usd"].get(t) for s in snaps if t in s.get("prices_usd", {})]
            ps = [p for p in ps if p and p > 0]
            if len(ps) < 3:
                weights[t] = None
                continue
            rs = [ps[i+1]/ps[i] - 1.0 for i in range(len(ps)-1)]
            if len(rs) < 2:
                weights[t] = None
                continue
            sd = statistics.stdev(rs)
            weights[t] = 1.0 / sd if sd > 0 else None
        valid = {k: v for k, v in weights.items() if v is not None}
        if valid:
            tot = sum(valid.values())
            weights = {t: (valid[t]/tot if t in valid else 0) for t in TICKERS}
        else:
            weights = {t: 1.0/len(TICKERS) for t in TICKERS}
    else:
        # Equal weight fallback
        weights = {t: 1.0/len(TICKERS) for t in TICKERS}
        print(f"rebalance: <{VOL_LOOKBACK_DAYS} snapshots ({len(snaps)}), using equal-weight")

    # Get current prices
    snap = fetch_current_prices()
    if "error" in snap:
        print(f"rebalance: ERROR fetching prices: {snap['error']}")
        return 1
    prices = snap["prices_usd"]
    val = basket_value(positions, prices)

    # Compute target USD per token + delta-trades from current holdings
    trades = []
    for t in TICKERS:
        target_usd = val * weights[t]
        current_units = positions["holdings"][t]
        price = prices.get(t)
        if not price:
            continue
        current_usd = current_units * price
        delta_usd = target_usd - current_usd
        delta_units = delta_usd / price
        if abs(delta_usd) < 1.0:  # skip <$1 micro-trades
            continue
        trades.append({"ticker": t, "delta_usd": delta_usd, "delta_units": delta_units,
                        "price": price, "target_pct": weights[t]})
        positions["holdings"][t] += delta_units
        positions["cash_usd"] -= delta_usd

    positions["rebalance_count"] += 1
    positions["last_rebalance_unix"] = int(time.time())
    save_positions(positions)
    append_jsonl(TRADES_PATH, {"timestamp": int(time.time()), "basket_value": val,
                                 "trades": trades, "weights": weights})
    
    print(f"rebalance: basket=${val:.2f}  executed {len(trades)} trades")
    for tr in trades:
        print(f"  {tr['ticker']:<6} target {tr['target_pct']*100:5.1f}%  "
              f"Δ ${tr['delta_usd']:+7.2f} ({tr['delta_units']:+.4f} units @ ${tr['price']:.6g})")
    return 0


def cmd_report() -> int:
    positions = load_positions()
    snap = fetch_current_prices()
    if "error" in snap:
        print(f"report: ERROR {snap['error']}")
        return 1
    val = basket_value(positions, snap["prices_usd"])
    ret = val / positions["deposit_usd"] - 1.0
    dd = val / positions["peak_value_usd"] - 1.0
    days_since = (int(time.time()) - positions["created_at"]) / 86400 if positions["created_at"] else 0
    print(f"# Solana basket — paper P&L report")
    print(f"Deposit:       ${positions['deposit_usd']:.2f}")
    print(f"Current value: ${val:.2f}")
    print(f"Return:        {ret*100:+.2f}%")
    print(f"Annualized:    {((1+ret)**(365/max(1,days_since))*100-100):+.0f}%" if days_since > 0 else "Annualized: N/A")
    print(f"Days running:  {days_since:.1f}")
    print(f"Drawdown:      {dd*100:+.1f}% (peak ${positions['peak_value_usd']:.2f})")
    print(f"Rebalances:    {positions['rebalance_count']}")
    print(f"Halted:        {positions['halted']}")
    print(f"\nHoldings:")
    for t in TICKERS:
        u = positions['holdings'][t]
        p = snap['prices_usd'].get(t, 0)
        v = u * p
        pct = v / val * 100 if val else 0
        print(f"  {t:<6} {u:>14.4f} units  ${v:>8.2f}  ({pct:5.1f}%)")
    print(f"  cash   {' '*14}             ${positions['cash_usd']:>8.2f}  ({positions['cash_usd']/val*100:5.1f}%)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["snapshot", "rebalance", "report"])
    args = p.parse_args()
    return {"snapshot": cmd_snapshot, "rebalance": cmd_rebalance, "report": cmd_report}[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
