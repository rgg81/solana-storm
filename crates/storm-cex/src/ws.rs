use std::str::FromStr;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use futures_util::{SinkExt, StreamExt};
use rust_decimal::Decimal;
use serde::Deserialize;
use tokio::sync::broadcast;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use crate::types::{normalize_symbol, CexEvent, FundingTick, PriceTick, Source};
use storm_core::{Result, StormError};

/// Initial reconnect delay; doubles up to [`MAX_BACKOFF`] on each failure.
pub const INITIAL_BACKOFF: Duration = Duration::from_secs(1);
/// Reconnect delay ceiling.
pub const MAX_BACKOFF: Duration = Duration::from_secs(60);

/// Next exponential-backoff delay, capped at [`MAX_BACKOFF`].
pub fn next_backoff(current: Duration) -> Duration {
    (current * 2).min(MAX_BACKOFF)
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

// ---- Binance combined-stream payloads -------------------------------------

#[derive(Deserialize)]
struct Combined<T> {
    #[allow(dead_code)]
    stream: String,
    data: T,
}

#[derive(Deserialize)]
struct BookTickerData {
    s: String, // symbol, e.g. "SOLUSDT"
    b: String, // best bid price
    a: String, // best ask price
}

#[derive(Deserialize)]
struct MarkPriceData {
    s: String, // symbol
    p: String, // mark price
    r: String, // funding rate
    #[serde(rename = "T")]
    next_funding_time: i64,
}

/// Parse a Binance combined-stream `bookTicker` frame into a [`PriceTick`].
pub fn parse_book_ticker(json: &str, source: Source) -> Result<PriceTick> {
    let msg: Combined<BookTickerData> = serde_json::from_str(json)
        .map_err(|e| StormError::Parse(format!("bookTicker frame: {e}")))?;
    let bid = Decimal::from_str(&msg.data.b)
        .map_err(|e| StormError::Parse(format!("bid '{}': {e}", msg.data.b)))?;
    let ask = Decimal::from_str(&msg.data.a)
        .map_err(|e| StormError::Parse(format!("ask '{}': {e}", msg.data.a)))?;
    Ok(PriceTick {
        symbol: normalize_symbol(&msg.data.s),
        bid,
        ask,
        timestamp: now_ms(),
        source,
    })
}

/// Parse a Binance combined-stream `markPrice` frame into a [`FundingTick`].
pub fn parse_mark_price(json: &str) -> Result<FundingTick> {
    let msg: Combined<MarkPriceData> = serde_json::from_str(json)
        .map_err(|e| StormError::Parse(format!("markPrice frame: {e}")))?;
    let mark_price = Decimal::from_str(&msg.data.p)
        .map_err(|e| StormError::Parse(format!("mark price '{}': {e}", msg.data.p)))?;
    let funding_rate = Decimal::from_str(&msg.data.r)
        .map_err(|e| StormError::Parse(format!("funding rate '{}': {e}", msg.data.r)))?;
    Ok(FundingTick {
        symbol: normalize_symbol(&msg.data.s),
        mark_price,
        funding_rate,
        next_funding_time: msg.data.next_funding_time,
        timestamp: now_ms(),
    })
}

/// Kind of stream a connection carries — selects the right parser.
#[derive(Debug, Clone, Copy)]
pub enum StreamKind {
    BookTicker(Source),
    MarkPrice,
}

impl StreamKind {
    fn parse(&self, json: &str) -> Result<CexEvent> {
        match self {
            StreamKind::BookTicker(src) => parse_book_ticker(json, *src).map(CexEvent::Price),
            StreamKind::MarkPrice => parse_mark_price(json).map(CexEvent::Funding),
        }
    }
}

/// Connect to `url`, pump frames into `tx`, and reconnect with exponential
/// backoff until `cancel` fires. Returns only on cancellation.
pub async fn run_stream(
    url: String,
    kind: StreamKind,
    tx: broadcast::Sender<CexEvent>,
    cancel: CancellationToken,
) {
    let mut backoff = INITIAL_BACKOFF;
    loop {
        if cancel.is_cancelled() {
            break;
        }
        match connect_async(&url).await {
            Ok((ws, _resp)) => {
                info!(%url, "cex stream connected");
                backoff = INITIAL_BACKOFF; // reset once the handshake succeeds
                pump(ws, kind, &tx, &cancel).await;
            }
            Err(e) => warn!(%url, error = %e, "cex stream connect failed"),
        }
        if cancel.is_cancelled() {
            break;
        }
        tokio::select! {
            _ = cancel.cancelled() => break,
            _ = tokio::time::sleep(backoff) => {}
        }
        backoff = next_backoff(backoff);
        warn!(%url, "reconnecting cex stream");
    }
    info!(%url, "cex stream stopped");
}

type WsStream =
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

async fn pump(
    mut ws: WsStream,
    kind: StreamKind,
    tx: &broadcast::Sender<CexEvent>,
    cancel: &CancellationToken,
) {
    loop {
        tokio::select! {
            _ = cancel.cancelled() => {
                let _ = ws.close(None).await;
                break;
            }
            msg = ws.next() => match msg {
                Some(Ok(Message::Text(txt))) => match kind.parse(txt.as_str()) {
                    Ok(ev) => {
                        // A send error just means no consumers are attached.
                        let _ = tx.send(ev);
                    }
                    Err(e) => debug!(error = %e, "skipped unparseable cex frame"),
                },
                Some(Ok(Message::Ping(payload))) => {
                    let _ = ws.send(Message::Pong(payload)).await;
                }
                Some(Ok(Message::Close(_))) | None => {
                    warn!("cex stream closed by peer");
                    break;
                }
                Some(Ok(_)) => {} // pong / binary — ignore
                Some(Err(e)) => {
                    warn!(error = %e, "cex stream read error");
                    break;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_doubles_and_caps() {
        let mut b = INITIAL_BACKOFF;
        assert_eq!(b, Duration::from_secs(1));
        b = next_backoff(b);
        assert_eq!(b, Duration::from_secs(2));
        b = next_backoff(b);
        assert_eq!(b, Duration::from_secs(4));
        // Run it well past the cap.
        for _ in 0..20 {
            b = next_backoff(b);
        }
        assert_eq!(b, MAX_BACKOFF);
    }

    #[test]
    fn parses_real_spot_book_ticker_frame() {
        let frame = r#"{"stream":"solusdt@bookTicker","data":{"u":400900217,"s":"SOLUSDT","b":"88.88000000","B":"0.13600000","a":"88.94000000","A":"22.49700000"}}"#;
        let tick = parse_book_ticker(frame, Source::BinanceSpot).unwrap();
        assert_eq!(tick.symbol, "SOL");
        assert_eq!(tick.bid.to_string(), "88.88000000");
        assert_eq!(tick.ask.to_string(), "88.94000000");
        assert_eq!(tick.source, Source::BinanceSpot);
    }

    #[test]
    fn parses_real_futures_mark_price_frame() {
        let frame = r#"{"stream":"solusdt@markPrice","data":{"e":"markPriceUpdate","E":1562305380000,"s":"SOLUSDT","p":"88.91000000","i":"88.90000000","P":"88.92000000","r":"0.00010000","T":1562306400000}}"#;
        let funding = parse_mark_price(frame).unwrap();
        assert_eq!(funding.symbol, "SOL");
        assert_eq!(funding.mark_price.to_string(), "88.91000000");
        assert_eq!(funding.funding_rate.to_string(), "0.00010000");
        assert_eq!(funding.next_funding_time, 1562306400000);
    }

    #[test]
    fn rejects_malformed_frame() {
        assert!(parse_book_ticker("not json", Source::BinanceSpot).is_err());
        assert!(parse_book_ticker(
            r#"{"stream":"x","data":{"s":"SOLUSDT","b":"abc","a":"1"}}"#,
            Source::BinanceSpot
        )
        .is_err());
    }
}
