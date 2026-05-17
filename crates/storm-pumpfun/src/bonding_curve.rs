use solana_sdk::pubkey::Pubkey;
use storm_core::{Result, StormError};

use crate::{read_pubkey, read_u64, PUMPFUN_PROGRAM_ID};

/// Derive the bonding-curve account address for a token mint.
pub fn bonding_curve_pda(mint: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(&[b"bonding-curve", mint.as_ref()], &PUMPFUN_PROGRAM_ID).0
}

/// Parsed pump.fun bonding-curve account.
#[derive(Debug, Clone)]
pub struct BondingCurve {
    pub virtual_token_reserves: u64,
    pub virtual_sol_reserves: u64,
    pub real_token_reserves: u64,
    pub real_sol_reserves: u64,
    pub token_total_supply: u64,
    /// True once the curve has filled and the token has graduated.
    pub complete: bool,
    pub creator: Pubkey,
}

impl BondingCurve {
    /// Minimum meaningful length: 8-byte discriminator + 5×u64 + bool + Pubkey.
    /// The on-chain account is larger (~150 bytes) with trailing zero padding.
    pub const MIN_LEN: usize = 8 + 40 + 1 + 32;

    /// Parse a bonding-curve account. Trailing padding bytes are ignored.
    pub fn unpack(data: &[u8]) -> Result<Self> {
        if data.len() < Self::MIN_LEN {
            return Err(StormError::Parse(format!(
                "bonding curve: expected >= {} bytes, got {}",
                Self::MIN_LEN,
                data.len()
            )));
        }
        Ok(Self {
            virtual_token_reserves: read_u64(data, 8),
            virtual_sol_reserves: read_u64(data, 16),
            real_token_reserves: read_u64(data, 24),
            real_sol_reserves: read_u64(data, 32),
            token_total_supply: read_u64(data, 40),
            complete: data[48] != 0,
            creator: read_pubkey(data, 49),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    // From tests/fixtures/NOTES.md (token: Pumpfun Pepe / PFP).
    const FIXTURE_MINT: &str = "5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump";
    const FIXTURE_BONDING_CURVE: &str = "HLtp5EM2QRJZZXgSJqtYQ84tP8CDiziVHvFDGrEwW2wS";

    const BONDING_CURVE_FIXTURE: &[u8] = include_bytes!("../tests/fixtures/bonding_curve.bin");

    #[test]
    fn pda_matches_real_bonding_curve() {
        let mint = Pubkey::from_str(FIXTURE_MINT).unwrap();
        let expected = Pubkey::from_str(FIXTURE_BONDING_CURVE).unwrap();
        assert_eq!(bonding_curve_pda(&mint), expected);
    }

    #[test]
    fn unpacks_real_bonding_curve() {
        let bc = BondingCurve::unpack(BONDING_CURVE_FIXTURE).unwrap();
        assert!(bc.complete);
        assert_eq!(bc.token_total_supply, 1_000_000_000_000_000);
        assert_ne!(bc.creator, Pubkey::default());
    }

    #[test]
    fn unpack_rejects_short_data() {
        assert!(BondingCurve::unpack(&[0u8; 40]).is_err());
    }
}
