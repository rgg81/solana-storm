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


_SOL_VOL_RE = __import__("re").compile(r"SOL 30d vol:\s*([\d.]+)%", __import__("re").IGNORECASE)


def _sol_30d_vol_from_regime(regime_status: str) -> float | None:
    """Parse the SOL 30d daily vol percentage from the regime_status text block.

    The block is the canonical source used in the agent prompt. Returns None
    when the value is unreadable (caller should leave the field as None rather
    than substitute 0 — zero would confuse downstream vol-scaled logic with a
    real reading).
    """
    if not isinstance(regime_status, str):
        return None
    m = _SOL_VOL_RE.search(regime_status)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


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


def _optimist_consensus(ma_optimist: float, se_optimist: float) -> float:
    """Mean of the two OPTIMIST scores — the bulls' agreement, independent of the
    pessimist drag. The Pass 2.5 probe gates on this instead of the 4-way mean so
    a genuine bull case is not vetoed by averaging in two structurally-bearish
    pessimists (desk-forensics root cause, 2026-06-16)."""
    return round((ma_optimist + se_optimist) / 2, 4)


class StaleSpecialistError(RuntimeError):
    """Raised when /tmp/smaf_*.json specialist outputs are older than the
    current tick's phase2 input — would mean a specialist dispatch failed and
    we're about to consume a previous tick's scores. See predictions/fund/
    tests/test_stale_specialist_guard.py for the rationale."""


class UniverseDataMismatchError(RuntimeError):
    """Raised when the /tmp/smaf_universe.json selected-symbols set diverges from
    the tick_phase2_input.json per_symbol set.

    Bug (tick-182): the Scout re-wrote the universe file (BONK -> ANSEM) AFTER
    stage_phase2 had already fetched BONK's data and the specialists had scored
    BONK. stage_phase3 then read the later ANSEM universe: ANSEM had no scores so
    it staged as an all-zero consensus row (a phantom neutral candidate that
    misleads the RM), while BONK's real scores were silently dropped. The two
    sets can only diverge if the universe file changed between Phase 2 and Phase 3
    (a scout revision consumed mid-flight, or a partial re-run). Fail loudly so
    the operator realigns /tmp/smaf_universe.json to the phase2 set (or re-runs
    the scout->phase2 chain) before any decision consumes the corrupted matrix.
    See tests/test_universe_phase2_consistency.py."""


def _assert_universe_matches_scored(universe: dict, per_sym_p2: dict) -> None:
    """The universe selected-symbols set MUST equal the phase2 per_symbol set.

    Set-equality (order/duplicates ignored). A symbol in the universe but not in
    per_symbol would stage all-zero scores; a symbol scored but not in the
    universe would be dropped. Either way the consensus matrix is corrupt.
    """
    uni = {s["ticker"] for s in universe.get("selected_symbols", []) if s.get("ticker")}
    scored = set(per_sym_p2.keys())
    only_universe = uni - scored   # staged but no phase2 data → all-zero rows
    only_phase2 = scored - uni     # scored but dropped from the staged matrix
    if only_universe or only_phase2:
        raise UniverseDataMismatchError(
            "Universe selected-symbols set diverges from the phase2 per_symbol "
            "set — the universe file was likely rewritten between Phase 2 and "
            "Phase 3. Realign /tmp/smaf_universe.json to the phase2 set (or "
            "re-run scout->stage_phase2) and re-run stage_phase3.\n"
            f"  in universe but NOT scored (would stage all-zero rows): {sorted(only_universe)}\n"
            f"  scored but NOT in universe (would be dropped): {sorted(only_phase2)}"
        )


def _assert_fresh(p2_path: Path) -> None:
    """Verify every /tmp/smaf_*.json mtime is newer than tick_phase2_input.json.

    A specialist dispatch that fails to overwrite its /tmp output leaves the
    previous tick's file in place; stage_phase3 would silently consume the
    stale scores and write a corrupted risk input. This guard raises so the
    runner can surface the failure instead of trading on stale consensus.
    """
    p2_mtime = p2_path.stat().st_mtime
    stale = []
    # 'univ' is the Phase-1 scout output — it's the SOURCE consumed by Phase 2
    # staging, so by construction it's always older than tick_phase2_input.json.
    # Only the 4 specialist outputs (written DURING Phase 2 dispatch) need to
    # be fresher than the phase2 input.
    SPECIALIST_KEYS = ("ma_opt", "ma_pes", "se_opt", "se_pes")
    for key in SPECIALIST_KEYS:
        path = TMP_PATHS.get(key)
        if path is None:
            stale.append(f"{key}: TMP_PATHS entry missing")
            continue
        p = Path(path)
        if not p.exists():
            stale.append(f"{key}: missing ({path})")
            continue
        lag_sec = p2_mtime - p.stat().st_mtime
        # Allow a small floor (5s) so identical-timestamp writes don't trip.
        if lag_sec > 5:
            stale.append(
                f"{key}: {lag_sec:.0f}s older than tick_phase2_input.json ({path})"
            )
    if stale:
        raise StaleSpecialistError(
            "Specialist outputs are stale — a Phase 2 dispatch likely failed "
            "to write its /tmp file. Refusing to compute consensus from "
            "previous-tick scores. Stale files:\n  - " + "\n  - ".join(stale)
        )


