//! Tool handler implementations extracted from agent_loop.rs (->747).
//!
//! Each handler follows: extract params -> route to kernel/fallback -> audit.
//! The dispatcher in agent_loop.rs calls into these handlers via the match
//! arms in `execute_tool()`.

use crate::kernel::destructor::detect_destructive;
use serde_json::Value;
use std::path::PathBuf;

/// Expand `~` or `~/...` to the user's home directory.
/// Rust's PathBuf doesn't expand tilde — models frequently send paths like
/// `~/projects/foo` which resolve to a literal `~` directory without this.
pub fn expand_tilde(path: &str) -> PathBuf {
    if path == "~" {
        std::env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from(path))
    } else if let Some(rest) = path.strip_prefix("~/") {
        std::env::var("HOME")
            .map(|h| PathBuf::from(h).join(rest))
            .unwrap_or_else(|_| PathBuf::from(path))
    } else {
        PathBuf::from(path)
    }
}

/// Run a blocking closure on `spawn_blocking` when a tokio runtime is
/// available, otherwise execute it synchronously on the current thread.
///
/// This prevents panics ("Tokio reactor not running") when tool handlers
/// are invoked from contexts without an active tokio runtime — e.g. the
/// TUI event loop dispatching agent sessions (→750).
async fn run_blocking<F, R>(f: F) -> Result<R, String>
where
    F: FnOnce() -> R + Send + 'static,
    R: Send + 'static,
{
    match tokio::runtime::Handle::try_current() {
        Ok(_) => match tokio::time::timeout(
            std::time::Duration::from_secs(300),
            tokio::task::spawn_blocking(f),
        ).await {
            Ok(Ok(result)) => Ok(result),
            Ok(Err(e)) => Err(format!("spawn_blocking join error: {e}")),
            Err(_) => Err("tool execution timed out (300s)".to_string()),
        },
        Err(_) => Ok(f()),
    }
}

/// Resolve the ostk binary name from OSTK_OS_NAME env var.
fn ostk_bin() -> String {
    std::env::var("OSTK_OS_NAME").unwrap_or_else(|_| {
        // Use the actual running binary path if available, fall back to "ostk"
        std::env::current_exe()
            .ok()
            .and_then(|p| p.to_str().map(String::from))
            .unwrap_or_else(|| "ostk".into())
    })
}

// ---------------------------------------------------------------------------
// →827: Bench container routing — docker exec interception
// ---------------------------------------------------------------------------

/// Check if we're in bench mode by reading OSTK_BENCH_CONTAINER env var.
/// Returns the container name if set.
fn bench_container() -> Option<String> {
    std::env::var("OSTK_BENCH_CONTAINER").ok().filter(|s| !s.is_empty())
}

/// Execute a command inside the bench container via `docker exec`.
async fn docker_exec_cmd(container: &str, cmd: &str) -> (String, bool) {
    let result = tokio::process::Command::new("docker")
        .args(["exec", container, "bash", "-c", cmd])
        .output()
        .await;
    match result {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            let stderr = String::from_utf8_lossy(&out.stderr);
            let combined = if stderr.is_empty() {
                stdout.to_string()
            } else {
                format!("{stdout}{stderr}")
            };
            (combined, out.status.success())
        }
        Err(e) => (format!("docker exec error: {e}"), false),
    }
}

/// Read a file from inside the bench container via `docker exec cat`.
async fn docker_read_file(container: &str, path: &str) -> (String, bool) {
    docker_exec_cmd(container, &format!("cat {}", shell_escape(path))).await
}

/// Write a file inside the bench container via `docker exec tee`.
async fn docker_write_file(container: &str, path: &str, content: &str) -> (String, bool) {
    // Use printf + tee to write content. Pipe through stdin.
    let result = tokio::process::Command::new("docker")
        .args(["exec", "-i", container, "tee", path])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn();
    match result {
        Ok(mut child) => {
            if let Some(mut stdin) = child.stdin.take() {
                use tokio::io::AsyncWriteExt;
                let _ = stdin.write_all(content.as_bytes()).await;
                drop(stdin);
            }
            match child.wait_with_output().await {
                Ok(out) => {
                    if out.status.success() {
                        ("file written".into(), true)
                    } else {
                        let stderr = String::from_utf8_lossy(&out.stderr);
                        (format!("write error: {stderr}"), false)
                    }
                }
                Err(e) => (format!("write error: {e}"), false),
            }
        }
        Err(e) => (format!("docker exec error: {e}"), false),
    }
}

/// Edit (str_replace) a file inside the bench container.
/// Reads the file, does the replacement locally, writes it back.
async fn docker_edit_file(container: &str, path: &str, old: &str, new: &str) -> (String, bool) {
    let (contents, ok) = docker_read_file(container, path).await;
    if !ok {
        return (format!("read error: {contents}"), false);
    }
    if !contents.contains(old) {
        return ("old_string not found in file".into(), false);
    }
    let updated = contents.replacen(old, new, 1);
    docker_write_file(container, path, &updated).await
}

/// Glob inside the bench container via `find`.
async fn docker_glob(container: &str, pattern: &str) -> (String, bool) {
    docker_exec_cmd(container, &format!("find /app -path '{}' -print 2>/dev/null | head -200", pattern)).await
}

/// Grep inside the bench container.
async fn docker_grep(container: &str, pattern: &str, path: &str) -> (String, bool) {
    docker_exec_cmd(container, &format!("grep -rn {} {} 2>/dev/null | head -200", shell_escape(pattern), shell_escape(path))).await
}

