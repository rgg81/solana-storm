# model — Phase 3 survival model & honest backtest

A standalone Python package that trains an ML **survival model** on the Phase 2
`historical_graduations` dataset, runs a **walk-forward, portfolio-evolution
backtest** with honest DEX-fee and constant-product-slippage costs, compares the
model's basket against three baselines split by market regime, and emits a
report — the input to the project's pre-committed decision gate.

This package is **decoupled from the Rust workspace**: no crate, no `Cargo.toml`,
no `storm-store` migration. It only **reads** the `historical_graduations` table
from `./storm.db` (assembled by the Phase 2 `bootstrap/` ETL) and writes nothing
back.

## Prerequisites

- Python 3.11+.
- A populated `./storm.db` with a `historical_graduations` table — the Phase 2
  ETL deliverable.

## Install

    python3 -m pip install -r model/requirements.txt

## Run the tests

    python3 -m pytest model/tests -q

The test suite is fully offline — it builds synthetic data or uses a temporary
SQLite database; it does not depend on the real `./storm.db`.

## Run the backtest

    python3 -m model.run

This runs the whole walk-forward backtest for the survival model and all three
baselines, then writes the report.

## Output

A report under `model/report/`: a markdown summary (`report.md`) plus matplotlib
plots — the equity curve, the probability calibration curve, and the per-position
outcome distribution. The report states the decision-gate inputs for a human to
evaluate; it never auto-decides the gate.

## Run log

- Backtest run completed `2026-05-19`: `5` walk-forward folds, model basket
  total return `-98.68%`, max drawdown `98.68%`.
- Baseline total returns: buy-everything `-99.22%`, random `-99.99%`,
  heuristic `-99.49%`.
- Decision-gate inputs — beats all baselines: `True`; >= 2 regimes:
  `True`; drawdown <= 40%: `False`.
- The full report (markdown + plots) is under `model/report/`.
