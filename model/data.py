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

    When the intraperiod_snapshots table is present, also LEFT-JOIN 14 daily
    snapshots per mint into 28 paired columns `snap_{i}_base_reserve` and
    `snap_{i}_quote_reserve` for i in 1..14. Missing snapshots load as NaN.
    When the table is missing, the 28 columns appear as all-NaN so callers
    can rely on a stable column set (spec 4.3).
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

    # LEFT-JOIN intraperiod snapshots if the table exists.
    snap_df = _load_intraperiod_snapshots(conn, mints=list(df.index))
    df = df.join(snap_df, how="left")

    return df


def _load_intraperiod_snapshots(
    conn: sqlite3.Connection, mints: list[str]
) -> pd.DataFrame:
    """Return a DataFrame indexed by mint with 28 snap_*_*_reserve columns.

    If the intraperiod_snapshots table is missing OR has no rows for the
    given mints, every column is NaN. Otherwise rows are pivoted: each
    (mint, snapshot_index) becomes snap_{i}_base_reserve / snap_{i}_quote_reserve.
    """
    columns = [
        f"snap_{i}_{kind}_reserve"
        for i in range(1, 15)
        for kind in ("base", "quote")
    ]
    empty = pd.DataFrame(
        index=pd.Index(mints, name="mint"), columns=columns, dtype=float
    )
    # Existence check.
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='intraperiod_snapshots'"
    )
    if cur.fetchone() is None:
        return empty

    raw = pd.read_sql_query(
        "SELECT mint, snapshot_index, base_reserve, quote_reserve "
        "FROM intraperiod_snapshots",
        conn,
    )
    if raw.empty:
        return empty

    # u64-string -> numeric.
    raw["base_reserve"] = pd.to_numeric(raw["base_reserve"], errors="coerce")
    raw["quote_reserve"] = pd.to_numeric(raw["quote_reserve"], errors="coerce")

    # Pivot to one row per mint with snap_{i}_{kind} columns.
    pivoted = raw.pivot(
        index="mint", columns="snapshot_index", values=["base_reserve", "quote_reserve"]
    )
    pivoted.columns = [
        f"snap_{int(idx)}_{kind.split('_')[0]}_reserve"
        for kind, idx in pivoted.columns
    ]
    # Align to the requested mints, fill missing.
    aligned = empty.copy()
    aligned.update(pivoted)
    return aligned


def load_graduations(config: Config) -> pd.DataFrame:
    """Open config.db_path and load historical_graduations into a DataFrame."""
    conn = sqlite3.connect(config.db_path)
    try:
        return load_dataframe(conn)
    finally:
        conn.close()
