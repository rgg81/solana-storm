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

// ---------------------------------------------------------------------------
// Orca Whirlpool (concentrated liquidity, Anchor account layout)
// ---------------------------------------------------------------------------

pub const ORCA_WHIRLPOOL_PROGRAM_ID: Pubkey =
    solana_sdk::pubkey!("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc");

/// Anchor 8-byte discriminator for the `Whirlpool` account type.
/// `sha256("account:Whirlpool")[..8]` (little-endian as stored on-chain).
const WHIRLPOOL_DISCRIMINATOR: [u8; 8] = [63, 149, 209, 12, 225, 128, 99, 9];

/// `2^64` as a `Decimal`. Used to convert `sqrt_price_x64` (Q64.64
/// fixed-point) into a regular fraction.
fn two_pow_64() -> Decimal {
    // 18_446_744_073_709_551_616 fits in `i128` (well under `i128::MAX`).
    Decimal::from_i128_with_scale(1 << 32, 0) * Decimal::from_i128_with_scale(1 << 32, 0)
}

/// Convert `sqrt_price_x64` and the two side decimals into a
/// decimals-normalised spot price of A in terms of B.
///
/// `raw_price = sqrt_price_x64² / 2^128`
/// `ui_price  = raw_price × 10^(decimals_a - decimals_b)`
pub fn sqrt_price_x64_to_price(sqrt_price_x64: u128, decimals_a: u8, decimals_b: u8) -> Decimal {
    // sqrt_price_x64 fits in i128 for every legal Whirlpool value
    // (MAX_SQRT_PRICE_X64 ≈ 7.9e28, i128::MAX ≈ 1.7e38).
    let sp = Decimal::from_i128_with_scale(sqrt_price_x64 as i128, 0) / two_pow_64();
    let raw = sp * sp;
    let exp = decimals_a as i32 - decimals_b as i32;
    if exp >= 0 {
        raw * Decimal::from(10u64.pow(exp as u32))
    } else {
        raw / Decimal::from(10u64.pow((-exp) as u32))
    }
}

/// Fixed-offset view into Orca's `Whirlpool` account (653 bytes
/// including the 8-byte Anchor discriminator). Only the fields we need
/// are extracted. Reward arrays at the tail are skipped.
#[derive(Debug, Clone)]
pub struct WhirlpoolState {
    pub tick_spacing: u16,
    /// Fee rate in hundredths of a basis point (10 000 = 1 %).
    pub fee_rate: u16,
    pub liquidity: u128,
    pub sqrt_price_x64: u128,
    pub tick_current_index: i32,
    pub token_mint_a: Pubkey,
    pub token_vault_a: Pubkey,
    pub token_mint_b: Pubkey,
    pub token_vault_b: Pubkey,
}

impl WhirlpoolState {
    pub const SIZE: usize = 653;
    pub const DISCRIMINATOR: [u8; 8] = WHIRLPOOL_DISCRIMINATOR;

    pub fn unpack(data: &[u8]) -> Result<Self> {
        if data.len() != Self::SIZE {
            return Err(StormError::Parse(format!(
                "orca whirlpool: expected {} bytes, got {}",
                Self::SIZE,
                data.len()
            )));
        }
        if data[..8] != Self::DISCRIMINATOR {
            return Err(StormError::Parse(format!(
                "orca whirlpool: wrong account discriminator (got {:?})",
                &data[..8]
            )));
        }
        Ok(Self {
            tick_spacing: u16::from_le_bytes(data[41..43].try_into().unwrap()),
            fee_rate: u16::from_le_bytes(data[45..47].try_into().unwrap()),
            liquidity: u128::from_le_bytes(data[49..65].try_into().unwrap()),
            sqrt_price_x64: u128::from_le_bytes(data[65..81].try_into().unwrap()),
            tick_current_index: i32::from_le_bytes(data[81..85].try_into().unwrap()),
            token_mint_a: read_pubkey(data, 101),
            token_vault_a: read_pubkey(data, 133),
            token_mint_b: read_pubkey(data, 181),
            token_vault_b: read_pubkey(data, 213),
        })
    }
}

#[derive(Debug, Clone)]
pub struct OrcaWhirlpool {
    pub address: Pubkey,
    pub program_id: Pubkey,
    pub state: WhirlpoolState,
    /// Each side's mint info, used for decimals.
    token_a: Token,
    token_b: Token,
    /// Total amount sitting in the protocol vaults — across *all* tick
    /// ranges, not just the active one. Useful for display and for
    /// upper-bound reasoning about possible swap size.
    vault_a_amount: u64,
    vault_b_amount: u64,
}

impl OrcaWhirlpool {
    pub fn new(
        address: Pubkey,
        program_id: Pubkey,
        state: WhirlpoolState,
        decimals_a: u8,
        decimals_b: u8,
        vault_a_amount: u64,
        vault_b_amount: u64,
    ) -> Self {
        let token_a = Token::new(state.token_mint_a, decimals_a);
        let token_b = Token::new(state.token_mint_b, decimals_b);
        Self {
            address,
            program_id,
            state,
            token_a,
            token_b,
            vault_a_amount,
            vault_b_amount,
        }
    }

