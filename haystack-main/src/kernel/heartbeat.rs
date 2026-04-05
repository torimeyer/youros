/// Heartbeat and crash detection for the ostk kernel.
///
/// On every tool call: write timestamp to agent's entry in agents.jsonl.
/// check_health() reads all agents, returns: active (<30s), stale (30-90s), crashed (>90s).
use crate::kernel::identity::Identity;
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

/// Agent health status based on heartbeat age.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum HealthStatus {
    /// Last heartbeat within 30 seconds
    Active,
    /// Last heartbeat 30-90 seconds ago
    Stale,
    /// Last heartbeat more than 90 seconds ago (or never)
    Crashed,
}

impl std::fmt::Display for HealthStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HealthStatus::Active => write!(f, "active"),
            HealthStatus::Stale => write!(f, "stale"),
            HealthStatus::Crashed => write!(f, "crashed"),
        }
    }
}

/// Health report for a single agent.
#[derive(Debug, Clone)]
pub struct AgentHealth {
    /// Agent alias
    pub alias: String,
    /// Health status
    pub status: HealthStatus,
    /// Seconds since last heartbeat
    pub age_secs: u64,
    /// Process ID
    pub pid: u32,
    /// Context utilization percent (0-100), from agents.jsonl context_pct field (→628)
    pub context_pct: u8,
}

/// Thresholds for health status (in seconds).
pub const ACTIVE_THRESHOLD: u64 = 30;
pub const STALE_THRESHOLD: u64 = 90;

/// Record a heartbeat for the given agent.
/// Updates the last_seen timestamp in agents.jsonl.
pub fn record_heartbeat(ostk_dir: &Path, alias: &str) -> Result<(), String> {
    let identity = Identity::new(ostk_dir);
    identity.heartbeat(alias)
}

/// Check health of all registered agents.
///
/// Reads agents.jsonl and compares last_seen timestamps against current time.
/// Returns health status for all agents marked as "active" in the registry.
pub fn check_health(ostk_dir: &Path) -> Result<Vec<AgentHealth>, String> {
    check_health_with_thresholds(ostk_dir, ACTIVE_THRESHOLD, STALE_THRESHOLD)
}

/// Check health with custom thresholds (useful for testing).
pub fn check_health_with_thresholds(
    ostk_dir: &Path,
    active_threshold_secs: u64,
    stale_threshold_secs: u64,
) -> Result<Vec<AgentHealth>, String> {
    let identity = Identity::new(ostk_dir);
    let agents = identity.read_agents()?;

    let now = current_epoch_secs();
    let mut results = Vec::new();

    for agent in &agents {
        if agent.status != "active" {
            continue;
        }

        let last_seen_epoch = parse_iso_to_epoch(&agent.last_seen).unwrap_or(0);
        let age_secs = now.saturating_sub(last_seen_epoch);

        let status = if age_secs < active_threshold_secs {
            HealthStatus::Active
        } else if age_secs < stale_threshold_secs {
            HealthStatus::Stale
        } else if agent.pid > 0 && is_process_alive(agent.pid) {
            // →802: idle but alive — don't misclassify as crashed
            HealthStatus::Stale
        } else {
            HealthStatus::Crashed
        };

        results.push(AgentHealth {
            alias: agent.alias.clone(),
            status,
            age_secs,
            pid: agent.pid,
            context_pct: load_context_pct_for_agent(ostk_dir, &agent.alias),
        });
    }

    Ok(results)
}

/// Compute context utilization for a specific agent from its session file (->749).
///
/// Uses the same heuristic as the TUI's load_context_pct: count non-empty
/// lines in `.ostk/sessions/{alias}.jsonl` and scale against a 2000-line
/// baseline (~200k tokens at ~100 tokens per session entry).
/// Returns 0 if no session file exists.
pub fn load_context_pct_for_agent(ostk_dir: &Path, alias: &str) -> u8 {
    let session_path = ostk_dir.join("sessions").join(format!("{alias}.jsonl"));
    let content = match std::fs::read_to_string(&session_path) {
        Ok(c) => c,
        Err(_) => return 0,
    };
    let line_count = content.lines().filter(|l| !l.trim().is_empty()).count();
    // Heuristic: 200k token context, ~100 tokens per session entry.
    // 2000 lines ~ 200k tokens = 100% context
    ((line_count as f64 / 2000.0) * 100.0).min(100.0) as u8
}

