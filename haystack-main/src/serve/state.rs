//! Server state — shared mutable state for the MCP server.
//!
//! Holds the process table and session list. All tool handlers
//! receive an Arc<ServerState> for concurrent access.

use std::collections::HashMap;
use std::time::Instant;

use tokio::sync::RwLock;

use crate::kernel::pty::PtyCapture;

/// A tracked background process.
pub struct ProcessEntry {
    pub alias: String,
    pub pid: u32,
    pub state: ProcessState,
    pub pty: Option<PtyCapture>,
    pub output: Vec<u8>,
    pub started: Instant,
}

/// Process lifecycle states.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProcessState {
    Running,
    Completed,
    Failed,
    Killed,
}

impl ProcessState {
    pub fn as_str(&self) -> &'static str {
        match self {
            ProcessState::Running => "running",
            ProcessState::Completed => "completed",
            ProcessState::Failed => "failed",
            ProcessState::Killed => "killed",
        }
    }

    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            ProcessState::Completed | ProcessState::Failed | ProcessState::Killed
        )
    }
}

/// A named coordination lock with async notification.
///
/// Agents call `lock create <name>` to acquire exclusively.
/// If held, `lock watch <name>` blocks until released — instant wake via
/// tokio::sync::Notify instead of 500ms polling.
/// Filesystem lock files (`.ostk/locks/{name}.lock`) provide persistence
/// across daemon restarts; the in-memory Notify provides instant coordination.
pub struct LockEntry {
    pub name: String,
    pub holder: String,
    pub created: Instant,
    /// Notified when the lock is released — all waiters wake up.
    pub notify: std::sync::Arc<tokio::sync::Notify>,
}

/// Shared server state.
pub struct ServerState {
    pub processes: RwLock<HashMap<String, ProcessEntry>>,
    pub locks: RwLock<HashMap<String, LockEntry>>,
    /// Kernel-assigned agent alias (set on initialize).
    pub agent_alias: RwLock<Option<String>>,
    /// Path to .ostk directory for kernel primitives.
    pub ostk_dir: std::path::PathBuf,
    /// Recovery text from a previous session, injected on first tool response.
    pub recovery_text: RwLock<Option<String>>,
    /// →628: Current context utilization (0.0–1.0). Updated by run() before dispatch.
    /// Used by →620 dying notification to detect 90% threshold.
    pub context_pct: std::sync::atomic::AtomicU32, // stored as fixed-point (pct * 100)
    /// →842: Daemon-hosted SessionManager. Present when `ostk listen` creates it.
    /// Clients (TUI, REPL) use session/* wire protocol to control it.
    /// Model stays warm across client connects/disconnects.
    pub session_manager: Option<tokio::sync::Mutex<crate::cpu::session::SessionManager>>,
    /// fcp-llm: Tool call counter for throttling context block injection.
    /// Full context block injected every 10th call; digest on every call.
    pub tool_call_counter: std::sync::atomic::AtomicU64,
    /// →928: One-shot flag — true after context note has been injected once.
    /// Resets on session clear. Prevents spamming "clear_tool_uses is routine."
    pub context_note_sent: std::sync::atomic::AtomicBool,
    /// →957: Last user input text for pre-dispatch intent resolution.
    /// Updated on session/dispatch; read on every tools/call.
    pub last_user_input: RwLock<Option<String>>,
}

impl Default for ServerState {
    fn default() -> Self {
        Self::new()
    }
}

impl ServerState {
    pub fn new() -> Self {
        let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
        let ostk_dir = crate::state_dir(&cwd);
        Self {
            processes: RwLock::new(HashMap::new()),
            locks: RwLock::new(HashMap::new()),
            agent_alias: RwLock::new(None),
            ostk_dir,
            recovery_text: RwLock::new(None),
            context_pct: std::sync::atomic::AtomicU32::new(0),
            session_manager: None,
            tool_call_counter: std::sync::atomic::AtomicU64::new(0),
            context_note_sent: std::sync::atomic::AtomicBool::new(false),
            last_user_input: RwLock::new(None),
        }
    }

    /// →628: Set context utilization (0.0–100.0 percent).
    pub fn set_context_pct(&self, pct: f32) {
        self.context_pct.store(
            (pct * 100.0) as u32,
            std::sync::atomic::Ordering::Relaxed,
        );
    }

    /// →628: Get current context utilization (0.0–100.0 percent).
    pub fn get_context_pct(&self) -> f32 {
        self.context_pct.load(std::sync::atomic::Ordering::Relaxed) as f32 / 100.0
    }

    /// Create a new ServerState with a custom .ostk directory.
    /// Used by tests to isolate kernel state.
    pub fn with_ostk_dir(ostk_dir: std::path::PathBuf) -> Self {
        Self {
            processes: RwLock::new(HashMap::new()),
            locks: RwLock::new(HashMap::new()),
            agent_alias: RwLock::new(None),
            ostk_dir,
            recovery_text: RwLock::new(None),
            context_pct: std::sync::atomic::AtomicU32::new(0),
            session_manager: None,
            tool_call_counter: std::sync::atomic::AtomicU64::new(0),
            context_note_sent: std::sync::atomic::AtomicBool::new(false),
            last_user_input: RwLock::new(None),
        }
    }