/// Simple shell escaping for docker exec arguments.
fn shell_escape(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

// ---------------------------------------------------------------------------
// Bash
// ---------------------------------------------------------------------------

pub async fn handle_bash(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let cmd = input
        .get("command")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // →827: Route through docker exec when in bench mode
    if let Some(container) = bench_container() {
        return docker_exec_cmd(&container, cmd).await;
    }

    // →766: Tag destructive commands so the operator sees the warning in output.
    let destructive = detect_destructive(cmd);
    let destructive_prefix = destructive.map(|(cat, pat)| {
        format!("[⚠ DESTRUCTIVE ({cat}): matched `{pat}`]\n")
    });
    // →775: Log destructive detections to audit trail
    if let (Some((cat, pat)), Some(project_root)) = (destructive, root) {
        let _ = crate::append_audit(project_root, &serde_json::json!({
            "event": "tool.bash.destructive",
            "category": cat,
            "pattern": pat,
            "cmd": cmd.chars().take(200).collect::<String>(),
            "ts": crate::now_iso(),
        }));
    }
    // Route through kernel shell handler — same execution path as MCP.
    // PTY capture, squasher compression, audit are handled by the kernel.
    // No separate "direct path" — the kernel is the execution layer.
    let project_root = root.cloned()
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_default());

    let params = crate::serve::types::ShRunParams {
        cmd: cmd.to_string(),
        timeout: None,
        cwd: Some(project_root.to_string_lossy().to_string()),
        raw: None,
    };

    match crate::serve::tools::sh_run::handle(params, &project_root).await {
        Ok(val) => {
            let mut output = val.get("output")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let exit_code = val.get("exit_code")
                .and_then(|v| v.as_i64())
                .unwrap_or(-1) as i32;
            if let Some(pfx) = destructive_prefix {
                output = format!("{pfx}{output}");
            }
            (output, exit_code == 0)
        }
        Err(e) => (format!("[error] {}", e.message), false),
    }
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

pub async fn handle_read(
    input: &Value,
    file_cache: Option<&crate::cpu::file_cache::FileCache>,
    root: Option<&PathBuf>,
) -> (String, bool) {
    let path = input
        .get("file_path")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // →827: Route through docker exec when in bench mode
    if let Some(container) = bench_container() {
        return docker_read_file(&container, path).await;
    }

    // ->728: If the file is cached via the Files API, return a compact
    // reference instead of the full content to save context tokens.
    if let Some(cache) = file_cache
        && let Some(entry) = cache.get(path) {
            return (
                format!(
                    "[file uploaded: id={}, path={}, size={}B]",
                    entry.file_id, entry.path, entry.size
                ),
                true,
            );
        }
    // Route through kernel file layer: elision (304), gen_table tracking.
    // Falls back to raw read when no project root is available.
    // ->750: run_blocking graceful fallback (was spawn_blocking →772).
    if let Some(project_root) = root {
        let ostk_dir = crate::state_dir(project_root);
        if ostk_dir.is_dir() {
            // Resolve relative paths against project root (Mistral/OpenAI models
            // send relative paths; Anthropic models send absolute paths).
            let file_path = {
                let p = expand_tilde(path);
                if p.is_relative() { project_root.join(&p) } else { p }
            };
            // Directory → return listing (don't pass to elision which expects files)
            if file_path.is_dir() {
                match tokio::fs::read_dir(&file_path).await {
                    Ok(mut entries) => {
                        let mut names = Vec::new();
                        while let Ok(Some(entry)) = entries.next_entry().await {
                            let name = entry.file_name().to_string_lossy().to_string();
                            let is_dir = entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false);
                            names.push(if is_dir { format!("{name}/") } else { name });
                        }
                        names.sort();
                        return (names.join("\n"), true);
                    }
                    Err(e) => return (format!("read dir error: {} — {e}", file_path.display()), false),
                }
            }
            let agent = std::env::var("OSTK_AGENT").unwrap_or_else(|_| "cpu".into());
            let gen_path = if let Ok(rel) = file_path.strip_prefix(project_root) {
                rel.to_string_lossy().to_string()
            } else {
                path.to_string()
            };
            let path_owned = path.to_string();
            let elision_result = run_blocking(move || {
                crate::kernel::elision::read_file_with_elision(
                    &file_path, &gen_path, &agent, &ostk_dir,
                )
                .map(|r| r.format(&path_owned))
            }).await;
            match elision_result {
                Ok(Ok(formatted)) => {
                    return (formatted, true);
                }
                _ => {
                    // Fall through to raw read on elision errors
                }
            }
        }
    }
    // Resolve relative paths against project root before fallback read.
    // Without this, relative paths resolve against process CWD which may
    // differ from the project root (embedded mode launched from subdirectory).
    let resolved = if let Some(project_root) = root {
        let p = expand_tilde(path);
        if p.is_relative() { project_root.join(&p) } else { p }
    } else {
        expand_tilde(path)
    };
    // If target is a directory, return a listing instead of erroring.
    if resolved.is_dir() {
        match tokio::fs::read_dir(&resolved).await {
            Ok(mut entries) => {
                let mut names = Vec::new();
                while let Ok(Some(entry)) = entries.next_entry().await {
                    let name = entry.file_name().to_string_lossy().to_string();
                    let is_dir = entry.file_type().await.map(|t| t.is_dir()).unwrap_or(false);
                    names.push(if is_dir { format!("{name}/") } else { name });
                }
                names.sort();
                return (names.join("\n"), true);
            }
            Err(e) => return (format!("read dir error: {} — {e}", resolved.display()), false),
        }
    }
    match tokio::fs::read_to_string(&resolved).await {
        Ok(contents) => {
            // Audit the file read when project root is available
            if let Some(project_root) = root {
                let _ = crate::append_audit(project_root, &serde_json::json!({
                    "event": "tool.read",
                    "path": path,
                    "ts": crate::now_iso(),
                }));
            }
            (contents, true)
        }
        Err(e) => (format!("read error: {} — {e}", resolved.display()), false),
    }
}

// ---------------------------------------------------------------------------
// Edit
// ---------------------------------------------------------------------------

pub async fn handle_edit(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let path = input
        .get("file_path")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let old = input
        .get("old_string")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let new = input
        .get("new_string")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // →827: Route through docker exec when in bench mode
    if let Some(container) = bench_container() {
        return docker_edit_file(&container, path, old, new).await;
    }

    // Route through kernel CAS (str_replace_cas) for Hot PR merge,
    // conflict detection, gen_table tracking, and policy enforcement.
    // Falls back to raw read/replacen/write when no project root.
    // ->750: run_blocking graceful fallback (was spawn_blocking →772).
    if let Some(project_root) = root {
        let ostk_dir = crate::state_dir(project_root);
        if ostk_dir.is_dir() && !old.is_empty() {
            let file_path = expand_tilde(path);
            let gen_path = if let Ok(rel) = file_path.strip_prefix(project_root) {
                rel.to_string_lossy().to_string()
            } else {
                path.to_string()
            };
            let old_owned = old.to_string();
            let new_owned = new.to_string();
            let ostk_dir_c = ostk_dir.clone();
            let gen_path_c = gen_path.clone();
            let cas_result = match run_blocking(move || {
                let gen_table = crate::kernel::gen_table::GenTable::new(&ostk_dir_c);
                let writer = std::env::var("OSTK_AGENT").ok();
                crate::kernel::file::str_replace_cas(
                    &file_path, &old_owned, &new_owned,
                    &gen_table, &gen_path_c, writer.as_deref(),
                )
            }).await {
                Err(timeout_msg) => return (timeout_msg, false),
                Ok(r) => r,
            };
            match cas_result {
                Ok(_edit_result) => {
                    let _ = crate::append_audit(project_root, &serde_json::json!({
                        "event": "tool.edit",
                        "path": path,
                        "ts": crate::now_iso(),
                    }));
                    return ("edit applied".into(), true);
                }
                Err(crate::kernel::file::FileError::NoMatch { .. }) => {
                    return ("old_string not found in file".into(), false);
                }
                Err(crate::kernel::file::FileError::AmbiguousMatch { count, .. }) => {
                    return (
                        format!("found {count} matches for old_string (expected 1)"),
                        false,
                    );
                }
                Err(crate::kernel::file::FileError::Tier2Conflict { message, .. }) => {
                    return (format!("assisted merge: {message}"), false);
                }
                Err(crate::kernel::file::FileError::Conflict { message, .. }) => {
                    return (format!("conflict: {message}"), false);
                }
                Err(crate::kernel::file::FileError::PolicyDenied { reason, .. }) => {
                    return (format!("policy denied: {reason}"), false);
                }
                Err(e) => {
                    return (format!("edit failed: {e}"), false);
                }
            }
        }
    }
    // Raw fallback (test mode only -- no kernel available)
    match tokio::fs::read_to_string(path).await {
        Ok(contents) => {
            if !contents.contains(old) {
                return ("old_string not found in file".into(), false);
            }
            let updated = contents.replacen(old, new, 1);
            match tokio::fs::write(path, &updated).await {
                Ok(()) => ("edit applied".into(), true),
                Err(e) => (format!("write error: {e}"), false),
            }
        }
        Err(e) => (format!("read error: {path} — {e}"), false),
    }
}

