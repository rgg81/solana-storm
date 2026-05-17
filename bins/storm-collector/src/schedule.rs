//! Pure scheduling decisions for the collector daemon.
//!
//! "Is a graduation due for a snapshot / an outcome?" is pure arithmetic over
//! Unix-second timestamps — no clock, no I/O. Unit-tested with synthetic input.

/// True when the T0+window observation period has elapsed and a feature
/// snapshot should be taken.
///
/// * `detected_at` — Unix seconds the graduation was detected (the collector's T0).
/// * `window_secs` — the observation window length in seconds.
/// * `now` — the current Unix-second timestamp.
pub fn is_snapshot_due(detected_at: i64, window_secs: i64, now: i64) -> bool {
    now >= detected_at.saturating_add(window_secs)
}

/// True when the outcome window has matured and an outcome should be recorded.
///
/// * `detected_at` — Unix seconds the graduation was detected (the collector's T0).
/// * `window_secs` — the outcome window length in seconds.
/// * `now` — the current Unix-second timestamp.
pub fn is_outcome_due(detected_at: i64, window_secs: i64, now: i64) -> bool {
    now >= detected_at.saturating_add(window_secs)
}

#[cfg(test)]
mod tests {
    use super::*;

    const T0: i64 = 1_779_000_000;
    const TWELVE_HOURS: i64 = 12 * 3600;
    const FOURTEEN_DAYS: i64 = 14 * 24 * 3600;

    #[test]
    fn snapshot_not_due_before_the_window() {
        // One second short of the window.
        assert!(!is_snapshot_due(T0, TWELVE_HOURS, T0 + TWELVE_HOURS - 1));
    }

    #[test]
    fn snapshot_due_exactly_at_the_window_boundary() {
        assert!(is_snapshot_due(T0, TWELVE_HOURS, T0 + TWELVE_HOURS));
    }

    #[test]
    fn snapshot_due_well_after_the_window() {
        assert!(is_snapshot_due(T0, TWELVE_HOURS, T0 + FOURTEEN_DAYS));
    }

    #[test]
    fn outcome_not_due_before_the_window() {
        assert!(!is_outcome_due(T0, FOURTEEN_DAYS, T0 + FOURTEEN_DAYS - 1));
    }

    #[test]
    fn outcome_due_at_and_after_the_window() {
        assert!(is_outcome_due(T0, FOURTEEN_DAYS, T0 + FOURTEEN_DAYS));
        assert!(is_outcome_due(T0, FOURTEEN_DAYS, T0 + FOURTEEN_DAYS + 1));
    }

    #[test]
    fn saturating_add_guards_against_overflow() {
        // A pathological window must not panic; it just means "never due".
        assert!(!is_snapshot_due(i64::MAX, i64::MAX, 0));
    }
}
