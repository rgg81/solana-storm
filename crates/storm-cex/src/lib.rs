pub mod feed;
pub mod types;
pub mod ws;

pub use feed::{BinanceFeed, FeedConfig, DEFAULT_FUTURES_WS, DEFAULT_SPOT_WS};
pub use types::{normalize_symbol, CexEvent, FundingTick, PriceTick, Source};
pub use ws::{next_backoff, parse_book_ticker, parse_mark_price, run_stream, StreamKind};

/// Install the process-wide rustls crypto provider.
///
/// Must be called once, early in `main()`, before opening any `wss://`
/// connection. Without it rustls 0.23 panics at connect time: the
/// dependency tree pulls in both the `ring` and `aws-lc-rs` backends
/// (via tokio-tungstenite and reqwest respectively), so rustls cannot
/// auto-select one. Idempotent — a second call is a no-op.
pub fn install_crypto_provider() {
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
}
