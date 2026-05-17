//! Graduation discovery — an incremental `getProgramAccountsV2` scan.
//!
//! Each cycle scans only the PumpSwap pools that changed since the previous
//! cycle's slot (`changedSinceSlot`), paginating until the cursor is exhausted.
//! The pure `graduation_from_pool_account` helper turns a candidate pool
//! account into a confirmed graduation; it is unit-tested here. The live scan
//! is exercised by the `#[ignore]`-d integration test.

use solana_sdk::pubkey::Pubkey;
use storm_core::Result;
use storm_pumpfun::{bonding_curve_pda, PumpSwapPool, PUMPSWAP_PROGRAM_ID};
use storm_solana::{fetch_program_accounts_v2_page, RpcContext};

/// On-chain byte length of a PumpSwap `Pool` account: 244 defined-field bytes
/// plus 57 trailing reserved bytes. Verified against the captured fixture in
/// `crates/storm-pumpfun/tests/fixtures/NOTES.md`.
const PUMPSWAP_POOL_ACCOUNT_LEN: u64 = 301;

/// Max accounts per `getProgramAccountsV2` page (the V2 maximum).
const GPA_V2_PAGE_LIMIT: u64 = 10_000;

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
/// `storm-pumpfun`'s `tests/fixtures/NOTES.md`).
///
/// Returns `Ok(None)` for a parseable pool that is *not* a canonical graduation
/// and `Err` only when the bytes are too short / malformed for `unpack`.
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

/// The `getProgramAccountsV2` filter array that narrows the scan to canonical
/// (index-0) PumpSwap `Pool` accounts: a `dataSize` filter on the on-chain
/// account length plus a `memcmp` of `[0x00, 0x00]` (base58 `"11"`) on the
/// `index` field — a little-endian `u16` at offset 9 (8-byte discriminator +
/// 1-byte `pool_bump`).
fn pumpswap_v2_filters() -> serde_json::Value {
    serde_json::json!([
        { "dataSize": PUMPSWAP_POOL_ACCOUNT_LEN },
        { "memcmp": { "offset": 9, "bytes": "11", "encoding": "base58" } }
    ])
}

/// Discover canonical pump.fun graduations by scanning the PumpSwap pools that
/// changed since `changed_since_slot`, paginating until the cursor is
/// exhausted. Every returned account is re-validated by
/// [`graduation_from_pool_account`] — the server filter is approximate, the
/// parser is authoritative. `changedSinceSlot` returns *changed* pools (new
/// ones and ones that merely traded), so the caller deduplicates against the
/// `graduations` table; this function only keeps the per-cycle set bounded.
pub async fn discover_graduations(
    rpc: &RpcContext,
    changed_since_slot: u64,
) -> Result<Vec<DiscoveredGraduation>> {
    let filters = pumpswap_v2_filters();
    let mut found = Vec::new();
    let mut cursor: Option<String> = None;
    loop {
        let page = fetch_program_accounts_v2_page(
            rpc,
            &PUMPSWAP_PROGRAM_ID,
            &filters,
            changed_since_slot,
            GPA_V2_PAGE_LIMIT,
            cursor.as_deref(),
        )
        .await?;
        for account in &page.accounts {
            // A pool that fails to parse is skipped, not fatal — the server
            // filter can return an account the strict parser rejects.
            match graduation_from_pool_account(account.pubkey, &account.data) {
                Ok(Some(grad)) => found.push(grad),
                Ok(None) => {}
                Err(e) => {
                    tracing::debug!(pubkey = %account.pubkey, error = %e, "skipping unparseable pool account")
                }
            }
        }
        match page.pagination_key {
            Some(key) => cursor = Some(key),
            None => break,
        }
    }
    Ok(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    // The canonical PumpSwap pool fixture captured by storm-pumpfun (sub-plan 1).
    const POOL_FIXTURE: &[u8] =
        include_bytes!("../../../crates/storm-pumpfun/tests/fixtures/pumpswap_pool.bin");

    #[test]
    fn real_fixture_is_recognised_as_a_graduation() {
        let pool_addr = Pubkey::new_unique();
        let grad = graduation_from_pool_account(pool_addr, POOL_FIXTURE)
            .unwrap()
            .expect("the fixture is a canonical graduation pool");
        assert_eq!(grad.pool_address, pool_addr);
        assert_eq!(
            grad.mint,
            Pubkey::from_str_const("5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump"),
        );
        assert_eq!(grad.bonding_curve, bonding_curve_pda(&grad.mint));
    }

    #[test]
    fn short_data_is_a_parse_error() {
        match graduation_from_pool_account(Pubkey::new_unique(), &[0u8; 40]) {
            Err(storm_core::StormError::Parse(_)) => {}
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn pumpswap_filters_pin_size_and_index() {
        let filters = pumpswap_v2_filters();
        let arr = filters.as_array().expect("filters is an array");
        assert_eq!(arr.len(), 2);
        // First filter pins the on-chain account size (301 bytes).
        assert_eq!(arr[0]["dataSize"], 301);
        // Second filter pins index == 0 via a base58 memcmp at offset 9.
        assert_eq!(arr[1]["memcmp"]["offset"], 9);
        assert_eq!(arr[1]["memcmp"]["bytes"], "11");
    }
}
