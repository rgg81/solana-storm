"""Stale-specialist guard regression test.

stage_phase3 reads 4 specialist outputs from /tmp/smaf_*.json plus the
universe.json. If a specialist dispatch FAILS to write its /tmp file (e.g.,
network error mid-run), the previous tick's file remains. The current
stage_phase3 would silently load that stale file and compute consensus mixing
fresh + stale scores. This test guards that a freshness check raises before
the corrupted consensus is written.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from predictions.fund import stage_phase3


@pytest.fixture
def tmp_specialist_files(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    p2 = state / "tick_phase2_input.json"
    p2.write_text(json.dumps({"universe": ["PYTH"], "per_symbol": {"PYTH": {"dexscreener": {}}}}))

    # Patch the /tmp paths to inside tmp_path so the test doesn't pollute /tmp
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    patched_paths = {
        "ma_opt": str(tmp_dir / "smaf_market_analyst_optimist.json"),
        "ma_pes": str(tmp_dir / "smaf_market_analyst_pessimist.json"),
        "se_opt": str(tmp_dir / "smaf_solana_expert_optimist.json"),
        "se_pes": str(tmp_dir / "smaf_solana_expert_pessimist.json"),
        "univ":   str(tmp_dir / "smaf_universe.json"),
    }
    monkeypatch.setattr(stage_phase3, "TMP_PATHS", patched_paths)
    monkeypatch.setattr(stage_phase3, "STATE", state)
    return state, p2, patched_paths


def _write_specialist(path: str, ticker: str = "PYTH", score: float = 0.1):
    Path(path).write_text(json.dumps({
        "specialist": "stub",
        "scores": [{"ticker": ticker, "score": score}],
    }))


def _write_universe(path: str, ticker: str = "PYTH"):
    Path(path).write_text(json.dumps({
        "selected_symbols": [{"ticker": ticker, "bucket": "infrastructure"}],
    }))


def test_raises_when_specialist_file_is_older_than_phase2(tmp_specialist_files):
    state, p2, paths = tmp_specialist_files

    # Write specialist files FIRST (older), then touch phase2 (newer) so the
    # specialists look stale relative to the current tick's input.
    for key, path in paths.items():
        if key == "univ":
            _write_universe(path)
        else:
            _write_specialist(path)
    # Make specialists old: set their mtime 10 minutes ago.
    old_ts = time.time() - 600
    for path in paths.values():
        os.utime(path, (old_ts, old_ts))

    # phase2 is current (just written by fixture).
    with pytest.raises(stage_phase3.StaleSpecialistError) as exc:
        stage_phase3.stage()
    assert "stale" in str(exc.value).lower() or "older" in str(exc.value).lower()


def test_raises_when_a_specialist_file_is_missing(tmp_specialist_files):
    state, p2, paths = tmp_specialist_files
    # Only write 3 of 4 specialists + universe. The 4th is missing — same
    # observable as a stale dispatch (no fresh output).
    _write_specialist(paths["ma_opt"])
    _write_specialist(paths["ma_pes"])
    _write_specialist(paths["se_opt"])
    _write_universe(paths["univ"])
    # paths["se_pes"] intentionally NOT written

    with pytest.raises(stage_phase3.StaleSpecialistError) as exc:
        stage_phase3.stage()
    assert "missing" in str(exc.value).lower() or "se_pes" in str(exc.value)


def test_passes_when_all_specialists_fresh(tmp_specialist_files):
    state, p2, paths = tmp_specialist_files
    # Write ALL files AFTER phase2 input — they're fresher.
    time.sleep(0.05)  # ensure newer mtime
    for key, path in paths.items():
        if key == "univ":
            _write_universe(path)
        else:
            _write_specialist(path)

    # Should not raise the freshness check. (May raise later for other reasons
    # — like missing account state — which is fine; we only care the freshness
    # gate passed.)
    try:
        stage_phase3.stage()
    except stage_phase3.StaleSpecialistError:
        pytest.fail("freshness check raised on fresh specialist files")
    except Exception:
        # Any other exception is acceptable here — we only test the guard.
        pass
