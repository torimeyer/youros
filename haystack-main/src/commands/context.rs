//! `ostk context` — the heartbeat primitive (→608).
//!
//! Outputs a compressed ≤75 token delta line showing kernel state changes:
//! `[ctx] Δ8t | audit:+N | needles:N→N | fleet:N/N | nudge:N | conflict:none`
//!
//! Also provides `build_heartbeat()` for automatic injection into tool output
//! on first call (boot), every 4 turns, or on tool failure.
//! Per-agent turn state persists in `.ostk/.heartbeat.{alias}`.

use std::fs;
use std::path::Path;

use super::helpers::{count_fleet, count_lines, count_open_needles};

use crate::kernel::heartbeat::{load_turn_state, save_turn_state};

/// Run `ostk context` — output the full context delta to stdout.
pub fn run() -> Result<(), String> {
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
    let ostk_dir = crate::state_dir(&cwd);

    if !ostk_dir.is_dir() {
        return Err("No .ostk directory found. Run `ostk install` first.".to_string());
    }

    let alias = "cli"; // CLI invocation uses a fixed alias
    let st = load_turn_state(&ostk_dir, alias);
    let now = current_epoch_secs();
    let turn_delta = st.current_turn.saturating_sub(st.last_heartbeat_turn);
    let wall_delta = now.saturating_sub(st.last_wall_epoch);
    let heartbeat = build_heartbeat(&ostk_dir, st.last_audit_count, st.last_needle_count, turn_delta, wall_delta);
    println!("{heartbeat}");
    Ok(())
}

/// Build the compressed ≤75 token heartbeat line.
///
/// Reads audit.jsonl count, open needles count, fleet alive/total,
/// pending nudges count, and formats a delta from `prev_audit` / `prev_needles`.
///
/// `turn_delta`: turns since last heartbeat injection for this agent.
/// `wall_delta_secs`: wall-clock seconds since last heartbeat injection.
///   0 means first injection (no prior reference point).
pub fn build_heartbeat(
    ostk_dir: &Path,
    prev_audit: usize,
    prev_needles: usize,
    turn_delta: usize,
    wall_delta_secs: u64,
) -> String {
    let audit_count = count_lines(&ostk_dir.join("audit.jsonl"));
    let open_needles = count_open_needles(&ostk_dir.join("needles").join("issues.jsonl"));
    let (fleet_alive, fleet_total) = count_fleet(&ostk_dir.join("agents.jsonl"));
    let pending_nudges = count_pending_nudges(ostk_dir);

    let audit_delta = audit_count.saturating_sub(prev_audit);
    let needle_transition = if prev_needles == 0 {
        format!("{open_needles}")
    } else if prev_needles != open_needles {
        format!("{prev_needles}\u{2192}{open_needles}")
    } else {
        format!("{open_needles}")
    };

    let nudge_part = if pending_nudges > 0 {
        format!("nudge:{pending_nudges}")
    } else {
        "nudge:0".to_string()
    };

    // Temporal delta: Δ{turns}t:{wall_duration}
    // e.g. Δ8t:45s (active session) or Δ0t:7h (returned after sleep)
    let wall_str = format_wall_delta(wall_delta_secs);
    let temporal = format!("\u{0394}{turn_delta}t:{wall_str}");

    format!(
        "[ctx] {temporal} | audit:+{audit_delta} | needles:{needle_transition} | fleet:{fleet_alive}/{fleet_total} | {nudge_part} | conflict:none"
    )
}

/// Format wall-clock delta for heartbeat display.
fn format_wall_delta(secs: u64) -> String {
    if secs == 0 {
        "boot".to_string()
    } else if secs < 60 {
        format!("{secs}s")
    } else if secs < 3600 {
        format!("{}m", secs / 60)
    } else if secs < 86400 {
        let h = secs / 3600;
        let m = (secs % 3600) / 60;
        if m == 0 { format!("{h}h") } else { format!("{h}h{m}m") }
    } else {
        let d = secs / 86400;
        let h = (secs % 86400) / 3600;
        if h == 0 { format!("{d}d") } else { format!("{d}d{h}h") }
    }
}

