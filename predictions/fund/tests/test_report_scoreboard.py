"""Scoreboard 4-specialist rendering test.

Historically report.py:Section 6 and lessons_io.summary_for_agent_prompt
iterated over ('market_analyst_optimist','market_analyst_pessimist',
'solana_expert') — the legacy unified key. audit.py writes
'solana_expert_optimist' and 'solana_expert_pessimist' (the 4-specialist
schema). Result: SE-Opt / SE-Pes rows were never rendered.

This test inspects the iteration source directly to guard against regression.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from predictions.fund import report as fund_report
from predictions.fund import lessons_io


def _iter_string_tuple_consts(source: str) -> list[tuple[str, ...]]:
    """Find every literal tuple of string constants in the source — that's the
    shape of the legacy scoreboard iteration."""
    tree = ast.parse(source)
    out: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple) and node.elts:
            if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
                out.append(tuple(e.value for e in node.elts))
    return out


def _legacy_only_iteration(source: str) -> bool:
    """Return True if the source still iterates over the legacy 3-tuple
    ('market_analyst_optimist','market_analyst_pessimist','solana_expert')
    WITHOUT also covering the split SE keys."""
    legacy = ("market_analyst_optimist", "market_analyst_pessimist", "solana_expert")
    tuples = _iter_string_tuple_consts(source)
    for t in tuples:
        # Find tuples that look like scoreboard iteration and check whether
        # they cover at least one split-SE key.
        if "market_analyst_optimist" in t and "market_analyst_pessimist" in t:
            covers_split = (
                "solana_expert_optimist" in t and "solana_expert_pessimist" in t
            )
            if not covers_split:
                return True
    return False


def test_report_scoreboard_covers_split_se_keys():
    src = Path(fund_report.__file__).read_text()
    assert not _legacy_only_iteration(src), (
        "report.py still iterates the legacy scoreboard tuple "
        "('market_analyst_optimist','market_analyst_pessimist','solana_expert') "
        "without covering solana_expert_optimist / solana_expert_pessimist. "
        "SE-Opt and SE-Pes scoreboard rows would never render."
    )


def test_lessons_io_summary_covers_split_se_keys():
    src = inspect.getsource(lessons_io.summary_for_agent_prompt)
    assert not _legacy_only_iteration(src), (
        "lessons_io.summary_for_agent_prompt still iterates the legacy "
        "scoreboard tuple. Update to include solana_expert_optimist and "
        "solana_expert_pessimist (legacy 'solana_expert' can remain as a "
        "back-compat fallback)."
    )