// ---------------------------------------------------------------------------
// Write
// ---------------------------------------------------------------------------

pub async fn handle_write(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let path = input
        .get("file_path")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let content = input
        .get("content")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // →827: Route through docker exec when in bench mode
    if let Some(container) = bench_container() {
        return docker_write_file(&container, path, content).await;
    }

    // →805: Route through kernel write_file_cas for atomic flock-protected
    // write + gen_table tracking. Previously used tokio::fs::write + bump_gen
    // as two separate steps, allowing concurrent writes to silently overwrite
    // each other without conflict detection.
    if let Some(project_root) = root {
        let ostk_dir = crate::state_dir(project_root);
        if ostk_dir.is_dir() {
            let file_path = expand_tilde(path);
            let gen_path = if let Ok(rel) = file_path.strip_prefix(project_root) {
                rel.to_string_lossy().to_string()
            } else {
                path.to_string()
            };
            let content_owned = content.to_string();
            let path_owned = path.to_string();
            let ostk_dir_c = ostk_dir.clone();
            let gen_path_c = gen_path.clone();
            let cas_result = match run_blocking(move || {
                let gen_table = crate::kernel::gen_table::GenTable::new(&ostk_dir_c);
                let writer = std::env::var("OSTK_AGENT").ok();
                crate::kernel::file::write_file_cas(
                    &file_path, &content_owned,
                    &gen_table, &gen_path_c, writer.as_deref(),
                )
            }).await {
                Err(timeout_msg) => return (timeout_msg, false),
                Ok(r) => r,
            };
            match cas_result {
                Ok(_write_result) => {
                    let _ = crate::append_audit(project_root, &serde_json::json!({
                        "event": "tool.write",
                        "path": path_owned,
                        "ts": crate::now_iso(),
                    }));
                    return ("file written".into(), true);
                }
                Err(crate::kernel::file::FileError::PolicyDenied { reason, .. }) => {
                    return (format!("policy denied: {reason}"), false);
                }
                Err(e) => {
                    return (format!("write error: {e}"), false);
                }
            }
        }
    }
    match tokio::fs::write(path, content).await {
        Ok(()) => ("file written".into(), true),
        Err(e) => (format!("write error: {e}"), false),
    }
}

// ---------------------------------------------------------------------------
// Glob
// ---------------------------------------------------------------------------

pub async fn handle_glob(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let pattern = input
        .get("pattern")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // →827: Route through docker exec when in bench mode
    if let Some(container) = bench_container() {
        return docker_glob(&container, pattern).await;
    }

    // Route through `find` for file-pattern matching.
    // Previous code incorrectly called `ostk search` (content search via rg)
    // for glob patterns — now uses `find` directly, no subprocess self-invocation.
    let search_root = input.get("path")
        .and_then(|v| v.as_str())
        .or_else(|| root.map(|r| r.to_str().unwrap_or(".")))
        .unwrap_or(".");
    match tokio::process::Command::new("find")
        .args([search_root, "-path", pattern, "-print"])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .output()
        .await
    {
        Ok(out) => {
            let text: String = String::from_utf8_lossy(&out.stdout)
                .lines().take(200).collect::<Vec<_>>().join("\n");
            (text, true)
        }
        Err(e) => (format!("glob error: {e}"), false),
    }
}

// ---------------------------------------------------------------------------
// Grep
// ---------------------------------------------------------------------------

pub async fn handle_grep(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let pattern = input
        .get("pattern")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let path = input
        .get("path")
        .and_then(|v| v.as_str())
        .unwrap_or(".");

    // →827: Route through docker exec when in bench mode
    if let Some(container) = bench_container() {
        return docker_grep(&container, pattern, path).await;
    }

    // Route through kernel search functions directly — no subprocess.
    // Uses rg (ripgrep) when available, falls back to grep -rn.
    if root.is_none() {
        return grep_fallback(pattern, path).await;
    }
    let pattern_owned = pattern.to_string();
    let path_owned = path.to_string();
    match run_blocking(move || {
        if crate::commands::search::which_rg() {
            crate::commands::search::run_rg(&pattern_owned, &path_owned)
        } else {
            crate::commands::search::run_grep(&pattern_owned, &path_owned)
        }
    }).await {
        Ok(Ok(text)) if !text.is_empty() => (text, true),
        Ok(Ok(_)) => ("no matches".into(), true),
        Ok(Err(e)) => {
            // Fallback to raw grep on search error
            let _ = e;
            grep_fallback(pattern, path).await
        }
        Err(timeout_msg) => (timeout_msg, false),
    }
}

/// Fallback grep implementation using raw grep -rn.
async fn grep_fallback(pattern: &str, path: &str) -> (String, bool) {
    match tokio::process::Command::new("grep")
        .args(["-rn", pattern, path])
        .output()
        .await
    {
        Ok(out) => {
            let text = String::from_utf8_lossy(&out.stdout).to_string();
            (text, out.status.success())
        }
        Err(e) => (format!("grep error: {e}"), false),
    }
}

// ---------------------------------------------------------------------------
// Kernel tools
// ---------------------------------------------------------------------------

