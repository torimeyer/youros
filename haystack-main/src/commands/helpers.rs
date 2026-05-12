//! Shared helper functions used across ostk commands.
//!
//! Canonical counting functions live here. Every subsystem that needs needle or
//! fleet counts MUST call these functions (or read from StatusCache, which also
//! uses them) to avoid pollers disagreeing on definitions.

use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

/// Agents not seen within this window are excluded from the active fleet count.
const FLEET_ACTIVE_STALENESS_SECS: u64 = 90;

/// Parse ISO 8601 UTC timestamp (YYYY-MM-DDTHH:MM:SS[Z]) into Unix seconds.
/// Hand-rolled so helpers.rs stays chrono-free. Returns None on any format error.
fn parse_iso8601_secs(s: &str) -> Option<u64> {
    let b = s.as_bytes();
    if b.len() < 19 { return None; }
    if b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[13] != b':' || b[16] != b':' {
        return None;
    }
    let p2 = |o: usize| -> Option<u64> {
        let a = b[o].wrapping_sub(b'0') as u64;
        let c = b[o + 1].wrapping_sub(b'0') as u64;
        if a > 9 || c > 9 { None } else { Some(a * 10 + c) }
    };
    let p4 = |o: usize| -> Option<u64> {
        let n1 = b[o].wrapping_sub(b'0') as u64;
        let n2 = b[o + 1].wrapping_sub(b'0') as u64;
        let n3 = b[o + 2].wrapping_sub(b'0') as u64;
        let n4 = b[o + 3].wrapping_sub(b'0') as u64;
        if n1 > 9 || n2 > 9 || n3 > 9 || n4 > 9 { None }
        else { Some(n1 * 1000 + n2 * 100 + n3 * 10 + n4) }
    };
    let y  = p4(0)?;
    let mo = p2(5)?;
    let dy = p2(8)?;
    let h  = p2(11)?;
    let mi = p2(14)?;
    let sc = p2(17)?;
    if mo < 1 || mo > 12 || dy < 1 || dy > 31 || h > 23 || mi > 59 || sc > 60 {
        return None;
    }
    // Howard Hinnant's days_from_civil (inverse of identity::days_to_ymd).
    let y2  = if mo <= 2 { y.checked_sub(1)? } else { y };
    let era = y2 / 400;
    let yoe = y2 - era * 400;
    let doy = (153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + dy - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe;
    let unix_days = days.checked_sub(719468)?;
    Some(unix_days * 86400 + h * 3600 + mi * 60 + sc)
}

/// Count non-empty lines in a file.
pub fn count_lines(path: &Path) -> usize {
    fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .count()
}

/// Canonical active needle count: open + in_progress.
/// This is the ONLY function that should count "active" needles.
///
/// All callers — StatusCache, BootContext (via build_register_dump), bootloader,
/// context heartbeat, show status — MUST use this function to avoid disagreement.
///
/// Warns on malformed JSON lines with actionable repair hints.
pub fn count_active_needles(path: &Path) -> usize {
    let content = fs::read_to_string(path).unwrap_or_default();
    let mut count = 0usize;
    let mut malformed = 0usize;
    let mut first_bad_line: Option<usize> = None;
    for (i, line) in content.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        match serde_json::from_str::<serde_json::Value>(line) {
            Ok(val) => {
                let status = val.get("status").and_then(|s| s.as_str()).unwrap_or("");
                if status == "open" || status == "in_progress" {
                    count += 1;
                }
            }
            Err(_) => {
                malformed += 1;
                if first_bad_line.is_none() {
                    first_bad_line = Some(i + 1);
                }
            }
        }
    }
    if malformed > 0 {
        let line_num = first_bad_line.unwrap_or(0);
        let fname = path.file_name().and_then(|n| n.to_str()).unwrap_or("issues.jsonl");
        eprintln!(
            "[warn] {fname} has {malformed} malformed line(s), first at line {line_num} \
             — run `ostk repair needles` to fix"
        );
    }
    count
}

/// Backward-compatible alias — calls `count_active_needles`.
pub fn count_open_needles(path: &Path) -> usize {
    count_active_needles(path)
}

