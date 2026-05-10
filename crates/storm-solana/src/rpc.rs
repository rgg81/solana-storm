use std::str::FromStr;

use solana_client::nonblocking::rpc_client::RpcClient;
use solana_sdk::{account::Account, commitment_config::CommitmentConfig, pubkey::Pubkey};
use storm_core::{Result, SolanaConfig, StormError};

pub struct RpcContext {
    client: RpcClient,
    commitment: CommitmentConfig,
}

impl RpcContext {
    pub fn rpc(&self) -> &RpcClient {
        &self.client
    }

    pub fn commitment(&self) -> CommitmentConfig {
        self.commitment
    }
}

#[derive(Debug, Clone)]
pub struct AccountSnapshot {
    pub address: Pubkey,
    pub lamports: u64,
    pub owner: Pubkey,
    pub executable: bool,
    pub data_len: usize,
    pub rent_epoch: u64,
    pub slot: u64,
}

impl RpcContext {
    pub fn from_config(cfg: &SolanaConfig) -> Self {
        let commitment = parse_commitment(&cfg.commitment);
        Self {
            client: RpcClient::new_with_commitment(cfg.rpc_url.clone(), commitment),
            commitment,
        }
    }

    pub async fn fetch_account(&self, address: &str) -> Result<AccountSnapshot> {
        let pubkey = Pubkey::from_str(address)
            .map_err(|e| StormError::Parse(format!("invalid pubkey '{address}': {e}")))?;

        let resp = self
            .client
            .get_account_with_commitment(&pubkey, self.commitment)
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?;

        let slot = resp.context.slot;
        let account: Account = resp.value.ok_or_else(|| {
            StormError::Parse(format!("account {pubkey} not found at slot {slot}"))
        })?;

        Ok(AccountSnapshot {
            address: pubkey,
            lamports: account.lamports,
            owner: account.owner,
            executable: account.executable,
            data_len: account.data.len(),
            rent_epoch: account.rent_epoch,
            slot,
        })
    }
}

fn parse_commitment(s: &str) -> CommitmentConfig {
    match s {
        "processed" => CommitmentConfig::processed(),
        "finalized" => CommitmentConfig::finalized(),
        _ => CommitmentConfig::confirmed(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn commitment_strings_round_trip() {
        assert_eq!(parse_commitment("processed"), CommitmentConfig::processed());
        assert_eq!(parse_commitment("confirmed"), CommitmentConfig::confirmed());
        assert_eq!(parse_commitment("finalized"), CommitmentConfig::finalized());
        assert_eq!(parse_commitment("garbage"), CommitmentConfig::confirmed());
    }
}