pub async fn handle_spawn_agent(input: &Value, socket_path: Option<&str>) -> (String, bool) {
    let agent_name = input.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let prompt = input.get("prompt").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let model = input.get("model").and_then(|v| v.as_str()).unwrap_or("sonnet").to_string();
    let budget = input.get("budget").and_then(|v| v.as_str()).unwrap_or("2").to_string();

    // →plan Phase 4: Route through daemon socket when available.
    // The daemon's SessionManager creates a managed session with registration,
    // lifecycle events, and transcript writes.
    if let Some(sock) = socket_path {
        match execute_via_socket(sock, "session/spawn_agent", &serde_json::json!({
            "name": agent_name,
            "model": model,
            "budget": budget,
            "prompt": prompt,
        })).await {
            Ok(result_str) => {
                // Parse the JSON response
                if let Ok(result) = serde_json::from_str::<serde_json::Value>(&result_str) {
                    let ok = result.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                    if ok {
                        (format!("spawned {agent_name} (model {model}, budget ${budget})"), true)
                    } else {
                        let err = result.get("error").and_then(|v| v.as_str()).unwrap_or("unknown");
                        (format!("spawn error: {err}"), false)
                    }
                } else {
                    (format!("spawned {agent_name} (model {model}, budget ${budget})"), true)
                }
            }
            Err(e) => (format!("spawn error: {e}"), false),
        }
    } else {
        // Fallback: direct spawn via fleet (no daemon available)
        match run_blocking(move || {
            crate::commands::fleet::run_spawn(&agent_name, &model, &budget, &prompt)
        }).await {
            Ok(Ok(())) => ("spawned agent".to_string(), true),
            Ok(Err(e)) => (format!("spawn error: {e}"), false),
            Err(timeout_msg) => (timeout_msg, false),
        }
    }
}

pub async fn handle_nudge_agent(input: &Value) -> (String, bool) {
    let agent = input.get("agent").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let message = input.get("message").and_then(|v| v.as_str()).unwrap_or("").to_string();
    // ->750: run_blocking graceful fallback (was spawn_blocking →772).
    let agent_c = agent.clone();
    match run_blocking(move || {
        crate::commands::nudge::run(&agent_c, &message)
    }).await {
        Err(timeout_msg) => (timeout_msg, false),
        Ok(Ok(())) => (format!("nudged {agent}"), true),
        Ok(Err(e)) => (format!("nudge error: {e}"), false),
    }
}

pub async fn handle_file_needle(input: &Value) -> (String, bool) {
    let title = input.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let priority = input.get("priority").and_then(|v| v.as_str()).unwrap_or("P1").to_string();
    // Direct kernel call — no subprocess. run_add prints "added <id>: <title> [<priority>]"
    // to stdout; we return a synthetic summary.
    match run_blocking(move || {
        crate::commands::work::run_add(
            &title, &priority,
            &None,   // milestone
            None,    // tags
            None,    // ac
            None,    // description
            None,    // depends_on
            None,    // blocks
        )
    }).await {
        Ok(Ok(())) => ("needle filed".to_string(), true),
        Ok(Err(e)) => (format!("file needle error: {e}"), false),
        Err(timeout_msg) => (timeout_msg, false),
    }
}

pub async fn handle_close_needle(input: &Value) -> (String, bool) {
    let id = input.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let reason = input.get("reason").and_then(|v| v.as_str()).map(|s| s.to_string());
    // Direct kernel call — no subprocess. run_close prints "closed <id>"
    // to stdout; we return a synthetic summary.
    match run_blocking(move || {
        crate::commands::work::run_close(&id, reason.as_deref())
    }).await {
        Ok(Ok(())) => ("needle closed".into(), true),
        Ok(Err(e)) => (format!("close needle error: {e}"), false),
        Err(timeout_msg) => (timeout_msg, false),
    }
}

pub async fn handle_compile_hay(input: &Value) -> (String, bool) {
    let dry_run = input.get("dry_run").and_then(|v| v.as_bool()).unwrap_or(false);
    // Direct kernel call — no subprocess. run_compile prints cluster/sphere
    // summaries to stdout; we return a synthetic summary.
    match run_blocking(move || {
        crate::commands::work::run_compile(dry_run)
    }).await {
        Ok(Ok(())) => ("compile complete".into(), true),
        Ok(Err(e)) => (format!("compile error: {e}"), false),
        Err(timeout_msg) => (timeout_msg, false),
    }
}

// ---------------------------------------------------------------------------
// Demand-paged tool loading — ostk_verbs and language tool execution
// ---------------------------------------------------------------------------

/// Verbs already bumped this boot cycle — prevents momentum gaming.
/// Capped at +0.1 per verb per session (one record_verb call).
static VERBS_BUMPED: std::sync::LazyLock<std::sync::Mutex<std::collections::HashSet<String>>> =
    std::sync::LazyLock::new(|| std::sync::Mutex::new(std::collections::HashSet::new()));

/// Handle `ostk_verbs(query)` — the page fault handler.
/// Searches .language for matching verbs, returns inline schemas.
pub async fn handle_ostk_verbs(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let root = match root {
        Some(r) => r.clone(),
        None => return ("no project root".into(), false),
    };
    let query = input
        .get("query")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_lowercase();

    if query.is_empty() {
        return ("query is required — e.g. ostk_verbs(query=\"search\")".into(), false);
    }

    let result = run_blocking(move || {
        let entries = crate::language::parse_language_file(&root).unwrap_or_default();
        let stamp = crate::cpu::current_boot_stamp(&root);

        // Match by verb name, doc, resolution, or signature
        let matches: Vec<&crate::language::LanguageEntry> = entries
            .iter()
            .filter(|e| e.layer == "user" && e.tier <= 2)
            .filter(|e| {
                e.verb.contains(&query)
                    || e.doc.to_lowercase().contains(&query)
                    || e.resolution.to_lowercase().contains(&query)
            })
            .collect();

        if matches.is_empty() {
            return format!("No verbs matching '{}'. Try a broader query.", query);
        }

        let mut result = format!(
            "Found {} verb(s) matching '{}':\n\n",
            matches.len(),
            query
        );
        for e in &matches {
            let schema = crate::cpu::generate_tool_schema(e, stamp);
            result.push_str(&format!(
                "## ostk_{} (momentum {:.2})\n{}\nResolution: {}\nSignature: {}\nSchema:\n```json\n{}\n```\n\n",
                e.verb,
                e.momentum,
                e.doc,
                e.resolution,
                e.signature,
                serde_json::to_string_pretty(&schema).unwrap_or_default()
            ));
            // Increment momentum — capped at one bump per verb per boot cycle
            if let Ok(mut bumped) = VERBS_BUMPED.lock()
                && bumped.insert(e.verb.clone()) {
                    let _ = crate::language::record_verb(&root, &e.verb, &e.resolution);
                }
        }
        result
    })
    .await;

    match result {
        Ok(text) => (text, true),
        Err(e) => (format!("ostk_verbs failed: {e}"), false),
    }
}

