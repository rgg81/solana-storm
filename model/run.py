"""The price-prediction-pivot backtest orchestrator.

Wires: config -> load historical_graduations into a DataFrame -> apply the
point-in-time garbage filter -> run the expanding-window walk-forward
backtest (the calibrated positive_return model and the three baselines) ->
write the report (markdown + plots) under the report dir.

Usage:
    python3 -m model.run
    python3 -m model.run --slots 30 --entry-threshold 0.6
"""

from __future__ import annotations

import argparse
import logging

from model.config import load_config
from model.data import load_graduations
from model.filter import filter_garbage
from model.report import write_report
from model.walkforward import run_walkforward

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("model.run")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="solana-storm price-prediction-pivot walk-forward backtest"
    )
    parser.add_argument(
        "--slots", type=int, default=None,
        help="number of equal bankroll slots (default: Config.slot_count=20)",
    )
    parser.add_argument(
        "--entry-threshold", type=float, default=None,
        help="min calibrated positive_return score to enter (default: 0.5)",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help="path to the SQLite DB (default: ./storm.db)",
    )
    args = parser.parse_args()

    overrides = {}
    if args.slots is not None:
        overrides["slot_count"] = args.slots
    if args.entry_threshold is not None:
        overrides["entry_threshold"] = args.entry_threshold
    if args.db is not None:
        overrides["db_path"] = args.db
    config = load_config(**overrides)

    log.info(
        "loading historical_graduations from %s", config.db_path
    )
    df = load_graduations(config)
    log.info("loaded %d graduations (pre-filter)", len(df))

    df = filter_garbage(df, config)  # spec 4.2: same universe for all pickers
    log.info("post-filter: %d rows", len(df))

    log.info(
        "running walk-forward backtest: slots=%d entry_threshold=%.2f "
        "dex_fee=%.4f",
        config.slot_count, config.entry_threshold, config.dex_fee_rate,
    )
    wf_result = run_walkforward(df, config)
    log.info("walk-forward complete: %d folds ran", len(wf_result.folds))

    report_path = write_report(wf_result, df, config)
    log.info("report written: %s", report_path)


if __name__ == "__main__":
    main()
