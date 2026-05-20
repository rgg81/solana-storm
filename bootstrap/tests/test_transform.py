"""Unit tests for bootstrap.transform -- pure Dune-row -> record functions."""

from bootstrap import transform
from bootstrap.transform import GraduationRecord


def grad_rows():
    """Two synthetic graduations-list rows, Dune-shaped."""
    return [
        {
            "mint": "MINT_A",
            "pool_address": "POOL_A",
            "bonding_curve_address": "BC_A",
            "lp_mint": "LP_A",
            "migrator_wallet": "MIG_A",
            "graduation_time": "2026-01-10 08:00:00.000 UTC",
            "graduation_slot": 312000000,
        },
        {
            "mint": "MINT_B",
            "pool_address": "POOL_B",
            "bonding_curve_address": "BC_B",
            "lp_mint": "LP_B",
            "migrator_wallet": "MIG_B",
            "graduation_time": "2026-02-01 12:30:00.000 UTC",
            "graduation_slot": 318000000,
        },
    ]


def test_parse_graduations_builds_seed_records():
    recs = transform.parse_graduations(grad_rows())
    assert set(recs.keys()) == {"MINT_A", "MINT_B"}
    a = recs["MINT_A"]
    assert isinstance(a, GraduationRecord)
    assert a.pool_address == "POOL_A"
    assert a.bonding_curve_address == "BC_A"
    assert a.lp_mint == "LP_A"
    assert a.migrator_wallet == "MIG_A"
    assert a.graduation_slot == 312000000
    # graduation_time parsed to Unix seconds (int).
    assert isinstance(a.graduation_time, int) and a.graduation_time > 0
    # all feature fields start as None / unset.
    assert a.survived is None
    assert a.liq_base_reserve is None
    assert a.visible_holder_count is None
    assert a.deployer_prior_launches is None
    # freeze authority is the cohort constant 0.
    assert a.freeze_authority_present == 0
    # lp_burned defaults to the heuristic 1.
    assert a.lp_burned == 1
    # pool_supply_fraction / creator_bag_fraction are never supplied -> None.
    assert a.pool_supply_fraction is None
    assert a.creator_bag_fraction is None


def test_parse_time_handles_dune_timestamp_formats():
    secs = transform.parse_dune_time("2026-01-10 08:00:00.000 UTC")
    assert secs == 1768032000
    # the bare-ISO variant some Dune columns return.
    assert transform.parse_dune_time("2026-01-10T08:00:00Z") == 1768032000


def test_merge_outcome_sets_survived_with_the_5_sol_rule():
    recs = transform.parse_graduations(grad_rows())
    # POOL_A has > 5 SOL quote; POOL_B is drained.
    out_rows = [
        {
            "pool_address": "POOL_A",
            "outcome_base_reserve": "120000000000000",
            "outcome_quote_reserve": "92000000000",
            "outcome_event_time": "2026-01-25 08:00:00.000 UTC",
        },
        {
            "pool_address": "POOL_B",
            "outcome_base_reserve": "999",
            "outcome_quote_reserve": "10000000",
            "outcome_event_time": "2026-02-16 12:30:00.000 UTC",
        },
    ]
    transform.merge_outcome(recs, out_rows,
                            survival_min_quote_lamports=5_000_000_000)
    assert recs["MINT_A"].survived == 1
    assert recs["MINT_A"].outcome_quote_reserve == "92000000000"
    assert recs["MINT_B"].survived == 0


def test_merge_outcome_missing_pool_is_rugged_with_zero_reserves():
    """Findings 3.1: no event row -> abandoned -> survived = 0, reserves 0."""
    recs = transform.parse_graduations(grad_rows())
    transform.merge_outcome(recs, [], survival_min_quote_lamports=5_000_000_000)
    for mint in ("MINT_A", "MINT_B"):
        assert recs[mint].survived == 0
        assert recs[mint].outcome_quote_reserve == "0"
        assert recs[mint].outcome_base_reserve == "0"


def test_merge_liquidity_sets_reserves_and_keeps_string_u64():
    recs = transform.parse_graduations(grad_rows())
    liq_rows = [
        {
            "pool_address": "POOL_A",
            "liq_base_reserve": "1073000000000000",
            "liq_quote_reserve": "64000000000",
            "liq_event_time": "2026-01-10 20:00:00.000 UTC",
        }
    ]
    transform.merge_liquidity(recs, liq_rows, withdrawn_pools=set())
    a = recs["MINT_A"]
    assert a.liq_base_reserve == "1073000000000000"  # kept as str
    assert a.liq_quote_reserve == "64000000000"
    assert a.lp_burned == 1  # not in withdrawn_pools -> stays burned


def test_merge_liquidity_clears_lp_burned_for_a_withdrawn_pool():
    recs = transform.parse_graduations(grad_rows())
    transform.merge_liquidity(recs, [], withdrawn_pools={"POOL_A"})
    assert recs["MINT_A"].lp_burned == 0
    assert recs["MINT_B"].lp_burned == 1


