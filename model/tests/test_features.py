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
    """A tiny raw-shaped frame: one survivor, one rug, one NaN-curve row."""
    return pd.DataFrame(
        {
            "graduation_time": [1000, 2000, 3000],
            "graduation_slot": [400, 500, 600],
            "survived": [1, 0, 1],
            "outcome_base_reserve": [1.2e14, 0.0, 5.0e13],
            "outcome_quote_reserve": [9.2e10, 0.0, 6.0e10],
            "outcome_checked_at": [4000, 5000, 6000],
            "liq_base_reserve": [1.07e15, 8.5e14, 9.0e14],
            "liq_quote_reserve": [6.4e10, 2.0e10, np.nan],
            "lp_burned": [1, 1, 0],
            "curve_real_sol_reserves": [8.5e10, 8.5e10, np.nan],
            "curve_real_token_reserves": [0.0, 0.0, np.nan],
            "curve_token_total_supply": [2.79e14, 2.80e14, np.nan],
            "deployer_prior_launches": [12, 0, 3],
            "deployer_age_secs": [691200, 0, 3600],
        },
        index=pd.Index(["M1", "M2", "M3"], name="mint"),
    )


def test_build_features_returns_X_and_y_aligned_by_mint():
    X, y = build_features(raw_frame())
    assert list(X.index) == ["M1", "M2", "M3"]
    assert list(y.index) == ["M1", "M2", "M3"]
    assert list(y) == [1, 0, 1]
    assert y.name == LABEL_COLUMN


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


def test_raw_point_in_time_columns_are_present_in_X():
    X, _ = build_features(raw_frame())
    for col in ("liq_base_reserve", "liq_quote_reserve", "lp_burned",
                "curve_real_sol_reserves", "deployer_prior_launches",
                "deployer_age_secs"):
        assert col in X.columns


def test_engineered_features_are_computed():
    X, _ = build_features(raw_frame())
    # log1p of a skewed count
    assert "log_deployer_prior_launches" in X.columns
    assert X.loc["M1", "log_deployer_prior_launches"] == np.log1p(12)
    # entry-liquidity to curve-SOL ratio
    assert "liq_to_curve_sol_ratio" in X.columns
    expected = 6.4e10 / 8.5e10
    assert abs(X.loc["M1", "liq_to_curve_sol_ratio"] - expected) < 1e-9


def test_missing_values_flow_through_as_nan_not_imputed():
    X, _ = build_features(raw_frame())
    # M3 had NaN liq_quote and NaN curve columns -> still NaN in X.
    assert np.isnan(X.loc["M3", "liq_quote_reserve"])
    assert np.isnan(X.loc["M3", "curve_real_sol_reserves"])
    # an engineered ratio built from a NaN input is itself NaN, not 0.
    assert np.isnan(X.loc["M3", "liq_to_curve_sol_ratio"])


def test_deployer_launch_rate_handles_zero_age_without_dividing_by_zero():
    X, _ = build_features(raw_frame())
    # M2 has deployer_age_secs == 0; the rate must be finite (NaN), not inf.
    rate = X.loc["M2", "deployer_launch_rate_per_day"]
    assert not np.isinf(rate)