/// Count open P0 needles in issues.jsonl.
///
/// Used by `generate_loadavg_line` to populate the `(N P0)` indicator.
pub fn count_p0_needles(path: &Path) -> usize {
    let content = fs::read_to_string(path).unwrap_or_default();
    let mut count = 0usize;
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() { continue; }
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(line) {
            let status = val.get("status").and_then(|s| s.as_str()).unwrap_or("");
            let priority = val.get("priority").and_then(|s| s.as_str()).unwrap_or("");
            if (status == "open" || status == "in_progress") && priority == "P0" {
                count += 1;
            }
        }
    }
    count
}

/// Count pending nudges across all agent mailboxes.
///
/// Reads the nudges/ directory under ostk_dir. Returns 0 if the directory does not
/// exist (e.g. worktree sessions that share only a subset of the ostk state).
pub fn count_pending_nudges(ostk_dir: &Path) -> usize {
    let nudge_dir = ostk_dir.join("nudges");
    if !nudge_dir.is_dir() {
        return 0;
    }
    let Ok(entries) = fs::read_dir(&nudge_dir) else { return 0; };
    entries
        .flatten()
        .filter(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            name.ends_with(".json") || name.ends_with(".jsonl")
        })
        .count()
}

/// Generate the `[loadavg]` MCP footer line by reading live from disk.
///
/// Root cause of →1199: the v6.0.0 binary reads these counts from a
/// per-session cache initialised to 0 at boot, so `[loadavg]` always shows
/// zeros even when needles/fleet/nudges are non-zero. `[ctx]` is correct
/// because it calls `build_heartbeat` which reads from disk on every invocation.
///
/// Fix: read the same files `[ctx]` uses — needles/issues.jsonl, agents.jsonl,
/// nudges/ — directly, with no intermediate cache.
pub fn generate_loadavg_line(ostk_dir: &Path) -> String {
    let issues_path = ostk_dir.join("needles").join("issues.jsonl");
    let agents_path = ostk_dir.join("agents.jsonl");
    let open   = count_open_needles(&issues_path);
    let p0     = count_p0_needles(&issues_path);
    let (alive, total) = count_fleet(&agents_path);
    let nudges = count_pending_nudges(ostk_dir);
    format!(
        "[loadavg] needles: {open} open ({p0} P0) | fleet: {alive}/{total} alive | nudges: {nudges}"
    )
}

/// Returns true if `cmd` is a git operation that mutates working-tree state.
///
/// →1153: Used by the fleet-active gate in serve/dispatch.rs to decide
/// which commands to block when peers are active. Read-only git subcommands
/// (log, diff, status, stash list, …) are NOT considered mutations.
pub fn is_git_state_mutation(cmd: &str) -> bool {
    let lower = cmd.trim().to_lowercase();
    // Strip leading VAR=value env overrides (e.g. OSTK_SKIP_GIT_GUARD=1 git stash …)
    let rest = {
        let mut s = lower.as_str();
        loop {
            let trimmed = s.trim_start();
            let eq_pos = trimmed.find('=');
            let sp_pos = trimmed.find(char::is_whitespace);
            match (eq_pos, sp_pos) {
                (Some(eq), Some(sp)) if eq < sp => { s = trimmed[sp..].trim_start(); }
                _ => break,
            }
        }
        s
    };
    let mut words = rest.split_whitespace();
    if words.next() != Some("git") { return false; }
    let sub = words.next().unwrap_or("");
    match sub {
        "reset" | "clean" => true,
        // stash push/save/drop/clear mutate state; list/show/apply/pop/branch are reads
        "stash" => !matches!(
            words.next().unwrap_or("push"),
            "list" | "show" | "apply" | "pop" | "branch"
        ),
        _ => false,
    }
}

