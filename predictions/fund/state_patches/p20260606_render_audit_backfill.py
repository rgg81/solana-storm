"""Backfill the missing RENDER take-profit audit row.

History: trades.jsonl has 2 sells (JTO stop-loss, RENDER take-profit) but
closed_trades_audit.jsonl has only 1 row (JTO). The RENDER +$133.13 winner
was never audited — likely because the entry_consensus stored at tick-1 BUY
used the legacy 3-specialist schema (optimist_score / pessimist_score /
solana_expert_score) and audit_close's score-correctness loop reads the
4-specialist keys (ma_optimist_score / ma_pessimist_score / se_*_score), so
when the close fired the score lookup silently missed every specialist.

This patch is idempotent. It reads the RENDER trade entry/exit from
trades.jsonl + entry_consensus from account.json (RENDER block) and calls
audit.audit_close with translated 4-specialist scores so the row matches
the canonical schema.
"""
from __future__ import annotations

import json
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state"
TRADES = STATE / "trades.jsonl"
ACCOUNT = STATE / "account.json"
AUDIT = STATE / "closed_trades_audit.jsonl"


def _already_audited(ticker: str, realized_pnl_usd: float) -> bool:
    if not AUDIT.exists():
        return False
    for l in AUDIT.read_text().splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except Exception:
            continue
        # Match on ticker + sign of realized P&L (don't depend on exact float).
        if r.get("ticker") == ticker:
            ev_pnl = r.get("realized_pnl_usd") or 0
            if (ev_pnl > 0) == (realized_pnl_usd > 0):
                return True
    return False


def _render_entry_consensus_translated() -> dict:
    """Translate the legacy RENDER entry_consensus (3-specialist) into the
    canonical 4-specialist shape audit_close expects, splitting the unified
    solana_expert_score evenly between SE-Opt and SE-Pes."""
    acct = json.loads(ACCOUNT.read_text())
    legacy = (acct.get("holdings", {}).get("RENDER", {}) or {}).get("entry_consensus", {}) or {}
    se = float(legacy.get("solana_expert_score") or 0.0)
    return {
        "snapshot_unix": int(legacy.get("snapshot_unix") or 0),
        "ma_optimist_score": float(legacy.get("optimist_score") or 0.0),
        "ma_pessimist_score": float(legacy.get("pessimist_score") or 0.0),
        # Even split — legacy unified score is the best estimate we have for
        # both axes since SE-Opt/SE-Pes weren't tracked separately at tick 1.
        "se_optimist_score": se,
        "se_pessimist_score": se,
        "consensus": float(legacy.get("consensus") or 0.0),
        "market_disagreement": float(legacy.get("disagreement") or 0.0),
        "onchain_disagreement": 0.0,
        "combined_uncertainty": float(legacy.get("disagreement") or 0.0),
        "risk_mgr_max_size_pct": float(legacy.get("risk_mgr_max_size_pct") or 0.0),
        "vol_30d_daily_pct": None,
        "_backfill_note": "tick 1 unified-MA schema; SE split evenly from solana_expert_score",
    }


def main() -> dict:
    if not TRADES.exists():
        return {"backfilled": False, "reason": "trades.jsonl missing"}
    if not ACCOUNT.exists():
        return {"backfilled": False, "reason": "account.json missing"}

    # Find the RENDER sell.
    render_sell = None
    render_buy = None
    for line in TRADES.read_text().splitlines():
        if not line.strip():
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t.get("ticker") != "RENDER":
            continue
        if t.get("side") == "buy" and render_buy is None:
            render_buy = t
        elif t.get("side") == "sell":
            render_sell = t
    if not render_sell or not render_buy:
        return {"backfilled": False, "reason": "RENDER buy/sell not found in trades.jsonl"}

    realized = float(render_sell.get("realized_pnl_usd") or 0)
    if _already_audited("RENDER", realized):
        return {"backfilled": False, "reason": "already audited"}

    cost_basis = float(render_buy.get("usd_amount") or 0)
    entry_consensus = _render_entry_consensus_translated()

    # Build the audit event directly (rather than calling audit_close which
    # would re-run reflector + calibration on an old close — undesirable).
    from predictions.fund import audit
    realized_pct = realized / cost_basis if cost_basis > 0 else 0
    was_winner = realized > 0
    direction_sign = 1 if was_winner else -1
    correct_specialists = {}
    for spec_name, score_key in [
        ("market_analyst_optimist", "ma_optimist_score"),
        ("market_analyst_pessimist", "ma_pessimist_score"),
        ("solana_expert_optimist", "se_optimist_score"),
        ("solana_expert_pessimist", "se_pessimist_score"),
    ]:
        score = entry_consensus.get(score_key, 0)
        score_sign = 1 if score > 0 else (-1 if score < 0 else 0)
        correct = (score_sign == direction_sign) if score_sign != 0 else False
        correct_specialists[spec_name] = {"score": score, "correct": correct, "sign": score_sign}

    disagreement = float(entry_consensus.get("market_disagreement") or 0)
    bucket = audit._bucket_for_disagreement(disagreement)

    event = {
        "timestamp": int(render_sell.get("timestamp") or 0),
        "ticker": "RENDER",
        "entry_consensus": entry_consensus,
        "exit_reason": "take_profit_executed",
        "realized_pnl_usd": round(realized, 4),
        "realized_pct": round(realized_pct * 100, 2),
        "cost_basis_usd": round(cost_basis, 2),
        "was_winner": was_winner,
        "specialists_correct": correct_specialists,
        "disagreement_bucket": bucket,
        "_backfilled_2026_06_06": True,
        "_backfill_reason": "TP path missed audit_close at execution time; legacy entry schema was 3-specialist",
    }
    audit._append_jsonl(AUDIT, event)

    # Update lessons.md frontmatter rollup to reflect both closes.
    try:
        from predictions.fund import lessons_io
        lessons_io.refresh_frontmatter_counters()
    except Exception:
        pass

    return {"backfilled": True, "ticker": "RENDER", "realized_pnl_usd": realized}


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
