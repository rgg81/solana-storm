"""Audit coverage test — every sell in trades.jsonl must have an audit row.

History: the RENDER winner (+$133.13 TP) was never audited; only the JTO
loser (-$31.34 stop) was. As a result lessons.md frontmatter was telling
the team total_closed_trades_audited=1 (vs actual closes = 2) and the
specialist scoreboard was loss-only.
"""
from __future__ import annotations

import json
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state"
TRADES = STATE / "trades.jsonl"
AUDIT = STATE / "closed_trades_audit.jsonl"


def test_every_sell_has_audit_row():
    if not TRADES.exists():
        return  # fund state not present (test env) — nothing to check
    sells = [
        json.loads(l)
        for l in TRADES.read_text().splitlines()
        if l.strip() and json.loads(l).get("side") == "sell"
    ]
    audit_tickers: set[str] = set()
    if AUDIT.exists():
        for l in AUDIT.read_text().splitlines():
            if not l.strip():
                continue
            try:
                audit_tickers.add(json.loads(l).get("ticker"))
            except Exception:
                continue
    sell_tickers = {s.get("ticker") for s in sells}
    missing = sell_tickers - audit_tickers
    assert not missing, (
        f"Sells in trades.jsonl have no matching audit row: {missing}. "
        f"Every closed position must invoke audit.audit_close at sell time "
        f"OR be backfilled via state_patches/p20260606_render_audit_backfill.py."
    )
