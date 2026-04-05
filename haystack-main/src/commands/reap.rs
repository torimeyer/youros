//! `ostk reap` — garbage-collect dead agents from the process table.
//!
//! Walks agents.jsonl, checks each "active" entry with kill(pid, 0).
//! Dead agents → tombstoned (status="inactive"), file compacted.
//! Audit trail entry for each tombstone.

use std::fmt::Write;
use std::path::Path;

use serde_json::json;

use crate::kernel::identity::Identity;
use crate::kernel::verb_ctx::VerbCtx;
use crate::{append_audit, find_project_root, now_iso};

/// CLI entry point — creates a VerbCtx, runs, prints output.
pub fn run() -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx)?;
    print!("{}", ctx.into_output());

    // →596: Record resolution event for :reap (exact match static)
    let _ = append_audit(&root, &json!({
        "event": "tack.resolved",
        "input": ":reap",
        "verb": "reap",
        "tier": 1,
        "source": "static",
        "resolved": true,
        "timestamp": now_iso()
    }));

    if let Err(e) = crate::verify_os_integrity(&root) {
        eprintln!("warning: OS integrity check failed: {e}");
    }

    Ok(())
}

/// Verb implementation — writes to VerbCtx (→1157).
pub fn run_verb(ctx: &mut VerbCtx) -> Result<(), String> {
    let ostk_dir = ctx.ostk_dir();
    let reaped = reap_dead_agents(&ostk_dir)?;
    if reaped == 0 {
        writeln!(ctx, "reap: process table clean.").unwrap();
    } else {
        writeln!(ctx, "reap: {reaped} reaped. table compacted.").unwrap();
    }
    Ok(())
}

/// Reap dead agents at a specific .ostk directory (used by boot sequence).
pub fn run_at(ostk_dir: &Path) -> Result<(), String> {
    let reaped = reap_dead_agents(ostk_dir)?;
    if reaped == 0 {
        println!("reap: process table clean.");
    } else {
        println!("reap: {reaped} reaped. table compacted.");
    }
    Ok(())
}

/// Core reap logic — usable from boot, shutdown, or standalone.
/// Returns the count of agents reaped.
pub fn reap_dead_agents(ostk_dir: &Path) -> Result<usize, String> {
    let identity = Identity::new(ostk_dir);
    let agents = identity.read_agents()?;

    let mut alive = Vec::new();
    let mut zombies: Vec<serde_json::Value> = Vec::new();
    let total_before = agents.len();
    let now = now_iso();

    for agent in agents {
        if agent.status == "active" {
            if is_process_alive(agent.pid) {
                alive.push(agent); // living — keep
            } else {
                // Mark phase: capture exit metadata (waitpid equivalent)
                zombies.push(json!({
                    "alias": agent.alias,
                    "pid": agent.pid,
                    "registered_at": agent.registered_at,
                    "last_seen": agent.last_seen,
                }));
            }
        }
        // Sweep phase: inactive entries dropped (already reaped in prior pass)
    }

    let reaped = zombies.len();
    let stale = total_before - alive.len() - reaped; // pre-existing inactive

    if reaped > 0 || stale > 0 {
        // Compact: write back ONLY living agents
        identity.write_agents_pub(&alive)?;

        // Audit trail: capture zombie metadata before they're gone
        let root = ostk_dir.parent().unwrap_or(ostk_dir);
        let _ = append_audit(
            root,
            &json!({
                "event": "reap",
                "reaped": reaped,
                "stale_purged": stale,
                "remaining": alive.len(),
                "zombies": zombies,
                "timestamp": now,
            }),
        );
    }

    Ok(reaped)
}

/// Check if a process is still alive (Unix-only).
fn is_process_alive(pid: u32) -> bool {
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::identity::Identity;
    use std::fs;

    fn temp_dir(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir()
            .join("ostk_test_reap")
            .join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_reap_dead_agents() {
        let dir = temp_dir("reap_dead");

        // Write agents with fake (dead) PIDs
        let agents_path = dir.join("agents.jsonl");
        let entries = [r#"{"alias":"agent-1","pid":999999,"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#,
            r#"{"alias":"agent-2","pid":999998,"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#,
            r#"{"alias":"agent-3","pid":999997,"status":"inactive","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#];
        fs::write(&agents_path, entries.join("\n") + "\n").unwrap();

        // Audit write is best-effort — no setup needed for test

        let reaped = reap_dead_agents(&dir).unwrap();
        assert_eq!(reaped, 2, "should reap 2 dead active agents");

        // Verify compaction: dead agents REMOVED, not just marked
        let identity = Identity::new(&dir);
        let agents = identity.read_agents().unwrap();
        assert_eq!(agents.len(), 0, "all entries should be purged (2 dead + 1 stale inactive)");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_reap_preserves_living_agents() {
        let dir = temp_dir("reap_alive");
        let agents_path = dir.join("agents.jsonl");

        // Use our own PID — guaranteed alive
        let our_pid = std::process::id();
        let entry = format!(
            r#"{{"alias":"self","pid":{our_pid},"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}}"#,
        );
        fs::write(&agents_path, entry + "\n").unwrap();

        let reaped = reap_dead_agents(&dir).unwrap();
        assert_eq!(reaped, 0, "our own PID should not be reaped");

        let identity = Identity::new(&dir);
        let agents = identity.read_agents().unwrap();
        let active_count = agents.iter().filter(|a| a.status == "active").count();
        assert_eq!(active_count, 1, "our agent should still be active");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_reap_empty_table() {
        let dir = temp_dir("reap_empty");
        let agents_path = dir.join("agents.jsonl");
        fs::write(&agents_path, "").unwrap();

        let reaped = reap_dead_agents(&dir).unwrap();
        assert_eq!(reaped, 0);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_reap_compacts_file_size() {
        let dir = temp_dir("reap_compact");
        let agents_path = dir.join("agents.jsonl");

        // 100 dead agents + 1 alive
        let our_pid = std::process::id();
        let mut entries = Vec::new();
        for i in 0..100 {
            entries.push(format!(
                r#"{{"alias":"ghost-{i}","pid":{},"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}}"#,
                999900 + i
            ));
        }
        entries.push(format!(
            r#"{{"alias":"alive","pid":{our_pid},"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}}"#
        ));
        fs::write(&agents_path, entries.join("\n") + "\n").unwrap();

        let size_before = fs::metadata(&agents_path).unwrap().len();
        let reaped = reap_dead_agents(&dir).unwrap();
        let size_after = fs::metadata(&agents_path).unwrap().len();

        assert_eq!(reaped, 100);
        assert!(size_after < size_before / 10, "file should be >90% smaller: {size_before} -> {size_after}");

        let identity = Identity::new(&dir);
        let agents = identity.read_agents().unwrap();
        assert_eq!(agents.len(), 1, "only the alive agent should remain");
        assert_eq!(agents[0].alias, "alive");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_reap_no_file() {
        let dir = temp_dir("reap_nofile");
        let reaped = reap_dead_agents(&dir).unwrap();
        assert_eq!(reaped, 0);

        let _ = fs::remove_dir_all(&dir);
    }
}