    pub fn vault_a_amount(&self) -> u64 {
        self.vault_a_amount
    }
    pub fn vault_b_amount(&self) -> u64 {
        self.vault_b_amount
    }

    /// Effective in-tick reserves derived from `(L, sqrt_price)`. Inside
    /// a single tick range a CLMM behaves like a CPMM with these
    /// notional reserves, so this is what to use for sample-swap math.
    fn active_range_reserves(&self) -> (u64, u64) {
        let l = Decimal::from_i128_with_scale(self.state.liquidity as i128, 0);
        let sp = Decimal::from_i128_with_scale(self.state.sqrt_price_x64 as i128, 0) / two_pow_64();
        if sp.is_zero() {
            return (0, 0);
        }
        let r_a = (l / sp).floor();
        let r_b = (l * sp).floor();
        let cap = Decimal::from(u64::MAX);
        let r_a = if r_a > cap { cap } else { r_a };
        let r_b = if r_b > cap { cap } else { r_b };
        // Decimal -> u64 via i128 (safe: we capped above).
        use rust_decimal::prelude::ToPrimitive;
        (r_a.to_u64().unwrap_or(0), r_b.to_u64().unwrap_or(0))
    }
}

impl DexPool for OrcaWhirlpool {
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
        // Vault totals — the *full* protocol holdings, including
        // out-of-range liquidity. For "what's in this pool right now"
        // this is the meaningful number; for swap math we use the
        // effective in-range reserves derived from sqrt_price + L.
        (
            TokenAmount::new(self.token_a.clone(), self.vault_a_amount),
            TokenAmount::new(self.token_b.clone(), self.vault_b_amount),
        )
    }
    fn price(&self) -> Decimal {
        sqrt_price_x64_to_price(
            self.state.sqrt_price_x64,
            self.token_a.decimals,
            self.token_b.decimals,
        )
    }
    fn calculate_swap_output(&self, input: &TokenAmount) -> Result<TokenAmount> {
        // CPMM approximation around the active tick range. Accurate for
        // swaps that don't cross ticks; off by an unbounded amount once
        // they do (caveat documented in `OrcaWhirlpool::active_range_reserves`).
        let (eff_a, eff_b) = self.active_range_reserves();
        let (r_in, r_out, out_token) = if input.token.mint == self.token_a.mint {
            (eff_a, eff_b, &self.token_b)
        } else if input.token.mint == self.token_b.mint {
            (eff_b, eff_a, &self.token_a)
        } else {
            return Err(StormError::Parse(format!(
                "input mint {} not in whirlpool {}",
                input.token.mint, self.address
            )));
        };
        // fee_rate is in hundredths-of-a-basis-point; denominator is 1_000_000.
        let out = cpmm_swap_output(
            r_in,
            r_out,
            input.raw,
            self.state.fee_rate as u64,
            1_000_000,
        )
        .ok_or_else(|| {
            StormError::Parse(format!("whirlpool swap math overflow on {}", self.address))
        })?;
        Ok(TokenAmount::new(out_token.clone(), out))
    }
}

