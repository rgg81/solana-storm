"""Audit-tick processor: read pending.jsonl, audit due items, write outcomes."""
from __future__ import annotations
import json, os, time
from pathlib import Path
from typing import Iterable

from predictions import config


def enqueue(pending_path: Path, entry: dict) -> None:
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    with pending_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _read_all(pending_path: Path) -> list[dict]:
    if not pending_path.exists():
        return []
    out = []
    for line in pending_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def partition_due(pending_path: Path, now_unix: int) -> tuple[list[dict], list[dict]]:
    items = _read_all(pending_path)
    due = [it for it in items if int(it.get("due_unix") or 0) <= now_unix]
    remaining = [it for it in items if int(it.get("due_unix") or 0) > now_unix]
    return due, remaining


def rewrite(pending_path: Path, items: list[dict]) -> None:
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pending_path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    tmp.replace(pending_path)


def compute_realized_return(entry_quote: int, entry_base: int,
                            current_quote: int, current_base: int,
                            pool_closed: bool) -> float:
    if pool_closed:
        return -1.0
    if not (entry_quote and entry_base and current_base):
        return 0.0
    entry_price = entry_quote / entry_base
    current_price = current_quote / current_base
    return (current_price / entry_price) - 1.0


def write_outcome(pick_id: str, payload: dict) -> Path:
    out_dir = config._REPO_ROOT / "predictions" / "diary" / "outcomes"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{pick_id}-outcome.md"
    body = "---\n"
    for k, v in payload.items():
        # Strings + numbers stay raw; everything else (including booleans and containers) goes through json.dumps
        # so 'true'/'false' are YAML-canonical and embedded dicts/lists round-trip safely.
        body += f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float)) else v}\n"
    body += "---\n"
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(out_path)
    return out_path


import json as _json
import subprocess as _subprocess
import sys as _sys
import time as _time

from predictions.diary import lessons_io


def _fetch_current_pool_state(mint: str, pool: str) -> dict:
    """Run audit_outcome.py to get current pool state."""
    helper = config._REPO_ROOT / "predictions" / "helpers" / "audit_outcome.py"
    r = _subprocess.run(
        [_sys.executable, str(helper), mint, "--pool", pool],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return {"current_quote_reserve_lamports": 0, "current_base_reserve_lamports": 0,
                "pool_closed": True}
    try:
        d = _json.loads(r.stdout).get("data") or {}
        return {
            "current_quote_reserve_lamports": int(d.get("current_quote_reserve_lamports") or 0),
            "current_base_reserve_lamports": int(d.get("current_base_reserve_lamports") or 0),
            "pool_closed": bool(d.get("pool_closed")),
        }
    except Exception:
        return {"current_quote_reserve_lamports": 0, "current_base_reserve_lamports": 0,
                "pool_closed": True}


def _recompute_hit_rate(specialist_stats: dict, won: bool) -> dict:
    """Increment picks_audited and recompute all-time hit rate. Returns new stats dict."""
    picks = int(specialist_stats.get("picks_audited") or 0) + 1
    hits = int(specialist_stats.get("_hits_all_time") or 0)
    if won:
        hits += 1
    hr = (hits / picks) if picks else 0.0
    return {
        **specialist_stats,
        "picks_audited": picks,
        "_hits_all_time": hits,  # private accumulator for honest division
        "hit_rate_all_time": round(hr, 4),
        "cold_start_mode": picks < 30,
    }


def process_due_audits(*, now_unix: int, lessons_path) -> int:
    """Process due audits: fetch current state, write outcomes, update lessons.md.

    Returns count processed.
    """
    due, remaining = partition_due(config.PENDING_AUDIT_PATH, now_unix=now_unix)
    processed = 0
    fm = lessons_io.load_frontmatter(lessons_path)
    total = int(fm.get("total_picks_audited") or 0)

    for item in due:
        specialist = item.get("specialist", "unknown")
        mint = item.get("mint", "")
        pool = item.get("pool", "")
        state = _fetch_current_pool_state(mint, pool)
        ret = compute_realized_return(
            entry_quote=int(item.get("entry_quote_lamports") or 0),
            entry_base=int(item.get("entry_base_lamports") or 0),
            current_quote=state["current_quote_reserve_lamports"],
            current_base=state["current_base_reserve_lamports"],
            pool_closed=state["pool_closed"],
        )
        # 'won' = realized return >= specialist's effective target.
        # Simplification: treat any return >= +0.5 as a win across specialists.
        # Future iteration: read specialist's exit rule from item['recommended_exit'].
        won = ret >= 0.5

        write_outcome(item["pick_id"], {
            "pick_id": item["pick_id"],
            "specialist": specialist,
            "mint": mint, "pool": pool,
            "audited_at_unix": now_unix,
            "realized_return": round(ret, 4),
            "pool_closed": state["pool_closed"],
            "won": won,
        })

        cur_stats = dict(fm.get(specialist) or {})
        fm[specialist] = _recompute_hit_rate(cur_stats, won)
        total += 1
        processed += 1

    # Only rewrite lessons.md if we actually processed anything — avoids spurious
    # version bumps + last_updated churn on idle audit-tick fires (cron runs every 10min).
    if processed > 0:
        fm["total_picks_audited"] = total
        fm["version"] = int(fm.get("version") or 0) + 1
        fm["last_updated"] = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now_unix))
        lessons_io.write(lessons_path, fm, lessons_io.load_body(lessons_path))

    rewrite(config.PENDING_AUDIT_PATH, remaining)
    return processed
