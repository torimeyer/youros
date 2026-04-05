//! →620: llmOS dying notification.
//!
//! When an agent's context utilization hits 90%, it announces its state before dying:
//! 1. Acquires `.ostk/fleet/<alias>/dying.lock` (idempotent — only fires once)
//! 2. Writes `.ostk/fleet/<alias>/dying.md` with current needle, work state, next steps
//! 3. Fires a nudge to the scheduler: "<alias> dying at N% — state in dying.md"
//!
//! The scheduler reads the nudge on the next turn, dispatches a fresh agent with
//! dying.md prepended to BOOT context. Law 2 + Law 3: ephemeral agent, durable state.

use std::fs;
use std::path::Path;

use crate::kernel::identity::Identity;
use crate::kernel::nudge;

/// Threshold at which dying notification fires (90%).
pub const DYING_THRESHOLD_PCT: f32 = 90.0;

/// Scheduler alias that receives dying nudges.
const SCHEDULER_ALIAS: &str = "scheduler";

/// Check if the agent should fire a dying notification.
/// Returns true if context_pct >= DYING_THRESHOLD_PCT, the dying service is
/// registered and has a consumer, and not already dying.
pub fn should_notify(context_pct: f32, ostk_dir: &Path, alias: &str) -> bool {
    if context_pct < DYING_THRESHOLD_PCT {
        return false;
    }
    // Only write fleet state if the dying service is registered and has a consumer
    let root = ostk_dir.parent().unwrap_or(ostk_dir);
    if !crate::language::is_capability_alive(root, "dying") {
        return false;
    }
    let identity = Identity::new(ostk_dir);
    // Idempotent: only fire once (dying.lock presence = already notified)
    !identity.dying_lock_path(alias).exists()
}

/// Fire the dying notification for `alias` at `context_pct`.
///
/// Writes:
/// - `.ostk/fleet/<alias>/dying.lock` (idempotency marker)
/// - `.ostk/fleet/<alias>/dying.md` (state declaration)
/// - nudge to scheduler alias
pub fn notify_dying(
    ostk_dir: &Path,
    alias: &str,
    context_pct: f32,
    active_needle: Option<&str>,
    work_done: &str,
    next_steps: &str,
) -> Result<(), String> {
    let identity = Identity::new(ostk_dir);

    // 1. Ensure fleet dir exists
    fs::create_dir_all(identity.fleet_dir(alias))
        .map_err(|e| format!("fleet dir: {e}"))?;

    // 2. Write dying.lock (idempotency marker — also prevents double-fire)
    fs::write(identity.dying_lock_path(alias), "dying")
        .map_err(|e| format!("dying.lock: {e}"))?;

    // 3. Write dying.md — the state handoff document
    let needle_line = active_needle
        .map(|n| format!("needle: {n}"))
        .unwrap_or_else(|| "needle: unknown".to_string());

    let content = format!(
        "# dying declaration — {alias}\n\n\
         context_pct: {context_pct:.1}%\n\
         {needle_line}\n\n\
         ## work done\n\n{work_done}\n\n\
         ## next steps\n\n{next_steps}\n\n\
         ## resume\n\n\
         Boot from this file. `ostk boot` then read `.ostk/fleet/{alias}/dying.md`.\n\
         The kernel state is intact. Continue from next_steps.\n"
    );

    fs::write(identity.dying_md_path(alias), &content)
        .map_err(|e| format!("dying.md: {e}"))?;

    // 4. Update state.yml to 'dying'
    identity.write_state(alias, "dying")?;

    // 5. Fire nudge to scheduler
    let nudge_msg = format!(
        "⚠ {alias} dying at {context_pct:.0}% — {needle_line} — state in .ostk/fleet/{alias}/dying.md"
    );
    nudge::push_nudge(ostk_dir, SCHEDULER_ALIAS, &nudge_msg)
        .map_err(|e| format!("nudge: {e}"))?;

    Ok(())
}

/// Read the dying declaration for an alias (for scheduler re-dispatch context).
pub fn read_dying_md(ostk_dir: &Path, alias: &str) -> Option<String> {
    let identity = Identity::new(ostk_dir);
    fs::read_to_string(identity.dying_md_path(alias)).ok()
}

/// Clear the dying state for an alias (agent was successfully re-dispatched).
pub fn clear_dying(ostk_dir: &Path, alias: &str) -> Result<(), String> {
    let identity = Identity::new(ostk_dir);
    let lock = identity.dying_lock_path(alias);
    let md = identity.dying_md_path(alias);
    if lock.exists() {
        fs::remove_file(&lock).map_err(|e| format!("remove dying.lock: {e}"))?;
    }
    if md.exists() {
        fs::remove_file(&md).map_err(|e| format!("remove dying.md: {e}"))?;
    }
    identity.write_state(alias, "ready")
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn setup() -> (TempDir, std::path::PathBuf) {
        let tmp = TempDir::new().unwrap();
        let hs = tmp.path().join(".ostk");
        fs::create_dir_all(&hs).unwrap();
        fs::create_dir_all(hs.join("nudges")).unwrap();
        // Register the dying service as alive so should_notify checks pass
        crate::language::register_capability(
            tmp.path(),
            "dying",
            "service",
            ".ostk/fleet/",
            "() -> (state)",
            "context pressure notification",
            true,
        )
        .unwrap();
        (tmp, hs)
    }

    #[test]
    fn test_should_notify_below_threshold() {
        let (_tmp, hs) = setup();
        assert!(!should_notify(89.9, &hs, "agent-1"));
    }

    #[test]
    fn test_should_notify_at_threshold() {
        let (_tmp, hs) = setup();
        assert!(should_notify(90.0, &hs, "agent-1"));
    }

    #[test]
    fn test_should_notify_idempotent() {
        let (_tmp, hs) = setup();
        // First call: should notify
        assert!(should_notify(95.0, &hs, "agent-1"));
        // Fire notification
        notify_dying(&hs, "agent-1", 95.0, Some("→628"), "wrote state.yml", "write dying.md").unwrap();
        // Second call: lock exists, should NOT notify again
        assert!(!should_notify(99.0, &hs, "agent-1"));
    }

    #[test]
    fn test_notify_dying_writes_files() {
        let (_tmp, hs) = setup();
        notify_dying(
            &hs,
            "agent-test",
            91.5,
            Some("→620"),
            "implemented should_notify",
            "wire into dispatch path",
        ).unwrap();

        let identity = Identity::new(&hs);
        assert!(identity.dying_lock_path("agent-test").exists());
        assert!(identity.dying_md_path("agent-test").exists());

        let md = fs::read_to_string(identity.dying_md_path("agent-test")).unwrap();
        assert!(md.contains("91.5%"));
        assert!(md.contains("→620"));
        assert!(md.contains("wire into dispatch path"));
    }

    #[test]
    fn test_clear_dying_removes_files() {
        let (_tmp, hs) = setup();
        notify_dying(&hs, "agent-clear", 93.0, None, "done", "continue").unwrap();

        clear_dying(&hs, "agent-clear").unwrap();

        let identity = Identity::new(&hs);
        assert!(!identity.dying_lock_path("agent-clear").exists());
        assert!(!identity.dying_md_path("agent-clear").exists());
    }
}
