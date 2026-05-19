"""The three baseline basket selectors (spec 6).

The model's basket is only worth its complexity if it beats simple rules.
Each selector takes a raw/feature frame indexed by mint and returns the set
of mints that baseline holds:

  1. buy_everything   -- every graduation, equal weight.
  2. random_basket    -- a seeded random subset, the model basket's size.
  3. heuristic_basket -- the spec's re-specified 3-rule heuristic. The
     original strategy rules (LP burned + mint renounced + low holder
     concentration) are not computable on this dataset -- mint authority is
     a cohort constant and holder data was never collected -- so the
     available-feature equivalent is used: lp_burned set AND the deployer is
     not a serial re-launcher AND entry liquidity clears a floor.
"""

from __future__ import annotations

from typing import Set

import numpy as np
import pandas as pd


def buy_everything(df: pd.DataFrame) -> Set[str]:
    """Baseline 1: hold every mint in the frame."""
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
    max_prior_launches: int,
    min_liq_quote: float,
) -> Set[str]:
    """Baseline 3: the re-specified 3-rule heuristic.

    A token is held iff all three rules hold:
      - lp_burned == 1, AND
      - deployer_prior_launches <= max_prior_launches (not a serial
        re-launcher), AND
      - liq_quote_reserve >= min_liq_quote (entry liquidity clears a floor).

    A NaN in any rule column fails that comparison, so the token is excluded.
    """
    lp_ok = df["lp_burned"] == 1
    deployer_ok = df["deployer_prior_launches"] <= max_prior_launches
    liq_ok = df["liq_quote_reserve"] >= min_liq_quote
    held = lp_ok & deployer_ok & liq_ok
    held = held.fillna(False)
    return set(df.index[held])
