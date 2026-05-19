"""Point-in-time garbage filter -- drops clearly-hopeless tokens (spec 4.2).

Applied uniformly between data loading and walk-forward training. The filter
is deliberately permissive: only clearly-hopeless tokens are dropped, so the
model and every baseline see the same filtered universe and the backtest
evaluates `filter + picker`, not `picker alone`.

Three conjunctive rules, all on point-in-time columns (no outcome leakage):
  1. liq_quote_reserve is non-NaN AND >= min_entry_liq_lamports
  2. deployer_prior_launches <= max_deployer_prior_launches
  3. curve_real_sol_reserves is NaN (kept -- LightGBM handles NaN) OR
     >= min_curve_sol_lamports

NaN handling differs deliberately per rule: rule 1 requires the value to be
known (a token with unknown entry liquidity cannot be evaluated honestly);
rule 3 allows missing curve data (an absent value carries less information
than a known-low one and is best left to LightGBM's native NaN routing).
"""

from __future__ import annotations

import logging

import pandas as pd

from model.config import Config

log = logging.getLogger("model.filter")


def filter_garbage(df: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Apply the three garbage-filter rules and return the kept frame.

    Args:
        df: the raw graduation frame (model.data.load_graduations output),
            indexed by mint.
        config: the run config; only the three `*_lamports` / `max_*` fields
            are read.

    Returns:
        A subset of `df` keeping only the rows that pass all three rules.
        Columns and index name are preserved; an empty input yields an
        empty output of the same shape.
    """
    rule1 = (
        df["liq_quote_reserve"].notna()
        & (df["liq_quote_reserve"] >= config.min_entry_liq_lamports)
    )
    rule2 = df["deployer_prior_launches"] <= config.max_deployer_prior_launches
    rule3 = (
        df["curve_real_sol_reserves"].isna()
        | (df["curve_real_sol_reserves"] >= config.min_curve_sol_lamports)
    )
    kept = rule1 & rule2 & rule3
    n_in = len(df)
    n_out = int(kept.sum())
    log.info(
        "filter_garbage: kept %d / %d rows "
        "(rule1 entry-liq: %d drop; rule2 deployer-spam: %d drop; "
        "rule3 curve-sol: %d drop)",
        n_out, n_in,
        int((~rule1).sum()), int((~rule2).sum()), int((~rule3).sum()),
    )
    return df[kept]
