//! Solana `accountSubscribe` WebSocket client.
//!
//! Opens a WebSocket to a Solana RPC node, subscribes to a fixed set of
//! account addresses, and forwards every change notification as an
//! [`AccountUpdate`]. Reconnects with exponential backoff.
//!
//! NOTE: a process-wide rustls `CryptoProvider` must be installed before
//! the first `wss://` connection — see `storm_cex::install_crypto_provider`.

use std::collections::HashMap;
use std::str::FromStr;

use base64::prelude::{Engine, BASE64_STANDARD};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use solana_sdk::pubkey::Pubkey;
use tokio::sync::mpsc;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;
use tokio_util::sync::CancellationToken;
use tracing::{debug, info, warn};

use storm_core::backoff::{next_backoff, INITIAL_BACKOFF};
use storm_core::{Result, StormError};

/// A single account-change notification.
#[derive(Debug, Clone)]
pub struct AccountUpdate {
    pub pubkey: Pubkey,
    pub owner: Pubkey,
    pub lamports: u64,
    pub data: Vec<u8>,
    pub slot: u64,
}

/// Subscribe to `accounts` over a Solana RPC WebSocket, forwarding every
/// change to `tx`. Reconnects (and re-subscribes) with exponential backoff
/// until `cancel` fires; returns only on cancellation or when `tx` closes.
pub async fn subscribe_accounts(
    ws_url: String,
    accounts: Vec<Pubkey>,
    commitment: String,
    tx: mpsc::Sender<AccountUpdate>,
    cancel: CancellationToken,
) {
    let mut backoff = INITIAL_BACKOFF;
    loop {
        if cancel.is_cancelled() || tx.is_closed() {
            break;
        }
        match connect_async(&ws_url).await {
            Ok((ws, _resp)) => {
                info!(%ws_url, accounts = accounts.len(), "solana account stream connected");
                backoff = INITIAL_BACKOFF; // reset once the handshake succeeds
                match run_subscription(ws, &accounts, &commitment, &tx, &cancel).await {
                    Ok(()) => {}
                    Err(e) => warn!(error = %e, "solana account stream ended"),
                }
            }
            Err(e) => warn!(%ws_url, error = %e, "solana ws connect failed"),
        }
        if cancel.is_cancelled() || tx.is_closed() {
            break;
        }
        tokio::select! {
            _ = cancel.cancelled() => break,
            _ = tokio::time::sleep(backoff) => {}
        }
        backoff = next_backoff(backoff);
        warn!(%ws_url, "reconnecting solana account stream");
    }
    info!(%ws_url, "solana account stream stopped");
}

fn subscribe_request(id: u64, pubkey: &Pubkey, commitment: &str) -> String {
    json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": "accountSubscribe",
        "params": [
            pubkey.to_string(),
            { "encoding": "base64", "commitment": commitment }
        ]
    })
    .to_string()
}

type WsStream =
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

async fn run_subscription(
    mut ws: WsStream,
    accounts: &[Pubkey],
    commitment: &str,
    tx: &mpsc::Sender<AccountUpdate>,
    cancel: &CancellationToken,
) -> Result<()> {
    // Send one accountSubscribe per address; remember which request id maps
    // to which pubkey so we can resolve subscription ids from confirmations.
    let mut id_to_pubkey: HashMap<u64, Pubkey> = HashMap::with_capacity(accounts.len());
    for (i, pk) in accounts.iter().enumerate() {
        let id = (i as u64) + 1;
        id_to_pubkey.insert(id, *pk);
        ws.send(Message::Text(subscribe_request(id, pk, commitment)))
            .await
            .map_err(|e| StormError::Rpc(format!("accountSubscribe send: {e}")))?;
    }

    let mut sub_to_pubkey: HashMap<u64, Pubkey> = HashMap::with_capacity(accounts.len());
    loop {
        tokio::select! {
            _ = cancel.cancelled() => {
                let _ = ws.close(None).await;
                return Ok(());
            }
            msg = ws.next() => match msg {
                Some(Ok(Message::Text(txt))) => {
                    if let Some(update) =
                        handle_message(txt.as_str(), &id_to_pubkey, &mut sub_to_pubkey)
                    {
                        if tx.send(update).await.is_err() {
                            return Ok(()); // consumer dropped — stop
                        }
                    }
                }
                Some(Ok(Message::Ping(payload))) => {
                    let _ = ws.send(Message::Pong(payload)).await;
                }
                Some(Ok(Message::Close(_))) | None => {
                    warn!("solana ws closed by peer");
                    return Ok(());
                }
                Some(Ok(_)) => {}
                Some(Err(e)) => {
                    return Err(StormError::Rpc(format!("solana ws read: {e}")));
                }
            }
        }
    }
}

