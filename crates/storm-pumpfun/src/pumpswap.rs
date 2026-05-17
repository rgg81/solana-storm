use solana_sdk::pubkey::Pubkey;
use storm_core::{Result, StormError};

use crate::{read_pubkey, read_u16, read_u64};

/// Parsed PumpSwap `Pool` account.
#[derive(Debug, Clone)]
pub struct PumpSwapPool {
    pub pool_bump: u8,
    /// 0 for the canonical pool created by a bonding-curve graduation.
    pub index: u16,
    /// The wallet that created the pool.
    pub creator: Pubkey,
    pub base_mint: Pubkey,
    pub quote_mint: Pubkey,
    pub lp_mint: Pubkey,
    pub pool_base_token_account: Pubkey,
    pub pool_quote_token_account: Pubkey,
    pub lp_supply: u64,
    /// The original token creator (matches the bonding curve's `creator` field).
    pub coin_creator: Pubkey,
    pub is_mayhem_mode: bool,
}

impl PumpSwapPool {
    /// 8-byte discriminator + u8 + u16 + 6×Pubkey + u64 + Pubkey + bool.
    /// The on-chain account is larger (~301 bytes) with trailing zero padding.
    pub const MIN_LEN: usize = 8 + 1 + 2 + (6 * 32) + 8 + 32 + 1;

    /// Parse a PumpSwap pool account. Trailing padding bytes are ignored.
    pub fn unpack(data: &[u8]) -> Result<Self> {
        if data.len() < Self::MIN_LEN {
            return Err(StormError::Parse(format!(
                "pumpswap pool: expected >= {} bytes, got {}",
                Self::MIN_LEN,
                data.len()
            )));
        }
        Ok(Self {
            pool_bump: data[8],
            index: read_u16(data, 9),
            creator: read_pubkey(data, 11),
            base_mint: read_pubkey(data, 43),
            quote_mint: read_pubkey(data, 75),
            lp_mint: read_pubkey(data, 107),
            pool_base_token_account: read_pubkey(data, 139),
            pool_quote_token_account: read_pubkey(data, 171),
            lp_supply: read_u64(data, 203),
            coin_creator: read_pubkey(data, 211),
            is_mayhem_mode: data[243] != 0,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const POOL_FIXTURE: &[u8] = include_bytes!("../tests/fixtures/pumpswap_pool.bin");

    #[test]
    fn unpacks_real_pool() {
        let pool = PumpSwapPool::unpack(POOL_FIXTURE).unwrap();
        // A canonical graduation pool has index 0.
        assert_eq!(pool.index, 0);
        // The two reserve token accounts are real pubkeys.
        assert_ne!(pool.pool_base_token_account, Pubkey::default());
        assert_ne!(pool.pool_quote_token_account, Pubkey::default());
    }

    #[test]
    fn unpack_rejects_short_data() {
        assert!(PumpSwapPool::unpack(&[0u8; 50]).is_err());
    }
}
