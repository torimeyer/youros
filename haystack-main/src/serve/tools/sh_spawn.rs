//! spawn (alias: sh_spawn) — background process spawning tool.
//!
//! Starts a command in a PTY as a background process, registers it
//! in the server state, and optionally waits for a regex match.

use std::time::{Duration, Instant};

use crate::kernel::pty::{PtyCapture, PtyError};
use crate::serve::state::{ProcessEntry, ProcessState, ServerState};
use crate::serve::types::{
    ERR_ALIAS_IN_USE, ERR_INVALID_PARAMS, ERR_SHELL_ERROR, ShSpawnParams, ShSpawnResponse,
    ToolError,
};

/// Handle a spawn tool call.
pub async fn handle(
    params: ShSpawnParams,
    state: &ServerState,
) -> Result<serde_json::Value, ToolError> {
    let alias = params.alias.clone();
    let cmd = params.cmd.clone();
    let timeout = Duration::from_secs(params.timeout.unwrap_or(30));

    // Validate alias is not empty.
    if alias.is_empty() {
        return Err(ToolError::new(
            ERR_INVALID_PARAMS,
            "alias must not be empty",
        ));
    }

    // Check alias uniqueness (allow reuse of terminal-state entries).
    {
        let processes = state.processes.read().await;
        if let Some(entry) = processes.get(&alias)
            && !entry.state.is_terminal()
        {
            return Err(ToolError::new(
                ERR_ALIAS_IN_USE,
                format!("alias '{}' is already in use", alias),
            ));
        }
    }

    // Spawn in a blocking task (PTY allocation is blocking).
    let cmd_clone = cmd.clone();
    let pty = tokio::task::spawn_blocking(move || {
        let args = vec!["/bin/sh".to_string(), "-c".to_string(), cmd_clone];
        PtyCapture::spawn(&args)
    })
    .await
    .map_err(|e| ToolError::new(ERR_SHELL_ERROR, format!("spawn_blocking join error: {e}")))?
    .map_err(|e| ToolError::new(ERR_SHELL_ERROR, format!("PTY spawn failed: {e}")))?;

    let pid = pty.pid().as_raw() as u32;

    // Register in process table.
    {
        let mut processes = state.processes.write().await;
        processes.insert(
            alias.clone(),
            ProcessEntry {
                alias: alias.clone(),
                pid,
                state: ProcessState::Running,
                pty: Some(pty),
                output: Vec::new(),
                started: Instant::now(),
            },
        );
    }

    // If wait_for is specified, poll for a regex match.
    if let Some(ref wait_pattern) = params.wait_for {
        let regex = regex::Regex::new(&format!("(?i){}", wait_pattern)).map_err(|e| {
            ToolError::new(
                ERR_INVALID_PARAMS,
                format!("invalid wait_for regex '{}': {}", wait_pattern, e),
            )
        })?;

        let start = Instant::now();
        let poll_interval = Duration::from_millis(50);

        loop {
            if start.elapsed() >= timeout {
                // Timeout — return with wait_matched: false
                let output_tail = {
                    let processes = state.processes.read().await;
                    processes
                        .get(&alias)
                        .map(|e| String::from_utf8_lossy(&e.output).to_string())
                        .unwrap_or_default()
                };
                let tail_lines: Vec<&str> = output_tail.lines().collect();
                let tail_start = tail_lines.len().saturating_sub(20);
                let tail = tail_lines[tail_start..].join("\n");

                let response = ShSpawnResponse {
                    alias,
                    pid,
                    state: "running".to_string(),
                    wait_matched: false,
                    match_line: None,
                    output_tail: Some(tail),
                    reason: Some(format!(
                        "wait_for regex did not match within {}s timeout",
                        timeout.as_secs()
                    )),
                };
                return serde_json::to_value(&response)
                    .map_err(|e| ToolError::internal(format!("serialize error: {e}")));
            }

            tokio::time::sleep(poll_interval).await;

            // Read new output from the PTY.
            {
                let mut processes = state.processes.write().await;
                if let Some(entry) = processes.get_mut(&alias)
                    && let Some(ref pty) = entry.pty
                {
                    let mut buf = vec![0u8; 4096];
                    match pty.read_output(&mut buf) {
                        Ok(n) if n > 0 => {
                            entry.output.extend_from_slice(&buf[..n]);
                        }
                        Err(PtyError::ChildGone) => {
                            // Child exited during wait_for — drain and mark
                            if let Ok(remaining) = pty.drain() {
                                entry.output.extend_from_slice(&remaining);
                            }
                            entry.state = ProcessState::Failed;
                            entry.pty = None;
                        }
                        _ => {}
                    }
                }
            }

            // Check for match.
            let all_output = {
                let processes = state.processes.read().await;
                processes
                    .get(&alias)
                    .map(|e| String::from_utf8_lossy(&e.output).to_string())
                    .unwrap_or_default()
            };

            for line in all_output.lines() {
                if regex.is_match(line) {
                    let response = ShSpawnResponse {
                        alias,
                        pid,
                        state: "running".to_string(),
                        wait_matched: true,
                        match_line: Some(line.to_string()),
                        output_tail: None,
                        reason: None,
                    };
                    return serde_json::to_value(&response)
                        .map_err(|e| ToolError::internal(format!("serialize error: {e}")));
                }
            }
        }
    }

    // No wait_for — return immediately.
    let response = ShSpawnResponse {
        alias,
        pid,
        state: "running".to_string(),
        wait_matched: false,
        match_line: None,
        output_tail: None,
        reason: None,
    };

    serde_json::to_value(&response)
        .map_err(|e| ToolError::internal(format!("serialize error: {e}")))
}
