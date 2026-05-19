"""The survival model: a calibrated LightGBM gradient-boosted classifier.

LightGBM suits a small tabular dataset with missing values -- it handles NaN
natively, so features are never imputed. Calibration is an explicit step
(spec 5): an isotonic regressor maps the booster's raw probabilities to
calibrated ones, fitted on a time-ordered held-out slice of the training
data, because the decision gate evaluates probability calibration, not only
ranking. The model's output is a calibrated survival probability per token.
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

# LightGBM hyper-parameters -- conservative for a small dataset, to limit
# overfitting. random_state / deterministic are set per call from the seed.
_LGB_PARAMS = {
    "objective": "binary",
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 30,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "verbose": -1,
}


class SurvivalModel:
    """A trained LightGBM booster plus an isotonic probability calibrator."""

    def __init__(
        self,
        booster: lgb.LGBMClassifier,
        calibrator: IsotonicRegression,
    ) -> None:
        self._booster = booster
        self._calibrator = calibrator

    def score(self, X: pd.DataFrame) -> pd.Series:
        """A calibrated survival probability in [0, 1] for each row of X."""
        raw = self._booster.predict_proba(X)[:, 1]
        calibrated = self._calibrator.predict(raw)
        calibrated = np.clip(calibrated, 0.0, 1.0)
        return pd.Series(calibrated, index=X.index, name="survival_score")


def train_survival_model(
    X: pd.DataFrame,
    y: pd.Series,
    calibration_fraction: float,
    random_seed: int,
) -> SurvivalModel:
    """Train the LightGBM classifier and fit an isotonic calibrator.

    The last `calibration_fraction` of rows (X is already time-ordered by the
    data loader) is held out; LightGBM is fitted on the earlier rows and the
    isotonic calibrator on the booster's predictions over the held-out slice.

    Args:
        X: the point-in-time feature matrix (features.build_features output).
        y: the integer survived label, aligned to X.
        calibration_fraction: fraction of training rows held out to calibrate.
        random_seed: seeds LightGBM for reproducibility.

    Returns:
        A SurvivalModel. If the calibration slice would be empty or the
        training slice would be empty (a tiny fold), the calibrator is fitted
        on the training slice itself -- a graceful degradation, still valid.
    """
    n = len(X)
    n_calib = int(round(n * calibration_fraction))
    n_calib = max(0, min(n_calib, n - 1))  # n_calib <= n-1 => split >= 1 (fit set non-empty)
    split = n - n_calib

    X_fit, y_fit = X.iloc[:split], y.iloc[:split]
    if n_calib > 0:
        X_calib, y_calib = X.iloc[split:], y.iloc[split:]
    else:
        X_calib, y_calib = X_fit, y_fit  # tiny fold: calibrate on the fit set

    booster = lgb.LGBMClassifier(
        random_state=random_seed,
        deterministic=True,
        n_jobs=1,
        **_LGB_PARAMS,
    )
    booster.fit(X_fit, y_fit)

    raw_calib = booster.predict_proba(X_calib)[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(raw_calib, y_calib.astype(float).values)

    return SurvivalModel(booster, calibrator)
