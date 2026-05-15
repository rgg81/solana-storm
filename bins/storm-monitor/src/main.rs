use std::time::Duration;

use storm_cex::{BinanceFeed, CexEvent, FeedConfig};
use tokio::sync::broadcast::error::RecvError;
use tokio_util::sync::CancellationToken;
use tracing::{info, warn};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Required before any wss:// connection — see storm_cex docs.
    storm_cex::install_crypto_provider();

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let config = FeedConfig::default().with_env_overrides();
    info!(
        spot = %config.spot_ws_base,
        futures = %config.futures_ws_base,
        symbols = ?config.symbols,
        "starting storm-monitor"
    );

    let feed = BinanceFeed::new(config);
    let mut rx = feed.subscribe();
    let cancel = CancellationToken::new();
    let handles = feed.spawn(cancel.clone());

    println!("storm-monitor — streaming Binance ticks (Ctrl-C to stop)");

    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                println!("\nshutdown signal received — closing streams…");
                cancel.cancel();
                break;
            }
            ev = rx.recv() => match ev {
                Ok(CexEvent::Price(t)) => {
                    println!(
                        "[{:<15}] {:<5} bid {:>16} ask {:>16}  spread {:>7} bps",
                        t.source, t.symbol, t.bid, t.ask, t.spread_bps().round_dp(2)
                    );
                }
                Ok(CexEvent::Funding(f)) => {
                    println!(
                        "[funding        ] {:<5} mark {:>16} funding_rate {:>12}",
                        f.symbol, f.mark_price, f.funding_rate
                    );
                }
                Err(RecvError::Lagged(n)) => {
                    warn!("consumer lagged — dropped {n} ticks");
                }
                Err(RecvError::Closed) => {
                    warn!("feed channel closed — exiting");
                    break;
                }
            }
        }
    }

    // Graceful shutdown: give the stream tasks a moment to close cleanly.
    for h in handles {
        let _ = tokio::time::timeout(Duration::from_secs(5), h).await;
    }
    println!("storm-monitor stopped.");
    Ok(())
}
