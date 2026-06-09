"""Phase 7 helius_health recalibration.

Before: fired if >5 helius bugs/24h. With Helius live on the free tier, ~5/11
symbols time out every tick (each logging retry-failure bugs across multiple RPC
methods), so the check fired on EVERY full tick — accepted partial coverage, not
a real outage. A persistently-firing MEDIUM desensitizes an audit the fund's
stop-conditions depend on.

After: fire only on REAL blindness — Helius failures logged AND zero symbols have
live holder data in the latest phase2 input. Partial coverage (≥1 live read)
passes. This still catches the original full-blindness case (config unset → 0
live + config bugs) while staying quiet on free-tier rate-limiting.
"""
from __future__ import annotations

import json

import pytest

from predictions.fund import auto_audit


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_audit, "STATE", tmp_path)
    return tmp_path


def _write_phase2(path, live_syms, blind_syms):
    per = {}
    for s in live_syms:
        per[s] = {"holder_distribution": {"top_10_pct": 50.0, "concentrated": False}}
    for s in blind_syms:
        per[s] = {"holder_distribution": {"helius_status": "rpc_failed"}}
    (path / "tick_phase2_input.json").write_text(json.dumps({"per_symbol": per}))


def _stub_bugs(monkeypatch, n_helius):
    import predictions.fund.bugs as bugs
    rows = [{"component": "helius_rpc", "severity": "MEDIUM", "message": "timeout"}
            for _ in range(n_helius)]
    monkeypatch.setattr(bugs, "recent", lambda hours=24, min_severity="MEDIUM": rows)


def test_partial_coverage_passes(isolated_state, monkeypatch):
    """≥1 live read + many failures = accepted free-tier partial coverage → PASS."""
    _write_phase2(isolated_state, live_syms=["JTO", "RENDER"], blind_syms=["SOL", "JUP", "BONK"])
    _stub_bugs(monkeypatch, n_helius=23)
    r = auto_audit.check_helius_health()
    assert r["passed"] is True


def test_full_blindness_fails(isolated_state, monkeypatch):
    """0 live reads + failures = real outage → FAIL."""
    _write_phase2(isolated_state, live_syms=[], blind_syms=["SOL", "JTO", "JUP", "BONK"])
    _stub_bugs(monkeypatch, n_helius=12)
    r = auto_audit.check_helius_health()
    assert r["passed"] is False
    assert r["severity"] == "MEDIUM"
    assert r["context"]["live"] == 0


def test_no_failures_passes(isolated_state, monkeypatch):
    """No helius bugs → nothing to flag, even if (hypothetically) 0 live."""
    _write_phase2(isolated_state, live_syms=[], blind_syms=["SOL"])
    _stub_bugs(monkeypatch, n_helius=0)
    r = auto_audit.check_helius_health()
    assert r["passed"] is True


def test_no_phase2_input_passes(isolated_state, monkeypatch):
    """No phase2 input (fresh state) → skip, pass."""
    _stub_bugs(monkeypatch, n_helius=8)
    r = auto_audit.check_helius_health()
    assert r["passed"] is True
