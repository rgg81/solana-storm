//! Pure holder-distribution feature computation.

use solana_sdk::pubkey::Pubkey;

/// One entry from `getTokenLargestAccounts` — a token account and its raw
/// balance. `address` is the token-account address (not the owner wallet);
/// `getTokenLargestAccounts` does not return owners.
#[derive(Debug, Clone, Copy)]
pub struct HolderBalance {
    /// The token-account address.
    pub address: Pubkey,
    /// Raw integer token balance held by this account.
    pub amount: u64,
}

/// Holder-distribution features — the Lean-v1 "holder distribution" group.
/// All concentration fields are fractions of total supply in `[0.0, 1.0]`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HolderFeatures {
    /// Number of holders visible in the top-N set (at most 20 from one RPC
    /// call). A lower bound on the true holder count.
    pub visible_holder_count: usize,
    /// Combined balance of the top 10 token *accounts* / total supply.
    /// Because one owner may hold multiple token accounts, this is a lower
    /// bound on true owner concentration.
    pub top10_concentration: f64,
    /// Combined balance of the top 20 token *accounts* / total supply.
    /// Because one owner may hold multiple token accounts, this is a lower
    /// bound on true owner concentration.
    pub top20_concentration: f64,
    /// The creator's remaining token balance / total supply ("dev's bag").
    pub creator_bag_fraction: f64,
}

/// Sum the `n` largest balances and divide by `total_supply`. Sorts defensively
/// (descending) so the result is correct regardless of input order. Returns
/// `0.0` if `total_supply` is zero.
fn top_n_concentration(holders: &[HolderBalance], n: usize, total_supply: u64) -> f64 {
    if total_supply == 0 {
        return 0.0;
    }
    let mut amounts: Vec<u64> = holders.iter().map(|h| h.amount).collect();
    amounts.sort_unstable_by_key(|&a| std::cmp::Reverse(a));
    let top_sum: u128 = amounts.iter().take(n).map(|&a| a as u128).sum();
    top_sum as f64 / total_supply as f64
}

/// Derive holder-distribution features from the top-holders list.
///
/// * `holders` — entries from `getTokenLargestAccounts` (up to 20).
/// * `total_supply` — the token's total supply (raw units).
/// * `creator_balance` — the raw balance held by the creator's token account,
///   or `0` if the creator holds none.
pub fn holder_features(
    holders: &[HolderBalance],
    total_supply: u64,
    creator_balance: u64,
) -> HolderFeatures {
    let creator_bag_fraction = if total_supply == 0 {
        0.0
    } else {
        creator_balance as f64 / total_supply as f64
    };
    HolderFeatures {
        visible_holder_count: holders.len(),
        top10_concentration: top_n_concentration(holders, 10, total_supply),
        top20_concentration: top_n_concentration(holders, 20, total_supply),
        creator_bag_fraction,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SUPPLY: u64 = 1_000_000_000_000_000;

    /// Build `n` holders each holding `each` raw units.
    fn holders(n: usize, each: u64) -> Vec<HolderBalance> {
        (0..n)
            .map(|_| HolderBalance {
                address: Pubkey::new_unique(),
                amount: each,
            })
            .collect()
    }

    #[test]
    fn visible_holder_count_is_list_length() {
        let f = holder_features(&holders(17, 1_000), SUPPLY, 0);
        assert_eq!(f.visible_holder_count, 17);
    }

    #[test]
    fn top10_sums_only_ten_largest() {
        // 20 holders, each 1% of supply. Top 10 = 10% of supply.
        let one_percent = SUPPLY / 100;
        let f = holder_features(&holders(20, one_percent), SUPPLY, 0);
        assert!((f.top10_concentration - 0.10).abs() < 1e-9);
        assert!((f.top20_concentration - 0.20).abs() < 1e-9);
    }

    #[test]
    fn concentration_uses_largest_regardless_of_input_order() {
        // One whale (50%) plus nine tiny holders, whale listed LAST.
        let mut hs = holders(9, 1);
        hs.push(HolderBalance {
            address: Pubkey::new_unique(),
            amount: SUPPLY / 2,
        });
        let f = holder_features(&hs, SUPPLY, 0);
        // Top 10 includes the whale → ~50% of supply.
        assert!(f.top10_concentration > 0.49 && f.top10_concentration < 0.51);
    }

    #[test]
    fn creator_bag_fraction_is_creator_balance_over_supply() {
        // Creator holds 5% of supply.
        let f = holder_features(&holders(5, 1_000), SUPPLY, SUPPLY / 20);
        assert!((f.creator_bag_fraction - 0.05).abs() < 1e-9);
    }

    #[test]
    fn empty_holders_give_zero_concentration() {
        let f = holder_features(&[], SUPPLY, 0);
        assert_eq!(f.visible_holder_count, 0);
        assert_eq!(f.top10_concentration, 0.0);
        assert_eq!(f.top20_concentration, 0.0);
        assert_eq!(f.creator_bag_fraction, 0.0);
    }

    #[test]
    fn zero_supply_gives_zero_fractions() {
        let f = holder_features(&holders(5, 1_000), 0, 1_000);
        assert_eq!(f.top10_concentration, 0.0);
        assert_eq!(f.top20_concentration, 0.0);
        assert_eq!(f.creator_bag_fraction, 0.0);
    }
}
