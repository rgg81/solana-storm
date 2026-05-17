//! Async RPC orchestration — the only module that touches the network.
//!
//! Pure conversion helpers (RPC response types → plain feature inputs) are
//! unit-tested here. The async fetch functions issue exactly one RPC call
//! each and are exercised by the `#[ignore]`-d integration test.

use std::str::FromStr;

use solana_client::rpc_client::GetConfirmedSignaturesForAddress2Config;
use solana_client::rpc_response::{
    RpcConfirmedTransactionStatusWithSignature, RpcTokenAccountBalance,
};
use solana_sdk::pubkey::Pubkey;
use storm_core::{Result, StormError};
use storm_pumpfun::{bonding_curve_pda, BondingCurve, PumpSwapPool};
use storm_solana::{MintInfo, RpcContext, TokenAccountSnapshot};

use crate::deployer::{SignaturePage, SIGNATURE_PAGE_LIMIT};
use crate::holders::HolderBalance;

/// Convert one `getTokenLargestAccounts` entry into a plain [`HolderBalance`].
///
/// `RpcTokenAccountBalance.amount.amount` is the raw integer balance encoded
/// as a decimal string; `.address` is the token-account address.
pub(crate) fn holder_balance_from_rpc(entry: &RpcTokenAccountBalance) -> Result<HolderBalance> {
    let address = Pubkey::from_str(&entry.address).map_err(|e| {
        StormError::Parse(format!("invalid holder address '{}': {e}", entry.address))
    })?;
    let amount = u64::from_str(&entry.amount.amount).map_err(|e| {
        StormError::Parse(format!(
            "invalid holder amount '{}': {e}",
            entry.amount.amount
        ))
    })?;
    Ok(HolderBalance { address, amount })
}

/// Convert a full `getSignaturesForAddress` page into a [`SignaturePage`].
///
/// The page is newest-first, so the oldest signature is the last element.
pub(crate) fn signature_page_from_rpc(
    sigs: &[RpcConfirmedTransactionStatusWithSignature],
) -> SignaturePage {
    SignaturePage {
        signature_count: sigs.len(),
        oldest_block_time: sigs.last().and_then(|s| s.block_time),
    }
}

/// Fetch the SPL `Mint` account for `mint`.
pub async fn fetch_mint(rpc: &RpcContext, mint: &Pubkey) -> Result<MintInfo> {
    rpc.fetch_mint(mint).await
}

/// Fetch and parse a PumpSwap `Pool` account at `pool`.
pub async fn fetch_pool(rpc: &RpcContext, pool: &Pubkey) -> Result<PumpSwapPool> {
    let acct = rpc
        .rpc()
        .get_account_with_commitment(pool, rpc.commitment())
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?
        .value
        .ok_or_else(|| StormError::Parse(format!("pumpswap pool {pool} not found")))?;
    PumpSwapPool::unpack(&acct.data)
}

/// Fetch and parse the bonding-curve account for `mint`.
pub async fn fetch_bonding_curve(rpc: &RpcContext, mint: &Pubkey) -> Result<BondingCurve> {
    let pda = bonding_curve_pda(mint);
    let acct = rpc
        .rpc()
        .get_account_with_commitment(&pda, rpc.commitment())
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?
        .value
        .ok_or_else(|| StormError::Parse(format!("bonding curve {pda} not found")))?;
    BondingCurve::unpack(&acct.data)
}

/// Fetch the raw token balance held by an SPL token account. Returns `0` if
/// the account does not exist (e.g. the creator never held the token).
pub async fn fetch_token_account_amount(rpc: &RpcContext, token_account: &Pubkey) -> Result<u64> {
    let maybe = rpc
        .rpc()
        .get_account_with_commitment(token_account, rpc.commitment())
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?
        .value;
    match maybe {
        None => Ok(0),
        Some(acct) => Ok(TokenAccountSnapshot::unpack(*token_account, &acct.data)?.amount),
    }
}

