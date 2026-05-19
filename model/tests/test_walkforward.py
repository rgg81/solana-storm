"""Unit tests for model.walkforward -- the expanding-window harness."""

import numpy as np
import pandas as pd

from model.config import load_config
from model.regime import month_of
from model.walkforward import build_folds, run_walkforward


def _ts(year, month, day):
    return int(pd.Timestamp(year=year, month=month, day=day,
                            tz="UTC").timestamp())


def wf_frame(per_month=60, seed=0):
    """A 4-month synthetic dataset with a learnable survival signal."""
    rng = np.random.default_rng(seed)
    rows = []
    mints = []
    for mi, (yr, mo) in enumerate(
        [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]
    ):
        for k in range(per_month):
            liq_quote = rng.uniform(0.1, 5.0)
            survived = int(rng.uniform(0, 1) < 0.2 + 0.1 * liq_quote)
            grad_t = _ts(yr, mo, 1 + (k % 27))
            rows.append(
                {
                    "graduation_time": grad_t,
                    "graduation_slot": 1000 + mi * 1000 + k,
                    "survived": survived,
                    "outcome_base_reserve": 1e15,
                    "outcome_quote_reserve": (3e12 if survived else 0.0),
                    "outcome_checked_at": grad_t + 14 * 86400,
                    "liq_base_reserve": 1e15,
                    "liq_quote_reserve": liq_quote * 1e11,
                    "lp_burned": 1,
                    "curve_real_sol_reserves": 8.5e10,
                    "curve_real_token_reserves": 0.0,
                    "curve_token_total_supply": 2.8e14,
                    "deployer_prior_launches": int(rng.integers(0, 30)),
                    "deployer_age_secs": int(rng.integers(0, 1_000_000)),
                }
            )
            mints.append(f"M{mi}_{k}")
    df = pd.DataFrame(rows, index=pd.Index(mints, name="mint"))
    return df.sort_values("graduation_time", kind="stable")


def test_build_folds_makes_expanding_windows():
    folds = build_folds(wf_frame())
    # 4 months -> the first fold trains on 2, tests the 3rd; then a 4th fold.
    assert len(folds) >= 2
    first = folds[0]
    # the first fold's training months all precede its test month.
    assert first.test_month > max(first.train_months)


def test_no_test_token_predates_its_training_cutoff():
    """The explicit no-leakage test (spec 8 / 10)."""
    df = wf_frame()
    for fold in build_folds(df):
        cutoff = pd.Timestamp(fold.test_month + "-01", tz="UTC").timestamp()
        train_times = df.loc[fold.train_mints, "graduation_time"]
        test_times = df.loc[fold.test_mints, "graduation_time"]
        # every training token graduated strictly before the test month.
        assert train_times.max() < cutoff
        # every test token graduated in (or after) the test month.
        assert test_times.min() >= cutoff


def test_run_walkforward_produces_per_fold_results():
    df = wf_frame()
    cfg = load_config(entry_threshold=0.5)
    result = run_walkforward(df, cfg)
    assert len(result.folds) >= 2
    for fold_result in result.folds:
        # each fold ran the model and the three baselines.
        assert fold_result.model_result is not None
        assert set(fold_result.baseline_results) == {
            "buy_everything", "random_basket", "heuristic_basket"
        }
        # Spec 4.1: test_labels is the positive_return label for the fold's
        # test tokens -- a Series indexed by mint with integer values in {0, 1}.
        assert isinstance(fold_result.test_labels, pd.Series)
        assert fold_result.test_labels.name == "positive_return"
        assert set(fold_result.test_labels.index) == set(fold_result.fold.test_mints)
        assert fold_result.test_labels.isin([0, 1]).all()


def test_walkforward_test_scores_are_held_out():
    """The scores attached to a fold cover only that fold's test tokens."""
    df = wf_frame()
    result = run_walkforward(df, load_config())
    for fold_result in result.folds:
        scored = set(fold_result.test_scores.index)
        assert scored == set(fold_result.fold.test_mints)


def test_a_fold_with_too_few_rows_is_skipped(caplog):
    """A month with fewer than the minimum test rows is skipped, not crashed."""
    df = wf_frame(per_month=2)  # 2 rows/month -> below the min-rows floor
    result = run_walkforward(df, load_config())
    # with such thin months every fold is skipped -> no fold results.
    assert result.folds == []
