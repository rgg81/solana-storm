use rust_decimal::Decimal;
use solana_sdk::pubkey::Pubkey;
use storm_core::{cpmm_swap_output, spot_price, Result, StormError, Token, TokenAmount};

use crate::accounts::TokenAccountSnapshot;
use crate::rpc::RpcContext;

/// Read-side abstraction over a two-token DEX pool.
///
/// Object-safe: callers can hold a `Box<dyn DexPool>` to mix Raydium,
/// Orca, etc. in a single collection.
pub trait DexPool {
    fn pool_address(&self) -> &Pubkey;
    fn program_id(&self) -> &Pubkey;
    fn token_a(&self) -> &Token;
    fn token_b(&self) -> &Token;
    fn reserves(&self) -> (TokenAmount, TokenAmount);
    /// Decimals-adjusted spot price of A in terms of B (B per A).
    fn price(&self) -> Decimal;
    /// Output amount for a given input. Errors if `input.token.mint`
    /// isn't either side of the pool.
    fn calculate_swap_output(&self, input: &TokenAmount) -> Result<TokenAmount>;
}

// ---------------------------------------------------------------------------
// Raydium AMM v4
// ---------------------------------------------------------------------------

pub const RAYDIUM_AMM_V4_PROGRAM_ID: Pubkey =
    solana_sdk::pubkey!("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8");

/// Fixed-offset view into Raydium's `LiquidityStateV4` (752 bytes).
/// Only the fields we actually use are extracted.
#[derive(Debug, Clone)]
pub struct RaydiumPoolState {
    pub base_decimals: u8,
    pub quote_decimals: u8,
    pub swap_fee_numerator: u64,
    pub swap_fee_denominator: u64,
    /// Pending fee / PnL owed to LPs, sitting inside the vault but not
    /// part of the swappable reserve.
    pub base_need_take_pnl: u64,
    pub quote_need_take_pnl: u64,
    pub base_vault: Pubkey,
    pub quote_vault: Pubkey,
    pub base_mint: Pubkey,
    pub quote_mint: Pubkey,
}

impl RaydiumPoolState {
    pub const SIZE: usize = 752;

    pub fn unpack(data: &[u8]) -> Result<Self> {
        if data.len() != Self::SIZE {
            return Err(StormError::Parse(format!(
                "raydium AMM v4: expected {} bytes, got {}",
                Self::SIZE,
                data.len()
            )));
        }
        Ok(Self {
            base_decimals: read_u64(data, 32) as u8,
            quote_decimals: read_u64(data, 40) as u8,
            swap_fee_numerator: read_u64(data, 176),
            swap_fee_denominator: read_u64(data, 184),
            base_need_take_pnl: read_u64(data, 192),
            quote_need_take_pnl: read_u64(data, 200),
            base_vault: read_pubkey(data, 336),
            quote_vault: read_pubkey(data, 368),
            base_mint: read_pubkey(data, 400),
            quote_mint: read_pubkey(data, 432),
        })
    }
}

fn read_u64(data: &[u8], offset: usize) -> u64 {
    u64::from_le_bytes(data[offset..offset + 8].try_into().unwrap())
}

fn read_pubkey(data: &[u8], offset: usize) -> Pubkey {
    Pubkey::try_from(&data[offset..offset + 32]).unwrap()
}

#[derive(Debug, Clone)]
pub struct RaydiumPool {
    pub address: Pubkey,
    pub program_id: Pubkey,
    pub state: RaydiumPoolState,
    token_a: Token,
    token_b: Token,
    base_reserve: u64,
    quote_reserve: u64,
}

impl RaydiumPool {
    pub fn new(
        address: Pubkey,
        program_id: Pubkey,
        state: RaydiumPoolState,
        base_vault_amount: u64,
        quote_vault_amount: u64,
    ) -> Self {
        // Swappable reserve = vault amount minus the PnL owed to LPs
        // that still sits in the vault.
        let base_reserve = base_vault_amount.saturating_sub(state.base_need_take_pnl);
        let quote_reserve = quote_vault_amount.saturating_sub(state.quote_need_take_pnl);
        let token_a = Token::new(state.base_mint, state.base_decimals);
        let token_b = Token::new(state.quote_mint, state.quote_decimals);
        Self {
            address,
            program_id,
            state,
            token_a,
            token_b,
            base_reserve,
            quote_reserve,
        }
    }

