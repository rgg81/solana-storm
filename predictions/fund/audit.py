"""Close-trade audit: when a position closes, determine which analyst was right.

Workflow:
1. When execute_pm_orders BUYs a token, snapshot the entry-time consensus
   (Optimist, Pessimist, Solana Expert scores) into the holding.
2. When execute_pm_orders SELLs (full close), call audit_close():
   - Compute realized P&L %
   - Compare each specialist's entry-time score sign to the realized P&L sign
   - Increment per-specialist scoreboard
   - Update disagreement→outcome correlation
   - Append to closed_trades_audit.jsonl (full event record)
"""
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Optional

_STATE_DIR = Path(__file__).resolve().parent / "state"
AUDIT_LOG_PATH = _STATE_DIR / "closed_trades_audit.jsonl"


def snapshot_entry_consensus(ticker: str, ma_opt_score: float, ma_pes_score: float,
                              se_opt_score: float, se_pes_score: float,
                              risk_mgr_size_pct: float,
                              market_disagreement: float,
                              onchain_disagreement: float,
                              vol_30d_daily_pct: float | None = None) -> dict:
    """Build the consensus snapshot dict (4-specialist version).

    Stored in position's `entry_consensus` field for audit-on-close. vol_30d_daily_pct
    is the symbol's 30-day daily volatility at entry — used by audit_close to feed
    risk_calibration.update_stop_calibration with the real entry vol (was a 0.05
    placeholder before 2026-06-06; stop multiplier auto-tuning was crippled).
    """
    return {
        "snapshot_unix": int(time.time()),
        "ma_optimist_score": ma_opt_score,
        "ma_pessimist_score": ma_pes_score,
        "se_optimist_score": se_opt_score,
        "se_pessimist_score": se_pes_score,
        "consensus": (ma_opt_score + ma_pes_score + se_opt_score + se_pes_score) / 4,
        "market_disagreement": market_disagreement,
        "onchain_disagreement": onchain_disagreement,
        "combined_uncertainty": max(market_disagreement, onchain_disagreement),
        "risk_mgr_max_size_pct": risk_mgr_size_pct,
        "vol_30d_daily_pct": vol_30d_daily_pct,
    }


def _bucket_for_disagreement(d: float) -> str:
    if d < 0.15: return "spread_0_to_15"
    if d < 0.40: return "spread_15_to_40"
    if d < 0.70: return "spread_40_to_70"
    return "spread_70_plus"


def _append_jsonl(path: Path, row: dict) -> None:
    existing = path.read_text() if path.exists() else ""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(existing + json.dumps(row) + "\n")
    tmp.rename(path)


