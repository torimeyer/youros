//! ->752: `ostk do "prompt text"` — one-shot AgentSession via kernel agent loop.
//!
//! Creates a SessionManager, dispatches a single prompt through the kernel's
//! agent loop (not the legacy `run.rs` HTTP path), streams the response to
//! stdout, and exits on TurnComplete. The simplest possible kernel-native
//! agent invocation.

use std::io::{self, Write};

use crate::cpu::agent_loop::{CpuEvent, LoopConfig};
use crate::cpu::session::{AgentSession, BootContext};
use crate::cpu::PermissionMode;
use crate::find_project_root;

/// Run a one-shot prompt through the kernel's agent loop (->752).
///
/// 1. Resolve project root and boot context
/// 2. Create AnthropicClient + AgentSession with kernel identity
/// 3. Send prompt as single user message
/// 4. Stream response to stdout via CpuEvent channel
/// 5. Exit on TurnComplete
pub fn run_oneshot(prompt: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let ostk_dir = crate::state_dir(&root);

    // Build boot context for system prompt enrichment
    let boot_context = BootContext::new(&root);

    // Base system prompt — concise scheduling agent persona
    let base_prompt = "You are ostk.prime, the scheduling intelligence of ostk llmOS. \
                       Be concise and actionable.";
    let system_prompt = boot_context.build_system_prompt(base_prompt);

    // Resolve model: staging/preferred_model → env OSTK_MODEL → cloud default → Ollama fallback
    let model = std::fs::read_to_string(ostk_dir.join("staging/preferred_model"))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("OSTK_MODEL").ok().filter(|s| !s.is_empty()))
        .unwrap_or_else(|| {
            // If no cloud API keys are available, fall back to Ollama
            if std::env::var("ANTHROPIC_API_KEY").is_err()
                && std::env::var("OPENROUTER_API_KEY").is_err()
            {
                // Auto-detect Ollama: check for OLLAMA_HOST or try localhost
                let host = std::env::var("OLLAMA_HOST")
                    .unwrap_or_else(|_| "http://localhost:11434".to_string());
                if let Ok(resp) = std::process::Command::new("curl")
                    .args(["-sf", &format!("{host}/api/tags")])
                    .output()
                    && resp.status.success()
                    && let Ok(tags) = serde_json::from_slice::<serde_json::Value>(&resp.stdout)
                    && let Some(name) = tags["models"][0]["name"].as_str()
                {
                    eprintln!("[kernel] offline mode: using Ollama model {name}");
                    return format!("local/{name}");
                }
                "local/codestral:22b".to_string() // last resort default
            } else {
                "claude-sonnet-4-6".to_string()
            }
        });

    // Build LoopConfig with standard tools for a scheduling agent
    let config = LoopConfig {
        model: model.clone(),
        system_prompt: Some(system_prompt),
        tools: crate::cpu::tool_schemas(&[
            "shell".to_string(),
            "file:read".to_string(),
            "file:edit".to_string(),
        ]),
        max_tokens: 16384,
        max_turns: Some(10), // safety cap for tool loops
        context_budget: None,
        permission_mode: PermissionMode::Governed,
        betas: vec![],
        preload_context: vec![],
        fast_mode: false,
        root: Some(root.clone()),
        thinking: None,
        citations: false,
        tool_patterns: vec![],
    };

    // Create session with kernel identity registration
    let mut session = AgentSession::new("do-oneshot", config, root.clone());

    // Create CpuDriver routed to the correct provider for this model
    let client = crate::cpu::create_driver(&model)
        .map_err(|e| format!("failed to create API client: {e}"))?;

    // Build tokio runtime
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| format!("runtime: {e}"))?;

    eprintln!("[kernel] do: dispatching one-shot (model: {model})");

    // →843: Create boot context for live system state refresh
    let boot_context = crate::cpu::session::BootContext::new(&root);

    // Dispatch the prompt
    session.dispatch(prompt, rt.handle(), &client, &boot_context);

    // Stream events to stdout until TurnComplete
    let mut stdout = io::stdout();
    loop {
        let event = {
            if let Some(ref mut rx) = session.event_rx {
                rt.block_on(rx.recv())
            } else {
                break;
            }
        };

        match event {
            Some(ev) => {
                session.process_event(&ev);
                match &ev {
                    CpuEvent::TextDelta(text) => {
                        let _ = stdout.write_all(text.as_bytes());
                        let _ = stdout.flush();
                    }
                    CpuEvent::TextComplete(_) => {}
                    CpuEvent::ToolStart { name, .. } => {
                        eprintln!("\n[tool] {name}");
                    }
                    CpuEvent::ToolResult { name, output, success } => {
                        let symbol = if *success { "+" } else { "!" };
                        eprintln!("[{symbol}] {name}: {}", truncate_output(output, 200));
                    }
                    CpuEvent::TurnComplete { usage } => {
                        eprintln!(
                            "\n[kernel] done — {}in / {}out tokens",
                            usage.input_tokens, usage.output_tokens
                        );
                        break;
                    }
                    CpuEvent::Error(e) => {
                        eprintln!("\n[error] {e}");
                        break;
                    }
                    _ => {}
                }
            }
            None => break,
        }
    }

    // Save session and let Drop handle deregistration
    let _ = session.save();

    Ok(())
}

/// Truncate output for display, appending "..." if truncated.
fn truncate_output(s: &str, max_len: usize) -> String {
    if s.len() <= max_len {
        s.to_string()
    } else {
        format!("{}...", &s[..max_len])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_truncate_output_short() {
        assert_eq!(truncate_output("hello", 10), "hello");
    }

    #[test]
    fn test_truncate_output_long() {
        let long = "a".repeat(300);
        let result = truncate_output(&long, 200);
        assert_eq!(result.len(), 203); // 200 + "..."
        assert!(result.ends_with("..."));
    }

    #[test]
    fn test_truncate_output_exact() {
        let exact = "a".repeat(200);
        assert_eq!(truncate_output(&exact, 200), exact);
    }
}
