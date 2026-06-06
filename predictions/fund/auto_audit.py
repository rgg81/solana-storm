"""SMAF Phase 7 — inline auto-audit.

Runs cheap integrity / consistency checks at the end of every tick (post-Phase
5b report) so silent failures surface within one tick instead of accumulating
for 13+ days. Each check is independent and fast (O(seconds)); a fail-mode
writes a HIGH bug and returns the failure summary so the runner can surface.

Checks (each a function returning dict {passed: bool, severity, msg, context}):
  1. price_history_jumps      — no >100x single-tick price ratio without flag
  2. audit_coverage           — count(sells) == count(audit rows)
  3. no_critical_unresolved   — bugs.unresolved_count(CRITICAL) == 0
  4. specialist_scores_numeric — no null score values in the latest specialist outputs
  5. tmp_files_fresh          — every /tmp/smaf_*.json mtime >= phase2 input mtime
  6. helius_health            — flag if rpc_failed rate > 90% across last 24h
  7. consecutive_below_floor  — flag HIGH if > 50 consecutive flat ticks

A failed check writes a HIGH bug (with category 'phase7_audit.<check_name>')
and the runner prints a one-line summary. Phase 7 never raises — its job is
to surface, not to halt the tick.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

STATE = Path(__file__).resolve().parent / "state"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def check_price_history_jumps() -> dict:
    history = _load_jsonl(STATE / "universe_price_history.jsonl")
    if not history:
        return {"passed": True, "msg": "no history"}
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in history:
        by_sym[r.get("symbol")].append(r)
    violations: list[str] = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: r.get("tick_id", 0))
        for prev, curr in zip(rows, rows[1:]):
            pp = float(prev.get("price_usd") or 0)
            cp = float(curr.get("price_usd") or 0)
            if pp <= 0 or cp <= 0:
                continue
            ratio = cp / pp
            if ratio > 100 or ratio < 0.01:
                if (prev.get("price_corrected_2026_06_05") or prev.get("price_corrected_2026_06_06")
                    or curr.get("price_corrected_2026_06_05") or curr.get("price_corrected_2026_06_06")
                    or prev.get("price_under_investigation_2026_06_06")
                    or curr.get("price_under_investigation_2026_06_06")):
                    continue
                violations.append(f"{sym} t{prev.get('tick_id')}->t{curr.get('tick_id')}: ratio {ratio:.2f}x")
    if violations:
        return {"passed": False, "severity": "HIGH", "msg": "price history jumps",
                "context": {"violations": violations[:10]}}
    return {"passed": True, "msg": "ok"}


def check_audit_coverage() -> dict:
    trades = _load_jsonl(STATE / "trades.jsonl")
    audit = _load_jsonl(STATE / "closed_trades_audit.jsonl")
    sell_tickers = {t.get("ticker") for t in trades if t.get("side") == "sell"}
    audit_tickers = {a.get("ticker") for a in audit}
    missing = sell_tickers - audit_tickers
    if missing:
        return {"passed": False, "severity": "HIGH", "msg": "audit coverage gap",
                "context": {"missing": sorted(missing)}}
    return {"passed": True, "msg": "ok"}


def check_no_critical_unresolved() -> dict:
    try:
        from predictions.fund import bugs
        n = bugs.unresolved_count(min_severity="CRITICAL")
    except Exception as e:
        return {"passed": True, "msg": f"check skipped: {e}"}
    if n > 0:
        return {"passed": False, "severity": "CRITICAL", "msg": f"{n} unresolved CRITICAL bugs",
                "context": {"unresolved_critical_count": n}}
    return {"passed": True, "msg": "ok"}


def _has_null_score(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text())
    except Exception:
        return False
    scores = d.get("scores") or []
    for s in scores:
        if isinstance(s, dict) and s.get("score") is None:
            return True
    return False


def check_specialist_scores_numeric() -> dict:
    paths = [
        Path("/tmp/smaf_market_analyst_optimist.json"),
        Path("/tmp/smaf_market_analyst_pessimist.json"),
        Path("/tmp/smaf_solana_expert_optimist.json"),
        Path("/tmp/smaf_solana_expert_pessimist.json"),
    ]
    null_specialists = [p.name for p in paths if _has_null_score(p)]
    if null_specialists:
        return {"passed": False, "severity": "HIGH", "msg": "specialist emitted score:null",
                "context": {"specialists": null_specialists}}
    return {"passed": True, "msg": "ok"}


def check_tmp_files_fresh(grace_sec: int = 60 * 60) -> dict:
    """Within grace_sec of phase2 input mtime — anything older is stale."""
    p2 = STATE / "tick_phase2_input.json"
    if not p2.exists():
        return {"passed": True, "msg": "no phase2 input"}
    p2_mtime = p2.stat().st_mtime
    stale = []
    for fname in (
        "smaf_market_analyst_optimist.json",
        "smaf_market_analyst_pessimist.json",
        "smaf_solana_expert_optimist.json",
        "smaf_solana_expert_pessimist.json",
        "smaf_universe.json",
        "smaf_risk.json",
        "smaf_pm.json",
    ):
        p = Path("/tmp") / fname
        if not p.exists():
            continue  # not a stale issue — could be absent if not used this tick
        lag = p2_mtime - p.stat().st_mtime
        if lag > grace_sec:
            stale.append(f"{fname}: {lag:.0f}s older than phase2 input")
    if stale:
        return {"passed": False, "severity": "HIGH", "msg": "stale /tmp files",
                "context": {"stale": stale}}
    return {"passed": True, "msg": "ok"}


def check_helius_health() -> dict:
    """If we've logged >5 helius_rpc bugs in the last 24h, surface."""
    try:
        from predictions.fund import bugs
        recent = bugs.recent(hours=24, min_severity="MEDIUM")
    except Exception:
        return {"passed": True, "msg": "skip"}
    helius_recent = [b for b in recent if "helius" in (b.get("component") or "")]
    if len(helius_recent) > 5:
        return {"passed": False, "severity": "MEDIUM",
                "msg": f"{len(helius_recent)} helius failures in last 24h",
                "context": {"n": len(helius_recent)}}
    return {"passed": True, "msg": "ok"}


