"""Helius RPC misconfig watchdog regression test.

History: if SOLANA_RPC_URL was unset, onchain_stats._rpc returned None
silently — every on-chain call across every symbol across every tick failed
without writing a single bug entry. Section 7 of the tick reports printed
"No MEDIUM+ issues" while the on-chain pipeline was completely dead.

The fix logs a MEDIUM bug when RPC_URL is empty (throttled to 1×/hour so we
don't write 11 entries per tick).
"""
from __future__ import annotations

import time
from unittest.mock import patch

from predictions.fund.helpers import onchain_stats
from predictions.fund import bugs


def test_rpc_logs_bug_when_url_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(onchain_stats, "RPC_URL", "")
    monkeypatch.setattr(onchain_stats, "_last_rpc_url_log_ts", 0)
    monkeypatch.setattr(bugs, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(bugs, "BUGS_PATH", tmp_path / "bugs.jsonl")
    with patch.object(bugs, "log", wraps=bugs.log) as mock_log:
        result = onchain_stats._rpc("getTokenLargestAccounts", ["abc"])
    assert result is None  # behavior preserved
    assert mock_log.called
    # First positional is severity
    args, _ = mock_log.call_args
    assert args[0] == "MEDIUM"
    assert "helius" in args[1].lower() or "rpc" in args[1].lower()


def test_rpc_throttled_repeat_calls_do_not_re_log(tmp_path, monkeypatch):
    """Within 1h, repeat calls should not write a new bug each time."""
    monkeypatch.setattr(onchain_stats, "RPC_URL", "")
    monkeypatch.setattr(onchain_stats, "_last_rpc_url_log_ts", int(time.time()))
    monkeypatch.setattr(bugs, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(bugs, "BUGS_PATH", tmp_path / "bugs.jsonl")
    with patch.object(bugs, "log") as mock_log:
        for _ in range(5):
            onchain_stats._rpc("getTokenLargestAccounts", ["abc"])
    assert not mock_log.called, "throttle gate must suppress repeat logs within the window"
