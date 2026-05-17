use solana_sdk::pubkey::Pubkey;

use crate::pumpswap::PumpSwapPool;

/// Index of the canonical pool created by a bonding-curve graduation.
pub const CANONICAL_POOL_INDEX: u16 = 0;

/// True if `pool` is the canonical PumpSwap pool for the token `mint` — the
/// index-0 pool whose base token is `mint`. pump.fun graduations create
/// exactly this pool. (Graduation itself is signalled by the token's
/// `BondingCurve::complete` flag.)
pub fn is_canonical_graduation(pool: &PumpSwapPool, mint: &Pubkey) -> bool {
    pool.index == CANONICAL_POOL_INDEX && pool.base_mint == *mint
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bonding_curve::BondingCurve;
    use crate::pumpswap::PumpSwapPool;

    const BONDING_CURVE_FIXTURE: &[u8] =
        include_bytes!("../tests/fixtures/bonding_curve.bin");
    const POOL_FIXTURE: &[u8] = include_bytes!("../tests/fixtures/pumpswap_pool.bin");

    #[test]
    fn real_fixture_pool_is_canonical_for_its_mint() {
        let pool = PumpSwapPool::unpack(POOL_FIXTURE).unwrap();
        // The captured pool is the index-0 pool for its own base-mint token.
        assert!(is_canonical_graduation(&pool, &pool.base_mint));
        // The graduated token's bonding curve is complete.
        let bc = BondingCurve::unpack(BONDING_CURVE_FIXTURE).unwrap();
        assert!(bc.complete);
    }

    #[test]
    fn wrong_mint_is_not_canonical() {
        let pool = PumpSwapPool::unpack(POOL_FIXTURE).unwrap();
        let other = solana_sdk::pubkey::Pubkey::new_unique();
        assert!(!is_canonical_graduation(&pool, &other));
    }
}
