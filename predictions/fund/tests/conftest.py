"""Shared pytest fixtures for predictions.fund tests.

Fixtures here build the minimum viable shapes of the phase-input / output JSONs
so per-bug tests can construct realistic states without re-deriving the schema.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    """Redirect predictions.fund.STATE_DIR-style paths to an isolated tmp_path.

    Modules that resolve STATE_DIR at import time are difficult to redirect
    cleanly; per-bug tests should prefer constructing the path directly via
    tmp_path rather than relying on module-level constants. This fixture is the
    blanket fallback for tests that exercise IO.
    """
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    # If any module reads STATE_DIR from an env var, set it.
    monkeypatch.setenv("SMAF_STATE_DIR", str(state))
    return state


@pytest.fixture
def synthetic_phase2_input():
    """Minimal phase-2 input shape matching what stage_phase2 writes."""
    return {
        "phase": "specialists_input",
        "run_time_utc": "2026-06-06T00:00:00Z",
        "team_charter": "",
        "universe": ["SOL", "JTO"],
        "per_symbol": {
            "SOL": {
                "ticker": "SOL",
                "bucket": "infrastructure",
                "indicators": {"vol_30d_daily_pct": 2.91},
                "latest_close_usd": 63.20,
                "dexscreener": {
                    "price_usd": 63.20,
                    "liq_usd": 20_000_000,
                    "primary_pool_liq_usd": 210_000,
                    "buys_h24": 81_697,
                    "sells_h24": 89_523,
                    "buy_skew_pct": 47.8,
                    "vol_h24": 195_000_000,
                    "vol_h1": 6_800_000,
                    "chg_h1": -1.59,
                    "chg_h6": -2.35,
                    "chg_h24": -4.18,
                },
                "holder_distribution": {"status": "rpc_failed"},
            },
            "JTO": {
                "ticker": "JTO",
                "bucket": "infrastructure",
                "indicators": {"insufficient_data": True},
                "latest_close_usd": 0.51,
                "dexscreener": {
                    "price_usd": 0.51,
                    "liq_usd": 1_044_000,
                    "primary_pool_liq_usd": 1_044_000,
                    "buys_h24": 1_858,
                    "sells_h24": 2_196,
                    "buy_skew_pct": 45.8,
                    "vol_h24": 820_000,
                    "chg_h24": -1.27,
                },
                "holder_distribution": {"status": "rpc_failed"},
            },
        },
        "performance_state": "FUND_PERFORMANCE stub",
        "lessons_summary": "",
        "goal_status": "",
        "regime_status": "SOL trend: strong_bear (price $63.20 vs SMA200 $102.41)\n  SOL 30d vol: 2.91% daily → normal\n",
        "risk_calibration": "",
        "recent_reflections": "",
        "network_health": {},
        "regime_notes": "",
        "open_positions_review": [],
        "stop_triggers_this_tick": [],
        "sentiment_anchors": {},
        "sentiment_anomalies": {},
        "sentiment_anchor_block": "",
    }


@pytest.fixture
def synthetic_specialist_scores():
    """A consensus-positive specialist score block for one ticker."""
    return {
        "ma_optimist_score": 0.42,
        "ma_pessimist_score": 0.05,
        "se_optimist_score": 0.20,
        "se_pessimist_score": -0.10,
        "consensus": 0.1425,
        "market_disagreement": 0.37,
        "onchain_disagreement": 0.30,
        "combined_uncertainty": 0.37,
        "current_price_usd": 0.032,
        "liq_usd_main_pool": 215_000,
        "30d_daily_vol_pct": 2.91,
        "fee_slippage_estimates": {"$500": {"cost_pct": 0.005, "rt_pct": 0.01}},
    }


@pytest.fixture
def synthetic_rm_output():
    """A no-trade Risk Manager output."""
    return {
        "specialist": "risk_manager",
        "run_time_utc": "2026-06-06T00:00:00Z",
        "account_gate": {
            "drawdown_pct": -0.28,
            "halt_buys": False,
            "halt_reason": None,
            "deployed_pct_now": 0.0,
            "cash_floor_ok": True,
            "remaining_budget_for_new_positions_usd": 8_081.42,
        },
        "stop_trigger_verifications": [],
        "existing_positions": [],
        "new_entry_recommendations": [],
        "rejections": [],
        "summary": "no-trade",
    }


@pytest.fixture
def synthetic_pm_output():
    """A no-trade PM output."""
    return {
        "specialist": "portfolio_mgr",
        "run_time_utc": "2026-06-06T00:00:00Z",
        "trades_to_execute": [],
        "trades": [],
        "stop_updates": [],
        "summary": "no-trade",
    }
