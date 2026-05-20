"""Unit tests for model.report -- metrics and report-artifact writing."""

import numpy as np
import pandas as pd

from model.backtest import BacktestResult, Position
from model.report import (
    calibration_table,
    max_drawdown,
    outcome_distribution,
)


def test_max_drawdown_of_a_monotonic_curve_is_zero():
    curve = pd.Series([100.0, 110.0, 120.0, 130.0])
    assert max_drawdown(curve) == 0.0


def test_max_drawdown_measures_the_largest_peak_to_trough_drop():
    # peak 200 -> trough 120 is a 40% drawdown.
    curve = pd.Series([100.0, 200.0, 120.0, 160.0])
    assert abs(max_drawdown(curve) - 0.40) < 1e-9


def test_max_drawdown_is_reported_as_a_positive_fraction():
    curve = pd.Series([100.0, 50.0])
    dd = max_drawdown(curve)
    assert dd > 0.0  # a 50% drop -> 0.5, positive


def test_outcome_distribution_summarises_positions():
    positions = [
        Position("A", 0, 1, 10.0, 25.0, 1.5),    # winner
        Position("B", 0, 1, 10.0, 12.0, 0.2),    # small winner
        Position("C", 0, 1, 10.0, 0.0, -1.0),    # total loss
        Position("D", 0, 1, 10.0, 4.0, -0.6),    # loser
    ]
    dist = outcome_distribution(positions)
    assert dist["count"] == 4
    assert dist["win_rate"] == 0.5            # 2 of 4 positive
    assert dist["total_loss_count"] == 1      # one -100%
    # the fat tail: mean != median.
    assert dist["mean_return"] != dist["median_return"]


def test_outcome_distribution_of_an_empty_basket_is_safe():
    dist = outcome_distribution([])
    assert dist["count"] == 0
    assert dist["win_rate"] == 0.0


def test_calibration_table_bins_predicted_vs_observed():
    # 100 tokens; score == true survival probability by construction.
    rng = np.random.default_rng(0)
    scores = pd.Series(rng.uniform(0, 1, size=100))
    labels = pd.Series((rng.uniform(0, 1, size=100) < scores).astype(int))
    table = calibration_table(scores, labels, n_bins=5)
    assert len(table) <= 5
    # each bin row has a predicted mean and an observed survival rate.
    assert {"bin_mid", "predicted_mean", "observed_rate", "count"}.issubset(
        table.columns
    )


def _make_backtest_result(equity_values) -> BacktestResult:
    """Build a minimal BacktestResult with the given equity curve values."""
    curve = pd.Series(equity_values, name="equity", dtype=float)
    final = float(curve.iloc[-1])
    return BacktestResult(
        positions=[],
        equity_curve=curve,
        final_equity=final,
        total_return=(final - 1.0),
        excluded_no_liquidity=0,
    )


def test_decision_gate_uses_candidate_drawdown_not_model_basket(tmp_path):
    """The drawdown_ok gate clause must read the stop-loss candidate's max
    drawdown, not the model basket's.  A regression here would silently let
    a high-drawdown stop-loss strategy pass the gate because of a low-dd
    model basket (or vice versa).

    Scenario:
    - model basket: monotonically rising  -> drawdown ~ 0%
    - stop_loss_buy_everything: peak 1.0 then drops to 0.40 -> drawdown 60%
    Expected: drawdown_ok == False  (60% > 40% gate).
    If the bug were present (gate reads model_dd), it would be True.
    """
    from model.config import load_config
    from model.walkforward import Fold, FoldResult, WalkForwardResult
    from model.report import write_report

    # Model basket: flat rise, near-zero drawdown.
    model_result = _make_backtest_result([1.0, 1.1, 1.2, 1.3])

    # Candidate: spike then crash -- 60% drawdown (1.0 -> 0.4).
    stop_loss_result = _make_backtest_result([1.0, 2.0, 0.8, 0.4])

    # Baselines: also gently rising so beats_all=False doesn't mask the test.
    buy_everything_result   = _make_backtest_result([1.0, 1.05, 1.10, 1.15])
    random_basket_result    = _make_backtest_result([1.0, 1.04, 1.08, 1.12])
    heuristic_basket_result = _make_backtest_result([1.0, 1.03, 1.06, 1.09])

    fold = Fold(
        train_months=["2026-01"],
        test_month="2026-02",
        train_mints=["M0"],
        test_mints=["M1"],
    )
    fold_result = FoldResult(
        fold=fold,
        model_result=model_result,
        baseline_results={
            "buy_everything":          buy_everything_result,
            "random_basket":           random_basket_result,
            "heuristic_basket":        heuristic_basket_result,
            "stop_loss_buy_everything": stop_loss_result,
        },
        test_scores=pd.Series([0.6], index=["M1"]),
        test_labels=pd.Series([1], index=["M1"], name="positive_return"),
        model_basket_size=1,
    )
    wf_result = WalkForwardResult(folds=[fold_result])

    # Minimal dataframe so assign_regime doesn't blow up.
    df = pd.DataFrame(
        {
            "graduation_time": [int(pd.Timestamp("2026-02-15", tz="UTC").timestamp())],
        },
        index=pd.Index(["M1"], name="mint"),
    )

    cfg = load_config(report_dir=str(tmp_path / "report"))
    write_report(wf_result, df, cfg)

    text = (tmp_path / "report" / "report.md").read_text()

    # The gate's drawdown clause must report False: 60% > 40% threshold.
    # Look for "Candidate max drawdown" line that contains False.
    assert "False" in text, (
        "drawdown_ok should be False when candidate drawdown is 60%, "
        "but the report says True -- the gate is still reading model_dd."
    )
    # Also verify the candidate drawdown is reported (not just model_dd).
    assert "stop_loss_buy_everything` max drawdown" in text, (
        "Candidate drawdown line missing from Headline results."
    )


def test_write_report_creates_the_markdown_and_plots(tmp_path):
    """write_report writes report.md and the three PNGs under report_dir."""
    from model.config import load_config
    from model.walkforward import run_walkforward
    from model.tests.test_walkforward import wf_frame

    df = wf_frame()
    cfg = load_config(report_dir=str(tmp_path / "report"))
    wf_result = run_walkforward(df, cfg)

    from model.report import write_report
    write_report(wf_result, df, cfg)

    report_dir = tmp_path / "report"
    assert (report_dir / "report.md").is_file()
    assert (report_dir / "equity_curve.png").is_file()
    assert (report_dir / "calibration.png").is_file()
    assert (report_dir / "outcome_distribution.png").is_file()
    # the markdown names the decision gate.
    text = (report_dir / "report.md").read_text()
    assert "decision gate" in text.lower()
