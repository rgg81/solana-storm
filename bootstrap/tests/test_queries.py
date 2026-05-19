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


def test_outcome_sql_embeds_pools_and_the_event_tables():
    sql = queries.outcome_sql(["POOL_A", "POOL_B"])
    low = sql.lower()
    assert "pump_amm_evt_buyevent" in low
    assert "pump_amm_evt_sellevent" in low
    assert "pool_quote_token_reserves" in low
    assert "pool_base_token_reserves" in low
    assert "union all" in low
    # both pools quoted into the IN list.
    assert "'POOL_A'" in sql and "'POOL_B'" in sql


def test_liquidity_sql_targets_pools_and_the_event_tables():
    sql = queries.liquidity_sql(["P1"])
    low = sql.lower()
    assert "pump_amm_evt_buyevent" in low
    assert "pump_amm_evt_sellevent" in low
    assert "'P1'" in sql


def test_bonding_curve_sql_uses_tradeevent_and_mints():
    sql = queries.bonding_curve_sql(["MINT1", "MINT2"])
    low = sql.lower()
    assert "pumpdotfun_solana.pump_evt_tradeevent" in low
    assert "real_sol_reserves" in low
    assert "real_token_reserves" in low
    assert "virtual_token_reserves" in low
    assert "evt_block_slot" in low
    assert "'MINT1'" in sql and "'MINT2'" in sql


def test_contract_flags_sql_joins_initializemint2_and_setauthority():
    sql = queries.contract_flags_sql(["MINTX"])
    low = sql.lower()
    assert "spl_token_call_initializemint2" in low
    assert "spl_token_call_setauthority" in low
    assert "minttokens" in low  # the authority type checked
    assert "mint_authority_present" in low
    assert "'MINTX'" in sql


def test_deployer_sql_self_joins_create_and_create_v2():
    sql = queries.deployer_sql(["MINTD"], max_grad_time="2026-05-03")
    low = sql.lower()
    assert "pump_call_create" in low
    # create_v2 also covered (findings caveat 8).
    assert "pump_call_create_v2" in low
    assert "account_user" in low
    assert "count(*)" in low
    assert "min(call_block_time)" in low
    assert "'MINTD'" in sql
    assert "2026-05-03" in sql


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
