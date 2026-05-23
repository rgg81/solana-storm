import subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "predictions" / "runner.py"

def test_runner_halts_on_kill_switch(monkeypatch):
    env = {"PUMP_V2_HALT": "1", "PATH": ""}
    r = subprocess.run([sys.executable, str(RUNNER), "late_curve"],
                       capture_output=True, text=True, env=env, cwd=REPO)
    assert r.returncode == 0
    assert "halted" in r.stdout.lower() or "halted" in r.stderr.lower()

def test_runner_rejects_unknown_command():
    r = subprocess.run([sys.executable, str(RUNNER), "nonsense"],
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode != 0
