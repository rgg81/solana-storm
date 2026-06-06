"""Regression test for the snapshot_entry_consensus signature bug.

History: runner.execute_pm_orders has been calling
audit.snapshot_entry_consensus with kwarg names that don't match the function
signature (opt_score/pes_score/se_score/disagreement), causing every BUY's
entry-consensus snapshot to silently raise TypeError. The exception was caught
and downgraded to a MEDIUM bug, so the failure was invisible at runtime, but
every closed-trade audit that ran after such a BUY would have read an empty
entry_consensus dict and zero-attributed all 4 specialist scores plus both
disagreement fields.

This test asserts the call-site kwargs match the actual function signature.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from predictions.fund import audit
from predictions.fund import runner as fund_runner


CANONICAL_REQUIRED = {
    "ticker",
    "ma_opt_score",
    "ma_pes_score",
    "se_opt_score",
    "se_pes_score",
    "risk_mgr_size_pct",
    "market_disagreement",
    "onchain_disagreement",
}


def test_audit_signature_exposes_canonical_kwargs():
    sig = inspect.signature(audit.snapshot_entry_consensus)
    params = set(sig.parameters.keys())
    missing = CANONICAL_REQUIRED - params
    assert not missing, f"audit.snapshot_entry_consensus signature missing kwargs: {missing}"


def test_runner_calls_snapshot_with_canonical_kwargs():
    src = Path(fund_runner.__file__).read_text()
    tree = ast.parse(src)
    snapshot_calls = []

    class _Finder(ast.NodeVisitor):
        def visit_Call(self, node):
            f = node.func
            attr = getattr(f, "attr", None) or getattr(f, "id", None)
            if attr == "snapshot_entry_consensus":
                snapshot_calls.append({k.arg for k in node.keywords if k.arg})
            self.generic_visit(node)

    _Finder().visit(tree)
    assert snapshot_calls, "runner.py contains no call to snapshot_entry_consensus"

    sig_params = set(inspect.signature(audit.snapshot_entry_consensus).parameters.keys())
    for call_kwargs in snapshot_calls:
        unexpected = call_kwargs - sig_params
        assert not unexpected, (
            f"runner snapshot_entry_consensus call uses kwargs not in audit signature: {unexpected}. "
            f"audit signature kwargs: {sig_params}"
        )
        missing_required = CANONICAL_REQUIRED - call_kwargs
        assert not missing_required, (
            f"runner snapshot_entry_consensus call missing required kwargs: {missing_required}"
        )


def test_snapshot_records_all_four_specialists_and_both_disagreements():
    """The snapshot dict must surface all 4 specialist scores plus both
    disagreement fields — this is what runner is supposed to capture on every
    BUY."""
    snap = audit.snapshot_entry_consensus(
        ticker="JTO",
        ma_opt_score=0.40,
        ma_pes_score=-0.05,
        se_opt_score=0.22,
        se_pes_score=-0.10,
        risk_mgr_size_pct=5.0,
        market_disagreement=0.35,
        onchain_disagreement=0.23,
    )
    assert snap["ma_optimist_score"] == 0.40
    assert snap["ma_pessimist_score"] == -0.05
    assert snap["se_optimist_score"] == 0.22
    assert snap["se_pessimist_score"] == -0.10
    assert snap["market_disagreement"] == 0.35
    assert snap["onchain_disagreement"] == 0.23
    # consensus = mean of 4 specialist scores
    assert abs(snap["consensus"] - ((0.40 - 0.05 + 0.22 - 0.10) / 4)) < 1e-9
    # combined_uncertainty = max of the two disagreements
    assert snap["combined_uncertainty"] == max(0.35, 0.23)
