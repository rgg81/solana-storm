"""Unit tests for model.features -- pure feature engineering."""

import numpy as np
import pandas as pd

from model.features import (
    FEATURE_COLUMNS,
    LABEL_COLUMN,
    LEAKAGE_FORBIDDEN,
    build_features,
)


def raw_frame():
    """Three post-filter tokens spanning the label outcomes.

    M1: gain (exit/entry ratio = 20)        -> positive_return = 1
    M2: abandoned (outcome 0/0 reserves)    -> positive_return = 0
    M3: gain (exit/entry ratio = 4) with NaN curve (allowed post-filter)
    """
    return pd.DataFrame(
        {
            "graduation_time": [1000, 2000, 3000],
            "graduation_slot": [400, 500, 600],
            "survived": [1, 0, 1],  # still loaded; not the label any more
            "outcome_base_reserve": [1.0e14, 0.0, 5.0e14],
            "outcome_quote_reserve": [1.2e11, 0.0, 1.0e11],
            "outcome_checked_at": [4000, 5000, 6000],
            "liq_base_reserve": [1.0e15, 8.5e14, 1.0e15],
            "liq_quote_reserve": [6.0e10, 2.0e10, 5.0e10],
            "lp_burned": [1, 1, 0],
            "curve_real_sol_reserves": [8.5e10, 8.5e10, np.nan],
            "curve_real_token_reserves": [0.0, 0.0, np.nan],
            "curve_token_total_supply": [2.79e14, 2.80e14, np.nan],
            "deployer_prior_launches": [12, 0, 3],
            "deployer_age_secs": [691200, 0, 3600],
        },
        index=pd.Index(["M1", "M2", "M3"], name="mint"),
    )


def test_label_column_name_and_values():
    _X, y = build_features(raw_frame())
    assert LABEL_COLUMN == "positive_return"
    assert y.name == LABEL_COLUMN
    # M1 exit/entry ratio = (1.2e11/1.0e14) / (6.0e10/1.0e15) = 20  -> 1
    # M2 abandoned -> 0
    # M3 ratio = (1.0e11/5.0e14) / (5.0e10/1.0e15) = 4              -> 1
    assert list(y) == [1, 0, 1]


def test_build_features_returns_X_and_y_aligned_by_mint():
    X, y = build_features(raw_frame())
    assert list(X.index) == ["M1", "M2", "M3"]
    assert list(y.index) == ["M1", "M2", "M3"]


def test_X_columns_are_exactly_the_feature_list():
    X, _ = build_features(raw_frame())
    assert list(X.columns) == FEATURE_COLUMNS


def test_no_outcome_or_label_column_leaks_into_X():
    """Spec 4: outcome reserves and the label are NEVER model features."""
    X, _ = build_features(raw_frame())
    for forbidden in LEAKAGE_FORBIDDEN:
        assert forbidden not in X.columns, (
            f"{forbidden} leaked into the feature matrix"
        )


def test_new_engineered_features_are_present_with_correct_values():
    X, _ = build_features(raw_frame())
    # curve-SOL to entry-liquidity ratio (M1).
    assert "curve_sol_to_entry_liq_ratio" in X.columns
    expected_ratio = 8.5e10 / 6.0e10
    assert abs(
        X.loc["M1", "curve_sol_to_entry_liq_ratio"] - expected_ratio
    ) < 1e-9
    # entry log-price (M1).
    assert "entry_log_price" in X.columns
    expected_log_price = np.log(6.0e10 / 1.0e15)
    assert abs(
        X.loc["M1", "entry_log_price"] - expected_log_price
    ) < 1e-9


def test_curve_sol_to_entry_liq_ratio_is_nan_when_curve_is_nan():
    X, _ = build_features(raw_frame())
    # M3 has NaN curve_real_sol_reserves -- the ratio is NaN, never imputed.
    assert np.isnan(X.loc["M3", "curve_sol_to_entry_liq_ratio"])


def test_existing_engineered_features_carry_over():
    X, _ = build_features(raw_frame())
    # log1p of a skewed count -- unchanged from Phase 3.
    assert X.loc["M1", "log_deployer_prior_launches"] == np.log1p(12)
    # entry-liquidity to curve-SOL ratio -- unchanged from Phase 3.
    expected = 6.0e10 / 8.5e10
    assert abs(X.loc["M1", "liq_to_curve_sol_ratio"] - expected) < 1e-9


def test_raw_point_in_time_columns_are_present_in_X():
    X, _ = build_features(raw_frame())
    for col in ("liq_base_reserve", "liq_quote_reserve", "lp_burned",
                "curve_real_sol_reserves", "deployer_prior_launches",
                "deployer_age_secs"):
        assert col in X.columns


def test_deployer_launch_rate_handles_zero_age_without_dividing_by_zero():
    X, _ = build_features(raw_frame())
    # M2 has deployer_age_secs == 0; the rate must be NaN -- never inf, and
    # never a fabricated finite value.
    rate = X.loc["M2", "deployer_launch_rate_per_day"]
    assert np.isnan(rate)


def test_abandoned_token_has_positive_return_zero():
    """outcome 0/0 -> exit_price NaN -> NaN comparison False -> label 0."""
    _X, y = build_features(raw_frame())
    assert y.loc["M2"] == 0
