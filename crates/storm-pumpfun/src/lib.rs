//! pump.fun bonding-curve and PumpSwap account parsing + graduation detection.
//!
//! A pure parsing/derivation library — no network I/O. Callers fetch raw
//! account bytes (via `storm-solana`) and hand them here.

use solana_sdk::pubkey::Pubkey;

/// pump.fun bonding-curve program.
pub const PUMPFUN_PROGRAM_ID: Pubkey =
    solana_sdk::pubkey!("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");

/// PumpSwap AMM program — graduation destination since ~March 2025.
pub const PUMPSWAP_PROGRAM_ID: Pubkey =
    solana_sdk::pubkey!("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");

pub mod bonding_curve;
pub mod graduation;
pub mod pumpswap;

pub use bonding_curve::{bonding_curve_pda, BondingCurve};
pub use graduation::{is_canonical_graduation, CANONICAL_POOL_INDEX};
pub use pumpswap::PumpSwapPool;

// ---- little-endian byte readers (crate-internal) --------------------------

pub(crate) fn read_u16(data: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes(data[offset..offset + 2].try_into().unwrap())
}

pub(crate) fn read_u64(data: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(data[offset..offset + 8].try_into().unwrap())
}

pub(crate) fn read_pubkey(data: &[u8], offset: usize) -> Pubkey {
    Pubkey::try_from(&data[offset..offset + 32]).unwrap()
}
