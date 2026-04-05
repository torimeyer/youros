//! interact (alias: sh_interact) — process interaction tool.
//!
//! Provides actions to interact with spawned processes:
//! read output, send input, send signals, kill, and query status.

use nix::sys::signal::{Signal, kill as nix_kill};
use nix::unistd::Pid;

use crate::kernel::pty::PtyError;
use crate::serve::state::{ProcessState, ServerState};
use crate::serve::types::{
    ERR_ALIAS_NOT_FOUND, ERR_INVALID_PARAMS, InteractAction, ShInteractParams, ShInteractResponse,
    ToolError,
};

/// Handle an interact tool call.
pub async fn handle(
    params: ShInteractParams,
    state: &ServerState,
) -> Result<serde_json::Value, ToolError> {
    let alias = params.alias.clone();

    // Check that the process exists.
    {
        let processes = state.processes.read().await;
        if !processes.contains_key(&alias) {
            return Err(ToolError::new(
                ERR_ALIAS_NOT_FOUND,
                format!("process alias not found: {}", alias),
            ));
        }
    }

    match params.action {
        InteractAction::ReadTail => {
            handle_read_tail(&alias, params.lines.unwrap_or(50), state).await
        }
        InteractAction::SendInput => {
            handle_send_input(&alias, params.input.as_deref(), state).await
        }
        InteractAction::SendSignal => {
            handle_send_signal(&alias, params.input.as_deref(), state).await
        }
        InteractAction::Kill => handle_kill(&alias, state).await,
        InteractAction::Status => handle_status(&alias, state).await,
    }
}

async fn handle_read_tail(
    alias: &str,
    max_lines: usize,
    state: &ServerState,
) -> Result<serde_json::Value, ToolError> {
    let mut processes = state.processes.write().await;
    let entry = processes.get_mut(alias).ok_or_else(|| {
        ToolError::new(ERR_ALIAS_NOT_FOUND, format!("process not found: {}", alias))
    })?;

    // Drain any new output from PTY.
    let mut child_gone = false;
    if let Some(ref pty) = entry.pty {
        let mut buf = vec![0u8; 4096];
        loop {
            match pty.read_output(&mut buf) {
                Ok(0) => break,
                Ok(n) => entry.output.extend_from_slice(&buf[..n]),
                Err(PtyError::ChildGone) => {
                    // Child exited — drain remaining buffered output
                    if let Ok(remaining) = pty.drain() {
                        entry.output.extend_from_slice(&remaining);
                    }
                    child_gone = true;
                    break;
                }
                Err(_) => break,
            }
        }

        // Also check via waitpid in case we didn't get EIO yet
        if !child_gone
            && let Some(status) = pty.try_reap() {
                if let Ok(remaining) = pty.drain() {
                    entry.output.extend_from_slice(&remaining);
                }
                child_gone = true;
                entry.state = if status.success() {
                    ProcessState::Completed
                } else {
                    ProcessState::Failed
                };
            }
    }

    // Drop the PTY if child is gone — release the fd
    if child_gone {
        if entry.state == ProcessState::Running {
            entry.state = ProcessState::Completed;
        }
        entry.pty = None;
    }

    let text = String::from_utf8_lossy(&entry.output).to_string();
    let all_lines: Vec<&str> = text.lines().collect();
    let start = all_lines.len().saturating_sub(max_lines);
    let tail_lines = &all_lines[start..];
    let output = tail_lines.join("\n");
    let lines_returned = tail_lines.len();

    let response = ShInteractResponse {
        alias: alias.to_string(),
        action: "read_tail".to_string(),
        output: Some(output),
        lines_returned: Some(lines_returned),
        bytes_written: None,
        state: entry.state.as_str().to_string(),
    };

    serde_json::to_value(&response)
        .map_err(|e| ToolError::internal(format!("serialize error: {e}")))
}

