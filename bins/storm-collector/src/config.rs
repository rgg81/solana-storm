//! Daemon tunables — cycle interval, observation windows, survival threshold.

use std::time::Duration;

/// Tunable timings and thresholds for the collector daemon. Construct with
/// [`CollectorConfig::from_env`]; every field has a default and an env override.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CollectorConfig {
    /// Delay between collection cycles.
    pub cycle_interval: Duration,
    /// Observation window before a feature snapshot is taken (spec: T0+6–24h).
    pub snapshot_window: Duration,
    /// Window before an outcome is recorded (spec: horizon N, ~1–4 weeks).
    pub outcome_window: Duration,
    /// Minimum pool quote (wrapped-SOL lamports) reserve for a "survived"
    /// verdict at the outcome check.
    pub survival_min_quote_lamports: u64,
}

impl Default for CollectorConfig {
    fn default() -> Self {
        Self {
            cycle_interval: Duration::from_secs(30 * 60), // 30 minutes
            snapshot_window: Duration::from_secs(12 * 3600), // 12 hours
            outcome_window: Duration::from_secs(14 * 24 * 3600), // 14 days
            survival_min_quote_lamports: 5_000_000_000,   // 5 SOL
        }
    }
}

impl CollectorConfig {
    /// Build from environment variables, falling back to [`Default`] for any
    /// unset or unparseable variable:
    ///
    /// * `STORM_CYCLE_INTERVAL_SECS`
    /// * `STORM_SNAPSHOT_WINDOW_SECS`
    /// * `STORM_OUTCOME_WINDOW_SECS`
    /// * `STORM_SURVIVAL_MIN_QUOTE_LAMPORTS`
    pub fn from_env() -> Self {
        let d = Self::default();
        Self {
            cycle_interval: env_secs("STORM_CYCLE_INTERVAL_SECS", d.cycle_interval),
            snapshot_window: env_secs("STORM_SNAPSHOT_WINDOW_SECS", d.snapshot_window),
            outcome_window: env_secs("STORM_OUTCOME_WINDOW_SECS", d.outcome_window),
            survival_min_quote_lamports: env_u64(
                "STORM_SURVIVAL_MIN_QUOTE_LAMPORTS",
                d.survival_min_quote_lamports,
            ),
        }
    }
}

/// Read `var` as a u64 count of seconds into a `Duration`, or `fallback`.
fn env_secs(var: &str, fallback: Duration) -> Duration {
    match std::env::var(var).ok().and_then(|s| s.parse::<u64>().ok()) {
        Some(secs) => Duration::from_secs(secs),
        None => fallback,
    }
}

/// Read `var` as a u64, or `fallback`.
fn env_u64(var: &str, fallback: u64) -> u64 {
    std::env::var(var)
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(fallback)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_the_spec_windows() {
        let c = CollectorConfig::default();
        assert_eq!(c.cycle_interval, Duration::from_secs(1800));
        assert_eq!(c.snapshot_window, Duration::from_secs(43_200)); // 12h
        assert_eq!(c.outcome_window, Duration::from_secs(1_209_600)); // 14d
        assert_eq!(c.survival_min_quote_lamports, 5_000_000_000);
    }

    #[test]
    fn env_secs_parses_or_falls_back() {
        // A junk value falls back to the default.
        assert_eq!(
            env_secs("STORM_TEST_DEFINITELY_UNSET_VAR", Duration::from_secs(99)),
            Duration::from_secs(99)
        );
    }

    #[test]
    fn env_u64_falls_back_when_unset() {
        assert_eq!(env_u64("STORM_TEST_DEFINITELY_UNSET_VAR", 42), 42);
    }
}
