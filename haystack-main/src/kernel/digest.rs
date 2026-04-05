/// Dual digest for the ostk kernel.
///
/// On every tool response, generate:
///   [procs] a1:active:5m:12% a2:stale:2m:67%   (HSCP G2)
///   [files] src/main.rs:gen=7:agent-1:2m src/lib.rs:gen=3:agent-2:30s
///
/// [procs] always included (read from agents.jsonl via heartbeat).
/// [files] only included for stale files (using hwm).
///
/// # Two-Layer Token Defense (Read Elision Integration)
///
/// The digest participates in a two-layer defense against redundant reads:
///
/// 1. **Digest suppression (this module):** Current files are absent from the
///    `[files]` section — only stale files appear. When an agent sees no stale
///    files, it has no reason to re-read. Cost: ~0 tokens (the read never happens).
///
/// 2. **304 interception (kernel::elision):** If the agent reads anyway (because
///    it wants to verify, or its context was compacted), the kernel returns a
///    `[304] path:gen=N (current)` response (~5 tokens) instead of full content
///    (~800 tokens). See `kernel::elision::read_file_with_elision`.
///
/// Both layers are invisible to the agent. No protocol to learn. The agent just
/// pays less for the same behavior.
use crate::kernel::gen_table::GenTable;
use crate::kernel::heartbeat;
use crate::kernel::hwm::{Hwm, StaleFile};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

/// A complete digest for one tool response.
#[derive(Debug, Clone)]
pub struct Digest {
    /// Process status line
    pub procs: String,
    /// File status line (empty if no stale files)
    pub files: String,
}

impl Digest {
    /// Format the digest for inclusion in a tool response.
    /// Returns empty string if no meaningful content.
    pub fn format(&self) -> String {
        if self.files.is_empty() {
            self.procs.clone()
        } else {
            format!("{}\n{}", self.procs, self.files)
        }
    }
}

/// Generate the dual digest for a given agent.
///
/// - [procs] always: shows all agents and their health status
/// - [files] only if stale: shows files modified since this agent's last read
pub fn generate_digest(ostk_dir: &Path, agent_alias: &str) -> Result<Digest, String> {
    // Generate [procs] line
    let health = heartbeat::check_health(ostk_dir)?;
    let procs = heartbeat::format_procs_digest(&health);

    // Snapshot gen_table once — all digest components use the same snapshot
    // to avoid races where another agent modifies state between reads.
    let gen_table = GenTable::new(ostk_dir);
    let gen_snapshot = gen_table.list_gens()?;

    // Generate [files] line (only stale files) using the snapshot
    let hwm = Hwm::new(ostk_dir);
    let stale_files = hwm.check_staleness_with_gens(agent_alias, &gen_snapshot)?;

    let files = if stale_files.is_empty() {
        String::new()
    } else {
        format_files_digest_from_snapshot(&stale_files, &gen_snapshot)?
    };

    Ok(Digest { procs, files })
}

/// Format the [files] digest line from a pre-fetched gen snapshot.
/// Avoids re-reading gen_table for atomicity with the staleness check.
fn format_files_digest_from_snapshot(
    stale_files: &[StaleFile],
    all_gens: &[crate::kernel::gen_table::GenEntry],
) -> Result<String, String> {
    let now = current_epoch_secs();

    let entries: Vec<String> = stale_files
        .iter()
        .map(|sf| {
            // Find the gen entry for this file to get the timestamp
            let age = all_gens
                .iter()
                .find(|g| g.path == sf.path)
                .and_then(|g| parse_iso_to_epoch(&g.timestamp))
                .map(|ts| format_duration(now.saturating_sub(ts)))
                .unwrap_or_else(|| "?".to_string());

            format!("{}:gen={}:{}:{}", sf.path, sf.current_gen, sf.writer, age)
        })
        .collect();

    Ok(format!("[files] {}", entries.join(" ")))
}

/// Format a duration in seconds into a human-readable string.
fn format_duration(secs: u64) -> String {
    if secs < 60 {
        format!("{secs}s")
    } else if secs < 3600 {
        format!("{}m", secs / 60)
    } else {
        format!("{}h", secs / 3600)
    }
}

/// Current time as seconds since epoch.
fn current_epoch_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

/// Parse an ISO 8601 timestamp to epoch seconds.
fn parse_iso_to_epoch(iso: &str) -> Option<u64> {
    let iso = iso.trim();
    if iso.len() < 19 {
        return None;
    }

    let year: u64 = iso.get(0..4)?.parse().ok()?;
    let month: u64 = iso.get(5..7)?.parse().ok()?;
    let day: u64 = iso.get(8..10)?.parse().ok()?;
    let hour: u64 = iso.get(11..13)?.parse().ok()?;
    let min: u64 = iso.get(14..16)?.parse().ok()?;
    let sec: u64 = iso.get(17..19)?.parse().ok()?;

    let days = ymd_to_days(year, month, day)?;
    Some(days * 86400 + hour * 3600 + min * 60 + sec)
}

