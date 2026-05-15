use tokio::sync::broadcast;
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

use crate::types::{CexEvent, Source};
use crate::ws::{run_stream, StreamKind};

/// Default Binance spot WebSocket base (global venue).
pub const DEFAULT_SPOT_WS: &str = "wss://stream.binance.com:9443";
/// Default Binance USD-M futures WebSocket base (global venue).
pub const DEFAULT_FUTURES_WS: &str = "wss://fstream.binance.com";

#[derive(Debug, Clone)]
pub struct FeedConfig {
    /// Spot WS base URL, no path (e.g. `wss://stream.binance.com:9443`).
    pub spot_ws_base: String,
    /// USD-M futures WS base URL, no path (e.g. `wss://fstream.binance.com`).
    pub futures_ws_base: String,
    /// Binance symbols to subscribe (e.g. `["SOLUSDT", "ETHUSDT", "BTCUSDT"]`).
    pub symbols: Vec<String>,
    /// Broadcast channel capacity (ticks buffered for slow consumers).
    pub channel_capacity: usize,
}

impl Default for FeedConfig {
    fn default() -> Self {
        Self {
            spot_ws_base: DEFAULT_SPOT_WS.to_string(),
            futures_ws_base: DEFAULT_FUTURES_WS.to_string(),
            symbols: ["SOLUSDT", "ETHUSDT", "BTCUSDT"].map(String::from).to_vec(),
            channel_capacity: 1024,
        }
    }
}

impl FeedConfig {
    /// Override the WS base URLs from `BINANCE_SPOT_WS` / `BINANCE_FUTURES_WS`
    /// if set — useful for Binance.US or a local mock server.
    pub fn with_env_overrides(mut self) -> Self {
        if let Ok(v) = std::env::var("BINANCE_SPOT_WS") {
            self.spot_ws_base = v;
        }
        if let Ok(v) = std::env::var("BINANCE_FUTURES_WS") {
            self.futures_ws_base = v;
        }
        self
    }

    fn combined_url(base: &str, suffix: &str, symbols: &[String]) -> String {
        let streams = symbols
            .iter()
            .map(|s| format!("{}@{suffix}", s.to_lowercase()))
            .collect::<Vec<_>>()
            .join("/");
        format!("{base}/stream?streams={streams}")
    }

    pub fn spot_book_ticker_url(&self) -> String {
        Self::combined_url(&self.spot_ws_base, "bookTicker", &self.symbols)
    }

    pub fn futures_mark_price_url(&self) -> String {
        Self::combined_url(&self.futures_ws_base, "markPrice", &self.symbols)
    }
}

/// Owns the broadcast channel and spawns the Binance stream tasks.
pub struct BinanceFeed {
    config: FeedConfig,
    tx: broadcast::Sender<CexEvent>,
}

impl BinanceFeed {
    pub fn new(config: FeedConfig) -> Self {
        let (tx, _) = broadcast::channel(config.channel_capacity);
        Self { config, tx }
    }

    /// A new receiver. Subscribe *before* calling [`BinanceFeed::spawn`] to
    /// avoid missing early ticks.
    pub fn subscribe(&self) -> broadcast::Receiver<CexEvent> {
        self.tx.subscribe()
    }

    pub fn config(&self) -> &FeedConfig {
        &self.config
    }

    /// Spawn the spot `bookTicker` and futures `markPrice` stream tasks.
    /// Each reconnects on its own until `cancel` fires.
    pub fn spawn(&self, cancel: CancellationToken) -> Vec<JoinHandle<()>> {
        vec![
            tokio::spawn(run_stream(
                self.config.spot_book_ticker_url(),
                StreamKind::BookTicker(Source::BinanceSpot),
                self.tx.clone(),
                cancel.clone(),
            )),
            tokio::spawn(run_stream(
                self.config.futures_mark_price_url(),
                StreamKind::MarkPrice,
                self.tx.clone(),
                cancel,
            )),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_combined_stream_urls() {
        let cfg = FeedConfig {
            spot_ws_base: "wss://spot".into(),
            futures_ws_base: "wss://fut".into(),
            symbols: vec!["SOLUSDT".into(), "ETHUSDT".into()],
            channel_capacity: 16,
        };
        assert_eq!(
            cfg.spot_book_ticker_url(),
            "wss://spot/stream?streams=solusdt@bookTicker/ethusdt@bookTicker"
        );
        assert_eq!(
            cfg.futures_mark_price_url(),
            "wss://fut/stream?streams=solusdt@markPrice/ethusdt@markPrice"
        );
    }

    #[test]
    fn default_targets_binance_global() {
        let cfg = FeedConfig::default();
        assert!(cfg.spot_ws_base.contains("stream.binance.com"));
        assert!(cfg.futures_ws_base.contains("fstream.binance.com"));
        assert_eq!(cfg.symbols.len(), 3);
    }
}
