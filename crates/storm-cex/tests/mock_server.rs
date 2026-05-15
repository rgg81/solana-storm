//! End-to-end pipeline tests against a local mock WebSocket server.
//! No external network — verifies connect → read → parse → broadcast → consume
//! and the reconnect loop.

use std::time::Duration;

use futures_util::SinkExt;
use storm_cex::{run_stream, CexEvent, Source, StreamKind};
use tokio::net::TcpListener;
use tokio::sync::broadcast;
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;

const FRAME: &str = r#"{"stream":"solusdt@bookTicker","data":{"u":1,"s":"SOLUSDT","b":"100.10","B":"1","a":"100.20","A":"2"}}"#;

#[tokio::test]
async fn pipeline_delivers_a_parsed_tick_from_a_mock_server() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    // Mock server: accept one WS connection, send one frame, hold it open.
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(stream).await.unwrap();
        ws.send(Message::Text(FRAME.into())).await.unwrap();
        tokio::time::sleep(Duration::from_secs(10)).await;
    });

    let (tx, mut rx) = broadcast::channel(16);
    let cancel = CancellationToken::new();
    let url = format!("ws://{addr}/stream?streams=solusdt@bookTicker");
    let client = tokio::spawn(run_stream(
        url,
        StreamKind::BookTicker(Source::BinanceSpot),
        tx,
        cancel.clone(),
    ));

    let ev = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("timed out waiting for a tick")
        .expect("broadcast channel closed");
    match ev {
        CexEvent::Price(t) => {
            assert_eq!(t.symbol, "SOL");
            assert_eq!(t.bid.to_string(), "100.10");
            assert_eq!(t.ask.to_string(), "100.20");
            assert_eq!(t.source, Source::BinanceSpot);
        }
        other => panic!("expected Price, got {other:?}"),
    }

    cancel.cancel();
    let _ = client.await;
    server.abort();
}

#[tokio::test]
async fn client_reconnects_after_the_server_drops_the_connection() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    // Mock server: accept connections in a loop; send one frame, then close.
    let server = tokio::spawn(async move {
        for _ in 0..3 {
            let (stream, _) = match listener.accept().await {
                Ok(v) => v,
                Err(_) => break,
            };
            if let Ok(mut ws) = tokio_tungstenite::accept_async(stream).await {
                let _ = ws.send(Message::Text(FRAME.into())).await;
                let _ = ws.close(None).await;
            }
        }
    });

    let (tx, mut rx) = broadcast::channel(16);
    let cancel = CancellationToken::new();
    let url = format!("ws://{addr}/stream?streams=solusdt@bookTicker");
    let client = tokio::spawn(run_stream(
        url,
        StreamKind::BookTicker(Source::BinanceSpot),
        tx,
        cancel.clone(),
    ));

    // First tick — initial connection.
    let first = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("timed out on first tick")
        .expect("channel closed");
    assert!(matches!(first, CexEvent::Price(_)));

    // Second tick — only possible if the client reconnected after the drop.
    let second = tokio::time::timeout(Duration::from_secs(10), rx.recv())
        .await
        .expect("timed out waiting for a post-reconnect tick")
        .expect("channel closed");
    assert!(matches!(second, CexEvent::Price(_)));

    cancel.cancel();
    let _ = client.await;
    server.abort();
}
