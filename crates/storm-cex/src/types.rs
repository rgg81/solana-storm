use std::fmt;

use rust_decimal::Decimal;

/// Which Binance venue a tick came from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    BinanceSpot,
    BinanceFutures,
}

impl Source {
    pub fn as_str(&self) -> &'static str {
        match self {
            Source::BinanceSpot => "binance-spot",
            Source::BinanceFutures => "binance-futures",
        }
    }
}

impl fmt::Display for Source {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Best bid/ask snapshot for one symbol, normalised across venues.
#[derive(Debug, Clone)]
pub struct PriceTick {
    /// Base asset, upper-cased (e.g. `SOL`). Quote is always USDT here.
    pub symbol: String,
    pub bid: Decimal,
    pub ask: Decimal,
    /// Milliseconds since the Unix epoch (local receive time).
    pub timestamp: i64,
    pub source: Source,
}

impl PriceTick {
    pub fn mid(&self) -> Decimal {
        (self.bid + self.ask) / Decimal::TWO
    }

    /// Bid/ask spread in basis points of the mid price.
    pub fn spread_bps(&self) -> Decimal {
        let mid = self.mid();
        if mid.is_zero() {
            return Decimal::ZERO;
        }
        (self.ask - self.bid) / mid * Decimal::from(10_000)
    }
}

/// Perpetual-futures mark price + funding rate for one symbol.
#[derive(Debug, Clone)]
pub struct FundingTick {
    pub symbol: String,
    pub mark_price: Decimal,
    /// Current funding rate (e.g. `0.0001` = 1 bp per funding interval).
    pub funding_rate: Decimal,
    /// Milliseconds since the Unix epoch of the next funding settlement.
    pub next_funding_time: i64,
    pub timestamp: i64,
}

/// Anything the CEX feed broadcasts to consumers.
#[derive(Debug, Clone)]
pub enum CexEvent {
    Price(PriceTick),
    Funding(FundingTick),
}

/// Strip a known quote suffix from a Binance symbol, upper-casing the base.
/// `"SOLUSDT"` → `"SOL"`; unknown suffixes pass through upper-cased.
pub fn normalize_symbol(raw: &str) -> String {
    let up = raw.to_uppercase();
    for quote in ["USDT", "USDC", "BUSD", "FDUSD"] {
        if let Some(base) = up.strip_suffix(quote) {
            if !base.is_empty() {
                return base.to_string();
            }
        }
    }
    up
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    #[test]
    fn normalize_strips_usdt() {
        assert_eq!(normalize_symbol("SOLUSDT"), "SOL");
        assert_eq!(normalize_symbol("btcusdt"), "BTC");
        assert_eq!(normalize_symbol("ETHUSDC"), "ETH");
    }

    #[test]
    fn normalize_passes_unknown_through() {
        assert_eq!(normalize_symbol("WEIRD"), "WEIRD");
    }

    #[test]
    fn mid_and_spread() {
        let t = PriceTick {
            symbol: "SOL".into(),
            bid: Decimal::from_str("99.90").unwrap(),
            ask: Decimal::from_str("100.10").unwrap(),
            timestamp: 0,
            source: Source::BinanceSpot,
        };
        assert_eq!(t.mid(), Decimal::from(100));
        // spread = 0.20 / 100 * 10000 = 20 bps
        assert_eq!(t.spread_bps(), Decimal::from(20));
    }
}
