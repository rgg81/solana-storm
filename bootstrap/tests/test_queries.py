"""Unit tests for bootstrap.queries -- pure SQL string builders."""

import pytest

from bootstrap import queries


def test_graduations_sql_filters_window_and_settle_cutoff():
    sql = queries.graduations_sql(
        window_start="2025-11-01", settle_cutoff="2026-05-03"
    )
    low = sql.lower()
    assert "pumpdotfun_solana.pump_call_migrate" in low
    assert "account_mint" in low
    assert "account_pool" in low
    assert "account_bonding_curve" in low
    assert "account_lp_mint" in low
    assert "account_user" in low  # the migrator wallet
    assert "call_block_time" in low
    assert "call_block_slot" in low
    # both date bounds embedded.
    assert "2025-11-01" in sql
    assert "2026-05-03" in sql


def test_sql_values_pairs_renders_cast_and_timestamp():
    result = queries._sql_values_pairs([("pool1", "2026-01-10 08:00:00")])
    assert "CAST('pool1' AS VARCHAR)" in result
    assert "TIMESTAMP '2026-01-10 08:00:00'" in result


def test_sql_values_pairs_rejects_a_quote_in_pool():
    with pytest.raises(ValueError):
        queries._sql_values_pairs([("po'ol", "2026-01-10 08:00:00")])


def test_sql_values_pairs_rejects_a_quote_in_time():
    with pytest.raises(ValueError):
        queries._sql_values_pairs([("pool", "2026-01-10 08:00:00' --")])


def test_outcome_sql_embeds_pairs_and_the_event_tables():
    sql = queries.outcome_sql(
        [("POOL_A", "2026-01-10 08:00:00"), ("POOL_B", "2026-02-01 00:00:00")]
    )
    low = sql.lower()
    assert "pump_amm_evt_buyevent" in low
    assert "pump_amm_evt_sellevent" in low
    assert "pool_quote_token_reserves" in low
    assert "pool_base_token_reserves" in low
    assert "union all" in low
    # windowed intervals (per-token T0+12d..T0+16d)
    assert "interval '12' day" in low
    assert "interval '16' day" in low
    # both pool addresses and timestamps embedded
    assert "POOL_A" in sql and "POOL_B" in sql
    assert "2026-01-10 08:00:00" in sql and "2026-02-01 00:00:00" in sql


def test_liquidity_sql_targets_pairs_and_the_event_tables():
    sql = queries.liquidity_sql(
        [("P1", "2026-01-10 08:00:00")]
    )
    low = sql.lower()
    assert "pump_amm_evt_buyevent" in low
    assert "pump_amm_evt_sellevent" in low
    # windowed interval (T0 to T0+12h)
    assert "interval '12' hour" in low
    assert "P1" in sql
    assert "2026-01-10 08:00:00" in sql


def test_bonding_curve_sql_windows_per_mint_before_migration():
    sql = queries.bonding_curve_sql([("MINT1", 312000000), ("MINT2", 318000000)])
    low = sql.lower()
    assert "pumpdotfun_solana.pump_evt_tradeevent" in low
    assert "real_sol_reserves" in low
    assert "real_token_reserves" in low
    assert "virtual_token_reserves" in low
    assert "evt_block_slot" in low
    assert "row_number()" in low  # one row per mint, not every trade
    assert "< t.grad_slot" in low  # strictly before the migration slot
    assert "is not null" in low  # undecoded (NULL-reserve) trade rows excluded
    assert "'MINT1'" in sql and "'MINT2'" in sql
    assert "312000000" in sql and "318000000" in sql


def test_contract_flags_sql_joins_initializemint2_and_setauthority():
    sql = queries.contract_flags_sql(["MINTX"])
    low = sql.lower()
    assert "spl_token_call_initializemint2" in low
    assert "spl_token_call_setauthority" in low
    assert "minttokens" in low  # the authority type checked
    assert "mint_authority_present" in low
    assert "'MINTX'" in sql


def test_deployer_sql_uses_createpoolevent_and_coin_creator():
    sql = queries.deployer_sql(["MINTD"])
    low = sql.lower()
    assert "createpoolevent" in low
    assert "coin_creator" in low
    assert "base_mint" in low
    assert "'MINTD'" in sql


def test_holders_sql_targets_spl_token_transfers_at_a_snapshot():
    sql = queries.holders_sql(["MINTH"], snapshot_time="2026-01-01 12:00:00")
    low = sql.lower()
    assert "tokens_solana.spl_token_transfers" in low
    assert "from_owner" in low
    assert "to_owner" in low
    assert "token_mint_address" in low
    assert "row_number()" in low  # the top-N ranking
    assert "'MINTH'" in sql
    assert "2026-01-01 12:00:00" in sql


def test_in_list_quotes_and_comma_joins():
    assert queries._sql_in_list(["a", "b", "c"]) == "'a', 'b', 'c'"


def test_in_list_rejects_a_value_with_a_quote():
    # defence against a malformed address breaking the SQL string.
    with pytest.raises(ValueError):
        queries._sql_in_list(["ok", "ev'il"])
