//! `ostk diff` — session delta since last boot (→572).
//!
//! Shows what changed in .ostk/ since boot.md was last written:
//!
//! 1. Files changed (gen counter deltas since boot)
//! 2. Needles filed this session (task.added events in audit.jsonl since last session.shutdown)
//! 3. Audit events this session (count by type)
//! 4. Active staging (if .ostk/staging/active.tack exists, show current draft)

use std::collections::HashMap;
use std::fmt::Write as _;
use std::fs;
use std::path::Path;

use serde_json::Value;

use crate::kernel::verb_ctx::VerbCtx;

/// CLI entry point.
pub fn run() -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation — writes to VerbCtx (→1157).
pub fn run_verb(ctx: &mut VerbCtx) -> Result<(), String> {
    let hs_dir = ctx.ostk_dir();

    if !hs_dir.is_dir() {
        return Err("No .ostk/ found. Run `ostk install` to bootstrap.".into());
    }

    let boot_epoch = boot_md_mtime(&hs_dir);

    write_files_changed(ctx, &hs_dir, boot_epoch);

    let session_events = collect_session_events(&hs_dir, boot_epoch);
    write_needles_filed(ctx, &session_events);
    write_audit_counts(ctx, &session_events);
    write_active_staging(ctx, &hs_dir);

    Ok(())
}

// ---------------------------------------------------------------------------
// Section 1: files changed (gen_table deltas since boot)
// ---------------------------------------------------------------------------

/// Returns the mtime of boot.md in epoch seconds, or 0 if unavailable.
fn boot_md_mtime(hs_dir: &Path) -> u64 {
    use std::time::UNIX_EPOCH;
    hs_dir
        .join("boot.md")
        .metadata()
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Print files that have been modified since boot.md was last written.
///
/// Uses gen_table.jsonl: each entry has a `timestamp` field. We filter
/// entries whose timestamp is after the boot epoch, then show them with
/// their generation number and writer.
fn write_files_changed(w: &mut VerbCtx, hs_dir: &Path, boot_epoch: u64) {
    writeln!(w, "## Files changed since boot\n").unwrap();

    let gen_table_path = hs_dir.join("gen_table.jsonl");
    let content = match fs::read_to_string(&gen_table_path) {
        Ok(c) => c,
        Err(_) => {
            writeln!(w, "  (gen_table.jsonl not found — no tracking data)\n").unwrap();
            return;
        }
    };

    // Parse all entries, keep latest generation per path
    let mut latest: HashMap<String, (u64, String, String)> = HashMap::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(v) = serde_json::from_str::<Value>(line) {
            let path = v.get("path").and_then(|p| p.as_str()).unwrap_or("").to_string();
            let generation = v.get("generation").and_then(|g| g.as_u64()).unwrap_or(0);
            let writer = v
                .get("writer")
                .and_then(|w| w.as_str())
                .unwrap_or("unknown")
                .to_string();
            let timestamp = v
                .get("timestamp")
                .and_then(|t| t.as_str())
                .unwrap_or("")
                .to_string();

            // Keep the entry with the highest generation number per path
            let entry = latest.entry(path).or_insert((0, String::new(), String::new()));
            if generation > entry.0 {
                *entry = (generation, writer, timestamp);
            }
        }
    }

    // Filter entries modified after boot
    let mut changed: Vec<(String, u64, String, String)> = latest
        .into_iter()
        .filter_map(|(path, (generation, writer, ts))| {
            let entry_epoch = parse_iso_epoch(&ts).unwrap_or(0);
            if entry_epoch > boot_epoch {
                Some((path, generation, writer, ts))
            } else {
                None
            }
        })
        .collect();

    if changed.is_empty() {
        writeln!(w, "  (no gen_table entries after boot)\n").unwrap();
        return;
    }

    // Sort by timestamp descending (most recent first)
    changed.sort_by(|a, b| b.3.cmp(&a.3));

    for (path, generation, writer, ts) in &changed {
        let display_path = path
            .strip_prefix(".ostk/")
            .or_else(|| path.strip_prefix("/.ostk/"))
            .unwrap_or(path);
        writeln!(w, "  gen:{generation:>4}  {writer:<16}  {ts}  {display_path}").unwrap();
    }
    writeln!(w, "  ({} file(s) changed)\n", changed.len()).unwrap();
}

// ---------------------------------------------------------------------------
// Section 2 + 3: audit events since last session.shutdown
// ---------------------------------------------------------------------------

