"""Phase 3 configuration: a frozen Config dataclass and load_config()."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Config:
    """Every tunable for the Phase 3 survival model and the backtest.

    All fields have spec-derived defaults; load_config() builds one and
    accepts keyword overrides for a single field.
    """

    # --- Storage (read-only; the Phase 2 ETL owns the table) ---
    db_path: str = "./storm.db"
    table_name: str = "historical_graduations"

    # --- Survival label rule (matches the Phase 2 ETL's threshold) ---
    survival_min_quote_lamports: int = 5_000_000_000  # 5 SOL

    # --- Backtest: slots & sizing (spec 7 / 12) ---
    slot_count: int = 20  # N equal bankroll slots, spec range 15-30
    entry_threshold: float = 0.5  # min calibrated positive-return score to enter
    initial_bankroll: float = 100.0  # paper SOL; the equity curve is relative

    # --- Garbage filter (spec 4.2) -- applied uniformly to model + baselines ---
    min_entry_liq_lamports: int = 1_000_000_000        # 1 SOL
    max_deployer_prior_launches: int = 500
    min_curve_sol_lamports: int = 10_000_000_000       # 10 SOL

    # --- Honest costs (spec 7 / 12) ---
    dex_fee_rate: float = 0.0025  # 0.25% PumpSwap AMM swap fee, per leg

    # --- Survival model calibration (spec 5 / 12) ---
    calibration_fraction: float = 0.20  # last 20% of a training fold, time-ordered

    # --- Determinism ---
    random_seed: int = 20260519

    # --- Report output ---
    report_dir: str = "model/report"


def load_config(**overrides: Any) -> Config:
    """Build a Config with the spec defaults, applying keyword overrides.

    Example: load_config(slot_count=30) returns a Config identical to the
    default except slot_count is 30.

    Raises:
        TypeError: if an override names a field Config does not have.
    """
    base = Config()
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    unknown = set(overrides) - valid
    if unknown:
        raise TypeError(f"unknown Config field(s): {sorted(unknown)}")
    return replace(base, **overrides)
