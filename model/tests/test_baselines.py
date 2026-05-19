"""Unit tests for model.baselines -- the three baseline basket selectors."""

import numpy as np
import pandas as pd

from model.baselines import buy_everything, heuristic_basket, random_basket


def basket_frame():
    """Six tokens spanning every combination of the 3 new heuristic rules."""
    return pd.DataFrame(
        {
            # rule 1: liq_quote_reserve >= 10 SOL = 1e10 lamports
            "liq_quote_reserve": [
                2.0e10, 6.0e10, 5.0e10, 5.0e10, 1.0e8, 5.0e10,
            ],
            # rule 2: deployer_prior_launches in [1, 30]
            "deployer_prior_launches": [3, 50, 0, 5, 5, np.nan],
            # rule 3: curve_real_sol_reserves >= 70 SOL = 7e10 lamports
            "curve_real_sol_reserves": [
                8.5e10, 8.5e10, 8.5e10, 3.0e10, 8.5e10, 8.5e10,
            ],
        },
        index=pd.Index(["A", "B", "C", "D", "E", "F"], name="mint"),
    )


def test_buy_everything_holds_every_mint():
    df = basket_frame()
    assert buy_everything(df) == {"A", "B", "C", "D", "E", "F"}


def test_random_basket_has_the_requested_size():
    df = basket_frame()
    picked = random_basket(df, size=3, seed=20260519)
    assert len(picked) == 3
    assert picked.issubset(set(df.index))


def test_random_basket_is_deterministic_for_a_fixed_seed():
    df = basket_frame()
    a = random_basket(df, size=3, seed=20260519)
    b = random_basket(df, size=3, seed=20260519)
    assert a == b


def test_random_basket_size_larger_than_frame_returns_all():
    df = basket_frame()
    picked = random_basket(df, size=99, seed=1)
    assert picked == set(df.index)


def test_heuristic_basket_applies_all_three_new_rules():
    df = basket_frame()
    held = heuristic_basket(
        df,
        min_liq_quote=1.0e10,
        deployer_launches_min=1,
        deployer_launches_max=30,
        min_curve_sol=7.0e10,
    )
    # A: liq 20 SOL ok, deployer 3 in [1,30] ok, curve 85 SOL ok        -> held
    # B: deployer 50 > 30                                                -> excluded
    # C: deployer 0  < 1                                                 -> excluded
    # D: curve 30 SOL < 70                                               -> excluded
    # E: liq 0.1 SOL < 10                                                -> excluded
    # F: deployer NaN -> NaN comparison False                            -> excluded
    assert held == {"A"}


def test_heuristic_basket_can_be_empty():
    df = basket_frame()
    # an unreachable liquidity floor -> no token qualifies.
    assert heuristic_basket(
        df,
        min_liq_quote=1.0e30,
        deployer_launches_min=1,
        deployer_launches_max=30,
        min_curve_sol=7.0e10,
    ) == set()


def test_heuristic_basket_excludes_nan_curve_when_floor_is_required():
    """The heuristic is STRICTER than the garbage filter: NaN curve is OUT."""
    df = basket_frame().copy()
    df.loc["A", "curve_real_sol_reserves"] = np.nan
    held = heuristic_basket(
        df,
        min_liq_quote=1.0e10,
        deployer_launches_min=1,
        deployer_launches_max=30,
        min_curve_sol=7.0e10,
    )
    assert "A" not in held


def test_heuristic_basket_keeps_tokens_at_floor_and_lower_bound_boundaries():
    """Inclusive bounds: rule-1 floor, rule-2 lower bound, rule-3 floor all pass at-value."""
    df = pd.DataFrame(
        {
            "liq_quote_reserve": [1.0e10],           # rule 1 floor exactly
            "deployer_prior_launches": [1],          # rule 2 lower bound exactly
            "curve_real_sol_reserves": [7.0e10],     # rule 3 floor exactly
        },
        index=pd.Index(["BOUND"], name="mint"),
    )
    held = heuristic_basket(
        df,
        min_liq_quote=1.0e10,
        deployer_launches_min=1,
        deployer_launches_max=30,
        min_curve_sol=7.0e10,
    )
    assert held == {"BOUND"}


def test_heuristic_basket_keeps_token_at_deployer_upper_bound():
    """Rule 2 upper bound is inclusive: deployer_prior_launches == max passes."""
    df = pd.DataFrame(
        {
            "liq_quote_reserve": [5.0e10],
            "deployer_prior_launches": [30],         # rule 2 upper bound exactly
            "curve_real_sol_reserves": [8.5e10],
        },
        index=pd.Index(["UPPER"], name="mint"),
    )
    held = heuristic_basket(
        df,
        min_liq_quote=1.0e10,
        deployer_launches_min=1,
        deployer_launches_max=30,
        min_curve_sol=7.0e10,
    )
    assert held == {"UPPER"}
