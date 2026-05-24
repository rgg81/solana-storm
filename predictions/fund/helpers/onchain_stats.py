"""Helius RPC wrapper for the Solana Expert agent.

Provides:
- holder_distribution(mint): top-10 holder concentration
- recent_transfer_volume(mint, hours): whale-size transfer activity proxy

Free Helius tier — no key beyond SOLANA_RPC_URL.
"""
from __future__ import annotations
import os, requests, json, time, sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
RPC_URL = os.environ.get("SOLANA_RPC_URL", "")


def _rpc(method: str, params: list, retries: int = 3) -> dict | None:
    """Helius RPC with retry/backoff. Logs MEDIUM bug if all retries fail."""
    if not RPC_URL: return None
    from predictions.fund import bugs
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1,
                                              "method": method, "params": params},
                               timeout=15)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                time.sleep(0.5 * (2 ** attempt))  # 0.5, 1.0, 2.0s backoff
                continue
            body = r.json()
            if "error" in body:
                err = body["error"].get("message", "")
                if "overload" in err.lower() or "rate" in err.lower():
                    last_err = err
                    time.sleep(1.0 * (2 ** attempt))
                    continue
                last_err = err
                break
            return body.get("result")
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(0.5 * (2 ** attempt))
    bugs.log("MEDIUM", "helius_rpc",
              f"{method} failed after {retries} retries: {last_err}",
              context={"method": method, "last_err": str(last_err)[:200]})
    return None


def holder_distribution(mint: str) -> dict:
    """Top-10 holders + concentration. Falls back to DexScreener pool data if Helius fails."""
    result = _rpc("getTokenLargestAccounts", [mint])
    if not result:
        # Fallback: ask DexScreener for the token's main pool — it shows fdv + supply
        # which we can use for a rough check but not real distribution
        try:
            r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                              headers={"User-Agent": "smaf/1.0"}, timeout=10)
            if r.status_code == 200:
                pairs = r.json().get("pairs") or []
                if pairs:
                    return {"source": "dexscreener_fallback",
                            "helius_status": "rpc_failed",
                            "note": "holder distribution unavailable; using DEX pool as proxy",
                            "primary_pool_liq_usd": float((pairs[0].get("liquidity") or {}).get("usd") or 0)}
        except Exception:
            pass
        return {"error": "rpc_failed", "fallback_attempted": True}
    accs = result.get("value") or []
    if not accs: return {"error": "no_accounts"}
    amounts = [float((a.get("uiAmount") or 0)) for a in accs]
    total_top = sum(amounts)
    if total_top == 0: return {"error": "zero_amounts"}
    supply = _rpc("getTokenSupply", [mint])
    total_supply = float((supply.get("value") or {}).get("uiAmount") or 0) if supply else 0
    if total_supply == 0: return {"error": "no_supply"}
    top1_pct = (amounts[0] / total_supply) * 100 if amounts else 0
    top5_pct = (sum(amounts[:5]) / total_supply) * 100
    top10_pct = (sum(amounts[:10]) / total_supply) * 100
    return {
        "n_top_accounts": len(accs),
        "top_1_pct": top1_pct,
        "top_5_pct": top5_pct,
        "top_10_pct": top10_pct,
        "concentrated": top1_pct > 25 or top10_pct > 60,
        "well_distributed": top10_pct < 40,
        "total_supply": total_supply,
    }


def network_health() -> dict:
    """Solana network: current TPS, slot time, fee."""
    out = {}
    perf = _rpc("getRecentPerformanceSamples", [3])
    if perf and isinstance(perf, list) and perf:
        # Each sample: numTransactions, numSlots, samplePeriodSecs
        avgs = []
        for s in perf:
            if s.get("samplePeriodSecs"):
                tps = (s.get("numTransactions") or 0) / s["samplePeriodSecs"]
                avgs.append(tps)
        out["recent_tps_avg"] = round(sum(avgs) / len(avgs), 1) if avgs else None
    blockheight = _rpc("getBlockHeight", [])
    if blockheight: out["block_height"] = blockheight
    return out


if __name__ == "__main__":
    print("=== holder distribution: JUP ===")
    print(json.dumps(holder_distribution("JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"), indent=2))
    print("\n=== Solana network health ===")
    print(json.dumps(network_health(), indent=2))
