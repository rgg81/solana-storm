//! Pure liquidity feature computation.

/// Already-fetched pool liquidity inputs (raw, oracle-free).
#[derive(Debug, Clone, Copy)]
pub struct PoolReserves {
    /// Raw balance of the pool's base-token account (the graduated token).
    pub base_reserve: u64,
    /// Raw balance of the pool's quote-token account (wrapped SOL).
    pub quote_reserve: u64,
    /// The PumpSwap pool record's `lp_supply` field.
    pub lp_supply: u64,
    /// The graduated token's total supply (raw units).
    pub token_total_supply: u64,
}

/// Liquidity features — the Lean-v1 "liquidity" feature group.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LiquidityFeatures {
    /// Raw base-token reserve held by the pool.
    pub base_reserve: u64,
    /// Raw quote-token (wrapped SOL) reserve held by the pool.
    pub quote_reserve: u64,
    /// True if the pool's LP supply is zero — a coarse "LP burned" proxy.
    pub lp_burned: bool,
    /// Fraction of total token supply that sits in the pool, in `[0.0, 1.0]`.
    /// An oracle-free proxy for pool liquidity relative to market cap. `0.0`
    /// when total supply is zero (degenerate input).
    pub pool_supply_fraction: f64,
}

impl PoolReserves {
    /// True if the pool holds no LP tokens — the coarse v1 "LP burned" signal.
    fn lp_burned(&self) -> bool {
        self.lp_supply == 0
    }

    /// Fraction of total supply held by the pool. `0.0` if supply is zero.
    fn pool_supply_fraction(&self) -> f64 {
        if self.token_total_supply == 0 {
            return 0.0;
        }
        self.base_reserve as f64 / self.token_total_supply as f64
    }
}

/// Derive the liquidity features from already-fetched pool reserves.
pub fn liquidity_features(reserves: &PoolReserves) -> LiquidityFeatures {
    LiquidityFeatures {
        base_reserve: reserves.base_reserve,
        quote_reserve: reserves.quote_reserve,
        lp_burned: reserves.lp_burned(),
        pool_supply_fraction: reserves.pool_supply_fraction(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lp_burned_when_supply_is_zero() {
        let r = PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 0,
            token_total_supply: 1_000_000_000_000_000,
        };
        assert!(liquidity_features(&r).lp_burned);
    }

    #[test]
    fn lp_not_burned_when_supply_positive() {
        let r = PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 1_000_000,
            token_total_supply: 1_000_000_000_000_000,
        };
        assert!(!liquidity_features(&r).lp_burned);
    }

    #[test]
    fn pool_supply_fraction_is_base_over_total() {
        // 200T base of a 1_000T total supply = 20% of supply in the pool.
        let r = PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 0,
            token_total_supply: 1_000_000_000_000_000,
        };
        let f = liquidity_features(&r);
        assert!((f.pool_supply_fraction - 0.2).abs() < 1e-9);
    }

    #[test]
    fn pool_supply_fraction_is_zero_for_zero_supply() {
        let r = PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 0,
            token_total_supply: 0,
        };
        assert_eq!(liquidity_features(&r).pool_supply_fraction, 0.0);
    }

    #[test]
    fn reserves_are_passed_through() {
        let r = PoolReserves {
            base_reserve: 123,
            quote_reserve: 456,
            lp_supply: 0,
            token_total_supply: 1_000,
        };
        let f = liquidity_features(&r);
        assert_eq!(f.base_reserve, 123);
        assert_eq!(f.quote_reserve, 456);
    }
}
