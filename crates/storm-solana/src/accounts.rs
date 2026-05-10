use std::collections::HashMap;
use std::str::FromStr;

use solana_sdk::{program_pack::Pack, pubkey::Pubkey};
use storm_core::{Result, StormError, Token, TokenAmount};

use crate::rpc::RpcContext;

#[derive(Debug, Clone)]
pub struct MintInfo {
    pub address: Pubkey,
    pub decimals: u8,
    pub supply: u64,
    pub mint_authority: Option<Pubkey>,
    pub freeze_authority: Option<Pubkey>,
}

impl MintInfo {
    pub fn unpack(address: Pubkey, data: &[u8]) -> Result<Self> {
        let m = spl_token::state::Mint::unpack(data)
            .map_err(|e| StormError::Parse(format!("unpack mint {address}: {e}")))?;
        Ok(Self {
            address,
            decimals: m.decimals,
            supply: m.supply,
            mint_authority: Option::<Pubkey>::from(m.mint_authority),
            freeze_authority: Option::<Pubkey>::from(m.freeze_authority),
        })
    }
}

#[derive(Debug, Clone)]
pub struct TokenAccountSnapshot {
    pub address: Pubkey,
    pub mint: Pubkey,
    pub owner: Pubkey,
    pub amount: u64,
}

impl TokenAccountSnapshot {
    pub fn unpack(address: Pubkey, data: &[u8]) -> Result<Self> {
        let a = spl_token::state::Account::unpack(data)
            .map_err(|e| StormError::Parse(format!("unpack token account {address}: {e}")))?;
        Ok(Self {
            address,
            mint: a.mint,
            owner: a.owner,
            amount: a.amount,
        })
    }
}

#[derive(Debug, Clone)]
pub struct PortfolioEntry {
    pub token_account: Pubkey,
    pub wallet: Pubkey,
    pub holding: TokenAmount,
}

impl RpcContext {
    pub async fn fetch_mint(&self, address: &Pubkey) -> Result<MintInfo> {
        let acct = self
            .rpc()
            .get_account_with_commitment(address, self.commitment())
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?
            .value
            .ok_or_else(|| StormError::Parse(format!("mint {address} not found")))?;
        MintInfo::unpack(*address, &acct.data)
    }

    pub async fn fetch_portfolio(&self, owner: &Pubkey) -> Result<Vec<PortfolioEntry>> {
        use solana_client::rpc_request::TokenAccountsFilter;

        let keyed = self
            .rpc()
            .get_token_accounts_by_owner(owner, TokenAccountsFilter::ProgramId(spl_token::id()))
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?;

        if keyed.is_empty() {
            return Ok(vec![]);
        }

        let ta_addrs: Vec<Pubkey> = keyed
            .iter()
            .map(|k| {
                Pubkey::from_str(&k.pubkey).map_err(|e| {
                    StormError::Parse(format!("invalid token-account pubkey '{}': {e}", k.pubkey))
                })
            })
            .collect::<Result<_>>()?;

        let raw_tas = self
            .rpc()
            .get_multiple_accounts(&ta_addrs)
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?;

        let mut snapshots = Vec::with_capacity(ta_addrs.len());
        for (addr, maybe) in ta_addrs.iter().zip(raw_tas.iter()) {
            let acct = maybe
                .as_ref()
                .ok_or_else(|| StormError::Parse(format!("token account {addr} vanished")))?;
            snapshots.push(TokenAccountSnapshot::unpack(*addr, &acct.data)?);
        }

        let mut unique_mints: Vec<Pubkey> = snapshots.iter().map(|s| s.mint).collect();
        unique_mints.sort();
        unique_mints.dedup();

        let raw_mints = self
            .rpc()
            .get_multiple_accounts(&unique_mints)
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?;

        let mut decimals_by_mint: HashMap<Pubkey, u8> = HashMap::with_capacity(unique_mints.len());
        for (addr, maybe) in unique_mints.iter().zip(raw_mints.iter()) {
            let acct = maybe
                .as_ref()
                .ok_or_else(|| StormError::Parse(format!("mint {addr} not found")))?;
            let m = MintInfo::unpack(*addr, &acct.data)?;
            decimals_by_mint.insert(*addr, m.decimals);
        }

        let portfolio = snapshots
            .into_iter()
            .map(|s| {
                let decimals = decimals_by_mint.get(&s.mint).copied().unwrap_or(0);
                let token = Token::new(s.mint, decimals);
                PortfolioEntry {
                    token_account: s.address,
                    wallet: s.owner,
                    holding: TokenAmount::new(token, s.amount),
                }
            })
            .collect();

        Ok(portfolio)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // USDC mint snapshot captured live (82 bytes).
    // Layout: COption(authority) | supply u64 LE | decimals u8 | initialized u8 |
    // COption(freeze authority). Byte 44 is `decimals = 6`.
    const USDC_MINT_BYTES: [u8; 82] = [
        0x01, 0x00, 0x00, 0x00, 0x98, 0xfe, 0x86, 0xe8, 0x8d, 0x9b, 0xe2, 0xea, 0x8b, 0xc1, 0xcc,
        0xa4, 0x87, 0x8b, 0x29, 0x88, 0xc2, 0x40, 0xf5, 0x2b, 0x84, 0x24, 0xbf, 0xb4, 0x0e, 0xd1,
        0xa2, 0xdd, 0xcb, 0x5e, 0x19, 0x9b, 0x56, 0x33, 0xcc, 0x43, 0xce, 0xf3, 0x1f, 0x00, 0x06,
        0x01, 0x01, 0x00, 0x00, 0x00, 0x62, 0x70, 0xaa, 0x8a, 0x59, 0xc5, 0x94, 0x05, 0xb4, 0x52,
        0x86, 0xc8, 0x67, 0x72, 0xe6, 0xcd, 0x12, 0x6e, 0x9b, 0x8a, 0x5d, 0x3a, 0x38, 0x53, 0x6d,
        0x37, 0xf7, 0xb4, 0x14, 0xe8, 0xb6, 0x67,
    ];

    fn usdc_mint() -> Pubkey {
        Pubkey::from_str("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").unwrap()
    }

    #[test]
    fn unpacks_real_usdc_mint() {
        let info = MintInfo::unpack(usdc_mint(), &USDC_MINT_BYTES).unwrap();
        assert_eq!(info.decimals, 6);
        assert!(info.mint_authority.is_some());
        assert!(info.freeze_authority.is_some());
        // Sanity: supply is well above 1 billion units (USDC has billions in circulation).
        assert!(info.supply > 1_000_000_000_000);
    }

    #[test]
    fn rejects_truncated_mint() {
        let err = MintInfo::unpack(usdc_mint(), &USDC_MINT_BYTES[..50]).unwrap_err();
        match err {
            StormError::Parse(msg) => assert!(msg.contains("unpack mint")),
            other => panic!("expected Parse, got {other:?}"),
        }
    }
}
