"""vol_30d propagation tests (audit signature + stage_phase3 population).

History: every entry in tick_risk_input.json carried 30d_daily_vol_pct: 0 for
all symbols, even SOL whose regime_status string correctly said "SOL 30d vol:
2.91%". audit.audit_close fed a 0.05 literal to risk_calibration so the stop
multiplier auto-tuning was crippled (multi-agent review 2026-06-06).

Fix: stage_phase3 falls back to parsing the regime_status string when per-
symbol vol is missing; audit.snapshot_entry_consensus accepts an optional
vol_30d_daily_pct and audit_close prefers the stored entry value.
"""
from __future__ import annotations

import inspect

from predictions.fund import audit, stage_phase3


def test_audit_snapshot_signature_accepts_vol_30d():
    sig = inspect.signature(audit.snapshot_entry_consensus)
    assert "vol_30d_daily_pct" in sig.parameters


def test_audit_snapshot_records_vol_30d():
    snap = audit.snapshot_entry_consensus(
        ticker="PYTH",
        ma_opt_score=0.42, ma_pes_score=0.05,
        se_opt_score=0.20, se_pes_score=-0.10,
        risk_mgr_size_pct=5.0,
        market_disagreement=0.37, onchain_disagreement=0.30,
        vol_30d_daily_pct=2.91,
    )
    assert snap["vol_30d_daily_pct"] == 2.91


def test_audit_snapshot_records_vol_30d_none_when_not_provided():
    snap = audit.snapshot_entry_consensus(
        ticker="PYTH",
        ma_opt_score=0.0, ma_pes_score=0.0,
        se_opt_score=0.0, se_pes_score=0.0,
        risk_mgr_size_pct=0.0,
        market_disagreement=0.0, onchain_disagreement=0.0,
    )
    assert snap["vol_30d_daily_pct"] is None


def test_stage_phase3_regime_parser_recovers_sol_vol():
    regime_status = (
        "REGIME_STATUS:\n"
        "  SOL trend: strong_bear (price $63.20 vs SMA200 $102.41)\n"
        "  SOL 30d vol: 2.91% daily → normal\n"
        "  Universe correlation: 0.651 (6 pairs) → elevated_correlation\n"
    )
    assert stage_phase3._sol_30d_vol_from_regime(regime_status) == 2.91


def test_stage_phase3_regime_parser_returns_none_on_unparseable():
    assert stage_phase3._sol_30d_vol_from_regime("") is None
    assert stage_phase3._sol_30d_vol_from_regime("no vol here") is None
    assert stage_phase3._sol_30d_vol_from_regime(None) is None  # type: ignore[arg-type]
