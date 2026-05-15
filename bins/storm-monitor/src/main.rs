use std::str::FromStr;
use std::time::Duration;

use clap::{Parser, Subcommand};
use rust_decimal::Decimal;
use solana_sdk::pubkey::Pubkey;
use storm_cex::{normalize_symbol, BinanceFeed, CexEvent, FeedConfig, DEFAULT_SPOT_WS};
use storm_core::Config;
use storm_engine::{CexDexEngine, DislocationEvent, EngineConfig, EngineEvent, Venue};
use tokio::sync::broadcast::error::RecvError;
use tokio_util::sync::CancellationToken;
use tracing::{info, warn};

#[derive(Parser)]
#[command(
    name = "storm-monitor",
    about = "Solana Storm monitoring daemon",
    version
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Stream raw Binance bid/ask + funding ticks.
    Binance,
    /// CEX vs DEX dashboard: Binance spot vs an Orca Whirlpool, with
    /// real-time dislocation alerts.
    CexDex {
        /// Orca Whirlpool address (default: SOL/USDC 0.05% tier).
        #[arg(long, default_value = "7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm")]
        whirlpool: String,
        /// Binance spot symbol to compare against.
        #[arg(long, default_value = "SOLUSDC")]
        symbol: String,
        /// Dislocation threshold in basis points (0.1% = 10 bps).
        #[arg(long, default_value_t = 10)]
        threshold_bps: u32,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Required before any wss:// connection — see storm_cex docs.
    storm_cex::install_crypto_provider();
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    match Cli::parse().command {
        Command::Binance => run_binance().await,
        Command::CexDex {
            whirlpool,
            symbol,
            threshold_bps,
        } => run_cex_dex(whirlpool, symbol, threshold_bps).await,
    }
}

/// Week 5 behaviour: print the raw Binance tick stream.
async fn run_binance() -> anyhow::Result<()> {
    let config = FeedConfig::default().with_env_overrides();
    info!(
        spot = %config.spot_ws_base,
        futures = %config.futures_ws_base,
        symbols = ?config.symbols,
        "storm-monitor: binance feed"
    );

    let feed = BinanceFeed::new(config);
    let mut rx = feed.subscribe();
    let cancel = CancellationToken::new();
    let handles = feed.spawn(cancel.clone());

    println!("storm-monitor binance — streaming ticks (Ctrl-C to stop)");
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                println!("\nshutdown signal received — closing streams…");
                cancel.cancel();
                break;
            }
            ev = rx.recv() => match ev {
                Ok(CexEvent::Price(t)) => println!(
                    "[{:<15}] {:<5} bid {:>16} ask {:>16}  spread {:>7} bps",
                    t.source, t.symbol, t.bid, t.ask, t.spread_bps().round_dp(2)
                ),
                Ok(CexEvent::Funding(f)) => println!(
                    "[funding        ] {:<5} mark {:>16} funding_rate {:>12}",
                    f.symbol, f.mark_price, f.funding_rate
                ),
                Err(RecvError::Lagged(n)) => warn!("consumer lagged — dropped {n} ticks"),
                Err(RecvError::Closed) => break,
            }
        }
    }
    for h in handles {
        let _ = tokio::time::timeout(Duration::from_secs(5), h).await;
    }
    println!("storm-monitor stopped.");
    Ok(())
}

/// Week 6 deliverable: the CEX vs DEX dislocation dashboard.
async fn run_cex_dex(whirlpool: String, symbol: String, threshold_bps: u32) -> anyhow::Result<()> {
    let cfg = Config::load()?;
    let whirlpool = Pubkey::from_str(&whirlpool)
        .map_err(|e| anyhow::anyhow!("invalid whirlpool '{whirlpool}': {e}"))?;
    let spot_base =
        std::env::var("BINANCE_SPOT_WS").unwrap_or_else(|_| DEFAULT_SPOT_WS.to_string());
    let threshold = Decimal::from(threshold_bps);

    let engine_config = EngineConfig {
        solana: cfg.solana,
        binance_spot_ws_base: spot_base,
        binance_symbol: symbol.clone(),
        base_symbol: normalize_symbol(&symbol),
        whirlpool,
        threshold_bps: threshold,
        channel_capacity: 1024,
    };

    let engine = CexDexEngine::new(1024);
    let mut rx = engine.subscribe();
    let cancel = CancellationToken::new();
    let engine_task = {
        let cancel = cancel.clone();
        tokio::spawn(async move { engine.run(engine_config, cancel).await })
    };

    println!(
        "storm-monitor cex-dex — Binance {symbol} vs Orca {whirlpool}\n\
         dislocation threshold {threshold_bps} bps (Ctrl-C to stop)"
    );

    let mut last_cex: Option<Decimal> = None;
    let mut last_dex: Option<Decimal> = None;

    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                println!("\nshutdown signal received — closing streams…");
                cancel.cancel();
                break;
            }
            ev = rx.recv() => match ev {
                Ok(EngineEvent::Price(mp)) => {
                    match mp.venue {
                        Venue::BinanceSpot => last_cex = Some(mp.price),
                        Venue::OrcaWhirlpool => last_dex = Some(mp.price),
                    }
                    let cex_s = last_cex
                        .map(|c| c.round_dp(6).to_string())
                        .unwrap_or_else(|| "—".to_string());
                    let dex_s = last_dex
                        .map(|d| d.round_dp(6).to_string())
                        .unwrap_or_else(|| "—".to_string());
                    let diff_s = match (last_cex, last_dex) {
                        (Some(c), Some(d)) if !d.is_zero() => {
                            let diff = (c - d) / d * Decimal::from(10_000);
                            let flag = if diff.abs() >= threshold {
                                "  <-- DISLOCATION"
                            } else {
                                ""
                            };
                            format!("  diff {:>8} bps{}", diff.round_dp(2), flag)
                        }
                        _ => String::new(),
                    };
                    println!("CEX {cex_s:>14}   DEX {dex_s:>14}{diff_s}");
                }
                Ok(EngineEvent::Dislocation(DislocationEvent::Opened(d))) => println!(
                    ">>> DISLOCATION OPENED  {} bps — {} (CEX {}, DEX {})",
                    d.diff_bps.round_dp(2), d.direction(),
                    d.cex_price.round_dp(6), d.dex_price.round_dp(6)
                ),
                Ok(EngineEvent::Dislocation(DislocationEvent::Closed { peak_bps, duration_ms, .. })) => println!(
                    "<<< dislocation CLOSED  after {:.1}s — peak {} bps",
                    duration_ms as f64 / 1000.0, peak_bps.round_dp(2)
                ),
                Err(RecvError::Lagged(n)) => warn!("dashboard lagged — dropped {n} events"),
                Err(RecvError::Closed) => break,
            }
        }
    }
    let _ = tokio::time::timeout(Duration::from_secs(6), engine_task).await;
    println!("storm-monitor stopped.");
    Ok(())
}
