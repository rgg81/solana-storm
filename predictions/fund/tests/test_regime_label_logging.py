"""Regime label must be derived from the correct producer key.

Bug (found by the 2026-06-11 no-trade review, run-id wf_a7795ce7): the Phase 6
ledger snapshot logged regime_label=null in 100% of rows (0/651). Root cause was
a producer/consumer key mismatch: regime.detect_sol_regime() returns the trend
under the key "sol_trend", but phase6_orchestrator read ".get('trend')" plus a
regime_cache.json['sol_regime']['trend'] path that is never written (the cache
holds only raw price arrays). Both paths returned None, silently blinding every
regime-sliced audit the fund's self-review depends on. Audit-only — the Risk
Manager receives the regime via its prompt, so no trade decision was affected.

The contract test below is the real guard: if anyone renames the producer key in
regime.py without updating the consumer, it fails immediately (data-shape-mismatch
discipline, CLAUDE.md).
"""
from __future__ import annotations

from predictions.fund import phase6_orchestrator, regime


def test_resolve_uses_sol_trend_key():
    """THE FIX: derive from the real producer key 'sol_trend'."""
    payload = {"sol_trend": "strong_bear", "sol_vol_regime": "normal"}
    assert phase6_orchestrator._resolve_regime_label(None, lambda: payload) == "strong_bear"


def test_resolve_wrong_key_yields_none_not_crash():
    """A payload missing 'sol_trend' (e.g. the old 'trend'-only shape) yields None
    gracefully — documents the exact bug shape that produced 0/651 labels."""
    assert phase6_orchestrator._resolve_regime_label(None, lambda: {"trend": "x"}) is None


def test_resolve_passthrough_when_label_provided():
    """An explicit caller-supplied label is never overridden by the detector."""
    assert phase6_orchestrator._resolve_regime_label(
        "calm_bull", lambda: {"sol_trend": "strong_bear"}) == "calm_bull"


def test_resolve_detector_raises_is_swallowed():
    def boom():
        raise RuntimeError("no network")
    assert phase6_orchestrator._resolve_regime_label(None, boom) is None


def test_detect_sol_regime_contract_exposes_sol_trend():
    """CONTRACT GUARD: the live producer must expose the key the resolver reads.
    If regime.detect_sol_regime() ever renames 'sol_trend', this fails before the
    null-label regression can ship again."""
    out = regime.detect_sol_regime()
    assert "sol_trend" in out, f"producer key drift — keys were {sorted(out)}"


def test_resolve_against_live_detector_is_nonnull():
    """End-to-end: the wired resolver returns a real label from the live detector
    (the exact path that was logging null)."""
    label = phase6_orchestrator._resolve_regime_label(None)
    assert label is not None and isinstance(label, str) and label
