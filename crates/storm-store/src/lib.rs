use rust_decimal::Decimal;
use solana_sdk::pubkey::Pubkey;
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use sqlx::SqlitePool;
use std::str::FromStr;
use storm_core::{Result, StormError};

#[derive(Clone)]
pub struct Store {
    pool: SqlitePool,
}

#[derive(Debug, Clone, Copy)]
pub enum Dex {
    RaydiumAmmV4,
    OrcaWhirlpool,
}

impl Dex {
    fn as_str(&self) -> &'static str {
        match self {
            Dex::RaydiumAmmV4 => "raydium-amm-v4",
            Dex::OrcaWhirlpool => "orca-whirlpool",
        }
    }
}

#[derive(Debug, Clone)]
pub struct PoolRow {
    pub address: Pubkey,
    pub program_id: Pubkey,
    pub dex: Dex,
    pub token_a_mint: Pubkey,
    pub token_b_mint: Pubkey,
    pub token_a_decimals: u8,
    pub token_b_decimals: u8,
}

#[derive(Debug, Clone)]
pub struct PriceSnapshot {
    pub pool_address: Pubkey,
    pub spot_price: Decimal,
    pub reserve_a_raw: u64,
    pub reserve_b_raw: u64,
}

#[derive(Debug, Clone)]
pub struct LatestPrice {
    pub spot_price: Decimal,
    pub reserve_a_raw: u64,
    pub reserve_b_raw: u64,
    pub captured_at: i64,
}

/// Lifecycle status of a tracked graduation. Drives the collector's state
/// machine: a graduation moves PendingSnapshot -> SnapshotDone -> OutcomeDone.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GraduationStatus {
    /// Discovered; waiting for the T0+window feature snapshot.
    PendingSnapshot,
    /// Feature snapshot taken; waiting for the outcome window to mature.
    SnapshotDone,
    /// Outcome recorded; terminal state.
    OutcomeDone,
}

impl GraduationStatus {
    /// The string persisted in the `graduations.status` column.
    pub fn as_str(&self) -> &'static str {
        match self {
            GraduationStatus::PendingSnapshot => "pending_snapshot",
            GraduationStatus::SnapshotDone => "snapshot_done",
            GraduationStatus::OutcomeDone => "outcome_done",
        }
    }

    /// Parse a `graduations.status` string back into the enum.
    ///
    /// Named `parse_status`, not `from_str`: an inherent `from_str` trips
    /// clippy's `should_implement_trait` lint (a hard error under
    /// `-D warnings`), and implementing the real `std::str::FromStr` trait would
    /// force an `Err` type other than the crate `Result`.
    pub fn parse_status(s: &str) -> Result<Self> {
        match s {
            "pending_snapshot" => Ok(GraduationStatus::PendingSnapshot),
            "snapshot_done" => Ok(GraduationStatus::SnapshotDone),
            "outcome_done" => Ok(GraduationStatus::OutcomeDone),
            other => Err(StormError::Parse(format!(
                "unknown graduation status '{other}'"
            ))),
        }
    }
}

/// A discovered pump.fun graduation, as stored in the `graduations` table.
#[derive(Debug, Clone)]
pub struct GraduationRow {
    /// The graduated token mint — the unique idempotency key.
    pub mint: Pubkey,
    /// The token's canonical PumpSwap pool address.
    pub pool_address: Pubkey,
    /// The token's bonding-curve account address.
    pub bonding_curve_address: Pubkey,
    /// `getSlot` value observed when the graduation was detected.
    pub graduation_slot: u64,
    /// Unix seconds the collector first detected the graduation (its T0).
    pub detected_at: i64,
    /// Lifecycle status.
    pub status: GraduationStatus,
}

/// A `graduations` row read back from the store, including its row id.
#[derive(Debug, Clone)]
pub struct StoredGraduation {
    /// The `graduations.id` primary key.
    pub id: i64,
    pub mint: Pubkey,
    pub pool_address: Pubkey,
    pub bonding_curve_address: Pubkey,
    pub graduation_slot: u64,
    pub detected_at: i64,
    pub status: GraduationStatus,
}

