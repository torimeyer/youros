//! `ostk ask <tack>` — single-turn LLM call via CpuDriver.
//!
//! System prompt: live register dump (same as TUI boot state).
//! User message: the tack expression.
//! Streams response to stdout.
//!
//! →751: Uses CpuDriver (same path as run_kernel and TUI) instead of raw HTTP.

use std::fs;
use std::io::Write as _;

use crate::commands::run::resolve_auto_model;
use crate::find_project_root;

/// Run a single-turn LLM call with the tack expression as user message.
/// Uses CpuDriver for provider routing — same path as run_kernel() and TUI.
pub fn ask(tack_expression: &str, max_tokens: u64) -> Result<(), String> {
    let root = find_project_root().unwrap_or_else(|_| {
        std::path::PathBuf::from(
            std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string()),
        )
    });
    let ostk_dir = crate::state_dir(&root);
    let system_prompt = crate::commands::boot::build_register_dump(&root, &ostk_dir);

    // Model selection: preferred_model file → HUMANFILE MODEL → FROM auto
    let model = fs::read_to_string(ostk_dir.join("staging/preferred_model"))
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(resolve_auto_model);

    // Create CpuDriver for the resolved model — same routing as TUI/run_kernel.
    let driver = crate::cpu::create_driver(&model)
        .map_err(|e| format!("failed to create driver for {model}: {e}"))?;

    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| format!("runtime: {e}"))?;

    rt.block_on(async {
        use crate::cpu::anthropic::{ContentBlock, Message};
        use crate::cpu::InferenceRequest;
        use futures_util::StreamExt;

        let params = InferenceRequest {
            model: model.clone(),
            max_tokens: max_tokens as u32,
            system: Some(system_prompt),
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: tack_expression.to_string(),
                }],
                model: None,
            }],
            tools: vec![],
            tool_choice: None,
            ..Default::default()
        };

        let stream = driver.create_stream(params).await?;
        futures_util::pin_mut!(stream);

        let mut stdout = std::io::stdout();
        while let Some(event) = stream.next().await {
            match event? {
                crate::cpu::anthropic::StreamEvent::TextDelta(text) => {
                    let _ = stdout.write_all(text.as_bytes());
                    let _ = stdout.flush();
                }
                crate::cpu::anthropic::StreamEvent::MessageStop { .. } => break,
                _ => {}
            }
        }
        println!();
        Ok(())
    })
}