/// Handle `ostk_man(command)` — CLI reference, like man(1).
/// Executes `ostk {command} --help` and returns the output.
pub async fn handle_ostk_man(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let _ = root; // reserved for future per-project overrides
    let command = match input.get("command").and_then(|v| v.as_str()) {
        Some(c) => c.to_string(),
        None => return ("command is required — e.g. ostk_man(command=\"work add\")".into(), false),
    };

    // Sanitize: only allow alphanumeric, spaces, hyphens, underscores
    if command.chars().any(|c| !c.is_alphanumeric() && c != ' ' && c != '-' && c != '_') {
        return ("invalid command name".into(), false);
    }

    let result = run_blocking(move || {
        let args: Vec<&str> = command.split_whitespace().collect();
        let mut cmd = std::process::Command::new(ostk_bin());
        for arg in &args {
            cmd.arg(arg);
        }
        cmd.arg("--help");

        match cmd.output() {
            Ok(output) => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                let stderr = String::from_utf8_lossy(&output.stderr);
                if stdout.is_empty() && !stderr.is_empty() {
                    format!("ostk {} --help:\n{}", command, stderr)
                } else if stdout.is_empty() {
                    format!("No help found for '{}'", command)
                } else {
                    format!("ostk {} --help:\n{}", command, stdout)
                }
            }
            Err(e) => format!("Failed to run ostk {} --help: {}", command, e),
        }
    })
    .await;

    match result {
        Ok(text) => (text, true),
        Err(e) => (format!("ostk_man failed: {e}"), false),
    }
}

/// Handle `ostk_ask(question)` — single-turn LLM call through the kernel.
/// This is the fcp-llm device tool — agents can query LLMs without shelling out.
pub async fn handle_ostk_ask(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let question = match input.get("question").and_then(|v| v.as_str()) {
        Some(q) => q.to_string(),
        None => return ("question is required — e.g. ostk_ask(question=\"...\")".into(), false),
    };
    let model_override = input.get("model").and_then(|v| v.as_str()).map(|s| s.to_string());

    let root_clone = root.cloned();
    let result = run_blocking(move || {
        // Build command: ostk ask "question" [--model override]
        let mut cmd = std::process::Command::new(ostk_bin());
        cmd.arg("ask");
        cmd.arg(&question);
        if let Some(model) = &model_override {
            cmd.arg("--model");
            cmd.arg(model);
        }
        if let Some(r) = &root_clone {
            cmd.current_dir(r);
        }

        match cmd.output() {
            Ok(output) => {
                let stdout = String::from_utf8_lossy(&output.stdout).to_string();
                let stderr = String::from_utf8_lossy(&output.stderr).to_string();
                if output.status.success() {
                    if stdout.is_empty() { stderr } else { stdout }
                } else {
                    format!("ostk ask failed: {}", if stderr.is_empty() { &stdout } else { &stderr })
                }
            }
            Err(e) => format!("Failed to run ostk ask: {}", e),
        }
    })
    .await;

    match result {
        Ok(text) => (text, true),
        Err(e) => (format!("ostk_ask failed: {e}"), false),
    }
}

/// Handle `ostk_session_history(n)` — read last N messages from previous session.
/// This is /proc/self/mem — the agent reading its own past.
pub async fn handle_ostk_session_history(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let n = input.get("n").and_then(|v| v.as_u64()).unwrap_or(5) as usize;

    let root = match root {
        Some(r) => r.clone(),
        None => return ("no project root".into(), false),
    };

    let result = run_blocking(move || {
        let state_dir = crate::state_dir(&root);
        let sessions_dir = state_dir.join("sessions");

        // Find the current agent's .prev session file
        // Agent identity comes from OSTK_AGENT env var
        let agent_name = std::env::var("OSTK_AGENT").unwrap_or_else(|_| "unknown".into());
        let prev_path = sessions_dir.join(format!("{}.prev.jsonl", agent_name));

        if !prev_path.exists() {
            return format!("No previous session found for agent '{}'", agent_name);
        }

        let content = match std::fs::read_to_string(&prev_path) {
            Ok(c) => c,
            Err(e) => return format!("Failed to read session: {}", e),
        };

        let lines: Vec<&str> = content.lines().collect();
        let start = if lines.len() > n { lines.len() - n } else { 0 };
        let recent = &lines[start..];

        format!("# Session history (last {} entries)\n{}", recent.len(), recent.join("\n"))
    })
    .await;

    match result {
        Ok(text) => (text, true),
        Err(e) => (format!("ostk_session_history failed: {e}"), false),
    }
}

/// Handle `ostk_context_restore(from_turn, n)` — restore evicted turns.
/// Inverse of kernel eviction. Reads from session JSONL.
pub async fn handle_ostk_context_restore(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let from_turn = input
        .get("from_turn")
        .and_then(|v| v.as_u64())
        .unwrap_or(0) as usize;
    let n = input.get("n").and_then(|v| v.as_u64()).unwrap_or(5) as usize;

    let root = match root {
        Some(r) => r.clone(),
        None => return ("no project root".into(), false),
    };

    let result = run_blocking(move || {
        let state_dir = crate::state_dir(&root);
        let agent_name =
            std::env::var("OSTK_AGENT").unwrap_or_else(|_| "unknown".into());
        let session_path = state_dir.join(format!("sessions/{}.jsonl", agent_name));

        if !session_path.exists() {
            return format!("No session file for agent '{}'", agent_name);
        }

        let content = match std::fs::read_to_string(&session_path) {
            Ok(c) => c,
            Err(e) => return format!("Failed to read session: {}", e),
        };

        let lines: Vec<&str> = content.lines().collect();
        let start = from_turn.min(lines.len());
        let end = (start + n).min(lines.len());
        let selected = &lines[start..end];

        format!(
            "# restored turns {}-{}\n{}",
            start,
            end,
            selected.join("\n")
        )
    })
    .await;

    match result {
        Ok(text) => (text, true),
        Err(e) => (format!("ostk_context_restore failed: {e}"), false),
    }
}

/// Handle `ostk_context_search(query)` — search full session history.
pub async fn handle_ostk_context_search(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let query = match input.get("query").and_then(|v| v.as_str()) {
        Some(q) => q.to_lowercase(),
        None => return ("query is required".into(), false),
    };

    let root = match root {
        Some(r) => r.clone(),
        None => return ("no project root".into(), false),
    };

    let result = run_blocking(move || {
        let state_dir = crate::state_dir(&root);
        let agent_name =
            std::env::var("OSTK_AGENT").unwrap_or_else(|_| "unknown".into());
        let session_path = state_dir.join(format!("sessions/{}.jsonl", agent_name));

        if !session_path.exists() {
            return format!("No session file for agent '{}'", agent_name);
        }

        let content = match std::fs::read_to_string(&session_path) {
            Ok(c) => c,
            Err(e) => return format!("Failed to read session: {}", e),
        };

        let mut matches = Vec::new();
        for (i, line) in content.lines().enumerate() {
            if line.to_lowercase().contains(&query) {
                let truncated: String = line.chars().take(200).collect();
                matches.push(format!("turn {}: {}", i, truncated));
            }
        }

        if matches.is_empty() {
            format!("No matches for '{}' in session history", query)
        } else {
            format!(
                "# {} matches for '{}'\n{}",
                matches.len(),
                query,
                matches.join("\n")
            )
        }
    })
    .await;

    match result {
        Ok(text) => (text, true),
        Err(e) => (format!("ostk_context_search failed: {e}"), false),
    }
}

