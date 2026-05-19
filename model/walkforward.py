"""The expanding-window walk-forward harness (spec 8).

Tokens are ordered by graduation month. Each fold trains on every token in
every month before a cutoff month and tests on the cutoff month -- the
training window expands as the cutoff rolls forward. The training cutoff
strictly precedes the test tokens and every feature is point-in-time, so
there is no lookahead leakage (test_walkforward.py asserts this explicitly).

For each fold the harness trains a calibrated SurvivalModel, scores the
held-out test tokens, selects the model basket (test tokens scoring at or
above the entry threshold), and backtests it -- and the three baselines over
the same test tokens -- through the portfolio-evolution simulator. A fold
with too few training or test rows is skipped and logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd

from model.backtest import BacktestResult, run_backtest
from model.baselines import buy_everything, heuristic_basket, random_basket
from model.config import Config
from model.features import build_features
from model.regime import month_of
from model.survival import train_survival_model

log = logging.getLogger("model.walkforward")

# Minimum rows for a fold to run (spec 10: too-small folds are skipped).
_MIN_TRAIN_ROWS = 50
_MIN_TEST_ROWS = 20

# The 3-rule heuristic baseline's thresholds (Task 6 re-specified rules).
# A non-serial deployer and an entry-liquidity floor; chosen to be a
# meaningful but not extreme filter on this dataset.
_HEURISTIC_MAX_PRIOR_LAUNCHES = 10
_HEURISTIC_MIN_LIQ_QUOTE = 5_000_000_000.0  # 5 SOL in lamports


@dataclass
class Fold:
    """One expanding-window fold: train months -> a single test month."""

    train_months: List[str]
    test_month: str
    train_mints: List[str]
    test_mints: List[str]


@dataclass
class FoldResult:
    """The backtest outcomes of one fold -- model + three baselines."""

    fold: Fold
    model_result: BacktestResult
    baseline_results: Dict[str, BacktestResult]
    test_scores: pd.Series          # calibrated score per test token
    test_labels: pd.Series          # the survived label per test token
    model_basket_size: int


@dataclass
class WalkForwardResult:
    """Every fold's results across the whole walk-forward run."""

    folds: List[FoldResult] = field(default_factory=list)


def build_folds(df: pd.DataFrame) -> List[Fold]:
    """Build expanding-window folds from the dataset's calendar months.

    Fold i trains on months[:i] (>= 2 months) and tests on months[i], for i
    from 2 up to the last month -- so the first fold has >= 2 training months.
    """
    months_by_mint = df["graduation_time"].apply(month_of)
    months = sorted(months_by_mint.unique())
    folds: List[Fold] = []
    for i in range(2, len(months)):
        train_months = months[:i]            # months[0 .. i-1]: >= 2 months
        test_month = months[i]
        train_mask = months_by_mint.isin(train_months)
        test_mask = months_by_mint == test_month
        folds.append(
            Fold(
                train_months=list(train_months),
                test_month=test_month,
                train_mints=list(df.index[train_mask]),
                test_mints=list(df.index[test_mask]),
            )
        )
    return folds


def _run_one_fold(
    df: pd.DataFrame, fold: Fold, config: Config
) -> FoldResult:
    """Train, score, and backtest one fold's model and baselines."""
    train_df = df.loc[fold.train_mints]
    test_df = df.loc[fold.test_mints]

    X_train, y_train = build_features(train_df)
    X_test, _y_test = build_features(test_df)

    model = train_survival_model(
        X_train, y_train,
        calibration_fraction=config.calibration_fraction,
        random_seed=config.random_seed,
    )
    scores = model.score(X_test)

    # the model basket: test tokens whose calibrated score clears the gate.
    model_basket = set(scores.index[scores >= config.entry_threshold])

    def _bt(basket):
        return run_backtest(
            test_df, basket=basket, slot_count=config.slot_count,
            initial_bankroll=config.initial_bankroll,
            dex_fee_rate=config.dex_fee_rate,
        )

    model_result = _bt(model_basket)
    baseline_results = {
        "buy_everything": _bt(buy_everything(test_df)),
        "random_basket": _bt(
            random_basket(test_df, size=len(model_basket),
                          seed=config.random_seed)
        ),
        "heuristic_basket": _bt(
            heuristic_basket(
                test_df,
                max_prior_launches=_HEURISTIC_MAX_PRIOR_LAUNCHES,
                min_liq_quote=_HEURISTIC_MIN_LIQ_QUOTE,
            )
        ),
    }
    return FoldResult(
        fold=fold,
        model_result=model_result,
        baseline_results=baseline_results,
        test_scores=scores,
        test_labels=test_df["survived"].astype(int),
        model_basket_size=len(model_basket),
    )


def run_walkforward(df: pd.DataFrame, config: Config) -> WalkForwardResult:
    """Run the whole expanding-window walk-forward backtest.

    Returns a WalkForwardResult with one FoldResult per runnable fold. A fold
    with fewer than _MIN_TRAIN_ROWS training or _MIN_TEST_ROWS test rows is
    skipped and logged.
    """
    result = WalkForwardResult()
    for fold in build_folds(df):
        if len(fold.train_mints) < _MIN_TRAIN_ROWS:
            log.info(
                "skipping fold (test %s): only %d training rows",
                fold.test_month, len(fold.train_mints),
            )
            continue
        if len(fold.test_mints) < _MIN_TEST_ROWS:
            log.info(
                "skipping fold (test %s): only %d test rows",
                fold.test_month, len(fold.test_mints),
            )
            continue
        log.info(
            "fold: train %s (%d rows) -> test %s (%d rows)",
            fold.train_months, len(fold.train_mints),
            fold.test_month, len(fold.test_mints),
        )
        result.folds.append(_run_one_fold(df, fold, config))
    return result
