//! One collection cycle: discover graduations, snapshot due ones, record
//! mature outcomes. Resilient — a per-graduation failure is logged and skipped.

use storm_core::Result;
use storm_features::{extract_features, FeatureVector};
use storm_solana::RpcContext;
use storm_store::{
    FeatureSnapshotRow, GraduationRow, GraduationStatus, OutcomeRow, Store, StoredGraduation,
};

use crate::classify::classify_outcome;
use crate::config::CollectorConfig;
use crate::discover::discover_graduations;
use crate::schedule::{is_outcome_due, is_snapshot_due};

/// Flatten a [`FeatureVector`] into a [`FeatureSnapshotRow`] for persistence.
/// Pure — no I/O; unit-tested.
pub fn flatten_feature_vector(
    fv: &FeatureVector,
    graduation_id: i64,
    snapshot_at: i64,
) -> FeatureSnapshotRow {
    FeatureSnapshotRow {
        graduation_id,
        snapshot_at,
        base_reserve: fv.liquidity.base_reserve,
        quote_reserve: fv.liquidity.quote_reserve,
        lp_burned: fv.liquidity.lp_burned,
        pool_supply_fraction: fv.liquidity.pool_supply_fraction,
        mint_authority_present: fv.contract.mint_authority_present,
        freeze_authority_present: fv.contract.freeze_authority_present,
        visible_holder_count: fv.holders.visible_holder_count as i64,
        top10_concentration: fv.holders.top10_concentration,
        top20_concentration: fv.holders.top20_concentration,
        creator_bag_fraction: fv.holders.creator_bag_fraction,
        curve_graduated: fv.curve.graduated,
        curve_real_sol_reserves: fv.curve.real_sol_reserves,
        curve_real_token_reserves: fv.curve.real_token_reserves,
        curve_token_total_supply: fv.curve.token_total_supply,
        capped_signature_count: fv.deployer.capped_signature_count as i64,
        signature_count_capped: fv.deployer.count_capped,
        oldest_signature_age_secs: fv.deployer.oldest_signature_age_secs,
    }
}

/// Run one full collection cycle against `rpc` / `store`, using `now`
/// (Unix seconds) as the reference clock for the window decisions.
pub async fn run_cycle(
    rpc: &RpcContext,
    store: &Store,
    cfg: &CollectorConfig,
    now: i64,
) -> Result<()> {
    discover_phase(rpc, store, now).await?;
    snapshot_phase(rpc, store, cfg, now).await?;
    outcome_phase(rpc, store, cfg, now).await?;
    store
        .set_collector_state("last_cycle_at", &now.to_string())
        .await?;
    Ok(())
}

/// Phase 1 — discover graduations and insert any not yet tracked.
async fn discover_phase(rpc: &RpcContext, store: &Store, now: i64) -> Result<()> {
    let slot = rpc
        .rpc()
        .get_slot()
        .await
        .map_err(|e| storm_core::StormError::Rpc(format!("get_slot: {e}")))?;
    let discovered = discover_graduations(rpc).await?;
    let mut new_count = 0usize;
    for grad in discovered {
        let row = GraduationRow {
            mint: grad.mint,
            pool_address: grad.pool_address,
            bonding_curve_address: grad.bonding_curve,
            graduation_slot: slot,
            detected_at: now,
            status: GraduationStatus::PendingSnapshot,
        };
        // insert_graduation is idempotent on `mint`: Some(id) = newly inserted,
        // None = already tracked.
        if store.insert_graduation(&row).await?.is_some() {
            new_count += 1;
        }
    }
    tracing::info!(new = new_count, "discover phase complete");
    Ok(())
}

/// Phase 2 — snapshot every pending graduation whose observation window elapsed.
async fn snapshot_phase(
    rpc: &RpcContext,
    store: &Store,
    cfg: &CollectorConfig,
    now: i64,
) -> Result<()> {
    let window = cfg.snapshot_window.as_secs() as i64;
    let pending = store
        .graduations_with_status(GraduationStatus::PendingSnapshot)
        .await?;
    for grad in pending {
        if !is_snapshot_due(grad.detected_at, window, now) {
            continue;
        }
        match snapshot_one(rpc, store, &grad, now).await {
            Ok(()) => tracing::info!(mint = %grad.mint, "feature snapshot recorded"),
            Err(e) => {
                tracing::warn!(mint = %grad.mint, error = %e, "snapshot failed; will retry next cycle")
            }
        }
    }
    Ok(())
}

/// Snapshot a single graduation: extract features, persist, advance status.
async fn snapshot_one(
    rpc: &RpcContext,
    store: &Store,
    grad: &StoredGraduation,
    now: i64,
) -> Result<()> {
    // Idempotency guard: if a snapshot already exists (a prior crash), just
    // advance the status and stop.
    if store.has_feature_snapshot(grad.id).await? {
        store
            .set_graduation_status(grad.id, GraduationStatus::SnapshotDone)
            .await?;
        return Ok(());
    }
    let fv = extract_features(rpc, &grad.mint, &grad.pool_address, now).await?;
    let row = flatten_feature_vector(&fv, grad.id, now);
    store.insert_feature_snapshot(&row).await?;
    store
        .set_graduation_status(grad.id, GraduationStatus::SnapshotDone)
        .await?;
    Ok(())
}

