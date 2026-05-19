"""The honest fill model: a DEX swap fee per leg + constant-product slippage.

Every backtest fill is priced by swapping the order slice against the pool's
real depth using the constant-product (x*y=k) invariant, after a swap fee is
taken off the input leg. `base_reserve` is the token side of the pool;
`quote_reserve` is the SOL side -- the same convention as the dataset's
liq_*/outcome_* reserve columns. Selling a slice into a near-empty pool is
priced honestly: the constant-product curve returns far less than the naive
mark, modelling the exit-liquidity problem directly.
"""

from __future__ import annotations


def _check_fee(fee_rate: float) -> None:
    """Reject a fee rate outside [0, 1)."""
    if fee_rate < 0.0 or fee_rate >= 1.0:
        raise ValueError(f"fee_rate must be in [0, 1), got {fee_rate}")


def entry_fill(
    sol_in: float,
    base_reserve: float,
    quote_reserve: float,
    fee_rate: float,
) -> float:
    """Tokens received for buying with `sol_in` SOL against pool depth.

    The fee is taken off the SOL input; the net SOL is swapped on the
    constant-product curve x*y=k. Returns the token amount out (>= 0). A
    zero-size order or an empty pool returns 0.0.
    """
    _check_fee(fee_rate)
    if sol_in <= 0.0 or base_reserve <= 0.0 or quote_reserve <= 0.0:
        return 0.0
    net_sol = sol_in * (1.0 - fee_rate)
    k = base_reserve * quote_reserve
    new_quote = quote_reserve + net_sol
    new_base = k / new_quote
    return base_reserve - new_base


def exit_fill(
    tokens_in: float,
    base_reserve: float,
    quote_reserve: float,
    fee_rate: float,
) -> float:
    """SOL received for selling `tokens_in` tokens against pool depth.

    The fee is taken off the token input; the net tokens are swapped on the
    constant-product curve x*y=k. Returns the SOL amount out (>= 0), which is
    strictly less than the pool's quote depth. A zero-size order or an empty
    pool returns 0.0 -- an abandoned token's empty pool realises nothing.
    """
    _check_fee(fee_rate)
    if tokens_in <= 0.0 or base_reserve <= 0.0 or quote_reserve <= 0.0:
        return 0.0
    net_tokens = tokens_in * (1.0 - fee_rate)
    k = base_reserve * quote_reserve
    new_base = base_reserve + net_tokens
    new_quote = k / new_base
    return quote_reserve - new_quote
