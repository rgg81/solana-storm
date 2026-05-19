"""ETL configuration: a frozen Config dataclass and load_config()."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Every tunable for the Dune historical-graduation ETL.

    All fields have spec/findings-derived defaults; load_config() builds one.
    """

    # --- Dune API ---
    dune_api_key: str
    dune_base_url: str = "https://api.dune.com"

    # --- Sample (design spec 4.3) ---
    sample_size: int = 5000
    window_start: str = "2025-11-01"  # PumpSwap-era start, ISO date
    outcome_settle_days: int = 16  # exclude tokens younger than this

    # --- Batch sizes (spike findings 3.x / 5) ---
    event_batch_size: int = 500  # outcome / liquidity / bonding-curve queries
    flag_batch_size: int = 1000  # contract-flag and deployer queries
    holder_batch_size: int = 50  # holder distribution: small, timeout-prone

    # --- Outcome / snapshot timing (hours / days) ---
    liquidity_snapshot_hours: int = 12  # ~T0+12h liquidity snapshot
    outcome_window_lo_days: int = 12  # outcome event window low bound
    outcome_window_hi_days: int = 16  # outcome event window high bound

    # --- Storage ---
    db_path: str = "./storm.db"  # project SQLite file (DATABASE_URL local path)
    cache_dir: str = "bootstrap/data"  # gitignored stage-result cache

    # --- Run mode ---
    is_pilot: bool = False

    # --- Outcome rule (same threshold as the live collector) ---
    survival_min_quote_lamports: int = 5_000_000_000  # 5 SOL


def load_config(pilot: bool = False) -> Config:
    """Build a Config. Reads DUNE_API_KEY from the environment.

    Args:
        pilot: when True, shrink sample_size to a pilot-run size and mark
            is_pilot -- the rest of the pipeline behaves identically.

    Raises:
        ValueError: if DUNE_API_KEY is not set in the environment.
    """
    key = os.environ.get("DUNE_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "DUNE_API_KEY is not set. Add it to the repo .env "
            "(see .env.example)."
        )
    if pilot:
        return Config(dune_api_key=key, sample_size=75, is_pilot=True)
    return Config(dune_api_key=key)
