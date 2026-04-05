/// Recovery digest for the ostk kernel.
///
/// Logs every tool call to .ostk/sessions/<agent-alias>.jsonl.
/// On reconnect (same alias), generates a structural summary:
///   "Last session: N tool calls. Edited X (gen A->B). Ran cargo test (exit 0)."
///
/// Grammar-compressed: actions not reasoning. The kernel sees every tool call
/// and compresses it to structural summaries the agent can pattern-match.
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// A logged tool call entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallEntry {
    /// Tool name (e.g. "shell", "file:edit", "file:read")
    pub tool: String,
    /// Brief description of args (e.g. command string, file path)
    pub args_summary: String,
    /// Result summary (e.g. "exit:0", "ok", "error: ...")
    pub result_summary: String,
    /// ISO 8601 timestamp
    pub timestamp: String,
}

/// Recovery summary for a reconnecting agent.
#[derive(Debug, Clone)]
pub struct RecoverySummary {
    /// Total number of tool calls in the previous session
    pub total_calls: usize,
    /// Files edited with generation changes
    pub edits: Vec<EditSummary>,
    /// Commands run with their exit codes
    pub commands: Vec<CommandSummary>,
    /// Files read
    pub reads: Vec<String>,
    /// Time since last tool call
    pub age: String,
    /// Formatted recovery string
    pub formatted: String,
}

/// Summary of a file edit in the recovery digest.
#[derive(Debug, Clone)]
pub struct EditSummary {
    pub path: String,
    pub gen_changes: String,
}

/// Summary of a command run in the recovery digest.
#[derive(Debug, Clone)]
pub struct CommandSummary {
    pub command: String,
    pub result: String,
}

/// Session logger and recovery generator.
pub struct Recovery {
    ostk_dir: PathBuf,
}

impl Recovery {
    pub fn new(ostk_dir: &Path) -> Self {
        Recovery {
            ostk_dir: ostk_dir.to_path_buf(),
        }
    }

    fn sessions_dir(&self) -> PathBuf {
        self.ostk_dir.join("sessions")
    }

    fn session_path(&self, alias: &str) -> PathBuf {
        self.sessions_dir().join(format!("{alias}.jsonl"))
    }

    /// Log a tool call for the given agent.
    pub fn log_tool_call(
        &self,
        alias: &str,
        tool: &str,
        args_summary: &str,
        result_summary: &str,
    ) -> Result<(), String> {
        let sessions_dir = self.sessions_dir();
        fs::create_dir_all(&sessions_dir).map_err(|e| format!("create sessions dir: {e}"))?;

        let entry = ToolCallEntry {
            tool: tool.to_string(),
            args_summary: args_summary.to_string(),
            result_summary: result_summary.to_string(),
            timestamp: now_iso(),
        };

        let path = self.session_path(alias);
        let line = serde_json::to_string(&entry).map_err(|e| e.to_string())?;

        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|e| format!("open session log: {e}"))?;

        file.write_all(line.as_bytes())
            .map_err(|e| format!("write session log: {e}"))?;
        file.write_all(b"\n")
            .map_err(|e| format!("write newline: {e}"))?;

