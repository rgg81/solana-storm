"""Unit tests for predictions.helpers.telegram_chatter."""

import json
import os
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "telegram_chatter.py"


def _run(args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HELPER), *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"helper failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_dry_run_returns_canned_fixture():
    result = _run(["STORM", "--dry-run"])
    assert result["error"] is None
    for field in (
        "ticker", "channels_polled", "channels_available",
        "channels_dropped", "total_mentions", "per_channel_mentions",
    ):
        assert field in result["data"]


def test_per_channel_mentions_sum_to_total():
    result = _run(["STORM", "--dry-run"])
    data = result["data"]
    assert sum(data["per_channel_mentions"].values()) == data["total_mentions"]


def test_dropped_channels_are_listed():
    result = _run(["STORM", "--dry-run"])
    data = result["data"]
    assert isinstance(data["channels_dropped"], list)
    assert data["channels_polled"] - data["channels_available"] == len(data["channels_dropped"])
