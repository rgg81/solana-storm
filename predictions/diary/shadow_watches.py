"""Shadow-watch storage. Tracks 'would-be BUY but vetoed by VALIDATED lesson' picks."""
from __future__ import annotations
import json, time
from pathlib import Path

from predictions import config


def write_shadow_watch(*, specialist: str, mint: str, pool: str,
                       would_be_conviction: str, vetoed_by: str,
                       entry_quote: int, entry_base: int,
                       recommended_exit: dict) -> str:
    config.SHADOW_WATCH_DIR.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    pick_id = f"{now}-{specialist}-{mint[:8]}"
    horizon_h = int(recommended_exit.get("hard_timeout_hours", 24))
    payload = {
        "pick_id": pick_id, "specialist": specialist,
        "mint": mint, "pool": pool,
        "would_be_conviction": would_be_conviction, "vetoed_by": vetoed_by,
        "entry_quote_lamports": entry_quote, "entry_base_lamports": entry_base,
        "entered_at_unix": now,
        "due_unix": now + horizon_h * 3600,
        "recommended_exit": recommended_exit,
    }
    body = "---\n" + "\n".join(f"{k}: {json.dumps(v)}" for k, v in payload.items()) + "\n---\n"
    path = config.SHADOW_WATCH_DIR / f"{pick_id}-shadow.md"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.rename(path)
    return pick_id


def list_pending(now_unix: int) -> list[dict]:
    """List shadow-watches with due_unix <= now_unix (audit horizon reached, awaiting attribution)."""
    if not config.SHADOW_WATCH_DIR.exists():
        return []
    out = []
    for p in config.SHADOW_WATCH_DIR.glob("*-shadow.md"):
        lines = p.read_text().splitlines()
        d = {}
        for ln in lines:
            if ln in ("---", ""):
                continue
            if ":" in ln:
                k, _, v = ln.partition(":")
                try:
                    d[k.strip()] = json.loads(v.strip())
                except Exception:
                    d[k.strip()] = v.strip()
        if int(d.get("due_unix") or 0) <= now_unix:
            out.append(d)
    return out
