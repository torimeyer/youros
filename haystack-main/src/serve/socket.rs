//! Kernel socket — Unix domain socket transport for ostk IPC (→619).
//!
//! Allows agents and ostk CLI to connect to a running `ostk serve`
//! kernel without going through MCP stdio. Same JSON-RPC protocol, same
//! dispatch layer — different transport.
//!
//! Layout:
//!   .ostk/ostk.sock   — Unix domain socket (AF_UNIX)
//!   .ostk/kernel.pid      — PID of the listening kernel process
//!
//! Entry point: `run_socket_server(ostk_dir)` — call from `ostk listen`.
//! Liveness:    `kernel_alive(ostk_dir)` — call before connecting.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::{mpsc, RwLock};

use tokio::io::BufReader;
use tokio::net::{UnixListener, UnixStream};

use crate::serve::server::McpServer;
use crate::serve::state::ServerState;
use crate::serve::transport::StdioTransport;

// ── Broadcast Manager ────────────────────────────────────────────────────────

/// Manages active socket connections for real-time broadcasts.
pub struct BroadcastManager {
    /// Active connection senders.
    subscribers: RwLock<Vec<mpsc::UnboundedSender<serde_json::Value>>>,
}

impl Default for BroadcastManager {
    fn default() -> Self {
        Self::new()
    }
}

impl BroadcastManager {
    pub fn new() -> Self {
        Self {
            subscribers: RwLock::new(Vec::new()),
        }
    }

    /// Register a new subscriber. Returns a receiver for the connection.
    pub async fn subscribe(&self) -> mpsc::UnboundedReceiver<serde_json::Value> {
        let (tx, rx) = mpsc::unbounded_channel();
        self.subscribers.write().await.push(tx);
        rx
    }

    /// Broadcast a message to all subscribers.
    pub async fn broadcast(&self, msg: serde_json::Value) {
        let mut subs = self.subscribers.write().await;
        // Remove closed channels while broadcasting
        subs.retain(|tx| tx.send(msg.clone()).is_ok());
    }
}

/// Global broadcast manager. Initialized on first access via `OnceLock`
/// (replaces lazy_static — no external dependency, same thread-safety).
///
/// →852: same pattern as approval.rs. `BroadcastHandle` implements `Deref`
/// so existing `BROADCAST.broadcast(...)` call sites compile unchanged.
static BROADCAST_CELL: std::sync::OnceLock<Arc<BroadcastManager>> = std::sync::OnceLock::new();

/// A zero-size handle that derefs to `Arc<BroadcastManager>`, providing the
/// same `BROADCAST.method()` ergonomics as the old `lazy_static!` ref.
pub struct BroadcastHandle;

impl std::ops::Deref for BroadcastHandle {
    type Target = Arc<BroadcastManager>;
    fn deref(&self) -> &Self::Target {
        BROADCAST_CELL.get_or_init(|| Arc::new(BroadcastManager::new()))
    }
}

pub static BROADCAST: BroadcastHandle = BroadcastHandle;

/// Function accessor for the global broadcast manager.
/// Equivalent to `&*BROADCAST` — provided so both `BROADCAST.method()` and
/// `broadcast().method()` work across the codebase.
pub fn broadcast() -> &'static Arc<BroadcastManager> {
    BROADCAST_CELL.get_or_init(|| Arc::new(BroadcastManager::new()))
}

// ── Path helpers ─────────────────────────────────────────────────────────────

pub fn socket_path(ostk_dir: &Path) -> PathBuf {
    ostk_dir.join("ostk.sock")
}

pub fn pid_path(ostk_dir: &Path) -> PathBuf {
    ostk_dir.join("kernel.pid")
}


// ── Idle timeout ──────────────────────────────────────────────────────────────

/// Parse idle timeout from an optional string value.
/// Returns 1800 (default) if None or unparseable, raw value otherwise.
fn parse_idle_timeout_secs(val: Option<&str>) -> u64 {
    val.and_then(|v| v.parse::<u64>().ok()).unwrap_or(1800)
}

/// Read idle timeout in seconds from `OSTK_DAEMON_IDLE_SECS`.
/// Default: 1800 (30 min). Set to 0 to disable.
pub fn idle_timeout_secs() -> u64 {
    parse_idle_timeout_secs(std::env::var("OSTK_DAEMON_IDLE_SECS").ok().as_deref())
}

// ── Liveness check ───────────────────────────────────────────────────────────

/// Returns true iff a kernel daemon is alive and accepting connections.
pub fn kernel_alive(ostk_dir: &Path) -> bool {
    let sock = socket_path(ostk_dir);
    let pid_file = pid_path(ostk_dir);

    if !sock.exists() {
        return false;
    }

    let pid_str = match std::fs::read_to_string(&pid_file) {
        Ok(s) => s,
        Err(_) => return false,
    };

    let pid: i32 = match pid_str.trim().parse() {
        Ok(p) => p,
        Err(_) => return false,
    };

    unsafe { libc::kill(pid, 0) == 0 }
}

// ── Socket server ─────────────────────────────────────────────────────────────

