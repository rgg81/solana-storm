use std::time::Duration;

/// Initial delay for an exponential-backoff reconnect loop.
pub const INITIAL_BACKOFF: Duration = Duration::from_secs(1);

/// Ceiling for the reconnect delay.
pub const MAX_BACKOFF: Duration = Duration::from_secs(60);

/// Next exponential-backoff delay: doubles `current`, capped at [`MAX_BACKOFF`].
///
/// Callers reset to [`INITIAL_BACKOFF`] once a connection succeeds.
pub fn next_backoff(current: Duration) -> Duration {
    (current * 2).min(MAX_BACKOFF)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn doubles_then_caps() {
        let mut b = INITIAL_BACKOFF;
        assert_eq!(b, Duration::from_secs(1));
        b = next_backoff(b);
        assert_eq!(b, Duration::from_secs(2));
        b = next_backoff(b);
        assert_eq!(b, Duration::from_secs(4));
        for _ in 0..20 {
            b = next_backoff(b);
        }
        assert_eq!(b, MAX_BACKOFF);
    }
}