/// Canonical fleet count: active agents from agents.jsonl.
///
/// Returns (active, total) — uses proper JSON parsing of the "status" field.
/// This is the ONLY function that should count fleet agents. All callers —
/// StatusCache, bootloader, context heartbeat, boot register dump — MUST use
/// this function to avoid substring-match bugs (e.g. "active" appearing in a
/// non-status field).
pub fn count_fleet(path: &Path) -> (usize, usize) {
    let ostk_dir = path.parent().unwrap_or(path);
    let agents = crate::kernel::identity::read_agents(ostk_dir);
    let total = agents.len();
    let now_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let active = agents.iter().filter(|a| {
        if a.status != "active" { return false; }
        match parse_iso8601_secs(&a.last_seen) {
            Some(ts) => now_secs.saturating_sub(ts) <= FLEET_ACTIVE_STALENESS_SECS,
            None => false,
        }
    }).count();
    (active, total)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::time::{SystemTime, UNIX_EPOCH};

    /// Format current UTC time as ISO 8601 (same algorithm as identity::now_iso).
    fn now_utc_iso() -> String {
        let secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let days = secs / 86400;
        let time_secs = secs % 86400;
        let h  = time_secs / 3600;
        let mi = (time_secs % 3600) / 60;
        let sc = time_secs % 60;
        let z   = days + 719468;
        let era = z / 146097;
        let doe = z - era * 146097;
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        let y   = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp  = (5 * doy + 2) / 153;
        let d   = doy - (153 * mp + 2) / 5 + 1;
        let m   = if mp < 10 { mp + 3 } else { mp - 9 };
        let y   = if m <= 2 { y + 1 } else { y };
        format!("{y:04}-{m:02}-{d:02}T{h:02}:{mi:02}:{sc:02}Z")
    }

    fn agent_row(alias: &str, pid: u32, status: &str, ts: &str) -> String {
        format!(
            r#"{{"alias":"{alias}","pid":{pid},"status":"{status}","registered_at":"{ts}","last_seen":"{ts}"}}"#
        )
    }

    fn write_tmp_file(content: &str) -> tempfile::NamedTempFile {
        let mut f = tempfile::NamedTempFile::new().unwrap();
        f.write_all(content.as_bytes()).unwrap();
        f.flush().unwrap();
        f
    }

    // ── count_active_needles ────────────────────────────────────────

    #[test]
    fn test_count_active_needles_open_and_in_progress() {
        let f = write_tmp_file(
            r#"{"id":"→001","status":"open","title":"a"}
{"id":"→002","status":"in_progress","title":"b"}
"#,
        );
        assert_eq!(count_active_needles(f.path()), 2);
    }

    #[test]
    fn test_count_active_needles_excludes_closed() {
        let f = write_tmp_file(
            r#"{"id":"→001","status":"open","title":"a"}
{"id":"→002","status":"closed","title":"b"}
{"id":"→003","status":"in_progress","title":"c"}
"#,
        );
        assert_eq!(count_active_needles(f.path()), 2);
    }

    #[test]
    fn test_count_active_needles_excludes_undefined() {
        let f = write_tmp_file(
            r#"{"id":"→001","title":"no status field"}
{"id":"→002","status":"open","title":"b"}
"#,
        );
        assert_eq!(count_active_needles(f.path()), 1);
    }

    // ── count_fleet ─────────────────────────────────────────────────

    #[test]
    fn test_fleet_count_json_parsing() {
        // active-worker needs a fresh last_seen so the staleness filter admits it.
        // The idle and done agents are excluded by status regardless of last_seen.
        let fresh = now_utc_iso();
        let dir = tempfile::tempdir().unwrap();
        let agents_path = dir.path().join("agents.jsonl");
        std::fs::write(&agents_path, format!(
            "{}\n{}\n{}\n",
            agent_row("active-worker", 1, "active", &fresh),
            agent_row("idle-box",      2, "idle",   "2026-01-01T00:00:00Z"),
            agent_row("active-probe",  3, "done",   "2026-01-01T00:00:00Z"),
        )).unwrap();
        let (active, total) = count_fleet(&agents_path);
        assert_eq!(active, 1, "only one agent has status==\"active\" with fresh last_seen");
        assert_eq!(total, 3);
    }

    #[test]
    fn test_fleet_count_empty_file() {
        let dir = tempfile::tempdir().unwrap();
        let agents_path = dir.path().join("agents.jsonl");
        std::fs::write(&agents_path, "").unwrap();
        let (active, total) = count_fleet(&agents_path);
        assert_eq!(active, 0);
        assert_eq!(total, 0);
    }

    #[test]
    fn test_fleet_active_staleness_cutoff() {
        // 3 stale active agents + 2 fresh active agents + 1 fresh non-active.
        // Post-fix: active=2 (only fresh active rows). Pre-fix: active=5 (all active rows).
        let fresh = now_utc_iso();
        let stale = "2026-01-01T00:00:00Z";
        let dir = tempfile::tempdir().unwrap();
        let agents_path = dir.path().join("agents.jsonl");
        let rows = [
            agent_row("stale-1", 1, "active", stale),
            agent_row("stale-2", 2, "active", stale),
            agent_row("stale-3", 3, "active", stale),
            agent_row("live-1",  4, "active", &fresh),
            agent_row("live-2",  5, "active", &fresh),
            agent_row("observer",6, "idle",   &fresh),
        ];
        std::fs::write(&agents_path, rows.join("\n") + "\n").unwrap();
        let (active, total) = count_fleet(&agents_path);
        assert_eq!(active, 2, "stale active agents must not count; got {active}");
        assert_eq!(total, 6);
    }

    #[test]
    fn test_count_open_needles_alias() {
        // Verify the backward-compatible alias returns the same result
        let f = write_tmp_file(
            r#"{"id":"→001","status":"open","title":"a"}
{"id":"→002","status":"in_progress","title":"b"}
{"id":"→003","status":"closed","title":"c"}
"#,
        );
        assert_eq!(count_open_needles(f.path()), count_active_needles(f.path()));
    }

    // ── count_fleet gate: 10-row staleness scenario (→1153) ─────────────────

    #[test]
    fn test_fleet_gate_count_10_rows() {
        // 8 stale active rows + 2 fresh active rows → gate must see only 2.
        // Pre-fix the gate counted all 10 rows, falsely blocking git mutations.
        let fresh = now_utc_iso();
        let stale = "2026-01-01T00:00:00Z";
        let dir = tempfile::tempdir().unwrap();
        let agents_path = dir.path().join("agents.jsonl");
        let rows: Vec<String> = (1..=8)
            .map(|i| agent_row(&format!("stale-{i}"), i, "active", stale))
            .chain([
                agent_row("live-1", 101, "active", &fresh),
                agent_row("live-2", 102, "active", &fresh),
            ])
            .collect();
        std::fs::write(&agents_path, rows.join("\n") + "\n").unwrap();
        let (active, total) = count_fleet(&agents_path);
        assert_eq!(total, 10);
        assert_eq!(active, 2, "only 2 fresh active rows should count; stale 8 must be excluded");
    }

    // ── is_git_state_mutation ────────────────────────────────────────────────

    #[test]
    fn test_git_state_mutation_detects_stash_push() {
        assert!(is_git_state_mutation("git stash"));
        assert!(is_git_state_mutation("git stash push"));
        assert!(is_git_state_mutation("git stash push -m \"wip\""));
        assert!(is_git_state_mutation("git stash save"));
        assert!(is_git_state_mutation("git stash drop"));
        assert!(is_git_state_mutation("git stash clear"));
    }

    #[test]
    fn test_git_state_mutation_allows_stash_reads() {
        assert!(!is_git_state_mutation("git stash list"));
        assert!(!is_git_state_mutation("git stash show"));
        assert!(!is_git_state_mutation("git stash apply"));
        assert!(!is_git_state_mutation("git stash pop"));
        assert!(!is_git_state_mutation("git stash branch my-branch"));
    }

    #[test]
    fn test_git_state_mutation_detects_reset_and_clean() {
        assert!(is_git_state_mutation("git reset --hard"));
        assert!(is_git_state_mutation("git reset --soft HEAD~1"));
        assert!(is_git_state_mutation("git clean -fd"));
        assert!(is_git_state_mutation("git clean -fxd"));
    }

    #[test]
    fn test_git_state_mutation_allows_read_only() {
        assert!(!is_git_state_mutation("git log --oneline -5"));
        assert!(!is_git_state_mutation("git status"));
        assert!(!is_git_state_mutation("git diff HEAD"));
        assert!(!is_git_state_mutation("git branch -a"));
    }

    #[test]
    fn test_git_state_mutation_strips_env_prefix() {
        // OSTK_SKIP_GIT_GUARD=1 git stash — env prefix stripped, mutation detected
        assert!(is_git_state_mutation("OSTK_SKIP_GIT_GUARD=1 git stash"));
        assert!(!is_git_state_mutation("OSTK_SKIP_GIT_GUARD=1 git status"));
    }

    #[test]
    fn test_git_state_mutation_non_git_commands() {
        assert!(!is_git_state_mutation("cargo test"));
        assert!(!is_git_state_mutation("echo hello"));
        assert!(!is_git_state_mutation(""));
    }

    // ── generate_loadavg_line (→1199) ───────────────────────────────────────

    #[test]
    fn test_generate_loadavg_line_reads_live_from_disk() {
        // Regression test for →1199: [loadavg] must read live from disk, not a
        // cached register. The v6.0.0 binary init'd the cache to 0 and never
        // refreshed it, so all three counters always showed zero.
        let dir = tempfile::tempdir().unwrap();
        let ostk_dir = dir.path();
        let needles_dir = ostk_dir.join("needles");
        fs::create_dir_all(&needles_dir).unwrap();

        // 3 open (1 P0), 1 in_progress (P2), 2 closed (one was P0)
        let issues = concat!(
            "{\"id\":\"001\",\"status\":\"open\",\"priority\":\"P0\",\"title\":\"critical\"}\n",
            "{\"id\":\"002\",\"status\":\"open\",\"priority\":\"P1\",\"title\":\"normal-a\"}\n",
            "{\"id\":\"003\",\"status\":\"open\",\"priority\":\"P1\",\"title\":\"normal-b\"}\n",
            "{\"id\":\"004\",\"status\":\"in_progress\",\"priority\":\"P2\",\"title\":\"doing\"}\n",
            "{\"id\":\"005\",\"status\":\"closed\",\"priority\":\"P1\",\"title\":\"done-a\"}\n",
            "{\"id\":\"006\",\"status\":\"closed\",\"priority\":\"P0\",\"title\":\"done-b\"}\n",
        );
        fs::write(needles_dir.join("issues.jsonl"), issues).unwrap();

        // 1 fresh active, 1 stale active (excluded by 90s window), 1 non-active
        let fresh = now_utc_iso();
        let stale = "2026-01-01T00:00:00Z";
        let agents = format!(
            "{}\n{}\n{}\n",
            agent_row("live-1",    1, "active", &fresh),
            agent_row("stale-1",   2, "active", stale),
            agent_row("observer",  3, "idle",   &fresh),
        );
        fs::write(ostk_dir.join("agents.jsonl"), &agents).unwrap();

        let line = generate_loadavg_line(ostk_dir);

        // 4 active needles (3 open + 1 in_progress)
        assert!(line.starts_with("[loadavg]"), "must start with [loadavg]: {line}");
        assert!(line.contains("needles: 4 open"), "open needle count wrong: {line}");
        // 1 open P0 (the closed P0 must not count)
        assert!(line.contains("(1 P0)"), "P0 count wrong: {line}");
        // 1 alive (stale excluded), 3 total
        assert!(line.contains("fleet: 1/3 alive"), "fleet count wrong: {line}");
        // No nudges/ dir → 0
        assert!(line.contains("nudges: 0"), "nudge count wrong: {line}");
    }

    #[test]
    fn test_generate_loadavg_line_no_needles_file() {
        // When issues.jsonl is missing, counts default to 0 — no panic.
        let dir = tempfile::tempdir().unwrap();
        let ostk_dir = dir.path();
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        // No issues.jsonl, no agents.jsonl
        let line = generate_loadavg_line(ostk_dir);
        assert_eq!(
            line,
            "[loadavg] needles: 0 open (0 P0) | fleet: 0/0 alive | nudges: 0"
        );
    }

    #[test]
    fn test_count_p0_needles_only_open() {
        let f = write_tmp_file(concat!(
            "{\"id\":\"1\",\"status\":\"open\",\"priority\":\"P0\",\"title\":\"a\"}\n",
            "{\"id\":\"2\",\"status\":\"open\",\"priority\":\"P1\",\"title\":\"b\"}\n",
            "{\"id\":\"3\",\"status\":\"closed\",\"priority\":\"P0\",\"title\":\"c\"}\n",
            "{\"id\":\"4\",\"status\":\"in_progress\",\"priority\":\"P0\",\"title\":\"d\"}\n",
        ));
        // closed P0 must not count; open+in_progress P0 should
        assert_eq!(count_p0_needles(f.path()), 2);
    }

    #[test]
    fn test_count_pending_nudges_counts_json_files() {
        let dir = tempfile::tempdir().unwrap();
        let nudge_dir = dir.path().join("nudges");
        fs::create_dir_all(&nudge_dir).unwrap();
        fs::write(nudge_dir.join("agent-a.json"), "{}").unwrap();
        fs::write(nudge_dir.join("agent-b.jsonl"), "{}").unwrap();
        fs::write(nudge_dir.join("README.md"), "notes").unwrap();
        // Only .json and .jsonl files count
        assert_eq!(count_pending_nudges(dir.path()), 2);
    }

    #[test]
    fn test_count_pending_nudges_missing_dir() {
        let dir = tempfile::tempdir().unwrap();
        // nudges/ does not exist — should return 0, not panic
        assert_eq!(count_pending_nudges(dir.path()), 0);
    }
}
