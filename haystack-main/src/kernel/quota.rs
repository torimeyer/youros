//! →633: Local token savings quota — append-only, no auth required.
//!
//! Tracks cumulative token savings in `.ostk/quota.jsonl`.
//! Past quota: squasher and HSCP pass-through (graceful degradation).
//! TUI, fleet, needles unaffected — OS works, just without compression.
//!
//! Free tier: 100M tokens saved.
//! No phone-home required until quota is exceeded.

use fs2::FileExt;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

/// Free tier limit: 100 million tokens saved.
pub const FREE_TIER_TOKENS: u64 = 100_000_000;

/// A single quota event appended to quota.jsonl.
#[derive(Debug)]
pub struct QuotaEvent {
    /// Tokens saved in this event (positive = savings).
    pub tokens_saved: u64,
    /// Source: "squash", "hscp_g2", "read_elision", etc.
    pub source: String,
}

/// Acquire a shared (read) flock on quota.lock.
/// Prevents reading a partially-written line during a concurrent append.
fn with_quota_read_lock<F, R>(ostk_dir: &Path, f: F) -> R
where
    F: FnOnce() -> R,
{
    let lock_path = ostk_dir.join("quota.lock");
    let lock_file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&lock_path);
    match lock_file {
        Ok(lf) => {
            let _ = lf.lock_shared();
            let result = f();
            drop(lf);
            result
        }
        Err(_) => f(), // best-effort: proceed unlocked if lock file can't be created
    }
}

/// Acquire an exclusive flock on quota.lock.
/// Serialises concurrent appends so readers never see a torn line.
fn with_quota_write_lock<F, R>(ostk_dir: &Path, f: F) -> R
where
    F: FnOnce() -> R,
{
    let lock_path = ostk_dir.join("quota.lock");
    let lock_file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&lock_path);
    match lock_file {
        Ok(lf) => {
            let _ = lf.lock_exclusive();
            let result = f();
            drop(lf);
            result
        }
        Err(_) => f(), // best-effort
    }
}

/// Read cumulative tokens saved.
///
/// Fast path: read from quota.jsonl.
/// Fallback: if quota.jsonl is missing or empty, scan metrics.jsonl (the audit
/// trail) and sum the `saved` field (bytes / 4 ≈ tokens). This makes the quota
/// audit-backed — deleting quota.jsonl does not reset the meter.
///
/// Protected by a shared flock on quota.lock so reads never see a torn line.
pub fn read_cumulative_savings(ostk_dir: &Path) -> u64 {
    with_quota_read_lock(ostk_dir, || read_cumulative_savings_inner(ostk_dir))
}

/// Inner (unlocked) reader — called under shared flock.
fn read_cumulative_savings_inner(ostk_dir: &Path) -> u64 {
    // Fast path: quota.jsonl
    let path = ostk_dir.join("quota.jsonl");
    if let Ok(content) = fs::read_to_string(&path) {
        let total: u64 = content
            .lines()
            .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
            .filter_map(|v| v["tokens_saved"].as_u64())
            .sum();
        if total > 0 {
            return total;
        }
    }

    // Fallback: derive from metrics.jsonl audit trail (bytes/4 ≈ tokens)
    let metrics_path = ostk_dir.join("metrics.jsonl");
    if let Ok(content) = fs::read_to_string(&metrics_path) {
        let total: u64 = content
            .lines()
            .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
            .filter_map(|v| v["saved"].as_u64())
            .map(|bytes| bytes / 4)
            .sum();
        return total;
    }

    0
}

/// Append a quota event to quota.jsonl.
///
/// Protected by an exclusive flock on quota.lock so concurrent appends
/// are serialised and readers never observe a torn line.
pub fn record_savings(ostk_dir: &Path, event: &QuotaEvent) -> Result<(), String> {
    with_quota_write_lock(ostk_dir, || record_savings_inner(ostk_dir, event))
}

/// Inner (unlocked) writer — called under exclusive flock.
fn record_savings_inner(ostk_dir: &Path, event: &QuotaEvent) -> Result<(), String> {
    let path = ostk_dir.join("quota.jsonl");
    let entry = serde_json::json!({
        "tokens_saved": event.tokens_saved,
        "source": event.source,
        "ts": crate::now_iso(),
    });

    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("quota.jsonl open: {e}"))?;

    writeln!(file, "{}", entry)
        .map_err(|e| format!("quota.jsonl write: {e}"))
}

/// Check if compression features are active (within free tier).
/// Returns true if savings are below FREE_TIER_TOKENS.
pub fn compression_active(ostk_dir: &Path) -> bool {
    read_cumulative_savings(ostk_dir) < FREE_TIER_TOKENS
}

/// Format quota status for display.
pub fn format_quota_status(ostk_dir: &Path) -> String {
    let saved = read_cumulative_savings(ostk_dir);
    let pct = (saved as f64 / FREE_TIER_TOKENS as f64 * 100.0).min(100.0);
    if saved >= FREE_TIER_TOKENS {
        format!("quota: {:.0}M saved (free tier used — compression paused)", saved / 1_000_000)
    } else {
        format!("quota: {:.0}M/{:.0}M tokens saved ({:.1}% of free tier)",
            saved / 1_000_000,
            FREE_TIER_TOKENS / 1_000_000,
            pct)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn setup() -> (TempDir, std::path::PathBuf) {
        let tmp = TempDir::new().unwrap();
        let hs = tmp.path().join(".ostk");
        fs::create_dir_all(&hs).unwrap();
        (tmp, hs)
    }

    #[test]
    fn test_fresh_install_zero_savings() {
        let (_tmp, hs) = setup();
        assert_eq!(read_cumulative_savings(&hs), 0);
    }

    #[test]
    fn test_compression_active_by_default() {
        let (_tmp, hs) = setup();
        assert!(compression_active(&hs));
    }

    #[test]
    fn test_record_and_read_savings() {
        let (_tmp, hs) = setup();
        record_savings(&hs, &QuotaEvent { tokens_saved: 1000, source: "squash".into() }).unwrap();
        record_savings(&hs, &QuotaEvent { tokens_saved: 500, source: "hscp_g2".into() }).unwrap();
        assert_eq!(read_cumulative_savings(&hs), 1500);
    }

    #[test]
    fn test_compression_inactive_past_quota() {
        let (_tmp, hs) = setup();
        // Simulate being past the free tier
        record_savings(&hs, &QuotaEvent {
            tokens_saved: FREE_TIER_TOKENS + 1,
            source: "test".into()
        }).unwrap();
        assert!(!compression_active(&hs));
    }

    #[test]
    fn test_format_quota_status() {
        let (_tmp, hs) = setup();
        let status = format_quota_status(&hs);
        assert!(status.contains("0M/100M"));
    }
}
