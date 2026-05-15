//! Real-time CEX vs DEX price comparison.
//!
//! Streams Binance spot bid/ask and an Orca Whirlpool's on-chain price into
//! a single [`DislocationDetector`], emitting [`EngineEvent`]s when the two
//! venues diverge past a configurable threshold.

use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use rust_decimal::Decimal;
use solana_sdk::pubkey::Pubkey;
use tokio::sync::{broadcast, mpsc};
use tokio_util::sync::CancellationToken;
use tracing::{info, warn};

use storm_cex::{run_stream, CexEvent, FeedConfig, Source, StreamKind};
use storm_core::{Result, SolanaConfig};
use storm_solana::{
    sqrt_price_x64_to_price, subscribe_accounts, AccountUpdate, DexPool, RpcContext, WhirlpoolState,
};

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

/// Where a price observation came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Venue {
    BinanceSpot,
    OrcaWhirlpool,
}

/// One normalised price observation: quote-per-base for a single pair.
#[derive(Debug, Clone)]
pub struct MarketPrice {
    pub venue: Venue,
    pub symbol: String,
    pub price: Decimal,
    pub timestamp: i64,
}

/// A CEX/DEX price divergence at the moment it crossed the threshold.
#[derive(Debug, Clone)]
pub struct Dislocation {
    pub cex_price: Decimal,
    pub dex_price: Decimal,
    /// Signed: positive ⇒ CEX above DEX (the DEX is the cheap leg).
    pub diff_bps: Decimal,
    pub detected_at: i64,
}

impl Dislocation {
    /// Which way to trade to capture the divergence.
    pub fn direction(&self) -> &'static str {
        if self.diff_bps > Decimal::ZERO {
            "buy DEX, sell CEX"
        } else if self.diff_bps < Decimal::ZERO {
            "buy CEX, sell DEX"
        } else {
            "flat"
        }
    }
}

/// Lifecycle event for a divergence.
#[derive(Debug, Clone)]
pub enum DislocationEvent {
    /// The spread crossed the threshold.
    Opened(Dislocation),
    /// The spread fell back under the threshold.
    Closed {
        peak_bps: Decimal,
        duration_ms: i64,
        at: i64,
    },
}

struct OpenState {
    since: i64,
    peak_bps: Decimal,
}

/// Tracks the latest CEX and DEX prices for one pair and reports when their
/// spread opens or closes relative to `threshold_bps`.
pub struct DislocationDetector {
    threshold_bps: Decimal,
    last_cex: Option<Decimal>,
    last_dex: Option<Decimal>,
    open: Option<OpenState>,
}

impl DislocationDetector {
    pub fn new(threshold_bps: Decimal) -> Self {
        Self {
            threshold_bps,
            last_cex: None,
            last_dex: None,
            open: None,
        }
    }

    /// Signed CEX-vs-DEX spread in basis points, or `None` until both sides
    /// have reported at least once.
    pub fn current_diff_bps(&self) -> Option<Decimal> {
        match (self.last_cex, self.last_dex) {
            (Some(cex), Some(dex)) if !dex.is_zero() => {
                Some((cex - dex) / dex * Decimal::from(10_000))
            }
            _ => None,
        }
    }

    /// Record a price; returns an event if a divergence opened or closed.
    pub fn observe(&mut self, price: &MarketPrice) -> Option<DislocationEvent> {
        match price.venue {
            Venue::BinanceSpot => self.last_cex = Some(price.price),
            Venue::OrcaWhirlpool => self.last_dex = Some(price.price),
        }
        let (cex, dex) = match (self.last_cex, self.last_dex) {
            (Some(c), Some(d)) => (c, d),
            _ => return None,
        };
        if dex.is_zero() {
            return None;
        }
        let diff_bps = (cex - dex) / dex * Decimal::from(10_000);
        let abs = diff_bps.abs();

        match &mut self.open {
            None if abs >= self.threshold_bps => {
                self.open = Some(OpenState {
                    since: price.timestamp,
                    peak_bps: abs,
                });
                Some(DislocationEvent::Opened(Dislocation {
                    cex_price: cex,
                    dex_price: dex,
                    diff_bps,
                    detected_at: price.timestamp,
                }))
            }
            None => None,
            Some(state) => {
                if abs > state.peak_bps {
                    state.peak_bps = abs;
                }
                if abs < self.threshold_bps {
                    let event = DislocationEvent::Closed {
                        peak_bps: state.peak_bps,
                        duration_ms: price.timestamp - state.since,
                        at: price.timestamp,
                    };
                    self.open = None;
                    Some(event)
                } else {
                    None
                }
            }
        }
    }
}

