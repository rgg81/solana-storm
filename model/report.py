"""Metrics and the Phase 3 report (spec 8).

Turns a WalkForwardResult into the Phase 3 deliverable: a markdown summary
plus matplotlib plots (the equity curve, the probability-calibration curve,
the per-position outcome distribution), written under config.report_dir. The
metrics are portfolio-level -- total return, max drawdown, the full
fat-tailed outcome distribution, and calibration -- never classifier
accuracy, which is misleading under heavy class imbalance.

The report STATES the decision-gate inputs (does the model basket beat all
three baselines after costs, across >= 2 regimes, with max drawdown <= 40%);
a human reads the gate. The report never auto-decides it.
"""

from __future__ import annotations

import os
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")  # headless: write PNGs, never open a window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from model.backtest import Position  # noqa: E402
from model.config import Config  # noqa: E402
from model.regime import assign_regime  # noqa: E402
from model.walkforward import WalkForwardResult  # noqa: E402

_MAX_DRAWDOWN_GATE = 0.40  # spec 2: the pre-committed drawdown ceiling
_BASELINE_KEYS = ("buy_everything", "random_basket", "heuristic_basket")


def max_drawdown(equity_curve: pd.Series) -> float:
    """The largest peak-to-trough fractional drop, as a positive fraction.

    A monotonically rising curve has a drawdown of 0.0.
    """
    if len(equity_curve) == 0:
        return 0.0
    running_peak = equity_curve.cummax()
    drawdowns = (running_peak - equity_curve) / running_peak
    return float(drawdowns.max())


def outcome_distribution(positions: List[Position]) -> Dict[str, float]:
    """Summary statistics of a list of per-position returns.

    Returns count, mean_return, median_return, win_rate (fraction with a
    positive return), and total_loss_count (positions at -100%).
    """
    if not positions:
        return {
            "count": 0,
            "mean_return": 0.0,
            "median_return": 0.0,
            "win_rate": 0.0,
            "total_loss_count": 0,
        }
    returns = np.array([p.return_pct for p in positions], dtype=float)
    return {
        "count": int(len(returns)),
        "mean_return": float(np.mean(returns)),
        "median_return": float(np.median(returns)),
        "win_rate": float(np.mean(returns > 0.0)),
        "total_loss_count": int(np.sum(returns <= -0.999)),
    }


def calibration_table(
    scores: pd.Series, labels: pd.Series, n_bins: int = 10
) -> pd.DataFrame:
    """Bin predicted survival probabilities against the observed rate.

    Each row: the bin midpoint, the mean predicted probability, the observed
    survival rate, and the bin's token count. Empty bins are dropped.
    """
    df = pd.DataFrame({"score": scores.values, "label": labels.values})
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    df["bin"] = pd.cut(df["score"], bins=edges, include_lowest=True)
    rows = []
    for interval, group in df.groupby("bin", observed=True):
        if len(group) == 0:
            continue
        rows.append(
            {
                "bin_mid": float(interval.mid),
                "predicted_mean": float(group["score"].mean()),
                "observed_rate": float(group["label"].mean()),
                "count": int(len(group)),
            }
        )
    return pd.DataFrame(rows)


def _pool_positions(results) -> List[Position]:
    """Flatten the per-fold positions of a list of BacktestResults."""
    pooled: List[Position] = []
    for result in results:
        pooled.extend(result.positions)
    return pooled


def _chain_equity(results) -> pd.Series:
    """Chain per-fold equity curves into one normalised compounding curve.

    Each fold's curve is rebased to start where the previous fold ended, so
    the pooled curve compounds fold returns -- the walk-forward equity curve.
    """
    chained_values: List[float] = []
    level = 1.0
    for result in results:
        curve = result.equity_curve
        if len(curve) == 0:
            continue
        start = curve.iloc[0]
        if start == 0:
            continue
        normalised = curve / start * level
        chained_values.extend(list(normalised.values))
        level = normalised.iloc[-1]
    if not chained_values:
        return pd.Series([1.0], name="equity")
    return pd.Series(chained_values, name="equity")


def _total_return(results) -> float:
    """The compounded total return across a list of per-fold results."""
    level = 1.0
    for result in results:
        level *= (1.0 + result.total_return)
    return level - 1.0


