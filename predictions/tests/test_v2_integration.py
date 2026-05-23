"""End-to-end smoke test in REHEARSAL mode (no live network)."""
import json, os, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "predictions" / "runner.py"


def _run(cmd: str, env_extra: dict = None):
    env = {**os.environ, "PUMP_PREDICTION_REHEARSAL": "1", "PUMP_V2_HALT": "0"}
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([sys.executable, str(RUNNER), cmd],
                       capture_output=True, text=True, env=env, cwd=REPO)
    return r


def test_universe_fetch_rehearsal_writes_db():
    r = _run("universe_fetch")
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert "recorded" in r.stdout

def test_audit_tick_completes_without_pending():
    r = _run("audit_tick")
    assert r.returncode == 0, r.stderr
    assert "audit_tick" in r.stdout

def test_kill_switch_blocks_all_commands():
    r = _run("late_curve", env_extra={"PUMP_V2_HALT": "1"})
    assert r.returncode == 0
    assert "halted" in r.stdout.lower()