async fn handle_send_input(
    alias: &str,
    input: Option<&str>,
    state: &ServerState,
) -> Result<serde_json::Value, ToolError> {
    let input = input
        .ok_or_else(|| ToolError::new(ERR_INVALID_PARAMS, "input is required for send_input"))?;

    let processes = state.processes.read().await;
    let entry = processes.get(alias).ok_or_else(|| {
        ToolError::new(ERR_ALIAS_NOT_FOUND, format!("process not found: {}", alias))
    })?;

    let bytes_written = if let Some(ref pty) = entry.pty {
        pty.write_stdin(input.as_bytes())
            .map_err(|e| ToolError::internal(format!("write error: {e}")))?
    } else {
        return Err(ToolError::internal("process has no PTY"));
    };

    let response = ShInteractResponse {
        alias: alias.to_string(),
        action: "send_input".to_string(),
        output: None,
        lines_returned: None,
        bytes_written: Some(bytes_written),
        state: entry.state.as_str().to_string(),
    };

    serde_json::to_value(&response)
        .map_err(|e| ToolError::internal(format!("serialize error: {e}")))
}

async fn handle_send_signal(
    alias: &str,
    signal_name: Option<&str>,
    state: &ServerState,
) -> Result<serde_json::Value, ToolError> {
    let signal_name = signal_name.unwrap_or("SIGTERM");
    let signal = match signal_name.to_uppercase().as_str() {
        "SIGTERM" | "TERM" | "15" => Signal::SIGTERM,
        "SIGINT" | "INT" | "2" => Signal::SIGINT,
        "SIGHUP" | "HUP" | "1" => Signal::SIGHUP,
        "SIGUSR1" | "USR1" | "10" => Signal::SIGUSR1,
        "SIGUSR2" | "USR2" | "12" => Signal::SIGUSR2,
        _ => {
            return Err(ToolError::new(
                ERR_INVALID_PARAMS,
                format!("unknown signal: {}", signal_name),
            ));
        }
    };

    let processes = state.processes.read().await;
    let entry = processes.get(alias).ok_or_else(|| {
        ToolError::new(ERR_ALIAS_NOT_FOUND, format!("process not found: {}", alias))
    })?;

    let pid = Pid::from_raw(entry.pid as i32);
    nix_kill(pid, signal).map_err(|e| ToolError::internal(format!("signal error: {e}")))?;

    let response = serde_json::json!({
        "alias": alias,
        "action": "send_signal",
        "signal_sent": signal_name.to_uppercase(),
        "state": entry.state.as_str(),
    });

    Ok(response)
}

async fn handle_kill(alias: &str, state: &ServerState) -> Result<serde_json::Value, ToolError> {
    let taken_pty;
    {
        let mut processes = state.processes.write().await;
        let entry = processes.get_mut(alias).ok_or_else(|| {
            ToolError::new(ERR_ALIAS_NOT_FOUND, format!("process not found: {}", alias))
        })?;

        let pid = Pid::from_raw(entry.pid as i32);
        let _ = nix_kill(pid, Signal::SIGKILL);

        entry.state = ProcessState::Killed;
        // Take the PTY out — we'll drop it off the async thread
        taken_pty = entry.pty.take();
    }
    // Drop is released — no write lock held while we clean up the PTY.

    // Drop the PtyCapture on a blocking thread so the Drop impl's
    // waitpid + sleep never blocks the tokio runtime.
    if let Some(pty) = taken_pty {
        drop(tokio::task::spawn_blocking(move || {
            drop(pty);
        }));
    }

    let response = ShInteractResponse {
        alias: alias.to_string(),
        action: "kill".to_string(),
        output: None,
        lines_returned: None,
        bytes_written: None,
        state: "killed".to_string(),
    };

    serde_json::to_value(&response)
        .map_err(|e| ToolError::internal(format!("serialize error: {e}")))
}

