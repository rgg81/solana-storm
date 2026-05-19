"""Unit tests for model.config."""

import dataclasses

import pytest

from model.config import Config, load_config


def test_load_config_returns_the_spec_defaults():
    cfg = load_config()
    # storage
    assert cfg.db_path == "./storm.db"
    assert cfg.table_name == "historical_graduations"
    # the (still-loaded) Phase 2 survival rule -- 5 SOL quote reserve
    assert cfg.survival_min_quote_lamports == 5_000_000_000
    # backtest slots & sizing
    assert cfg.slot_count == 20
    # re-tuned for the post-filter ~10% positive-class base rate (spec 8)
    assert cfg.entry_threshold == 0.5
    # honest costs (0.25% PumpSwap AMM fee per leg)
    assert cfg.dex_fee_rate == 0.0025
    # calibration slice -- last 20% of each training fold
    assert cfg.calibration_fraction == 0.20
    # determinism
    assert cfg.random_seed == 20260519
    # report output
    assert cfg.report_dir == "model/report"
    # NEW: garbage-filter thresholds (spec 4.2)
    assert cfg.min_entry_liq_lamports == 1_000_000_000        # 1 SOL
    assert cfg.max_deployer_prior_launches == 500
    assert cfg.min_curve_sol_lamports == 10_000_000_000       # 10 SOL


def test_load_config_applies_keyword_overrides():
    cfg = load_config(slot_count=30, entry_threshold=0.7)
    assert cfg.slot_count == 30
    assert cfg.entry_threshold == 0.7
    # untouched fields keep their defaults
    assert cfg.dex_fee_rate == 0.0025


def test_config_is_frozen():
    cfg = load_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.slot_count = 1  # frozen dataclass forbids attribute assignment


def test_initial_bankroll_is_positive():
    cfg = load_config()
    assert cfg.initial_bankroll > 0
