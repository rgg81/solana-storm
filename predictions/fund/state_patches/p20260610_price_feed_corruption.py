"""Correct the tick-121 (t54) corrupt price rows in universe_price_history.jsonl.

A corrupt DexScreener response returned ~4800x prices for JUP/PYTH/PUMP/BONK at
t54 (e.g. PYTH $0.0317 → $154.69), written before the _guard_price() write-path
guard existed. This patch retroactively applies the same carry-forward the guard
would have: replace each corrupt t54 price with the symbol's t53 price and flag
the row (price_corrupt_guard + original_corrupt_price_usd). Idempotent: rows
already flagged are skipped. Fund was 100% cash / 0 positions at t54, so no trade
or MTM was affected — this only de-poisons the counterfactual ledger.
"""
from __future__ import annotations

import json
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state"
HIST = STATE / "universe_price_history.jsonl"

CORRUPT_TICK = 54
PRIOR_TICK = 53
SYMS = {"JUP", "PYTH", "PUMP", "BONK"}
MAX_RATIO = 100.0


def main() -> dict:
    if not HIST.exists():
        return {"corrected": 0, "note": "no history"}
    rows = []
    for line in HIST.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            rows.append(None)  # preserve unparseable lines positionally

    prior = {}
    for r in rows:
        if r and r.get("tick_id") == PRIOR_TICK and r.get("symbol") in SYMS:
            prior[r["symbol"]] = float(r.get("price_usd") or 0)

    corrected = 0
    for r in rows:
        if not r or r.get("tick_id") != CORRUPT_TICK or r.get("symbol") not in SYMS:
            continue
        if r.get("price_corrupt_guard"):
            continue  # idempotent
        cur = float(r.get("price_usd") or 0)
        pp = prior.get(r["symbol"], 0)
        if pp > 0 and cur > 0 and cur / pp > MAX_RATIO:
            r["original_corrupt_price_usd"] = cur
            r["price_usd"] = pp
            r["price_corrupt_guard"] = True
            corrected += 1

    if corrected:
        tmp = HIST.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(json.dumps(r, default=str) for r in rows if r is not None) + "\n")
        tmp.rename(HIST)
    return {"corrected": corrected}


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
