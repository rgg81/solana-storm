use solana_sdk::pubkey::Pubkey;

use crate::PUMPFUN_PROGRAM_ID;

/// Derive the bonding-curve account address for a token mint.
pub fn bonding_curve_pda(mint: &Pubkey) -> Pubkey {
    Pubkey::find_program_address(
        &[b"bonding-curve", mint.as_ref()],
        &PUMPFUN_PROGRAM_ID,
    )
    .0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    // From tests/fixtures/NOTES.md (token: Pumpfun Pepe / PFP).
    const FIXTURE_MINT: &str = "5TfqNKZbn9AnNtzq8bbkyhKgcPGTfNDc9wNzFrTBpump";
    const FIXTURE_BONDING_CURVE: &str = "HLtp5EM2QRJZZXgSJqtYQ84tP8CDiziVHvFDGrEwW2wS";

    #[test]
    fn pda_matches_real_bonding_curve() {
        let mint = Pubkey::from_str(FIXTURE_MINT).unwrap();
        let expected = Pubkey::from_str(FIXTURE_BONDING_CURVE).unwrap();
        assert_eq!(bonding_curve_pda(&mint), expected);
    }
}
