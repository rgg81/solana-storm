"""Load the historical_graduations table from ./storm.db into a DataFrame.

The Phase 2 ETL owns this table (see bootstrap/load.py). Phase 3 only reads
it. Large u64 on-chain values are stored as TEXT strings in SQLite (SQLite's
max integer is i64); they are parsed here to nullable numeric columns -- a
SQL NULL becomes NaN and is never fabricated into a number.
"""

from __future__ import annotations

import sqlite3
from typing import List

import pandas as pd

from model.config import Config

# Exactly the columns Phase 3 reads -- the names match bootstrap/load.py's
# _COLUMNS. The dropped columns (mint_authority_present,
# freeze_authority_present, pool_supply_fraction, creator_bag_fraction, the
# holder group) are intentionally NOT selected: the spec drops them.
RAW_COLUMNS: List[str] = [
    # identity / facts
    "mint",
    "pool_address",
    "bonding_curve_address",
    "lp_mint",
    "migrator_wallet",
    "graduation_time",
    "graduation_slot",
    # outcome label
    "survived",
    # outcome reserves (backtest-only -- NEVER model features)
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "outcome_checked_at",
    # liquidity at ~T0+12h (point-in-time features)
    "liq_base_reserve",
    "liq_quote_reserve",
    "lp_burned",
    # bonding-curve final state (point-in-time features)
    "curve_real_sol_reserves",
    "curve_real_token_reserves",
    "curve_token_total_supply",
    # deployer signal (point-in-time features)
    "deployer_wallet",
    "deployer_prior_launches",
    "deployer_age_secs",
]

# The TEXT u64-string columns -- parsed to numeric (float; NaN-able).
_U64_STRING_COLUMNS: List[str] = [
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "liq_base_reserve",
    "liq_quote_reserve",
    "curve_real_sol_reserves",
    "curve_real_token_reserves",
    "curve_token_total_supply",
]


def load_dataframe(conn: sqlite3.Connection) -> pd.DataFrame:
    """Read historical_graduations from an open connection into a DataFrame.

    The frame is indexed by `mint` and sorted ascending by `graduation_time`
    so every downstream consumer (notably the walk-forward harness) gets a
    chronologically ordered frame. TEXT u64-string reserve columns are
    parsed to numeric; a SQL NULL stays NaN.
    """
    select = ", ".join(RAW_COLUMNS)
    df = pd.read_sql_query(
        f"SELECT {select} FROM historical_graduations", conn
    )
    for col in _U64_STRING_COLUMNS:
        # errors='coerce' turns a NULL/None into NaN; never fabricates a value.
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.set_index("mint")
    df = df.sort_values("graduation_time", kind="stable")
    return df


def load_graduations(config: Config) -> pd.DataFrame:
    """Open config.db_path and load historical_graduations into a DataFrame."""
    conn = sqlite3.connect(config.db_path)
    try:
        return load_dataframe(conn)
    finally:
        conn.close()
