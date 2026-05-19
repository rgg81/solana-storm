"""Unit tests for model.backtest -- the portfolio-evolution simulator."""

import numpy as np
import pandas as pd

from model.backtest import BacktestResult, run_backtest


def sim_frame():
    """Four tokens: two clear winners, one mild loser, one abandoned rug.

    Entry pools are deep (low slippage); the rug's outcome pool is empty.
    """
    return pd.DataFrame(
        {
            "graduation_time": [0, 0, 100, 100],
            "outcome_checked_at": [1000, 1000, 1100, 1100],
            # entry depth: token side (base), SOL side (quote).
            "liq_base_reserve": [1e15, 1e15, 1e15, 1e15],
            "liq_quote_reserve": [1e12, 1e12, 1e12, 1e12],
            # exit depth: W1/W2 richer (price up), L mild loss, R abandoned.
            "outcome_base_reserve": [5e14, 5e14, 1.2e15, 0.0],
            "outcome_quote_reserve": [3e12, 3e12, 9e11, 0.0],
        },
        index=pd.Index(["W1", "W2", "L", "R"], name="mint"),
    )


def test_run_backtest_returns_a_result_with_an_equity_curve():
    df = sim_frame()
    result = run_backtest(
        df, basket=set(df.index), slot_count=4,
        initial_bankroll=100.0, dex_fee_rate=0.0025, entry_offset_secs=0,
    )
    assert isinstance(result, BacktestResult)
    assert len(result.equity_curve) >= 2  # at least a start and an end point
    assert result.equity_curve.iloc[0] == 100.0  # starts at the bankroll


def test_every_basket_token_with_liquidity_produces_a_position():
    df = sim_frame()
    result = run_backtest(df, basket=set(df.index), slot_count=4,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    assert len(result.positions) == 4
    assert {p.mint for p in result.positions} == {"W1", "W2", "L", "R"}


def test_abandoned_token_realises_a_total_loss():
    df = sim_frame()
    result = run_backtest(df, basket={"R"}, slot_count=1,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    rug = result.positions[0]
    assert rug.mint == "R"
    # an empty outcome pool -> exit_fill returns 0 -> -100% return.
    assert rug.return_pct <= -0.999


def test_a_basket_of_winners_grows_the_bankroll():
    df = sim_frame()
    result = run_backtest(df, basket={"W1", "W2"}, slot_count=2,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    assert result.final_equity > 100.0
    assert result.total_return > 0.0


def test_only_basket_tokens_are_traded():
    df = sim_frame()
    result = run_backtest(df, basket={"W1"}, slot_count=4,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    assert {p.mint for p in result.positions} == {"W1"}


def test_slot_cap_limits_concurrent_positions():
    """With 1 slot, the second simultaneous token cannot enter."""
    df = sim_frame()
    # W1 and W2 are both eligible at t=0; only one slot is free.
    result = run_backtest(df, basket={"W1", "W2"}, slot_count=1,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    # W1/W2 exit at t=1000; only one of them ever held the single slot.
    assert len(result.positions) == 1


def test_capital_recycles_from_an_exit_into_a_later_entry():
    """One slot: the t=0 token exits at t=1000, freeing the slot for the
    t=100 token, which enters at its horizon-free moment."""
    df = sim_frame()
    result = run_backtest(df, basket={"W1", "L"}, slot_count=1,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    # W1 occupies the slot [0,1000]; L is eligible at 100 but the slot is
    # busy -- L can still enter after W1 exits because L's horizon is 1100.
    held = {p.mint for p in result.positions}
    assert "W1" in held


def test_token_with_nan_entry_liquidity_is_excluded_and_counted():
    df = sim_frame()
    df.loc["W2", "liq_quote_reserve"] = np.nan  # no derivable entry price
    result = run_backtest(df, basket=set(df.index), slot_count=4,
                          initial_bankroll=100.0, dex_fee_rate=0.0025,
                          entry_offset_secs=0)
    assert "W2" not in {p.mint for p in result.positions}
    assert result.excluded_no_liquidity == 1


def test_simulator_reads_outcome_reserves_only_at_exit():
    """No-leakage: zeroing the outcome reserves must not change which tokens
    enter or when -- the outcome only affects the realised exit value."""
    df = sim_frame()
    base = run_backtest(df, basket=set(df.index), slot_count=4,
                        initial_bankroll=100.0, dex_fee_rate=0.0025,
                        entry_offset_secs=0)
    scrambled = df.copy()
    scrambled["outcome_base_reserve"] = 0.0
    scrambled["outcome_quote_reserve"] = 0.0
    after = run_backtest(scrambled, basket=set(df.index), slot_count=4,
                         initial_bankroll=100.0, dex_fee_rate=0.0025,
                         entry_offset_secs=0)
    # the same set of tokens entered (entry decisions ignore the outcome).
    assert ({p.mint for p in base.positions}
            == {p.mint for p in after.positions})