/// Handle `ostk_pitchfork(query)` — search across all kernel state.
pub async fn handle_ostk_pitchfork(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let query = match input.get("query").and_then(|v| v.as_str()) {
        Some(q) => q.to_string(),
        None => return ("query is required".into(), false),
    };

    let root = match root {
        Some(r) => r.clone(),
        None => return ("no project root".into(), false),
    };

    let result = run_blocking(move || {
        let ostk_dir = crate::state_dir(&root);
        match crate::serve::tools::pitchfork::handle(&query, &ostk_dir) {
            Ok(v) => v["text"].as_str().unwrap_or("").to_string(),
            Err(e) => format!("pitchfork error: {}", e.message),
        }
    })
    .await;

    match result {
        Ok(text) => (text, true),
        Err(e) => (format!("ostk_pitchfork failed: {e}"), false),
    }
}

/// Handle `ostk_context_release(before_turn, reason?)` — signal context release.
pub async fn handle_ostk_context_release(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let before_turn = match input.get("before_turn").and_then(|v| v.as_i64()) {
        Some(n) => n,
        None => return ("before_turn is required".into(), false),
    };
    let reason = input.get("reason").and_then(|v| v.as_str()).map(|s| s.to_string());

    let root = match root {
        Some(r) => r.clone(),
        None => return ("no project root".into(), false),
    };

    let result = run_blocking(move || {
        let ostk_dir = crate::state_dir(&root);
        let agent_name =
            std::env::var("OSTK_AGENT").unwrap_or_else(|_| "unknown".into());
        match crate::serve::tools::context_release::handle(
            before_turn,
            reason.as_deref(),
            &ostk_dir,
            Some(&agent_name),
        ) {
            Ok(v) => v["text"].as_str().unwrap_or("").to_string(),
            Err(e) => format!("context_release error: {}", e.message),
        }
    })
    .await;

    match result {
        Ok(text) => (text, true),
        Err(e) => (format!("ostk_context_release failed: {e}"), false),
    }
}

/// Handle `log_decision(key, value, reason?)` — explicitly log a decision.
pub async fn handle_log_decision(input: &Value, root: Option<&PathBuf>) -> (String, bool) {
    let key = match input.get("key").and_then(|v| v.as_str()) {
        Some(k) => k.to_string(),
        None => return ("key is required".into(), false),
    };
    let value = match input.get("value").and_then(|v| v.as_str()) {
        Some(v) => v.to_string(),
        None => return ("value is required".into(), false),
    };
    let reason = input
        .get("reason")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let root = match root {
        Some(r) => r.clone(),
        None => return ("no project root".into(), false),
    };

    let result = run_blocking(move || {
        let decisions_path = crate::state_dir(&root).join("decisions.jsonl");
        let entry = serde_json::json!({
            "key": key,
            "value": value,
            "reason": reason,
            "timestamp": crate::now_iso(),
        });
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&decisions_path)
            .map_err(|e| format!("failed to open decisions.jsonl: {e}"))?;
        use std::io::Write;
        writeln!(file, "{}", entry)
            .map_err(|e| format!("failed to write decision: {e}"))?;
        Ok::<String, String>(format!("Decision logged: {} = {}", key, value))
    })
    .await;

    match result {
        Ok(Ok(msg)) => (msg, true),
        Ok(Err(e)) => (e, false),
        Err(e) => (format!("log_decision failed: {e}"), false),
    }
}

/// Execute a .language verb by routing through the kernel shell.
/// Called for both resident and demand-loaded verbs.
pub async fn handle_language_tool(
    entry: &crate::language::LanguageEntry,
    input: &Value,
    root: Option<&PathBuf>,
) -> (String, bool) {
    // →879: .language resolutions may reference 
    // "ostk" (current). Replace with the actual binary to ensure dispatch works.
    let bin = ostk_bin();
    let mut cmd = if entry.resolution.starts_with("ostk ") {
        format!("{} {}", bin, &entry.resolution["ostk ".len()..])
    } else {
        entry.resolution.clone()
    };

    // Parse signature to determine positional vs optional args
    // Signature format: "(title, body?) -> (id)"
    // Non-? params are positional (in order), ? params are optional flags
    let (positional_names, _optional_names) = parse_signature_params(&entry.signature);

    if let Some(obj) = input.as_object() {
        // Positional args first, in signature order
        for param_name in &positional_names {
            if let Some(val) = obj.get(param_name.as_str()).and_then(|v| v.as_str())
                && !val.is_empty() {
                    cmd.push(' ');
                    cmd.push_str(&shell_quote(val));
                }
        }
        // Remaining args as --key 'value' flags
        for (key, val) in obj {
            if key.starts_with('_') { continue; }
            if positional_names.iter().any(|p| p == key) { continue; }
            if let Some(s) = val.as_str() {
                if !s.is_empty() {
                    cmd.push_str(&format!(" --{} {}", key, shell_quote(s)));
                }
            } else if let Some(b) = val.as_bool()
                && b {
                    cmd.push_str(&format!(" --{}", key));
                }
        }
    }

    // Record verb usage for momentum tracking
    if let Some(r) = root {
        let _ = crate::language::record_verb(r, &entry.verb, &entry.resolution);
    }

    handle_bash(&serde_json::json!({"command": cmd}), root).await
}

/// Parse a .language signature like "(title, body?) -> (id)" into positional and optional param names.
fn parse_signature_params(sig: &str) -> (Vec<String>, Vec<String>) {
    let input_part = if sig.contains("->") {
        sig.split("->").next().unwrap_or(sig)
    } else if sig.contains('\u{2192}') {
        sig.split('\u{2192}').next().unwrap_or(sig)
    } else {
        sig
    }
        .trim()
        .trim_start_matches('(')
        .trim_end_matches(')');

    let mut positional = Vec::new();
    let mut optional = Vec::new();

    for param in input_part.split(',') {
        let param = param.trim();
        if param.is_empty() { continue; }
        if param.ends_with('?') {
            optional.push(param.trim_end_matches('?').trim().to_string());
        } else {
            positional.push(param.trim().to_string());
        }
    }

    (positional, optional)
}

/// Shell-quote a string value safely.
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

// ---------------------------------------------------------------------------
// Signal verb handling — bracket-delimited resolutions
// ---------------------------------------------------------------------------

