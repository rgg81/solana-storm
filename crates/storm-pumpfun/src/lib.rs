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

// ---- little-endian byte readers (crate-internal) --------------------------
// `#[allow(dead_code)]` is temporary: Tasks 4-5 consume these helpers. It keeps
// the CI `clippy -D warnings` gate green on the intermediate commits; the
// attributes are removed in Task 7 once every helper is in use.

#[allow(dead_code)]
pub(crate) fn read_u16(data: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes(data[offset..offset + 2].try_into().unwrap())
}

#[allow(dead_code)]
pub(crate) fn read_u64(data: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(data[offset..offset + 8].try_into().unwrap())
}

#[allow(dead_code)]
pub(crate) fn read_pubkey(data: &[u8], offset: usize) -> Pubkey {
    Pubkey::try_from(&data[offset..offset + 32]).unwrap()
}
