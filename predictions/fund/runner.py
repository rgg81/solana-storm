"""Solana Multi-Agent Fund orchestrator — 5-agent, 8h cadence.

Usage:
    python3 predictions/fund/runner.py prepare    # do Phase 0-1: account refresh + universe scout data
    python3 predictions/fund/runner.py status     # show account + performance + open positions
    python3 predictions/fund/runner.py mark       # just mark-to-market

The full multi-agent dispatch (Phases 2-5) is driven by the skill (Agent tool calls).
Runner stages the data + writes per-phase artifacts to /tmp/fund_tick_<phase>.json
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from predictions.fund import account, performance, fees_model
from predictions.fund.helpers import coingecko_top, indicators

STATE_DIR = Path(__file__).resolve().parent / "state"
PROMPTS_DIR = Path(__file__).resolve().parent / "agents"

def mark_to_market_all(force_refresh: bool = False) -> dict:
    """Refresh prices for all open holdings + universe candidates.
    Returns: prices dict {ticker: price_usd} + mtm summary."""
    state = account.load()
    held = [t for t, h in state["holdings"].items() if h.get("units", 0) > 0]
    # For mark-to-market we only need PRICES — universe will fetch full data
    prices = {}
    # Use CoinGecko simple/price for known tickers (cheap, no key)
    if held:
        import requests
        # Build cg_id lookup from CoinGecko top fetch (cached)
        tops = coingecko_top.fetch_top_solana(per_page=100)
        cgid_by_ticker = {t["ticker"]: t["cg_id"] for t in tops}
        ids = [cgid_by_ticker.get(t) for t in held if cgid_by_ticker.get(t)]
        ids = [i for i in ids if i]
        if ids:
            try:
                r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                                  params={"ids": ",".join(ids), "vs_currencies": "usd"},
                                  headers={"User-Agent": "smaf/1.0"}, timeout=15)
                if r.status_code == 200:
                    j = r.json()
                    for ticker in held:
                        cg = cgid_by_ticker.get(ticker)
                        if cg and cg in j: prices[ticker] = float(j[cg]["usd"])
            except Exception as e:
                print(f"  mtm prices fetch failed: {e}")
    mtm = account.mark_to_market(state, prices)
    # Check stops
    triggered = account.check_stop_triggers(state, prices) if prices else []
    account.save(state)
    account.snapshot_equity(state, mtm)
    return {"prices": prices, "mtm": mtm, "stop_triggers": triggered}


def stage_universe(out_path: Path) -> dict:
    """Phase 1 prep: assemble universe candidates + lessons + perf state.
    Writes a single JSON for the Universe Scout subagent."""
    state = account.load()
    held = sorted([t for t, h in state["holdings"].items() if h.get("units", 0) > 0])
    
    # CoinGecko top-50 Solana, filtered
    tops = coingecko_top.fetch_top_solana(per_page=50)
    filtered = coingecko_top.filter_universe(tops, min_mcap=5_000_000, min_vol_24h=200_000)
    trending = coingecko_top.fetch_trending()
    
    # DexScreener boosts (Solana only)
    boosts = []
    try:
        import requests
        r = requests.get("https://api.dexscreener.com/token-boosts/top/v1",
                          headers={"User-Agent": "smaf/1.0"}, timeout=10)
        if r.status_code == 200:
            j = r.json()
            if isinstance(j, list):
                boosts = [{"address": b.get("tokenAddress",""), "amount": b.get("amount")}
                          for b in j if b.get("chainId") == "solana"][:10]
    except Exception:
        pass
    
    # Performance state
    perf = performance.compute()
    
    payload = {
        "phase": "universe_scout_input",
        "run_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "current_holdings": held,
        "candidates": [{"ticker": t["ticker"], "name": t["name"],
                         "market_cap_usd": t["market_cap_usd"],
                         "volume_24h_usd": t["volume_24h_usd"],
                         "change_1h_pct": t["change_1h_pct"],
                         "change_24h_pct": t["change_24h_pct"],
                         "change_7d_pct": t["change_7d_pct"]}
                        for t in filtered[:30]],
        "cg_trending": trending[:15],
        "dex_boosts_sol": boosts,
        "performance_state": performance.format_for_agent_prompt(perf),
        "performance_state_raw": perf,
        "fund_account": {
            "deposit_usd": state["deposit_usd"],
            "cash_usd": state["cash_usd"],
            "n_positions": sum(1 for h in state["holdings"].values() if h.get("units", 0) > 0),
            "halted": state.get("halted", False),
        },
    }
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.rename(out_path)
    return payload


def execute_pm_orders(pm_output: dict, prices: dict | None = None) -> dict:
    """Execute Portfolio Manager orders against the account.
    
    Maps PM's `stop_loss_price_usd` / `take_profit_price_usd` keys to
    account.set_risk_levels' `stop_loss_price` / `take_profit_price` kwargs.
    Logs any wiring issues to bugs.jsonl.
    """
    from predictions.fund import account as acct, fees_model, bugs
    state = acct.load()
    
    # Get fee/slippage estimates from per-symbol input (cached from this tick)
    risk_in_path = STATE_DIR / "tick_risk_input.json"
    per_sym = {}
    if risk_in_path.exists():
        per_sym = json.loads(risk_in_path.read_text()).get("specialist_consensus_per_symbol", {})
    
    results = []
    for trade in pm_output.get("trades", []):
        ticker = trade.get("ticker")
        side = trade.get("side")
        # PM may use usd_amount or usd_amount_gross (sell-side) — accept both
        usd = float(trade.get("usd_amount") or trade.get("usd_amount_gross") or 0)
        price = float(trade.get("price_usd") or 0)
        if not (ticker and side and usd > 0 and price > 0):
            bugs.log("HIGH", "execution",
                      f"Malformed PM trade order: ticker={ticker} side={side} usd={usd} price={price}",
                      context={"keys": list(trade.keys())})
            results.append({"trade": trade, "result": {"executed": False, "reason": "MALFORMED"}})
            continue
        
        # Recompute fees from current pool liquidity
        liq = per_sym.get(ticker, {}).get("liq_usd_main_pool", 0)
        est = fees_model.estimate(usd, liq)
        if est.slippage_pct > 0.015:  # 1.5% hard limit
            bugs.log("HIGH", "execution",
                      f"{ticker} {side} skipped: slippage {est.slippage_pct*100:.2f}% > 1.5% cap",
                      context={"ticker": ticker, "usd": usd, "liq": liq})
            results.append({"trade": trade, "result": {"executed": False, "reason": "SLIPPAGE_CAP"}})
            continue
        
        # Snapshot entry_consensus BEFORE execution (so we can audit-close even on full liquidation)
        pre_holdings_snapshot = dict(state["holdings"].get(ticker, {}))
        
        result = acct.execute_trade(state, ticker, side, usd, price,
                                      est.fee_pct, est.slippage_pct,
                                      reason=trade.get("reason", "pm")[:200])
        # Item K — slippage auto-calibration: compare est vs realized
        # (For paper mode, realized = estimated; this becomes useful with live execution)
        try:
            from predictions.fund import risk_calibration
            est_slip = est.slippage_pct
            # In paper mode, we use est as realized — no calibration signal.
            # When live: compute realized_slip from actual fill quote vs DexScreener mid-price
            # risk_calibration.update_slippage_calibration(est_slip, realized_slip)
        except Exception:
            pass
        
        # If this was a SELL that closed the position (units → 0), audit it
        if side == "sell" and result.get("executed"):
            post_units = state["holdings"].get(ticker, {}).get("units", 0)
            if post_units < 1e-4 and pre_holdings_snapshot.get("entry_consensus"):  # tolerance for float dust
                try:
                    from predictions.fund import audit as audit_mod
                    realized = result.get("realized_pnl_usd")
                    if realized is None:
                        # Estimate: gross proceeds - cost basis (since execute_trade already netted fees)
                        realized = (usd - (pre_holdings_snapshot.get("cost_basis_usd", 0)))
                    audit_event = audit_mod.audit_close(
                        ticker=ticker,
                        entry_consensus=pre_holdings_snapshot["entry_consensus"],
                        realized_pnl_usd=realized,
                        cost_basis_usd=pre_holdings_snapshot.get("cost_basis_usd", 0),
                        exit_reason=trade.get("reason", "manual")[:60],
                    )
                    print(f"  📋 audit_close({ticker}): realized {audit_event['realized_pct']:+.2f}%, "
                          f"Opt {'✓' if audit_event['specialists_correct']['market_analyst_optimist']['correct'] else '✗'}, "
                          f"Pes {'✓' if audit_event['specialists_correct']['market_analyst_pessimist']['correct'] else '✗'}")
                except Exception as e:
                    bugs.log("HIGH", "audit",
                              f"{ticker} audit_close failed: {e}",
                              context={"ticker": ticker})
        
        # Set stops on BUY orders + snapshot entry consensus for audit later
        if side == "buy" and result.get("executed"):
            # Map PM's _usd-suffixed keys to set_risk_levels' kwarg names
            sl = trade.get("stop_loss_price_usd") or trade.get("stop_loss_usd")
            tp = trade.get("take_profit_price_usd") or trade.get("take_profit_usd")
            if not sl:
                bugs.log("HIGH", "execution",
                          f"{ticker} BUY executed but PM provided NO stop_loss — risk-management gap",
                          context=trade)
            else:
                ok = acct.set_risk_levels(state, ticker, stop_loss_price=sl,
                                            take_profit_price=tp, set_by="pm_execute")
                if not ok:
                    bugs.log("HIGH", "execution",
                              f"{ticker} set_risk_levels failed (no units?)",
                              context=trade)
            # Snapshot entry consensus from the tick's risk input — needed for audit on close
            try:
                from predictions.fund import audit as audit_mod
                spec = per_sym.get(ticker, {}) if per_sym else {}
                deposit = float(state.get("deposit_usd") or 0) or 1.0
                # Pull entry vol from the per-symbol risk input (populated by
                # stage_phase3 from regime detector / per-symbol indicators).
                vol_pct = spec.get("30d_daily_vol_pct")
                if vol_pct in (None, 0, 0.0):
                    vol_pct = None  # explicit "unknown" rather than zero
                snap = audit_mod.snapshot_entry_consensus(
                    ticker=ticker,
                    ma_opt_score=float(spec.get("ma_optimist_score") or 0.0),
                    ma_pes_score=float(spec.get("ma_pessimist_score") or 0.0),
                    se_opt_score=float(spec.get("se_optimist_score") or 0.0),
                    se_pes_score=float(spec.get("se_pessimist_score") or 0.0),
                    risk_mgr_size_pct=float(trade.get("usd_amount", 0)) / deposit * 100,
                    market_disagreement=float(spec.get("market_disagreement") or 0.0),
                    onchain_disagreement=float(spec.get("onchain_disagreement") or 0.0),
                    vol_30d_daily_pct=(float(vol_pct) if vol_pct is not None else None),
                )
                state["holdings"][ticker]["entry_consensus"] = snap
            except Exception as e:
                bugs.log("MEDIUM", "execution",
                          f"{ticker} entry_consensus snapshot failed: {e}",
                          context={"ticker": ticker})
        
        results.append({"trade": trade, "result": result})

    # Phase 2.5 probe audit trail (risk_manager.md, lines 95+109): every probe
    # trade gets appended to probe_log.jsonl so the RM's 4-tick cooldown gate
    # has something authoritative to read. Historically the file was referenced
    # but had no writer — multi-agent review 2026-06-06.
    probe = pm_output.get("regime_probe")
    if probe and isinstance(probe, dict):
        probe_ticker = probe.get("ticker")
        probe_executed = any(
            r.get("trade", {}).get("ticker") == probe_ticker
            and r.get("trade", {}).get("side") == "buy"
            and r.get("result", {}).get("executed")
            for r in results
        )
        if probe_executed:
            try:
                from predictions.fund import universe_price_history as uph
                probe_row = {
                    "ts": int(time.time()),
                    "tick_id": uph.latest_tick_id(),
                    "ticker": probe_ticker,
                    "consensus_at_entry": probe.get("consensus_at_entry"),
                    "stop_loss_usd": probe.get("stop_loss_usd"),
                    "tp_usd": probe.get("tp_usd"),
                    "max_size_usd": probe.get("max_size_usd"),
                    "rationale": str(probe.get("rationale", ""))[:300],
                }
                probe_log_path = STATE_DIR / "probe_log.jsonl"
                probe_log_path.parent.mkdir(parents=True, exist_ok=True)
                existing = probe_log_path.read_text() if probe_log_path.exists() else ""
                tmp = probe_log_path.with_suffix(".jsonl.tmp")
                tmp.write_text(existing + json.dumps(probe_row) + "\n")
                tmp.rename(probe_log_path)
            except Exception as e:
                bugs.log("MEDIUM", "execution",
                          f"probe_log append failed: {e}",
                          context={"probe_ticker": probe_ticker})

    # Process stop_updates (TIGHTEN_STOP / TRAIL_UP / etc from Risk Mgr)
    for update in pm_output.get("stop_updates", []):
        ticker = update.get("ticker")
        new_stop = update.get("new_stop_usd") or update.get("new_stop_loss_price_usd")
        new_tp = update.get("new_take_profit_usd") or update.get("new_take_profit_price_usd")
        if not ticker: continue
        ok = acct.set_risk_levels(state, ticker, stop_loss_price=new_stop,
                                    take_profit_price=new_tp, set_by="pm_stop_update")
        if not ok:
            bugs.log("MEDIUM", "execution",
                      f"{ticker} stop_update no-op (position closed?)",
                      context=update)
    
    acct.save(state)
    
    # Mark-to-market + snapshot equity
    if prices is None:
        prices = {t["ticker"]: float(t["price_usd"]) for t in pm_output.get("trades", [])
                  if t.get("side") == "buy" and t.get("price_usd")}
    mtm = acct.mark_to_market(state, prices)
    acct.snapshot_equity(state, mtm)
    
    return {"results": results, "mtm": mtm, "state_after": state}


def cmd_prepare() -> int:
    """Run Phases 0-1: mark-to-market then stage universe data for Scout."""
    print("=== Phase 0: mark-to-market ===")
    mtm_out = mark_to_market_all()
    mtm = mtm_out["mtm"]
    print(f"  Equity: ${mtm['equity_usd']:,.2f}  Cash: ${mtm['cash_usd']:,.2f}  "
          f"Holdings: ${mtm['holdings_value_usd']:,.2f}  Deployed: {mtm['deployed_pct']*100:.1f}%")
    print(f"  Positions: {mtm['n_positions']}  Drawdown: {mtm['drawdown_from_peak_pct']*100:+.2f}%")
    if mtm_out["stop_triggers"]:
        print("  ⚠ STOP TRIGGERS:")
        for s in mtm_out["stop_triggers"]:
            print(f"    {s['ticker']} {s['trigger']} @ ${s['current_price']:.6g} (level ${s['level']:.6g})")
    print()
    print("=== Phase 1: stage universe data for Scout ===")
    out = STATE_DIR / "tick_universe_input.json"
    p = stage_universe(out)
    print(f"  Candidates: {len(p['candidates'])}, current holdings: {len(p['current_holdings'])}, "
          f"trending tokens: {len(p['cg_trending'])}")
    print(f"  Output: {out}")
    return 0


def cmd_status() -> int:
    state = account.load()
    mtm_out = mark_to_market_all()
    mtm = mtm_out["mtm"]
    perf = performance.compute(prices=mtm_out["prices"])
    print("# Solana Multi-Agent Fund — status")
    print()
    print(performance.format_for_agent_prompt(perf))
    print()
    print(f"Holdings:")
    if not mtm["positions"]:
        print("  (none)")
    else:
        for tk, p in mtm["positions"].items():
            if p["units"] <= 0: continue
            print(f"  {tk:<8} {p['units']:>14.4f} @ ${p['current_price']:.6g}  "
                  f"mv ${p['market_value_usd']:>8.2f}  "
                  f"pnl ${p['unrealized_pnl_usd']:>+7.2f} ({p['unrealized_pnl_pct']*100:>+5.1f}%)")
    return 0


def cmd_mark() -> int:
    out = mark_to_market_all()
    print(json.dumps(out["mtm"], indent=2, default=str))
    return 0


def cmd_audit() -> int:
    """Phase 7 — inline auto-audit. Cheap integrity checks at end of every tick.

    Runs the suite from predictions.fund.auto_audit and prints a one-line
    summary. Failed checks ALSO write a bug to bugs.jsonl so ops-health surfaces
    them. Exit code 0 unless a CRITICAL check failed (then 1 — the runner
    chain can fail-fast on that)."""
    from predictions.fund import auto_audit
    summary = auto_audit.run()
    n_pass = summary["passed"]
    n_fail = summary["failed"]
    print(f"=== Phase 7: auto-audit ===")
    print(f"  {n_pass} passed, {n_fail} failed")
    if n_fail:
        for r in summary["results"]:
            if not r.get("passed"):
                sev = r.get("severity", "MEDIUM")
                print(f"  ⚠ [{sev}] {r['check']}: {r.get('msg', '?')}")
    critical_failures = [r for r in summary["results"]
                         if not r.get("passed") and r.get("severity") == "CRITICAL"]
    return 1 if critical_failures else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["prepare", "status", "mark", "audit"])
    args = p.parse_args()
    return {"prepare": cmd_prepare, "status": cmd_status,
            "mark": cmd_mark, "audit": cmd_audit}[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
