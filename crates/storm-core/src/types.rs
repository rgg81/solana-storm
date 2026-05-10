use std::fmt;

use rust_decimal::Decimal;
use solana_sdk::pubkey::Pubkey;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Token {
    pub mint: Pubkey,
    pub symbol: Option<String>,
    pub decimals: u8,
}

impl Token {
    pub fn new(mint: Pubkey, decimals: u8) -> Self {
        Self {
            mint,
            symbol: None,
            decimals,
        }
    }

    pub fn with_symbol(mut self, symbol: impl Into<String>) -> Self {
        self.symbol = Some(symbol.into());
        self
    }

    pub fn label(&self) -> String {
        self.symbol.clone().unwrap_or_else(|| self.mint.to_string())
    }
}

#[derive(Debug, Clone)]
pub struct TokenAmount {
    pub token: Token,
    pub raw: u64,
}

impl TokenAmount {
    pub fn new(token: Token, raw: u64) -> Self {
        Self { token, raw }
    }

    /// Human-readable amount: `raw / 10^decimals`.
    pub fn ui_amount(&self) -> Decimal {
        Decimal::from_i128_with_scale(self.raw as i128, self.token.decimals as u32)
    }
}

impl fmt::Display for TokenAmount {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} {}", self.ui_amount(), self.token.label())
    }
}

#[derive(Debug, Clone)]
pub struct Price {
    pub base: Token,
    pub quote: Token,
    pub value: Decimal,
}

impl fmt::Display for Price {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{}/{} {}",
            self.base.label(),
            self.quote.label(),
            self.value
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    fn usdc() -> Token {
        let mint = Pubkey::from_str("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v").unwrap();
        Token::new(mint, 6).with_symbol("USDC")
    }

    #[test]
    fn ui_amount_respects_decimals() {
        let a = TokenAmount::new(usdc(), 1_234_567);
        assert_eq!(a.ui_amount().to_string(), "1.234567");
    }

    #[test]
    fn ui_amount_handles_u64_max() {
        let a = TokenAmount::new(usdc(), u64::MAX);
        // u64::MAX = 18446744073709551615; with 6 decimals → 18446744073709.551615
        assert_eq!(a.ui_amount().to_string(), "18446744073709.551615");
    }

    #[test]
    fn display_falls_back_to_mint_when_no_symbol() {
        let mint = Pubkey::from_str("So11111111111111111111111111111111111111112").unwrap();
        let unknown = Token::new(mint, 9);
        let a = TokenAmount::new(unknown, 1_500_000_000);
        assert_eq!(
            a.to_string(),
            "1.500000000 So11111111111111111111111111111111111111112"
        );
    }
}
