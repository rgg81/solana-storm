"""Stage Phase 3 (Risk Manager) input.

Reads the 4 specialist outputs (/tmp/smaf_market_analyst_optimist.json,
/tmp/smaf_market_analyst_pessimist.json, /tmp/smaf_solana_expert_optimist.json,
/tmp/smaf_solana_expert_pessimist.json) plus tick_phase2_input.json, computes
4-way consensus + market_disagreement + onchain_disagreement + combined_uncertainty,
and writes tick_risk_input.json for the Risk Manager subagent.

Usage:
    python3 -m predictions.fund.stage_phase3
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from predictions.fund import account, performance, goals, regime, lessons_io, risk_calibration, fees_model

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"

TMP_PATHS = {
    "ma_opt":  "/tmp/smaf_market_analyst_optimist.json",
    "ma_pes":  "/tmp/smaf_market_analyst_pessimist.json",
    "se_opt":  "/tmp/smaf_solana_expert_optimist.json",
    "se_pes":  "/tmp/smaf_solana_expert_pessimist.json",
    "univ":    "/tmp/smaf_universe.json",
}


def _scores_to_dict(out: dict) -> dict:
    """Normalize a specialist's `scores` (list-of-dicts OR dict) to {ticker: entry}."""
    s = out.get("scores", [])
    if isinstance(s, dict): return s
    if isinstance(s, list):
        return {e.get("ticker"): e for e in s if isinstance(e, dict) and e.get("ticker")}
    return {}


def _get_score(out: dict, ticker: str) -> float:
    e = _scores_to_dict(out).get(ticker)
    if not e: return 0.0
    v = e.get("score")
    return float(v) if isinstance(v, (int, float)) else 0.0


def stage() -> dict:
    """Build the Phase 3 (risk manager) input payload and write to state/tick_risk_input.json."""
    ma_opt = json.load(open(TMP_PATHS["ma_opt"]))
    ma_pes = json.load(open(TMP_PATHS["ma_pes"]))
    se_opt = json.load(open(TMP_PATHS["se_opt"]))
    se_pes = json.load(open(TMP_PATHS["se_pes"]))
    universe = json.load(open(TMP_PATHS["univ"]))

    p2 = json.load(open(STATE / "tick_phase2_input.json"))
    per_sym_p2 = p2.get("per_symbol", {})
    tickers = [s["ticker"] for s in universe.get("selected_symbols", [])]

    per_sym = {}
    for t in tickers:
        ma_o = _get_score(ma_opt, t)
        ma_p = _get_score(ma_pes, t)
        se_o = _get_score(se_opt, t)
        se_p = _get_score(se_pes, t)

        # 4-way mean. Pessimist scores are already signed (negative = bearish).
        consensus = round((ma_o + ma_p + se_o + se_p) / 4, 4)
        market_disagreement = round(abs(ma_o - ma_p), 4)
        onchain_disagreement = round(abs(se_o - se_p), 4)
        combined_uncertainty = round(max(market_disagreement, onchain_disagreement), 4)

        # Phase-2 dex data — note: keys are `liq_usd` (NOT `liquidity_usd`) and `price_usd`.
        dex = (per_sym_p2.get(t, {}) or {}).get("dexscreener", {}) or {}
        cur_price = float(dex.get("price_usd") or 0)
        liq = float(dex.get("liq_usd") or 0)
        vol_30d = (per_sym_p2.get(t, {}) or {}).get("indicators", {}).get("vol_30d_daily_pct", 0)

        try:
            est = fees_model.estimate(500.0, liq)
            cost = float(est.total_cost_pct)
            rt = cost * 2
        except Exception:
            cost, rt = 0.0, 0.0

        per_sym[t] = {
            "ma_optimist_score": ma_o,
            "ma_pessimist_score": ma_p,
            "se_optimist_score": se_o,
            "se_pessimist_score": se_p,
            "consensus": consensus,
            "market_disagreement": market_disagreement,
            "onchain_disagreement": onchain_disagreement,
            "combined_uncertainty": combined_uncertainty,
            "current_price_usd": cur_price,
            "liq_usd_main_pool": liq,
            "30d_daily_vol_pct": vol_30d,
            "fee_slippage_estimates": {"$500": {"cost_pct": cost, "rt_pct": rt}},
        }

    state = account.load()
    prices = {t: per_sym[t]["current_price_usd"] for t in tickers if per_sym[t]["current_price_usd"]}
    mtm = account.mark_to_market(state, prices)
    triggers = account.check_stop_triggers(state, prices)

    payload = {
        "phase": "risk_manager_input",
        "run_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "team_charter": (ROOT / "team_charter.md").read_text(),
        "account_state": {
            "cash_usd": state["cash_usd"],
            "equity_usd": mtm["equity_usd"],
            "deployed_pct": mtm["deployed_pct"],
            "n_positions": mtm["n_positions"],
            "halted": state.get("halted", False),
            "deposit_usd": state["deposit_usd"],
            "current_dd_pct": mtm["drawdown_from_peak_pct"],
        },
        "open_positions": {t: p for t, p in mtm["positions"].items() if p["units"] > 0},
        "stop_triggers_this_tick": triggers,
        "specialist_consensus_per_symbol": per_sym,
        "performance_state": performance.format_for_agent_prompt(performance.compute()),
        "lessons_summary": lessons_io.summary_for_agent_prompt(),
        "goal_status": goals.format_for_agent_prompt(),
        "regime_status": regime.format_for_agent_prompt(),
        "risk_calibration": risk_calibration.format_for_agent_prompt(),
    }

    out = STATE / "tick_risk_input.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.rename(out)
    return {"path": str(out), "per_sym": per_sym, "triggers": triggers, "mtm": mtm}


if __name__ == "__main__":
    r = stage()
    print(f"Wrote {r['path']}")
    print(f"Symbols staged: {list(r['per_sym'].keys())}")
    print(f"Triggers: {r['triggers']}")
    print(f"Equity: ${r['mtm']['equity_usd']:.2f}")
    print()
    print("Top consensus (sorted):")
    for t, d in sorted(r["per_sym"].items(), key=lambda x: x[1]["consensus"], reverse=True):
        print(f"  {t:<8} cons={d['consensus']:+.3f} (Opt {d['ma_optimist_score']:+.2f} Pes {d['ma_pessimist_score']:+.2f} "
              f"SE-O {d['se_optimist_score']:+.2f} SE-P {d['se_pessimist_score']:+.2f}) "
              f"mkt-dis={d['market_disagreement']:.2f} onch-dis={d['onchain_disagreement']:.2f}")
