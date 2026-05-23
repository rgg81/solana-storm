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


def _fetch_curve_state(mint: str) -> dict:
    """Run pumpfun_coin_detail.py to read current bonding-curve state.

    Returns a dict with: complete, virtual_sol_reserves_lamports,
    virtual_token_reserves, bonding_curve_pct, fetched_at_unix, error.
    On failure, error is a non-empty string and reserves are zero.
    """
    helper = config._REPO_ROOT / "predictions" / "helpers" / "pumpfun_coin_detail.py"
    try:
        r = _subprocess.run(
            [_sys.executable, str(helper), mint],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        return {"complete": False, "virtual_sol_reserves_lamports": 0,
                "virtual_token_reserves": 0, "bonding_curve_pct": 0.0,
                "fetched_at_unix": 0, "error": f"subprocess failed: {e}"}
    if r.returncode != 0:
        return {"complete": False, "virtual_sol_reserves_lamports": 0,
                "virtual_token_reserves": 0, "bonding_curve_pct": 0.0,
                "fetched_at_unix": 0, "error": f"helper exit {r.returncode}"}
    try:
        payload = _json.loads(r.stdout)
    except Exception as e:
        return {"complete": False, "virtual_sol_reserves_lamports": 0,
                "virtual_token_reserves": 0, "bonding_curve_pct": 0.0,
                "fetched_at_unix": 0, "error": f"bad json: {e}"}
    if payload.get("error"):
        return {"complete": False, "virtual_sol_reserves_lamports": 0,
                "virtual_token_reserves": 0, "bonding_curve_pct": 0.0,
                "fetched_at_unix": 0, "error": str(payload.get("error"))}
    d = payload.get("data") or {}
    return {
        "complete": bool(d.get("complete")),
        "virtual_sol_reserves_lamports": int(d.get("virtual_sol_reserves_lamports") or 0),
        "virtual_token_reserves": int(d.get("virtual_token_reserves") or 0),
        "bonding_curve_pct": float(d.get("bonding_curve_pct") or 0.0),
        "fetched_at_unix": int(d.get("fetched_at_unix") or 0),
        "error": None,
    }


def _lookup_graduated_pool(mint: str) -> str | None:
    """Query Dune's pump_call_migrate for this mint's AMM pool. Returns pool address or None."""
    try:
        from bootstrap.dune_client import DuneClient
        from bootstrap.config import load_config as load_bcfg
        sql = (
            "SELECT account_pool FROM pumpdotfun_solana.pump_call_migrate "
            f"WHERE account_mint = '{mint}' LIMIT 1"
        )
        client = DuneClient(load_bcfg())
        rows, _ = client.run_sql(sql)
        if rows:
            return rows[0].get("account_pool")
        return None
    except Exception:
        return None


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
        kind = item.get("kind", "standard")

        graduated_during_hold = False
        if kind == "curve_stage":
            curve = _fetch_curve_state(mint)
            if curve.get("error"):
                # Couldn't fetch — treat as rugged.
                ret = -1.0
                pool_closed = True
            elif curve["complete"]:
                # Token graduated between entry and audit. Look up the AMM pool.
                graduated_during_hold = True
                grad_pool = _lookup_graduated_pool(mint)
                if grad_pool:
                    pool = grad_pool
                    state = _fetch_current_pool_state(mint, grad_pool)
                    ret = compute_realized_return(
                        entry_quote=int(item.get("entry_quote_lamports") or 0),
                        entry_base=int(item.get("entry_base_lamports") or 0),
                        current_quote=state["current_quote_reserve_lamports"],
                        current_base=state["current_base_reserve_lamports"],
                        pool_closed=state["pool_closed"],
                    )
                    pool_closed = state["pool_closed"]
                else:
                    # Graduated but pool not yet in Dune (publisher lag) — neutral fallback.
                    ret = 0.0
                    pool_closed = False
            else:
                # Still on curve — compute price ratio.
                entry_q = int(item.get("entry_quote_lamports") or 0)
                entry_b = int(item.get("entry_base_lamports") or 0)
                curr_q = curve["virtual_sol_reserves_lamports"]
                curr_b = curve["virtual_token_reserves"]
                if entry_q and entry_b and curr_b:
                    entry_price = entry_q / entry_b
                    curr_price = curr_q / curr_b
                    ret = (curr_price / entry_price) - 1.0
                else:
                    ret = 0.0
                pool_closed = False
        else:
            # Existing standard path: AMM pool state via audit_outcome.py.
            state = _fetch_current_pool_state(mint, pool)
            ret = compute_realized_return(
                entry_quote=int(item.get("entry_quote_lamports") or 0),
                entry_base=int(item.get("entry_base_lamports") or 0),
                current_quote=state["current_quote_reserve_lamports"],
                current_base=state["current_base_reserve_lamports"],
                pool_closed=state["pool_closed"],
            )
            pool_closed = state["pool_closed"]

        # 'won' = realized return >= specialist's effective target.
        # Simplification: treat any return >= +0.5 as a win across specialists.
        # Future iteration: read specialist's exit rule from item['recommended_exit'].
        won = ret >= 0.5

        outcome_payload = {
            "pick_id": item["pick_id"],
            "kind": kind,
            "specialist": specialist,
            "mint": mint, "pool": pool,
            "audited_at_unix": now_unix,
            "realized_return": round(ret, 4),
            "pool_closed": pool_closed,
            "won": won,
        }
        if kind == "curve_stage":
            outcome_payload["graduated_during_hold"] = graduated_during_hold
        write_outcome(item["pick_id"], outcome_payload)

        cur_stats = dict(fm.get(specialist) or {})
        fm[specialist] = _recompute_hit_rate(cur_stats, won)
        total += 1
        processed += 1

    # Shadow-watch processing: audit "would-be BUY but vetoed by VALIDATED lesson" picks
    # at their horizon. If the realized outcome contradicts the veto (token would have
    # appreciated), log the disconfirm as evidence toward eventual lesson refinement.
    from predictions.diary import shadow_watches as _shadow
    shadow_items = _shadow.list_pending(now_unix=now_unix)
    for sw in shadow_items:
        try:
            state = _fetch_current_pool_state(sw.get("mint", ""), sw.get("pool", ""))
            entry_q = int(sw.get("entry_quote_lamports") or 0)
            entry_b = int(sw.get("entry_base_lamports") or 0)
            ret = compute_realized_return(
                entry_quote=entry_q, entry_base=entry_b,
                current_quote=state["current_quote_reserve_lamports"],
                current_base=state["current_base_reserve_lamports"],
                pool_closed=state["pool_closed"],
            )
            # The shadow-watch IS its own audit record: write an outcome with a marker so
            # downstream lesson-refinement tooling can find them. Don't increment per-specialist
            # picks_audited (these were NOT actual picks, just counterfactuals).
            write_outcome(sw.get("pick_id", f"shadow-{int(_time.time())}"), {
                "pick_id": sw.get("pick_id", ""),
                "kind": "shadow_watch",
                "specialist": sw.get("specialist", "unknown"),
                "would_be_conviction": sw.get("would_be_conviction", ""),
                "vetoed_by": sw.get("vetoed_by", ""),
                "mint": sw.get("mint", ""),
                "audited_at_unix": now_unix,
                "realized_return": round(ret, 4),
                "pool_closed": state["pool_closed"],
                # Disconfirms the veto only if return >= 0.5 (i.e., the would-be-BUY would have hit target)
                "disconfirms_veto": ret >= 0.5,
            })
            processed += 1
        except Exception:
            continue
        # Remove the shadow-watch file after auditing so it doesn't get re-processed.
        try:
            shadow_path = config.SHADOW_WATCH_DIR / f"{sw.get('pick_id', '')}-shadow.md"
            if shadow_path.exists():
                shadow_path.unlink()
        except Exception:
            pass

    # Only rewrite lessons.md if we actually processed anything — avoids spurious
    # version bumps + last_updated churn on idle audit-tick fires (cron runs every 10min).
    if processed > 0:
        fm["total_picks_audited"] = total
        fm["version"] = int(fm.get("version") or 0) + 1
        fm["last_updated"] = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(now_unix))
        lessons_io.write(lessons_path, fm, lessons_io.load_body(lessons_path))

    rewrite(config.PENDING_AUDIT_PATH, remaining)
    return processed
