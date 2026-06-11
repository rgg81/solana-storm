"""Backfill the missing JTO-probe close into closed_trades_audit.jsonl.

Found 2026-06-11 while answering "are the agents learning from past decisions?".
JTO was round-tripped twice:
  1. stop-loss: buy $0.5411 -> sell $0.5108, -$31.36  (audited, exit_reason stop_loss_verified)
  2. PROBE:     buy $0.5622 -> sell $0.6507, +$18.67  (NOT audited)
Round-trip #2 is the fund's single most-cited trade — "first clean realized winner
under the corrected probe gate after 108 ticks" — yet its audit row (which stores
the entry_consensus the Reflector learns winning entries from) was never written.
Its P&L IS reflected in equity (+$120.44 net = RENDER +133.13 - JTO_stop 31.36 +
JTO_probe 18.67) and in the headline stats (PF 4.84, hit-rate 66.7% on 3 closed),
so this is a memory-completeness gap in the audit ledger, not a P&L error.

The old Phase 7 audit_coverage check (set-difference) missed it because JTO was
already in the audit set via the stop. That check is fixed (count per ticker) in
auto_audit.check_audit_coverage; this patch restores the missing row so the ledger
is truthful and the Reflector can learn from the fund's best entry.

Entry signals reconstructed from universe_price_history.jsonl tick-43 (the
BUY_EXECUTED row): consensus +0.2425, ma_opt +0.52, ma_pes +0.15, se_opt +0.30,
se_pes 0.0, market_disagreement 0.37, onchain_disagreement 0.30. Exit figures from
trades.jsonl (sell trade_id d46a5062a8b5): +$18.67 net, take_profit_executed.

DELIBERATELY NOT re-run: the live scoreboard / stop-calibration / reflector
side-effects that audit_close() would normally fire. Those have adapted over 100+
subsequent ticks; injecting a months-old sample now would distort current-state
calibration more than it would help. This patch appends ONLY the audit log row
(flagged backfilled=True) and lets refresh_frontmatter_counters resync the audited
count from the log. Idempotent: skips if a JTO take_profit audit row already exists.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state"
AUDIT_LOG = STATE / "closed_trades_audit.jsonl"

# Ground-truth figures (verified against trades.jsonl + universe_price_history.jsonl).
PROBE = {
    "exit_trade_id": "d46a5062a8b5",
    "exit_timestamp": 1780931295,
    "realized_pnl_usd": 18.67,
    "cost_basis_usd": 125.00,
    "ma_optimist_score": 0.52,
    "ma_pessimist_score": 0.15,
    "se_optimist_score": 0.30,
    "se_pessimist_score": 0.0,
    "market_disagreement": 0.37,
    "onchain_disagreement": 0.30,
    "entry_snapshot_unix": 1780815624,  # probe buy timestamp
}


def _bucket(d: float) -> str:
    if d < 0.15: return "spread_0_to_15"
    if d < 0.40: return "spread_15_to_40"
    if d < 0.70: return "spread_40_to_70"
    return "spread_70_plus"


def _already_backfilled(rows: list[dict]) -> bool:
    for r in rows:
        if r.get("ticker") != "JTO":
            continue
        if "take_profit" in (r.get("exit_reason") or ""):
            return True
        if r.get("backfilled") and r.get("exit_trade_id") == PROBE["exit_trade_id"]:
            return True
    return False


def main() -> dict:
    rows = []
    if AUDIT_LOG.exists():
        for line in AUDIT_LOG.read_text().splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    if _already_backfilled(rows):
        return {"backfilled": 0, "note": "JTO probe audit row already present"}

    realized_pct = PROBE["realized_pnl_usd"] / PROBE["cost_basis_usd"]
    was_winner = realized_pct > 0
    direction_sign = 1 if was_winner else -1

    entry_consensus = {
        "snapshot_unix": PROBE["entry_snapshot_unix"],
        "ma_optimist_score": PROBE["ma_optimist_score"],
        "ma_pessimist_score": PROBE["ma_pessimist_score"],
        "se_optimist_score": PROBE["se_optimist_score"],
        "se_pessimist_score": PROBE["se_pessimist_score"],
        "consensus": round((PROBE["ma_optimist_score"] + PROBE["ma_pessimist_score"]
                            + PROBE["se_optimist_score"] + PROBE["se_pessimist_score"]) / 4, 4),
        "market_disagreement": PROBE["market_disagreement"],
        "onchain_disagreement": PROBE["onchain_disagreement"],
        "combined_uncertainty": max(PROBE["market_disagreement"], PROBE["onchain_disagreement"]),
        "risk_mgr_max_size_pct": None,
        "vol_30d_daily_pct": None,
        "reconstructed_from": "universe_price_history.jsonl tick-43 BUY_EXECUTED",
    }

    specialists_correct = {}
    for spec, key in [
        ("market_analyst_optimist", "ma_optimist_score"),
        ("market_analyst_pessimist", "ma_pessimist_score"),
        ("solana_expert_optimist", "se_optimist_score"),
        ("solana_expert_pessimist", "se_pessimist_score"),
    ]:
        score = entry_consensus[key]
        sign = 1 if score > 0 else (-1 if score < 0 else 0)
        specialists_correct[spec] = {
            "score": score, "sign": sign,
            "correct": (sign == direction_sign) if sign != 0 else False,
        }

    event = {
        "timestamp": PROBE["exit_timestamp"],
        "ticker": "JTO",
        "exit_reason": "take_profit_executed",
        "realized_pnl_usd": round(PROBE["realized_pnl_usd"], 2),
        "realized_pct": round(realized_pct * 100, 2),
        "was_winner": was_winner,
        "entry_consensus": entry_consensus,
        "specialists_correct": specialists_correct,
        "disagreement_bucket": _bucket(entry_consensus["combined_uncertainty"]),
        "backfilled": True,
        "backfill_note": "2026-06-11 reconstruction of the unaudited JTO probe close; "
                         "scoreboard/calibration side-effects intentionally NOT replayed",
        "exit_trade_id": PROBE["exit_trade_id"],
    }

    existing = AUDIT_LOG.read_text() if AUDIT_LOG.exists() else ""
    tmp = AUDIT_LOG.with_suffix(".jsonl.tmp")
    tmp.write_text(existing + json.dumps(event) + "\n")
    tmp.rename(AUDIT_LOG)

    # Resync the audited-trade counter from the log (counts lines).
    try:
        from predictions.fund import lessons_io
        lessons_io.refresh_frontmatter_counters()
    except Exception:
        pass

    return {"backfilled": 1, "ticker": "JTO", "realized_pnl_usd": event["realized_pnl_usd"],
            "realized_pct": event["realized_pct"]}


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