/// Anything the engine publishes to its consumers.
#[derive(Debug, Clone)]
pub enum EngineEvent {
    Price(MarketPrice),
    Dislocation(DislocationEvent),
}

/// Inputs for [`CexDexEngine::run`].
#[derive(Debug, Clone)]
pub struct EngineConfig {
    /// RPC + WS URLs and commitment for the Solana side.
    pub solana: SolanaConfig,
    /// Binance spot WS base URL (e.g. `wss://stream.binance.com:9443`).
    pub binance_spot_ws_base: String,
    /// Binance symbol to stream (e.g. `SOLUSDC`).
    pub binance_symbol: String,
    /// Display label for the base asset (e.g. `SOL`).
    pub base_symbol: String,
    /// Orca Whirlpool address to watch.
    pub whirlpool: Pubkey,
    /// Spread threshold for opening a dislocation.
    pub threshold_bps: Decimal,
    /// Broadcast / stream channel capacity.
    pub channel_capacity: usize,
}

/// Orchestrates the Binance and Solana streams through a shared detector.
pub struct CexDexEngine {
    events_tx: broadcast::Sender<EngineEvent>,
}

impl CexDexEngine {
    pub fn new(channel_capacity: usize) -> Self {
        let (events_tx, _) = broadcast::channel(channel_capacity);
        Self { events_tx }
    }

    /// A receiver for engine events. Subscribe before calling [`run`].
    pub fn subscribe(&self) -> broadcast::Receiver<EngineEvent> {
        self.events_tx.subscribe()
    }

    /// Run until `cancel` fires: one whirlpool fetch for decimals + initial
    /// price, then the Binance and Solana streams feed a shared detector.
    pub async fn run(&self, config: EngineConfig, cancel: CancellationToken) -> Result<()> {
        let rpc = RpcContext::from_config(&config.solana);
        let pool = rpc.fetch_orca_whirlpool(&config.whirlpool).await?;
        let (dec_a, dec_b) = (pool.token_a().decimals, pool.token_b().decimals);
        info!(
            whirlpool = %config.whirlpool,
            decimals_a = dec_a,
            decimals_b = dec_b,
            initial_price = %pool.price(),
            "cex-dex engine: whirlpool resolved"
        );

        // Shared between the CEX and DEX consumer tasks.
        let detector = Arc::new(Mutex::new(DislocationDetector::new(config.threshold_bps)));

        // Seed with the initial on-chain price.
        publish(
            &detector,
            &self.events_tx,
            MarketPrice {
                venue: Venue::OrcaWhirlpool,
                symbol: config.base_symbol.clone(),
                price: pool.price(),
                timestamp: now_ms(),
            },
        );

        // Binance spot bookTicker stream.
        let feed_cfg = FeedConfig {
            spot_ws_base: config.binance_spot_ws_base.clone(),
            futures_ws_base: String::new(),
            symbols: vec![config.binance_symbol.clone()],
            channel_capacity: config.channel_capacity,
        };
        let (cex_tx, cex_rx) = broadcast::channel::<CexEvent>(config.channel_capacity);
        let cex_stream = tokio::spawn(run_stream(
            feed_cfg.spot_book_ticker_url(),
            StreamKind::BookTicker(Source::BinanceSpot),
            cex_tx,
            cancel.clone(),
        ));

        // Solana accountSubscribe stream for the whirlpool account.
        let (acct_tx, acct_rx) = mpsc::channel::<AccountUpdate>(256);
        let dex_stream = tokio::spawn(subscribe_accounts(
            config.solana.ws_url.clone(),
            vec![config.whirlpool],
            config.solana.commitment.clone(),
            acct_tx,
            cancel.clone(),
        ));

        let cex_consumer = tokio::spawn(consume_cex(
            cex_rx,
            detector.clone(),
            self.events_tx.clone(),
        ));
        let dex_consumer = tokio::spawn(consume_dex(
            acct_rx,
            detector,
            self.events_tx.clone(),
            config.whirlpool,
            config.base_symbol.clone(),
            dec_a,
            dec_b,
        ));

        cancel.cancelled().await;
        let _ = tokio::join!(cex_stream, dex_stream, cex_consumer, dex_consumer);
        Ok(())
    }
}

/// Observe a price through the shared detector, then publish the price plus
/// any resulting dislocation event.
fn publish(
    detector: &Arc<Mutex<DislocationDetector>>,
    events_tx: &broadcast::Sender<EngineEvent>,
    price: MarketPrice,
) {
    let event = detector
        .lock()
        .expect("detector mutex poisoned")
        .observe(&price);
    let _ = events_tx.send(EngineEvent::Price(price));
    if let Some(event) = event {
        let _ = events_tx.send(EngineEvent::Dislocation(event));
    }
}

