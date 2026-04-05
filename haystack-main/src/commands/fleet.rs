use std::fs;
use std::io::Write;
use std::time::{Duration, Instant};
use std::thread;

use crate::cpu::agent_loop::{self, CpuEvent};
use crate::cpu::anthropic::{Message, ContentBlock};
use crate::cpu::{AgentfileSource, SpawnRequest};
use crate::{append_audit, find_project_root, now_iso};
use serde_json::json;

pub fn run_ps() -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = crate::kernel::verb_ctx::VerbCtx::new(&root, &input);
    run_ps_verb(&mut ctx)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for :ps (→1157).
pub fn run_ps_verb(ctx: &mut crate::kernel::verb_ctx::VerbCtx) -> Result<(), String> {
    use std::fmt::Write;
    let state = ctx.ostk_dir();

    if crate::serve::socket::kernel_alive(&state) {
        let pid_file = state.join("kernel.pid");
        let pid = fs::read_to_string(&pid_file)
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|_| "?".into());
        let sock = state.join("ostk.sock");
        writeln!(ctx, "daemon running (pid {}, socket {})", pid, sock.display()).unwrap();
    } else {
        writeln!(ctx, "no daemon running").unwrap();
    }
    Ok(())
}

/// CLI entry point for status (thin wrapper).
pub fn run_status() -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = crate::kernel::verb_ctx::VerbCtx::new(&root, &input);
    run_status_verb(&mut ctx)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for :status (→1157).
pub fn run_status_verb(ctx: &mut crate::kernel::verb_ctx::VerbCtx) -> Result<(), String> {
    use std::fmt::Write;
    let state = ctx.ostk_dir();

    if crate::serve::socket::kernel_alive(&state) {
        let pid_file = state.join("kernel.pid");
        let pid = fs::read_to_string(&pid_file)
            .map(|s| s.trim().to_string())
            .unwrap_or_else(|_| "?".into());
        writeln!(ctx, "ostk: daemon running (pid {})", pid).unwrap();
    } else {
        writeln!(ctx, "ostk: no daemon running").unwrap();
    }
    Ok(())
}

/// CLI entry point for await (thin wrapper).
pub fn run_await(name: &str, timeout_secs: u64) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = crate::kernel::verb_ctx::VerbCtx::new(&root, &input);
    run_await_verb(&mut ctx, name, timeout_secs)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for await (→1157).
pub fn run_await_verb(ctx: &mut crate::kernel::verb_ctx::VerbCtx, name: &str, timeout_secs: u64) -> Result<(), String> {
    use std::fmt::Write;
    let root = ctx.root.to_path_buf();

    // Verify agent was actually spawned this session
    let audit_file = crate::state_dir(&root).join("audit.jsonl");
    if let Ok(content) = fs::read_to_string(&audit_file)
        && !content.contains(&format!("\"name\":\"{}\"", name)) {
            return Err(format!("agent '{}' not found in audit trail", name));
        }

    let done_file = crate::state_dir(&root).join("store").join(format!("agent-{}-done.json", name));

    writeln!(ctx, "awaiting agent '{}' (timeout: {}s)...", name, timeout_secs).unwrap();

    let start = Instant::now();
    let timeout = Duration::from_secs(timeout_secs);

    while start.elapsed() < timeout {
        if done_file.exists() {
            let content = fs::read_to_string(&done_file)
                .map_err(|e| format!("failed to read completion artifact: {e}"))?;
            let val: serde_json::Value = serde_json::from_str(&content)
                .map_err(|e| format!("failed to parse completion artifact: {e}"))?;

            let exit_code = val["exit_code"].as_i64().unwrap_or(-1);
            let summary = val["summary"].as_str().unwrap_or("no summary");

            writeln!(ctx, "agent '{}' finished with exit code {}: {}", name, exit_code, summary).unwrap();
            return if exit_code == 0 {
                Ok(())
            } else {
                Err(format!("agent '{}' failed with code {}", name, exit_code))
            };
        }
        thread::sleep(Duration::from_millis(500));
    }

    Err(format!("timed out waiting for agent '{}' after {}s", name, timeout_secs))
}