def test_merge_bonding_curve_picks_last_trade_before_migration_slot():
    recs = transform.parse_graduations(grad_rows())
    # MINT_A migrated at slot 312000000. Three trades; one is AT the
    # migration slot and must be ignored (findings caveat 4).
    bc_rows = [
        {
            "mint": "MINT_A",
            "real_sol_reserves": "70000000000",
            "real_token_reserves": "5000000",
            "virtual_token_reserves": "100000000",
            "evt_block_slot": 311999990,
        },
        {
            "mint": "MINT_A",
            "real_sol_reserves": "85005359500",
            "real_token_reserves": "0",
            "virtual_token_reserves": "0",
            "evt_block_slot": 311999999,
        },
        {
            "mint": "MINT_A",
            "real_sol_reserves": "999",
            "real_token_reserves": "999",
            "virtual_token_reserves": "999",
            "evt_block_slot": 312000000,  # the migration slot itself -> skip
        },
    ]
    transform.merge_bonding_curve(recs, bc_rows)
    a = recs["MINT_A"]
    assert a.curve_real_sol_reserves == "85005359500"  # the slot-99 trade
    assert a.curve_real_token_reserves == "0"
    # total supply = virtual + real token reserves at that final trade.
    assert a.curve_token_total_supply == "0"


def test_merge_bonding_curve_skips_trade_rows_with_null_reserves():
    """Dune leaves ~1% of pump_evt_tradeevent rows undecoded (NULL reserves).
    Such rows are skipped, not crashed on; the latest fully populated trade is
    used, and a mint with only undecoded rows keeps its NULL curve fields.
    """
    recs = transform.parse_graduations(grad_rows())
    bc_rows = [
        # MINT_A: a populated trade, then a later undecoded one.
        {
            "mint": "MINT_A",
            "real_sol_reserves": "85005359500",
            "real_token_reserves": "0",
            "virtual_token_reserves": "0",
            "evt_block_slot": 311999990,
        },
        {
            "mint": "MINT_A",
            "real_sol_reserves": None,
            "real_token_reserves": None,
            "virtual_token_reserves": None,
            "evt_block_slot": 311999999,  # latest, but undecoded -> skipped
        },
        # MINT_B: every trade row is undecoded.
        {
            "mint": "MINT_B",
            "real_sol_reserves": None,
            "real_token_reserves": None,
            "virtual_token_reserves": None,
            "evt_block_slot": 317999990,
        },
    ]
    transform.merge_bonding_curve(recs, bc_rows)
    # MINT_A: the undecoded latest row is skipped -> the populated slot-90 trade.
    a = recs["MINT_A"]
    assert a.curve_real_sol_reserves == "85005359500"
    assert a.curve_real_token_reserves == "0"
    assert a.curve_token_total_supply == "0"
    # MINT_B: no usable trade row -> curve fields stay NULL.
    b = recs["MINT_B"]
    assert b.curve_real_sol_reserves is None
    assert b.curve_token_total_supply is None


def test_merge_contract_flags_sets_mint_authority_present():
    recs = transform.parse_graduations(grad_rows())
    flag_rows = [
        {"mint": "MINT_A", "mint_authority_present": 0},
        {"mint": "MINT_B", "mint_authority_present": 1},
    ]
    transform.merge_contract_flags(recs, flag_rows)
    assert recs["MINT_A"].mint_authority_present == 0
    assert recs["MINT_B"].mint_authority_present == 1
    # freeze authority stays the cohort constant 0.
    assert recs["MINT_A"].freeze_authority_present == 0


def test_merge_deployer_populates_the_first_class_signal():
    recs = transform.parse_graduations(grad_rows())
    dep_rows = [
        {
            "mint": "MINT_A",
            "deployer_wallet": "DEP_A",
            "deployer_prior_launches": 443,
            "deployer_age_secs": 691200,
        }
    ]
    transform.merge_deployer(recs, dep_rows)
    a = recs["MINT_A"]
    assert a.deployer_wallet == "DEP_A"
    assert a.deployer_prior_launches == 443
    assert a.deployer_age_secs == 691200
    # MINT_B had no create row (findings caveat 8) -> deployer fields None.
    assert recs["MINT_B"].deployer_wallet is None


def test_merge_holders_populates_when_present_and_skips_when_absent():
    recs = transform.parse_graduations(grad_rows())
    holder_rows = [
        {
            "mint": "MINT_A",
            "visible_holder_count": 137,
            "top10_concentration": 0.42,
            "top20_concentration": 0.61,
        }
    ]
    transform.merge_holders(recs, holder_rows)
    assert recs["MINT_A"].visible_holder_count == 137
    assert recs["MINT_A"].top10_concentration == 0.42
    # MINT_B not in the holder rows -> stays None (the NULL fallback).
    assert recs["MINT_B"].visible_holder_count is None


def test_parse_snapshots_builds_typed_records():
    from bootstrap.transform import parse_snapshots
    rows = [
        {
            "pool_address": "POOL_A",
            "base_reserve": "1070000000000000",
            "quote_reserve": "63000000000",
            "event_time": "2026-01-02 03:00:00",
            "event_slot": 510,
        },
        {
            "pool_address": "POOL_B",
            "base_reserve": "85093814600000",
            "quote_reserve": "20732018000",
            "event_time": "2026-01-02 04:00:00",
            "event_slot": 511,
        },
    ]
    records = parse_snapshots(rows, snapshot_index=1)
    assert len(records) == 2
    # mint is None until the orchestrator remaps.
    for r in records:
        assert r.mint is None
    # The pool address is stored in pool_address.
    by_pool = {r.pool_address: r for r in records}
    a = by_pool["POOL_A"]
    assert a.snapshot_index == 1
    assert a.base_reserve == "1070000000000000"
    assert a.quote_reserve == "63000000000"
    assert a.snapshot_slot == 510
    # event_time parsed to Unix seconds.
    assert a.snapshot_time == 1767322800  # 2026-01-02 03:00:00 UTC


def test_parse_snapshots_returns_empty_for_no_rows():
    from bootstrap.transform import parse_snapshots
    assert parse_snapshots([], snapshot_index=5) == []