/// A flattened `storm_features::FeatureVector` ready for the `feature_snapshots`
/// table. `storm-store` deliberately does not depend on `storm-features`; the
/// collector flattens the `FeatureVector` into this plain struct.
#[derive(Debug, Clone)]
pub struct FeatureSnapshotRow {
    /// FK to the `graduations` row this snapshot describes.
    pub graduation_id: i64,
    /// Unix seconds the snapshot was taken.
    pub snapshot_at: i64,
    // liquidity group
    pub base_reserve: u64,
    pub quote_reserve: u64,
    pub lp_burned: bool,
    pub pool_supply_fraction: f64,
    // contract-flags group
    pub mint_authority_present: bool,
    pub freeze_authority_present: bool,
    // holder-distribution group
    pub visible_holder_count: i64,
    pub top10_concentration: f64,
    pub top20_concentration: f64,
    pub creator_bag_fraction: f64,
    // bonding-curve-snapshot group
    pub curve_graduated: bool,
    pub curve_real_sol_reserves: u64,
    pub curve_real_token_reserves: u64,
    pub curve_token_total_supply: u64,
    // deployer-signal group
    pub capped_signature_count: i64,
    pub signature_count_capped: bool,
    /// `None` when the deployer's oldest signature age is unknown.
    pub oldest_signature_age_secs: Option<i64>,
}

/// The recorded outcome for a graduation, as stored in the `outcomes` table.
#[derive(Debug, Clone)]
pub struct OutcomeRow {
    /// FK to the `graduations` row this outcome describes.
    pub graduation_id: i64,
    /// Unix seconds the outcome was checked.
    pub outcome_at: i64,
    /// `true` = the token survived; `false` = it rugged / died.
    pub survived: bool,
    /// Pool base-token reserve (raw units) at the outcome check.
    pub base_reserve: u64,
    /// Pool quote-token (wrapped SOL) reserve (lamports) at the outcome check.
    pub quote_reserve: u64,
}

impl Store {
    /// Open (creating if necessary) a SQLite store at `database_url`.
    /// `database_url` examples: `sqlite::memory:`, `sqlite://./storm.db`,
    /// `sqlite:///abs/path/storm.db`.
    pub async fn open(database_url: &str) -> Result<Self> {
        let opts = SqliteConnectOptions::from_str(database_url)
            .map_err(|e| StormError::Parse(format!("invalid sqlite url '{database_url}': {e}")))?
            .create_if_missing(true);
        let pool = SqlitePoolOptions::new()
            .max_connections(4)
            .connect_with(opts)
            .await
            .map_err(|e| StormError::Rpc(format!("sqlite connect: {e}")))?;
        Ok(Self { pool })
    }

    pub async fn migrate(&self) -> Result<()> {
        sqlx::migrate!("./migrations")
            .run(&self.pool)
            .await
            .map_err(|e| StormError::Rpc(format!("sqlite migrate: {e}")))?;
        Ok(())
    }

