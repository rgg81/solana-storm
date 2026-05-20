"""Unit tests for model.data -- against a temp-file SQLite DB."""

import sqlite3

import numpy as np
import pandas as pd

from model.data import RAW_COLUMNS, load_dataframe

# A trimmed CREATE TABLE that matches the columns model/data.py reads. The
# real table (bootstrap/load.py) has the same column names; the test only
# needs the columns the loader selects.
_CREATE = """
CREATE TABLE historical_graduations (
    mint TEXT PRIMARY KEY,
    pool_address TEXT, bonding_curve_address TEXT, lp_mint TEXT,
    migrator_wallet TEXT,
    graduation_time INTEGER, graduation_slot INTEGER,
    survived INTEGER,
    outcome_base_reserve TEXT, outcome_quote_reserve TEXT,
    outcome_checked_at INTEGER,
    liq_base_reserve TEXT, liq_quote_reserve TEXT, lp_burned INTEGER,
    curve_real_sol_reserves TEXT, curve_real_token_reserves TEXT,
    curve_token_total_supply TEXT,
    deployer_wallet TEXT, deployer_prior_launches INTEGER,
    deployer_age_secs INTEGER
)
"""


def _seed(conn):
    conn.execute(_CREATE)
    # row 1: fully populated; row 2 (out of order in time) populated;
    # row 3: NULL liquidity + NULL curve columns (the abandoned-token shape).
    rows = [
        ("M1", "P1", "BC1", "LP1", "MIG1", 2000, 500, 1,
         "120000000000000", "92000000000", 3000,
         "1073000000000000", "64000000000", 1,
         "85005359507", "0", "279900000000000",
         "DEP1", 12, 691200),
        ("M2", "P2", "BC2", "LP2", "MIG2", 1000, 400, 0,
         "0", "0", 1000,
         "850938146206890", "20732018898", 1,
         "85000000000", "0", "280000000000000",
         "DEP2", 0, 0),
        ("M3", "P3", "BC3", "LP3", "MIG3", 3000, 600, 0,
         "0", "0", 3000,
         None, None, 1,
         None, None, None,
         "DEP3", 3, 3600),
    ]
    conn.executemany(
        "INSERT INTO historical_graduations VALUES ("
        + ", ".join("?" for _ in range(20)) + ")",
        rows,
    )
    conn.commit()


_CREATE_SNAPSHOTS = """
CREATE TABLE intraperiod_snapshots (
    mint TEXT NOT NULL,
    snapshot_index INTEGER NOT NULL,
    snapshot_time INTEGER NOT NULL,
    snapshot_slot INTEGER NOT NULL,
    base_reserve TEXT,
    quote_reserve TEXT,
    PRIMARY KEY (mint, snapshot_index)
)
"""


def _seed_snapshots(conn):
    """Seed 2 snapshots on M1 (days 1 and 7), nothing for M2 / M3."""
    conn.execute(_CREATE_SNAPSHOTS)
    rows = [
        # M1: a healthy day-1, a degraded day-7.
        ("M1", 1, 1086400, 510, "1070000000000000", "63000000000"),
        ("M1", 7, 1604800, 590, "1500000000000000", "30000000000"),
    ]
    conn.executemany(
        "INSERT INTO intraperiod_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def test_load_dataframe_returns_every_raw_column():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    # `mint` becomes the frame index; every other RAW_COLUMNS name is a column.
    assert df.index.name == "mint"
    for col in RAW_COLUMNS:
        if col == "mint":
            continue
        assert col in df.columns, f"{col} missing from the loaded frame"


def test_frame_is_indexed_by_mint_and_sorted_by_time():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    assert df.index.name == "mint"
    # seeded times are 2000, 1000, 3000 -> sorted ascending the index is M2,M1,M3
    assert list(df.index) == ["M2", "M1", "M3"]
    assert list(df["graduation_time"]) == [1000, 2000, 3000]


def test_u64_string_reserves_are_parsed_to_numbers():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    # the reserve columns become numeric (float), not object strings.
    for col in ("liq_base_reserve", "liq_quote_reserve",
                "outcome_base_reserve", "outcome_quote_reserve",
                "curve_real_sol_reserves", "curve_real_token_reserves",
                "curve_token_total_supply"):
        assert pd.api.types.is_numeric_dtype(df[col]), f"{col} not numeric"
    # the large u64 value survives the round-trip exactly.
    assert df.loc["M2", "liq_base_reserve"] == 850938146206890.0


def test_null_reserves_become_nan_not_fabricated():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    # M3 had NULL liquidity and NULL curve columns -> NaN, never zero/imputed.
    assert np.isnan(df.loc["M3", "liq_base_reserve"])
    assert np.isnan(df.loc["M3", "curve_real_sol_reserves"])


def test_label_and_counts_keep_integer_semantics():
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    df = load_dataframe(conn)
    assert int(df.loc["M1", "survived"]) == 1
    assert int(df.loc["M2", "survived"]) == 0
    assert int(df.loc["M1", "deployer_prior_launches"]) == 12


def test_load_dataframe_returns_nan_snapshots_when_table_missing():
    """Back-compat: if intraperiod_snapshots doesn't exist, the 28 columns
    still appear on the loaded frame as all-NaN, so callers can rely on them.
    """
    conn = sqlite3.connect(":memory:")
    _seed(conn)  # historical_graduations only -- NO snapshots table
    df = load_dataframe(conn)
    for i in range(1, 15):
        for kind in ("base", "quote"):
            col = f"snap_{i}_{kind}_reserve"
            assert col in df.columns, f"{col} missing"
            assert df[col].isna().all(), f"{col} should be all NaN"


def test_load_dataframe_joins_snapshot_rows_when_present():
    """When intraperiod_snapshots exists, the 28 columns are populated where
    data is present and NaN elsewhere. Row M1 has snap 1 and 7 only."""
    conn = sqlite3.connect(":memory:")
    _seed(conn)
    _seed_snapshots(conn)
    df = load_dataframe(conn)
    # M1's snap 1 + 7 are populated.
    assert df.loc["M1", "snap_1_base_reserve"] == 1070000000000000.0
    assert df.loc["M1", "snap_1_quote_reserve"] == 63000000000.0
    assert df.loc["M1", "snap_7_base_reserve"] == 1500000000000000.0
    assert df.loc["M1", "snap_7_quote_reserve"] == 30000000000.0
    # M1's other snapshots are NaN.
    assert np.isnan(df.loc["M1", "snap_2_base_reserve"])
    assert np.isnan(df.loc["M1", "snap_14_quote_reserve"])
    # M2's and M3's snapshots are all NaN (no rows seeded).
    for i in range(1, 15):
        assert np.isnan(df.loc["M2", f"snap_{i}_quote_reserve"])
        assert np.isnan(df.loc["M3", f"snap_{i}_base_reserve"])
