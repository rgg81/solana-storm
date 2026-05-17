//! Live-RPC end-to-end check for graduation discovery.
//!
//! `#[ignore]`-d: it requires network and is never run by CI. Run it manually:
//!
//! ```text
//! set -a && . ./.env && set +a
//! cargo test -p storm-collector --test integration -- --ignored --nocapture
//! ```
//!
//! `SOLANA_RPC_URL` comes from `.env`. The test issues one
//! `getProgramAccountsV2` page — the same call the daemon's discover phase
//! makes — for PumpSwap pools changed in roughly the last 30 minutes, and
//! asserts the result parses into canonical graduations.

use solana_sdk::pubkey::Pubkey;
use storm_core::SolanaConfig;
use storm_pumpfun::{PumpSwapPool, PUMPSWAP_PROGRAM_ID};
use storm_solana::{fetch_program_accounts_v2_page, RpcContext};

/// On-chain PumpSwap `Pool` account length (see storm-pumpfun NOTES.md).
const PUMPSWAP_POOL_ACCOUNT_LEN: u64 = 301;

/// Wrapped SOL — the quote mint of every pump.fun graduation pool.
const WRAPPED_SOL_MINT: Pubkey = solana_sdk::pubkey!("So11111111111111111111111111111111111111112");

/// Roughly 30 minutes of Solana slots (≈400 ms/slot).
const SLOTS_PER_30_MIN: u64 = 5_000;

#[tokio::test]
#[ignore = "hits live Solana RPC; run manually with SOLANA_RPC_URL set"]
async fn discovers_canonical_graduations_from_pumpswap() {
    let rpc_url =
        std::env::var("SOLANA_RPC_URL").expect("set SOLANA_RPC_URL (see .env) to run this test");
    let cfg = SolanaConfig {
        rpc_url,
        ws_url: String::new(),
        commitment: "confirmed".to_string(),
    };
    let rpc = RpcContext::from_config(&cfg);

    let current_slot = rpc.rpc().get_slot().await.expect("getSlot failed");
    let changed_since_slot = current_slot.saturating_sub(SLOTS_PER_30_MIN);

    // The same filtered query the daemon's discover phase issues: index-0
    // PumpSwap Pool accounts of the on-chain account size.
    let filters = serde_json::json!([
        { "dataSize": PUMPSWAP_POOL_ACCOUNT_LEN },
        { "memcmp": { "offset": 9, "bytes": "11", "encoding": "base58" } }
    ]);

    let page = fetch_program_accounts_v2_page(
        &rpc,
        &PUMPSWAP_PROGRAM_ID,
        &filters,
        changed_since_slot,
        10_000,
        None,
    )
    .await
    .expect("getProgramAccountsV2 call failed");

    // PumpSwap is busy; a ~30-minute window always has changed pools.
    assert!(
        !page.accounts.is_empty(),
        "expected at least one changed index-0 PumpSwap pool in the last ~30 min"
    );

    // Every returned account must parse; a clear majority must be a canonical
    // graduation — index 0 with wSOL as the quote mint.
    let mut canonical = 0usize;
    for account in &page.accounts {
        let pool = PumpSwapPool::unpack(&account.data)
            .unwrap_or_else(|e| panic!("pool {} failed to parse: {e}", account.pubkey));
        if pool.index == 0 && pool.quote_mint == WRAPPED_SOL_MINT {
            canonical += 1;
        }
    }
    assert!(
        canonical * 2 >= page.accounts.len(),
        "at least half of the {} changed pools should be canonical graduations, got {canonical}",
        page.accounts.len(),
    );

    println!(
        "page returned {} changed index-0 pools, {canonical} confirmed canonical; next cursor: {:?}",
        page.accounts.len(),
        page.pagination_key,
    );
}
