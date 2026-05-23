"""Read/write lessons.md with YAML frontmatter, preserving body content."""
from __future__ import annotations
import re
from pathlib import Path
import yaml


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _split(path: Path) -> tuple[dict, str]:
    raw = path.read_text() if path.exists() else "---\n---\n"
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw
    fm_text, body = m.group(1), m.group(2)
    fm = yaml.safe_load(fm_text) or {}
    return fm, body


def load_frontmatter(path: Path) -> dict:
    fm, _ = _split(path)
    return fm


def load_body(path: Path) -> str:
    _, body = _split(path)
    return body


def write(path: Path, frontmatter: dict, body: str) -> None:
    tmp = path.with_suffix(".tmp")
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False).rstrip("\n")
    tmp.write_text(f"---\n{fm_text}\n---\n{body}")
    tmp.rename(path)


def update_frontmatter(path: Path, updates: dict) -> None:
    fm, body = _split(path)
    fm.update(updates)
    write(path, fm, body)


def update_specialist_stats(path: Path, specialist: str, stats: dict) -> None:
    fm, body = _split(path)
    cur = dict(fm.get(specialist) or {})
    cur.update(stats)
    fm[specialist] = cur
    write(path, fm, body)
