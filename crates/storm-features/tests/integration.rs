//! Live-RPC end-to-end test for `extract_features`.
//!
//! `#[ignore]`-d: it requires network and is never run by CI. Run it manually:
//!
//! ```text
//! set -a && . ./.env && set +a
//! export STORM_TEST_POOL=<pumpswap pool address for the test mint>
//! cargo test -p storm-features --test integration -- --ignored --nocapture
//! ```
//!
//! `SOLANA_RPC_URL` comes from `.env`. The test mint is the graduated token
//! "Pumpfun Pepe" (`5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump`).

use std::str::FromStr;
use std::time::{SystemTime, UNIX_EPOCH};

use solana_sdk::pubkey::Pubkey;
use storm_core::SolanaConfig;
use storm_features::extract_features;
use storm_solana::RpcContext;

/// Graduated pump.fun token used for the manual integration check.
const TEST_MINT: &str = "5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump";

#[tokio::test]
#[ignore = "hits live Solana RPC; run manually with SOLANA_RPC_URL + STORM_TEST_POOL set"]
async fn extract_features_against_a_real_graduated_token() {
    let rpc_url =
        std::env::var("SOLANA_RPC_URL").expect("set SOLANA_RPC_URL (see .env) to run this test");
    let pool_str = std::env::var("STORM_TEST_POOL")
        .expect("set STORM_TEST_POOL to the test mint's PumpSwap pool address");

    let cfg = SolanaConfig {
        rpc_url,
        ws_url: String::new(),
        commitment: "confirmed".to_string(),
    };
    let rpc = RpcContext::from_config(&cfg);

    let mint = Pubkey::from_str(TEST_MINT).unwrap();
    let pool = Pubkey::from_str(&pool_str).expect("STORM_TEST_POOL is not a valid pubkey");
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;

    let fv = extract_features(&rpc, &mint, &pool, now)
        .await
        .expect("feature extraction failed");

    // The token graduated, so its bonding curve is complete.
    assert!(fv.curve.graduated, "test token should be graduated");
    // The mint and the feature vector agree.
    assert_eq!(fv.mint, mint);
    // The pool holds a non-zero base reserve (a live graduated pool has tokens).
    assert!(
        fv.liquidity.base_reserve > 0,
        "pool base reserve should be > 0"
    );
    // getTokenLargestAccounts returns at most 20 holders.
    assert!(fv.holders.visible_holder_count <= 20);
    // Concentration fractions are well-formed.
    assert!((0.0..=1.0).contains(&fv.holders.top20_concentration));
    // The deployer wallet has at least one signature (it deployed the token).
    assert!(fv.deployer.capped_signature_count > 0);

    println!("FeatureVector for {TEST_MINT}:\n{fv:#?}");
}