    pub fn base_reserve_raw(&self) -> u64 {
        self.base_reserve
    }
    pub fn quote_reserve_raw(&self) -> u64 {
        self.quote_reserve
    }
}

impl DexPool for RaydiumPool {
    fn pool_address(&self) -> &Pubkey {
        &self.address
    }
    fn program_id(&self) -> &Pubkey {
        &self.program_id
    }
    fn token_a(&self) -> &Token {
        &self.token_a
    }
    fn token_b(&self) -> &Token {
        &self.token_b
    }
    fn reserves(&self) -> (TokenAmount, TokenAmount) {
        (
            TokenAmount::new(self.token_a.clone(), self.base_reserve),
            TokenAmount::new(self.token_b.clone(), self.quote_reserve),
        )
    }
    fn price(&self) -> Decimal {
        spot_price(
            self.base_reserve,
            self.quote_reserve,
            self.token_a.decimals,
            self.token_b.decimals,
        )
        .unwrap_or(Decimal::ZERO)
    }
    fn calculate_swap_output(&self, input: &TokenAmount) -> Result<TokenAmount> {
        let (r_in, r_out, out_token) = if input.token.mint == self.token_a.mint {
            (self.base_reserve, self.quote_reserve, &self.token_b)
        } else if input.token.mint == self.token_b.mint {
            (self.quote_reserve, self.base_reserve, &self.token_a)
        } else {
            return Err(StormError::Parse(format!(
                "input mint {} not in pool {}",
                input.token.mint, self.address
            )));
        };
        let out = cpmm_swap_output(
            r_in,
            r_out,
            input.raw,
            self.state.swap_fee_numerator,
            self.state.swap_fee_denominator,
        )
        .ok_or_else(|| StormError::Parse(format!("swap math overflow on pool {}", self.address)))?;
        Ok(TokenAmount::new(out_token.clone(), out))
    }
}

// ---------------------------------------------------------------------------
// RPC plumbing
// ---------------------------------------------------------------------------