/// Format health info for the [procs] digest line.
/// HSCP G2: extract numeric suffix from alias for compact encoding.
/// `agent-1` → `1`, `agent-42` → `42`, `worker` → `worker`
/// Intent-preserving: `a1:active:5s:12%` is readable without a table.
fn agent_short(alias: &str) -> &str {
    // Find last run of digits
    let bytes = alias.as_bytes();
    let mut end = bytes.len();
    while end > 0 && bytes[end - 1].is_ascii_digit() {
        end -= 1;
    }
    if end < bytes.len() {
        &alias[end..]
    } else {
        alias
    }
}

pub fn format_procs_digest(health: &[AgentHealth]) -> String {
    if health.is_empty() {
        return "[procs] (none)".to_string();
    }
    let entries: Vec<String> = health
        .iter()
        .map(|h| {
            let age = format_duration(h.age_secs);
            // HSCP G2: a{N}:{status}:{age}:{ctx}% — intent-preserving compression
            format!("a{}:{}:{}:{}%", agent_short(&h.alias), h.status, age, h.context_pct)
        })
        .collect();
    format!("[procs] {}", entries.join(" "))
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

/// Parse an ISO 8601 timestamp (2026-03-07T12:34:56Z) into seconds since epoch.
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

/// Convert year/month/day to days since Unix epoch (1970-01-01).
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

// ── Turn-based heartbeat state (.ostk/.heartbeat) ────────────────────────────
//
// Separate from agent health heartbeats (agents.jsonl last_seen).
// This tracks the context injection cadence: every 8 turns or on tool failure.

/// Turn-based heartbeat state persisted between tool calls.
/// Per-agent: stored in `.ostk/.heartbeat.{alias}` so concurrent agents
/// track independent temporal positions on the same kernel.
#[derive(Debug, Clone)]
pub struct TurnState {
    /// Current turn counter (incremented every tool call).
    pub current_turn: usize,
    /// Turn number when the last heartbeat was injected.
    pub last_heartbeat_turn: usize,
    /// Audit event count at last injection.
    pub last_audit_count: usize,
    /// Open needle count at last injection.
    pub last_needle_count: usize,
    /// Wall-clock epoch seconds at last heartbeat injection.
    /// Enables temporal delta: model sees `Δ8t:7h12m` (turns + wall time).
    pub last_wall_epoch: u64,
}

/// Load turn-based heartbeat state for an agent from `.ostk/.heartbeat.{alias}`.
/// Falls back to global `.ostk/.heartbeat` for backward compatibility.
pub fn load_turn_state(ostk_dir: &Path, alias: &str) -> TurnState {
    let per_agent = ostk_dir.join(format!(".heartbeat.{alias}"));
    let path = if per_agent.exists() {
        per_agent
    } else {
        ostk_dir.join(".heartbeat")
    };
    let content = std::fs::read_to_string(path).unwrap_or_default();
    let mut lines = content.lines();
    let current_turn = lines.next().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let last_heartbeat_turn = lines.next().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let last_audit_count = lines.next().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let last_needle_count = lines.next().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let last_wall_epoch = lines.next().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    TurnState { current_turn, last_heartbeat_turn, last_audit_count, last_needle_count, last_wall_epoch }
}

/// Save turn-based heartbeat state for an agent to `.ostk/.heartbeat.{alias}`.
pub fn save_turn_state(ostk_dir: &Path, alias: &str, state: &TurnState) {
    let path = ostk_dir.join(format!(".heartbeat.{alias}"));
    let content = format!(
        "{}\n{}\n{}\n{}\n{}\n",
        state.current_turn, state.last_heartbeat_turn,
        state.last_audit_count, state.last_needle_count,
        state.last_wall_epoch
    );
    let _ = std::fs::write(path, content);
}

/// Check if a process is still alive using kill(pid, 0).
/// Signal 0 tests existence without actually sending a signal.
/// Uses nix::sys::signal::kill directly — no subprocess needed.
fn is_process_alive(pid: u32) -> bool {
    nix::sys::signal::kill(nix::unistd::Pid::from_raw(pid as i32), None).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::identity::Identity;
    use std::fs;
    use std::path::PathBuf;

    fn temp_ostk_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join("ostk_test_heartbeat")
            .join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_parse_iso_roundtrip() {
        let now_epoch = current_epoch_secs();
        // Reconstruct ISO from epoch
        let secs = now_epoch;
        let days = secs / 86400;
        let time_secs = secs % 86400;
        let hours = time_secs / 3600;
        let minutes = (time_secs % 3600) / 60;
        let seconds = time_secs % 60;

        let z = days + 719468;
        let era = z / 146097;
        let doe = z - era * 146097;
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        let y = if m <= 2 { y + 1 } else { y };
        let now_iso = format!("{y:04}-{m:02}-{d:02}T{hours:02}:{minutes:02}:{seconds:02}Z");

        let parsed = parse_iso_to_epoch(&now_iso).unwrap();
        assert_eq!(parsed, now_epoch);
    }

    #[test]
    fn test_agent_active() {
        let dir = temp_ostk_dir("agent_active");
        let identity = Identity::new(&dir);

        let alias = identity.assign_alias_with(None).unwrap();
        assert_eq!(alias, "agent-1");

        let health = check_health(&dir).unwrap();
        assert_eq!(health.len(), 1);
        assert_eq!(health[0].alias, "agent-1");
        assert_eq!(health[0].status, HealthStatus::Active);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_detect_stale_with_short_thresholds() {
        let dir = temp_ostk_dir("detect_stale");
        let identity = Identity::new(&dir);

        let alias = identity.assign_alias_with(None).unwrap();
        assert_eq!(alias, "agent-1");

        // With active_threshold=0, even a fresh agent is "stale"
        let health = check_health_with_thresholds(&dir, 0, 1).unwrap();
        assert_eq!(health.len(), 1);
        assert!(
            health[0].status == HealthStatus::Stale || health[0].status == HealthStatus::Crashed
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_heartbeat_refreshes_status() {
        let dir = temp_ostk_dir("heartbeat_refresh");
        let identity = Identity::new(&dir);

        let alias = identity.assign_alias_with(None).unwrap();

        record_heartbeat(&dir, &alias).unwrap();

        let health = check_health(&dir).unwrap();
        assert_eq!(health.len(), 1);
        assert_eq!(health[0].status, HealthStatus::Active);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_multiple_agents_health() {
        let dir = temp_ostk_dir("multi_health");
        let identity = Identity::new(&dir);

        let a1 = identity.assign_alias_with(None).unwrap();
        let a2 = identity.assign_alias_with(None).unwrap();

        record_heartbeat(&dir, &a1).unwrap();
        record_heartbeat(&dir, &a2).unwrap();

        let health = check_health(&dir).unwrap();
        assert_eq!(health.len(), 2);
        assert!(health.iter().all(|h| h.status == HealthStatus::Active));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_format_procs_digest() {
        let health = vec![
            AgentHealth {
                alias: "agent-1".to_string(),
                status: HealthStatus::Active,
                age_secs: 5,
                pid: 1234,
                context_pct: 12,
            },
            AgentHealth {
                alias: "agent-2".to_string(),
                status: HealthStatus::Stale,
                age_secs: 45,
                pid: 5678,
                context_pct: 67,
            },
        ];
        let digest = format_procs_digest(&health);
        // HSCP G2: a{N}:{status}:{age}:{ctx}% — intent-preserving compression
        assert_eq!(digest, "[procs] a1:active:5s:12% a2:stale:45s:67%");
    }

    #[test]
    fn test_format_duration() {
        assert_eq!(format_duration(5), "5s");
        assert_eq!(format_duration(59), "59s");
        assert_eq!(format_duration(60), "1m");
        assert_eq!(format_duration(120), "2m");
        assert_eq!(format_duration(3600), "1h");
        assert_eq!(format_duration(7200), "2h");
    }

    #[test]
    fn test_deregistered_agent_excluded() {
        let dir = temp_ostk_dir("deregistered");
        let identity = Identity::new(&dir);

        let alias = identity.assign_alias_with(None).unwrap();
        identity.deregister(&alias).unwrap();

        let health = check_health(&dir).unwrap();
        assert!(health.is_empty());

        let _ = fs::remove_dir_all(&dir);
    }

    // -----------------------------------------------------------------------
    // ->749: context_pct from session file
    // -----------------------------------------------------------------------

    #[test]
    fn test_context_pct_no_session_file() {
        let dir = temp_ostk_dir("ctx_no_session");
        // No sessions dir at all
        assert_eq!(load_context_pct_for_agent(&dir, "agent-1"), 0);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_context_pct_empty_session() {
        let dir = temp_ostk_dir("ctx_empty_session");
        let sessions = dir.join("sessions");
        fs::create_dir_all(&sessions).unwrap();
        fs::write(sessions.join("agent-1.jsonl"), "").unwrap();
        assert_eq!(load_context_pct_for_agent(&dir, "agent-1"), 0);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_context_pct_scales_with_lines() {
        let dir = temp_ostk_dir("ctx_scaled");
        let sessions = dir.join("sessions");
        fs::create_dir_all(&sessions).unwrap();
        // 200 lines = 10% of 2000
        let content: String = (0..200).map(|i| format!("{{\"line\":{i}}}\n")).collect();
        fs::write(sessions.join("agent-1.jsonl"), &content).unwrap();
        assert_eq!(load_context_pct_for_agent(&dir, "agent-1"), 10);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_context_pct_caps_at_100() {
        let dir = temp_ostk_dir("ctx_capped");
        let sessions = dir.join("sessions");
        fs::create_dir_all(&sessions).unwrap();
        // 3000 lines > 2000 baseline -> should cap at 100
        let content: String = (0..3000).map(|i| format!("{{\"line\":{i}}}\n")).collect();
        fs::write(sessions.join("agent-1.jsonl"), &content).unwrap();
        assert_eq!(load_context_pct_for_agent(&dir, "agent-1"), 100);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_context_pct_integrated_with_health() {
        let dir = temp_ostk_dir("ctx_integrated");
        let identity = Identity::new(&dir);
        let alias = identity.assign_alias_with(None).unwrap();

        // Create a session file for this agent with 400 lines = 20%
        let sessions = dir.join("sessions");
        fs::create_dir_all(&sessions).unwrap();
        let content: String = (0..400).map(|i| format!("{{\"line\":{i}}}\n")).collect();
        fs::write(sessions.join(format!("{alias}.jsonl")), &content).unwrap();

        let health = check_health(&dir).unwrap();
        assert_eq!(health.len(), 1);
        assert_eq!(health[0].context_pct, 20, "context_pct should reflect session file size");

        let _ = fs::remove_dir_all(&dir);
    }

    // -----------------------------------------------------------------------
    // is_process_alive: nix kill(pid, 0) — no subprocess
    // -----------------------------------------------------------------------

    #[test]
    fn test_is_process_alive_current_process() {
        // Our own process is always alive.
        let pid = std::process::id();
        assert!(is_process_alive(pid), "current process should be alive");
    }

    #[test]
    fn test_is_process_alive_bogus_pid() {
        // PID 4_000_000 is almost certainly not running.
        assert!(!is_process_alive(4_000_000), "bogus PID should not be alive");
    }
}