    pub async fn upsert_pool(&self, row: &PoolRow) -> Result<()> {
        sqlx::query(
            "INSERT INTO pools \
             (address, program_id, dex, token_a_mint, token_b_mint, token_a_decimals, token_b_decimals) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7) \
             ON CONFLICT(address) DO UPDATE SET \
                 program_id       = excluded.program_id, \
                 dex              = excluded.dex, \
                 token_a_mint     = excluded.token_a_mint, \
                 token_b_mint     = excluded.token_b_mint, \
                 token_a_decimals = excluded.token_a_decimals, \
                 token_b_decimals = excluded.token_b_decimals",
        )
        .bind(row.address.to_string())
        .bind(row.program_id.to_string())
        .bind(row.dex.as_str())
        .bind(row.token_a_mint.to_string())
        .bind(row.token_b_mint.to_string())
        .bind(row.token_a_decimals as i64)
        .bind(row.token_b_decimals as i64)
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("upsert pool: {e}")))?;
        Ok(())
    }

    pub async fn insert_price_snapshot(&self, snap: &PriceSnapshot) -> Result<()> {
        sqlx::query(
            "INSERT INTO prices (pool_address, spot_price, reserve_a_raw, reserve_b_raw) \
             VALUES (?1, ?2, ?3, ?4)",
        )
        .bind(snap.pool_address.to_string())
        .bind(snap.spot_price.to_string())
        .bind(snap.reserve_a_raw.to_string())
        .bind(snap.reserve_b_raw.to_string())
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("insert price snapshot: {e}")))?;
        Ok(())
    }

    pub async fn latest_price(&self, pool_address: &Pubkey) -> Result<Option<LatestPrice>> {
        let row: Option<(String, String, String, i64)> = sqlx::query_as(
            "SELECT spot_price, reserve_a_raw, reserve_b_raw, captured_at \
             FROM prices WHERE pool_address = ?1 \
             ORDER BY captured_at DESC, id DESC LIMIT 1",
        )
        .bind(pool_address.to_string())
        .fetch_optional(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("latest_price: {e}")))?;
        Ok(row.map(|(price, ra, rb, ts)| LatestPrice {
            spot_price: price.parse().unwrap_or(Decimal::ZERO),
            reserve_a_raw: ra.parse().unwrap_or(0),
            reserve_b_raw: rb.parse().unwrap_or(0),
            captured_at: ts,
        }))
    }

    /// Insert a discovered graduation. Idempotent: if a row with the same
    /// `mint` already exists this is a no-op and returns `Ok(None)`. On a
    /// fresh insert it returns `Ok(Some(new_row_id))`.
    pub async fn insert_graduation(&self, grad: &GraduationRow) -> Result<Option<i64>> {
        let res = sqlx::query(
            "INSERT INTO graduations \
             (mint, pool_address, bonding_curve_address, graduation_slot, detected_at, status) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6) \
             ON CONFLICT(mint) DO NOTHING",
        )
        .bind(grad.mint.to_string())
        .bind(grad.pool_address.to_string())
        .bind(grad.bonding_curve_address.to_string())
        .bind(grad.graduation_slot as i64)
        .bind(grad.detected_at)
        .bind(grad.status.as_str())
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("insert graduation: {e}")))?;

        // rows_affected() is 0 when the ON CONFLICT clause suppressed the insert.
        if res.rows_affected() == 0 {
            Ok(None)
        } else {
            Ok(Some(res.last_insert_rowid()))
        }
    }

    /// All graduations currently in `status`, oldest-detected first — the
    /// collector's work queue for that lifecycle stage.
    pub async fn graduations_with_status(
        &self,
        status: GraduationStatus,
    ) -> Result<Vec<StoredGraduation>> {
        let rows: Vec<(i64, String, String, String, i64, i64, String)> = sqlx::query_as(
            "SELECT id, mint, pool_address, bonding_curve_address, graduation_slot, \
                    detected_at, status \
             FROM graduations WHERE status = ?1 ORDER BY detected_at ASC, id ASC",
        )
        .bind(status.as_str())
        .fetch_all(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("graduations_with_status: {e}")))?;

        rows.into_iter()
            .map(|(id, mint, pool, bc, slot, detected_at, st)| {
                Ok(StoredGraduation {
                    id,
                    mint: parse_pubkey(&mint, "graduation mint")?,
                    pool_address: parse_pubkey(&pool, "graduation pool_address")?,
                    bonding_curve_address: parse_pubkey(&bc, "graduation bonding_curve_address")?,
                    graduation_slot: slot as u64,
                    detected_at,
                    status: GraduationStatus::parse_status(&st)?,
                })
            })
            .collect()
    }

    /// Advance a graduation to a new lifecycle status.
    pub async fn set_graduation_status(
        &self,
        graduation_id: i64,
        status: GraduationStatus,
    ) -> Result<()> {
        sqlx::query("UPDATE graduations SET status = ?1 WHERE id = ?2")
            .bind(status.as_str())
            .bind(graduation_id)
            .execute(&self.pool)
            .await
            .map_err(|e| StormError::Rpc(format!("set_graduation_status: {e}")))?;
        Ok(())
    }

    /// Total number of rows in `graduations`.
    pub async fn graduation_count(&self) -> Result<i64> {
        let row: (i64,) = sqlx::query_as("SELECT COUNT(*) FROM graduations")
            .fetch_one(&self.pool)
            .await
            .map_err(|e| StormError::Rpc(format!("graduation_count: {e}")))?;
        Ok(row.0)
    }

    /// Persist a feature snapshot for a graduation. The caller is expected to
    /// have checked `has_feature_snapshot` first; the `graduation_id UNIQUE`
    /// constraint is the hard backstop against duplicates.
    pub async fn insert_feature_snapshot(&self, row: &FeatureSnapshotRow) -> Result<()> {
        sqlx::query(
            "INSERT INTO feature_snapshots \
             (graduation_id, snapshot_at, base_reserve, quote_reserve, lp_burned, \
              pool_supply_fraction, mint_authority_present, freeze_authority_present, \
              visible_holder_count, top10_concentration, top20_concentration, \
              creator_bag_fraction, curve_graduated, curve_real_sol_reserves, \
              curve_real_token_reserves, curve_token_total_supply, capped_signature_count, \
              signature_count_capped, oldest_signature_age_secs) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, \
                     ?16, ?17, ?18, ?19)",
        )
        .bind(row.graduation_id)
        .bind(row.snapshot_at)
        .bind(row.base_reserve.to_string())
        .bind(row.quote_reserve.to_string())
        .bind(row.lp_burned as i64)
        .bind(row.pool_supply_fraction)
        .bind(row.mint_authority_present as i64)
        .bind(row.freeze_authority_present as i64)
        .bind(row.visible_holder_count)
        .bind(row.top10_concentration)
        .bind(row.top20_concentration)
        .bind(row.creator_bag_fraction)
        .bind(row.curve_graduated as i64)
        .bind(row.curve_real_sol_reserves.to_string())
        .bind(row.curve_real_token_reserves.to_string())
        .bind(row.curve_token_total_supply.to_string())
        .bind(row.capped_signature_count)
        .bind(row.signature_count_capped as i64)
        .bind(row.oldest_signature_age_secs)
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("insert feature snapshot: {e}")))?;
        Ok(())
    }

    /// True if a feature snapshot already exists for `graduation_id` — the
    /// collector's idempotency check before extracting features.
    pub async fn has_feature_snapshot(&self, graduation_id: i64) -> Result<bool> {
        let row: (i64,) =
            sqlx::query_as("SELECT COUNT(*) FROM feature_snapshots WHERE graduation_id = ?1")
                .bind(graduation_id)
                .fetch_one(&self.pool)
                .await
                .map_err(|e| StormError::Rpc(format!("has_feature_snapshot: {e}")))?;
        Ok(row.0 > 0)
    }

    /// Persist the recorded outcome for a graduation. The `graduation_id UNIQUE`
    /// constraint backstops the collector's `has_outcome` idempotency check.
    pub async fn insert_outcome(&self, row: &OutcomeRow) -> Result<()> {
        sqlx::query(
            "INSERT INTO outcomes \
             (graduation_id, outcome_at, survived, base_reserve, quote_reserve) \
             VALUES (?1, ?2, ?3, ?4, ?5)",
        )
        .bind(row.graduation_id)
        .bind(row.outcome_at)
        .bind(row.survived as i64)
        .bind(row.base_reserve.to_string())
        .bind(row.quote_reserve.to_string())
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("insert outcome: {e}")))?;
        Ok(())
    }

    /// True if an outcome already exists for `graduation_id`.
    pub async fn has_outcome(&self, graduation_id: i64) -> Result<bool> {
        let row: (i64,) = sqlx::query_as("SELECT COUNT(*) FROM outcomes WHERE graduation_id = ?1")
            .bind(graduation_id)
            .fetch_one(&self.pool)
            .await
            .map_err(|e| StormError::Rpc(format!("has_outcome: {e}")))?;
        Ok(row.0 > 0)
    }

    /// Upsert a `collector_state` key/value pair (daemon heartbeat / progress).
    pub async fn set_collector_state(&self, key: &str, value: &str) -> Result<()> {
        sqlx::query(
            "INSERT INTO collector_state (key, value, updated_at) \
             VALUES (?1, ?2, unixepoch()) \
             ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        )
        .bind(key)
        .bind(value)
        .execute(&self.pool)
        .await
        .map_err(|e| StormError::Rpc(format!("set_collector_state: {e}")))?;
        Ok(())
    }

    /// Read a `collector_state` value, or `None` if the key is absent.
    pub async fn get_collector_state(&self, key: &str) -> Result<Option<String>> {
        let row: Option<(String,)> =
            sqlx::query_as("SELECT value FROM collector_state WHERE key = ?1")
                .bind(key)
                .fetch_optional(&self.pool)
                .await
                .map_err(|e| StormError::Rpc(format!("get_collector_state: {e}")))?;
        Ok(row.map(|(v,)| v))
    }
}