/// Increment the turn counter and check if injection is due.
/// Called from the dispatch path on every tool call, scoped to the calling agent.
///
/// Injection triggers:
/// - Every 8 turns since last heartbeat injection
/// - On any tool failure (non-zero exit / error)
///
/// Per-agent state in `.ostk/.heartbeat.{alias}` — concurrent agents
/// track independent temporal positions on the same kernel.
/// Returns Some(heartbeat_line) when injection triggers.
pub fn tick_and_maybe_inject(ostk_dir: &Path, alias: &str, tool_failed: bool) -> Option<String> {
    let mut state = load_turn_state(ostk_dir, alias);
    state.current_turn += 1;

    let now = current_epoch_secs();
    let turns_since_heartbeat = state.current_turn.saturating_sub(state.last_heartbeat_turn);
    // wall_delta: 0 signals "first boot" (no prior wall epoch recorded)
    let wall_delta = if state.last_wall_epoch == 0 {
        0
    } else {
        now.saturating_sub(state.last_wall_epoch)
    };
    // Inject on: first call (boot), every 4 turns, or tool failure.
    // First call is critical — temporal delta tells model if minutes or hours passed.
    let should_inject = tool_failed || state.last_heartbeat_turn == 0 || turns_since_heartbeat >= 4;

    if should_inject {
        let audit_count = count_lines(&ostk_dir.join("audit.jsonl"));
        let open_needles = count_open_needles(&ostk_dir.join("needles").join("issues.jsonl"));

        let heartbeat = build_heartbeat(
            ostk_dir,
            state.last_audit_count,
            state.last_needle_count,
            turns_since_heartbeat,
            wall_delta,
        );
        state.last_heartbeat_turn = state.current_turn;
        state.last_audit_count = audit_count;
        state.last_needle_count = open_needles;
        state.last_wall_epoch = now;
        save_turn_state(ostk_dir, alias, &state);
        let _ = audit_heartbeat(ostk_dir);
        Some(heartbeat)
    } else {
        save_turn_state(ostk_dir, alias, &state);
        None
    }
}

/// Current time as seconds since epoch.
fn current_epoch_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

// ── Helpers ───────────────────────────────────────────────────────────────

/// Count total pending nudges across all agent nudge files.
fn count_pending_nudges(ostk_dir: &Path) -> usize {
    let nudge_dir = ostk_dir.join("nudges");
    if !nudge_dir.is_dir() {
        return 0;
    }
    let mut total = 0usize;
    if let Ok(entries) = fs::read_dir(&nudge_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.ends_with(".jsonl") {
                let content = fs::read_to_string(entry.path()).unwrap_or_default();
                total += content.lines().filter(|l| !l.trim().is_empty()).count();
            }
        }
    }
    total
}

