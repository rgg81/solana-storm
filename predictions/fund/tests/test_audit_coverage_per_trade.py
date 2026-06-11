"""Phase 7 audit-coverage must count closes PER TICKER, not as a set difference.

Bug (found 2026-06-11 while answering "are the agents learning?"): JTO was traded
twice — a stop-loss (-$31.36, audited) and later a probe take-profit (+$18.67, the
fund's single best probe win, "first clean realized winner under the corrected
probe gate"). Only the stop was written to closed_trades_audit.jsonl. The old check
did `sell_tickers - audit_tickers`; JTO was present in both sets, so the gap
collapsed to empty and the check passed — the fund's richest record (entry_consensus
scores the Reflector learns winning entries from) silently missed its best example.

Fix: count sells vs audit rows per ticker; flag when audit rows < sells.
"""
from __future__ import annotations

import json

from predictions.fund import auto_audit


def _write(tmp_path, monkeypatch, trades, audit):
    (tmp_path / "trades.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in trades))
    (tmp_path / "closed_trades_audit.jsonl").write_text(
        "".join(json.dumps(a) + "\n" for a in audit))
    monkeypatch.setattr(auto_audit, "STATE", tmp_path)


def test_two_sells_one_audit_same_ticker_fails(tmp_path, monkeypatch):
    """THE BUG: JTO sold twice, audited once → must FAIL (old set-diff passed)."""
    trades = [{"side": "buy", "ticker": "JTO"}, {"side": "sell", "ticker": "JTO"},
              {"side": "buy", "ticker": "JTO"}, {"side": "sell", "ticker": "JTO"}]
    audit = [{"ticker": "JTO", "exit_reason": "stop_loss_verified"}]
    _write(tmp_path, monkeypatch, trades, audit)
    r = auto_audit.check_audit_coverage()
    assert r["passed"] is False
    assert r["severity"] == "HIGH"
    assert any("JTO" in g for g in r["context"]["gaps"])


def test_two_sells_two_audits_same_ticker_passes(tmp_path, monkeypatch):
    """After the backfill: JTO has 2 sells AND 2 audit rows → passes."""
    trades = [{"side": "sell", "ticker": "JTO"}, {"side": "sell", "ticker": "JTO"}]
    audit = [{"ticker": "JTO", "exit_reason": "stop_loss_verified"},
             {"ticker": "JTO", "exit_reason": "take_profit_executed"}]
    _write(tmp_path, monkeypatch, trades, audit)
    assert auto_audit.check_audit_coverage()["passed"] is True


def test_multi_ticker_one_gap_fails(tmp_path, monkeypatch):
    """RENDER fully covered, JTO short one — only JTO flagged."""
    trades = [{"side": "sell", "ticker": "RENDER"},
              {"side": "sell", "ticker": "JTO"}, {"side": "sell", "ticker": "JTO"}]
    audit = [{"ticker": "RENDER"}, {"ticker": "JTO"}]
    _write(tmp_path, monkeypatch, trades, audit)
    r = auto_audit.check_audit_coverage()
    assert r["passed"] is False
    assert len(r["context"]["gaps"]) == 1 and "JTO" in r["context"]["gaps"][0]


def test_all_covered_passes(tmp_path, monkeypatch):
    trades = [{"side": "buy", "ticker": "RENDER"}, {"side": "sell", "ticker": "RENDER"}]
    audit = [{"ticker": "RENDER", "exit_reason": "take_profit_executed"}]
    _write(tmp_path, monkeypatch, trades, audit)
    assert auto_audit.check_audit_coverage()["passed"] is True


def test_no_trades_passes(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, [], [])
    assert auto_audit.check_audit_coverage()["passed"] is True
