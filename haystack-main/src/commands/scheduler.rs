//! →629: llmOS scheduler loop — §3 of docs/spec/llmos-concurrency.md
//!
//! READ → ORIENT → DECIDE → DISPATCH → MONITOR → YIELD
//!
//! The scheduler runs at every turn boundary. It reads OS state (diff,
//! active.tack, vault, fleet), orients on intent and system state, decides
//! what to dispatch, fires the dispatch, writes nudges, then yields until
//! the next tack submission.

use std::fs;
use std::path::Path;

use crate::commands::run::{resolve_auto_with_lookup};
use crate::kernel::nudge;

// ---------------------------------------------------------------------------
// OS state read
// ---------------------------------------------------------------------------

/// Snapshot of OS state read at the start of each scheduler turn.
#[derive(Debug)]
pub struct OsSnapshot {
    /// Session delta since boot (ostk diff output).
    pub diff: String,
    /// Current human intent draft (.ostk/staging/active.tack).
    pub active_tack: Option<String>,
    /// Available models from vault (env-key presence).
    pub vault_models: Vec<String>,
    /// Fleet thread states read from .ostk/fleet/*/state.yml.
    pub fleet_states: Vec<FleetState>,
    /// Agents flagged as dying (have dying.lock).
    pub dying_agents: Vec<String>,
}

#[derive(Debug)]
pub struct FleetState {
    pub alias: String,
    pub state: String, // init/ready/running/blocked/dying/idle/reaped
}

/// READ: load full OS snapshot from .ostk/.
pub fn read_os_state(ostk_dir: &Path) -> OsSnapshot {
    let diff = read_diff(ostk_dir);
    let active_tack = read_active_tack(ostk_dir);
    let vault_models = read_vault_models();
    let fleet_states = read_fleet_states(ostk_dir);
    let dying_agents = fleet_states
        .iter()
        .filter(|s| s.state == "dying")
        .map(|s| s.alias.clone())
        .collect();

    OsSnapshot {
        diff,
        active_tack,
        vault_models,
        fleet_states,
        dying_agents,
    }
}

fn read_diff(ostk_dir: &Path) -> String {
    let diff_path = ostk_dir.join("staging/diff.md");
    fs::read_to_string(diff_path).unwrap_or_default()
}

fn read_active_tack(ostk_dir: &Path) -> Option<String> {
    let tack_path = ostk_dir.join("staging/active.tack");
    fs::read_to_string(tack_path).ok().filter(|s| !s.trim().is_empty())
}

