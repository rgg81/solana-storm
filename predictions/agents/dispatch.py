"""Dispatch layer: builds prompts for subagents and (in production) calls the Agent tool.

In test/dry-run, `_invoke_agent` is mockable. In production runtime, it's expected to be
overridden by the harness (e.g., a wrapper that calls Claude SDK's Agent tool).
"""
from __future__ import annotations
import json
import time
from pathlib import Path

from predictions import config, universe
from predictions.agents import invoker, fm_allocation
from predictions.diary import lessons_io


def _invoke_agent(*, prompt: str, model: str = "sonnet") -> str:
    """Placeholder. Returns empty JSON object. Production overrides this with the Agent tool call.

    Tests mock this. The runner's actual production wiring sets this attribute to a function
    that calls the Claude Agent SDK or equivalent.
    """
    return '{"specialist": "_stub_", "picks": [], "error": "agent not wired"}'


def _format_subagent_prompt(specialist: str, context: dict) -> str:
    template = context["prompt_template"]
    inputs_block = json.dumps({
        "universe": context["universe"],
        "curve_history": context["curve_history"],
        "extras": context["extras"],
    }, indent=2)
    return (f"{template}\n\n## Current inputs (JSON)\n```json\n{inputs_block}\n```\n\n"
            f"## Current lessons.md\n```markdown\n{context['lessons_md']}\n```\n\n"
            f"Respond with the JSON output object only.")


def dispatch_specialist(specialist: str, *, universe_data: dict | None = None,
                        curve_history: dict | None = None,
                        extras: dict | None = None) -> dict:
    if universe_data is None:
        universe_data = universe.fetch_pregrad_universe()
    ctx = invoker.build_context(specialist, universe=universe_data,
                                 curve_history=curve_history, extras=extras)
    prompt = _format_subagent_prompt(specialist, ctx)
    output = _invoke_agent(prompt=prompt)
    parsed = invoker.parse_specialist_output(output)
    return parsed


def _collect_specialist_files() -> list[dict]:
    """Find the most recent specialist decision file per specialist."""
    decisions_dir = config._REPO_ROOT / "predictions" / "diary" / "decisions"
    if not decisions_dir.exists():
        return []
    by_specialist: dict[str, Path] = {}
    for p in decisions_dir.glob("*.md"):
        for spec in ("late_curve", "early_curve", "smart_mirror", "catalyst"):
            if f"-{spec}." in p.name:
                current = by_specialist.get(spec)
                if current is None or p.stat().st_mtime > current.stat().st_mtime:
                    by_specialist[spec] = p
    return [{"specialist": s, "path": str(p)} for s, p in by_specialist.items()]


def dispatch_fund_manager() -> dict:
    specialist_files = _collect_specialist_files()
    lessons_path = config._REPO_ROOT / "predictions" / "diary" / "lessons.md"
    fm = lessons_io.load_frontmatter(lessons_path)
    stats = {s: fm.get(s) or {} for s in ("late_curve", "early_curve", "smart_mirror", "catalyst")}
    weights = fm_allocation.specialist_weights(stats)
    total_audited = int(fm.get("total_picks_audited") or 0)
    cold_start = fm_allocation.is_cold_start_total(total_audited)

    extras = {
        "specialist_outputs": specialist_files,
        "specialist_weights": weights,
        "cold_start_mode": cold_start,
        "total_picks_audited": total_audited,
    }
    ctx = invoker.build_context("fund_manager", universe={"data": []}, curve_history=None, extras=extras)
    prompt = _format_subagent_prompt("fund_manager", ctx)
    output = _invoke_agent(prompt=prompt)
    return invoker.parse_specialist_output(output)
