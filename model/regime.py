"""Market-regime labelling (spec 8).

Each calendar month is labelled a regime -- `mania` or `quiet` -- from the
TRUE pump.fun graduation rate: a month whose graduation count is strictly
above the median monthly count is `mania`, every other month is `quiet`.

The rate is the *full settled-graduation population* (the 56,850 PumpSwap-era
graduations of Nov 2025 - May 2026), NOT the `historical_graduations` table.
That table is a month-stratified sample -- Phase 2's `sample.py` deliberately
draws ~equal tokens per calendar month -- so its per-month row counts are flat
by construction and carry no regime signal. The true counts below were
computed from the full graduations population and are fixed, immutable facts
for this closed historical window.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Dict

import pandas as pd

MANIA = "mania"
QUIET = "quiet"

# True graduations per calendar month over the full settled-graduation
# population (56,850 PumpSwap-era graduations); immutable facts for this
# closed window. The historical_graduations TABLE is a month-stratified
# sample with flat per-month counts -- it must not be used for the rate.
TRUE_MONTHLY_GRADUATIONS: Dict[str, int] = {
    "2025-11": 4880,
    "2025-12": 6007,
    "2026-01": 7677,
    "2026-02": 10790,
    "2026-03": 15175,
    "2026-04": 11646,
    "2026-05": 675,   # partial month -- the window cutoff falls on 2026-05-02
}


def month_of(graduation_time: int) -> str:
    """The 'YYYY-MM' calendar month of a Unix timestamp (UTC)."""
    dt = datetime.fromtimestamp(int(graduation_time), tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def label_regimes() -> Dict[str, str]:
    """Map each calendar month to MANIA or QUIET by the true graduation rate.

    A month whose true full-population graduation count is strictly above the
    median monthly count is MANIA; every other month is QUIET.
    """
    median = statistics.median(TRUE_MONTHLY_GRADUATIONS.values())
    return {
        month: (MANIA if count > median else QUIET)
        for month, count in TRUE_MONTHLY_GRADUATIONS.items()
    }


def assign_regime(df: pd.DataFrame) -> pd.Series:
    """A per-mint regime Series (MANIA / QUIET), aligned to df.index."""
    labels = label_regimes()
    regimes = df["graduation_time"].apply(month_of).map(labels)
    regimes.name = "regime"
    return regimes