/// Run the kernel socket server.
/// When `session_manager` is Some, the daemon hosts agent sessions (model stays warm).
pub async fn run_socket_server(
    ostk_dir: PathBuf,
    session_manager: Option<crate::cpu::session::SessionManager>,
) -> Result<(), Box<dyn std::error::Error>> {
    let sock_path = socket_path(&ostk_dir);
    let pid_file = pid_path(&ostk_dir);

    if sock_path.exists() {
        std::fs::remove_file(&sock_path)?;
    }

    std::fs::create_dir_all(&ostk_dir)?;

    let listener = UnixListener::bind(&sock_path)?;

    // →760: owner-only access on kernel socket
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&sock_path, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| format!("chmod socket: {e}"))?;
    }

    let pid = std::process::id();
    std::fs::write(&pid_file, pid.to_string())?;

    eprintln!(
        "[ostk] kernel socket listening: {} (pid {})",
        sock_path.display(),
        pid
    );

    let mut state = ServerState::new();
    if let Some(mgr) = session_manager {
        state.session_manager = Some(tokio::sync::Mutex::new(mgr));
    }
    let state = Arc::new(state);

    let sock_path_cleanup = sock_path.clone();
    let pid_cleanup = pid_file.clone();
    tokio::spawn(async move {
        if tokio::signal::ctrl_c().await.is_ok() {
            let _ = std::fs::remove_file(&sock_path_cleanup);
            let _ = std::fs::remove_file(&pid_cleanup);
            std::process::exit(0);
        }
    });

    let idle_secs = idle_timeout_secs();
    let check_interval = std::time::Duration::from_secs(60);
    let last_activity = Arc::new(std::sync::Mutex::new(Instant::now()));

    loop {
        let accept_result = if idle_secs > 0 {
            tokio::time::timeout(check_interval, listener.accept()).await.ok()
        } else {
            Some(listener.accept().await)
        };

        match accept_result {
            Some(Ok((stream, _addr))) => {
                *last_activity.lock().unwrap() = Instant::now();
                let conn_state = state.clone();
                let last_act = last_activity.clone();
                tokio::spawn(async move {
                    if let Err(e) = handle_connection(stream, conn_state).await {
                        eprintln!("[ostk] socket connection error: {e}");
                    }
                    // Record activity when connection closes
                    *last_act.lock().unwrap() = Instant::now();
                });
            }
            Some(Err(e)) => return Err(e.into()),
            None => {
                // Check interval elapsed — evaluate idle condition
                let elapsed = last_activity.lock().unwrap().elapsed();
                if elapsed >= std::time::Duration::from_secs(idle_secs) {
                    let any_busy = if let Some(mgr) = &state.session_manager {
                        let mgr = mgr.lock().await;
                        mgr.list_sessions().iter().any(|(_, _, busy)| *busy)
                    } else {
                        false
                    };

                    if any_busy {
                        // A session is active — reset the timer
                        *last_activity.lock().unwrap() = Instant::now();
                    } else {
                        let project_root = ostk_dir
                            .parent()
                            .unwrap_or_else(|| std::path::Path::new("."));
                        let _ = crate::append_audit(
                            project_root,
                            &serde_json::json!({
                                "event": "daemon.idle_shutdown",
                                "idle_secs": elapsed.as_secs(),
                                "pid": std::process::id(),
                            }),
                        );
                        eprintln!(
                            "[ostk] idle timeout after {}s — shutting down",
                            elapsed.as_secs()
                        );
                        let _ = std::fs::remove_file(&sock_path);
                        let _ = std::fs::remove_file(&pid_file);
                        break;
                    }
                }
            }
        }
    }

    Ok(())
}

async fn handle_connection(
    stream: UnixStream,
    state: Arc<ServerState>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let (read_half, write_half) = stream.into_split();

    let reader = BufReader::new(read_half);
    // →800: Route MCP responses through the real write_half so
    // execute_via_socket clients receive JSON-RPC replies.
    // Broadcast subscriptions are for long-lived TUI connections and are
    // handled separately; ephemeral tool-call connections don't need them.
    let mut transport = StdioTransport::with_io(reader, write_half);

    let server = McpServer::with_state_arc(state);
    server
        .run(&mut transport)
        .await
        .map_err(|e| format!("server error: {e}").into())
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn temp_dir() -> TempDir {
        tempfile::tempdir().unwrap()
    }

    #[test]
    fn kernel_alive_no_files() {
        let dir = temp_dir();
        assert!(!kernel_alive(dir.path()));
    }

    #[test]
    fn kernel_alive_sock_exists_no_pid() {
        let dir = temp_dir();
        std::fs::write(socket_path(dir.path()), "").unwrap();
        assert!(!kernel_alive(dir.path()));
    }

    #[test]
    fn kernel_alive_current_process() {
        let dir = temp_dir();
        std::fs::write(socket_path(dir.path()), "").unwrap();
        let pid = std::process::id();
        std::fs::write(pid_path(dir.path()), pid.to_string()).unwrap();
        assert!(kernel_alive(dir.path()));
    }

    #[tokio::test]
    async fn broadcast_to_multiple_subscribers() {
        let manager = BroadcastManager::new();
        let mut rx1 = manager.subscribe().await;
        let mut rx2 = manager.subscribe().await;

        let msg = serde_json::json!({"event": "test"});
        manager.broadcast(msg.clone()).await;

        assert_eq!(rx1.recv().await.unwrap(), msg);
        assert_eq!(rx2.recv().await.unwrap(), msg);
    }

    // ── Idle timeout ──

    #[test]
    fn test_idle_timeout_default() {
        assert_eq!(parse_idle_timeout_secs(None), 1800);
    }

    #[test]
    fn test_idle_timeout_env_override() {
        assert_eq!(parse_idle_timeout_secs(Some("60")), 60);
    }

    #[test]
    fn test_idle_timeout_disabled() {
        assert_eq!(parse_idle_timeout_secs(Some("0")), 0);
    }
}
