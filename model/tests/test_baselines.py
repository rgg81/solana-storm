"""Unit tests for model.baselines -- the three baseline basket selectors."""

import numpy as np
import pandas as pd

from model.baselines import buy_everything, heuristic_basket, random_basket


def basket_frame():
    """Six tokens with varied lp_burned / deployer / liquidity values."""
    return pd.DataFrame(
        {
            "lp_burned": [1, 1, 1, 0, 1, 1],
            "deployer_prior_launches": [0, 2, 50, 0, 1, np.nan],
            "liq_quote_reserve": [
                9.0e10, 6.0e10, 9.0e10, 9.0e10, 1.0e8, 9.0e10,
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
    # a different seed gives (very likely) a different basket.
    c = random_basket(df, size=3, seed=999)
    assert isinstance(c, set)


def test_random_basket_size_larger_than_frame_returns_all():
    df = basket_frame()
    picked = random_basket(df, size=99, seed=1)
    assert picked == set(df.index)


def test_heuristic_basket_applies_all_three_rules():
    df = basket_frame()
    # rules: lp_burned == 1 AND prior_launches <= 5 AND liq_quote >= 1e10.
    held = heuristic_basket(df, max_prior_launches=5, min_liq_quote=1.0e10)
    # A: lp=1, launches=0, liq ok            -> held
    # B: lp=1, launches=2, liq ok            -> held
    # C: lp=1, launches=50 (> 5)             -> excluded
    # D: lp=0                                -> excluded
    # E: liq 1e8 (< 1e10)                    -> excluded
    # F: deployer_prior_launches NaN         -> excluded (rule not satisfied)
    assert held == {"A", "B"}


def test_heuristic_basket_can_be_empty():
    df = basket_frame()
    # an unreachable liquidity floor -> no token qualifies.
    assert heuristic_basket(df, max_prior_launches=5,
                            min_liq_quote=1.0e30) == set()
