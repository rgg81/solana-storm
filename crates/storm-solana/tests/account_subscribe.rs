//! End-to-end test of the Solana `accountSubscribe` client against a local
//! mock WebSocket server. No external network.

use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use solana_sdk::pubkey::Pubkey;
use storm_solana::{subscribe_accounts, AccountUpdate};
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;

#[tokio::test]
async fn account_subscribe_pipeline_emits_an_update() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    // Mock RPC: accept the WS, read the subscribe request, confirm it, then
    // push one account notification.
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(stream).await.unwrap();

        // Client sends exactly one accountSubscribe (request id 1).
        let _req = ws.next().await;
        ws.send(Message::Text(
            r#"{"jsonrpc":"2.0","result":42,"id":1}"#.into(),
        ))
        .await
        .unwrap();

        // base64 "AQID" decodes to bytes [1, 2, 3].
        let notif = r#"{"jsonrpc":"2.0","method":"accountNotification","params":{"result":{"context":{"slot":100},"value":{"data":["AQID","base64"],"executable":false,"lamports":5,"owner":"11111111111111111111111111111111","rentEpoch":0,"space":3}},"subscription":42}}"#;
        ws.send(Message::Text(notif.into())).await.unwrap();

        tokio::time::sleep(Duration::from_secs(10)).await;
    });

    let account = Pubkey::new_unique();
    let (tx, mut rx) = mpsc::channel::<AccountUpdate>(16);
    let cancel = CancellationToken::new();
    let client = tokio::spawn(subscribe_accounts(
        format!("ws://{addr}"),
        vec![account],
        "confirmed".to_string(),
        tx,
        cancel.clone(),
    ));

    let update = tokio::time::timeout(Duration::from_secs(5), rx.recv())
        .await
        .expect("timed out waiting for an account update")
        .expect("channel closed");
    assert_eq!(update.pubkey, account);
    assert_eq!(update.data, vec![1, 2, 3]);
    assert_eq!(update.slot, 100);
    assert_eq!(update.lamports, 5);

    cancel.cancel();
    let _ = client.await;
    server.abort();
}