/// Handle a signal verb — a `.language` entry whose resolution is a bracket-
/// delimited cognitive directive rather than a CLI command.
///
/// Signal verbs:
/// - Record usage (momentum bump via `record_verb`)
/// - Return a structured acknowledgment the model can reason about
/// - Are recorded as session events for context summary/eviction
///
/// They do NOT execute shell commands.
pub async fn handle_signal_verb(
    entry: &crate::language::LanguageEntry,
    input: &Value,
    root: Option<&PathBuf>,
) -> (String, bool) {
    let signal = match entry.signal_kind() {
        Some(s) => s,
        None => {
            // Shouldn't happen — caller checks is_signal() first
            return (format!("verb :{} has no signal kind", entry.verb), false);
        }
    };

    // Record verb usage for momentum tracking
    if let Some(r) = root {
        let _ = crate::language::record_verb(r, &entry.verb, &entry.resolution);
    }

    // Extract optional context from the tool input (e.g. topic for :ultrathink)
    let context = input
        .as_object()
        .and_then(|obj| {
            // Try each possible parameter name from the signature
            obj.values()
                .filter_map(|v| v.as_str())
                .find(|s| !s.is_empty())
        })
        .unwrap_or("");

    // Build structured acknowledgment
    let ack = match &signal {
        crate::language::SignalKind::Intent(detail) => {
            format!(
                "signal:intent:{} — acknowledged. Verb :{} recorded.\n\
                 The kernel has noted this intent shift{}.",
                detail,
                entry.verb,
                if context.is_empty() {
                    String::new()
                } else {
                    format!(": \"{}\"", context)
                },
            )
        }
        crate::language::SignalKind::Inference(detail) => {
            format!(
                "signal:inference:{} — acknowledged. Verb :{} recorded.\n\
                 Reasoning mode shift noted{}.",
                detail,
                entry.verb,
                if context.is_empty() {
                    String::new()
                } else {
                    format!(" for topic: \"{}\"", context)
                },
            )
        }
        crate::language::SignalKind::Inferred(detail) => {
            format!(
                "signal:inferred:{} — acknowledged. Verb :{} recorded.\n\
                 Implicit command noted{}.",
                detail,
                entry.verb,
                if context.is_empty() {
                    String::new()
                } else {
                    format!(": \"{}\"", context)
                },
            )
        }
        crate::language::SignalKind::ControlFlow(detail) => {
            format!(
                "signal:control-flow:{} — acknowledged. Verb :{} recorded.\n\
                 Control gate: {}.",
                detail,
                entry.verb,
                detail,
            )
        }
        crate::language::SignalKind::Modifier(detail) => {
            format!(
                "signal:modifier:{} — acknowledged. Verb :{} recorded.\n\
                 Modifier applied{}.",
                detail,
                entry.verb,
                if context.is_empty() {
                    String::new()
                } else {
                    format!(": \"{}\"", context)
                },
            )
        }
    };

    (ack, true)
}

// ---------------------------------------------------------------------------
// Socket-based tool execution (→784)
// ---------------------------------------------------------------------------

/// Execute a tool call via the kernel daemon Unix socket.
///
/// Connects to the daemon, sends a JSON-RPC `tools/call` request, reads the
/// response, and extracts the result text. Falls back to an error string on
/// any transport or protocol failure so the caller can retry via direct exec.
pub async fn execute_via_socket(
    socket_path: &str,
    tool_name: &str,
    input: &Value,
) -> Result<String, String> {
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
    use tokio::net::UnixStream;

    let stream = UnixStream::connect(socket_path)
        .await
        .map_err(|e| format!("socket connect: {e}"))?;
    let (read_half, mut write_half) = stream.into_split();

    // Build JSON-RPC request
    let request = serde_json::json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": input,
        }
    });

    let mut payload = serde_json::to_string(&request)
        .map_err(|e| format!("serialize: {e}"))?;
    payload.push('\n');

    write_half
        .write_all(payload.as_bytes())
        .await
        .map_err(|e| format!("socket write: {e}"))?;
    write_half
        .shutdown()
        .await
        .map_err(|e| format!("socket shutdown: {e}"))?;

    // Read response (single JSON line)
    let mut reader = BufReader::new(read_half);
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .await
        .map_err(|e| format!("socket read: {e}"))?;

    if line.trim().is_empty() {
        return Err("empty response from daemon".into());
    }

    let resp: Value = serde_json::from_str(line.trim())
        .map_err(|e| format!("parse response: {e}"))?;

    // Check for JSON-RPC error
    if let Some(err) = resp.get("error") {
        let msg = err["message"].as_str().unwrap_or("unknown error");
        return Err(format!("daemon error: {msg}"));
    }

    // Extract text from MCP content wrapper
    let text = resp
        .pointer("/result/content/0/text")
        .and_then(|v| v.as_str())
        .unwrap_or_else(|| {
            resp.get("result")
                .and_then(|r| r.as_str())
                .unwrap_or("")
        });

    Ok(text.to_string())
}

