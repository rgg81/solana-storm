"""Unit tests for model.filter -- the point-in-time garbage filter."""

import numpy as np
import pandas as pd

from model.config import load_config
from model.filter import filter_garbage


def _df(**overrides):
    """A baseline-good 3-token frame, easily mutable per test."""
    base = {
        "liq_quote_reserve": [5.0e10, 5.0e10, 5.0e10],     # 50 SOL each
        "deployer_prior_launches": [3, 3, 3],
        "curve_real_sol_reserves": [8.5e10, 8.5e10, 8.5e10],
        # other columns the filter does not read -- present for plausibility
        "graduation_time": [1000, 2000, 3000],
        "survived": [1, 1, 1],
    }
    base.update(overrides)
    return pd.DataFrame(base, index=pd.Index(["A", "B", "C"], name="mint"))


def test_filter_keeps_a_fully_good_frame_unchanged():
    df = _df()
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert list(kept.index) == ["A", "B", "C"]


def test_filter_preserves_the_mint_index_and_column_set():
    df = _df()
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert kept.index.name == "mint"
    assert list(kept.columns) == list(df.columns)


def test_rule1_drops_below_entry_liquidity_floor():
    df = _df(liq_quote_reserve=[5.0e10, 5.0e8, 5.0e10])  # B has 0.5 SOL
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # rule 1: liq_quote >= 1 SOL -- B fails.
    assert set(kept.index) == {"A", "C"}


def test_rule1_drops_nan_entry_liquidity():
    df = _df(liq_quote_reserve=[5.0e10, np.nan, 5.0e10])
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # NaN entry liq -> rule 1 fails (NaN not >= floor).
    assert set(kept.index) == {"A", "C"}


def test_rule2_drops_over_deployer_spam_ceiling():
    df = _df(deployer_prior_launches=[3, 3, 1_000_000])  # C is a spam bot
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # rule 2: deployer_prior_launches <= 500 -- C fails.
    assert set(kept.index) == {"A", "B"}


def test_rule3_keeps_nan_curve_real_sol():
    """NaN curve_real_sol is KEPT (LightGBM handles NaN); only known-low fails."""
    df = _df(curve_real_sol_reserves=[8.5e10, np.nan, 8.5e10])
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # rule 3: NaN OR >= floor -- B's NaN keeps it.
    assert set(kept.index) == {"A", "B", "C"}


def test_rule3_drops_below_curve_real_sol_floor():
    df = _df(curve_real_sol_reserves=[8.5e10, 5.0e9, 8.5e10])  # B has 5 SOL
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # rule 3 floor is 10 SOL -- B fails.
    assert set(kept.index) == {"A", "C"}


def test_filter_is_a_conjunction_of_all_three_rules():
    df = pd.DataFrame(
        {
            "liq_quote_reserve": [5.0e10, 5.0e8, 5.0e10, 5.0e10],         # B fails r1
            "deployer_prior_launches": [3, 3, 1_000_000, 3],              # C fails r2
            "curve_real_sol_reserves": [8.5e10, 8.5e10, 8.5e10, 5.0e9],   # D fails r3
            "graduation_time": [1000, 2000, 3000, 4000],
            "survived": [1, 1, 1, 1],
        },
        index=pd.Index(["A", "B", "C", "D"], name="mint"),
    )
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    # only A passes every rule.
    assert set(kept.index) == {"A"}


def test_filter_handles_an_empty_frame():
    df = _df().iloc[:0]
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert len(kept) == 0
    assert list(kept.columns) == list(df.columns)


def test_filter_respects_overridden_thresholds():
    df = _df()  # all 3 rows pass the defaults
    # tighten rule 1 to 100 SOL -- now all fail.
    cfg = load_config(min_entry_liq_lamports=100_000_000_000)
    assert len(filter_garbage(df, cfg)) == 0


def test_rule1_keeps_value_at_the_entry_liq_floor():
    """Boundary: liq_quote_reserve exactly == min_entry_liq_lamports passes."""
    df = _df(liq_quote_reserve=[1_000_000_000, 5.0e10, 5.0e10])  # A at 1 SOL exactly
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert set(kept.index) == {"A", "B", "C"}


def test_rule2_keeps_value_at_the_deployer_ceiling():
    """Boundary: deployer_prior_launches exactly == max_deployer_prior_launches passes."""
    df = _df(deployer_prior_launches=[3, 3, 500])  # C at the ceiling exactly
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert set(kept.index) == {"A", "B", "C"}


def test_rule3_keeps_value_at_the_curve_sol_floor():
    """Boundary: curve_real_sol_reserves exactly == min_curve_sol_lamports passes."""
    df = _df(curve_real_sol_reserves=[10_000_000_000, 8.5e10, 8.5e10])  # A at 10 SOL exactly
    cfg = load_config()
    kept = filter_garbage(df, cfg)
    assert set(kept.index) == {"A", "B", "C"}