fn read_vault_models() -> Vec<String> {
    // Mirror resolve_auto_with_lookup scoring but return all available
    let candidates = [
        ("claude-opus-4-6",   "ANTHROPIC_API_KEY"),
        ("claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
        ("gpt-4o",            "OPENAI_API_KEY"),
        ("mistral-large-latest", "MISTRAL_API_KEY"),
        ("gemini-2.0-flash",  "GEMINI_API_KEY"),
    ];
    candidates
        .iter()
        .filter(|(_, key)| std::env::var(key).is_ok_and(|v| !v.is_empty()))
        .map(|(model, _)| model.to_string())
        .collect()
}

fn read_fleet_states(ostk_dir: &Path) -> Vec<FleetState> {
    let fleet_dir = ostk_dir.join("fleet");
    let Ok(entries) = fs::read_dir(&fleet_dir) else { return Vec::new() };

    let mut states = Vec::new();
    for entry in entries.flatten() {
        let alias = entry.file_name().to_string_lossy().to_string();
        let state_path = entry.path().join("state.yml");
        let state = fs::read_to_string(&state_path)
            .ok()
            .and_then(|s| {
                s.lines()
                    .find(|l| l.starts_with("state:"))
                    .map(|l| l.trim_start_matches("state:").trim().to_string())
            })
            .unwrap_or_else(|| "unknown".to_string());
        states.push(FleetState { alias, state });
    }
    states
}

// ---------------------------------------------------------------------------
// ORIENT: parse intent from tack
// ---------------------------------------------------------------------------

/// Parsed scheduler intent from active.tack content.
#[derive(Debug, PartialEq)]
pub enum SchedulerIntent {
    /// Hard command: :verb
    Command(String),
    /// Soft probe: .? query
    Query(String),
    /// Hay (raw intent, file for compilation)
    Hay(String),
    /// No intent yet
    Idle,
}

/// ORIENT: parse tack into scheduler intent.
pub fn orient(snapshot: &OsSnapshot) -> SchedulerIntent {
    let Some(ref tack) = snapshot.active_tack else {
        return SchedulerIntent::Idle;
    };
    let tack = tack.trim();

    if let Some(rest) = tack.strip_prefix(':') {
        return SchedulerIntent::Command(rest.trim().to_string());
    }
    if let Some(rest) = tack.strip_prefix(".? ") {
        return SchedulerIntent::Query(rest.trim().to_string());
    }
    if !tack.is_empty() {
        return SchedulerIntent::Hay(tack.to_string());
    }
    SchedulerIntent::Idle
}

// ---------------------------------------------------------------------------
// DECIDE: select model + action
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub struct SchedulerDecision {
    pub model: String,
    pub action: SchedulerAction,
    pub reap_aliases: Vec<String>,
}

#[derive(Debug)]
pub enum SchedulerAction {
    /// Execute a tack command
    Execute(String),
    /// Re-dispatch a dying agent with fresh context
    RedispatchDying { alias: String, dying_md: String },
    /// File hay for compilation
    FileHay(String),
    /// No action — idle
    Idle,
}

/// DECIDE: select model and action from snapshot + intent.
pub fn decide(snapshot: &OsSnapshot, intent: SchedulerIntent, ostk_dir: &Path) -> SchedulerDecision {
    // Model selection via FROM auto
    let model = resolve_auto_with_lookup(|key| std::env::var(key).ok());

    // Dying agents take priority — re-dispatch before new work
    if let Some(dying_alias) = snapshot.dying_agents.first() {
        let dying_md = crate::kernel::dying::read_dying_md(ostk_dir, dying_alias)
            .unwrap_or_else(|| format!("# {dying_alias} was dying — no declaration found"));
        return SchedulerDecision {
            model,
            action: SchedulerAction::RedispatchDying {
                alias: dying_alias.clone(),
                dying_md,
            },
            reap_aliases: reaped_aliases(snapshot),
        };
    }

    let action = match intent {
        SchedulerIntent::Command(cmd) => SchedulerAction::Execute(cmd),
        SchedulerIntent::Hay(text) => SchedulerAction::FileHay(text),
        SchedulerIntent::Query(q) => SchedulerAction::Execute(format!("query {q}")),
        SchedulerIntent::Idle => SchedulerAction::Idle,
    };

    SchedulerDecision {
        model,
        action,
        reap_aliases: reaped_aliases(snapshot),
    }
}

fn reaped_aliases(snapshot: &OsSnapshot) -> Vec<String> {
    snapshot.fleet_states
        .iter()
        .filter(|s| s.state == "reaped" || s.state == "idle")
        .map(|s| s.alias.clone())
        .collect()
}

// ---------------------------------------------------------------------------
// MONITOR: write nudges from scheduler decisions
// ---------------------------------------------------------------------------

/// MONITOR: surface scheduler decisions as nudges.
pub fn monitor(decision: &SchedulerDecision, ostk_dir: &Path) {
    match &decision.action {
        SchedulerAction::RedispatchDying { alias, .. } => {
            let msg = format!(
                ":scheduler re-dispatching {alias} (was dying) with model {}",
                decision.model
            );
            let _ = nudge::push_nudge(ostk_dir, "operator", &msg);
        }
        SchedulerAction::Execute(cmd) => {
            let msg = format!(":scheduler dispatching :{cmd} via {}", decision.model);
            let _ = nudge::push_nudge(ostk_dir, "operator", &msg);
        }
        SchedulerAction::FileHay(text) => {
            let preview = crate::util::safe_truncate(text, 60);
            let _ = nudge::push_nudge(ostk_dir, "operator", &format!(":scheduler filing hay: {preview}…"));
        }
        SchedulerAction::Idle => {}
    }
}

// ---------------------------------------------------------------------------
// SCHEDULER — entry point for `ostk scheduler`
// ---------------------------------------------------------------------------

/// Run one scheduler turn: READ → ORIENT → DECIDE → MONITOR → print decision.
pub fn run_scheduler_turn(ostk_dir: &Path) -> Result<(), String> {
    let snapshot = read_os_state(ostk_dir);
    let intent = orient(&snapshot);
    let decision = decide(&snapshot, intent, ostk_dir);
    monitor(&decision, ostk_dir);

    // Print decision for human review (YIELD — scheduler surfaces, human approves)
    match &decision.action {
        SchedulerAction::Idle => println!(":scheduler idle — no active intent"),
        SchedulerAction::Execute(cmd) => {
            println!(":scheduler → :{cmd}  [model: {}]", decision.model);
        }
        SchedulerAction::RedispatchDying { alias, .. } => {
            println!(":scheduler re-dispatching {alias}  [model: {}]", decision.model);
        }
        SchedulerAction::FileHay(text) => {
            println!(":scheduler filing hay ({} chars)", text.len());
        }
    }

    if !decision.reap_aliases.is_empty() {
        println!(":scheduler reaping: {}", decision.reap_aliases.join(", "));
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use tempfile::TempDir;

    fn setup() -> (TempDir, PathBuf) {
        let tmp = TempDir::new().unwrap();
        let hs = tmp.path().join(".ostk");
        fs::create_dir_all(hs.join("staging")).unwrap();
        fs::create_dir_all(hs.join("fleet")).unwrap();
        fs::create_dir_all(hs.join("nudges")).unwrap();
        (tmp, hs)
    }

    #[test]
    fn test_orient_command() {
        let snap = OsSnapshot {
            diff: String::new(),
            active_tack: Some(":compile".to_string()),
            vault_models: vec![],
            fleet_states: vec![],
            dying_agents: vec![],
        };
        assert_eq!(orient(&snap), SchedulerIntent::Command("compile".to_string()));
    }

    #[test]
    fn test_orient_idle() {
        let snap = OsSnapshot {
            active_tack: None,
            diff: String::new(), vault_models: vec![], fleet_states: vec![], dying_agents: vec![],
        };
        assert_eq!(orient(&snap), SchedulerIntent::Idle);
    }

    #[test]
    fn test_orient_hay() {
        let snap = OsSnapshot {
            active_tack: Some("thinking about the scheduler model".to_string()),
            diff: String::new(), vault_models: vec![], fleet_states: vec![], dying_agents: vec![],
        };
        assert!(matches!(orient(&snap), SchedulerIntent::Hay(_)));
    }

    #[test]
    fn test_read_active_tack() {
        let (_tmp, hs) = setup();
        fs::write(hs.join("staging/active.tack"), ":compile").unwrap();
        let snap = read_os_state(&hs);
        assert_eq!(snap.active_tack, Some(":compile".to_string()));
    }

    #[test]
    fn test_read_fleet_states() {
        let (_tmp, hs) = setup();
        let agent_dir = hs.join("fleet/agent-1");
        fs::create_dir_all(&agent_dir).unwrap();
        fs::write(agent_dir.join("state.yml"), "alias: agent-1\nstate: running\nupdated: 2026-03-12T00:00:00Z\n").unwrap();
        let snap = read_os_state(&hs);
        assert_eq!(snap.fleet_states.len(), 1);
        assert_eq!(snap.fleet_states[0].state, "running");
    }

    #[test]
    fn test_dying_agents_prioritized() {
        let (_tmp, hs) = setup();
        let agent_dir = hs.join("fleet/agent-dying");
        fs::create_dir_all(&agent_dir).unwrap();
        fs::write(agent_dir.join("state.yml"), "alias: agent-dying\nstate: dying\n").unwrap();
        fs::write(agent_dir.join("dying.lock"), "dying").unwrap();
        fs::write(agent_dir.join("dying.md"), "# dying — agent-dying\nneedle: →629\n").unwrap();

        let snap = read_os_state(&hs);
        assert_eq!(snap.dying_agents, vec!["agent-dying"]);

        let intent = SchedulerIntent::Command("compile".to_string());
        let decision = decide(&snap, intent, &hs);
        assert!(matches!(decision.action, SchedulerAction::RedispatchDying { .. }));
    }
}