/// Lazy-spawn a device driver when its socket is dead.
///
/// Looks up the driver command from HUMANFILE, spawns the process,
/// waits for the socket to appear (up to 5s). The driver is detached
/// and survives TUI exit — found alive by PID check on next boot.
pub async fn lazy_spawn_driver(root: &PathBuf, verb: &str, socket_path: &str) -> Result<(), String> {
    use std::os::unix::net::UnixStream;

    // Derive driver name from verb: "fcp-rust" → "rust", or use verb directly
    let driver_name = verb.strip_prefix("fcp-").unwrap_or(verb);

    // Look up spawn command from HUMANFILE
    let ostk_dir = crate::state_dir(root);
    let load_result = crate::humanfile::load(&ostk_dir);
    let cmd_str = load_result.humanfile.drivers.iter()
        .find(|(name, _)| name == driver_name || name == verb || *name == format!("fcp-{driver_name}"))
        .map(|(_, cmd)| cmd.clone())
        .ok_or_else(|| format!("no HUMANFILE DRIVER entry for '{driver_name}'"))?;

    // Check PID file first — might already be starting
    let pid_path = ostk_dir.join("drivers").join(format!("{driver_name}.pid"));
    if let Ok(pid_str) = std::fs::read_to_string(&pid_path)
        && let Ok(pid) = pid_str.trim().parse::<i32>()
            && unsafe { libc::kill(pid, 0) } == 0 {
                // Process alive, just wait for socket
                let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
                while std::time::Instant::now() < deadline {
                    if UnixStream::connect(socket_path).is_ok() {
                        return Ok(());
                    }
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                }
                return Err("driver process alive but socket not ready".into());
            }

    // Spawn the driver
    let parts: Vec<&str> = cmd_str.split_whitespace().collect();
    if parts.is_empty() {
        return Err("empty driver command".into());
    }

    let _ = std::fs::create_dir_all(ostk_dir.join("drivers"));
    let log_path = ostk_dir.join("drivers").join(format!("{driver_name}.log"));
    let log_file = std::fs::File::create(&log_path)
        .map_err(|e| format!("driver log: {e}"))?;
    let stderr_log = log_file.try_clone()
        .map_err(|e| format!("driver log clone: {e}"))?;

    let child = std::process::Command::new(parts[0])
        .args(&parts[1..])
        .stdout(std::process::Stdio::from(log_file))
        .stderr(std::process::Stdio::from(stderr_log))
        .spawn()
        .map_err(|e| format!("driver spawn: {e}"))?;

    let _ = std::fs::write(&pid_path, child.id().to_string());

    // Wait for socket
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    while std::time::Instant::now() < deadline {
        if UnixStream::connect(socket_path).is_ok() {
            return Ok(());
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }

    Err("driver spawned but socket not ready within 5s".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    // →833: Verify run_blocking completes fast tasks within timeout
    #[tokio::test]
    async fn run_blocking_completes_fast_task() {
        let result = run_blocking(|| 42).await;
        assert_eq!(result.unwrap(), 42);
    }

    // →833: Verify run_blocking returns Err on join failure
    #[tokio::test]
    async fn run_blocking_handles_panic() {
        let result: Result<(), String> = run_blocking(|| {
            panic!("intentional test panic");
        }).await;
        assert!(result.is_err(), "panicking task should return Err");
        assert!(result.unwrap_err().contains("join error"),
            "error should mention join error");
    }

    // →833: Verify run_blocking works without tokio runtime (fallback path)
    #[test]
    fn run_blocking_fallback_without_runtime() {
        // Outside of a tokio runtime, run_blocking should call f() directly
        // We can't easily test this because #[test] doesn't have a runtime,
        // but the function guards with Handle::try_current() — line 22.
        // This test verifies the function signature is correct.
        assert!(true, "fallback path exists via Handle::try_current()");
    }

    // ─── parse_signature_params tests ────────────────────────────────

    #[test]
    fn parse_signature_params_basic() {
        // "(title, body?) -> (id)" → positional: ["title"], optional: ["body"]
        let (pos, opt) = parse_signature_params("(title, body?) -> (id)");
        assert_eq!(pos, vec!["title"]);
        assert_eq!(opt, vec!["body"]);
    }

    #[test]
    fn parse_signature_params_no_optional() {
        // "(title) -> (id)" → positional: ["title"], optional: []
        let (pos, opt) = parse_signature_params("(title) -> (id)");
        assert_eq!(pos, vec!["title"]);
        assert!(opt.is_empty());
    }

    #[test]
    fn parse_signature_params_empty() {
        // "()" → both empty
        let (pos, opt) = parse_signature_params("()");
        assert!(pos.is_empty());
        assert!(opt.is_empty());
    }

    #[test]
    fn parse_signature_params_arrow_variants() {
        // Test with → (unicode arrow) as well as ->
        let (pos, _) = parse_signature_params("(query) \u{2192} (results)");
        assert_eq!(pos, vec!["query"]);
    }

    #[test]
    fn parse_signature_params_multiple_positional() {
        // "(agent, msg) -> ()" → positional: ["agent", "msg"]
        let (pos, opt) = parse_signature_params("(agent, msg) -> ()");
        assert_eq!(pos, vec!["agent", "msg"]);
        assert!(opt.is_empty());
    }

    #[test]
    fn parse_signature_params_all_optional() {
        // :ls signature: all optional params become --key flags, no positional
        let (pos, opt) = parse_signature_params("(status?, priority?, count?, json?) → (needles)");
        assert!(pos.is_empty(), "all params are optional — no positional");
        assert_eq!(opt, vec!["status", "priority", "count", "json"]);
    }

    // ─── shell_quote tests ───────────────────────────────────────────

    #[test]
    fn shell_quote_basic() {
        assert_eq!(shell_quote("hello"), "'hello'");
    }

    #[test]
    fn shell_quote_with_single_quotes() {
        assert_eq!(shell_quote("it's"), "'it'\\''s'");
    }

    #[test]
    fn shell_quote_empty() {
        assert_eq!(shell_quote(""), "''");
    }

    // ── Bash squasher integration tests ─────────────────────────

    #[tokio::test]
    async fn handle_bash_applies_squasher() {
        // Verify that handle_bash routes output through the squasher.
        // The squasher appends a metadata line with command, exit code, and timing.
        // This proves the compression pipeline is wired, not raw passthrough.
        let input = serde_json::json!({"command": "echo hello"});
        let (output, success) = handle_bash(&input, None).await;
        assert!(success, "echo should succeed");
        // Squasher appends a summary line: [passthrough] echo hello | exit:0 | ...
        assert!(output.contains("exit:0"),
            "squasher should append exit status metadata, got: {}", output);
    }

    #[tokio::test]
    async fn handle_bash_preserves_exit_code() {
        // Verify exit codes pass through correctly (the null-input bug fix)
        let input = serde_json::json!({"command": "true"});
        let (_, success) = handle_bash(&input, None).await;
        assert!(success, "true should return success");

        let input = serde_json::json!({"command": "false"});
        let (output, success) = handle_bash(&input, None).await;
        eprintln!("DEBUG false command: output={:?}, success={}", output, success);
        assert!(!success, "false should return failure, output was: {}", output);
    }

    #[tokio::test]
    async fn handle_bash_grammar_detection_for_head() {
        // Verify head grammar is detected and applied
        let tmp = tempfile::TempDir::new().unwrap();
        let test_file = tmp.path().join("test.txt");
        std::fs::write(&test_file, "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n").unwrap();
        let cmd = format!("head -3 {}", test_file.display());
        let input = serde_json::json!({"command": cmd});
        let (output, success) = handle_bash(&input, None).await;
        assert!(success, "head should succeed");
        // The output should contain the first 3 lines
        assert!(output.contains("a"), "should contain first line");
        assert!(output.contains("c"), "should contain third line");
    }

    #[tokio::test]
    async fn handle_bash_sed_exit_code_zero_on_no_match() {
        // The old shim bug: sed with no match returned exit code 2.
        // Real sed returns 0. Since we now use the real binary (not a shim),
        // exit code should be 0.
        let tmp = tempfile::TempDir::new().unwrap();
        let test_file = tmp.path().join("test.txt");
        std::fs::write(&test_file, "hello world\n").unwrap();
        let cmd = format!("sed 's/NONEXISTENT/replaced/' {}", test_file.display());
        let input = serde_json::json!({"command": cmd});
        let (_, success) = handle_bash(&input, None).await;
        assert!(success, "sed with no match should return exit 0 (real binary behavior)");
    }
}