    /// Reap dead processes — check all running entries, drain output,
    /// transition to terminal state, and drop the PTY fd.
    /// Also cleans up any terminal-state entries that still have a PTY
    /// (e.g., killed processes where the async drop was delayed).
    ///
    /// Returns the number of processes reaped.
    pub async fn reap_dead_processes(&self) -> usize {
        let mut processes = self.processes.write().await;
        let mut reaped = 0;
        let mut ptys_to_drop = Vec::new();

        for entry in processes.values_mut() {
            // Defensive: if a terminal-state entry still has a PTY, take it
            // for off-thread cleanup (e.g., kill raced with reaper).
            if entry.state.is_terminal() {
                if let Some(pty) = entry.pty.take() {
                    ptys_to_drop.push((entry.alias.clone(), pty));
                }
                continue;
            }

            if let Some(ref pty) = entry.pty
                && let Some(status) = pty.try_reap() {
                    // Drain any remaining output before dropping the PTY
                    if let Ok(remaining) = pty.drain() {
                        entry.output.extend_from_slice(&remaining);
                    }

                    entry.state = if status.success() {
                        ProcessState::Completed
                    } else {
                        ProcessState::Failed
                    };
                    entry.pty = None; // Drop the PTY fd
                    reaped += 1;
                    eprintln!(
                        "[ostk] reaped process '{}' pid:{} -> {}",
                        entry.alias,
                        entry.pid,
                        entry.state.as_str()
                    );
                }
        }

        // Drop releases the write lock first, then drop PTYs off the async thread.
        drop(processes);

        if !ptys_to_drop.is_empty() {
            let count = ptys_to_drop.len();
            drop(tokio::task::spawn_blocking(move || {
                for (alias, pty) in ptys_to_drop {
                    eprintln!("[ostk] reaper: dropping orphaned PTY for '{alias}'");
                    drop(pty);
                }
            }));
            reaped += count;
        }

        reaped
    }