async fn consume_cex(
    mut rx: broadcast::Receiver<CexEvent>,
    detector: Arc<Mutex<DislocationDetector>>,
    events_tx: broadcast::Sender<EngineEvent>,
) {
    loop {
        match rx.recv().await {
            Ok(CexEvent::Price(tick)) => publish(
                &detector,
                &events_tx,
                MarketPrice {
                    venue: Venue::BinanceSpot,
                    symbol: tick.symbol.clone(),
                    price: tick.mid(),
                    timestamp: tick.timestamp,
                },
            ),
            Ok(CexEvent::Funding(_)) => {}
            Err(broadcast::error::RecvError::Lagged(n)) => {
                warn!("cex-dex engine: cex consumer lagged, dropped {n}");
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn consume_dex(
    mut rx: mpsc::Receiver<AccountUpdate>,
    detector: Arc<Mutex<DislocationDetector>>,
    events_tx: broadcast::Sender<EngineEvent>,
    whirlpool: Pubkey,
    symbol: String,
    dec_a: u8,
    dec_b: u8,
) {
    while let Some(update) = rx.recv().await {
        if update.pubkey != whirlpool {
            continue;
        }
        match WhirlpoolState::unpack(&update.data) {
            Ok(state) => publish(
                &detector,
                &events_tx,
                MarketPrice {
                    venue: Venue::OrcaWhirlpool,
                    symbol: symbol.clone(),
                    price: sqrt_price_x64_to_price(state.sqrt_price_x64, dec_a, dec_b),
                    timestamp: now_ms(),
                },
            ),
            Err(e) => warn!(error = %e, "cex-dex engine: whirlpool unpack failed"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    fn cex(price: &str, ts: i64) -> MarketPrice {
        MarketPrice {
            venue: Venue::BinanceSpot,
            symbol: "SOL".into(),
            price: Decimal::from_str(price).unwrap(),
            timestamp: ts,
        }
    }

    fn dex(price: &str, ts: i64) -> MarketPrice {
        MarketPrice {
            venue: Venue::OrcaWhirlpool,
            symbol: "SOL".into(),
            price: Decimal::from_str(price).unwrap(),
            timestamp: ts,
        }
    }

    #[test]
    fn no_event_until_both_sides_seen() {
        let mut d = DislocationDetector::new(Decimal::from(10));
        assert!(d.observe(&cex("100", 0)).is_none());
        assert!(d.current_diff_bps().is_none());
        // DEX equal to CEX → 0 bps, still no event.
        assert!(d.observe(&dex("100", 1)).is_none());
        assert_eq!(d.current_diff_bps(), Some(Decimal::ZERO));
    }

    #[test]
    fn no_event_below_threshold() {
        let mut d = DislocationDetector::new(Decimal::from(10));
        d.observe(&cex("100.00", 0));
        // 100 vs 99.95 → ~5 bps, under the 10 bps threshold.
        assert!(d.observe(&dex("99.95", 1)).is_none());
    }

    #[test]
    fn opens_then_closes_with_size_and_duration() {
        let mut d = DislocationDetector::new(Decimal::from(10));
        d.observe(&cex("100", 0));

        // 100 vs 99 → ~101 bps → opens.
        let opened = d.observe(&dex("99", 1000)).expect("should open");
        match opened {
            DislocationEvent::Opened(disl) => {
                assert!(disl.diff_bps > Decimal::from(100));
                assert_eq!(disl.direction(), "buy DEX, sell CEX");
            }
            other => panic!("expected Opened, got {other:?}"),
        }

        // Still dislocated (~50 bps) → no event, peak retained.
        assert!(d.observe(&dex("99.5", 2000)).is_none());

        // Back in line → closes with the recorded peak + duration.
        let closed = d.observe(&dex("100", 4000)).expect("should close");
        match closed {
            DislocationEvent::Closed {
                peak_bps,
                duration_ms,
                ..
            } => {
                assert!(peak_bps > Decimal::from(100));
                assert_eq!(duration_ms, 4000 - 1000);
            }
            other => panic!("expected Closed, got {other:?}"),
        }
    }

    #[test]
    fn direction_flips_when_dex_is_richer() {
        let mut d = DislocationDetector::new(Decimal::from(10));
        d.observe(&cex("99", 0));
        let opened = d.observe(&dex("100", 1)).expect("should open");
        match opened {
            DislocationEvent::Opened(disl) => {
                assert!(disl.diff_bps < Decimal::ZERO);
                assert_eq!(disl.direction(), "buy CEX, sell DEX");
            }
            other => panic!("expected Opened, got {other:?}"),
        }
    }
}
