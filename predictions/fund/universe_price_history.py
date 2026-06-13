"""Universe price history — append-only per-tick snapshot of every universe symbol.

This is the **counterfactual ledger**. After every tick's Phase 5 executes, we record
the full universe state: prices, consensus scores, decision tag, raw RM reason.
Future ticks read this to answer: "did the rejection N hours ago turn out to be right?"

State file: predictions/fund/state/universe_price_history.jsonl (gitignored)

One row per (tick_id, symbol):
{
  "tick_id": int,                      # equity.jsonl line count at snapshot time
  "ts": int,                           # unix epoch
  "iso_utc": str,
  "symbol": str,
  "price_usd": float,
  "vol_24h_usd": float,
  "liq_usd_main_pool": float,
  "consensus": float,                  # 4-way mean
  "ma_optimist": float,
  "ma_pessimist": float,
  "se_optimist": float,
  "se_pessimist": float,
  "market_disagreement": float,
  "onchain_disagreement": float,
  "combined_uncertainty": float,
  "decision_tag": str,                 # BUY_EXECUTED | SELL_EXECUTED | HOLD | REJECT_*
  "rm_reason": str,                    # raw verbatim from Risk Manager
  "risk_mgr_max_size_pct": float | None,  # what RM would have sized at (None if rejected pre-sizing)
  "regime_label": str | None,          # "strong_bear" etc — for slicing later
}
"""
from __future__ import annotations
import json, time
from pathlib import Path

_STATE_DIR = Path(__file__).resolve().parent / "state"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_PATH = _STATE_DIR / "universe_price_history.jsonl"
_RISK_JSON = Path("/tmp/smaf_risk.json")

# A single-tick price move beyond this ratio (or its reciprocal) is a corrupt
# data feed, not real volatility — nothing in our universe moves 100x in 6h. A
# corrupt DexScreener response (tick-121: ~4800x for JUP/PYTH/PUMP/BONK) must not
# poison the counterfactual ledger. Matches the Phase 7 price_history_jumps gate.
_MAX_TICK_PRICE_RATIO = 100.0


def _guard_price(symbol: str, price: float, prior_price: float | None) -> tuple[float, str | None]:
    """Return (clean_price, reason). `reason` is:
      - None            → the raw price is used as-is (normal move, or first-seen).
      - "corrupt_jump"  → an implausible >100x/<1/100x move vs the symbol's prior
                          recorded price; carry `prior_price` forward (tick-121).
      - "fetch_failure" → the feed returned 0/missing (a DexScreener miss, e.g. SPX
                          at tick-132); carry `prior_price` forward rather than
                          poison the ledger with a $0 row — a $0 also produces
                          nonsensical forward what-if deltas for that symbol.
    First-seen symbols with no usable prior pass through with reason None."""
    has_prior = bool(prior_price and prior_price > 0)
    # Fetch failure / missing price: carry the prior forward if we have one.
    if not price or price <= 0:
        return (prior_price, "fetch_failure") if has_prior else (price, None)
    # Implausible jump vs the prior recorded price.
    if has_prior:
        ratio = price / prior_price
        if ratio > _MAX_TICK_PRICE_RATIO or ratio < 1.0 / _MAX_TICK_PRICE_RATIO:
            return prior_price, "corrupt_jump"
    return price, None


def _last_prices() -> dict[str, float]:
    """Most recent recorded price_usd per symbol (for the corruption guard)."""
    out: dict[str, float] = {}
    for r in load_all():
        sym = r.get("symbol")
        p = r.get("price_usd")
        if sym and isinstance(p, (int, float)) and p > 0:
            out[sym] = float(p)
    return out


def _append(row: dict) -> None:
    existing = HISTORY_PATH.read_text() if HISTORY_PATH.exists() else ""
    tmp = HISTORY_PATH.with_suffix(HISTORY_PATH.suffix + ".tmp")
    tmp.write_text(existing + json.dumps(row, default=str) + "\n")
    tmp.rename(HISTORY_PATH)


