"""The three baseline basket selectors (spec 7).

The model's basket is only worth its complexity if it beats simple rules.
Each selector takes a raw/feature frame indexed by mint and returns the set
of mints that baseline holds:

  1. buy_everything   -- every (filtered) graduation, equal weight.
  2. random_basket    -- a seeded random subset, the model basket's size.
  3. heuristic_basket -- the return-oriented re-specified 3-rule heuristic:
     entry liquidity clears a floor AND the deployer is in an experience
     window (not too few prior launches, not a serial churner) AND the
     bonding curve completed with significant accumulated capital.

The Phase 3 lp_burned rule is dropped: graduated tokens nearly always have
lp_burned == 1 in this dataset, so the rule provided almost no signal. The
curve-SOL floor replaces it.
"""

from __future__ import annotations

from typing import Set

import numpy as np
import pandas as pd


def buy_everything(df: pd.DataFrame) -> Set[str]:
    """Baseline 1: hold every mint in the (filtered) frame."""
    return set(df.index)


def random_basket(df: pd.DataFrame, size: int, seed: int) -> Set[str]:
    """Baseline 2: a seeded random subset of `size` mints.

    `size` is the model basket's size so the comparison is apples-to-apples.
    If `size` is at least the frame's row count, every mint is returned.
    Deterministic for a fixed seed.
    """
    mints = list(df.index)
    if size >= len(mints):
        return set(mints)
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(mints), size=size, replace=False)
    return {mints[i] for i in chosen}


def heuristic_basket(
    df: pd.DataFrame,
    min_liq_quote: float,
    deployer_launches_min: int,
    deployer_launches_max: int,
    min_curve_sol: float,
) -> Set[str]:
    """Baseline 3: the return-oriented re-specified 3-rule heuristic.

    A token is held iff all three rules hold:
      - liq_quote_reserve >= min_liq_quote (entry liquidity clears a floor),
      - deployer_prior_launches in [deployer_launches_min,
        deployer_launches_max] (a non-zero, non-spam launch history),
      - curve_real_sol_reserves >= min_curve_sol (the curve completed with
        significant capital).

    The heuristic is intentionally STRICTER than the garbage filter: NaN in
    any rule column fails that comparison and excludes the token, so a NaN
    curve-SOL value -- which the upstream filter keeps -- is OUT here.
    """
    liq_ok = df["liq_quote_reserve"] >= min_liq_quote
    deployer_ok = (
        (df["deployer_prior_launches"] >= deployer_launches_min)
        & (df["deployer_prior_launches"] <= deployer_launches_max)
    )
    curve_ok = df["curve_real_sol_reserves"] >= min_curve_sol
    held = liq_ok & deployer_ok & curve_ok
    held = held.fillna(False)
    return set(df.index[held])