        Ok(())
    }

    /// Read all tool call entries for an agent's session.
    pub fn read_session(&self, alias: &str) -> Result<Vec<ToolCallEntry>, String> {
        let path = self.session_path(alias);
        if !path.exists() {
            return Ok(vec![]);
        }

        let content = fs::read_to_string(&path).map_err(|e| format!("read session log: {e}"))?;

        let mut entries = Vec::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(entry) = serde_json::from_str::<ToolCallEntry>(line) {
                entries.push(entry);
            }
        }
        Ok(entries)
    }

    /// Generate a recovery summary from a previous session.
    ///
    /// Grammar-compressed: structural, not narrative.
    /// Actions, not reasoning. Pattern-matchable by the agent.
    pub fn generate_recovery(&self, alias: &str) -> Result<Option<RecoverySummary>, String> {
        let entries = self.read_session(alias)?;
        if entries.is_empty() {
            return Ok(None);
        }

        let total_calls = entries.len();
        let mut edit_map: HashMap<String, Vec<String>> = HashMap::new();
        let mut commands = Vec::new();
        let mut reads = Vec::new();

        for entry in &entries {
            match entry.tool.as_str() {
                "fs_ops" | "file:edit" | "str_replace" | "file_edit" => {
                    let gen_info = if entry.result_summary.contains("gen") {
                        entry.result_summary.clone()
                    } else {
                        "ok".to_string()
                    };
                    edit_map
                        .entry(entry.args_summary.clone())
                        .or_default()
                        .push(gen_info);
                }
                "fs_read" | "file:read" | "file_read" | "read" => {
                    if !reads.contains(&entry.args_summary) {
                        reads.push(entry.args_summary.clone());
                    }
                }
                "shell" | "sh_run" | "spawn" | "sh_spawn" | "command" => {
                    commands.push(CommandSummary {
                        command: truncate_str(&entry.args_summary, 60),
                        result: entry.result_summary.clone(),
                    });
                }
                _ => {}
            }
        }

        let edits: Vec<EditSummary> = edit_map
            .into_iter()
            .map(|(path, gens)| EditSummary {
                path,
                gen_changes: if gens.len() == 1 {
                    gens[0].clone()
                } else {
                    format!("{} edits", gens.len())
                },
            })
            .collect();

        let last_ts = entries.last().map(|e| e.timestamp.as_str()).unwrap_or("");
        let age = if let Some(last_epoch) = parse_iso_to_epoch(last_ts) {
            let now = current_epoch_secs();
            format_duration(now.saturating_sub(last_epoch))
        } else {
            "unknown".to_string()
        };

        let mut parts = Vec::new();
        parts.push(format!("Last session: {total_calls} tool calls."));

        if !edits.is_empty() {
            let edit_strs: Vec<String> = edits
                .iter()
                .map(|e| format!("{} ({})", e.path, e.gen_changes))
                .collect();
            parts.push(format!("Edited {}.", edit_strs.join(", ")));
        }

        if !commands.is_empty() {
            let cmd_strs: Vec<String> = commands
                .iter()
                .rev()
                .take(3)
                .rev()
                .map(|c| format!("{} ({})", c.command, c.result))
                .collect();
            parts.push(format!("Ran {}.", cmd_strs.join(", ")));
        }

        if !reads.is_empty() {
            parts.push(format!("Read {} files.", reads.len()));
        }

        parts.push(format!("Session ended {age} ago."));

        let formatted = parts.join(" ");

        Ok(Some(RecoverySummary {
            total_calls,
            edits,
            commands,
            reads,
            age,
            formatted,
        }))
    }

    /// Clear a session log (after recovery has been consumed).
    pub fn clear_session(&self, alias: &str) -> Result<(), String> {
        let path = self.session_path(alias);
        if path.exists() {
            let prev = self.sessions_dir().join(format!("{alias}.prev.jsonl"));
            fs::rename(&path, &prev).map_err(|e| format!("rename session log: {e}"))?;
        }
        Ok(())
    }
}

fn truncate_str(s: &str, max_len: usize) -> String {
    if s.len() <= max_len {
        s.to_string()
    } else {
        let end = max_len.saturating_sub(3);
        // Find the nearest char boundary at or before `end` to avoid panicking
        // on multi-byte UTF-8 sequences (e.g. em-dash U+2014 is 3 bytes).
        let safe_end = s.floor_char_boundary(end);
        format!("{}...", &s[..safe_end])
    }
}

fn now_iso() -> String {
    let dur = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = dur.as_secs();
    let days = secs / 86400;
    let time_secs = secs % 86400;
    let hours = time_secs / 3600;
    let minutes = (time_secs % 3600) / 60;
    let seconds = time_secs % 60;
    let (year, month, day) = days_to_ymd(days);
    format!("{year:04}-{month:02}-{day:02}T{hours:02}:{minutes:02}:{seconds:02}Z")
}

fn format_duration(secs: u64) -> String {
    if secs < 60 {
        format!("{secs}s")
    } else if secs < 3600 {
        format!("{}m", secs / 60)
    } else {
        format!("{}h", secs / 3600)
    }
}

fn current_epoch_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

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

