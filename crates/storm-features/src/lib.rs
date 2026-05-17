//! Survival-prediction feature extraction for graduated pump.fun tokens.
//!
//! Given a graduated token mint and its PumpSwap pool, [`extract_features`]
//! fetches the needed on-chain data via Solana RPC and computes a Lean-v1
//! [`FeatureVector`].
//!
//! The crate is split in two layers:
//!
//! * **Pure compute** — [`contract`], [`curve`], [`liquidity`], [`holders`],
//!   [`deployer`]: each takes already-fetched plain-data inputs and returns
//!   feature values. No network, no `solana-client` types — unit-tested
//!   against synthetic data.
//! * **RPC orchestration** — [`fetch`]: the only module that touches the
//!   network. It fetches accounts / the holder list / one signature page and
//!   feeds the pure functions.

pub mod contract;
pub mod curve;
pub mod deployer;
pub mod fetch;
pub mod holders;
pub mod liquidity;

use solana_sdk::pubkey::Pubkey;
use storm_core::Result;
use storm_solana::RpcContext;

pub use contract::ContractFlags;
pub use curve::CurveSnapshot;
pub use deployer::DeployerSignals;
pub use holders::HolderFeatures;
pub use liquidity::LiquidityFeatures;

/// The Lean-v1 survival-prediction feature vector for one graduated token.
#[derive(Debug, Clone)]
pub struct FeatureVector {
    /// The graduated token mint these features describe.
    pub mint: Pubkey,
    /// Liquidity feature group (PumpSwap pool reserves, LP burn).
    pub liquidity: LiquidityFeatures,
    /// Contract-flag feature group (mint / freeze authority presence).
    pub contract: ContractFlags,
    /// Holder-distribution feature group (top-N concentration, dev bag).
    pub holders: HolderFeatures,
    /// Bonding-curve snapshot feature group.
    pub curve: CurveSnapshot,
    /// Deployer-signal feature group (bounded signature page).
    pub deployer: DeployerSignals,
}

/// Assemble a [`FeatureVector`] from the five already-computed group structs.
/// Pure — no I/O; unit-testable.
fn assemble_feature_vector(
    mint: Pubkey,
    liquidity: LiquidityFeatures,
    contract: ContractFlags,
    holders: HolderFeatures,
    curve: CurveSnapshot,
    deployer: DeployerSignals,
) -> FeatureVector {
    FeatureVector {
        mint,
        liquidity,
        contract,
        holders,
        curve,
        deployer,
    }
}

/// Extract the Lean-v1 [`FeatureVector`] for a graduated pump.fun token.
///
/// * `rpc` — a configured [`RpcContext`].
/// * `mint` — the graduated token mint.
/// * `pool` — the token's canonical PumpSwap pool address (held by the caller
///   from graduation detection — see `storm-pumpfun`).
/// * `now_unix` — the reference "now" timestamp (Unix seconds) for age-based
///   features. The caller passes the snapshot instant.
///
/// Issues a bounded set of RPC calls (mint, pool, two pool reserve accounts,
/// bonding curve, top-20 holders, creator token account, one signature page)
/// and computes every feature group. Network errors surface as
/// [`storm_core::StormError::Rpc`]; malformed accounts as `StormError::Parse`.
pub async fn extract_features(
    rpc: &RpcContext,
    mint: &Pubkey,
    pool: &Pubkey,
    now_unix: i64,
) -> Result<FeatureVector> {
    // TODO(v2): the independent leading fetches (mint, bonding curve, pool,
    // top holders) could run concurrently via tokio::try_join! to cut latency.

    // --- contract flags + bonding curve --------------------------------
    let mint_info = fetch::fetch_mint(rpc, mint).await?;
    let contract = contract::contract_flags(&mint_info);

    let bonding_curve = fetch::fetch_bonding_curve(rpc, mint).await?;
    let curve = curve::curve_snapshot(&bonding_curve);
    let creator = bonding_curve.creator;

    // --- liquidity: pool record + its two reserve token accounts -------
    let pool_record = fetch::fetch_pool(rpc, pool).await?;
    let base_reserve =
        fetch::fetch_token_account_amount(rpc, &pool_record.pool_base_token_account).await?;
    let quote_reserve =
        fetch::fetch_token_account_amount(rpc, &pool_record.pool_quote_token_account).await?;
    let liquidity = liquidity::liquidity_features(&liquidity::PoolReserves {
        base_reserve,
        quote_reserve,
        lp_supply: pool_record.lp_supply,
        token_total_supply: mint_info.supply,
    });

    // --- holder distribution -------------------------------------------
    let top_holders = fetch::fetch_top_holders(rpc, mint).await?;
    let creator_ata = spl_associated_token_account_address(&creator, mint);
    let creator_balance = fetch::fetch_token_account_amount(rpc, &creator_ata).await?;
    let holders = holders::holder_features(&top_holders, mint_info.supply, creator_balance);

    // --- deployer signal -----------------------------------------------
    let page = fetch::fetch_signature_page(rpc, &creator).await?;
    let deployer = deployer::deployer_signals(&page, now_unix);

    Ok(assemble_feature_vector(
        *mint, liquidity, contract, holders, curve, deployer,
    ))
}

