//! Pure contract-flag feature computation.

use storm_solana::MintInfo;

/// SPL-mint authority flags — the Lean-v1 "contract flags" feature group.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ContractFlags {
    /// True if the mint authority is still set (more tokens can be minted).
    pub mint_authority_present: bool,
    /// True if the freeze authority is still set (holder accounts can be
    /// frozen).
    pub freeze_authority_present: bool,
}

/// Derive the contract flags from a fetched SPL `Mint`.
pub fn contract_flags(mint: &MintInfo) -> ContractFlags {
    ContractFlags {
        mint_authority_present: mint.mint_authority.is_some(),
        freeze_authority_present: mint.freeze_authority.is_some(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::pubkey::Pubkey;

    fn mint_with(mint_authority: Option<Pubkey>, freeze_authority: Option<Pubkey>) -> MintInfo {
        MintInfo {
            address: Pubkey::new_unique(),
            decimals: 6,
            supply: 1_000_000_000_000_000,
            mint_authority,
            freeze_authority,
        }
    }

    #[test]
    fn renounced_mint_has_no_authorities() {
        let flags = contract_flags(&mint_with(None, None));
        assert!(!flags.mint_authority_present);
        assert!(!flags.freeze_authority_present);
    }

    #[test]
    fn both_authorities_present_are_detected() {
        let flags = contract_flags(&mint_with(
            Some(Pubkey::new_unique()),
            Some(Pubkey::new_unique()),
        ));
        assert!(flags.mint_authority_present);
        assert!(flags.freeze_authority_present);
    }

    #[test]
    fn mixed_authorities_map_independently() {
        let only_freeze = contract_flags(&mint_with(None, Some(Pubkey::new_unique())));
        assert!(!only_freeze.mint_authority_present);
        assert!(only_freeze.freeze_authority_present);

        let only_mint = contract_flags(&mint_with(Some(Pubkey::new_unique()), None));
        assert!(only_mint.mint_authority_present);
        assert!(!only_mint.freeze_authority_present);
    }
}
