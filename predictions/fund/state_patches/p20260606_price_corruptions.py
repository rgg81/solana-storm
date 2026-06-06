"""One-shot patch for 4 confirmed DexScreener wrong-pool price corruptions in
universe_price_history.jsonl + 1 pending-investigation flag for BONK.

Confirmed corruptions (back-anchor: tick 40-41 prices match trade ledger):
  PYTH ticks 35-39: ~$156-163  (real: ~$0.031-$0.036)   ratio ~4,400x
  JUP  ticks 34-39: ~$812-866  (real: ~$0.155-$0.187)   ratio ~4,800x
  JTO  ticks 38-39: ~$2,554-2,708 (real: ~$0.51-$0.54)  ratio ~5,000x
  PUMP ticks 35-39: ~$7.55-$8.16 (real: ~$0.00146-$0.00167) ratio ~4,700x

Each row corrupt-anchored to the closest pre-tick OR linearly interpolated
between known-good anchors at ticks before and after the corruption window.
All patched rows carry: original_corrupt_price_usd, original_corrupt_liq,
price_corrected_2026_06_06=True, correction_reason.

BONK ticks 35-41: the wrong-pool quote ($0.022xx vs real ~$0.0000050) never
reverted. Marked price_under_investigation_2026_06_06=True (NOT corrected — we
need to understand whether the DexScreener primary-pool selection is broken
for BONK specifically, or whether there's a token symbol collision). Bug
ticket logged via bugs.log on script run.

Idempotent: re-running this script skips rows that already carry the flag.
"""
from __future__ import annotations

import json
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parents[1] / "state"
HISTORY_PATH = STATE_DIR / "universe_price_history.jsonl"


# (symbol, tick_id) -> corrected (price_usd, liq_usd_main_pool)
# Prices interpolated between known-good anchors.
# Liquidity recalculated proportional to price ratio (rough — actual liq depends
# on pool composition but this captures the order-of-magnitude correction).
PYTH_ANCHOR_T34, PYTH_ANCHOR_T40 = 0.03538, 0.03188
PYTH_LIQ_ANCHOR = 215_000  # ~$215k primary pool, stable across ticks
JUP_ANCHOR_T33, JUP_ANCHOR_T40 = 0.1792, 0.1558
JUP_LIQ_ANCHOR = 925_000
JTO_ANCHOR_T40, JTO_TRADE_PRICE = 0.5108, 0.5411  # tick 40 = real, trade was at $0.5411
JTO_LIQ_ANCHOR = 1_040_000
PUMP_ANCHOR_T34, PUMP_ANCHOR_T40 = 0.001673, 0.001417
PUMP_LIQ_ANCHOR = 12_200_000


def _interp(a: float, b: float, frac: float) -> float:
    return a + (b - a) * frac


CORRECTIONS: dict[tuple[str, int], dict] = {
    # PYTH: linear interp ticks 35-39 between tick 34 and tick 40
    ("PYTH", 35): {"price_usd": _interp(PYTH_ANCHOR_T34, PYTH_ANCHOR_T40, 1 / 6), "liq_usd_main_pool": PYTH_LIQ_ANCHOR},
    ("PYTH", 36): {"price_usd": _interp(PYTH_ANCHOR_T34, PYTH_ANCHOR_T40, 2 / 6), "liq_usd_main_pool": PYTH_LIQ_ANCHOR},
    ("PYTH", 37): {"price_usd": _interp(PYTH_ANCHOR_T34, PYTH_ANCHOR_T40, 3 / 6), "liq_usd_main_pool": PYTH_LIQ_ANCHOR},
    ("PYTH", 38): {"price_usd": _interp(PYTH_ANCHOR_T34, PYTH_ANCHOR_T40, 4 / 6), "liq_usd_main_pool": PYTH_LIQ_ANCHOR},
    ("PYTH", 39): {"price_usd": _interp(PYTH_ANCHOR_T34, PYTH_ANCHOR_T40, 5 / 6), "liq_usd_main_pool": PYTH_LIQ_ANCHOR},
    # JUP: linear interp ticks 34-39 between tick 33 and tick 40
    ("JUP", 34): {"price_usd": _interp(JUP_ANCHOR_T33, JUP_ANCHOR_T40, 1 / 7), "liq_usd_main_pool": JUP_LIQ_ANCHOR},
    ("JUP", 35): {"price_usd": _interp(JUP_ANCHOR_T33, JUP_ANCHOR_T40, 2 / 7), "liq_usd_main_pool": JUP_LIQ_ANCHOR},
    ("JUP", 36): {"price_usd": _interp(JUP_ANCHOR_T33, JUP_ANCHOR_T40, 3 / 7), "liq_usd_main_pool": JUP_LIQ_ANCHOR},
    ("JUP", 37): {"price_usd": _interp(JUP_ANCHOR_T33, JUP_ANCHOR_T40, 4 / 7), "liq_usd_main_pool": JUP_LIQ_ANCHOR},
    ("JUP", 38): {"price_usd": _interp(JUP_ANCHOR_T33, JUP_ANCHOR_T40, 5 / 7), "liq_usd_main_pool": JUP_LIQ_ANCHOR},
    ("JUP", 39): {"price_usd": _interp(JUP_ANCHOR_T33, JUP_ANCHOR_T40, 6 / 7), "liq_usd_main_pool": JUP_LIQ_ANCHOR},
    # JTO: ticks 38-39 — use trade ledger ($0.5411 buy) as the upstream anchor
    # and tick 40 ($0.5108, matched stop trigger) as the downstream anchor.
    ("JTO", 38): {"price_usd": _interp(JTO_TRADE_PRICE, JTO_ANCHOR_T40, 1 / 3), "liq_usd_main_pool": JTO_LIQ_ANCHOR},
    ("JTO", 39): {"price_usd": _interp(JTO_TRADE_PRICE, JTO_ANCHOR_T40, 2 / 3), "liq_usd_main_pool": JTO_LIQ_ANCHOR},
    # PUMP: linear interp ticks 35-39 between tick 34 and tick 40
    ("PUMP", 35): {"price_usd": _interp(PUMP_ANCHOR_T34, PUMP_ANCHOR_T40, 1 / 6), "liq_usd_main_pool": PUMP_LIQ_ANCHOR},
    ("PUMP", 36): {"price_usd": _interp(PUMP_ANCHOR_T34, PUMP_ANCHOR_T40, 2 / 6), "liq_usd_main_pool": PUMP_LIQ_ANCHOR},
    ("PUMP", 37): {"price_usd": _interp(PUMP_ANCHOR_T34, PUMP_ANCHOR_T40, 3 / 6), "liq_usd_main_pool": PUMP_LIQ_ANCHOR},
    ("PUMP", 38): {"price_usd": _interp(PUMP_ANCHOR_T34, PUMP_ANCHOR_T40, 4 / 6), "liq_usd_main_pool": PUMP_LIQ_ANCHOR},
    ("PUMP", 39): {"price_usd": _interp(PUMP_ANCHOR_T34, PUMP_ANCHOR_T40, 5 / 6), "liq_usd_main_pool": PUMP_LIQ_ANCHOR},
}

