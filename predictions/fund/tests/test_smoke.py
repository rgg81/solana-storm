"""Smoke import test for predictions.fund — catches packaging-level breakage."""

import importlib


def test_can_import_fund_modules():
    modules = [
        "predictions.fund.runner",
        "predictions.fund.audit",
        "predictions.fund.report",
        "predictions.fund.phase6_orchestrator",
        "predictions.fund.stage_phase2",
        "predictions.fund.stage_phase3",
        "predictions.fund.stage_phase4",
        "predictions.fund.stage_phase6",
        "predictions.fund.account",
        "predictions.fund.bugs",
        "predictions.fund.lessons_io",
        "predictions.fund.regime",
        "predictions.fund.performance",
        "predictions.fund.goals",
        "predictions.fund.universe_price_history",
    ]
    for name in modules:
        importlib.import_module(name)