/// Parse a base58 `Pubkey` stored as TEXT, attributing failures to `field`.
fn parse_pubkey(s: &str, field: &str) -> Result<Pubkey> {
    Pubkey::from_str(s).map_err(|e| StormError::Parse(format!("invalid {field} pubkey '{s}': {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn migrate_then_roundtrip_a_snapshot() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let pool_addr = Pubkey::new_unique();
        let row = PoolRow {
            address: pool_addr,
            program_id: Pubkey::new_unique(),
            dex: Dex::RaydiumAmmV4,
            token_a_mint: Pubkey::new_unique(),
            token_b_mint: Pubkey::new_unique(),
            token_a_decimals: 9,
            token_b_decimals: 6,
        };
        store.upsert_pool(&row).await.unwrap();

        let snap = PriceSnapshot {
            pool_address: pool_addr,
            spot_price: Decimal::from_str("142.5").unwrap(),
            reserve_a_raw: 1_000_000_000_000,
            reserve_b_raw: 142_000_000_000,
        };
        store.insert_price_snapshot(&snap).await.unwrap();
        store.insert_price_snapshot(&snap).await.unwrap();

        let latest = store.latest_price(&pool_addr).await.unwrap().unwrap();
        assert_eq!(latest.spot_price.to_string(), "142.5");
        assert_eq!(latest.reserve_a_raw, 1_000_000_000_000);
        assert_eq!(latest.reserve_b_raw, 142_000_000_000);

        // Upsert idempotency: re-upsert with different decimals should overwrite.
        let mut updated = row;
        updated.token_a_decimals = 8;
        store.upsert_pool(&updated).await.unwrap();
    }

    #[tokio::test]
    async fn latest_price_for_unknown_pool_is_none() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();
        assert!(store
            .latest_price(&Pubkey::new_unique())
            .await
            .unwrap()
            .is_none());
    }

    fn sample_graduation(mint: Pubkey) -> GraduationRow {
        GraduationRow {
            mint,
            pool_address: Pubkey::new_unique(),
            bonding_curve_address: Pubkey::new_unique(),
            graduation_slot: 250_000_000,
            detected_at: 1_779_000_000,
            status: GraduationStatus::PendingSnapshot,
        }
    }

    #[tokio::test]
    async fn graduation_insert_is_idempotent_on_mint() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let mint = Pubkey::new_unique();
        let grad = sample_graduation(mint);

        // First insert returns the new row id.
        let id1 = store.insert_graduation(&grad).await.unwrap();
        assert!(id1.is_some(), "first insert should return an id");

        // Re-inserting the SAME mint is a no-op: returns None, no duplicate row.
        let id2 = store.insert_graduation(&grad).await.unwrap();
        assert!(id2.is_none(), "duplicate mint insert should be a no-op");
        assert_eq!(store.graduation_count().await.unwrap(), 1);
    }

    #[tokio::test]
    async fn graduation_status_queue_and_transition() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let grad = sample_graduation(Pubkey::new_unique());
        let id = store.insert_graduation(&grad).await.unwrap().unwrap();

        // Freshly inserted -> appears in the pending_snapshot queue.
        let pending = store
            .graduations_with_status(GraduationStatus::PendingSnapshot)
            .await
            .unwrap();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].id, id);
        assert_eq!(pending[0].mint, grad.mint);
        assert_eq!(pending[0].pool_address, grad.pool_address);

        // Advance it; it leaves the pending queue and enters snapshot_done.
        store
            .set_graduation_status(id, GraduationStatus::SnapshotDone)
            .await
            .unwrap();
        assert!(store
            .graduations_with_status(GraduationStatus::PendingSnapshot)
            .await
            .unwrap()
            .is_empty());
        let done = store
            .graduations_with_status(GraduationStatus::SnapshotDone)
            .await
            .unwrap();
        assert_eq!(done.len(), 1);
        assert_eq!(done[0].id, id);
    }

    fn sample_snapshot(graduation_id: i64) -> FeatureSnapshotRow {
        FeatureSnapshotRow {
            graduation_id,
            snapshot_at: 1_779_050_000,
            base_reserve: 200_000_000_000_000,
            quote_reserve: 85_000_000_000,
            lp_burned: true,
            pool_supply_fraction: 0.2,
            mint_authority_present: false,
            freeze_authority_present: false,
            visible_holder_count: 18,
            top10_concentration: 0.31,
            top20_concentration: 0.44,
            creator_bag_fraction: 0.05,
            curve_graduated: true,
            curve_real_sol_reserves: 85_000_000_000,
            curve_real_token_reserves: 0,
            curve_token_total_supply: 1_000_000_000_000_000,
            capped_signature_count: 7,
            signature_count_capped: false,
            oldest_signature_age_secs: Some(123_456),
        }
    }

    #[tokio::test]
    async fn feature_snapshot_round_trips() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let grad = sample_graduation(Pubkey::new_unique());
        let gid = store.insert_graduation(&grad).await.unwrap().unwrap();

        // No snapshot yet.
        assert!(!store.has_feature_snapshot(gid).await.unwrap());

        let snap = sample_snapshot(gid);
        store.insert_feature_snapshot(&snap).await.unwrap();

        // Now the existence guard reports it.
        assert!(store.has_feature_snapshot(gid).await.unwrap());

        // The persisted u64 fields survive the TEXT round-trip exactly.
        let (base, quote, supply, capped): (String, String, String, i64) = sqlx::query_as(
            "SELECT base_reserve, quote_reserve, curve_token_total_supply, \
                    capped_signature_count FROM feature_snapshots WHERE graduation_id = ?1",
        )
        .bind(gid)
        .fetch_one(&store.pool)
        .await
        .unwrap();
        assert_eq!(base, "200000000000000");
        assert_eq!(quote, "85000000000");
        assert_eq!(supply, "1000000000000000");
        assert_eq!(capped, 7);
    }

    #[tokio::test]
    async fn feature_snapshot_nullable_age_persists_as_null() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();
        let gid = store
            .insert_graduation(&sample_graduation(Pubkey::new_unique()))
            .await
            .unwrap()
            .unwrap();
        let mut snap = sample_snapshot(gid);
        snap.oldest_signature_age_secs = None;
        store.insert_feature_snapshot(&snap).await.unwrap();
        let age: (Option<i64>,) = sqlx::query_as(
            "SELECT oldest_signature_age_secs FROM feature_snapshots WHERE graduation_id = ?1",
        )
        .bind(gid)
        .fetch_one(&store.pool)
        .await
        .unwrap();
        assert_eq!(age.0, None);
    }

    #[tokio::test]
    async fn outcome_round_trips_and_existence_check() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        let gid = store
            .insert_graduation(&sample_graduation(Pubkey::new_unique()))
            .await
            .unwrap()
            .unwrap();

        assert!(!store.has_outcome(gid).await.unwrap());

        let outcome = OutcomeRow {
            graduation_id: gid,
            outcome_at: 1_780_000_000,
            survived: true,
            base_reserve: 150_000_000_000_000,
            quote_reserve: 60_000_000_000,
        };
        store.insert_outcome(&outcome).await.unwrap();
        assert!(store.has_outcome(gid).await.unwrap());

        let (survived, quote): (i64, String) =
            sqlx::query_as("SELECT survived, quote_reserve FROM outcomes WHERE graduation_id = ?1")
                .bind(gid)
                .fetch_one(&store.pool)
                .await
                .unwrap();
        assert_eq!(survived, 1);
        assert_eq!(quote, "60000000000");
    }

    #[tokio::test]
    async fn collector_state_is_an_upsert() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();

        // Unknown key -> None.
        assert_eq!(
            store.get_collector_state("last_cycle_at").await.unwrap(),
            None
        );

        store
            .set_collector_state("last_cycle_at", "1779000000")
            .await
            .unwrap();
        assert_eq!(
            store.get_collector_state("last_cycle_at").await.unwrap(),
            Some("1779000000".to_string())
        );

        // Writing the same key again overwrites, never duplicates.
        store
            .set_collector_state("last_cycle_at", "1779999999")
            .await
            .unwrap();
        assert_eq!(
            store.get_collector_state("last_cycle_at").await.unwrap(),
            Some("1779999999".to_string())
        );
    }

    #[tokio::test]
    async fn migration_0002_creates_survival_tables() {
        let store = Store::open("sqlite::memory:").await.unwrap();
        store.migrate().await.unwrap();
        // Each new table must exist and be queryable (count of an empty table is 0).
        for table in [
            "graduations",
            "feature_snapshots",
            "outcomes",
            "collector_state",
        ] {
            let count: (i64,) = sqlx::query_as(&format!("SELECT COUNT(*) FROM {table}"))
                .fetch_one(&store.pool)
                .await
                .unwrap_or_else(|e| panic!("table {table} not queryable: {e}"));
            assert_eq!(count.0, 0, "{table} should start empty");
        }
    }
}
