//! Shared helper functions used across ostk commands.
//!
//! Canonical counting functions live here. Every subsystem that needs needle or
//! fleet counts MUST call these functions (or read from StatusCache, which also
//! uses them) to avoid pollers disagreeing on definitions.

use std::fs;
use std::path::Path;

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

/// Canonical fleet count: active agents from agents.jsonl.
///
/// Returns (active, total) — uses proper JSON parsing of the "status" field.
/// This is the ONLY function that should count fleet agents. All callers —
/// StatusCache, bootloader, context heartbeat, boot register dump — MUST use
/// this function to avoid substring-match bugs (e.g. "active" appearing in a
/// non-status field).
pub fn count_fleet(path: &Path) -> (usize, usize) {
    // Derive the .ostk directory from the agents.jsonl path (its parent).
    let ostk_dir = path.parent().unwrap_or(path);
    let agents = crate::kernel::identity::read_agents(ostk_dir);
    let total = agents.len();
    let active = agents.iter().filter(|a| a.status == "active").count();
    (active, total)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

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
        // Uses proper AgentEntry format since count_fleet goes through kernel::identity
        let dir = tempfile::tempdir().unwrap();
        let agents_path = dir.path().join("agents.jsonl");
        std::fs::write(&agents_path, concat!(
            r#"{"alias":"active-worker","pid":1,"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#, "\n",
            r#"{"alias":"idle-box","pid":2,"status":"idle","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#, "\n",
            r#"{"alias":"active-probe","pid":3,"status":"done","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#, "\n",
        )).unwrap();
        let (active, total) = count_fleet(&agents_path);
        assert_eq!(active, 1, "only one agent has status==\"active\"");
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
}
