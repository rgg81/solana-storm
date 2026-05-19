"""Unit tests for model.regime -- market-regime labelling."""

import pandas as pd

from model.regime import (
    MANIA,
    QUIET,
    TRUE_MONTHLY_GRADUATIONS,
    assign_regime,
    label_regimes,
    month_of,
)


def _ts(year, month, day=15):
    """A Unix timestamp for a given calendar day, UTC."""
    return int(pd.Timestamp(year=year, month=month, day=day,
                            tz="UTC").timestamp())


def test_month_of_returns_year_month_string():
    assert month_of(_ts(2026, 1, 10)) == "2026-01"
    assert month_of(_ts(2025, 11, 30)) == "2025-11"


def test_label_regimes_uses_the_true_population_rate():
    labels = label_regimes()
    # True monthly counts ramp Nov->Mar then decline; median is 7677.
    # The above-median months Feb/Mar/Apr are mania; the rest quiet.
    assert labels["2026-02"] == MANIA
    assert labels["2026-03"] == MANIA
    assert labels["2026-04"] == MANIA
    assert labels["2025-11"] == QUIET
    assert labels["2025-12"] == QUIET
    assert labels["2026-01"] == QUIET
    assert labels["2026-05"] == QUIET


def test_label_regimes_spans_both_regimes():
    # The closed historical window genuinely contains both regimes.
    assert set(label_regimes().values()) == {MANIA, QUIET}


def test_assign_regime_returns_a_per_mint_series():
    df = pd.DataFrame(
        {"graduation_time": [_ts(2026, 3, 9), _ts(2026, 1, 20),
                             _ts(2025, 11, 8)]},
        index=pd.Index(["A", "B", "C"], name="mint"),
    )
    regimes = assign_regime(df)
    assert list(regimes.index) == ["A", "B", "C"]
    assert regimes["A"] == MANIA   # March
    assert regimes["B"] == QUIET   # January
    assert regimes["C"] == QUIET   # November
    assert regimes.name == "regime"


def test_true_counts_cover_the_seven_month_window():
    assert set(TRUE_MONTHLY_GRADUATIONS) == {
        "2025-11", "2025-12", "2026-01", "2026-02",
        "2026-03", "2026-04", "2026-05",
    }