CORRECTION_REASON = (
    "DexScreener wrong-pool quote (likely a non-base-asset pair selected as "
    "primary). Anchored to the trade ledger or to interpolation between known-"
    "good neighbors. Original values preserved for audit. See state_patches/"
    "p20260606_price_corruptions.py for derivation."
)

# Tickers/ticks to flag as pending-investigation rather than patched in-place.
# These are corruptions where we don't yet have a confident corrected value
# (BONK quote drifted to $0.022xx mid-streak and never reverted, possibly a
# symbol collision or a permanently-changed primary pool — needs upstream
# investigation before we apply a back-anchor).
PENDING_INVESTIGATION: set[tuple[str, int]] = {("BONK", t) for t in range(35, 42)}

PENDING_REASON = (
    "Mid-streak price jump from sub-cent to $0.022 region with no reversion. "
    "Possible primary-pool drift, symbol collision, or genuine token migration "
    "we haven't accounted for. Flag is informational; do NOT trust the value "
    "without verification. Tracked separately for an upstream investigation."
)


def patch(history_path: Path = HISTORY_PATH, *, write: bool = True) -> dict:
    """Apply corrections + investigation flags in-place. Idempotent.

    Returns a summary dict {patched: int, marked_pending: int, skipped: int}.
    """
    if not history_path.exists():
        return {"patched": 0, "marked_pending": 0, "skipped": 0, "note": "no history file"}

    lines = history_path.read_text().splitlines()
    out: list[str] = []
    patched = 0
    pending = 0
    skipped = 0
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        r = json.loads(line)
        key = (r.get("symbol"), r.get("tick_id"))
        if key in CORRECTIONS:
            if r.get("price_corrected_2026_06_06"):
                skipped += 1
            else:
                fix = CORRECTIONS[key]
                r["original_corrupt_price_usd"] = r.get("price_usd")
                r["original_corrupt_liq_usd_main_pool"] = r.get("liq_usd_main_pool")
                r["price_usd"] = round(fix["price_usd"], 8)
                r["liq_usd_main_pool"] = fix["liq_usd_main_pool"]
                r["price_corrected_2026_06_06"] = True
                r["correction_reason"] = CORRECTION_REASON
                patched += 1
        elif key in PENDING_INVESTIGATION:
            if r.get("price_under_investigation_2026_06_06"):
                skipped += 1
            else:
                r["price_under_investigation_2026_06_06"] = True
                r["investigation_reason"] = PENDING_REASON
                pending += 1
        out.append(json.dumps(r))

    if write and (patched or pending):
        tmp = history_path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(out) + ("\n" if out and not out[-1].endswith("\n") else ""))
        tmp.rename(history_path)

    return {"patched": patched, "marked_pending": pending, "skipped": skipped}


def main():
    summary = patch()
    print(json.dumps(summary, indent=2))
    if summary.get("marked_pending"):
        try:
            from predictions.fund import bugs
            bugs.log(
                "HIGH",
                "data.universe_price_history.bonk",
                "BONK ticks 35-41 carry wrong-pool quote ($0.022 vs real ~$0.0000054). "
                "Marked price_under_investigation_2026_06_06; do NOT trust the value. "
                "Investigate whether DexScreener primary-pool selection or upstream "
                "ingest is querying the wrong pool for BONK specifically.",
                context={"affected_ticks": sorted(t for _, t in PENDING_INVESTIGATION)},
            )
            print("Logged HIGH bug for BONK investigation.")
        except Exception as e:
            print(f"(could not log bug: {e})")


if __name__ == "__main__":
    main()