impl RpcContext {
    /// Fetch a Raydium AMM v4 pool: the pool state account, then both
    /// vaults in a single batched call (2 RPCs total).
    pub async fn fetch_raydium_pool(&self, address: &Pubkey) -> Result<RaydiumPool> {
        let pool_account = self
            .rpc()
            .get_account_with_commitment(address, self.commitment())
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?
            .value
            .ok_or_else(|| StormError::Parse(format!("pool {address} not found")))?;
        if pool_account.owner != RAYDIUM_AMM_V4_PROGRAM_ID {
            return Err(StormError::Parse(format!(
                "account {address} is not owned by Raydium AMM v4 (owner: {})",
                pool_account.owner
            )));
        }
        let state = RaydiumPoolState::unpack(&pool_account.data)?;
        let vaults = self
            .rpc()
            .get_multiple_accounts(&[state.base_vault, state.quote_vault])
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?;
        let base_acct = vaults[0].as_ref().ok_or_else(|| {
            StormError::Parse(format!("base vault {} vanished", state.base_vault))
        })?;
        let quote_acct = vaults[1].as_ref().ok_or_else(|| {
            StormError::Parse(format!("quote vault {} vanished", state.quote_vault))
        })?;
        let base = TokenAccountSnapshot::unpack(state.base_vault, &base_acct.data)?;
        let quote = TokenAccountSnapshot::unpack(state.quote_vault, &quote_acct.data)?;
        Ok(RaydiumPool::new(
            *address,
            pool_account.owner,
            state,
            base.amount,
            quote.amount,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use storm_core::Token;

    fn synth_pool_bytes(base_mint: &Pubkey, quote_mint: &Pubkey) -> [u8; 752] {
        let mut data = [0u8; 752];
        data[32..40].copy_from_slice(&9u64.to_le_bytes()); // base_decimals = 9 (SOL)
        data[40..48].copy_from_slice(&6u64.to_le_bytes()); // quote_decimals = 6 (USDC)
        data[176..184].copy_from_slice(&25u64.to_le_bytes()); // 25 bps fee num
        data[184..192].copy_from_slice(&10_000u64.to_le_bytes()); // fee den
                                                                  // base_need_take_pnl / quote_need_take_pnl left at 0
        data[400..432].copy_from_slice(base_mint.as_ref());
        data[432..464].copy_from_slice(quote_mint.as_ref());
        data
    }

    #[test]
    fn raydium_v4_layout_size_is_752() {
        assert_eq!(RaydiumPoolState::SIZE, 752);
    }

    #[test]
    fn unpack_rejects_wrong_size() {
        match RaydiumPoolState::unpack(&[0u8; 100]) {
            Err(StormError::Parse(m)) => assert!(m.contains("expected 752 bytes")),
            other => panic!("expected Parse error, got {other:?}"),
        }
    }

    #[test]
    fn price_and_swap_match_textbook_for_sol_usdc_synthetic() {
        let bm = Pubkey::new_unique();
        let qm = Pubkey::new_unique();
        let data = synth_pool_bytes(&bm, &qm);
        let state = RaydiumPoolState::unpack(&data).unwrap();
        let pool = RaydiumPool::new(
            Pubkey::new_unique(),
            RAYDIUM_AMM_V4_PROGRAM_ID,
            state,
            1_000_000_000_000, // 1000 SOL
            142_000_000_000,   // 142 000 USDC
        );

        // Spot price 142 USDC per SOL.
        assert_eq!(pool.price().normalize().to_string(), "142");

        // Swap 1 SOL → between 141 and 142 USDC (25 bps fee).
        let one_sol = TokenAmount::new(pool.token_a().clone(), 1_000_000_000);
        let out = pool.calculate_swap_output(&one_sol).unwrap();
        assert_eq!(out.token.mint, qm);
        let ui = out.ui_amount();
        assert!(ui > Decimal::from(141), "got {ui}");
        assert!(ui < Decimal::from(142), "got {ui}");
    }

    #[test]
    fn swap_routes_by_mint_in_both_directions() {
        let bm = Pubkey::new_unique();
        let qm = Pubkey::new_unique();
        let data = synth_pool_bytes(&bm, &qm);
        let state = RaydiumPoolState::unpack(&data).unwrap();
        let pool = RaydiumPool::new(
            Pubkey::new_unique(),
            RAYDIUM_AMM_V4_PROGRAM_ID,
            state,
            1_000_000_000_000,
            142_000_000_000,
        );

        let in_a = TokenAmount::new(pool.token_a().clone(), 1_000_000_000);
        let out_b = pool.calculate_swap_output(&in_a).unwrap();
        assert_eq!(out_b.token.mint, qm);

        let in_b = TokenAmount::new(pool.token_b().clone(), 100_000_000);
        let out_a = pool.calculate_swap_output(&in_b).unwrap();
        assert_eq!(out_a.token.mint, bm);
    }

    #[test]
    fn swap_rejects_foreign_mint() {
        let bm = Pubkey::new_unique();
        let qm = Pubkey::new_unique();
        let data = synth_pool_bytes(&bm, &qm);
        let state = RaydiumPoolState::unpack(&data).unwrap();
        let pool = RaydiumPool::new(
            Pubkey::new_unique(),
            RAYDIUM_AMM_V4_PROGRAM_ID,
            state,
            100,
            100,
        );
        let foreign = TokenAmount::new(Token::new(Pubkey::new_unique(), 6), 1);
        assert!(matches!(
            pool.calculate_swap_output(&foreign),
            Err(StormError::Parse(_))
        ));
    }

    #[test]
    fn pnl_reduces_reserves() {
        let bm = Pubkey::new_unique();
        let qm = Pubkey::new_unique();
        let mut data = synth_pool_bytes(&bm, &qm);
        data[192..200].copy_from_slice(&1_000_000_000u64.to_le_bytes()); // 1 SOL PnL owed
        let state = RaydiumPoolState::unpack(&data).unwrap();
        let pool = RaydiumPool::new(
            Pubkey::new_unique(),
            RAYDIUM_AMM_V4_PROGRAM_ID,
            state,
            10_000_000_000,
            1_420_000_000,
        );
        // Vault held 10 SOL but 1 SOL is PnL → reserve is 9 SOL.
        assert_eq!(pool.base_reserve_raw(), 9_000_000_000);
    }
}
