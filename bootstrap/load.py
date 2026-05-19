"""The historical_graduations table: its DDL and an idempotent loader.

This table is created and owned by the Python ETL -- it is NOT part of any
storm-store sqlx migration. Column conventions follow the repo's
0002_survival.sql: large u64 on-chain values as TEXT, booleans and the outcome
as INTEGER 0/1, INTEGER Unix-seconds timestamps. Features Dune may not supply
(liq reserves, pool_supply_fraction, creator_bag_fraction, the holder group)
are nullable. `mint` is the PRIMARY KEY -- the idempotency / resumability key.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, List, Set

from bootstrap.transform import GraduationRecord

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS historical_graduations (
    -- graduation facts ----------------------------------------------------
    mint                      TEXT PRIMARY KEY,        -- idempotency key
    pool_address              TEXT NOT NULL,
    bonding_curve_address     TEXT NOT NULL,
    lp_mint                   TEXT NOT NULL,
    migrator_wallet           TEXT NOT NULL,
    graduation_time           INTEGER NOT NULL,        -- Unix seconds (T0)
    graduation_slot           INTEGER NOT NULL,
    -- outcome label -------------------------------------------------------
    survived INTEGER NOT NULL,                         -- 0 rugged, 1 survived
    outcome_base_reserve      TEXT NOT NULL,           -- u64
    outcome_quote_reserve TEXT NOT NULL,               -- u64
    outcome_checked_at        INTEGER NOT NULL,        -- Unix seconds
    -- liquidity at ~T0+12h ------------------------------------------------
    liq_base_reserve          TEXT,                    -- u64; null if abandoned
    liq_quote_reserve         TEXT,                    -- u64; null if abandoned
    lp_burned                 INTEGER NOT NULL,        -- 0 | 1 (heuristic)
    pool_supply_fraction      REAL,                    -- null: Dune cannot supply
    -- bonding-curve final state ------------------------------------------
    curve_real_sol_reserves TEXT NOT NULL,             -- u64
    curve_real_token_reserves TEXT NOT NULL,           -- u64
    curve_token_total_supply  TEXT NOT NULL,           -- u64
    -- contract flags ------------------------------------------------------
    mint_authority_present    INTEGER NOT NULL,        -- 0 | 1
    freeze_authority_present  INTEGER NOT NULL,        -- 0 | 1 (cohort constant)
    -- holder distribution (best-effort; nullable on a Dune timeout) -------
    visible_holder_count      INTEGER,
    top10_concentration       REAL,
    top20_concentration       REAL,
    creator_bag_fraction      REAL,                    -- null: Dune cannot supply
    -- deployer signal (FIRST-CLASS; populated, not null) -----------------
    deployer_wallet           TEXT NOT NULL,
    deployer_prior_launches   INTEGER NOT NULL,
    deployer_age_secs         INTEGER NOT NULL,
    loaded_at                 INTEGER NOT NULL DEFAULT (unixepoch())
)
""".strip()

# The INSERT column order; `loaded_at` is left to its DEFAULT.
_COLUMNS = [
    "mint",
    "pool_address",
    "bonding_curve_address",
    "lp_mint",
    "migrator_wallet",
    "graduation_time",
    "graduation_slot",
    "survived",
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "outcome_checked_at",
    "liq_base_reserve",
    "liq_quote_reserve",
    "lp_burned",
    "pool_supply_fraction",
    "curve_real_sol_reserves",
    "curve_real_token_reserves",
    "curve_token_total_supply",
    "mint_authority_present",
    "freeze_authority_present",
    "visible_holder_count",
    "top10_concentration",
    "top20_concentration",
    "creator_bag_fraction",
    "deployer_wallet",
    "deployer_prior_launches",
    "deployer_age_secs",
]

_INSERT_SQL = (
    "INSERT INTO historical_graduations ("
    + ", ".join(_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" for _ in _COLUMNS)
    + ") ON CONFLICT(mint) DO NOTHING"
)


def create_table(conn: sqlite3.Connection) -> None:
    """Create historical_graduations if it does not already exist."""
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()


def _record_row(record: GraduationRecord) -> tuple:
    """A GraduationRecord as a tuple in _COLUMNS order."""
    return tuple(getattr(record, column) for column in _COLUMNS)


def load_records(
    conn: sqlite3.Connection, records: Iterable[GraduationRecord]
) -> int:
    """Idempotently insert records keyed on mint.

    Returns the number of rows actually inserted (mints already present are
    skipped via ON CONFLICT DO NOTHING).
    """
    before = conn.execute(
        "SELECT COUNT(*) FROM historical_graduations"
    ).fetchone()[0]
    rows = [_record_row(record) for record in records]
    conn.executemany(_INSERT_SQL, rows)
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) FROM historical_graduations"
    ).fetchone()[0]
    return after - before


def existing_mints(conn: sqlite3.Connection) -> Set[str]:
    """The set of mints already in historical_graduations."""
    cur = conn.execute("SELECT mint FROM historical_graduations")
    return {row[0] for row in cur.fetchall()}