def audit_close(ticker: str, entry_consensus: dict, realized_pnl_usd: float,
                 cost_basis_usd: float, exit_reason: str = "manual") -> dict:
    """Audit a closed position. Updates lessons.md scoreboard + appends to audit log.
    
    Args:
        ticker: closed symbol
        entry_consensus: snapshot from snapshot_entry_consensus() stored in the position
        realized_pnl_usd: net P&L from execute_trade's sell side (fees already netted)
        cost_basis_usd: original cost basis
        exit_reason: "stop_loss", "take_profit", "risk_mgr_close", "manual", etc.
    
    Returns event dict.
    """
    from predictions.fund import lessons_io
    
    realized_pct = (realized_pnl_usd / cost_basis_usd) if cost_basis_usd > 0 else 0
    was_winner = realized_pct > 0
    direction_sign = 1 if realized_pct > 0 else -1
    
    # For each specialist: was their score sign correct?
    correct_specialists = {}
    for spec_name, score_key in [
        ("market_analyst_optimist", "ma_optimist_score"),
        ("market_analyst_pessimist", "ma_pessimist_score"),
        ("solana_expert_optimist", "se_optimist_score"),
        ("solana_expert_pessimist", "se_pessimist_score"),
    ]:
        score = entry_consensus.get(score_key, 0)
        score_sign = 1 if score > 0 else (-1 if score < 0 else 0)
        # Correct if signs match (winner + positive score, OR loser + negative score)
        correct = (score_sign == direction_sign) if score_sign != 0 else False
        correct_specialists[spec_name] = {"score": score, "correct": correct, "sign": score_sign}
    
    # Load existing frontmatter, update scoreboard
    fm = lessons_io.load_frontmatter()
    sb = fm.get("scoreboard") or {}
    
    for spec_name, cs in correct_specialists.items():
        s = sb.get(spec_name) or {}
        n_old = s.get("closed_trades_scored", 0)
        cc_old = s.get("correct_directional_calls", 0)
        # Running averages of entry-time scores on winners vs losers
        if was_winner:
            avg_old = s.get("avg_score_on_winners") or 0
            n_w_old = s.get("_n_winners", 0)
            new_avg = (avg_old * n_w_old + cs["score"]) / (n_w_old + 1)
            s["avg_score_on_winners"] = round(new_avg, 3)
            s["_n_winners"] = n_w_old + 1
        else:
            avg_old = s.get("avg_score_on_losers") or 0
            n_l_old = s.get("_n_losers", 0)
            new_avg = (avg_old * n_l_old + cs["score"]) / (n_l_old + 1)
            s["avg_score_on_losers"] = round(new_avg, 3)
            s["_n_losers"] = n_l_old + 1
        s["closed_trades_scored"] = n_old + 1
        s["correct_directional_calls"] = cc_old + (1 if cs["correct"] else 0)
        
        # Flags
        if s["closed_trades_scored"] >= 3:
            if spec_name in ("market_analyst_optimist", "solana_expert_optimist"):
                aol = s.get("avg_score_on_losers")
                s["over_confidence_flag"] = (aol or 0) > 0.30
            elif spec_name in ("market_analyst_pessimist", "solana_expert_pessimist"):
                aow = s.get("avg_score_on_winners")
                s["over_caution_flag"] = (aow or 0) < -0.10
        sb[spec_name] = s
    
    # PM scoreboard
    pm_sb = sb.get("portfolio_manager") or {}
    pm_sb["closes_executed"] = pm_sb.get("closes_executed", 0) + 1
    sb["portfolio_manager"] = pm_sb
    
    # === Per-symbol scoreboard (NEW item H) ===
    # Track each specialist's accuracy ON THIS SYMBOL specifically
    per_sym = fm.get("per_symbol_specialist_accuracy") or {}
    sym_data = per_sym.setdefault(ticker, {})
    sym_data["closed_trades"] = sym_data.get("closed_trades", 0) + 1
    sym_data["last_realized_pct"] = round(realized_pct * 100, 2)
    # Running average of realized return for this symbol
    sym_data["cumulative_realized_pct"] = round(
        sym_data.get("cumulative_realized_pct", 0) + realized_pct * 100, 2
    )
    sym_data["avg_realized_pct"] = round(
        sym_data["cumulative_realized_pct"] / sym_data["closed_trades"], 2
    )
    # Per-specialist score-at-entry tracking
    for spec_name, score_key in [
        ("market_analyst_optimist", "ma_optimist_score"),
        ("market_analyst_pessimist", "ma_pessimist_score"),
        ("solana_expert_optimist", "se_optimist_score"),
        ("solana_expert_pessimist", "se_pessimist_score"),
    ]:
        s = sym_data.setdefault(spec_name, {"avg_entry_score": 0.0, "correct_directional": 0, "n": 0})
        score = entry_consensus.get(score_key, 0)
        s["n"] += 1
        s["avg_entry_score"] = round((s["avg_entry_score"] * (s["n"] - 1) + score) / s["n"], 3)
        score_sign = 1 if score > 0 else (-1 if score < 0 else 0)
        direction_sign = 1 if was_winner else -1
        if score_sign == direction_sign and score_sign != 0:
            s["correct_directional"] += 1
    
    # Flag: if all 4 specialists consistently negative for this symbol AND we've never won → blacklist hint
    avg_realized = sym_data["avg_realized_pct"]
    if sym_data["closed_trades"] >= 2 and avg_realized < -5:
        sym_data["blacklist_hint"] = True
        sym_data["blacklist_reason"] = f"avg realized {avg_realized}% over {sym_data['closed_trades']} closes"
    
    fm["per_symbol_specialist_accuracy"] = per_sym
    
    # Disagreement → outcome (use combined_uncertainty if present, else legacy disagreement)
    disagreement = entry_consensus.get("combined_uncertainty",
                                          entry_consensus.get("disagreement", 0))
    bucket = _bucket_for_disagreement(disagreement)
    do = fm.get("disagreement_outcome") or {}
    b = do.get(bucket) or {"n": 0, "avg_return_pct": 0, "win_rate": 0}
    n_old = b.get("n", 0)
    avg_old = b.get("avg_return_pct") or 0
    wr_old = b.get("win_rate") or 0
    new_avg = (avg_old * n_old + realized_pct * 100) / (n_old + 1)
    new_wr = (wr_old * n_old + (100 if was_winner else 0)) / (n_old + 1)
    b["n"] = n_old + 1
    b["avg_return_pct"] = round(new_avg, 2)
    b["win_rate"] = round(new_wr, 1)
    do[bucket] = b
    
    fm["scoreboard"] = sb
    fm["disagreement_outcome"] = do
    fm["total_closed_trades_audited"] = fm.get("total_closed_trades_audited", 0) + 1
    
    lessons_io.update_frontmatter(fm)
    
    # Audit log event
    event = {
        "timestamp": int(time.time()),
        "ticker": ticker,
        "exit_reason": exit_reason,
        "realized_pnl_usd": round(realized_pnl_usd, 2),
        "realized_pct": round(realized_pct * 100, 2),
        "was_winner": was_winner,
        "entry_consensus": entry_consensus,
        "specialists_correct": correct_specialists,
        "disagreement_bucket": bucket,
    }
    _append_jsonl(AUDIT_LOG_PATH, event)
    
    # === Run reflector + auto-calibration on this close (items G + A + D) ===
    try:
        from predictions.fund import reflector, risk_calibration
        reflector.reflect_on_close(event)
        # Stop calibration: did stop trigger?
        stop_triggered = exit_reason in ("stop_loss_verified", "stop_loss")
        # Need vol_30d at entry — proxy: use realized_pct magnitude as a noise proxy
        # (better: store entry vol in entry_consensus; for now use cost-basis-derived approximation)
        # vol_30d_daily_pct comes from the entry-time snapshot now (was a 0.05
        # literal placeholder pre-2026-06-06 — stop auto-tuning was crippled).
        # Fall back to 0.05 ONLY when the entry pre-dates the propagation.
        entry_vol = entry_consensus.get("vol_30d_daily_pct")
        if entry_vol is None:
            entry_vol = 0.05
        risk_calibration.update_stop_calibration(
            was_stop_triggered=stop_triggered,
            was_winner=was_winner,
            realized_pct=realized_pct,
            vol_30d=float(entry_vol),
        )
        # Disagreement penalty recalibration (uses bucket data from lessons.md)
        risk_calibration.update_disagreement_calibration()
    except Exception as e:
        pass  # don't let reflection bugs break audit
    
    return event


if __name__ == "__main__":
    # Self-test: simulate auditing a fake close
    fake_entry = snapshot_entry_consensus("TEST", 0.55, 0.10, -0.10, 7.0, 0.65)
    print("Fake entry consensus:", json.dumps(fake_entry, indent=2))
    print()
    print("(audit_close would now run if there was a real close — skipping in self-test)")
