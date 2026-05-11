use rust_decimal::Decimal;

/// Constant-product market-maker swap output, after fee.
///
/// Given pool reserves `(reserve_in, reserve_out)` and an `amount_in`,
/// returns the amount of the output token, with the swap fee
/// `fee_numerator / fee_denominator` taken on the *input* side
/// (Raydium AMM v4 convention).
///
/// Returns `None` if any input is zero, the fee is invalid
/// (`numerator >= denominator`), or any intermediate multiplication
/// overflows `u128`.
pub fn cpmm_swap_output(
    reserve_in: u64,
    reserve_out: u64,
    amount_in: u64,
    fee_numerator: u64,
    fee_denominator: u64,
) -> Option<u64> {
    if reserve_in == 0 || reserve_out == 0 || amount_in == 0 {
        return None;
    }
    if fee_denominator == 0 || fee_numerator >= fee_denominator {
        return None;
    }
    let fee_den = fee_denominator as u128;
    let fee_num = fee_numerator as u128;
    let amt_in = amount_in as u128;
    let r_in = reserve_in as u128;
    let r_out = reserve_out as u128;

    let effective_in = amt_in.checked_mul(fee_den.checked_sub(fee_num)?)?;
    let numerator = r_out.checked_mul(effective_in)?;
    let denominator = r_in.checked_mul(fee_den)?.checked_add(effective_in)?;
    let out = numerator / denominator;
    if out > u64::MAX as u128 {
        return None;
    }
    Some(out as u64)
}

/// Spot price of token A in terms of token B, given raw pool reserves
/// and each side's decimals. `None` if `reserve_a == 0`.
pub fn spot_price(
    reserve_a: u64,
    reserve_b: u64,
    decimals_a: u8,
    decimals_b: u8,
) -> Option<Decimal> {
    if reserve_a == 0 {
        return None;
    }
    let a = Decimal::from_i128_with_scale(reserve_a as i128, decimals_a as u32);
    let b = Decimal::from_i128_with_scale(reserve_b as i128, decimals_b as u32);
    Some(b / a)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn output_with_zero_fee_matches_textbook_cpmm() {
        // x=1000, y=1000, dx=10, no fee
        // dy = 1000 * 10 / (1000 + 10) = 9.9009… → floor 9
        assert_eq!(cpmm_swap_output(1000, 1000, 10, 0, 10_000), Some(9));
    }

    #[test]
    fn output_with_raydium_fee_is_less_than_zero_fee() {
        let no_fee = cpmm_swap_output(1_000_000, 1_000_000, 10_000, 0, 10_000).unwrap();
        let fee = cpmm_swap_output(1_000_000, 1_000_000, 10_000, 25, 10_000).unwrap();
        assert!(fee < no_fee);
    }

    #[test]
    fn output_zero_amount_in_is_none() {
        assert_eq!(cpmm_swap_output(1_000, 1_000, 0, 25, 10_000), None);
    }

    #[test]
    fn output_zero_reserve_is_none() {
        assert_eq!(cpmm_swap_output(0, 1_000, 10, 25, 10_000), None);
        assert_eq!(cpmm_swap_output(1_000, 0, 10, 25, 10_000), None);
    }

    #[test]
    fn output_invalid_fee_is_none() {
        // fee_num >= fee_den
        assert_eq!(cpmm_swap_output(1_000, 1_000, 10, 10_000, 10_000), None);
        assert_eq!(cpmm_swap_output(1_000, 1_000, 10, 1, 0), None);
    }

    #[test]
    fn spot_price_normalises_decimals() {
        // 1000 SOL (9 dec) ↔ 142 000 USDC (6 dec) → 142 USDC per SOL
        let p = spot_price(1_000_000_000_000, 142_000_000_000, 9, 6).unwrap();
        assert_eq!(p.normalize().to_string(), "142");
    }

    #[test]
    fn spot_price_zero_reserve_a_is_none() {
        assert!(spot_price(0, 100, 9, 6).is_none());
    }
}

#[cfg(test)]
mod proptests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        #[test]
        fn output_never_drains_reserve_out(
            r_in in 1u64..1_000_000_000_000u64,
            r_out in 1u64..1_000_000_000_000u64,
            amt in 1u64..1_000_000_000u64,
            fee_num in 0u64..1_000u64,
        ) {
            if let Some(out) = cpmm_swap_output(r_in, r_out, amt, fee_num, 10_000) {
                prop_assert!(out < r_out, "out {out} >= r_out {r_out}");
            }
        }

        #[test]
        fn output_is_monotone_in_amount_in(
            r_in in 1_000u64..1_000_000_000u64,
            r_out in 1_000u64..1_000_000_000u64,
            amt1 in 1u64..10_000u64,
            extra in 1u64..10_000u64,
        ) {
            let amt2 = amt1.saturating_add(extra);
            let out1 = cpmm_swap_output(r_in, r_out, amt1, 25, 10_000);
            let out2 = cpmm_swap_output(r_in, r_out, amt2, 25, 10_000);
            if let (Some(o1), Some(o2)) = (out1, out2) {
                prop_assert!(o2 >= o1, "out2 {o2} < out1 {o1} for amt1 {amt1} amt2 {amt2}");
            }
        }

        #[test]
        fn higher_fee_yields_less_or_equal_output(
            r_in in 100u64..1_000_000u64,
            r_out in 100u64..1_000_000u64,
            amt in 1u64..10_000u64,
            low_fee in 0u64..50u64,
            extra_fee in 0u64..50u64,
        ) {
            let high_fee = low_fee + extra_fee;
            let lo = cpmm_swap_output(r_in, r_out, amt, low_fee, 10_000);
            let hi = cpmm_swap_output(r_in, r_out, amt, high_fee, 10_000);
            if let (Some(l), Some(h)) = (lo, hi) {
                prop_assert!(h <= l, "higher fee {high_fee} gave more output ({h}) than lower fee {low_fee} ({l})");
            }
        }

        #[test]
        fn round_trip_strictly_loses_to_fees(
            r_in in 1_000_000u64..1_000_000_000u64,
            r_out in 1_000_000u64..1_000_000_000u64,
            amt in 100u64..10_000u64,
        ) {
            if let Some(out1) = cpmm_swap_output(r_in, r_out, amt, 25, 10_000) {
                prop_assume!(out1 > 0);
                let new_r_in = r_in + amt;
                let new_r_out = r_out - out1;
                if let Some(out2) = cpmm_swap_output(new_r_out, new_r_in, out1, 25, 10_000) {
                    prop_assert!(out2 < amt, "round trip out2 {out2} >= original amt {amt}");
                }
            }
        }
    }
}
