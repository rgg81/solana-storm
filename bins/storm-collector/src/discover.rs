//! Graduation discovery — poll PumpSwap `getProgramAccounts` for index-0 pools.
//!
//! The pure `graduation_from_pool_account` helper (account bytes -> a confirmed
//! graduation) is unit-tested here. `discover_graduations` issues the single
//! RPC call and is exercised by the `#[ignore]`-d integration test.

use solana_client::rpc_config::{RpcAccountInfoConfig, RpcProgramAccountsConfig};
use solana_client::rpc_filter::{Memcmp, RpcFilterType};
use solana_sdk::pubkey::Pubkey;
use storm_core::{Result, StormError};
use storm_pumpfun::{bonding_curve_pda, PumpSwapPool, PUMPSWAP_PROGRAM_ID};
use storm_solana::RpcContext;

/// On-chain byte length of a PumpSwap `Pool` account: 244 defined-field bytes
/// plus 57 trailing reserved bytes. Verified against the captured fixture in
/// `crates/storm-pumpfun/tests/fixtures/NOTES.md`. This is the value the
/// `DataSize` filter needs — `PumpSwapPool::MIN_LEN` (244) is only the minimum
/// *parseable* length and would match zero accounts as a `DataSize` filter.
const PUMPSWAP_POOL_ACCOUNT_LEN: u64 = 301;

/// Wrapped SOL — the quote mint of every pump.fun graduation pool.
const WRAPPED_SOL_MINT: Pubkey = solana_sdk::pubkey!("So11111111111111111111111111111111111111112");

/// A graduation discovered on-chain — the data the collector needs to insert a
/// `graduations` row and later run feature extraction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscoveredGraduation {
    /// The graduated token mint (the pool's `base_mint`).
    pub mint: Pubkey,
    /// The canonical PumpSwap pool address.
    pub pool_address: Pubkey,
    /// The token's bonding-curve account — `bonding_curve_pda(mint)`.
    pub bonding_curve: Pubkey,
}

/// Parse a candidate PumpSwap pool account and, if it is a canonical
/// graduation, return the [`DiscoveredGraduation`].
///
/// A canonical pump.fun graduation pool is identified by `index == 0` **and**
/// `quote_mint == wrapped SOL`. NOTE: the pool's `creator` field is the
/// pool-creator EOA, *not* the token's bonding-curve PDA (documented in
/// `storm-pumpfun`'s `tests/fixtures/NOTES.md`), so `is_canonical_graduation`
/// cannot be used here with a derived PDA — that equality never holds.
///
/// Returns `Ok(None)` for a parseable pool that is *not* a canonical graduation
/// (wrong index, or quote mint is not wSOL) and `Err` only when the bytes are
/// too short / malformed for `PumpSwapPool::unpack`.
pub fn graduation_from_pool_account(
    pool_address: Pubkey,
    data: &[u8],
) -> Result<Option<DiscoveredGraduation>> {
    let pool = PumpSwapPool::unpack(data)?;
    if pool.index != 0 || pool.quote_mint != WRAPPED_SOL_MINT {
        return Ok(None);
    }
    let mint = pool.base_mint;
    Ok(Some(DiscoveredGraduation {
        mint,
        pool_address,
        bonding_curve: bonding_curve_pda(&mint),
    }))
}

/// The `getProgramAccounts` config that asks the RPC node for only canonical
/// (index-0) PumpSwap `Pool` accounts: a `DataSize` filter on the on-chain
/// account length plus a `Memcmp` of `[0x00, 0x00]` on the `index` field
/// (a little-endian `u16` at offset 9 — 8-byte discriminator + 1-byte `pool_bump`).
fn graduation_pool_filter() -> RpcProgramAccountsConfig {
    RpcProgramAccountsConfig {
        filters: Some(vec![
            RpcFilterType::DataSize(PUMPSWAP_POOL_ACCOUNT_LEN),
            RpcFilterType::Memcmp(Memcmp::new_raw_bytes(9, vec![0, 0])),
        ]),
        account_config: RpcAccountInfoConfig::default(),
        with_context: None,
        sort_results: None,
    }
}

/// Discover canonical pump.fun graduations by polling `getProgramAccounts` on
/// the PumpSwap program. One RPC call; every returned account is re-validated
/// by [`graduation_from_pool_account`] (the server filter is approximate).
pub async fn discover_graduations(rpc: &RpcContext) -> Result<Vec<DiscoveredGraduation>> {
    let accounts = rpc
        .rpc()
        .get_program_accounts_with_config(&PUMPSWAP_PROGRAM_ID, graduation_pool_filter())
        .await
        .map_err(|e| StormError::Rpc(format!("getProgramAccounts pumpswap: {e}")))?;

    let mut found = Vec::new();
    for (address, account) in accounts {
        // A pool that fails to parse is skipped, not fatal — the server filter
        // can in principle return an account the strict parser rejects.
        match graduation_from_pool_account(address, &account.data) {
            Ok(Some(grad)) => found.push(grad),
            Ok(None) => {}
            Err(e) => tracing::debug!(%address, error = %e, "skipping unparseable pool account"),
        }
    }
    Ok(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    // The canonical PumpSwap pool fixture captured by storm-pumpfun (sub-plan 1).
    // NOTES.md records it as a 301-byte index-0 pool with quote_mint = wSOL.
    const POOL_FIXTURE: &[u8] =
        include_bytes!("../../../crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin");

    #[test]
    fn real_fixture_is_recognised_as_a_graduation() {
        let pool_addr = Pubkey::new_unique();
        let grad = graduation_from_pool_account(pool_addr, POOL_FIXTURE)
            .unwrap()
            .expect("the fixture is a canonical graduation pool");
        assert_eq!(grad.pool_address, pool_addr);
        // The discovered mint is the pool's base mint; the fixture's base_mint
        // is the "Pumpfun Pepe" token (see storm-pumpfun NOTES.md).
        assert_eq!(
            grad.mint,
            Pubkey::from_str_const("5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump"),
        );
        // The bonding curve is the PDA of that mint.
        assert_eq!(grad.bonding_curve, bonding_curve_pda(&grad.mint));
    }

    #[test]
    fn short_data_is_a_parse_error() {
        match graduation_from_pool_account(Pubkey::new_unique(), &[0u8; 40]) {
            Err(StormError::Parse(_)) => {}
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn filter_pins_account_size_and_index() {
        let cfg = graduation_pool_filter();
        let filters = cfg.filters.expect("filters set");
        assert_eq!(filters.len(), 2);
        // First filter pins the on-chain account size (301 bytes, not MIN_LEN).
        match &filters[0] {
            RpcFilterType::DataSize(n) => assert_eq!(*n, 301),
            other => panic!("expected DataSize, got {other:?}"),
        }
        // Second filter is a memcmp at the index offset.
        match &filters[1] {
            RpcFilterType::Memcmp(_) => {}
            other => panic!("expected Memcmp, got {other:?}"),
        }
    }
}
