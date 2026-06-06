"""Mark known-fixed bugs as resolved in state/bugs.jsonl.

Multi-agent review 2026-06-06 surfaced 59 unresolved entries (1 CRITICAL,
3 HIGH) for which the underlying cause has since been fixed at source.
This script marks them resolved with a note pointing at the fix commit.
Idempotent: rows already resolved are skipped.
"""
from __future__ import annotations

import json
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state"
BUGS = STATE / "bugs.jsonl"


# Resolution policy: (substring_to_match_in_message, severity_filter, note).
# severity_filter=None matches any severity. Substring match for tolerance to
# minor message variations.
RESOLUTIONS = [
    (
        "Missing agent prompt: market_analyst.md",
        "CRITICAL",
        "Agent prompts migrated to market_analyst_optimist.md + market_analyst_pessimist.md. Legacy unified prompt is intentionally absent.",
    ),
    (
        "SOLANA_RPC_URL not set",
        "HIGH",
        "Helius config issue; replaced by throttled 'helius_rpc.config' watchdog logged by helpers/onchain_stats.py:_rpc (commit Phase D.3).",
    ),
    (
        "Malformed PM trade order",
        "HIGH",
        "One-shot JTO stop trigger order_type irregularity; trade did execute (trades.jsonl). Cross-checked in audit coverage test.",
    ),
    (
        "slippage 10.00% > 1.5% cap",
        "HIGH",
        "Caused by liq=0 lookup at SELL time; addressed at source per CLAUDE.md note and codified in test_audit_coverage.py.",
    ),
]


def _matches(ev: dict, substring: str, severity_filter: str | None) -> bool:
    if severity_filter and ev.get("severity") != severity_filter:
        return False
    return substring in (ev.get("message") or "")


def main() -> dict:
    if not BUGS.exists():
        return {"resolved": 0, "skipped": 0, "note": "no bugs.jsonl"}
    from predictions.fund import bugs as bugs_mod

    rows = []
    for line in BUGS.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue

    resolved = 0
    skipped = 0
    for r in rows:
        if r.get("resolved"):
            continue
        for substring, sev_filter, note in RESOLUTIONS:
            if _matches(r, substring, sev_filter):
                ok = bugs_mod.mark_resolved(r.get("timestamp"), note)
                if ok:
                    resolved += 1
                else:
                    skipped += 1
                break

    return {"resolved": resolved, "skipped": skipped}


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