/// Derive the associated-token-account address holding `mint` for `owner`.
///
/// The ATA is a PDA of the Associated Token Account program; this matches
/// `spl_associated_token_account::get_associated_token_address` without
/// pulling in that crate. The seeds are `[owner, token_program, mint]`.
fn spl_associated_token_account_address(owner: &Pubkey, mint: &Pubkey) -> Pubkey {
    // Associated Token Account program ID (mainnet + devnet).
    const ATA_PROGRAM_ID: Pubkey =
        solana_sdk::pubkey!("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");
    Pubkey::find_program_address(
        &[owner.as_ref(), spl_token::id().as_ref(), mint.as_ref()],
        &ATA_PROGRAM_ID,
    )
    .0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    fn sample_liquidity() -> LiquidityFeatures {
        liquidity::liquidity_features(&liquidity::PoolReserves {
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_supply: 0,
            token_total_supply: 1_000_000_000_000_000,
        })
    }

    fn sample_contract() -> ContractFlags {
        ContractFlags {
            mint_authority_present: false,
            freeze_authority_present: false,
        }
    }

    fn sample_holders() -> HolderFeatures {
        holders::holder_features(&[], 1_000_000_000_000_000, 0)
    }

    fn sample_curve() -> CurveSnapshot {
        CurveSnapshot {
            graduated: true,
            real_sol_reserves: 85_000_000_000,
            real_token_reserves: 0,
            token_total_supply: 1_000_000_000_000_000,
        }
    }

    fn sample_deployer() -> DeployerSignals {
        deployer::deployer_signals(
            &deployer::SignaturePage {
                signature_count: 7,
                oldest_block_time: Some(1_000),
            },
            10_000,
        )
    }

    #[test]
    fn assemble_carries_every_group_and_the_mint() {
        let mint = Pubkey::new_unique();
        let fv = assemble_feature_vector(
            mint,
            sample_liquidity(),
            sample_contract(),
            sample_holders(),
            sample_curve(),
            sample_deployer(),
        );
        assert_eq!(fv.mint, mint);
        assert!(fv.liquidity.lp_burned);
        assert!(!fv.contract.mint_authority_present);
        assert_eq!(fv.holders.visible_holder_count, 0);
        assert!(fv.curve.graduated);
        assert_eq!(fv.deployer.capped_signature_count, 7);
    }

    #[test]
    fn ata_derivation_is_deterministic_and_owner_specific() {
        let usdc = Pubkey::from_str("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").unwrap();
        let owner_a = Pubkey::new_unique();
        let owner_b = Pubkey::new_unique();

        // Same (owner, mint) → same ATA every time.
        assert_eq!(
            spl_associated_token_account_address(&owner_a, &usdc),
            spl_associated_token_account_address(&owner_a, &usdc),
        );
        // Different owners → different ATAs.
        assert_ne!(
            spl_associated_token_account_address(&owner_a, &usdc),
            spl_associated_token_account_address(&owner_b, &usdc),
        );
        // The same owner with a different mint also yields a different ATA.
        let other_mint = Pubkey::new_unique();
        assert_ne!(
            spl_associated_token_account_address(&owner_a, &usdc),
            spl_associated_token_account_address(&owner_a, &other_mint),
        );
    }
}
