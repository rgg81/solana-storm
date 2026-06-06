"""Probe-log writer regression test.

risk_manager.md (lines 95+109) instructs the system to write every probe
trade to predictions/fund/state/probe_log.jsonl so the 4-tick cooldown gate
can read it. Historically there was no writer — the gate had nothing to read.

This test exercises execute_pm_orders against a real (isolated) account state
in tmp_path and asserts the probe row is appended.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Redirect every STATE_DIR-style constant to tmp_path BEFORE runner imports
    its dependencies. Returns (state_dir, fund_runner)."""
    # Pre-populate the canonical files so account.load() and friends find a
    # well-formed but empty state.
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "account.json").write_text(json.dumps({
        "cash_usd": 10_000.0, "deposit_usd": 10_000.0, "holdings": {},
        "halted": False, "peak_equity_usd": 10_000.0,
        "total_fees_paid_usd": 0.0, "total_slippage_usd": 0.0,
        "trade_count": 0, "win_count": 0, "loss_count": 0,
        "total_realized_pnl_usd": 0.0,
    }))
    (state / "trades.jsonl").write_text("")
    (state / "equity.jsonl").write_text("")
    (state / "stop_triggers.jsonl").write_text("")
    (state / "universe_price_history.jsonl").write_text("")
    (state / "bugs.jsonl").write_text("")
    # tick_risk_input.json is read by execute_pm_orders for fee/slippage
    # estimation — provide enough liquidity for the $125 probe to clear the
    # 1.5% slippage cap.
    (state / "tick_risk_input.json").write_text(json.dumps({
        "specialist_consensus_per_symbol": {
            "PYTH": {"liq_usd_main_pool": 5_000_000,
                     "ma_optimist_score": 0.45, "ma_pessimist_score": 0.05,
                     "se_optimist_score": 0.20, "se_pessimist_score": -0.05,
                     "market_disagreement": 0.40, "onchain_disagreement": 0.25},
        }
    }))

    # Patch every module's _STATE_DIR / STATE_DIR pointer.
    from predictions.fund import account, runner as fund_runner, bugs, audit, universe_price_history
    monkeypatch.setattr(fund_runner, "STATE_DIR", state)
    monkeypatch.setattr(account, "_STATE_DIR", state)
    monkeypatch.setattr(account, "ACCOUNT_PATH", state / "account.json")
    monkeypatch.setattr(account, "TRADES_PATH", state / "trades.jsonl")
    monkeypatch.setattr(account, "EQUITY_PATH", state / "equity.jsonl")
    monkeypatch.setattr(account, "TRIGGERS_LOG_PATH", state / "stop_triggers.jsonl")
    monkeypatch.setattr(bugs, "_STATE_DIR", state)
    monkeypatch.setattr(bugs, "BUGS_PATH", state / "bugs.jsonl")
    monkeypatch.setattr(audit, "_STATE_DIR", state)
    monkeypatch.setattr(audit, "AUDIT_LOG_PATH", state / "closed_trades_audit.jsonl")
    monkeypatch.setattr(universe_price_history, "_STATE_DIR", state)
    monkeypatch.setattr(universe_price_history, "HISTORY_PATH", state / "universe_price_history.jsonl")

    return state, fund_runner


def _probe_pm_output(ticker: str = "PYTH") -> dict:
    return {
        "trades": [
            {
                "ticker": ticker,
                "side": "buy",
                "usd_amount": 125.0,
                "price_usd": 0.032,
                "stop_loss_price_usd": 0.0294,
                "take_profit_price_usd": 0.0368,
                "reason": "Pass 2.5 probe — out-of-sample test of strong_bear floor",
            }
        ],
        "regime_probe": {
            "ticker": ticker,
            "consensus_at_entry": 0.22,
            "stop_loss_usd": 0.0294,
            "tp_usd": 0.0368,
            "max_size_usd": 125,
            "rationale": "Pass 2.5 probe per 2026-06-01 audit",
        },
    }


def test_probe_log_appended_when_probe_executes(isolated_state):
    state, fund_runner = isolated_state
    pm = _probe_pm_output("PYTH")
    out = fund_runner.execute_pm_orders(pm, prices={"PYTH": 0.032})

    # First confirm the BUY actually executed (otherwise the probe-log path
    # is correctly skipped and the test wouldn't exercise the writer).
    executed_buys = [r for r in out["results"]
                     if r["trade"]["side"] == "buy" and r["result"].get("executed")]
    assert executed_buys, f"setup error — buy did not execute: {out['results']}"

    probe_log = state / "probe_log.jsonl"
    assert probe_log.exists(), "probe_log.jsonl was not created on probe execution"
    rows = [json.loads(l) for l in probe_log.read_text().splitlines() if l.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "PYTH"
    assert row["consensus_at_entry"] == 0.22
    assert row["stop_loss_usd"] == 0.0294
    assert row["tp_usd"] == 0.0368
    assert row["max_size_usd"] == 125
    assert "Pass 2.5 probe" in row["rationale"]
    assert "ts" in row
    assert "tick_id" in row


def test_no_probe_log_when_no_probe(isolated_state):
    state, fund_runner = isolated_state
    pm = {"trades": []}
    fund_runner.execute_pm_orders(pm, prices={})
    probe_log = state / "probe_log.jsonl"
    assert not probe_log.exists(), "probe_log.jsonl must NOT be created on a no-probe tick"
