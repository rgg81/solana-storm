"""Pure feature engineering: the raw DataFrame -> the model feature matrix.

Every column of the returned X is STRICTLY point-in-time -- known at or
before T0+12h. The outcome reserve columns and the survived label are future
data and are never placed in X (LEAKAGE_FORBIDDEN lists them; a test asserts
none leak). Missing values flow through as NaN; LightGBM handles them
natively, so nothing is imputed or fabricated.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd

# The label the survival model predicts.
LABEL_COLUMN = "survived"

# Columns that must NEVER appear in the feature matrix -- they are future
# data (the outcome) or the label itself. The no-leakage test checks this.
LEAKAGE_FORBIDDEN: List[str] = [
    "survived",
    "outcome_base_reserve",
    "outcome_quote_reserve",
    "outcome_checked_at",
]

# Raw point-in-time columns passed straight through as features.
_RAW_FEATURE_COLUMNS: List[str] = [
    "liq_base_reserve",
    "liq_quote_reserve",
    "lp_burned",
    "curve_real_sol_reserves",
    "curve_real_token_reserves",
    "curve_token_total_supply",
    "deployer_prior_launches",
    "deployer_age_secs",
]

# Engineered columns added by _engineer(), in order.
_ENGINEERED_FEATURE_COLUMNS: List[str] = [
    "log_liq_base_reserve",
    "log_liq_quote_reserve",
    "log_curve_real_sol_reserves",
    "log_curve_token_total_supply",
    "log_deployer_prior_launches",
    "log_deployer_age_secs",
    "liq_to_curve_sol_ratio",
    "curve_token_burn_fraction",
    "deployer_launch_rate_per_day",
]

# The canonical ordered feature-matrix columns -- one source of truth.
FEATURE_COLUMNS: List[str] = _RAW_FEATURE_COLUMNS + _ENGINEERED_FEATURE_COLUMNS

_SECONDS_PER_DAY = 86_400.0


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise divide; a zero or NaN denominator yields NaN, never inf."""
    denom = denominator.where(denominator != 0)
    return numerator / denom


def _engineer(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame of the engineered feature columns, aligned to df.index."""
    out = pd.DataFrame(index=df.index)
    # log1p transforms of skewed counts / reserves -- NaN stays NaN.
    out["log_liq_base_reserve"] = np.log1p(df["liq_base_reserve"])
    out["log_liq_quote_reserve"] = np.log1p(df["liq_quote_reserve"])
    out["log_curve_real_sol_reserves"] = np.log1p(df["curve_real_sol_reserves"])
    out["log_curve_token_total_supply"] = np.log1p(
        df["curve_token_total_supply"]
    )
    out["log_deployer_prior_launches"] = np.log1p(df["deployer_prior_launches"])
    out["log_deployer_age_secs"] = np.log1p(df["deployer_age_secs"])
    # entry liquidity (SOL side) relative to the bonding curve's final SOL.
    out["liq_to_curve_sol_ratio"] = _safe_divide(
        df["liq_quote_reserve"], df["curve_real_sol_reserves"]
    )
    # fraction of the curve's token supply already sold off the curve.
    sold = df["curve_token_total_supply"] - df["curve_real_token_reserves"]
    out["curve_token_burn_fraction"] = _safe_divide(
        sold, df["curve_token_total_supply"]
    )
    # deployer prior launches per day of pump.fun-relative wallet age.
    age_days = df["deployer_age_secs"] / _SECONDS_PER_DAY
    out["deployer_launch_rate_per_day"] = _safe_divide(
        df["deployer_prior_launches"], age_days
    )
    return out


def build_features(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """Build the model feature matrix X and the label series y.

    Args:
        df: the raw DataFrame from model.data.load_dataframe -- indexed by
            mint, with the historical_graduations columns.

    Returns:
        (X, y) -- X is the point-in-time feature matrix with columns exactly
        FEATURE_COLUMNS; y is the integer survived label. Both are indexed
        by mint, aligned. Missing inputs propagate to X as NaN.
    """
    raw = df[_RAW_FEATURE_COLUMNS].copy()
    engineered = _engineer(df)
    X = pd.concat([raw, engineered], axis=1)
    X = X[FEATURE_COLUMNS]  # enforce the canonical column order
    y = df[LABEL_COLUMN].astype(int)
    y.name = LABEL_COLUMN
    return X, y