def _plot_equity(model_curve, baseline_curves, path) -> None:
    plt.figure(figsize=(9, 5))
    plt.plot(model_curve.values, label="model basket", linewidth=2)
    for name, curve in baseline_curves.items():
        plt.plot(curve.values, label=name, alpha=0.7)
    plt.title("Walk-forward equity curve (compounded, normalised to 1.0)")
    plt.xlabel("backtest event")
    plt.ylabel("equity (x initial)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()


def _plot_calibration(table, path) -> None:
    plt.figure(figsize=(6, 6))
    plt.plot([0, 1], [0, 1], "k--", label="perfect calibration")
    if len(table) > 0:
        plt.plot(table["predicted_mean"], table["observed_rate"],
                 "o-", label="model")
    plt.title("Survival-probability calibration")
    plt.xlabel("predicted survival probability")
    plt.ylabel("observed survival rate")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()


def _plot_outcomes(positions, path) -> None:
    plt.figure(figsize=(9, 5))
    returns = [p.return_pct for p in positions]
    if returns:
        plt.hist(returns, bins=40)
    plt.title("Per-position outcome distribution (model basket)")
    plt.xlabel("position return (fraction)")
    plt.ylabel("position count")
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()


def write_report(
    wf_result: WalkForwardResult, df: pd.DataFrame, config: Config
) -> str:
    """Write report.md and the three PNG plots under config.report_dir.

    Returns the path to report.md.
    """
    os.makedirs(config.report_dir, exist_ok=True)

    model_results = [f.model_result for f in wf_result.folds]
    baseline_results = {
        key: [f.baseline_results[key] for f in wf_result.folds]
        for key in _BASELINE_KEYS
    }

    # pooled equity, returns, drawdown.
    model_curve = _chain_equity(model_results)
    baseline_curves = {
        key: _chain_equity(results)
        for key, results in baseline_results.items()
    }
    model_total = _total_return(model_results)
    model_dd = max_drawdown(model_curve)
    baseline_totals = {
        key: _total_return(results)
        for key, results in baseline_results.items()
    }

    # pooled positions + the outcome distribution.
    model_positions = _pool_positions(model_results)
    model_dist = outcome_distribution(model_positions)

    # pooled calibration over every fold's held-out test scores.
    all_scores = pd.concat([f.test_scores for f in wf_result.folds]) \
        if wf_result.folds else pd.Series(dtype=float)
    all_labels = pd.concat([f.test_labels for f in wf_result.folds]) \
        if wf_result.folds else pd.Series(dtype=int)
    cal_table = (
        calibration_table(all_scores, all_labels)
        if len(all_scores) > 0 else pd.DataFrame()
    )

    # per-regime model total return.
    regimes = assign_regime(df)
    per_regime: Dict[str, float] = {}
    for fold in wf_result.folds:
        # every test token of a fold shares the fold's test month/regime.
        if not fold.fold.test_mints:
            continue
        sample_mint = fold.fold.test_mints[0]
        regime = regimes.loc[sample_mint]
        per_regime.setdefault(regime, 1.0)
        per_regime[regime] *= (1.0 + fold.model_result.total_return)
    per_regime = {k: v - 1.0 for k, v in per_regime.items()}

    # plots.
    _plot_equity(model_curve, baseline_curves,
                 os.path.join(config.report_dir, "equity_curve.png"))
    _plot_calibration(cal_table,
                      os.path.join(config.report_dir, "calibration.png"))
    _plot_outcomes(model_positions,
                   os.path.join(config.report_dir,
                                "outcome_distribution.png"))

    # the decision-gate inputs (stated, not auto-decided).
    beats_all = all(
        model_total > baseline_totals[key] for key in _BASELINE_KEYS
    )
    enough_regimes = len(per_regime) >= 2
    drawdown_ok = model_dd <= _MAX_DRAWDOWN_GATE

    report_path = os.path.join(config.report_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(_render_markdown(
            config=config,
            n_folds=len(wf_result.folds),
            model_total=model_total,
            model_dd=model_dd,
            baseline_totals=baseline_totals,
            model_dist=model_dist,
            per_regime=per_regime,
            cal_table=cal_table,
            beats_all=beats_all,
            enough_regimes=enough_regimes,
            drawdown_ok=drawdown_ok,
        ))
    return report_path


def _render_markdown(**ctx) -> str:
    """Render the report.md body from the computed context."""
    config: Config = ctx["config"]
    lines: List[str] = []
    lines.append("# Phase 3 — Survival Model & Backtest Report")
    lines.append("")
    lines.append(
        "An honest walk-forward, portfolio-evolution backtest of the "
        "pump.fun token survival strategy. Generated by `model/run.py`."
    )
    lines.append("")
    lines.append("## Configuration")
    lines.append("")
    lines.append(f"- Slots: {config.slot_count}")
    lines.append(f"- Entry score threshold: {config.entry_threshold}")
    lines.append(f"- DEX fee per leg: {config.dex_fee_rate}")
    lines.append(f"- Walk-forward folds run: {ctx['n_folds']}")
    lines.append("")
    lines.append("## Headline results (after costs, out-of-sample)")
    lines.append("")
    lines.append("| Basket | Total return | ")
    lines.append("|---|---|")
    lines.append(f"| **Model** | {ctx['model_total']:+.2%} |")
    for key, value in ctx["baseline_totals"].items():
        lines.append(f"| {key} | {value:+.2%} |")
    lines.append("")
    lines.append(f"- Model basket max drawdown: {ctx['model_dd']:.2%}")
    dist = ctx["model_dist"]
    lines.append(
        f"- Model positions: {dist['count']}, "
        f"win rate {dist['win_rate']:.2%}, "
        f"mean return {dist['mean_return']:+.2%}, "
        f"median return {dist['median_return']:+.2%}, "
        f"total-loss positions {dist['total_loss_count']}"
    )
    lines.append("")
    lines.append("## Per-regime model return")
    lines.append("")
    if ctx["per_regime"]:
        lines.append("| Regime | Model total return |")
        lines.append("|---|---|")
        for regime, value in sorted(ctx["per_regime"].items()):
            lines.append(f"| {regime} | {value:+.2%} |")
    else:
        lines.append("_No folds ran -- per-regime breakdown unavailable._")
    lines.append("")
    lines.append("## Probability calibration")
    lines.append("")
    cal = ctx["cal_table"]
    if len(cal) > 0:
        lines.append("| Predicted mean | Observed rate | Count |")
        lines.append("|---|---|---|")
        for _, row in cal.iterrows():
            lines.append(
                f"| {row['predicted_mean']:.3f} | "
                f"{row['observed_rate']:.3f} | {int(row['count'])} |"
            )
    else:
        lines.append("_No held-out scores -- calibration unavailable._")
    lines.append("")
    lines.append("![equity curve](equity_curve.png)")
    lines.append("")
    lines.append("![calibration](calibration.png)")
    lines.append("")
    lines.append("![outcome distribution](outcome_distribution.png)")
    lines.append("")
    lines.append("## Decision gate")
    lines.append("")
    lines.append(
        "The pre-committed decision gate (spec 2): reviving the parked live "
        "component is greenlit only if the model basket beats all three "
        "baselines, out-of-sample, after costs, across >= 2 distinct market "
        "regimes, with a maximum drawdown <= 40%. This report states the "
        "inputs; a human evaluates the gate."
    )
    lines.append("")
    lines.append(
        f"- Beats all three baselines on total return: "
        f"**{ctx['beats_all']}**"
    )
    lines.append(
        f"- Spans >= 2 distinct market regimes: "
        f"**{ctx['enough_regimes']}**"
    )
    lines.append(
        f"- Max drawdown <= 40%: **{ctx['drawdown_ok']}** "
        f"(measured {ctx['model_dd']:.2%})"
    )
    lines.append("")
    gate_pass = (
        ctx["beats_all"] and ctx["enough_regimes"] and ctx["drawdown_ok"]
    )
    lines.append(
        f"All three gate inputs hold: **{gate_pass}**. A human makes the "
        "final deploy / do-not-deploy decision from this report; a failing "
        "gate ('no edge -- do not deploy') is a valid, planned outcome."
    )
    lines.append("")
    return "\n".join(lines)