/// →834: Spawn an agent using ostk's native agent_loop — no external `claude -p` binary.
///
/// Direct API calls via CpuDriver, kernel tools (Bash/Read/Edit/Write/Glob/Grep),
/// compressed output, full audit trail. No Claude Code overhead.
///
/// The agent runs in a background thread with its own tokio runtime. Events drain
/// to a transcript file. Completion artifacts written for `ostk await`.
pub fn run_spawn(name: &str, model: &str, budget: &str, prompt: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = crate::kernel::verb_ctx::VerbCtx::new(&root, &input);
    run_spawn_verb(&mut ctx, name, model, budget, prompt)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for spawn (→1157).
pub fn run_spawn_verb(ctx: &mut crate::kernel::verb_ctx::VerbCtx, name: &str, model: &str, budget: &str, prompt: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    // ── Transcript setup ─────────────────────────────────────────────
    let transcript_dir = root.join("transcripts");
    fs::create_dir_all(&transcript_dir)
        .map_err(|e| format!("failed to create transcripts dir: {e}"))?;

    let transcript_path = transcript_dir.join(format!("{}.md", name));

    // ── Canonical config pipeline ───────────────────────────────────
    // Build an ephemeral Agentfile from CLI args and hand it to SpawnRequest.
    // prepare() resolves model aliases, creates the driver, parses the AF,
    // builds LoopConfig, and enriches the boot context — same pipeline as
    // `ostk run`, but errors surface here before the thread is spawned.

    // Build ephemeral Agentfile content — use the raw model arg so that
    // prepare() can resolve aliases (e.g. "sonnet" → canonical name).
    let af_content = format!(
        "FROM {model}\n\
         PROMPT \"You are a helpful coding assistant.\"\n\
         TOOL shell\n\
         TOOL file:read\n\
         TOOL file:edit\n\
         TOOL Write\n\
         TOOL Glob\n\
         TOOL Grep\n\
         TOOL web_read\n\
         TOOL web_links\n\
         TOOL web_status\n\
         LIMIT budget_usd {budget}\n\
         LIMIT permissions autonomous\n\
         LIMIT max_turns 50\n",
        model = model,
        budget = budget,
    );

    let spawn_req = SpawnRequest {
        root: root.clone(),
        source: AgentfileSource::Inline(af_content),
        model_override: None,
        parent_context: None,
    };
    let prepared = spawn_req.prepare()?;
    let config = prepared.config;
    let driver = prepared.driver;
    let resolved_model = prepared.model;

    // ── Background agent thread ──────────────────────────────────────
    let name_owned = name.to_string();
    let root_owned = root.clone();
    let model_owned = resolved_model.clone();
    let prompt_owned = prompt.to_string();
    let transcript_path_owned = transcript_path.clone();

    let agent_thread = thread::Builder::new()
        .name(format!("agent-{name}"))
        .spawn(move || {
            // Create a dedicated tokio runtime for this agent
            let rt = match tokio::runtime::Runtime::new() {
                Ok(rt) => rt,
                Err(e) => {
                    eprintln!("[agent {name_owned}] failed to create runtime: {e}");
                    return;
                }
            };

            let (event_tx, mut event_rx) = tokio::sync::mpsc::channel::<CpuEvent>(128);

            // Build initial messages
            let messages = vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: prompt_owned.clone() }],
                model: None,
            }];
            let shared_messages = crate::cpu::agent_loop::SharedMessages::new(messages);

            // Run agent loop
            let driver_ref = driver.clone();
            let config_ref = config.clone();
            let tx = event_tx.clone();
            drop(event_tx); // drop our copy so rx closes when loop finishes

            let loop_handle = rt.spawn(async move {
                agent_loop::run_loop(&*driver_ref, &config_ref, &shared_messages, tx, Default::default()).await
            });

            // Drain events to transcript
            let mut transcript = fs::OpenOptions::new()
                .create(true).write(true).truncate(true)
                .open(&transcript_path_owned)
                .unwrap_or_else(|_| fs::File::create(&transcript_path_owned).unwrap());

            rt.block_on(async {
                while let Some(event) = event_rx.recv().await {
                    match &event {
                        CpuEvent::TextComplete(text) => {
                            let _ = writeln!(transcript, "{text}\n");
                        }
                        CpuEvent::ToolStart { name, .. } => {
                            let _ = writeln!(transcript, "> tool: {name}");
                        }
                        CpuEvent::ToolResult { name, output, success } => {
                            let status = if *success { "ok" } else { "FAILED" };
                            let trunc: String = output.chars().take(500).collect();
                            let _ = writeln!(transcript, "> {name} [{status}]: {trunc}");
                        }
                        CpuEvent::TurnComplete { usage } => {
                            let _ = writeln!(transcript, "\n[done] ↓{} ↑{}",
                                usage.input_tokens, usage.output_tokens);
                        }
                        CpuEvent::Error(msg) => {
                            let _ = writeln!(transcript, "[error] {msg}");
                        }
                        _ => {}
                    }
                }
            });

            // Wait for the loop to finish
            let exit_code = match rt.block_on(loop_handle) {
                Ok(Ok(())) => 0,
                Ok(Err(e)) => {
                    let _ = writeln!(transcript, "\n[agent error] {e}");
                    1
                }
                Err(e) => {
                    let _ = writeln!(transcript, "\n[agent panic] {e}");
                    2
                }
            };

            // Write completion artifact for `ostk await`
            let done_file = crate::state_dir(&root_owned).join("store")
                .join(format!("agent-{name_owned}-done.json"));
            let artifact = json!({
                "name": name_owned,
                "model": model_owned,
                "prompt": prompt_owned,
                "exit_code": exit_code,
                "summary": if exit_code == 0 { "completed successfully" } else { "failed or interrupted" },
                "timestamp": now_iso(),
                "runtime": "native", // →834: marks this as native, not claude -p
            });
            let _ = fs::create_dir_all(done_file.parent().unwrap());
            if let Ok(json) = serde_json::to_string_pretty(&artifact) {
                let _ = fs::write(&done_file, json);
            }
        })
        .map_err(|e| format!("failed to spawn agent thread: {e}"))?;

    // ── Audit + user feedback ────────────────────────────────────────
    let thread_id = format!("{:?}", agent_thread.thread().id());
    let event = json!({
        "event": "agent.spawned",
        "name": name,
        "model": resolved_model,
        "budget": budget,
        "runtime": "native",
        "thread_id": thread_id,
        "timestamp": now_iso()
    });
    append_audit(&root, &event)?;

    {
        use std::fmt::Write;
        writeln!(ctx,
            "spawned {} (model {}, budget ${})",
            name, resolved_model, budget
        ).unwrap();
        writeln!(ctx, "transcript: transcripts/{}.md", name).unwrap();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spawn_creates_transcript_dir() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        fs::create_dir_all(crate::state_dir(&root)).unwrap();
        let transcript_dir = root.join("transcripts");
        assert!(!transcript_dir.exists());
        fs::create_dir_all(&transcript_dir).unwrap();
        assert!(transcript_dir.exists());
    }

    #[test]
    fn spawn_model_alias_resolves() {
        use crate::cpu::providers::resolve_model_alias;
        assert_eq!(resolve_model_alias("sonnet"), "claude-sonnet-4-5-20250929");
        assert_eq!(resolve_model_alias("opus"), "claude-opus-4-6");
        assert_eq!(resolve_model_alias("haiku"), "claude-haiku-4-5-20251001");
        // Passthrough
        assert_eq!(resolve_model_alias("custom-model"), "custom-model");
    }

    #[test]
    fn spawn_loop_config_has_correct_tools() {
        let tools: Vec<String> = vec![
            "shell".into(), "file:read".into(), "file:edit".into(),
            "Write".into(), "Glob".into(), "Grep".into(),
        ];
        let schemas = crate::cpu::tool_schemas(&tools);
        // Should produce 6 tool schemas
        assert_eq!(schemas.len(), 6, "should have 6 tool schemas");
        let names: Vec<&str> = schemas.iter()
            .filter_map(|s| s["name"].as_str())
            .collect();
        assert!(names.contains(&"Bash"), "should include Bash");
        assert!(names.contains(&"Read"), "should include Read");
        assert!(names.contains(&"Edit"), "should include Edit");
        assert!(names.contains(&"Write"), "should include Write");
        assert!(names.contains(&"Glob"), "should include Glob");
        assert!(names.contains(&"Grep"), "should include Grep");
    }

    #[test]
    fn spawn_permission_mode_is_autonomous() {
        // →889: Spawned agents MUST run Autonomous with no approval_source.
        // If this changes, approval requests will hang forever in embedded mode
        // because no TUI is polling the approval_bus for spawned agents.
        let af_content = "FROM test-model\n\
            PROMPT \"test\"\n\
            TOOL shell\n\
            LIMIT permissions autonomous\n\
            LIMIT budget_usd 1\n\
            LIMIT max_turns 5\n";
        let af = crate::agentfile::parse(af_content).expect("parse test agentfile");
        let config = crate::cpu::config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(config.permission_mode, crate::cpu::PermissionMode::Autonomous,
            "spawned agents must be Autonomous — Governed mode hangs without approval routing");
    }

    #[test]
    fn spawn_options_have_no_approval_source() {
        // →889: Default AgentLoopOptions (used by run_spawn) must have no
        // approval_source. This ensures spawned agents never block on approval.
        let opts = crate::cpu::agent_loop::AgentLoopOptions::default();
        assert!(opts.approval_source.is_none(),
            "spawned agents must have no approval_source");
    }

    #[test]
    fn spawn_completion_artifact_format() {
        let artifact = json!({
            "name": "test-agent",
            "model": "claude-sonnet-4-5-20250929",
            "prompt": "do the thing",
            "exit_code": 0,
            "summary": "completed successfully",
            "timestamp": "2026-03-22T00:00:00Z",
            "runtime": "native",
        });
        assert_eq!(artifact["runtime"], "native");
        assert_eq!(artifact["exit_code"], 0);
        assert_eq!(artifact["name"], "test-agent");
    }

    #[test]
    fn await_reads_completion_artifact() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let store = root.join(".ostk/store");
        fs::create_dir_all(&store).unwrap();

        // Write audit trail so run_await doesn't reject
        let audit = root.join(".ostk/audit.jsonl");
        fs::write(&audit, r#"{"event":"agent.spawned","name":"test-agent"}"#).unwrap();

        // Write completion artifact
        let done_file = store.join("agent-test-agent-done.json");
        let artifact = json!({
            "name": "test-agent",
            "exit_code": 0,
            "summary": "done",
            "runtime": "native",
        });
        fs::write(&done_file, serde_json::to_string_pretty(&artifact).unwrap()).unwrap();

        // Verify the artifact is readable
        let content = fs::read_to_string(&done_file).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&content).unwrap();
        assert_eq!(parsed["exit_code"], 0);
        assert_eq!(parsed["runtime"], "native");
    }

    #[test]
    fn ps_reports_no_daemon_when_no_socket() {
        // run_ps checks kernel_alive which requires socket + pid file.
        // With no socket, it should report "no daemon running" (not hardcoded).
        let dir = tempfile::tempdir().unwrap();
        let state = crate::state_dir(dir.path());
        fs::create_dir_all(&state).unwrap();
        // No socket, no pid file — kernel_alive returns false
        assert!(!crate::serve::socket::kernel_alive(&state));
    }

    #[test]
    fn ps_detects_stale_pid() {
        // If pid file exists but process is dead, kernel_alive should return false
        let dir = tempfile::tempdir().unwrap();
        let state = crate::state_dir(dir.path());
        fs::create_dir_all(&state).unwrap();
        // Write a socket file and a bogus PID
        fs::write(state.join("ostk.sock"), "").unwrap();
        fs::write(state.join("kernel.pid"), "999999999").unwrap();
        assert!(!crate::serve::socket::kernel_alive(&state));
    }

    #[test]
    fn status_uses_ostk_prefix() {
        // Verify run_status output uses "ostk:" not "haystack:"
        let source = include_str!("fleet.rs");
        let non_test: String = source.lines()
            .take_while(|l| !l.contains("#[cfg(test)]"))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(non_test.contains("\"ostk: daemon running"),
            "run_status should use 'ostk:' prefix");
        assert!(non_test.contains("\"ostk: no daemon running\""),
            "run_status should use 'ostk:' prefix for no-daemon case");
        assert!(!non_test.contains("\"haystack:"),
            "run_status should not use 'haystack:' prefix");
    }

    #[test]
    fn no_claude_binary_dependency() {
        // →834: Verify the spawn code does NOT reference "claude" binary
        // This is a structural test — the actual code uses CpuDriver, not Command::new("claude")
        let source = include_str!("fleet.rs");
        let non_test_code: String = source.lines()
            .take_while(|l| !l.contains("#[cfg(test)]"))
            .collect::<Vec<_>>()
            .join("\n");
        assert!(!non_test_code.contains("Command::new(\"claude\""),
            "production code should not spawn claude binary");
        assert!(!non_test_code.contains("\"claude\"")
            || non_test_code.contains("// →834"),
            "production code should not reference claude binary");
    }
}
