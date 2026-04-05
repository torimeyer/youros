//! `ostk listen` — start the kernel daemon on a Unix domain socket.
//!
//! Wires socket::run_socket_server() as a CLI command. Cleans stale sockets
//! on startup, writes kernel.pid, and blocks until interrupted.
//!
//! →842: Creates a SessionManager so the daemon keeps model context warm.
//! Clients (TUI, REPL) use session/* wire protocol to dispatch and drain events.

use crate::find_project_root;
use crate::serve::socket;

/// Entry point for `ostk listen`.
pub fn run() -> Result<(), String> {
    let root = find_project_root()?;
    let ostk_dir = crate::state_dir(&root);

    // Clean stale socket if the old kernel is dead
    let sock = socket::socket_path(&ostk_dir);
    if sock.exists() && !socket::kernel_alive(&ostk_dir) {
        eprintln!("[listen] removing stale socket: {}", sock.display());
        let _ = std::fs::remove_file(&sock);
        let _ = std::fs::remove_file(socket::pid_path(&ostk_dir));
    }

    // If a kernel is already alive, exit early
    if socket::kernel_alive(&ostk_dir) {
        eprintln!("[listen] kernel already running");
        return Ok(());
    }

    // Build a tokio runtime and run the socket server
    let rt = tokio::runtime::Runtime::new()
        .map_err(|e| format!("failed to create runtime: {e}"))?;

    // →842: Create SessionManager from Agentfile so the daemon hosts sessions.
    // Model stays warm across client connects/disconnects.
    // Resolution: ./Agentfile → .ostk/scheduler.af (legacy)
    let session_manager = {
        let af_path = crate::agentfile::resolve_default_agentfile(&root);
        match af_path.and_then(|p| std::fs::read_to_string(&p).ok()) {
            Some(content) => match crate::agentfile::parse(&content) {
                Ok(af) => {
                    let mut cpu = crate::cpu::config_from_agentfile(&af, &ostk_dir);
                    if cpu.max_turns.is_none() { cpu.max_turns = Some(25); }
                    let lc = cpu.into_loop_config(Some(root.clone()));
                    match crate::cpu::session::SessionManager::with_handle(
                        rt.handle().clone(), root.clone(), lc,
                    ) {
                        Ok(mgr) => {
                            eprintln!("[listen] session manager created (model stays warm)");
                            Some(mgr)
                        }
                        Err(e) => {
                            eprintln!("[listen] session manager failed: {e} (tool-only mode)");
                            None
                        }
                    }
                }
                Err(e) => {
                    eprintln!("[listen] Agentfile parse error: {e} (tool-only mode)");
                    None
                }
            }
            None => {
                eprintln!("[listen] no Agentfile found (tool-only mode)");
                None
            }
        }
    };

    rt.block_on(socket::run_socket_server(ostk_dir, session_manager))
        .map_err(|e| format!("socket server error: {e}"))
}
