//! shell (alias: sh_run) — synchronous command execution tool.
//!
//! Executes a command in a PTY and returns structured output.
//! Uses ostk's kernel::pty for execution.
//! →879: CWD defaults to project root (kernel register), not process CWD.

use std::path::PathBuf;
use std::time::Instant;

use crate::kernel::pty;
use crate::serve::types::{ERR_SHELL_ERROR, LineCount, ShRunParams, ShRunResponse, ToolError};

/// Handle a shell tool call.
///
/// →879: `project_root` is the kernel-resolved project root (ostk_dir.parent()).
/// Commands run there by default. Params `cwd` overrides if set.
pub async fn handle(params: ShRunParams, project_root: &std::path::Path) -> Result<serde_json::Value, ToolError> {
    let cmd = params.cmd.clone();
    let _timeout_secs = params.timeout.unwrap_or(300);

    // →879: Resolve working directory — explicit override or project root
    let cwd: PathBuf = match params.cwd {
        Some(ref dir) => PathBuf::from(dir),
        None => project_root.to_path_buf(),
    };

    // Run command in a blocking task (PTY is sync).
    let result = tokio::task::spawn_blocking(move || {
        let command = vec!["/bin/sh".to_string(), "-c".to_string(), cmd];
        let start = Instant::now();
        let pty_result = pty::run_command_in(&command, Some(&cwd));
        let duration_ms = start.elapsed().as_millis() as u64;
        (pty_result, duration_ms)
    })
    .await
    .map_err(|e| ToolError::new(ERR_SHELL_ERROR, format!("spawn_blocking join error: {e}")))?;

    let (pty_result, duration_ms) = result;

    match pty_result {
        Ok((status, output_bytes)) => {
            let output = String::from_utf8_lossy(&output_bytes).to_string();
            let total_lines = output.lines().count() as u64;
            let exit_code = status.code.unwrap_or(-1);

            let raw = params.raw.unwrap_or(false);
            let output = if raw {
                output
            } else {
                let (compressed, _stats) = crate::squasher::compress_with_cmd(
                    &output,
                    Some(&params.cmd),
                    exit_code,
                    "",          // stderr — PTY interleaves into stdout
                    duration_ms,
                );
                compressed
            };
            let shown_lines = output.lines().count() as u64;

            // Canonical audit point for all shell execution (MCP + embedded).
            let _ = crate::append_audit(project_root, &serde_json::json!({
                "event": "tool.bash",
                "cmd": params.cmd.chars().take(200).collect::<String>(),
                "exit_code": exit_code,
                "success": exit_code == 0,
                "duration_ms": duration_ms,
                "ts": crate::now_iso(),
            }));

            let response = ShRunResponse {
                exit_code,
                duration_ms,
                output,
                lines: LineCount {
                    total: total_lines,
                    shown: shown_lines,
                },
            };

            serde_json::to_value(&response)
                .map_err(|e| ToolError::internal(format!("Failed to serialize: {e}")))
        }
        Err(e) => Err(ToolError::new(ERR_SHELL_ERROR, format!("PTY error: {e}"))),
    }
}

/// Handle help (alias: sh_help) — return usage information.
pub fn handle_sh_help() -> serde_json::Value {
    serde_json::json!({
        "text": "# ostk reference card\n\n## tools\n  shell — execute command (alias: sh_run)\n  spawn — background process (alias: sh_spawn)\n  interact — interact with process (alias: sh_interact)\n  session — session management (alias: sh_session)\n  lock — coordination locks (alias: sh_lock)\n  help — this card (alias: sh_help)"
    })
}
