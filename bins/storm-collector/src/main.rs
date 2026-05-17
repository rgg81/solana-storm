//! storm-collector — the always-on pump.fun survival-data daemon.
//!
//! Each cycle it discovers newly-graduated tokens, snapshots their features at
//! T0+window, and records outcomes once the outcome window matures. See
//! `docs/superpowers/plans/2026-05-17-storm-collector.md`.

mod classify;
mod config;
mod cycle;
mod discover;
mod schedule;

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use clap::Parser;
use storm_core::backoff::{next_backoff, INITIAL_BACKOFF};
use storm_core::Config;
use storm_solana::RpcContext;
use storm_store::Store;

use crate::config::CollectorConfig;
use crate::cycle::run_cycle;

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
    /// Run exactly one collection cycle and exit (for cron-style scheduling /
    /// manual checks) instead of looping forever.
    #[arg(long)]
    once: bool,
}

/// The RPC URL with any query string removed — the query carries the API key,
/// which must not be written to logs.
fn redacted_rpc_url(url: &str) -> &str {
    match url.split_once('?') {
        Some((base, _)) => base,
        None => url,
    }
}

/// Current wall-clock time as Unix seconds. Isolated so the daemon's clock read
/// is in one named place; the pure cycle logic takes the value as an argument.
fn now_unix() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
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
    let collector_cfg = CollectorConfig::from_env();

    let store = Store::open(&cli.db).await?;
    store.migrate().await?;
    let rpc = RpcContext::from_config(&cfg.solana);

    tracing::info!(
        db = %cli.db,
        rpc = redacted_rpc_url(&cfg.solana.rpc_url),
        cycle_secs = collector_cfg.cycle_interval.as_secs(),
        once = cli.once,
        "storm-collector starting",
    );

    if cli.once {
        run_cycle(&rpc, &store, &collector_cfg, now_unix()).await?;
        tracing::info!("single cycle complete; exiting (--once)");
        return Ok(());
    }

    run_daemon(&rpc, &store, &collector_cfg).await;
    Ok(())
}

/// The forever-loop: run a cycle, sleep, repeat — until Ctrl-C. A failing cycle
/// is logged and the next sleep uses exponential backoff; a success resets it.
async fn run_daemon(rpc: &RpcContext, store: &Store, cfg: &CollectorConfig) {
    let mut backoff = INITIAL_BACKOFF;
    loop {
        match run_cycle(rpc, store, cfg, now_unix()).await {
            Ok(()) => {
                backoff = INITIAL_BACKOFF; // healthy cycle — reset the backoff
                tracing::info!("cycle complete");
                if sleep_or_shutdown(cfg.cycle_interval).await {
                    break;
                }
            }
            Err(e) => {
                tracing::error!(error = %e, backoff_secs = backoff.as_secs(), "cycle failed; backing off");
                if sleep_or_shutdown(backoff).await {
                    break;
                }
                backoff = next_backoff(backoff);
            }
        }
    }
    tracing::info!("storm-collector stopped");
}

/// Sleep `dur`, or wake immediately on Ctrl-C. Returns `true` if Ctrl-C fired
/// (the daemon should stop), `false` if the sleep simply elapsed.
async fn sleep_or_shutdown(dur: Duration) -> bool {
    tokio::select! {
        _ = tokio::time::sleep(dur) => false,
        _ = tokio::signal::ctrl_c() => {
            tracing::info!("Ctrl-C received; shutting down");
            true
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacted_rpc_url_strips_the_api_key_query() {
        assert_eq!(
            redacted_rpc_url("https://mainnet.helius-rpc.com/?api-key=secret"),
            "https://mainnet.helius-rpc.com/"
        );
        // A URL with no query string is returned unchanged.
        assert_eq!(
            redacted_rpc_url("https://api.mainnet-beta.solana.com"),
            "https://api.mainnet-beta.solana.com"
        );
    }

    #[test]
    fn now_unix_is_a_plausible_recent_timestamp() {
        // Sanity bound: after 2025-01-01 and before 2100-01-01. Catches a clock
        // that is wildly wrong without being flaky.
        let now = now_unix();
        assert!(now > 1_735_689_600, "now_unix() should be after 2025-01-01");
        assert!(
            now < 4_102_444_800,
            "now_unix() should be before 2100-01-01"
        );
    }
}