async fn handle_status(alias: &str, state: &ServerState) -> Result<serde_json::Value, ToolError> {
    let processes = state.processes.read().await;
    let entry = processes.get(alias).ok_or_else(|| {
        ToolError::new(ERR_ALIAS_NOT_FOUND, format!("process not found: {}", alias))
    })?;

    let elapsed_ms = entry.started.elapsed().as_millis() as u64;

    let response = serde_json::json!({
        "alias": alias,
        "action": "status",
        "state": entry.state.as_str(),
        "pid": entry.pid,
        "elapsed_ms": elapsed_ms,
    });

    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;
    use crate::serve::state::ProcessEntry;
    use crate::serve::types::{InteractAction, ShInteractParams};

    async fn state_with_process(alias: &str, pstate: ProcessState, output: &[u8]) -> ServerState {
        let state = ServerState::new();
        {
            let mut procs = state.processes.write().await;
            procs.insert(
                alias.to_string(),
                ProcessEntry {
                    alias: alias.to_string(),
                    pid: 9999,
                    state: pstate,
                    pty: None,
                    output: output.to_vec(),
                    started: Instant::now(),
                },
            );
        }
        state
    }

    fn make_params(alias: &str, action: InteractAction) -> ShInteractParams {
        ShInteractParams {
            alias: alias.to_string(),
            action,
            input: None,
            lines: None,
            timeout: None,
        }
    }

    // ── alias not found ──

    #[tokio::test]
    async fn interact_alias_not_found() {
        let state = ServerState::new();
        let params = make_params("ghost", InteractAction::Status);
        let err = handle(params, &state).await.unwrap_err();
        assert_eq!(err.code, ERR_ALIAS_NOT_FOUND);
        assert!(err.message.contains("ghost"));
    }

    // ── status ──

    #[tokio::test]
    async fn interact_status_running() {
        let state = state_with_process("bg", ProcessState::Running, b"").await;
        let params = make_params("bg", InteractAction::Status);
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["alias"], "bg");
        assert_eq!(result["action"], "status");
        assert_eq!(result["state"], "running");
        assert_eq!(result["pid"], 9999);
        assert!(result["elapsed_ms"].as_u64().is_some());
    }

    #[tokio::test]
    async fn interact_status_completed() {
        let state = state_with_process("done", ProcessState::Completed, b"").await;
        let params = make_params("done", InteractAction::Status);
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["state"], "completed");
    }

    // ── read_tail (no PTY — uses buffered output) ──

    #[tokio::test]
    async fn interact_read_tail_buffered_output() {
        let output = b"line1\nline2\nline3\nline4\nline5";
        let state = state_with_process("reader", ProcessState::Completed, output).await;
        let mut params = make_params("reader", InteractAction::ReadTail);
        params.lines = Some(3);
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["alias"], "reader");
        assert_eq!(result["action"], "read_tail");
        assert_eq!(result["lines_returned"], 3);
        let out = result["output"].as_str().unwrap();
        assert!(out.contains("line3"));
        assert!(out.contains("line4"));
        assert!(out.contains("line5"));
        assert!(!out.contains("line1"));
    }

    #[tokio::test]
    async fn interact_read_tail_empty_output() {
        let state = state_with_process("empty", ProcessState::Running, b"").await;
        let params = make_params("empty", InteractAction::ReadTail);
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["lines_returned"], 0);
    }

    #[tokio::test]
    async fn interact_read_tail_default_lines() {
        // Default is 50 lines — test with fewer than 50
        let output = b"a\nb\nc";
        let state = state_with_process("few", ProcessState::Running, output).await;
        let params = make_params("few", InteractAction::ReadTail);
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["lines_returned"], 3);
    }

    // ── send_input (no PTY — should error) ──

    #[tokio::test]
    async fn interact_send_input_no_pty() {
        let state = state_with_process("no-pty", ProcessState::Running, b"").await;
        let mut params = make_params("no-pty", InteractAction::SendInput);
        params.input = Some("hello\n".to_string());
        let err = handle(params, &state).await.unwrap_err();
        assert!(err.message.contains("no PTY"));
    }

    #[tokio::test]
    async fn interact_send_input_missing_input() {
        let state = state_with_process("x", ProcessState::Running, b"").await;
        let params = make_params("x", InteractAction::SendInput);
        let err = handle(params, &state).await.unwrap_err();
        assert_eq!(err.code, ERR_INVALID_PARAMS);
        assert!(err.message.contains("input is required"));
    }

    // ── kill (no real PID — nix_kill will fail but state transitions) ──

    #[tokio::test]
    async fn interact_kill_transitions_to_killed() {
        let state = state_with_process("killme", ProcessState::Running, b"").await;
        let params = make_params("killme", InteractAction::Kill);
        let result = handle(params, &state).await.unwrap();
        assert_eq!(result["alias"], "killme");
        assert_eq!(result["action"], "kill");
        assert_eq!(result["state"], "killed");

        // Verify state was mutated
        let procs = state.processes.read().await;
        assert_eq!(procs.get("killme").unwrap().state, ProcessState::Killed);
    }
}
