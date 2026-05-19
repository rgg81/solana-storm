"""Unit tests for bootstrap.load -- against a real temp-file SQLite DB."""

import sqlite3

from bootstrap.load import (
    CREATE_TABLE_SQL,
    create_table,
    existing_mints,
    load_records,
)
from bootstrap.transform import GraduationRecord


def open_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    create_table(conn)
    return conn


def full_record(mint: str) -> GraduationRecord:
    """A record with every feature populated (no NULLs)."""
    return GraduationRecord(
        mint=mint,
        pool_address=f"pool-{mint}",
        bonding_curve_address=f"bc-{mint}",
        lp_mint=f"lp-{mint}",
        migrator_wallet="mig",
        graduation_time=1768032000,
        graduation_slot=312000000,
        survived=1,
        outcome_base_reserve="120000000000000",
        outcome_quote_reserve="92000000000",
        outcome_checked_at=1769241600,
        liq_base_reserve="1073000000000000",
        liq_quote_reserve="64000000000",
        lp_burned=1,
        pool_supply_fraction=None,
        curve_real_sol_reserves="85005359500",
        curve_real_token_reserves="0",
        curve_token_total_supply="1000000000000000",
        mint_authority_present=0,
        freeze_authority_present=0,
        visible_holder_count=137,
        top10_concentration=0.42,
        top20_concentration=0.61,
        creator_bag_fraction=None,
        deployer_wallet="DEP",
        deployer_prior_launches=443,
        deployer_age_secs=691200,
    )


def sparse_record(mint: str) -> GraduationRecord:
    """A record with every NULL-able feature left None (Dune timed out)."""
    return GraduationRecord(
        mint=mint,
        pool_address=f"pool-{mint}",
        bonding_curve_address=f"bc-{mint}",
        lp_mint=f"lp-{mint}",
        migrator_wallet="mig",
        graduation_time=1768032000,
        graduation_slot=312000000,
        survived=0,
        outcome_base_reserve="0",
        outcome_quote_reserve="0",
        outcome_checked_at=1768032000,
        curve_real_sol_reserves="85000000000",
        curve_real_token_reserves="0",
        curve_token_total_supply="1000000000000000",
        mint_authority_present=0,
        deployer_wallet="DEP2",
        deployer_prior_launches=1,
        deployer_age_secs=3600,
    )


def test_create_table_is_idempotent(tmp_path):
    conn = open_db(tmp_path)
    create_table(conn)  # second call must not raise
    cur = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='historical_graduations'"
    )
    assert cur.fetchone() is not None


def test_load_a_full_record_round_trips(tmp_path):
    conn = open_db(tmp_path)
    inserted = load_records(conn, [full_record("MINT_A")])
    assert inserted == 1
    row = conn.execute(
        "SELECT mint, survived, curve_real_sol_reserves, "
        "deployer_prior_launches, outcome_quote_reserve, visible_holder_count "
        "FROM historical_graduations WHERE mint = 'MINT_A'"
    ).fetchone()
    assert row[0] == "MINT_A"
    assert row[1] == 1  # survived
    assert row[2] == "85005359500"  # u64 stored as TEXT
    assert row[3] == 443  # deployer_prior_launches
    assert row[4] == "92000000000"
    assert row[5] == 137


def test_sparse_record_stores_nulls_for_missing_features(tmp_path):
    conn = open_db(tmp_path)
    load_records(conn, [sparse_record("MINT_S")])
    row = conn.execute(
        "SELECT visible_holder_count, top10_concentration, "
        "top20_concentration, pool_supply_fraction, creator_bag_fraction "
        "FROM historical_graduations WHERE mint = 'MINT_S'"
    ).fetchone()
    assert row == (None, None, None, None, None)
    # but the first-class deployer signal is NOT null.
    dep = conn.execute(
        "SELECT deployer_wallet, deployer_prior_launches "
        "FROM historical_graduations WHERE mint = 'MINT_S'"
    ).fetchone()
    assert dep == ("DEP2", 1)


def test_load_is_idempotent_on_mint(tmp_path):
    conn = open_db(tmp_path)
    first = load_records(conn, [full_record("MINT_A")])
    assert first == 1
    # re-loading the same mint inserts nothing and does not raise.
    again = load_records(conn, [full_record("MINT_A")])
    assert again == 0
    count = conn.execute(
        "SELECT COUNT(*) FROM historical_graduations"
    ).fetchone()[0]
    assert count == 1


def test_load_a_batch_with_some_already_present(tmp_path):
    conn = open_db(tmp_path)
    load_records(conn, [full_record("M1")])
    inserted = load_records(
        conn, [full_record("M1"), full_record("M2"), full_record("M3")]
    )
    assert inserted == 2  # M1 skipped, M2 + M3 new
    assert conn.execute(
        "SELECT COUNT(*) FROM historical_graduations"
    ).fetchone()[0] == 3


def test_existing_mints_returns_the_loaded_set(tmp_path):
    conn = open_db(tmp_path)
    assert existing_mints(conn) == set()
    load_records(conn, [full_record("M1"), full_record("M2")])
    assert existing_mints(conn) == {"M1", "M2"}


def test_create_table_sql_has_the_spec_columns():
    low = CREATE_TABLE_SQL.lower()
    # mint is the PRIMARY KEY idempotency key.
    assert "mint" in low and "primary key" in low
    # the first-class deployer columns are present.
    assert "deployer_wallet" in low
    assert "deployer_prior_launches" in low
    assert "deployer_age_secs" in low
    # u64 reserves are TEXT.
    assert "curve_real_sol_reserves text" in low
    assert "outcome_quote_reserve text" in low
    # the outcome is an INTEGER.
    assert "survived integer" in low
    # the NULL-able feature columns are declared without NOT NULL.
    for nullable in (
        "pool_supply_fraction",
        "creator_bag_fraction",
        "visible_holder_count",
        "top10_concentration",
        "top20_concentration",
    ):
        assert nullable in low
