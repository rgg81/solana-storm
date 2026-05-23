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
        body += f"{k}: {json.dumps(v) if not isinstance(v, (str, int, float, bool)) else v}\n"
    body += "---\n"
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(out_path)
    return out_path