/// Phase 3 — record an outcome for every snapshot_done graduation whose outcome
/// window has matured.
async fn outcome_phase(
    rpc: &RpcContext,
    store: &Store,
    cfg: &CollectorConfig,
    now: i64,
) -> Result<()> {
    let window = cfg.outcome_window.as_secs() as i64;
    let due = store
        .graduations_with_status(GraduationStatus::SnapshotDone)
        .await?;
    for grad in due {
        if !is_outcome_due(grad.detected_at, window, now) {
            continue;
        }
        match outcome_one(rpc, store, cfg, &grad, now).await {
            Ok(()) => tracing::info!(mint = %grad.mint, "outcome recorded"),
            Err(e) => {
                tracing::warn!(mint = %grad.mint, error = %e, "outcome check failed; will retry next cycle")
            }
        }
    }
    Ok(())
}

/// Record a single graduation's outcome: read pool liquidity, classify, persist.
async fn outcome_one(
    rpc: &RpcContext,
    store: &Store,
    cfg: &CollectorConfig,
    grad: &StoredGraduation,
    now: i64,
) -> Result<()> {
    if store.has_outcome(grad.id).await? {
        store
            .set_graduation_status(grad.id, GraduationStatus::OutcomeDone)
            .await?;
        return Ok(());
    }
    // Re-extract features purely to read the pool's current reserves; only the
    // liquidity group is used. Re-using extract_features keeps the RPC plumbing
    // in one place.
    let fv = extract_features(rpc, &grad.mint, &grad.pool_address, now).await?;
    let verdict = classify_outcome(fv.liquidity.quote_reserve, cfg.survival_min_quote_lamports);
    let row = OutcomeRow {
        graduation_id: grad.id,
        outcome_at: now,
        survived: verdict.survived(),
        base_reserve: fv.liquidity.base_reserve,
        quote_reserve: fv.liquidity.quote_reserve,
    };
    store.insert_outcome(&row).await?;
    store
        .set_graduation_status(grad.id, GraduationStatus::OutcomeDone)
        .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::pubkey::Pubkey;
    use storm_features::{
        ContractFlags, CurveSnapshot, DeployerSignals, HolderFeatures, LiquidityFeatures,
    };

    /// A fully-populated synthetic FeatureVector for the flatten test.
    fn sample_feature_vector() -> FeatureVector {
        FeatureVector {
            mint: Pubkey::new_unique(),
            liquidity: LiquidityFeatures {
                base_reserve: 200_000_000_000_000,
                quote_reserve: 85_000_000_000,
                lp_burned: true,
                pool_supply_fraction: 0.2,
            },
            contract: ContractFlags {
                mint_authority_present: false,
                freeze_authority_present: true,
            },
            holders: HolderFeatures {
                visible_holder_count: 18,
                top10_concentration: 0.31,
                top20_concentration: 0.44,
                creator_bag_fraction: 0.05,
            },
            curve: CurveSnapshot {
                graduated: true,
                real_sol_reserves: 85_000_000_000,
                real_token_reserves: 0,
                token_total_supply: 1_000_000_000_000_000,
            },
            deployer: DeployerSignals {
                capped_signature_count: 7,
                count_capped: false,
                oldest_signature_age_secs: Some(123_456),
            },
        }
    }

    #[test]
    fn flatten_copies_every_group_field() {
        let fv = sample_feature_vector();
        let row = flatten_feature_vector(&fv, 99, 1_779_050_000);

        assert_eq!(row.graduation_id, 99);
        assert_eq!(row.snapshot_at, 1_779_050_000);
        // liquidity
        assert_eq!(row.base_reserve, 200_000_000_000_000);
        assert_eq!(row.quote_reserve, 85_000_000_000);
        assert!(row.lp_burned);
        assert!((row.pool_supply_fraction - 0.2).abs() < 1e-9);
        // contract flags
        assert!(!row.mint_authority_present);
        assert!(row.freeze_authority_present);
        // holders
        assert_eq!(row.visible_holder_count, 18);
        assert!((row.top20_concentration - 0.44).abs() < 1e-9);
        // curve
        assert!(row.curve_graduated);
        assert_eq!(row.curve_token_total_supply, 1_000_000_000_000_000);
        // deployer
        assert_eq!(row.capped_signature_count, 7);
        assert!(!row.signature_count_capped);
        assert_eq!(row.oldest_signature_age_secs, Some(123_456));
    }

    #[test]
    fn flatten_preserves_a_none_signature_age() {
        let mut fv = sample_feature_vector();
        fv.deployer.oldest_signature_age_secs = None;
        let row = flatten_feature_vector(&fv, 1, 0);
        assert_eq!(row.oldest_signature_age_secs, None);
    }
}
