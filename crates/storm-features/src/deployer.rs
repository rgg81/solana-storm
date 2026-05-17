//! Pure deployer-signal feature computation.

/// The page limit for the single bounded `getSignaturesForAddress` call.
/// Coarse by design — v1 never crawls full deployer history.
pub const SIGNATURE_PAGE_LIMIT: usize = 1000;

/// A summary of one bounded `getSignaturesForAddress` page for a wallet.
#[derive(Debug, Clone, Copy)]
pub struct SignaturePage {
    /// Number of signatures returned by the single page (`<= SIGNATURE_PAGE_LIMIT`).
    pub signature_count: usize,
    /// Unix timestamp (seconds) of the oldest signature in the page, if the
    /// page was non-empty and that signature carried a block time.
    pub oldest_block_time: Option<i64>,
}

/// Deployer signals — the Lean-v1 "deployer signal" feature group. Coarse,
/// derived from a single bounded signature page; not a full-history crawl.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeployerSignals {
    /// Signature count from the single page, capped at `SIGNATURE_PAGE_LIMIT`.
    pub capped_signature_count: usize,
    /// True if the page came back full — the real count is a lower bound.
    pub count_capped: bool,
    /// Age in seconds of the oldest visible signature (`now - oldest_block_time`).
    /// `None` if the page was empty or the oldest signature had no block time.
    /// Clamped to `0` if the oldest block time is in the future (clock skew).
    pub oldest_signature_age_secs: Option<i64>,
}

/// Derive the deployer signals from a bounded signature page.
///
/// * `page` — the summary of one `getSignaturesForAddress` page.
/// * `now_unix` — the reference "now" timestamp in Unix seconds.
pub fn deployer_signals(page: &SignaturePage, now_unix: i64) -> DeployerSignals {
    let capped_signature_count = page.signature_count.min(SIGNATURE_PAGE_LIMIT);
    let count_capped = page.signature_count >= SIGNATURE_PAGE_LIMIT;
    let oldest_signature_age_secs = page.oldest_block_time.map(|t| (now_unix - t).max(0));
    DeployerSignals {
        capped_signature_count,
        count_capped,
        oldest_signature_age_secs,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 2026-05-17T00:00:00Z, a fixed reference for deterministic tests.
    const NOW: i64 = 1_779_062_400;

    #[test]
    fn small_page_is_not_capped() {
        let page = SignaturePage {
            signature_count: 42,
            oldest_block_time: Some(NOW - 3600),
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.capped_signature_count, 42);
        assert!(!s.count_capped);
    }

    #[test]
    fn full_page_is_capped_and_flagged() {
        let page = SignaturePage {
            signature_count: SIGNATURE_PAGE_LIMIT,
            oldest_block_time: Some(NOW - 86_400),
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.capped_signature_count, SIGNATURE_PAGE_LIMIT);
        assert!(s.count_capped);
    }

    #[test]
    fn over_limit_count_is_clamped_to_the_limit() {
        // The RPC contract caps at 1000, but clamp defensively anyway.
        let page = SignaturePage {
            signature_count: 5000,
            oldest_block_time: Some(NOW - 86_400),
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.capped_signature_count, SIGNATURE_PAGE_LIMIT);
        assert!(s.count_capped);
    }

    #[test]
    fn oldest_signature_age_is_now_minus_block_time() {
        let page = SignaturePage {
            signature_count: 10,
            oldest_block_time: Some(NOW - 7 * 86_400),
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.oldest_signature_age_secs, Some(7 * 86_400));
    }

    #[test]
    fn missing_block_time_gives_no_age() {
        let page = SignaturePage {
            signature_count: 10,
            oldest_block_time: None,
        };
        assert_eq!(deployer_signals(&page, NOW).oldest_signature_age_secs, None);
    }

    #[test]
    fn future_block_time_clamps_age_to_zero() {
        // Clock skew: oldest signature appears 60s in the future.
        let page = SignaturePage {
            signature_count: 10,
            oldest_block_time: Some(NOW + 60),
        };
        assert_eq!(
            deployer_signals(&page, NOW).oldest_signature_age_secs,
            Some(0)
        );
    }

    #[test]
    fn empty_page_has_zero_count_and_no_age() {
        let page = SignaturePage {
            signature_count: 0,
            oldest_block_time: None,
        };
        let s = deployer_signals(&page, NOW);
        assert_eq!(s.capped_signature_count, 0);
        assert!(!s.count_capped);
        assert_eq!(s.oldest_signature_age_secs, None);
    }
}