impl RpcContext {
    /// Fetch an Orca Whirlpool: pool state + both vaults + both mints
    /// (for decimals) in 2 RPC calls.
    pub async fn fetch_orca_whirlpool(&self, address: &Pubkey) -> Result<OrcaWhirlpool> {
        let pool_account = self
            .rpc()
            .get_account_with_commitment(address, self.commitment())
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?
            .value
            .ok_or_else(|| StormError::Parse(format!("whirlpool {address} not found")))?;
        if pool_account.owner != ORCA_WHIRLPOOL_PROGRAM_ID {
            return Err(StormError::Parse(format!(
                "account {address} is not owned by Orca Whirlpool (owner: {})",
                pool_account.owner
            )));
        }
        let state = WhirlpoolState::unpack(&pool_account.data)?;
        let extras = self
            .rpc()
            .get_multiple_accounts(&[
                state.token_vault_a,
                state.token_vault_b,
                state.token_mint_a,
                state.token_mint_b,
            ])
            .await
            .map_err(|e| StormError::Rpc(e.to_string()))?;
        let vault_a = extras[0].as_ref().ok_or_else(|| {
            StormError::Parse(format!("vault A {} vanished", state.token_vault_a))
        })?;
        let vault_b = extras[1].as_ref().ok_or_else(|| {
            StormError::Parse(format!("vault B {} vanished", state.token_vault_b))
        })?;
        let mint_a_acct = extras[2]
            .as_ref()
            .ok_or_else(|| StormError::Parse(format!("mint A {} not found", state.token_mint_a)))?;
        let mint_b_acct = extras[3]
            .as_ref()
            .ok_or_else(|| StormError::Parse(format!("mint B {} not found", state.token_mint_b)))?;
        let va = TokenAccountSnapshot::unpack(state.token_vault_a, &vault_a.data)?;
        let vb = TokenAccountSnapshot::unpack(state.token_vault_b, &vault_b.data)?;
        let ma = crate::accounts::MintInfo::unpack(state.token_mint_a, &mint_a_acct.data)?;
        let mb = crate::accounts::MintInfo::unpack(state.token_mint_b, &mint_b_acct.data)?;
        Ok(OrcaWhirlpool::new(
            *address,
            pool_account.owner,
            state,
            ma.decimals,
            mb.decimals,
            va.amount,
            vb.amount,
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

    // ---- Orca Whirlpool ------------------------------------------------

    fn synth_whirlpool_bytes(
        sqrt_price_x64: u128,
        liquidity: u128,
        fee_rate: u16,
        tick_spacing: u16,
        tick_current_index: i32,
        mint_a: &Pubkey,
        mint_b: &Pubkey,
    ) -> [u8; 653] {
        let mut data = [0u8; 653];
        data[..8].copy_from_slice(&WhirlpoolState::DISCRIMINATOR);
        data[41..43].copy_from_slice(&tick_spacing.to_le_bytes());
        data[45..47].copy_from_slice(&fee_rate.to_le_bytes());
        data[49..65].copy_from_slice(&liquidity.to_le_bytes());
        data[65..81].copy_from_slice(&sqrt_price_x64.to_le_bytes());
        data[81..85].copy_from_slice(&tick_current_index.to_le_bytes());
        data[101..133].copy_from_slice(mint_a.as_ref());
        data[181..213].copy_from_slice(mint_b.as_ref());
        data
    }

    #[test]
    fn whirlpool_layout_size_is_653() {
        assert_eq!(WhirlpoolState::SIZE, 653);
    }

    #[test]
    fn whirlpool_unpack_rejects_wrong_discriminator() {
        let mut data = [0u8; 653];
        // discriminator left as zeros
        match WhirlpoolState::unpack(&data) {
            Err(StormError::Parse(m)) => assert!(m.contains("discriminator")),
            other => panic!("expected discriminator error, got {other:?}"),
        }
        // now correct bytes
        data[..8].copy_from_slice(&WhirlpoolState::DISCRIMINATOR);
        // still zeros for the rest is structurally legal — verifies the path
        WhirlpoolState::unpack(&data).unwrap();
    }

    #[test]
    fn sqrt_price_round_trip_for_unit_price() {
        // sqrt(1) * 2^64 = 2^64 → price = 1, no decimals difference.
        let p = sqrt_price_x64_to_price(1u128 << 64, 6, 6);
        assert_eq!(p.normalize().to_string(), "1");
    }

    #[test]
    fn sqrt_price_handles_decimals_skew() {
        // SOL/USDC at 100 USDC per SOL.
        // raw_price = (USDC_raw / SOL_raw) at 100 ui USDC per 1 ui SOL
        //           = 100 * 10^6 / 10^9 = 10^-1
        // sqrt(raw_price) = 10^-0.5 ≈ 0.31622776601683794
        // sqrt_price_x64 = 0.31622... * 2^64
        let sqrt = 0.31622776601683794_f64;
        let sqrt_price_x64 = (sqrt * (1u128 << 64) as f64) as u128;
        let p = sqrt_price_x64_to_price(sqrt_price_x64, 9, 6);
        // Allow a tiny tolerance — f64 → u128 → Decimal round-trip drift.
        let val = p.to_string().parse::<f64>().unwrap();
        assert!((val - 100.0).abs() < 0.01, "got {val}");
    }

    #[test]
    fn whirlpool_price_matches_sqrt_price_helper() {
        let mint_a = Pubkey::new_unique();
        let mint_b = Pubkey::new_unique();
        let sqrt = 0.31622776601683794_f64;
        let sqrt_price_x64 = (sqrt * (1u128 << 64) as f64) as u128;
        let liquidity = 10_000_000_000_000u128;
        let data = synth_whirlpool_bytes(
            sqrt_price_x64,
            liquidity,
            5, // 0.005% fee tier (5 = 0.0005% in hundredths-bp; treat as low)
            8,
            0,
            &mint_a,
            &mint_b,
        );
        let state = WhirlpoolState::unpack(&data).unwrap();
        let pool = OrcaWhirlpool::new(
            Pubkey::new_unique(),
            ORCA_WHIRLPOOL_PROGRAM_ID,
            state,
            9, // SOL
            6, // USDC
            0,
            0,
        );
        let val = pool.price().to_string().parse::<f64>().unwrap();
        assert!((val - 100.0).abs() < 0.01, "got {val}");
    }

    #[test]
    fn whirlpool_swap_rejects_foreign_mint() {
        let mint_a = Pubkey::new_unique();
        let mint_b = Pubkey::new_unique();
        let data = synth_whirlpool_bytes(1u128 << 64, 1_000_000, 30, 8, 0, &mint_a, &mint_b);
        let state = WhirlpoolState::unpack(&data).unwrap();
        let pool = OrcaWhirlpool::new(
            Pubkey::new_unique(),
            ORCA_WHIRLPOOL_PROGRAM_ID,
            state,
            6,
            6,
            0,
            0,
        );
        let foreign = TokenAmount::new(Token::new(Pubkey::new_unique(), 6), 1);
        assert!(matches!(
            pool.calculate_swap_output(&foreign),
            Err(StormError::Parse(_))
        ));
    }
}
