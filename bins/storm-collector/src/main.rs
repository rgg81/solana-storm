//! storm-collector — the always-on pump.fun survival-data daemon.
//!
//! Each cycle it discovers newly-graduated tokens, snapshots their features at
//! T0+window, and records outcomes once the outcome window matures. See
//! `docs/superpowers/plans/2026-05-17-storm-collector.md`.

mod config;

// `schedule`'s pure functions are reachable only once `main` calls the cycle
// (Task 11); allow dead_code until then. The whole allow is removed in Task 11.
#[allow(dead_code)]
mod schedule;

// `classify` is reachable only once `main` calls the cycle (Task 11); allow
// dead_code until then. Removed in Task 11.
#[allow(dead_code)]
mod classify;

// `discover` is reachable only once `main` calls the cycle (Task 11); allow
// dead_code until then. Removed in Task 11.
#[allow(dead_code)]
mod discover;

// `cycle` is called by `main` in Task 11; allow dead_code until then.
// Removed in Task 11.
#[allow(dead_code)]
mod cycle;

use clap::Parser;
use storm_core::Config;

/// storm-collector command-line arguments.
#[derive(Parser)]
#[command(
    name = "storm-collector",
    about = "pump.fun survival-data collector",
    version
)]
struct Cli {
    /// SQLite database URL.
    #[arg(long, default_value = "sqlite://./storm.db")]
    db: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Load .env (gitignored) so SOLANA_RPC_URL / Helius credentials are picked up.
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cli = Cli::parse();
    let cfg = Config::load()?;

    tracing::info!(db = %cli.db, rpc = %cfg.solana.rpc_url, "storm-collector starting");

    let _collector_cfg = config::CollectorConfig::from_env();

    Ok(())
}
