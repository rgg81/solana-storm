//! Pure outcome classification — the v1 survival rule.
//!
//! A token survived if its pool's quote (wrapped-SOL) reserve at the outcome
//! check is at least the configured threshold; otherwise it rugged. Pure and
//! unit-tested so validation (Phase 3) can retune the rule freely.

/// The recorded outcome verdict for a graduated token.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// The pool still holds at least the threshold quote liquidity.
    Survived,
    /// The pool's quote liquidity fell below the threshold (drained / dead).
    Rugged,
}

impl Verdict {
    /// `true` for [`Verdict::Survived`] — the value persisted to `outcomes.survived`.
    pub fn survived(self) -> bool {
        matches!(self, Verdict::Survived)
    }
}

/// Classify a graduated token's outcome from its pool's quote reserve.
///
/// * `quote_reserve_lamports` — the pool's quote-token (wrapped-SOL) reserve, in
///   lamports, observed at the outcome check.
/// * `min_quote_lamports` — the survival threshold (`CollectorConfig`).
pub fn classify_outcome(quote_reserve_lamports: u64, min_quote_lamports: u64) -> Verdict {
    if quote_reserve_lamports >= min_quote_lamports {
        Verdict::Survived
    } else {
        Verdict::Rugged
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 5 SOL, the default CollectorConfig survival threshold.
    const THRESHOLD: u64 = 5_000_000_000;

    #[test]
    fn well_funded_pool_survives() {
        // 40 SOL of quote liquidity — comfortably survives.
        assert_eq!(
            classify_outcome(40_000_000_000, THRESHOLD),
            Verdict::Survived
        );
    }

    #[test]
    fn drained_pool_rugs() {
        // 0.1 SOL left — rugged.
        assert_eq!(classify_outcome(100_000_000, THRESHOLD), Verdict::Rugged);
    }

    #[test]
    fn exactly_at_threshold_survives() {
        // The boundary is inclusive: exactly the threshold counts as survived.
        assert_eq!(classify_outcome(THRESHOLD, THRESHOLD), Verdict::Survived);
    }

    #[test]
    fn one_lamport_below_threshold_rugs() {
        assert_eq!(classify_outcome(THRESHOLD - 1, THRESHOLD), Verdict::Rugged);
    }

    #[test]
    fn empty_pool_rugs() {
        assert_eq!(classify_outcome(0, THRESHOLD), Verdict::Rugged);
    }

    #[test]
    fn survived_flag_maps_to_bool() {
        assert!(Verdict::Survived.survived());
        assert!(!Verdict::Rugged.survived());
    }
}