/// Record heartbeat injection in audit.jsonl.
fn audit_heartbeat(ostk_dir: &Path) -> Result<(), String> {
    let audit_path = ostk_dir.join("audit.jsonl");
    let entry = serde_json::json!({
        "event": "heartbeat_injected",
        "ts": crate::now_iso(),
        "agent": "kernel",
    });
    let mut line = serde_json::to_string(&entry).map_err(|e| e.to_string())?;
    line.push('\n');
    fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&audit_path)
        .and_then(|mut f| {
            use std::io::Write;
            f.write_all(line.as_bytes())
        })
        .map_err(|e| format!("failed to audit heartbeat: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_ostk_dir(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir()
            .join("ostk_test_context")
            .join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("needles")).unwrap();
        fs::create_dir_all(dir.join("nudges")).unwrap();
        dir
    }

    #[test]
    fn test_heartbeat_format() {
        let dir = temp_ostk_dir("format");

        // Create audit.jsonl with 5 lines
        let mut audit = String::new();
        for i in 0..5 {
            audit.push_str(&format!("{{\"event\":\"test\",\"n\":{i}}}\n"));
        }
        fs::write(dir.join("audit.jsonl"), &audit).unwrap();

        // Create issues.jsonl with 3 open, 1 closed
        let mut issues = String::new();
        for i in 0..3 {
            issues.push_str(&format!(
                "{{\"id\":\"{i}\",\"status\":\"open\",\"title\":\"test\",\"priority\":\"P1\"}}\n"
            ));
        }
        issues.push_str("{\"id\":\"3\",\"status\":\"closed\",\"title\":\"done\",\"priority\":\"P1\"}\n");
        fs::write(dir.join("needles").join("issues.jsonl"), &issues).unwrap();

        // Create agents.jsonl with 2 active, 1 dead (proper AgentEntry format)
        let agents = concat!(
            r#"{"alias":"a1","pid":1,"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#, "\n",
            r#"{"alias":"a2","pid":2,"status":"active","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#, "\n",
            r#"{"alias":"a3","pid":3,"status":"dead","registered_at":"2026-01-01T00:00:00Z","last_seen":"2026-01-01T00:00:00Z"}"#, "\n",
        );
        fs::write(dir.join("agents.jsonl"), agents).unwrap();

        let heartbeat = build_heartbeat(&dir, 2, 4, 8, 45);

        // Verify format
        assert!(heartbeat.starts_with("[ctx]"), "should start with [ctx]: {heartbeat}");
        assert!(heartbeat.contains("Δ8t:45s"), "should show temporal delta: {heartbeat}");
        // ctx% deliberately omitted — showing it to the model triggers compaction panic loops
        assert!(heartbeat.contains("audit:+3"), "should show audit delta: {heartbeat}");
        assert!(heartbeat.contains("needles:4\u{2192}3"), "should show needle transition: {heartbeat}");
        assert!(heartbeat.contains("fleet:2/3"), "should show fleet: {heartbeat}");
        assert!(heartbeat.contains("conflict:none"), "should show conflict: {heartbeat}");

        // Verify ≤75 tokens (approximate: split on whitespace + punctuation)
        let token_estimate = heartbeat.split_whitespace().count();
        assert!(
            token_estimate <= 75,
            "heartbeat should be ≤75 tokens, got {token_estimate}: {heartbeat}"
        );
    }

    #[test]
    fn test_heartbeat_delta() {
        let dir = temp_ostk_dir("delta");

        // Initial state: 10 audit lines, 5 open needles
        let mut audit = String::new();
        for i in 0..10 {
            audit.push_str(&format!("{{\"event\":\"test\",\"n\":{i}}}\n"));
        }
        fs::write(dir.join("audit.jsonl"), &audit).unwrap();

        let mut issues = String::new();
        for i in 0..5 {
            issues.push_str(&format!(
                "{{\"id\":\"{i}\",\"status\":\"open\",\"title\":\"t\",\"priority\":\"P1\"}}\n"
            ));
        }
        fs::write(dir.join("needles").join("issues.jsonl"), &issues).unwrap();
        fs::write(dir.join("agents.jsonl"), "").unwrap();

        // With prev=10, should show +0
        let hb1 = build_heartbeat(&dir, 10, 5, 8, 30);
        assert!(hb1.contains("audit:+0"), "no change: {hb1}");
        assert!(hb1.contains("needles:5"), "same count shows just current: {hb1}");
        // Should NOT contain arrow when unchanged
        assert!(!hb1.contains("\u{2192}"), "no arrow when unchanged: {hb1}");

        // Add 3 more audit lines
        for i in 10..13 {
            audit.push_str(&format!("{{\"event\":\"new\",\"n\":{i}}}\n"));
        }
        fs::write(dir.join("audit.jsonl"), &audit).unwrap();

        // Close 2 needles (rewrite with 3 open, 2 closed)
        let mut issues2 = String::new();
        for i in 0..3 {
            issues2.push_str(&format!(
                "{{\"id\":\"{i}\",\"status\":\"open\",\"title\":\"t\",\"priority\":\"P1\"}}\n"
            ));
        }
        for i in 3..5 {
            issues2.push_str(&format!(
                "{{\"id\":\"{i}\",\"status\":\"closed\",\"title\":\"t\",\"priority\":\"P1\"}}\n"
            ));
        }
        fs::write(dir.join("needles").join("issues.jsonl"), &issues2).unwrap();

        let hb2 = build_heartbeat(&dir, 10, 5, 8, 120);
        assert!(hb2.contains("audit:+3"), "should show +3: {hb2}");
        assert!(hb2.contains("needles:5\u{2192}3"), "should show transition: {hb2}");
        assert!(hb2.contains("Δ8t:2m"), "should show 2min wall delta: {hb2}");
    }

    #[test]
    fn test_heartbeat_turn_tracking() {
        let dir = temp_ostk_dir("turns");

        // Set up minimal state
        fs::write(dir.join("audit.jsonl"), "").unwrap();
        fs::write(dir.join("needles").join("issues.jsonl"), "").unwrap();
        fs::write(dir.join("agents.jsonl"), "").unwrap();

        let alias = "test-agent";

        // Turn 1: inject immediately (first call = boot)
        let result = tick_and_maybe_inject(&dir, alias, false);
        assert!(result.is_some(), "should inject on first call (boot)");
        let hb = result.unwrap();
        assert!(hb.starts_with("[ctx]"));
        assert!(hb.contains("Δ1t:boot"), "first call should show boot: {hb}");

        // Turns 2-4: no injection
        for _ in 0..3 {
            let result = tick_and_maybe_inject(&dir, alias, false);
            assert!(result.is_none(), "should not inject before 4 turns");
        }

        // Turn 5: should inject (4 turns since last)
        let result = tick_and_maybe_inject(&dir, alias, false);
        assert!(result.is_some(), "should inject at turn 5");
        let hb = result.unwrap();
        assert!(hb.contains("Δ4t:"), "should show 4-turn delta: {hb}");

        // Turns 6-8: no injection
        for _ in 0..3 {
            let result = tick_and_maybe_inject(&dir, alias, false);
            assert!(result.is_none(), "should not inject between heartbeats");
        }

        // Turn 9: should inject again
        let result = tick_and_maybe_inject(&dir, alias, false);
        assert!(result.is_some(), "should inject at turn 9");

        // Immediate injection on tool failure
        let result = tick_and_maybe_inject(&dir, alias, true);
        assert!(result.is_some(), "should inject on tool failure regardless of turn count");
    }

    #[test]
    fn test_heartbeat_with_nudges() {
        let dir = temp_ostk_dir("nudges");

        fs::write(dir.join("audit.jsonl"), "").unwrap();
        fs::write(dir.join("needles").join("issues.jsonl"), "").unwrap();
        fs::write(dir.join("agents.jsonl"), "").unwrap();

        // Add nudges for two agents
        fs::write(
            dir.join("nudges").join("agent-1.jsonl"),
            "{\"message\":\"do X\"}\n{\"message\":\"do Y\"}\n",
        )
        .unwrap();
        fs::write(
            dir.join("nudges").join("agent-2.jsonl"),
            "{\"message\":\"do Z\"}\n",
        )
        .unwrap();

        let hb = build_heartbeat(&dir, 0, 0, 8, 60);
        assert!(hb.contains("nudge:3"), "should count all pending nudges: {hb}");
    }

    #[test]
    fn test_heartbeat_state_persistence() {
        let dir = temp_ostk_dir("persist");
        let alias = "agent-1";

        let state = crate::kernel::heartbeat::TurnState {
            current_turn: 42,
            last_heartbeat_turn: 40,
            last_audit_count: 100,
            last_needle_count: 50,
            last_wall_epoch: 1711800000,
        };
        save_turn_state(&dir, alias, &state);

        let loaded = load_turn_state(&dir, alias);
        assert_eq!(loaded.current_turn, 42);
        assert_eq!(loaded.last_heartbeat_turn, 40);
        assert_eq!(loaded.last_audit_count, 100);
        assert_eq!(loaded.last_needle_count, 50);
        assert_eq!(loaded.last_wall_epoch, 1711800000);
    }

    #[test]
    fn test_heartbeat_state_missing_file() {
        let dir = temp_ostk_dir("missing");

        let loaded = load_turn_state(&dir, "agent-1");
        assert_eq!(loaded.current_turn, 0);
        assert_eq!(loaded.last_heartbeat_turn, 0);
        assert_eq!(loaded.last_audit_count, 0);
        assert_eq!(loaded.last_needle_count, 0);
        assert_eq!(loaded.last_wall_epoch, 0);
    }

    #[test]
    fn test_per_agent_temporal_isolation() {
        let dir = temp_ostk_dir("isolation");

        fs::write(dir.join("audit.jsonl"), "").unwrap();
        fs::write(dir.join("needles").join("issues.jsonl"), "").unwrap();
        fs::write(dir.join("agents.jsonl"), "").unwrap();

        // Agent-1 runs 5 turns (boot + 4 more = 2 injections)
        for _ in 0..5 {
            tick_and_maybe_inject(&dir, "agent-1", false);
        }
        // Agent-2 runs 5 turns
        for _ in 0..5 {
            tick_and_maybe_inject(&dir, "agent-2", false);
        }

        // Each agent should have independent state
        let s1 = load_turn_state(&dir, "agent-1");
        let s2 = load_turn_state(&dir, "agent-2");
        assert_eq!(s1.current_turn, 5);
        assert_eq!(s2.current_turn, 5);
        // Both should have their own wall epoch (non-zero after boot injection)
        assert!(s1.last_wall_epoch > 0);
        assert!(s2.last_wall_epoch > 0);

        // Run agent-1 for 5 more turns — agent-2 should be unaffected
        for _ in 0..5 {
            tick_and_maybe_inject(&dir, "agent-1", false);
        }
        let s1 = load_turn_state(&dir, "agent-1");
        let s2 = load_turn_state(&dir, "agent-2");
        assert_eq!(s1.current_turn, 10);
        assert_eq!(s2.current_turn, 5, "agent-2 should be unaffected by agent-1 turns");
    }

    #[test]
    fn test_wall_delta_formats() {
        assert_eq!(format_wall_delta(0), "boot");
        assert_eq!(format_wall_delta(30), "30s");
        assert_eq!(format_wall_delta(90), "1m");
        assert_eq!(format_wall_delta(3661), "1h1m");
        assert_eq!(format_wall_delta(7200), "2h");
        assert_eq!(format_wall_delta(90000), "1d1h");
        assert_eq!(format_wall_delta(86400), "1d");
    }
}
