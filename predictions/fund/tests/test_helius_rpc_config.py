"""Helius RPC URL resolution — env var OR gitignored state file.

Context (tick-117, 2026-06-09): SOLANA_RPC_URL was unset for the entire run, so
Helius holder-distribution data was never available and both Solana Expert
specialists ran permanently capped at +0.0 (blind mode). The user opted to
configure a free Helius RPC URL. To make that persist across sessions without
relying on shell-env injection timing, _get_rpc_url() resolves the URL from
either the SOLANA_RPC_URL env var (precedence) or a gitignored
state/helius_rpc_url.txt file, read lazily so a freshly-dropped URL is picked up
on the next tick with no code change.
"""
from __future__ import annotations

import importlib

import pytest

from predictions.fund.helpers import onchain_stats


def test_env_var_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLANA_RPC_URL", "https://env.example/?api-key=ENV")
    monkeypatch.setattr(onchain_stats, "_RPC_URL_FILE", tmp_path / "helius_rpc_url.txt")
    (tmp_path / "helius_rpc_url.txt").write_text("https://file.example/?api-key=FILE\n")
    assert onchain_stats._get_rpc_url() == "https://env.example/?api-key=ENV"


def test_file_fallback_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.setattr(onchain_stats, "_RPC_URL_FILE", tmp_path / "helius_rpc_url.txt")
    (tmp_path / "helius_rpc_url.txt").write_text("  https://file.example/?api-key=FILE  \n")
    assert onchain_stats._get_rpc_url() == "https://file.example/?api-key=FILE"


def test_empty_when_neither_set(tmp_path, monkeypatch):
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.setattr(onchain_stats, "_RPC_URL_FILE", tmp_path / "absent.txt")
    assert onchain_stats._get_rpc_url() == ""


def test_blank_env_falls_through_to_file(tmp_path, monkeypatch):
    """An empty/whitespace env var must not shadow a configured file."""
    monkeypatch.setenv("SOLANA_RPC_URL", "   ")
    monkeypatch.setattr(onchain_stats, "_RPC_URL_FILE", tmp_path / "helius_rpc_url.txt")
    (tmp_path / "helius_rpc_url.txt").write_text("https://file.example/?api-key=FILE")
    assert onchain_stats._get_rpc_url() == "https://file.example/?api-key=FILE"


def test_rpc_logs_config_bug_when_unresolved(tmp_path, monkeypatch):
    """When no URL resolves, _rpc still returns None and logs the throttled
    config watchdog (regression guard on the existing behavior)."""
    monkeypatch.delenv("SOLANA_RPC_URL", raising=False)
    monkeypatch.setattr(onchain_stats, "_RPC_URL_FILE", tmp_path / "absent.txt")
    monkeypatch.setattr(onchain_stats, "_last_rpc_url_log_ts", 0)
    assert onchain_stats._rpc("getTokenSupply", ["mint"]) is None