/// Fetch the top-20 holder list for `mint` via `getTokenLargestAccounts`
/// (one RPC call) and convert it to plain [`HolderBalance`] entries.
pub async fn fetch_top_holders(rpc: &RpcContext, mint: &Pubkey) -> Result<Vec<HolderBalance>> {
    let raw = rpc
        .rpc()
        .get_token_largest_accounts(mint)
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?;
    raw.iter().map(holder_balance_from_rpc).collect()
}

/// Fetch a single bounded `getSignaturesForAddress` page for `wallet` and
/// summarise it. One RPC call; never a full-history crawl.
pub async fn fetch_signature_page(rpc: &RpcContext, wallet: &Pubkey) -> Result<SignaturePage> {
    let config = GetConfirmedSignaturesForAddress2Config {
        before: None,
        until: None,
        limit: Some(SIGNATURE_PAGE_LIMIT),
        commitment: Some(rpc.commitment()),
    };
    let sigs = rpc
        .rpc()
        .get_signatures_for_address_with_config(wallet, config)
        .await
        .map_err(|e| StormError::Rpc(e.to_string()))?;
    Ok(signature_page_from_rpc(&sigs))
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_account_decoder_client_types::token::UiTokenAmount;

    fn ui_amount(raw: &str) -> UiTokenAmount {
        UiTokenAmount {
            ui_amount: None,
            decimals: 6,
            amount: raw.to_string(),
            ui_amount_string: String::new(),
        }
    }

    #[test]
    fn holder_balance_parses_address_and_amount() {
        let entry = RpcTokenAccountBalance {
            address: "So11111111111111111111111111111111111111112".to_string(),
            amount: ui_amount("123456789"),
        };
        let hb = holder_balance_from_rpc(&entry).unwrap();
        assert_eq!(hb.amount, 123_456_789);
        assert_eq!(
            hb.address,
            Pubkey::from_str("So11111111111111111111111111111111111111112").unwrap()
        );
    }

    #[test]
    fn holder_balance_rejects_bad_address() {
        let entry = RpcTokenAccountBalance {
            address: "not-a-pubkey".to_string(),
            amount: ui_amount("1"),
        };
        match holder_balance_from_rpc(&entry) {
            Err(StormError::Parse(m)) => assert!(m.contains("holder address")),
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn holder_balance_rejects_bad_amount() {
        let entry = RpcTokenAccountBalance {
            address: "So11111111111111111111111111111111111111112".to_string(),
            amount: ui_amount("not-a-number"),
        };
        match holder_balance_from_rpc(&entry) {
            Err(StormError::Parse(m)) => assert!(m.contains("holder amount")),
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    fn sig(slot: u64, block_time: Option<i64>) -> RpcConfirmedTransactionStatusWithSignature {
        RpcConfirmedTransactionStatusWithSignature {
            signature: format!("sig{slot}"),
            slot,
            err: None,
            memo: None,
            block_time,
            confirmation_status: None,
        }
    }

    #[test]
    fn signature_page_counts_and_takes_oldest_block_time() {
        // Newest-first: index 0 is newest, the last element is oldest.
        let sigs = vec![
            sig(300, Some(3000)),
            sig(200, Some(2000)),
            sig(100, Some(1000)),
        ];
        let page = signature_page_from_rpc(&sigs);
        assert_eq!(page.signature_count, 3);
        assert_eq!(page.oldest_block_time, Some(1000));
    }

    #[test]
    fn signature_page_handles_empty_input() {
        let page = signature_page_from_rpc(&[]);
        assert_eq!(page.signature_count, 0);
        assert_eq!(page.oldest_block_time, None);
    }

    #[test]
    fn signature_page_handles_missing_oldest_block_time() {
        let sigs = vec![sig(200, Some(2000)), sig(100, None)];
        let page = signature_page_from_rpc(&sigs);
        assert_eq!(page.signature_count, 2);
        assert_eq!(page.oldest_block_time, None);
    }
}
