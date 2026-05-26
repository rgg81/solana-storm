"""Stage Phase 4 (Portfolio Manager) input.

Reads /tmp/smaf_risk.json (Risk Manager output) + tick_risk_input.json (for prices/liq/consensus)
and writes tick_pm_input.json for the Portfolio Manager subagent.

Usage:
    python3 -m predictions.fund.stage_phase4
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from predictions.fund import account, performance, goals, regime, lessons_io

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"

RISK_OUT_PATH = "/tmp/smaf_risk.json"


def stage() -> dict:
    risk = json.load(open(RISK_OUT_PATH))
    p3 = json.load(open(STATE / "tick_risk_input.json"))
    per_sym = p3.get("specialist_consensus_per_symbol", {})

    state = account.load()
    prices = {t: per_sym[t]["current_price_usd"] for t in per_sym if per_sym[t]["current_price_usd"]}
    mtm = account.mark_to_market(state, prices)
    triggers = account.check_stop_triggers(state, prices)

    payload = {
        "phase": "portfolio_manager_input",
        "run_time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "team_charter": (ROOT / "team_charter.md").read_text(),
        "risk_manager_output": risk,
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
        "symbol_market_data": {t: {
            "current_price_usd": per_sym[t]["current_price_usd"],
            "liq_usd_main_pool": per_sym[t]["liq_usd_main_pool"],
            "30d_daily_vol_pct": per_sym[t]["30d_daily_vol_pct"],
            "consensus": per_sym[t]["consensus"],
            "combined_uncertainty": per_sym[t]["combined_uncertainty"],
            "fee_slippage_estimates": per_sym[t]["fee_slippage_estimates"],
        } for t in per_sym},
        "performance_state": performance.format_for_agent_prompt(performance.compute()),
        "goal_status": goals.format_for_agent_prompt(),
        "regime_status": regime.format_for_agent_prompt(),
        "lessons_summary": lessons_io.summary_for_agent_prompt(),
    }

    out = STATE / "tick_pm_input.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.rename(out)
    return {"path": str(out), "risk_approved_entries": len(risk.get("new_entry_recommendations", [])),
            "triggers": triggers, "existing_position_actions": len(risk.get("existing_positions", []))}


if __name__ == "__main__":
    r = stage()
    print(f"Wrote {r['path']}")
    print(f"Risk-approved entries: {r['risk_approved_entries']}")
    print(f"Stop triggers: {r['triggers']}")
    print(f"Existing position actions: {r['existing_position_actions']}")