/// Collect audit events that occurred after the last session.shutdown event,
/// or after boot if no shutdown event exists.
///
/// Returns the events in chronological order.
fn collect_session_events(hs_dir: &Path, boot_epoch: u64) -> Vec<Value> {
    let audit_path = hs_dir.join("audit.jsonl");
    let content = match fs::read_to_string(&audit_path) {
        Ok(c) => c,
        Err(_) => return vec![],
    };

    let all_events: Vec<Value> = content
        .lines()
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| serde_json::from_str::<Value>(l.trim()).ok())
        .collect();

    // Find the epoch of the last session.shutdown event
    let last_shutdown_epoch = all_events
        .iter()
        .rev()
        .find(|v| {
            matches!(
                v.get("event").and_then(|e| e.as_str()),
                Some("session.shutdown")
            )
        })
        .and_then(|v| v.get("timestamp").and_then(|t| t.as_str()))
        .and_then(parse_iso_epoch)
        .unwrap_or(0);

    // Use the later of boot_epoch and last_shutdown_epoch as our cut-off
    let cutoff = last_shutdown_epoch.max(boot_epoch);

    all_events
        .into_iter()
        .filter(|v| {
            let ts = v.get("timestamp").and_then(|t| t.as_str()).unwrap_or("");
            parse_iso_epoch(ts).unwrap_or(0) > cutoff
        })
        .collect()
}

/// Print needles filed this session (task.added events).
fn write_needles_filed(w: &mut VerbCtx, events: &[Value]) {
    writeln!(w, "## Needles filed this session\n").unwrap();

    let added: Vec<&Value> = events
        .iter()
        .filter(|v| {
            matches!(
                v.get("event").and_then(|e| e.as_str()),
                Some("task.added")
            )
        })
        .collect();

    if added.is_empty() {
        writeln!(w, "  (none)\n").unwrap();
        return;
    }

    for v in &added {
        let id = v.get("id").and_then(|i| i.as_str()).unwrap_or("?");
        let title = v.get("title").and_then(|t| t.as_str()).unwrap_or("untitled");
        let priority = v.get("priority").and_then(|p| p.as_str()).unwrap_or("P1");
        writeln!(w, "  [{priority}] {id}  {title}").unwrap();
    }
    writeln!(w, "  ({} needle(s) added)\n", added.len()).unwrap();
}

/// Print audit event counts grouped by type.
fn write_audit_counts(w: &mut VerbCtx, events: &[Value]) {
    writeln!(w, "## Audit events this session\n").unwrap();

    if events.is_empty() {
        writeln!(w, "  (none)\n").unwrap();
        return;
    }

    let mut counts: HashMap<String, usize> = HashMap::new();
    for v in events {
        let event_type = v
            .get("event")
            .and_then(|e| e.as_str())
            .unwrap_or("unknown")
            .to_string();
        *counts.entry(event_type).or_insert(0) += 1;
    }

    let mut sorted: Vec<(String, usize)> = counts.into_iter().collect();
    sorted.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));

    for (event_type, count) in &sorted {
        writeln!(w, "  {count:>4}  {event_type}").unwrap();
    }
    writeln!(w, "  ({} total events)\n", events.len()).unwrap();
}

// ---------------------------------------------------------------------------
// Section 4: active staging draft
// ---------------------------------------------------------------------------

/// Print the current active.tack staging draft if it exists.
fn write_active_staging(w: &mut VerbCtx, hs_dir: &Path) {
    writeln!(w, "## Active staging\n").unwrap();

    let staging_path = hs_dir.join("staging").join("active.tack");
    if !staging_path.exists() {
        writeln!(w, "  (no active.tack — no intent forming)\n").unwrap();
        return;
    }

    let content = match fs::read_to_string(&staging_path) {
        Ok(c) => c,
        Err(e) => {
            writeln!(w, "  (failed to read active.tack: {e})\n").unwrap();
            return;
        }
    };

    let trimmed = content.trim();
    if trimmed.is_empty() {
        writeln!(w, "  (active.tack is empty)\n").unwrap();
    } else {
        for line in trimmed.lines() {
            writeln!(w, "  {line}").unwrap();
        }
        writeln!(w).unwrap();
    }
}

// ---------------------------------------------------------------------------
// Timestamp parsing
// ---------------------------------------------------------------------------