/// Convert year/month/day to days since Unix epoch.
fn ymd_to_days(year: u64, month: u64, day: u64) -> Option<u64> {
    let (y, m) = if month <= 2 {
        (year.checked_sub(1)?, month + 9)
    } else {
        (year, month - 3)
    };

    let era = y / 400;
    let yoe = y - era * 400;
    let doy = (153 * m + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe;

    days.checked_sub(719468)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::gen_table::GenTable;
    use crate::kernel::hwm::Hwm;
    use crate::kernel::identity::Identity;
    use std::fs;
    use std::path::PathBuf;

    fn temp_ostk_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join("ostk_test_digest").join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_digest_procs_only() {
        let dir = temp_ostk_dir("procs_only");
        let identity = Identity::new(&dir);

        let alias = identity.assign_alias_with(None).unwrap();
        assert_eq!(alias, "agent-1");

        let digest = generate_digest(&dir, "agent-1").unwrap();
        assert!(digest.procs.starts_with("[procs]"));
        assert!(digest.procs.contains("a1:active")); // HSCP G2
        assert!(digest.files.is_empty());

        let formatted = digest.format();
        assert!(formatted.starts_with("[procs]"));
        assert!(!formatted.contains("[files]"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_digest_with_stale_files() {
        let dir = temp_ostk_dir("stale_files");
        let identity = Identity::new(&dir);
        let gt = GenTable::new(&dir);
        let hwm = Hwm::new(&dir);

        // Register two agents
        let a1 = identity.assign_alias_with(None).unwrap();
        let _a2 = identity.assign_alias_with(None).unwrap();

        // Agent-1 reads a file at gen 1
        gt.bump_gen("src/main.rs", Some("agent-1")).unwrap();
        hwm.record_read("agent-1", "src/main.rs", 1).unwrap();

        // Agent-2 edits the file (gen 2, 3)
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap();
        gt.bump_gen("src/main.rs", Some("agent-2")).unwrap();

        // Generate digest for agent-1
        let digest = generate_digest(&dir, &a1).unwrap();

        // Should have procs
        assert!(digest.procs.contains("a1:")); // HSCP G2
        assert!(digest.procs.contains("a2:"));

        // Should have stale files
        assert!(!digest.files.is_empty());
        assert!(digest.files.contains("[files]"));
        assert!(digest.files.contains("src/main.rs:gen=3:agent-2"));

        let formatted = digest.format();
        assert!(formatted.contains("[procs]"));
        assert!(formatted.contains("[files]"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_digest_no_stale_when_current() {
        let dir = temp_ostk_dir("no_stale");
        let identity = Identity::new(&dir);
        let gt = GenTable::new(&dir);
        let hwm = Hwm::new(&dir);

        let alias = identity.assign_alias_with(None).unwrap();

        // Agent reads at current gen
        gt.bump_gen("src/main.rs", Some("agent-1")).unwrap();
        hwm.record_read(&alias, "src/main.rs", 1).unwrap();

        let digest = generate_digest(&dir, &alias).unwrap();
        assert!(digest.files.is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_two_agents_digest_reflects_both() {
        let dir = temp_ostk_dir("two_agents");
        let identity = Identity::new(&dir);
        let gt = GenTable::new(&dir);
        let hwm = Hwm::new(&dir);

        let a1 = identity.assign_alias_with(None).unwrap();
        let a2 = identity.assign_alias_with(None).unwrap();

        // Agent-1 edits file A
        gt.bump_gen("src/a.rs", Some(&a1)).unwrap();
        hwm.record_read(&a2, "src/a.rs", 0).unwrap(); // a2 hasn't read it

        // Agent-2 edits file B
        gt.bump_gen("src/b.rs", Some(&a2)).unwrap();
        hwm.record_read(&a1, "src/b.rs", 0).unwrap(); // a1 hasn't read it

        // Digest for agent-1 should show src/b.rs as stale
        let d1 = generate_digest(&dir, &a1).unwrap();
        // HSCP G2: aliases appear as a{N}: in procs — extract numeric suffix
        let a1_short = a1.trim_start_matches(|c: char| !c.is_ascii_digit());
        let a2_short = a2.trim_start_matches(|c: char| !c.is_ascii_digit());
        assert!(d1.procs.contains(&format!("a{a1_short}:")), "procs should contain G2 alias for {a1}: got {}", d1.procs);
        assert!(d1.procs.contains(&format!("a{a2_short}:")), "procs should contain G2 alias for {a2}: got {}", d1.procs);
        assert!(d1.files.contains("src/b.rs"));

        // Digest for agent-2 should show src/a.rs as stale
        let d2 = generate_digest(&dir, &a2).unwrap();
        assert!(d2.files.contains("src/a.rs"));

        let _ = fs::remove_dir_all(&dir);
    }
}