def stage() -> dict:
    """Build the Phase 3 (risk manager) input payload and write to state/tick_risk_input.json."""
    p2_path = STATE / "tick_phase2_input.json"
    _assert_fresh(p2_path)
    ma_opt = json.load(open(TMP_PATHS["ma_opt"]))
    ma_pes = json.load(open(TMP_PATHS["ma_pes"]))
    se_opt = json.load(open(TMP_PATHS["se_opt"]))
    se_pes = json.load(open(TMP_PATHS["se_pes"]))
    universe = json.load(open(TMP_PATHS["univ"]))

    p2 = json.load(open(p2_path))
    per_sym_p2 = p2.get("per_symbol", {})
    # The universe file must not have changed between Phase 2 (which fetched
    # per_symbol data and drove specialist scoring) and now — else we'd stage
    # all-zero rows for unscored symbols and drop scored ones (tick-182 bug).
    _assert_universe_matches_scored(universe, per_sym_p2)
    tickers = [s["ticker"] for s in universe.get("selected_symbols", [])]

    per_sym = {}
    for t in tickers:
        ma_o = _get_score(ma_opt, t)
        ma_p = _get_score(ma_pes, t)
        se_o = _get_score(se_opt, t)
        se_p = _get_score(se_pes, t)

        # 4-way mean. Pessimist scores are already signed (negative = bearish).
        consensus = round((ma_o + ma_p + se_o + se_p) / 4, 4)
        # Optimist-pair mean: the BULLS' agreement, independent of the pessimist
        # drag. The Pass 2.5 probe gates on this (>= +0.30) instead of the 4-way
        # mean so two strong bulls can clear the bar even when two structurally
        # bearish specialists would have dragged the blended mean under +0.20 —
        # the dominant inaction root cause (desk-forensics wf_8321460b). The
        # pessimist is retained as a separate HARD VETO (ma_pes > -0.50) + the
        # combined_uncertainty cap; pessimists VETO, they no longer DILUTE.
        optimist_consensus = _optimist_consensus(ma_o, se_o)
        market_disagreement = round(abs(ma_o - ma_p), 4)
        onchain_disagreement = round(abs(se_o - se_p), 4)
        combined_uncertainty = round(max(market_disagreement, onchain_disagreement), 4)

        # Phase-2 dex data — note: keys are `liq_usd` (NOT `liquidity_usd`) and `price_usd`.
        dex = (per_sym_p2.get(t, {}) or {}).get("dexscreener", {}) or {}
        cur_price = float(dex.get("price_usd") or 0)
        liq = float(dex.get("liq_usd") or 0)
        vol_30d = (per_sym_p2.get(t, {}) or {}).get("indicators", {}).get("vol_30d_daily_pct")
        # Fallback to SOL's 30d vol from regime_status when per-symbol indicator
        # is missing (covers infra/AI symbols whose per-symbol vol isn't computed).
        # Pre-fix this was a literal 0 for every symbol including SOL — review
        # 2026-06-06 flagged the structured field never matched the regime string.
        if vol_30d in (None, 0, 0.0):
            vol_30d = _sol_30d_vol_from_regime(p2.get("regime_status", ""))

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
            "optimist_consensus": optimist_consensus,
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
    print("Top by optimist_consensus (the probe-gate signal; sorted):")
    for t, d in sorted(r["per_sym"].items(), key=lambda x: x[1].get("optimist_consensus", 0), reverse=True):
        print(f"  {t:<8} opt_cons={d.get('optimist_consensus', 0):+.3f} cons={d['consensus']:+.3f} "
              f"(Opt {d['ma_optimist_score']:+.2f} Pes {d['ma_pessimist_score']:+.2f} "
              f"SE-O {d['se_optimist_score']:+.2f} SE-P {d['se_pessimist_score']:+.2f}) "
              f"comb-unc={d['combined_uncertainty']:.2f}")