def snapshot_tick(tick_id: int, risk_input: dict, pm_output: dict,
                   regime_label: str | None = None) -> int:
    """Snapshot every universe symbol from this tick. Idempotent guard: refuse to
    snapshot if the last row's tick_id matches (so re-runs don't double-write).
    Returns N rows written."""
    if HISTORY_PATH.exists():
        last_line = ""
        for line in HISTORY_PATH.read_text().splitlines()[-1:]:
            last_line = line
        if last_line.strip():
            try:
                if json.loads(last_line).get("tick_id") == tick_id:
                    return 0
            except Exception:
                pass

    per_sym = risk_input.get("specialist_consensus_per_symbol", {})
    if not per_sym:
        return 0

    # Build the decision tag from PM trades + RM rejections
    decisions: dict[str, tuple[str, str, float | None]] = {}

    # RM gave us new_entry_recommendations + existing_positions + rejections
    # Look in /tmp/smaf_risk.json for the source of truth (rm_reason)
    rm_raw = {}
    rm_path = _RISK_JSON
    if rm_path.exists():
        try:
            rm = json.loads(rm_path.read_text())
            for rej in rm.get("rejections", []) or []:
                if isinstance(rej, dict) and rej.get("ticker"):
                    rm_raw[rej["ticker"]] = (rej.get("reason") or rej.get("rationale") or "")
            for rec in rm.get("new_entry_recommendations", []) or []:
                if isinstance(rec, dict) and rec.get("ticker"):
                    decisions[rec["ticker"]] = ("RM_APPROVED_ENTRY",
                                                 (rec.get("reason") or rec.get("rationale") or ""),
                                                 rec.get("max_size_pct"))
            for ex in rm.get("existing_positions", []) or []:
                if isinstance(ex, dict) and ex.get("ticker"):
                    act = (ex.get("action") or "").upper()
                    decisions[ex["ticker"]] = (f"RM_EXISTING_{act}",
                                                 (ex.get("reason") or ""),
                                                 None)
        except Exception:
            pass

    # PM trades override RM (since they are the actual execution)
    for t in pm_output.get("trades", []) or []:
        if not isinstance(t, dict): continue
        ticker = t.get("ticker")
        side = (t.get("side") or "").lower()
        if ticker and side in ("buy", "sell"):
            tag = f"{side.upper()}_EXECUTED"
            reason = (t.get("reason") or "")
            decisions[ticker] = (tag, reason, None)

    # Tag anything else as REJECTED (with raw RM reason)
    n = 0
    ts = int(time.time())
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    prior_prices = _last_prices()
    for ticker, sd in per_sym.items():
        if ticker in decisions:
            tag, reason, size = decisions[ticker]
        else:
            tag = "REJECT"
            reason = rm_raw.get(ticker, "")
            size = None

        raw_price = float(sd.get("current_price_usd") or 0)
        price_usd, guard_reason = _guard_price(ticker, raw_price, prior_prices.get(ticker))

        row = {
            "tick_id": tick_id,
            "ts": ts,
            "iso_utc": iso,
            "symbol": ticker,
            "price_usd": price_usd,
            "vol_24h_usd": None,  # not in risk input; skip for now
            "liq_usd_main_pool": float(sd.get("liq_usd_main_pool") or 0),
            "consensus": float(sd.get("consensus") or 0),
            "ma_optimist": float(sd.get("ma_optimist_score") or 0),
            "ma_pessimist": float(sd.get("ma_pessimist_score") or 0),
            "se_optimist": float(sd.get("se_optimist_score") or 0),
            "se_pessimist": float(sd.get("se_pessimist_score") or 0),
            "market_disagreement": float(sd.get("market_disagreement") or 0),
            "onchain_disagreement": float(sd.get("onchain_disagreement") or 0),
            "combined_uncertainty": float(sd.get("combined_uncertainty") or 0),
            "decision_tag": tag,
            "rm_reason": reason[:500] if isinstance(reason, str) else "",
            "risk_mgr_max_size_pct": size,
            "regime_label": regime_label,
        }
        if guard_reason == "corrupt_jump":
            # Carried the prior price forward; keep the raw value for forensics.
            row["price_corrupt_guard"] = True
            row["original_corrupt_price_usd"] = raw_price
        elif guard_reason == "fetch_failure":
            # Feed returned 0/missing; carried the prior price forward.
            row["price_fetch_failure_carryforward"] = True
            row["original_fetch_failure_price_usd"] = raw_price
        _append(row)
        n += 1
    return n


def load_all() -> list[dict]:
    """Read every row. Used by stage_phase6 for windowed lookups."""
    if not HISTORY_PATH.exists(): return []
    rows = []
    for line in HISTORY_PATH.read_text().splitlines():
        if not line.strip(): continue
        try: rows.append(json.loads(line))
        except Exception: pass
    return rows


def load_for_symbol(symbol: str, limit: int | None = None) -> list[dict]:
    rows = [r for r in load_all() if r.get("symbol") == symbol]
    return rows[-limit:] if limit else rows


def latest_tick_id() -> int | None:
    """Return the most recent tick_id snapshotted, or None if empty."""
    rows = load_all()
    if not rows: return None
    return max(r.get("tick_id", 0) for r in rows)
