"""Shared universe API: pregrad (pump.fun /coins) + graduated (Dune)."""
from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path

from predictions import config
from predictions.state import curve_history

_HELPERS = Path(__file__).resolve().parent / "helpers"


def _run_helper(name: str, args: list[str] = ()) -> dict:
    cmd = [sys.executable, str(_HELPERS / name)] + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        return {"data": None, "error": f"{name} exit={res.returncode}: {res.stderr[:300]}"}
    try:
        return json.loads(res.stdout)
    except Exception as e:
        return {"data": None, "error": f"{name} parse error: {e}"}


def fetch_pregrad_universe() -> dict:
    return _run_helper("pumpfun_curve_universe.py",
                        ["--dry-run"] if config.is_rehearsal() else [])


def fetch_graduated_universe() -> dict:
    return _run_helper("recent_graduations.py",
                        ["--dry-run"] if config.is_rehearsal() else [])


def record_pregrad_universe(rows: list[dict]) -> int:
    db = config.CURVE_HISTORY_DB
    curve_history.init_db(db)
    count = 0
    for r in rows:
        curve_history.record_snapshot(
            db,
            mint=r.get("mint", ""),
            fetched_at_unix=int(r.get("fetched_at_unix") or time.time()),
            bonding_curve_pct=r.get("bonding_curve_pct"),
            market_cap_sol=r.get("market_cap_sol"),
            reply_count=r.get("reply_count"),
            recent_trades_count=r.get("recent_trades_count"),
        )
        count += 1
    return count