// ---- message parsing ------------------------------------------------------

#[derive(Deserialize)]
struct NotificationParams {
    subscription: u64,
    result: NotificationResult,
}

#[derive(Deserialize)]
struct NotificationResult {
    context: NotificationContext,
    value: AccountValue,
}

#[derive(Deserialize)]
struct NotificationContext {
    slot: u64,
}

#[derive(Deserialize)]
struct AccountValue {
    /// `[base64_payload, "base64"]` when `encoding: base64` is requested.
    data: Vec<String>,
    owner: String,
    lamports: u64,
}

/// Dispatch one incoming text frame. Subscription confirmations populate
/// `sub_to_pubkey`; account notifications produce an [`AccountUpdate`].
fn handle_message(
    text: &str,
    id_to_pubkey: &HashMap<u64, Pubkey>,
    sub_to_pubkey: &mut HashMap<u64, Pubkey>,
) -> Option<AccountUpdate> {
    let value: Value = serde_json::from_str(text).ok()?;

    // Subscription confirmation: {"id": <req id>, "result": <sub id>}
    if value.get("method").is_none() {
        let req_id = value.get("id")?.as_u64()?;
        let sub_id = value.get("result")?.as_u64()?;
        if let Some(pk) = id_to_pubkey.get(&req_id) {
            sub_to_pubkey.insert(sub_id, *pk);
            debug!(req_id, sub_id, pubkey = %pk, "account subscription confirmed");
        }
        return None;
    }

    // Account change notification.
    if value.get("method")?.as_str()? != "accountNotification" {
        return None;
    }
    let params: NotificationParams = serde_json::from_value(value.get("params")?.clone()).ok()?;
    let pubkey = *sub_to_pubkey.get(&params.subscription)?;
    let b64 = params.result.value.data.first()?;
    let data = BASE64_STANDARD.decode(b64).ok()?;
    let owner = Pubkey::from_str(&params.result.value.owner).ok()?;
    Some(AccountUpdate {
        pubkey,
        owner,
        lamports: params.result.value.lamports,
        data,
        slot: params.result.context.slot,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn subscribe_request_shape() {
        let pk = Pubkey::new_unique();
        let req = subscribe_request(7, &pk, "confirmed");
        let v: Value = serde_json::from_str(&req).unwrap();
        assert_eq!(v["method"], "accountSubscribe");
        assert_eq!(v["id"], 7);
        assert_eq!(v["params"][0], pk.to_string());
        assert_eq!(v["params"][1]["encoding"], "base64");
        assert_eq!(v["params"][1]["commitment"], "confirmed");
    }

    #[test]
    fn confirmation_then_notification_yields_an_update() {
        let pk = Pubkey::new_unique();
        let mut id_to_pubkey = HashMap::new();
        id_to_pubkey.insert(1u64, pk);
        let mut sub_to_pubkey = HashMap::new();

        // Confirmation maps request id 1 -> subscription id 24.
        let confirm = r#"{"jsonrpc":"2.0","result":24,"id":1}"#;
        assert!(handle_message(confirm, &id_to_pubkey, &mut sub_to_pubkey).is_none());
        assert_eq!(sub_to_pubkey.get(&24), Some(&pk));

        // base64 of bytes [1, 2, 3] is "AQID".
        let notif = r#"{"jsonrpc":"2.0","method":"accountNotification","params":{"result":{"context":{"slot":12345},"value":{"data":["AQID","base64"],"executable":false,"lamports":777,"owner":"11111111111111111111111111111111","rentEpoch":0,"space":3}},"subscription":24}}"#;
        let update = handle_message(notif, &id_to_pubkey, &mut sub_to_pubkey).unwrap();
        assert_eq!(update.pubkey, pk);
        assert_eq!(update.data, vec![1, 2, 3]);
        assert_eq!(update.lamports, 777);
        assert_eq!(update.slot, 12345);
    }

    #[test]
    fn notification_for_unknown_subscription_is_ignored() {
        let id_to_pubkey = HashMap::new();
        let mut sub_to_pubkey = HashMap::new();
        let notif = r#"{"jsonrpc":"2.0","method":"accountNotification","params":{"result":{"context":{"slot":1},"value":{"data":["","base64"],"executable":false,"lamports":0,"owner":"11111111111111111111111111111111","rentEpoch":0,"space":0}},"subscription":999}}"#;
        assert!(handle_message(notif, &id_to_pubkey, &mut sub_to_pubkey).is_none());
    }

    #[test]
    fn garbage_frame_is_ignored() {
        let id_to_pubkey = HashMap::new();
        let mut sub_to_pubkey = HashMap::new();
        assert!(handle_message("not json", &id_to_pubkey, &mut sub_to_pubkey).is_none());
    }
}
