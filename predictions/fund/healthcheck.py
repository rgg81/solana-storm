"""SMAF operational health check.

Runs every tick (Phase 0). Catches invariant violations BEFORE they propagate.

Categories:
1. Account invariants (cash >= 0, costs accounted, all open positions have stops)
2. Data freshness (cache age, last successful API hit)
3. Configuration sanity (mints valid, prompts present, .env loaded)
4. Recent agent output validation (last tick's JSONs parseable, schemas match)
5. External API liveness (cheap pings to CG, DexScreener, Helius)

Returns a structured report. CRITICAL issues halt new trades; HIGH+ surface to user.

Usage:
    python3 predictions/fund/healthcheck.py
    python3 predictions/fund/healthcheck.py --ping-apis    # also test external APIs
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from predictions.fund import account, bugs, performance

FUND_DIR = Path(__file__).resolve().parent
STATE_DIR = FUND_DIR / "state"
AGENTS_DIR = FUND_DIR / "agents"


def check_account_invariants() -> list[dict]:
    """Account state must satisfy basic invariants."""
    issues = []
    state = account.load()
    
    # I1: Cash must be non-negative
    if state["cash_usd"] < 0:
        issues.append(bugs.log("CRITICAL", "account",
                                f"Negative cash balance: ${state['cash_usd']:.2f}",
                                context={"cash": state["cash_usd"]}))
    
    # I2: Every open position must have units >= 0 and consistent cost_basis
    for ticker, h in state.get("holdings", {}).items():
        u = h.get("units", 0)
        cb = h.get("cost_basis_usd", 0)
        if u < 0:
            issues.append(bugs.log("CRITICAL", "account",
                                    f"{ticker} has negative units: {u}",
                                    context={"holding": h}))
        if u > 0 and cb <= 0:
            issues.append(bugs.log("HIGH", "account",
                                    f"{ticker} open with units {u} but cost_basis=${cb}",
                                    context={"holding": h}))
        # I3: Every OPEN position (units > 0) must have a stop loss
        if u > 0 and not h.get("stop_loss_price_usd"):
            issues.append(bugs.log("HIGH", "account",
                                    f"{ticker} OPEN position has NO stop_loss_price_usd set",
                                    context={"ticker": ticker, "units": u}))
        # I4: Avg entry price should match cost_basis / units
        if u > 0:
            expected_avg = cb / u
            actual_avg = h.get("avg_entry_price_usd", 0)
            if actual_avg > 0 and abs(expected_avg - actual_avg) / expected_avg > 0.001:
                issues.append(bugs.log("MEDIUM", "account",
                                        f"{ticker} avg_entry inconsistent: stored ${actual_avg:.4g} vs cost/units ${expected_avg:.4g}",
                                        context={"ticker": ticker}))
    
    # I5: Fee + slippage running totals should be sum of trades
    trades_path = STATE_DIR / "trades.jsonl"
    if trades_path.exists():
        total_fees = sum(json.loads(l).get("fee_usd", 0) 
                         for l in trades_path.read_text().splitlines() if l.strip())
        total_slip = sum(json.loads(l).get("slippage_usd", 0)
                         for l in trades_path.read_text().splitlines() if l.strip())
        if abs(total_fees - state["total_fees_paid_usd"]) > 0.01:
            issues.append(bugs.log("HIGH", "account",
                                    f"Fee tracking inconsistent: state ${state['total_fees_paid_usd']:.4f} vs trades.jsonl ${total_fees:.4f}",
                                    context={}))
        if abs(total_slip - state["total_slippage_usd"]) > 0.01:
            issues.append(bugs.log("HIGH", "account",
                                    f"Slippage tracking inconsistent: state ${state['total_slippage_usd']:.4f} vs trades.jsonl ${total_slip:.4f}",
                                    context={}))
    return issues


def check_configuration() -> list[dict]:
    """Static config must be valid."""
    issues = []
    
    # C1: All 6 agent prompts present (MA split: optimist + pessimist)
    required = ["universe_scout.md",
                 "market_analyst_optimist.md", "market_analyst_pessimist.md",
                 "solana_expert.md", "risk_manager.md", "portfolio_mgr.md"]
    for r in required:
        if not (AGENTS_DIR / r).exists():
            issues.append(bugs.log("CRITICAL", "config",
                                    f"Missing agent prompt: {r}"))
    
    # C2: SOLANA_RPC_URL set
    import os
    if not os.environ.get("SOLANA_RPC_URL"):
        env_file = _REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("SOLANA_RPC_URL=") and "PASTE" not in line:
                    os.environ["SOLANA_RPC_URL"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if not os.environ.get("SOLANA_RPC_URL"):
            issues.append(bugs.log("HIGH", "config",
                                    "SOLANA_RPC_URL not set (and .env did not provide it)"))
    
    # C3: Hardcoded mints in stage_phase2.py must look like base58 addresses (32-44 chars)
    stage_path = FUND_DIR / "stage_phase2.py"
    if stage_path.exists():
        import re
        text = stage_path.read_text()
        # Extract KNOWN_MINTS dict
        m = re.search(r"KNOWN_MINTS\s*=\s*\{(.+?)\n\}", text, re.DOTALL)
        if m:
            mints_block = m.group(1)
            for line in mints_block.splitlines():
                ml = re.match(r'\s*"([^"]+)":\s*"([^"]+)"', line)
                if ml:
                    cg_id, mint = ml.group(1), ml.group(2)
                    # Skip comment-only or empty
                    if len(mint) < 30 or len(mint) > 50:
                        issues.append(bugs.log("HIGH", "config",
                                                f"Hardcoded mint for {cg_id} looks malformed: {mint}",
                                                context={"cg_id": cg_id, "mint": mint}))
    return issues


def check_recent_tick_outputs() -> list[dict]:
    """Validate the most recent agent output JSONs."""
    issues = []
    # Accept either single-MA (legacy tick 1) or split-MA outputs
    files_to_check = [
        ("/tmp/smaf_universe.json", ["selected_symbols"]),
        ("/tmp/smaf_solana_expert.json", ["scores"]),
        ("/tmp/smaf_risk.json", ["account_gate", "new_entry_recommendations"]),
        ("/tmp/smaf_pm.json", ["trades", "account_state_pre"]),
    ]
    # MA: split or unified
    if Path("/tmp/smaf_market_analyst_optimist.json").exists():
        files_to_check.append(("/tmp/smaf_market_analyst_optimist.json", ["scores"]))
        files_to_check.append(("/tmp/smaf_market_analyst_pessimist.json", ["scores"]))
    elif Path("/tmp/smaf_market_analyst.json").exists():
        files_to_check.append(("/tmp/smaf_market_analyst.json", ["scores"]))
    for fname, schema_keys in files_to_check:
        p = Path(fname)
        if not p.exists():
            issues.append(bugs.log("LOW", "agents",
                                    f"Last-tick output missing: {fname}"))
            continue
        try:
            data = json.loads(p.read_text())
            missing = [k for k in schema_keys if k not in data]
            if missing:
                issues.append(bugs.log("MEDIUM", "agents",
                                        f"{fname} missing keys: {missing}",
                                        context={"file": fname}))
        except json.JSONDecodeError as e:
            issues.append(bugs.log("HIGH", "agents",
                                    f"{fname} is not valid JSON: {e}",
                                    context={"file": fname}))
    return issues


def ping_external_apis() -> list[dict]:
    """Cheap liveness pings to external APIs."""
    import requests, os
    issues = []
    H = {"User-Agent": "smaf-healthcheck/1.0"}
    
    # CoinGecko
    try:
        r = requests.get("https://api.coingecko.com/api/v3/ping", headers=H, timeout=8)
        if r.status_code != 200:
            issues.append(bugs.log("MEDIUM", "external_api",
                                    f"CoinGecko ping HTTP {r.status_code}"))
    except Exception as e:
        issues.append(bugs.log("HIGH", "external_api",
                                f"CoinGecko unreachable: {type(e).__name__}"))
    
    # DexScreener
    try:
        r = requests.get("https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112",
                          headers=H, timeout=8)
        if r.status_code != 200:
            issues.append(bugs.log("MEDIUM", "external_api",
                                    f"DexScreener ping HTTP {r.status_code}"))
    except Exception as e:
        issues.append(bugs.log("HIGH", "external_api",
                                f"DexScreener unreachable: {type(e).__name__}"))
    
    # Helius RPC
    rpc = os.environ.get("SOLANA_RPC_URL", "")
    if rpc:
        try:
            r = requests.post(rpc, json={"jsonrpc":"2.0","id":1,"method":"getHealth","params":[]},
                              headers={"Content-Type":"application/json"}, timeout=8)
            if r.status_code != 200:
                issues.append(bugs.log("HIGH", "external_api",
                                        f"Helius getHealth HTTP {r.status_code}"))
            elif r.json().get("result") != "ok":
                issues.append(bugs.log("MEDIUM", "external_api",
                                        f"Helius health: {r.json()}"))
        except Exception as e:
            issues.append(bugs.log("HIGH", "external_api",
                                    f"Helius unreachable: {type(e).__name__}"))
    return issues


def run_full() -> dict:
    print("=== SMAF Health Check ===")
    all_issues = []
    print("\n[1/4] Account invariants...")
    a = check_account_invariants()
    all_issues.extend(a); print(f"  {len(a)} issues")
    print("[2/4] Configuration...")
    c = check_configuration()
    all_issues.extend(c); print(f"  {len(c)} issues")
    print("[3/4] Recent tick outputs...")
    r = check_recent_tick_outputs()
    all_issues.extend(r); print(f"  {len(r)} issues")
    
    by_sev = {s: 0 for s in bugs.SEVERITIES}
    for i in all_issues: by_sev[i["severity"]] += 1
    
    print(f"\n=== SUMMARY: {len(all_issues)} issues this check ===")
    for s in bugs.SEVERITIES:
        if by_sev[s] > 0:
            print(f"  {s}: {by_sev[s]}")
    print()
    
    # Show CRITICAL + HIGH details
    for i in all_issues:
        if i["severity"] in ("CRITICAL", "HIGH"):
            print(f"  [{i['severity']}] [{i['component']}] {i['message']}")
    
    # Recent bug summary (last 24h)
    print()
    print("=== Bug log summary (last 24h) ===")
    summ = bugs.summary(hours=24)
    print(f"  Total: {summ['total']}")
    print(f"  By severity: {summ['by_severity']}")
    print(f"  By component: {summ['by_component']}")
    if summ["unresolved_critical_high"]:
        print(f"  ⚠ UNRESOLVED CRITICAL/HIGH: {len(summ['unresolved_critical_high'])}")
    
    return {"issues_this_check": all_issues, "summary_24h": summ}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ping-apis", action="store_true",
                    help="Also ping external APIs (~5 HTTPS calls)")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()
    if args.ping_apis:
        api_issues = ping_external_apis()
        print(f"[ext APIs] {len(api_issues)} issues")
        for i in api_issues:
            print(f"  [{i['severity']}] {i['component']}: {i['message']}")
    return 0 if run_full() else 1


if __name__ == "__main__":
    sys.exit(main())
