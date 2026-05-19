"""Unit tests for bootstrap.config."""

import pytest

from bootstrap.config import Config, load_config


def test_load_config_reads_dune_key_from_env(monkeypatch):
    monkeypatch.setenv("DUNE_API_KEY", "test-key-123")
    cfg = load_config()
    assert cfg.dune_api_key == "test-key-123"
    assert cfg.dune_base_url == "https://api.dune.com"


def test_load_config_raises_when_key_missing(monkeypatch):
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DUNE_API_KEY"):
        load_config()


def test_defaults_match_the_spec(monkeypatch):
    monkeypatch.setenv("DUNE_API_KEY", "k")
    cfg = load_config()
    # spec 4.3: ~5,000 graduations, exclude < ~16 days old.
    assert cfg.sample_size == 5000
    assert cfg.outcome_settle_days == 16
    # findings 3.5/3.6: holder batches small, others larger.
    assert cfg.holder_batch_size == 50
    assert cfg.event_batch_size == 500
    assert cfg.liquidity_batch_size == 150
    assert cfg.flag_batch_size == 1000
    # the project DB file and the gitignored cache dir.
    assert cfg.db_path == "./storm.db"
    assert cfg.cache_dir == "bootstrap/data"
    # the PumpSwap-era window start (spec 4.3).
    assert cfg.window_start == "2025-11-01"
    assert cfg.is_pilot is False


def test_pilot_overrides_shrink_the_sample(monkeypatch):
    monkeypatch.setenv("DUNE_API_KEY", "k")
    cfg = load_config(pilot=True)
    assert cfg.sample_size == 75
    assert cfg.is_pilot is True
    # batch sizes still valid, just a tiny sample.
    assert cfg.holder_batch_size == 50


def test_config_is_frozen(monkeypatch):
    monkeypatch.setenv("DUNE_API_KEY", "k")
    cfg = load_config()
    with pytest.raises(Exception):
        cfg.sample_size = 1  # frozen dataclass -> FrozenInstanceError
