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
            let count: (i64,) =
                sqlx::query_as(&format!("SELECT COUNT(*) FROM {table}"))
                    .fetch_one(&store.pool)
                    .await
                    .unwrap_or_else(|e| panic!("table {table} not queryable: {e}"));
            assert_eq!(count.0, 0, "{table} should start empty");
        }
    }
}