    /// Purge stale terminal-state entries older than the given duration.
    /// Prevents unbounded growth of the process table.
    ///
    /// Returns the number of entries purged.
    pub async fn purge_stale_entries(&self, max_age: std::time::Duration) -> usize {
        let mut processes = self.processes.write().await;
        let before = processes.len();
        processes.retain(|_, entry| {
            !(entry.state.is_terminal() && entry.started.elapsed() > max_age)
        });
        before - processes.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    // ── ProcessState ──

    #[test]
    fn process_state_as_str() {
        assert_eq!(ProcessState::Running.as_str(), "running");
        assert_eq!(ProcessState::Completed.as_str(), "completed");
        assert_eq!(ProcessState::Failed.as_str(), "failed");
        assert_eq!(ProcessState::Killed.as_str(), "killed");
    }

    #[test]
    fn process_state_is_terminal() {
        assert!(!ProcessState::Running.is_terminal());
        assert!(ProcessState::Completed.is_terminal());
        assert!(ProcessState::Failed.is_terminal());
        assert!(ProcessState::Killed.is_terminal());
    }

    // ── LockEntry ──

    #[test]
    fn lock_entry_has_notify() {
        let entry = LockEntry {
            name: "test".into(),
            holder: "agent-1".into(),
            created: Instant::now(),
            notify: std::sync::Arc::new(tokio::sync::Notify::new()),
        };
        assert_eq!(entry.name, "test");
        assert_eq!(entry.holder, "agent-1");
    }

    // ── ServerState::new ──

    #[tokio::test]
    async fn server_state_new_empty() {
        let state = ServerState::new();
        let procs = state.processes.read().await;
        assert!(procs.is_empty());
        let locks = state.locks.read().await;
        assert!(locks.is_empty());
        let alias = state.agent_alias.read().await;
        assert!(alias.is_none());
    }

    #[test]
    fn server_state_default_matches_new() {
        // Default impl should produce same result as new()
        let state = ServerState::default();
        let dir_str = state.ostk_dir.to_string_lossy();
        assert!(dir_str.contains(".ostk") || dir_str.contains(".ostk"),
            "expected .ostk, got: {dir_str}");
    }

    #[test]
    fn server_state_with_ostk_dir() {
        let dir = std::path::PathBuf::from("/tmp/test-ostk");
        let state = ServerState::with_ostk_dir(dir.clone());
        assert_eq!(state.ostk_dir, dir);
    }

    // ── Insert and read processes ──

    #[tokio::test]
    async fn insert_and_read_process() {
        let state = ServerState::new();
        {
            let mut procs = state.processes.write().await;
            procs.insert(
                "test-proc".to_string(),
                ProcessEntry {
                    alias: "test-proc".to_string(),
                    pid: 12345,
                    state: ProcessState::Running,
                    pty: None,
                    output: b"hello".to_vec(),
                    started: Instant::now(),
                },
            );
        }
        let procs = state.processes.read().await;
        assert_eq!(procs.len(), 1);
        let entry = procs.get("test-proc").unwrap();
        assert_eq!(entry.pid, 12345);
        assert_eq!(entry.state, ProcessState::Running);
        assert_eq!(entry.output, b"hello");
    }

    // ── reap_dead_processes (no PTY — nothing to reap) ──

    #[tokio::test]
    async fn reap_dead_processes_no_pty() {
        let state = ServerState::new();
        {
            let mut procs = state.processes.write().await;
            procs.insert(
                "no-pty".to_string(),
                ProcessEntry {
                    alias: "no-pty".to_string(),
                    pid: 1,
                    state: ProcessState::Running,
                    pty: None,
                    output: Vec::new(),
                    started: Instant::now(),
                },
            );
        }
        // No PTY means nothing to reap
        let reaped = state.reap_dead_processes().await;
        assert_eq!(reaped, 0);
    }

    #[tokio::test]
    async fn reap_skips_terminal_entries() {
        let state = ServerState::new();
        {
            let mut procs = state.processes.write().await;
            procs.insert(
                "done".to_string(),
                ProcessEntry {
                    alias: "done".to_string(),
                    pid: 2,
                    state: ProcessState::Completed,
                    pty: None,
                    output: Vec::new(),
                    started: Instant::now(),
                },
            );
        }
        let reaped = state.reap_dead_processes().await;
        assert_eq!(reaped, 0);
    }

    // ── purge_stale_entries ──

    #[tokio::test]
    async fn purge_stale_entries_removes_old_terminal() {
        let state = ServerState::new();
        {
            let mut procs = state.processes.write().await;
            // Old terminal entry (started "long ago" — we use Instant::now but set a 0 max_age)
            procs.insert(
                "old".to_string(),
                ProcessEntry {
                    alias: "old".to_string(),
                    pid: 100,
                    state: ProcessState::Completed,
                    pty: None,
                    output: Vec::new(),
                    started: Instant::now() - Duration::from_secs(120),
                },
            );
            // Recent terminal entry
            procs.insert(
                "recent".to_string(),
                ProcessEntry {
                    alias: "recent".to_string(),
                    pid: 101,
                    state: ProcessState::Failed,
                    pty: None,
                    output: Vec::new(),
                    started: Instant::now(),
                },
            );
            // Running entry (should never be purged)
            procs.insert(
                "running".to_string(),
                ProcessEntry {
                    alias: "running".to_string(),
                    pid: 102,
                    state: ProcessState::Running,
                    pty: None,
                    output: Vec::new(),
                    started: Instant::now() - Duration::from_secs(120),
                },
            );
        }

        // Purge entries older than 60 seconds
        let purged = state.purge_stale_entries(Duration::from_secs(60)).await;
        assert_eq!(purged, 1); // only "old" should be purged

        let procs = state.processes.read().await;
        assert_eq!(procs.len(), 2);
        assert!(procs.contains_key("recent"));
        assert!(procs.contains_key("running"));
        assert!(!procs.contains_key("old"));
    }

    #[tokio::test]
    async fn purge_stale_entries_nothing_to_purge() {
        let state = ServerState::new();
        let purged = state.purge_stale_entries(Duration::from_secs(60)).await;
        assert_eq!(purged, 0);
    }

    // ── Lock operations ──

    #[tokio::test]
    async fn insert_and_read_lock() {
        let state = ServerState::new();
        {
            let mut locks = state.locks.write().await;
            locks.insert(
                "build".to_string(),
                LockEntry {
                    name: "build".to_string(),
                    holder: "agent-1".into(),
                    created: Instant::now(),
                    notify: std::sync::Arc::new(tokio::sync::Notify::new()),
                },
            );
        }
        let locks = state.locks.read().await;
        assert_eq!(locks.len(), 1);
        assert_eq!(locks.get("build").unwrap().holder, "agent-1");
    }

    #[tokio::test]
    async fn lock_release_notifies_waiters() {
        let state = ServerState::new();
        let notify = std::sync::Arc::new(tokio::sync::Notify::new());
        {
            let mut locks = state.locks.write().await;
            locks.insert(
                "deploy".to_string(),
                LockEntry {
                    name: "deploy".to_string(),
                    holder: "agent-1".into(),
                    created: Instant::now(),
                    notify: notify.clone(),
                },
            );
        }
        // Simulate release: remove entry + notify
        {
            let mut locks = state.locks.write().await;
            locks.remove("deploy");
        }
        notify.notify_waiters();
        // Verify lock is gone
        let locks = state.locks.read().await;
        assert!(locks.get("deploy").is_none(), "lock should be removed after release");
    }

    // ── recovery_text ──

    #[tokio::test]
    async fn recovery_text_starts_none() {
        let state = ServerState::new();
        let text = state.recovery_text.read().await;
        assert!(text.is_none());
    }

    #[tokio::test]
    async fn recovery_text_set_and_take() {
        let state = ServerState::new();
        {
            let mut text = state.recovery_text.write().await;
            *text = Some("recovered session data".to_string());
        }
        {
            let mut text = state.recovery_text.write().await;
            let taken = text.take();
            assert_eq!(taken.unwrap(), "recovered session data");
        }
        let text = state.recovery_text.read().await;
        assert!(text.is_none());
    }
}
