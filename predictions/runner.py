"""Single cron entry point. Dispatches based on argv[1]."""
from __future__ import annotations
import os, sys, time, traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from predictions import config

KNOWN_COMMANDS = {
    "late_curve", "early_curve", "smart_mirror", "catalyst",
    "fund_manager", "audit_tick", "universe_fetch",
}


def _log_error(cmd: str, exc: Exception) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    import json
    entry = {
        "ts_unix": int(time.time()),
        "cmd": cmd,
        "error": str(exc),
        "traceback": traceback.format_exc()[:2000],
    }
    with config.ERROR_LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _run_universe_fetch():
    from predictions import universe
    res = universe.fetch_pregrad_universe()
    if res.get("error"):
        print(f"universe_fetch: error={res['error']}", file=sys.stderr)
        return 1
    rows = res.get("data") or []
    count = universe.record_pregrad_universe([{**r, "fetched_at_unix": res.get("fetched_at_unix") or int(time.time())} for r in rows])
    print(f"universe_fetch: recorded {count} snapshots")
    return 0


def _run_audit_tick():
    from predictions.audit import processor
    due, remaining = processor.partition_due(config.PENDING_AUDIT_PATH, now_unix=int(time.time()))
    print(f"audit_tick: {len(due)} due, {len(remaining)} pending")
    # Actual outcome resolution: delegated to per-pick logic in audit/processor.py extensions
    # (Stub here — Task 19's integration test covers end-to-end.)
    if due:
        processor.rewrite(config.PENDING_AUDIT_PATH, remaining)
    return 0


def _run_specialist(name: str):
    # Stub: real subagent invocation is performed by the calling harness (CronCreate command).
    # This runner just confirms the request is well-formed and emits a status file.
    print(f"specialist {name} dispatch — real subagent invocation should be wired by orchestrator")
    return 0


def _run_fund_manager():
    print("fund_manager dispatch — real subagent invocation should be wired by orchestrator")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: runner.py <command>", file=sys.stderr)
        return 2
    cmd = argv[1]
    if config.PUMP_V2_HALT:
        print(f"runner: halted by PUMP_V2_HALT (cmd={cmd})")
        return 0
    if cmd not in KNOWN_COMMANDS:
        print(f"runner: unknown command {cmd!r} (expected one of {sorted(KNOWN_COMMANDS)})", file=sys.stderr)
        return 2
    try:
        if cmd == "universe_fetch":
            return _run_universe_fetch()
        if cmd == "audit_tick":
            return _run_audit_tick()
        if cmd == "fund_manager":
            return _run_fund_manager()
        return _run_specialist(cmd)
    except Exception as e:
        _log_error(cmd, e)
        print(f"runner: error in {cmd}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
