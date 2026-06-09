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

# Repo-root .env (project convention — same file healthcheck.py reads) and a
# gitignored state-file fallback. Either may hold the full Helius RPC URL, e.g.
# https://mainnet.helius-rpc.com/?api-key=KEY
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_RPC_URL_FILE = Path(__file__).resolve().parents[1] / "state" / "helius_rpc_url.txt"


def _is_placeholder(url: str) -> bool:
    """True for an unconfigured template value (e.g. the .env ships with
    SOLANA_RPC_URL=...?api-key=PASTE_YOUR_HELIUS_FREE_KEY). Treated as blind so a
    placeholder never reaches Helius as a bad key."""
    if not url:
        return True
    u = url.upper()
    return ("PASTE" in u or "YOUR_" in u or "<" in url or url.rstrip().endswith("api-key="))


def _read_dotenv_rpc() -> str:
    """Extract the SOLANA_RPC_URL value from repo-root .env (zero-dependency —
    python-dotenv is not installed). Same convention healthcheck.py uses."""
    try:
        if _ENV_FILE.exists():
            for line in _ENV_FILE.read_text().splitlines():
                line = line.strip()
                if line.startswith("SOLANA_RPC_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _get_rpc_url() -> str:
    """Resolve the Helius RPC URL lazily, in precedence order:
    1. SOLANA_RPC_URL env var
    2. repo-root .env (project convention)
    3. gitignored state/helius_rpc_url.txt
    Read at call time (not import) so a freshly-configured URL is picked up on the
    next tick with no code edit. Placeholder values are rejected (→ blind mode)."""
    candidates = [os.environ.get("SOLANA_RPC_URL", "").strip(), _read_dotenv_rpc()]
    try:
        if _RPC_URL_FILE.exists():
            candidates.append(_RPC_URL_FILE.read_text().strip())
    except Exception:
        pass
    for c in candidates:
        if c and not _is_placeholder(c):
            return c
    return ""


_RPC_URL_LOG_THROTTLE_SEC = 60 * 60  # 1h — don't spam the log on cold-config runs
_last_rpc_url_log_ts = 0


def _rpc(method: str, params: list, retries: int = 3) -> dict | None:
    """Helius RPC with retry/backoff. Logs MEDIUM bug if all retries fail OR
    if SOLANA_RPC_URL is unset (the latter is throttled to 1× per hour so we
    don't fire on every per-symbol call inside a single tick — but we still
    surface the misconfig to bugs.jsonl so ops-health can detect it).

    History: when RPC_URL is empty, _rpc historically returned None silently
    so bugs.jsonl never recorded a Helius outage even though every on-chain
    read failed for 100+ ticks (multi-agent review 2026-06-06)."""
    global _last_rpc_url_log_ts
    from predictions.fund import bugs
    rpc_url = _get_rpc_url()
    if not rpc_url:
        now = int(time.time())
        if now - _last_rpc_url_log_ts > _RPC_URL_LOG_THROTTLE_SEC:
            bugs.log("MEDIUM", "helius_rpc.config",
                      "SOLANA_RPC_URL unset and state/helius_rpc_url.txt absent — Helius calls returning None for all symbols",
                      context={"method": method, "throttle_sec": _RPC_URL_LOG_THROTTLE_SEC})
            _last_rpc_url_log_ts = now
        return None
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(rpc_url, json={"jsonrpc": "2.0", "id": 1,
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