fn days_to_ymd(days: u64) -> (u64, u64, u64) {
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
    (y, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn temp_ostk_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join("ostk_test_recovery")
            .join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn test_log_and_read() {
        let dir = temp_ostk_dir("log_read");
        let recovery = Recovery::new(&dir);

        recovery
            .log_tool_call("agent-1", "sh_run", "cargo test", "exit:0")
            .unwrap();
        recovery
            .log_tool_call("agent-1", "fs_ops", "src/main.rs", "ok gen=5")
            .unwrap();

        let entries = recovery.read_session("agent-1").unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].tool, "sh_run");
        assert_eq!(entries[0].args_summary, "cargo test");
        assert_eq!(entries[0].result_summary, "exit:0");
        assert_eq!(entries[1].tool, "fs_ops");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_empty_session() {
        let dir = temp_ostk_dir("empty_session");
        let recovery = Recovery::new(&dir);

        let summary = recovery.generate_recovery("agent-1").unwrap();
        assert!(summary.is_none());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_generate_recovery_structural() {
        let dir = temp_ostk_dir("structural");
        let recovery = Recovery::new(&dir);

        // Log 5 diverse tool calls
        recovery
            .log_tool_call("agent-1", "fs_read", "src/main.rs", "200 lines")
            .unwrap();
        recovery
            .log_tool_call("agent-1", "fs_ops", "src/main.rs", "ok gen=5->6")
            .unwrap();
        recovery
            .log_tool_call("agent-1", "sh_run", "cargo test", "exit:0")
            .unwrap();
        recovery
            .log_tool_call("agent-1", "fs_read", "src/lib.rs", "150 lines")
            .unwrap();
        recovery
            .log_tool_call("agent-1", "sh_run", "cargo build", "exit:0")
            .unwrap();

        let summary = recovery.generate_recovery("agent-1").unwrap().unwrap();

        // Structural checks
        assert_eq!(summary.total_calls, 5);
        assert!(!summary.edits.is_empty());
        assert!(!summary.commands.is_empty());
        assert!(!summary.reads.is_empty());

        // Formatted output is structural, not narrative
        let fmt = &summary.formatted;
        assert!(fmt.contains("5 tool calls"));
        assert!(fmt.contains("Edited"));
        assert!(fmt.contains("src/main.rs"));
        assert!(fmt.contains("Ran"));
        assert!(fmt.contains("cargo"));
        assert!(fmt.contains("Read"));
        assert!(fmt.contains("files"));
        assert!(fmt.contains("ago"));

        // Should NOT contain reasoning/narrative words
        assert!(!fmt.contains("because"));
        assert!(!fmt.contains("I think"));
        assert!(!fmt.contains("should"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_clear_session() {
        let dir = temp_ostk_dir("clear");
        let recovery = Recovery::new(&dir);

        recovery
            .log_tool_call("agent-1", "sh_run", "echo hi", "exit:0")
            .unwrap();

        let entries = recovery.read_session("agent-1").unwrap();
        assert_eq!(entries.len(), 1);

        recovery.clear_session("agent-1").unwrap();

        let entries = recovery.read_session("agent-1").unwrap();
        assert!(entries.is_empty());

        let prev_path = dir.join("sessions/agent-1.prev.jsonl");
        assert!(prev_path.exists());

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_separate_sessions() {
        let dir = temp_ostk_dir("separate");
        let recovery = Recovery::new(&dir);

        recovery
            .log_tool_call("agent-1", "sh_run", "cmd1", "exit:0")
            .unwrap();
        recovery
            .log_tool_call("agent-2", "sh_run", "cmd2", "exit:0")
            .unwrap();

        let e1 = recovery.read_session("agent-1").unwrap();
        let e2 = recovery.read_session("agent-2").unwrap();
        assert_eq!(e1.len(), 1);
        assert_eq!(e2.len(), 1);
        assert_eq!(e1[0].args_summary, "cmd1");
        assert_eq!(e2[0].args_summary, "cmd2");

        let _ = fs::remove_dir_all(&dir);
    }

    // --- 779: session summary from audit entries ---
    #[test]
    fn test_session_summary_from_audit_entries() {
        let dir = temp_ostk_dir("summary_audit");
        let recovery = Recovery::new(&dir);
        let alias = "agent-audit";

        // Log a realistic audit sequence: read, edit, shell, edit same file again
        recovery
            .log_tool_call(alias, "file:read", "src/handler.rs", "200 ok")
            .unwrap();
        recovery
            .log_tool_call(alias, "file:edit", "src/handler.rs", "ok gen=1->2")
            .unwrap();
        recovery
            .log_tool_call(alias, "shell", "cargo check", "exit:0")
            .unwrap();
        recovery
            .log_tool_call(alias, "file:edit", "src/handler.rs", "ok gen=2->3")
            .unwrap();
        recovery
            .log_tool_call(alias, "file:read", "Cargo.toml", "50 lines")
            .unwrap();

        let summary = recovery.generate_recovery(alias).unwrap().unwrap();

        // Total calls
        assert_eq!(summary.total_calls, 5);

        // Edits: src/handler.rs edited twice → "2 edits"
        assert_eq!(summary.edits.len(), 1, "should consolidate edits to same file");
        assert_eq!(summary.edits[0].path, "src/handler.rs");
        assert!(
            summary.edits[0].gen_changes.contains("2 edits"),
            "multiple edits should say '2 edits', got: {}",
            summary.edits[0].gen_changes
        );

        // Commands
        assert_eq!(summary.commands.len(), 1);
        assert!(summary.commands[0].command.contains("cargo check"));
        assert_eq!(summary.commands[0].result, "exit:0");

        // Reads: 2 distinct files
        assert_eq!(summary.reads.len(), 2);
        assert!(summary.reads.contains(&"src/handler.rs".to_string()));
        assert!(summary.reads.contains(&"Cargo.toml".to_string()));

        // Formatted summary includes key info
        let fmt = &summary.formatted;
        assert!(fmt.contains("5 tool calls"), "missing total: {fmt}");
        assert!(fmt.contains("Edited"), "missing Edited section: {fmt}");
        assert!(fmt.contains("Ran"), "missing Ran section: {fmt}");
        assert!(fmt.contains("Read 2 files"), "missing Read count: {fmt}");
        assert!(fmt.contains("ago"), "missing age: {fmt}");

        let _ = fs::remove_dir_all(&dir);
    }

    // --- 779: clear then recover returns None ---
    #[test]
    fn test_clear_then_recover_returns_none() {
        let dir = temp_ostk_dir("clear_recover");
        let recovery = Recovery::new(&dir);
        let alias = "agent-clear";

        // Log some calls
        recovery
            .log_tool_call(alias, "shell", "ls", "exit:0")
            .unwrap();
        recovery
            .log_tool_call(alias, "file:read", "README.md", "ok")
            .unwrap();

        // Recovery should exist
        assert!(recovery.generate_recovery(alias).unwrap().is_some());

        // Clear the session
        recovery.clear_session(alias).unwrap();

        // Recovery should now be None
        assert!(
            recovery.generate_recovery(alias).unwrap().is_none(),
            "after clear_session, generate_recovery should return None"
        );

        let _ = fs::remove_dir_all(&dir);
    }

    /// Round-trip: log tool calls of every category, then generate a
    /// recovery summary and verify all categories appear in the output.
    #[test]
    fn test_log_tool_call_generate_recovery_round_trip() {
        let dir = temp_ostk_dir("log_gen_roundtrip");
        let recovery = Recovery::new(&dir);
        let alias = "agent-rt";

        // No session yet → None
        assert!(recovery.generate_recovery(alias).unwrap().is_none());

        // Log one of each category: edit, read, shell
        recovery
            .log_tool_call(alias, "file:edit", "src/lib.rs", "ok gen=3->4")
            .unwrap();
        recovery
            .log_tool_call(alias, "file:read", "src/main.rs", "200 lines")
            .unwrap();
        recovery
            .log_tool_call(alias, "shell", "cargo test --lib", "exit:0")
            .unwrap();

        // Generate recovery
        let summary = recovery.generate_recovery(alias).unwrap().unwrap();

        // Structural counts
        assert_eq!(summary.total_calls, 3);
        assert_eq!(summary.edits.len(), 1, "should have 1 edited file");
        assert_eq!(summary.reads.len(), 1, "should have 1 read file");
        assert_eq!(summary.commands.len(), 1, "should have 1 command");

        // Edit details
        assert_eq!(summary.edits[0].path, "src/lib.rs");
        assert!(
            summary.edits[0].gen_changes.contains("gen"),
            "edit gen_changes should contain 'gen': {}",
            summary.edits[0].gen_changes
        );

        // Command details
        assert!(
            summary.commands[0].command.contains("cargo test"),
            "command should contain 'cargo test': {}",
            summary.commands[0].command
        );
        assert_eq!(summary.commands[0].result, "exit:0");

        // Read details
        assert_eq!(summary.reads[0], "src/main.rs");

        // Formatted output includes all sections
        let fmt = &summary.formatted;
        assert!(fmt.contains("3 tool calls"), "missing total: {fmt}");
        assert!(fmt.contains("Edited"), "missing Edited: {fmt}");
        assert!(fmt.contains("src/lib.rs"), "missing edit path: {fmt}");
        assert!(fmt.contains("Ran"), "missing Ran: {fmt}");
        assert!(fmt.contains("cargo test"), "missing command: {fmt}");
        assert!(fmt.contains("Read 1 files"), "missing Read: {fmt}");
        assert!(fmt.contains("ago"), "missing age: {fmt}");

        let _ = fs::remove_dir_all(&dir);
    }
}
