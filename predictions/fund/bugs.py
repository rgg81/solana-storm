"""Centralized bug + operational-issue logging for SMAF.

Severity:
- CRITICAL: account corruption, money lost incorrectly, infinite loop risk
- HIGH:    silent failure that would propagate (e.g., stop not set on buy)
- MEDIUM:  data quality issue degrading decisions (rpc_failed, stale cache)
- LOW:     log-only, no functional impact (rate-limit retries, cache misses)

All bugs persist to predictions/fund/state/bugs.jsonl.
The healthcheck reads recent bugs and surfaces CRITICAL+HIGH to the user.
"""
from __future__ import annotations
import json, time
from pathlib import Path
from typing import Optional

_STATE_DIR = Path(__file__).resolve().parent / "state"
BUGS_PATH = _STATE_DIR / "bugs.jsonl"

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def log(severity: str, component: str, message: str,
         context: Optional[dict] = None, resolved: bool = False) -> dict:
    """Log a bug/issue. Returns the event dict."""
    assert severity in SEVERITIES
    event = {
        "timestamp": int(time.time()),
        "severity": severity,
        "component": component,
        "message": message,
        "resolved": resolved,
        "context": context or {},
    }
    BUGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = BUGS_PATH.read_text() if BUGS_PATH.exists() else ""
    tmp = BUGS_PATH.with_suffix(".tmp")
    tmp.write_text(existing + json.dumps(event) + "\n")
    tmp.rename(BUGS_PATH)
    return event


def recent(hours: int = 24, min_severity: str = "MEDIUM") -> list[dict]:
    """Return bugs logged in last N hours at min_severity or above."""
    if not BUGS_PATH.exists(): return []
    cutoff = int(time.time()) - hours * 3600
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    min_rank = sev_rank.get(min_severity, 2)
    out = []
    for line in BUGS_PATH.read_text().splitlines():
        if not line.strip(): continue
        try:
            ev = json.loads(line)
            if ev.get("timestamp", 0) < cutoff: continue
            if sev_rank.get(ev.get("severity", ""), 99) > min_rank: continue
            out.append(ev)
        except Exception:
            continue
    return out


def summary(hours: int = 24) -> dict:
    bugs = recent(hours, min_severity="LOW")
    by_sev = {s: 0 for s in SEVERITIES}
    by_component = {}
    for b in bugs:
        by_sev[b["severity"]] = by_sev.get(b["severity"], 0) + 1
        c = b.get("component", "unknown")
        by_component[c] = by_component.get(c, 0) + 1
    return {
        "window_hours": hours,
        "total": len(bugs),
        "by_severity": by_sev,
        "by_component": by_component,
        "unresolved_critical_high": [b for b in bugs
                                       if b["severity"] in ("CRITICAL", "HIGH")
                                       and not b.get("resolved")],
    }


if __name__ == "__main__":
    print(json.dumps(summary(), indent=2))
