"""Single cron entry point. Dispatches based on argv[1]."""
from __future__ import annotations
import json, os, sys, time, traceback
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
    lessons = config._REPO_ROOT / "predictions" / "diary" / "lessons.md"
    n = processor.process_due_audits(now_unix=int(time.time()), lessons_path=lessons)
    print(f"audit_tick: processed {n} due audits")
    return 0


def _run_specialist(name: str):
    from predictions.agents import dispatch
    result = dispatch.dispatch_specialist(name)
    # Write specialist output to a decision file
    decisions_dir = config._REPO_ROOT / "predictions" / "diary" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d-%H-%M", time.gmtime())
    out = decisions_dir / f"{ts}-{name}.md"
    body = "---\n" + "\n".join(f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float, bool)) else v}"
                                for k, v in result.items()) + "\n---\n"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(out)
    print(f"specialist {name}: wrote {out.name}")
    return 0


def _run_fund_manager():
    from predictions.agents import dispatch
    result = dispatch.dispatch_fund_manager()
    decisions_dir = config._REPO_ROOT / "predictions" / "diary" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d-%H-%M", time.gmtime())
    out = decisions_dir / f"{ts}-fund_manager.md"
    body = "---\n" + "\n".join(f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float, bool)) else v}"
                                for k, v in result.items()) + "\n---\n"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(out)
    # Update last_fm_cycle marker
    config.LAST_FM_CYCLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.LAST_FM_CYCLE_PATH.write_text(str(int(time.time())))
    print(f"fund_manager: wrote {out.name}")
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
