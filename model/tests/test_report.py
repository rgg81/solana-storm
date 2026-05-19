"""Unit tests for model.report -- metrics and report-artifact writing."""

import numpy as np
import pandas as pd

from model.backtest import Position
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
