"""Unit tests for model.survival -- the calibrated LightGBM survival model.

A synthetic dataset with a deliberately learnable signal is used: tokens
whose `liq_quote_reserve` is high mostly survive. The tests assert the model
learns the direction, returns calibrated [0,1] scores, and is deterministic.
"""

import numpy as np
import pandas as pd

from model.features import FEATURE_COLUMNS
from model.survival import SurvivalModel, train_survival_model


def synthetic_xy(n=400, seed=0):
    """A learnable dataset: high liq_quote_reserve -> more likely to survive."""
    rng = np.random.default_rng(seed)
    liq_quote = rng.uniform(0.0, 1.0, size=n)
    # survival probability rises with liq_quote; add noise.
    p = 0.15 + 0.7 * liq_quote
    survived = (rng.uniform(0.0, 1.0, size=n) < p).astype(int)
    data = {col: rng.normal(size=n) for col in FEATURE_COLUMNS}
    data["liq_quote_reserve"] = liq_quote
    X = pd.DataFrame(data, columns=FEATURE_COLUMNS)
    X.index = pd.Index([f"M{i}" for i in range(n)], name="mint")
    y = pd.Series(survived, index=X.index, name="survived")
    return X, y


def test_train_returns_a_survival_model():
    X, y = synthetic_xy()
    model = train_survival_model(
        X, y, calibration_fraction=0.2, random_seed=20260519
    )
    assert isinstance(model, SurvivalModel)


def test_scores_are_probabilities_in_the_unit_interval():
    X, y = synthetic_xy()
    model = train_survival_model(X, y, calibration_fraction=0.2,
                                 random_seed=20260519)
    scores = model.score(X)
    assert len(scores) == len(X)
    assert scores.min() >= 0.0 and scores.max() <= 1.0
    assert list(scores.index) == list(X.index)


def test_model_learns_the_signal_direction():
    """High-liquidity tokens should score higher than low-liquidity ones."""
    X, y = synthetic_xy(n=600, seed=1)
    model = train_survival_model(X, y, calibration_fraction=0.2,
                                 random_seed=20260519)
    scores = model.score(X)
    hi = scores[X["liq_quote_reserve"] > 0.8].mean()
    lo = scores[X["liq_quote_reserve"] < 0.2].mean()
    assert hi > lo, "the model failed to learn the planted signal"


def test_training_is_deterministic_for_a_fixed_seed():
    X, y = synthetic_xy()
    m1 = train_survival_model(X, y, calibration_fraction=0.2,
                              random_seed=20260519)
    m2 = train_survival_model(X, y, calibration_fraction=0.2,
                              random_seed=20260519)
    s1 = m1.score(X)
    s2 = m2.score(X)
    np.testing.assert_allclose(s1.values, s2.values)


def test_score_handles_nan_features():
    """LightGBM handles NaN natively -- a NaN-bearing row still scores."""
    X, y = synthetic_xy()
    model = train_survival_model(X, y, calibration_fraction=0.2,
                                 random_seed=20260519)
    X_nan = X.copy()
    X_nan.iloc[0, 0] = np.nan
    scores = model.score(X_nan)
    assert not np.isnan(scores.iloc[0])
