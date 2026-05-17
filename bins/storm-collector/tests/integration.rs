//! Live-RPC end-to-end check for graduation discovery.
//!
//! `#[ignore]`-d: it requires network and is never run by CI. Run it manually:
//!
//! ```text
//! set -a && . ./.env && set +a
//! cargo test -p storm-collector --test integration -- --ignored --nocapture
//! ```
//!
//! `SOLANA_RPC_URL` comes from `.env`. The test issues one `getProgramAccounts`
//! call against the PumpSwap program — the same call the daemon's discover
//! phase makes — and asserts the result parses into canonical graduations.

use solana_client::rpc_config::{RpcAccountInfoConfig, RpcProgramAccountsConfig};
use solana_client::rpc_filter::{Memcmp, RpcFilterType};
use solana_sdk::pubkey::Pubkey;
use storm_core::SolanaConfig;
use storm_pumpfun::{PumpSwapPool, PUMPSWAP_PROGRAM_ID};
use storm_solana::RpcContext;

/// On-chain PumpSwap `Pool` account length (see storm-pumpfun NOTES.md) — the
/// value the `DataSize` filter needs; `PumpSwapPool::MIN_LEN` (244) is smaller.
const PUMPSWAP_POOL_ACCOUNT_LEN: u64 = 301;

/// Wrapped SOL — the quote mint of every pump.fun graduation pool.
const WRAPPED_SOL_MINT: Pubkey = solana_sdk::pubkey!("So11111111111111111111111111111111111111112");

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

    // The same filtered query the daemon's discover phase issues: index-0
    // PumpSwap Pool accounts of the on-chain account size.
    let config = RpcProgramAccountsConfig {
        filters: Some(vec![
            RpcFilterType::DataSize(PUMPSWAP_POOL_ACCOUNT_LEN),
            RpcFilterType::Memcmp(Memcmp::new_raw_bytes(9, vec![0, 0])),
        ]),
        account_config: RpcAccountInfoConfig::default(),
        with_context: None,
        sort_results: None,
    };

    let accounts = rpc
        .rpc()
        .get_program_accounts_with_config(&PUMPSWAP_PROGRAM_ID, config)
        .await
        .expect("getProgramAccounts call failed");

    // The PumpSwap program has many graduated pools; the filtered set is non-empty.
    assert!(
        !accounts.is_empty(),
        "expected at least one index-0 PumpSwap pool"
    );

    // Every returned account must parse; a clear majority must be a canonical
    // graduation — index 0 with wSOL as the quote mint. The server filter is
    // approximate, so we do not demand 100%, but a real result set is
    // overwhelmingly genuine.
    let mut canonical = 0usize;
    for (address, account) in &accounts {
        let pool = PumpSwapPool::unpack(&account.data)
            .unwrap_or_else(|e| panic!("pool {address} failed to parse: {e}"));
        if pool.index == 0 && pool.quote_mint == WRAPPED_SOL_MINT {
            canonical += 1;
        }
    }
    assert!(
        canonical * 2 >= accounts.len(),
        "at least half of the {} filtered pools should be canonical graduations, got {canonical}",
        accounts.len(),
    );

    println!(
        "discovered {} index-0 pools, {canonical} confirmed canonical graduations",
        accounts.len()
    );
}
