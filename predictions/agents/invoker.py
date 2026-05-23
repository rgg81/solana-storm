"""Subagent invocation harness. Loads markdown prompt templates and builds context dicts.

NOTE: actual Claude `Agent` tool invocation happens from the runner. This module is
the prep + parse layer so it can be unit-tested without hitting the LLM.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from predictions import config

_PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    path = _PROMPT_DIR / f"{name}.md"
    return path.read_text()


def _load_lessons() -> str:
    p = config._REPO_ROOT / "predictions" / "diary" / "lessons.md"
    return p.read_text() if p.exists() else ""


def build_context(specialist: str, *, universe: dict, curve_history: dict | None = None,
                  extras: dict[str, Any] | None = None) -> dict:
    return {
        "specialist": specialist,
        "prompt_template": load_prompt(specialist),
        "lessons_md": _load_lessons(),
        "universe": universe,
        "curve_history": curve_history or {},
        "extras": extras or {},
    }


def parse_specialist_output(stdout: str) -> dict:
    """Parse the JSON the specialist subagent prints. Tolerant of leading/trailing text."""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < 0:
        return {"error": "no JSON object found in subagent output", "raw": stdout[:500]}
    try:
        return json.loads(stdout[start:end + 1])
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse: {e}", "raw": stdout[start:end + 1][:500]}
