"""Fee + slippage model for Solana DEX swaps via Jupiter aggregator.

References:
- Jupiter routes through LP pools (Raydium, Orca, Meteora, etc.); LP fees vary
  by pool (0.25%-1%), Jupiter itself is 0% protocol fee
- We use a typical-route assumption of 0.30% all-in LP fee
- Slippage = (trade_size_usd / pool_liquidity_usd) × IMPACT_COEFFICIENT
  - Linear approximation; constant-product impact is non-linear but linear
    is a good upper-bound for trades < 5% of pool size
- Solana network fee: ~$0.0001 per signature → negligible at our trade sizes
- Priority fee for fast confirmation: $0.001-$0.10 → also negligible

For a $1,000 trade in a $1M-liquidity pool:
  fee = $3.00 (0.3%)
  slippage = $1,000 × ($1,000 / $1,000,000) = $1.00 (0.1%)
  total cost = $4.00 = 0.4% of trade

For a $1,000 trade in a $100K-liquidity pool:
  slippage = $1,000 × ($1,000 / $100,000) = $10.00 (1.0%)
  total cost = $13.00 = 1.3% of trade
"""
from __future__ import annotations
from dataclasses import dataclass

# Defaults — tunable
DEFAULT_LP_FEE_PCT = 0.0030        # 0.30% Jupiter-aggregated LP fee
SOLANA_NETWORK_FEE_USD = 0.001     # negligible
PRIORITY_FEE_USD = 0.01            # negligible
IMPACT_COEFFICIENT = 1.0           # slippage_pct = trade/pool × coefficient
MIN_SLIPPAGE_PCT = 0.0005          # floor: 5 bps even in deep pools (spread)
MAX_REASONABLE_SLIPPAGE_PCT = 0.10 # 10% — refuse to trade beyond


@dataclass
class TradeCostEstimate:
    """All-in cost estimate for a single swap."""
    trade_size_usd: float
    pool_liquidity_usd: float
    fee_pct: float          # LP fee as fraction
    slippage_pct: float     # estimated price impact
    fixed_fee_usd: float    # network + priority
    total_cost_pct: float   # fee + slippage as % of trade
    total_cost_usd: float
    breakeven_pct: float    # round-trip cost (this trade × 2)
    
    def to_dict(self) -> dict:
        return {
            "trade_size_usd": round(self.trade_size_usd, 2),
            "pool_liquidity_usd": round(self.pool_liquidity_usd, 0),
            "fee_pct": round(self.fee_pct, 5),
            "slippage_pct": round(self.slippage_pct, 5),
            "total_cost_pct": round(self.total_cost_pct, 5),
            "total_cost_usd": round(self.total_cost_usd, 4),
            "breakeven_pct": round(self.breakeven_pct, 5),
        }


def estimate(trade_size_usd: float, pool_liquidity_usd: float,
              lp_fee_pct: float = DEFAULT_LP_FEE_PCT) -> TradeCostEstimate:
    """Estimate all-in cost for a single buy or sell trade."""
    if pool_liquidity_usd <= 0:
        # No liquidity = can't trade reasonably; use max slippage marker
        slip_pct = MAX_REASONABLE_SLIPPAGE_PCT
    else:
        slip_pct = max(MIN_SLIPPAGE_PCT,
                        (trade_size_usd / pool_liquidity_usd) * IMPACT_COEFFICIENT)
    fixed = SOLANA_NETWORK_FEE_USD + PRIORITY_FEE_USD
    fee_usd = trade_size_usd * lp_fee_pct
    slip_usd = trade_size_usd * slip_pct
    total_usd = fee_usd + slip_usd + fixed
    total_pct = total_usd / trade_size_usd if trade_size_usd > 0 else 0
    return TradeCostEstimate(
        trade_size_usd=trade_size_usd,
        pool_liquidity_usd=pool_liquidity_usd,
        fee_pct=lp_fee_pct,
        slippage_pct=slip_pct,
        fixed_fee_usd=fixed,
        total_cost_pct=total_pct,
        total_cost_usd=total_usd,
        breakeven_pct=total_pct * 2,  # round-trip
    )


def is_trade_viable(trade_size_usd: float, pool_liquidity_usd: float,
                     min_expected_move_pct: float = 0.02) -> tuple[bool, str]:
    """Decide if a trade clears the cost floor.
    
    A trade is viable iff expected_move_pct > round_trip_cost × safety_margin.
    Default: need expected 2%+ move to make 0.6%+ round-trip worth it.
    """
    est = estimate(trade_size_usd, pool_liquidity_usd)
    if est.slippage_pct > MAX_REASONABLE_SLIPPAGE_PCT:
        return False, f"slippage {est.slippage_pct*100:.1f}% exceeds MAX_REASONABLE ({MAX_REASONABLE_SLIPPAGE_PCT*100:.1f}%)"
    rt = est.breakeven_pct
    if min_expected_move_pct < rt * 1.5:
        return False, f"expected move {min_expected_move_pct*100:.2f}% insufficient vs round-trip cost {rt*100:.2f}%"
    return True, f"OK (cost {est.total_cost_pct*100:.3f}%, round-trip {rt*100:.3f}%)"


if __name__ == "__main__":
    import json
    # Quick sanity table
    print("=== Trade cost matrix ===")
    print(f"{'trade $':>10} {'pool $':>12} {'fee':>7} {'slip':>7} {'total':>7} {'rt_BE':>7}")
    for trade in [100, 500, 1000, 2000]:
        for pool in [50_000, 200_000, 1_000_000, 5_000_000]:
            e = estimate(trade, pool)
            print(f"{trade:>10} {pool:>12,} {e.fee_pct*100:>6.2f}% "
                  f"{e.slippage_pct*100:>6.2f}% {e.total_cost_pct*100:>6.2f}% "
                  f"{e.breakeven_pct*100:>6.2f}%")
