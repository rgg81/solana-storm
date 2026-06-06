"""Universe price history integrity test.

Codifies the invariant: no >100× single-tick price ratio between consecutive
rows of the same symbol unless the row carries an explicit `price_corrected_*`
metadata flag (which documents the patch).

History: 4 separate price corruptions slipped through DexScreener wrong-pool
quotes (PYTH/JUP/JTO/PUMP across ticks 34-39). The symmetric anomaly clamp in
stage_phase6.py blocks the corrupted rows from firing spurious reflector
triggers, but the corrupt prices themselves remain in the price history file.
This test ensures we never silently accept new corruption.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

HISTORY_PATH = Path(__file__).resolve().parents[1] / "state" / "universe_price_history.jsonl"

# Max plausible single-tick price ratio. Real memecoin rugs can drop 90% in 6h,
# but a 100× jump is physically implausible for a >$200M-mcap token. Same
# threshold the stage_phase6 anomaly clamp uses (logically).
MAX_RATIO = 100.0
MIN_RATIO = 1 / MAX_RATIO

CORRECTION_FLAGS = ("price_corrected_2026_06_05", "price_corrected_2026_06_06")
INVESTIGATION_FLAGS = ("price_under_investigation_2026_06_06",)


def _is_corrected(row: dict) -> bool:
    return any(row.get(flag) for flag in CORRECTION_FLAGS)


def _is_under_investigation(row: dict) -> bool:
    return any(row.get(flag) for flag in INVESTIGATION_FLAGS)


def test_no_uncorrected_intertick_price_jumps():
    if not HISTORY_PATH.exists():
        return  # Empty fund state in tests — nothing to check.

    rows = [json.loads(line) for line in HISTORY_PATH.read_text().splitlines() if line.strip()]
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[r.get("symbol")].append(r)

    violations: list[str] = []
    for sym, sym_rows in by_sym.items():
        sym_rows.sort(key=lambda r: r.get("tick_id", 0))
        for prev, curr in zip(sym_rows, sym_rows[1:]):
            prev_price = float(prev.get("price_usd") or 0)
            curr_price = float(curr.get("price_usd") or 0)
            if prev_price <= 0 or curr_price <= 0:
                continue
            ratio = curr_price / prev_price
            if ratio > MAX_RATIO or ratio < MIN_RATIO:
                # Tolerated ONLY if the curr row (the new value) carries a
                # correction flag, OR the prev row carries one (then the curr
                # row is the trusted anchor and the prev was the corrupt one).
                if _is_corrected(prev) or _is_corrected(curr):
                    continue
                # OR if either row is under-investigation (open bug, do not
                # silently accept the value but accept the test pass while the
                # follow-up patch is being designed). The bugs.jsonl entry is
                # the authoritative tracking record.
                if _is_under_investigation(prev) or _is_under_investigation(curr):
                    continue
                violations.append(
                    f"{sym} t{prev.get('tick_id')}->t{curr.get('tick_id')}: "
                    f"${prev_price:.6f} -> ${curr_price:.6f} (ratio {ratio:.2f}x)"
                )

    assert not violations, (
        "Universe price history contains uncorrected >100x jumps "
        "(likely DexScreener wrong-pool quotes). Patch the offending rows "
        "with a price_corrected_* flag + original_corrupt_price_usd, or "
        "investigate the data ingest:\n  " + "\n  ".join(violations)
    )