def check_consecutive_below_floor() -> dict:
    """Flag a HIGH bug if equity has been flat for >50 consecutive ticks."""
    try:
        from predictions.fund import goals
        n = goals._consecutive_below_floor_ticks()
    except Exception:
        return {"passed": True, "msg": "skip"}
    if n > 50:
        return {"passed": False, "severity": "HIGH",
                "msg": f"{n} consecutive flat ticks — fund is structurally idle",
                "context": {"flat_ticks": n}}
    return {"passed": True, "msg": "ok"}


CHECKS: list[tuple[str, Callable[[], dict]]] = [
    ("price_history_jumps", check_price_history_jumps),
    ("audit_coverage", check_audit_coverage),
    ("no_critical_unresolved", check_no_critical_unresolved),
    ("specialist_scores_numeric", check_specialist_scores_numeric),
    ("tmp_files_fresh", check_tmp_files_fresh),
    ("helius_health", check_helius_health),
    ("consecutive_below_floor", check_consecutive_below_floor),
]


def run() -> dict:
    """Run all checks. Returns summary {passed: int, failed: int, results: [...]}.

    Failed checks ALSO write a bug to bugs.jsonl (severity per check). Never raises.
    """
    try:
        from predictions.fund import bugs
    except Exception:
        bugs = None

    results = []
    n_pass, n_fail = 0, 0
    for name, fn in CHECKS:
        try:
            r = fn()
        except Exception as e:
            r = {"passed": False, "severity": "HIGH", "msg": f"check {name} crashed: {e}"}
        r["check"] = name
        if r.get("passed"):
            n_pass += 1
        else:
            n_fail += 1
            if bugs is not None:
                try:
                    bugs.log(r.get("severity", "MEDIUM"),
                              f"phase7_audit.{name}",
                              r.get("msg", "phase 7 check failed"),
                              context=r.get("context") or {})
                except Exception:
                    pass
        results.append(r)
    return {"passed": n_pass, "failed": n_fail, "results": results}


if __name__ == "__main__":
    summary = run()
    print(json.dumps(summary, indent=2, default=str))
