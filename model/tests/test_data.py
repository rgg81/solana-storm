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