/// Parse an ISO 8601 timestamp ("2026-03-11T12:34:56Z") to epoch seconds.
/// Returns None if the format doesn't match.
fn parse_iso_epoch(ts: &str) -> Option<u64> {
    let ts = ts.trim_end_matches('Z');
    let (date, time) = ts.split_once('T')?;
    let date_parts: Vec<&str> = date.split('-').collect();
    if date_parts.len() != 3 {
        return None;
    }
    let year: u64 = date_parts[0].parse().ok()?;
    let month: u64 = date_parts[1].parse().ok()?;
    let day: u64 = date_parts[2].parse().ok()?;

    let time_parts: Vec<&str> = time.split(':').collect();
    if time_parts.len() != 3 {
        return None;
    }
    let hours: u64 = time_parts[0].parse().ok()?;
    let minutes: u64 = time_parts[1].parse().ok()?;
    // Handle sub-seconds by taking only the integer part
    let seconds: u64 = time_parts[2]
        .split('.')
        .next()
        .and_then(|s| s.parse().ok())?;

    // Zeller / proleptic Gregorian calendar → epoch conversion
    let y = if month <= 2 { year - 1 } else { year };
    let m = if month <= 2 { month + 9 } else { month - 3 };
    let era = y / 400;
    let yoe = y - era * 400;
    let doy = (153 * m + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe;
    let days = days.checked_sub(719468)?;
    Some(days * 86400 + hours * 3600 + minutes * 60 + seconds)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::verb_ctx::VerbCtx;
    use std::fs;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);

    fn test_dir(prefix: &str) -> std::path::PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_diff_test_{prefix}_{pid}_{n}"))
    }

    fn setup_ostk(hs_dir: &std::path::Path) {
        fs::create_dir_all(hs_dir.join("needles")).unwrap();
        fs::write(hs_dir.join("needles/counter"), "0").unwrap();
        fs::write(hs_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(hs_dir.join("audit.jsonl"), "").unwrap();
        fs::write(hs_dir.join("boot.md"), "# test\n").unwrap();
    }

    #[test]
    fn test_parse_iso_epoch_basic() {
        // 2026-03-11T00:00:00Z — should parse cleanly
        let epoch = parse_iso_epoch("2026-03-11T00:00:00Z");
        assert!(epoch.is_some(), "should parse a valid ISO timestamp");
        let e = epoch.unwrap();
        // Rough sanity: 2026 is ~56 years after 1970, so > 56*365*86400
        assert!(e > 56 * 365 * 86400, "epoch should be > ~2026");
    }

    #[test]
    fn test_parse_iso_epoch_invalid() {
        assert!(parse_iso_epoch("not-a-timestamp").is_none());
        assert!(parse_iso_epoch("").is_none());
        assert!(parse_iso_epoch("2026-03-11").is_none()); // missing T+time
    }

    #[test]
    fn test_boot_md_mtime_no_file() {
        let tmp = test_dir("no_boot");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        // No boot.md — should return 0
        let epoch = boot_md_mtime(&tmp);
        assert_eq!(epoch, 0, "missing boot.md should give epoch 0");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_md_mtime_with_file() {
        let tmp = test_dir("with_boot");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        fs::write(tmp.join("boot.md"), "# test\n").unwrap();

        let epoch = boot_md_mtime(&tmp);
        // boot.md just written — epoch should be close to now
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();
        assert!(epoch > 0, "existing boot.md should give nonzero epoch");
        assert!(now - epoch < 10, "boot.md mtime should be recent");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_collect_session_events_no_audit() {
        let tmp = test_dir("no_audit");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        let events = collect_session_events(&tmp, 0);
        assert!(events.is_empty(), "no audit.jsonl should give empty events");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_collect_session_events_since_shutdown() {
        let tmp = test_dir("session_events");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        // Create an audit with a shutdown event and some events after it
        let audit = concat!(
            "{\"event\":\"task.added\",\"id\":\"→001\",\"priority\":\"P1\",\"timestamp\":\"2026-03-11T10:00:00Z\",\"title\":\"old needle\"}\n",
            "{\"event\":\"session.shutdown\",\"timestamp\":\"2026-03-11T11:00:00Z\"}\n",
            "{\"event\":\"task.added\",\"id\":\"→002\",\"priority\":\"P0\",\"timestamp\":\"2026-03-11T12:00:00Z\",\"title\":\"new needle\"}\n",
            "{\"event\":\"hay.filed\",\"timestamp\":\"2026-03-11T12:30:00Z\"}\n",
        );
        fs::write(tmp.join("audit.jsonl"), audit).unwrap();

        // Boot epoch before all events
        let events = collect_session_events(&tmp, 0);

        // Should only include events after the shutdown (→002 + hay.filed)
        assert_eq!(events.len(), 2, "should have 2 events after shutdown");
        let first_event = events[0].get("event").and_then(|e| e.as_str()).unwrap_or("");
        assert_eq!(first_event, "task.added");
        let second_event = events[1].get("event").and_then(|e| e.as_str()).unwrap_or("");
        assert_eq!(second_event, "hay.filed");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_collect_session_events_no_shutdown_uses_boot_epoch() {
        let tmp = test_dir("no_shutdown");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        // No session.shutdown — all events returned since boot_epoch
        let audit = concat!(
            "{\"event\":\"task.added\",\"id\":\"→001\",\"priority\":\"P1\",\"timestamp\":\"2026-03-11T10:00:00Z\",\"title\":\"old\"}\n",
            "{\"event\":\"task.added\",\"id\":\"→002\",\"priority\":\"P0\",\"timestamp\":\"2026-03-11T12:00:00Z\",\"title\":\"new\"}\n",
        );
        fs::write(tmp.join("audit.jsonl"), audit).unwrap();

        // Boot epoch between the two events
        let boot_epoch = parse_iso_epoch("2026-03-11T11:00:00Z").unwrap();
        let events = collect_session_events(&tmp, boot_epoch);

        // Only the event after boot_epoch should appear
        assert_eq!(events.len(), 1, "only 1 event after boot epoch");
        assert_eq!(
            events[0].get("id").and_then(|i| i.as_str()),
            Some("→002")
        );

        let _ = fs::remove_dir_all(&tmp);
    }

    fn dummy_ctx(root: &Path) -> VerbCtx<'_> {
        // Use a static json value to avoid lifetime issues
        VerbCtx::new(root, &serde_json::Value::Null)
    }

    #[test]
    fn test_write_needles_filed_no_panic_empty() {
        let tmp = test_dir("needles_empty");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let mut ctx = dummy_ctx(&tmp);
        write_needles_filed(&mut ctx, &[]);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_write_audit_counts_no_panic_empty() {
        let tmp = test_dir("audit_empty");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let mut ctx = dummy_ctx(&tmp);
        write_audit_counts(&mut ctx, &[]);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_write_active_staging_no_file() {
        let tmp = test_dir("no_staging");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let mut ctx = dummy_ctx(&tmp);
        write_active_staging(&mut ctx, &tmp);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_write_active_staging_with_draft() {
        let tmp = test_dir("with_staging");
        let _ = fs::remove_dir_all(&tmp);
        let staging_dir = tmp.join("staging");
        fs::create_dir_all(&staging_dir).unwrap();
        fs::write(staging_dir.join("active.tack"), ":compile — triage hay\n").unwrap();
        let mut ctx = dummy_ctx(&tmp);
        write_active_staging(&mut ctx, &tmp);
        assert!(ctx.into_output().contains(":compile"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_run_returns_error_without_ostk_dir() {
        // run() calls find_project_root() which requires .ostk/
        // In a temp dir without .ostk it should fail gracefully.
        // We can test the inner logic via write_files_changed etc.
        let tmp = test_dir("no_hs");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        // boot_md_mtime on a dir without boot.md returns 0
        assert_eq!(boot_md_mtime(&tmp), 0);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_files_changed_no_gen_table() {
        let tmp = test_dir("no_gen_table");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        let mut ctx = dummy_ctx(&tmp);
        write_files_changed(&mut ctx, &tmp, 0);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_files_changed_with_gen_table() {
        let tmp = test_dir("with_gen_table");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        fs::write(tmp.join("boot.md"), "# test\n").unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));

        let now_ts = "2026-03-11T20:00:00Z";
        let gen_table = format!(
            "{{\"path\":\".ostk/audit.jsonl\",\"generation\":3,\"writer\":\"agent-1\",\"timestamp\":\"{now_ts}\",\"mtime\":null}}\n"
        );
        fs::write(tmp.join("gen_table.jsonl"), &gen_table).unwrap();

        let mut ctx = dummy_ctx(&tmp);
        write_files_changed(&mut ctx, &tmp, 0);
        assert!(ctx.into_output().contains("audit.jsonl"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_run_integration_with_ostk_dir() {
        let tmp = test_dir("integration");
        let _ = fs::remove_dir_all(&tmp);
        let hs_dir = tmp.join(".ostk");
        setup_ostk(&hs_dir);
        fs::create_dir_all(tmp.join(".git")).unwrap();

        // run() should succeed on a valid ostk project
        // We can't call run() directly because it calls find_project_root() from CWD,
        // but we can call the helper functions directly.
        let boot_epoch = boot_md_mtime(&hs_dir);
        let events = collect_session_events(&hs_dir, boot_epoch);
        assert!(events.is_empty(), "no events in empty audit");

        let mut ctx = dummy_ctx(tmp.as_ref());
        write_files_changed(&mut ctx, &hs_dir, boot_epoch);
        write_needles_filed(&mut ctx, &events);
        write_audit_counts(&mut ctx, &events);
        write_active_staging(&mut ctx, &hs_dir);

        let _ = fs::remove_dir_all(&tmp);
    }
}
