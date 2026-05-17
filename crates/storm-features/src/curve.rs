//! Pure bonding-curve snapshot feature computation.

use storm_pumpfun::BondingCurve;

/// Bonding-curve snapshot — the Lean-v1 "bonding-curve" feature group.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CurveSnapshot {
    /// True once the curve has filled and the token has graduated.
    pub graduated: bool,
    /// SOL reserves still recorded on the bonding-curve account (lamports).
    pub real_sol_reserves: u64,
    /// Token reserves still recorded on the bonding-curve account (raw units).
    pub real_token_reserves: u64,
    /// Total token supply minted by the curve (raw units).
    pub token_total_supply: u64,
}

/// Derive the bonding-curve snapshot features from a fetched `BondingCurve`.
pub fn curve_snapshot(bc: &BondingCurve) -> CurveSnapshot {
    CurveSnapshot {
        graduated: bc.complete,
        real_sol_reserves: bc.real_sol_reserves,
        real_token_reserves: bc.real_token_reserves,
        token_total_supply: bc.token_total_supply,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::pubkey::Pubkey;

    fn curve(complete: bool) -> BondingCurve {
        BondingCurve {
            virtual_token_reserves: 1_073_000_000_000_000,
            virtual_sol_reserves: 30_000_000_000,
            real_token_reserves: 793_100_000_000_000,
            real_sol_reserves: 85_000_000_000,
            token_total_supply: 1_000_000_000_000_000,
            complete,
            creator: Pubkey::new_unique(),
        }
    }

    #[test]
    fn complete_curve_is_graduated() {
        let snap = curve_snapshot(&curve(true));
        assert!(snap.graduated);
    }

    #[test]
    fn incomplete_curve_is_not_graduated() {
        let snap = curve_snapshot(&curve(false));
        assert!(!snap.graduated);
    }

    #[test]
    fn snapshot_copies_reserves_and_supply() {
        let snap = curve_snapshot(&curve(true));
        assert_eq!(snap.real_sol_reserves, 85_000_000_000);
        assert_eq!(snap.real_token_reserves, 793_100_000_000_000);
        assert_eq!(snap.token_total_supply, 1_000_000_000_000_000);
    }
}
