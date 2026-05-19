"""Unit tests for model.costs -- the DEX-fee + constant-product fill model."""

import math

import pytest

from model.costs import exit_fill, entry_fill


def test_entry_fill_constant_product_no_fee_matches_hand_computation():
    # Pool: 1000 token (base), 1000 SOL (quote). Buy with 100 SOL, 0% fee.
    # x*y=k: tokens_out = base - k/(quote + sol_in)
    #      = 1000 - (1000*1000)/(1000+100) = 1000 - 909.0909... = 90.9090...
    tokens_out = entry_fill(
        sol_in=100.0, base_reserve=1000.0, quote_reserve=1000.0, fee_rate=0.0
    )
    assert math.isclose(tokens_out, 1000.0 - 1_000_000.0 / 1100.0, rel_tol=1e-12)


def test_entry_fill_fee_is_taken_off_the_input():
    # 0.25% fee: only 99.75 SOL of the 100 reaches the curve.
    no_fee = entry_fill(100.0, 1000.0, 1000.0, fee_rate=0.0)
    with_fee = entry_fill(100.0, 1000.0, 1000.0, fee_rate=0.0025)
    # the fee reduces the tokens received.
    assert with_fee < no_fee
    effective = entry_fill(99.75, 1000.0, 1000.0, fee_rate=0.0)
    assert math.isclose(with_fee, effective, rel_tol=1e-12)


def test_exit_fill_constant_product_no_fee_matches_hand_computation():
    # Pool: 1000 token, 1000 SOL. Sell 100 token back, 0% fee.
    # sol_out = quote - k/(base + tokens_in)
    #         = 1000 - (1000*1000)/(1000+100) = 90.9090...
    sol_out = exit_fill(
        tokens_in=100.0, base_reserve=1000.0, quote_reserve=1000.0,
        fee_rate=0.0,
    )
    assert math.isclose(sol_out, 1000.0 - 1_000_000.0 / 1100.0, rel_tol=1e-12)


def test_exit_into_a_near_empty_pool_craters_the_fill():
    """The exit-liquidity problem: a thin pool returns almost nothing."""
    # Selling 100 token into a pool with only 0.5 SOL of depth.
    sol_out = exit_fill(100.0, base_reserve=1000.0, quote_reserve=0.5,
                        fee_rate=0.0025)
    # cannot get back more than the pool's entire SOL depth.
    assert 0.0 < sol_out < 0.5


def test_exit_fill_can_never_exceed_pool_quote_depth():
    # Even an enormous sell only ever drains up to the quote reserve.
    sol_out = exit_fill(1e18, base_reserve=1000.0, quote_reserve=42.0,
                        fee_rate=0.0)
    assert sol_out < 42.0


def test_zero_size_order_returns_zero():
    assert entry_fill(0.0, 1000.0, 1000.0, 0.0025) == 0.0
    assert exit_fill(0.0, 1000.0, 1000.0, 0.0025) == 0.0


def test_empty_pool_returns_zero_fill():
    # An abandoned token: zero reserves. Buying or selling yields 0.
    assert entry_fill(10.0, base_reserve=0.0, quote_reserve=0.0,
                      fee_rate=0.0025) == 0.0
    assert exit_fill(10.0, base_reserve=0.0, quote_reserve=0.0,
                     fee_rate=0.0025) == 0.0


def test_fee_rate_out_of_range_raises():
    with pytest.raises(ValueError):
        entry_fill(10.0, 1000.0, 1000.0, fee_rate=1.5)
    with pytest.raises(ValueError):
        exit_fill(10.0, 1000.0, 1000.0, fee_rate=-0.1)
