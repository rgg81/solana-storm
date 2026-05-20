"""The portfolio-evolution backtest simulator (spec 7).

One paper bankroll is split into N equal slots. The simulator walks the
dataset chronologically: a token becomes eligible at its entry instant
(graduation_time + an offset, the T0+12h point-in-time moment); if it is in
the basket and a slot is free it enters one equal-weight slice of the current
bankroll, priced through the honest fill model against the entry pool depth.
It is held to its horizon (outcome_checked_at) and exits there, priced
against the outcome pool depth -- an abandoned token's empty pool realises
-100%. Capital freed at an exit recycles into the next eligible entry. The
output is an equity curve over calendar time plus every per-position outcome.

No leakage: entry decisions use only the entry instant and pool depth; the
outcome reserves are read ONLY at the exit event.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Set

import pandas as pd

from model.costs import exit_fill, entry_fill

log = logging.getLogger("model.backtest")

# The point-in-time entry instant is T0 + 12 hours (spec 4 / 7).
ENTRY_OFFSET_SECS = 12 * 3600


@dataclass
class Position:
    """One completed round-trip trade."""

    mint: str
    entry_time: int
    exit_time: int
    sol_in: float          # SOL committed at entry (the slot slice)
    sol_out: float         # SOL realised at exit
    return_pct: float      # (sol_out - sol_in) / sol_in


@dataclass
class BacktestResult:
    """The outcome of one portfolio-evolution simulation."""

    positions: List[Position]
    equity_curve: pd.Series        # equity indexed by event time, chronological
    final_equity: float
    total_return: float            # (final - initial) / initial
    excluded_no_liquidity: int     # basket tokens dropped for NaN entry depth


def _has_entry_liquidity(row: pd.Series) -> bool:
    """True if the token has a usable (non-NaN, positive) entry pool depth."""
    base = row["liq_base_reserve"]
    quote = row["liq_quote_reserve"]
    if base is None or quote is None:
        return False
    if isinstance(base, float) and math.isnan(base):
        return False
    if isinstance(quote, float) and math.isnan(quote):
        return False
    return base > 0.0 and quote > 0.0


def _compute_exit_event(
    row: pd.Series,
    entry_quote_reserve: float,
    stop_loss_threshold: float | None,
) -> tuple[int, float, float]:
    """Compute the (exit_time, exit_base, exit_quote) for one position.

    If `stop_loss_threshold` is None, returns the outcome triple as before
    (outcome_checked_at + outcome_*_reserve).

    Otherwise walks the 14 daily snapshots in chronological order. At the
    first snapshot where BOTH `snap_i_base_reserve` and `snap_i_quote_reserve`
    are non-NaN AND `snap_i_quote_reserve < threshold * entry_quote_reserve`,
    returns that snapshot's (time, base, quote). Snapshot-times are derived
    deterministically as `graduation_time + i * 86_400`. If no snapshot triggers,
    falls back to the outcome triple.

    NaN snapshots are skipped -- they neither trigger nor veto.
    """
    if stop_loss_threshold is not None:
        floor = stop_loss_threshold * entry_quote_reserve
        grad_time = int(row["graduation_time"])
        for i in range(1, 15):
            base = row[f"snap_{i}_base_reserve"]
            quote = row[f"snap_{i}_quote_reserve"]
            if math.isnan(base) or math.isnan(quote):
                continue
            if quote < floor:
                return grad_time + i * 86_400, float(base), float(quote)
    # Fall back to the existing outcome triple.
    exit_t = int(row["outcome_checked_at"])
    return exit_t, float(row["outcome_base_reserve"]), float(row["outcome_quote_reserve"])


def run_backtest(
    df: pd.DataFrame,
    basket: Set[str],
    slot_count: int,
    initial_bankroll: float,
    dex_fee_rate: float,
    entry_offset_secs: int = ENTRY_OFFSET_SECS,
    stop_loss_threshold: float | None = None,
) -> BacktestResult:
    """Simulate the portfolio-evolution backtest for one basket.

    Args:
        df: the raw frame (model.data output) -- needs graduation_time,
            outcome_checked_at, and the liq_*/outcome_* reserve columns.
            When stop_loss_threshold is set, df must also contain the 28
            intra-period snapshot columns (snap_1..14_base/quote_reserve).
        basket: the set of mints this run is allowed to hold.
        slot_count: N equal bankroll slots.
        initial_bankroll: starting paper SOL.
        dex_fee_rate: the per-leg DEX swap fee.
        entry_offset_secs: seconds after graduation_time a token is eligible.
        stop_loss_threshold: when set, each position exits at the first daily
            snapshot where quote_reserve < threshold * entry_quote_reserve.
            NaN snapshots are skipped. When None (the default), behavior is
            identical to before -- exit at outcome_checked_at.

    Returns:
        A BacktestResult with the equity curve, per-position outcomes, the
        final equity, the total return, and the excluded-token count.
    """
    # Build the chronological event list: (time, kind, mint). An ENTRY event
    # is at graduation_time + offset; an EXIT event at outcome_checked_at.
    # Tradeable = in the basket AND has usable entry liquidity.
    excluded = 0
    tradeable: List[str] = []
    for mint in basket:
        if mint not in df.index:
            log.warning("basket mint %s not in the dataset; skipping", mint)
            continue
        row = df.loc[mint]
        if not _has_entry_liquidity(row):
            excluded += 1
            continue
        tradeable.append(mint)
    if excluded:
        log.info("excluded %d basket token(s) with no entry liquidity",
                 excluded)

    degenerate_horizon = 0
    events = []
    exit_triples: dict[str, tuple[int, float, float]] = {}
    for mint in tradeable:
        row = df.loc[mint]
        entry_t = int(row["graduation_time"]) + entry_offset_secs
        exit_t, exit_base, exit_quote = _compute_exit_event(
            row,
            entry_quote_reserve=float(row["liq_quote_reserve"]),
            stop_loss_threshold=stop_loss_threshold,
        )
        if exit_t <= entry_t:
            # degenerate horizon: force the exit one second after entry so the
            # ENTRY event always precedes the EXIT. Equal-timestamp events sort
            # EXIT-before-ENTRY, which would otherwise leak the slot and drop
            # the position.
            degenerate_horizon += 1
            exit_t = entry_t + 1
        exit_triples[mint] = (exit_t, exit_base, exit_quote)
        events.append((entry_t, 1, mint))   # kind 1 = ENTRY
        events.append((exit_t, 0, mint))    # kind 0 = EXIT
    # Sort by time; at equal time process EXITs (0) before ENTRYs (1) so a
    # slot freed at instant t is reusable by an entry at the same instant.
    events.sort(key=lambda e: (e[0], e[1]))
    if degenerate_horizon:
        log.info(
            "forced a minimal hold for %d position(s) with a degenerate "
            "horizon (outcome_checked_at <= entry)",
            degenerate_horizon,
        )

    bankroll = float(initial_bankroll)
    free_slots = slot_count
    open_positions = {}            # mint -> (entry_time, sol_in, tokens_held)
    positions: List[Position] = []
    equity_points = [(events[0][0] if events else 0, bankroll)]

    for event_time, kind, mint in events:
        row = df.loc[mint]
        if kind == 1:  # ENTRY
            if free_slots <= 0:
                continue  # no slot free -- this token is skipped entirely
            slice_sol = bankroll / free_slots
            if slice_sol <= 0.0:
                continue
            tokens = entry_fill(
                sol_in=slice_sol,
                base_reserve=float(row["liq_base_reserve"]),
                quote_reserve=float(row["liq_quote_reserve"]),
                fee_rate=dex_fee_rate,
            )
            bankroll -= slice_sol
            free_slots -= 1
            open_positions[mint] = (event_time, slice_sol, tokens)
        else:  # EXIT
            if mint not in open_positions:
                continue  # this token never entered (slot was full)
            entry_time, sol_in, tokens = open_positions.pop(mint)
            _, exit_base, exit_quote = exit_triples[mint]
            sol_out = exit_fill(
                tokens_in=tokens,
                base_reserve=exit_base,
                quote_reserve=exit_quote,
                fee_rate=dex_fee_rate,
            )
            bankroll += sol_out
            free_slots += 1
            return_pct = (sol_out - sol_in) / sol_in if sol_in > 0 else 0.0
            positions.append(
                Position(
                    mint=mint,
                    entry_time=entry_time,
                    exit_time=event_time,
                    sol_in=sol_in,
                    sol_out=sol_out,
                    return_pct=return_pct,
                )
            )
        # equity = idle bankroll + the entry cost basis of open positions.
        held_basis = sum(p[1] for p in open_positions.values())
        equity_points.append((event_time, bankroll + held_basis))

    times = [t for t, _ in equity_points]
    values = [v for _, v in equity_points]
    equity_curve = pd.Series(values, index=times, name="equity")

    final_equity = float(equity_curve.iloc[-1])
    total_return = (final_equity - initial_bankroll) / initial_bankroll
    return BacktestResult(
        positions=positions,
        equity_curve=equity_curve,
        final_equity=final_equity,
        total_return=total_return,
        excluded_no_liquidity=excluded,
    )
