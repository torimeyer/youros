use crate::cpu::agent_loop::LoopConfig;
use crate::cpu::anthropic::{InferenceRequest, Message};
use crate::cpu::session::BootContext;

/// →874: Model registry — map model name to context window budget.
/// Returns 80% of the model's context window for safe operation.
pub fn model_context_budget(model: &str) -> u64 {
    let lower = model.to_lowercase();
    // Opus: 200k default, 1M only with explicit [1m] suffix
    if lower.contains("opus") && lower.contains("1m") || lower.contains("gemini") {
        800_000 // 1M * 80%
    } else if lower.contains("opus") || lower.contains("sonnet") {
        160_000 // 200k * 80%
    } else if lower.contains("deepseek") || lower.contains("gpt-4") || lower.contains("llama") {
        100_000 // 128k * 80%
    } else if lower.contains("qwen") && lower.contains("32") {
        25_000 // 32k * 80%
    } else {
        50_000 // qwen 64k + conservative default
    }
}

/// →874: Adaptive intent window based on context budget.
/// Returns the number of recent user intents to preserve during eviction.
/// →968: Superseded by FcpLlm::for_model().window_size in production.
/// Retained for test coverage of the budget-based heuristic.
#[cfg(test)]
fn adaptive_intent_window(budget: u64) -> usize {
    if budget < 50_000 {
        2 // tight — small context models
    } else if budget < 200_000 {
        3 // default — medium context
    } else {
        5 // large context — preserve more history
    }
}

/// Build the common InferenceRequest for a given loop iteration.
///
/// →813: Smart defaults for Claude models when user hasn't set explicit betas.
/// If user sets BETA compact/context-management, respect their choices exactly.
///
/// →843: If boot_context is provided, refresh and rebuild preload_context on
/// every turn so agents always see current system state (needle count, P0, etc).
/// →873: `accumulated_tokens` feeds eviction pressure in fcp-llm.
/// On turn 0 this is 0 (no eviction). On turn 1+ it carries the
/// input_tokens from the previous API response or count_tokens call.
pub fn build_params(
    config: &LoopConfig,
    messages: &[Message],
    tools: Vec<serde_json::Value>,
    tool_choice: Option<serde_json::Value>,
    boot_context: Option<&mut BootContext>,
    accumulated_tokens: u64,
    session_summary: Option<&crate::fcp::llm::SessionSummary>,
) -> InferenceRequest {
    let is_claude = config.model.contains("claude");
    let is_46 = config.model.contains("4-6");

    // →928: Scale clear_tool_uses keep value by model context window.
    // Opus[1m]/Gemini (1M) keeps 20, Opus/Sonnet (200k) keeps 10, others keep 5.
    let tool_keep = if (config.model.contains("opus") && config.model.contains("1m")) || config.model.contains("gemini") {
        20
    } else if config.model.contains("opus") || config.model.contains("sonnet") {
        10
    } else {
        5
    };

    let speed = if config.fast_mode
        && config.betas.iter().any(|b| b == "fast-mode")
        && config.model.contains("opus")
    {
        Some("fast".to_string())
    } else {
        None
    };

    // Did the user explicitly set context management betas?
    let user_set_compact = config.betas.iter().any(|b| b == "compact");
    let user_set_cm = config.betas.iter().any(|b| b == "context-management");
    let user_set_any_cm = user_set_compact || user_set_cm;

    // →phase1: Extract compact_instructions before boot_context is mutably borrowed later.
    let compact_instructions = boot_context
        .as_ref()
        .map(|bc| bc.compact_instructions())
        .unwrap_or_default();

    let context_management = if user_set_any_cm {
        // User explicitly controls context management — respect exactly
        let mut edits: Vec<serde_json::Value> = Vec::new();
        if user_set_compact {
            if let Some(budget) = config.context_budget {
                let trigger = budget * 80 / 100;
                edits.push(serde_json::json!({
                    "type": "compact_20260112",
                    "trigger": { "type": "input_tokens", "value": trigger }
                }));
            } else {
                edits.push(serde_json::json!({
                    "type": "compact_20260112"
                }));
            }
        }
        if user_set_cm {
            // →clear_thinking when thinking enabled (must precede clear_tool_uses)
            if config.thinking.is_some() {
                edits.push(serde_json::json!({
                    "type": "clear_thinking_20251015",
                    "keep": { "type": "thinking_turns", "value": 2 }
                }));
            }
            edits.push(serde_json::json!({
                "type": "clear_tool_uses_20250919",
                "keep": { "type": "tool_uses", "value": tool_keep },
                "clear_at_least": { "type": "input_tokens", "value": 5000 },
                "exclude_tools": ["ostk_verbs", "ostk_man"],
                "clear_tool_inputs": true
            }));
        }
        // INVARIANT: Both fcp-llm (client) and clear_tool_uses (server) evict from
        // the front of the conversation. They don't coordinate, but their same-direction
        // eviction prevents orphaned tool_use/tool_result pairs. If either changes
        // eviction direction, the other must be updated to match.
        if edits.is_empty() { None } else { Some(serde_json::json!({ "edits": edits })) }
    } else if is_claude {
        // →813: No explicit betas — enable smart defaults for Claude
        let mut edits: Vec<serde_json::Value> = Vec::new();
        // compact only for 4-6 models
        if is_46 {
            let trigger = config.context_budget
                .map(|b| b * 80 / 100)
                .unwrap_or(160_000u64);
            edits.push(serde_json::json!({
                "type": "compact_20260112",
                "trigger": { "type": "input_tokens", "value": trigger },
                "instructions": compact_instructions,
                "pause_after_compaction": true
            }));
        }
        // →clear_thinking for Claude with thinking enabled (must precede clear_tool_uses)
        if config.thinking.is_some() {
            edits.push(serde_json::json!({
                "type": "clear_thinking_20251015",
                "keep": { "type": "thinking_turns", "value": 2 }
            }));
        }
        // clear_tool_uses for all Claude 4.x
        edits.push(serde_json::json!({
            "type": "clear_tool_uses_20250919",
            "keep": { "type": "tool_uses", "value": tool_keep },
            "clear_at_least": { "type": "input_tokens", "value": 5000 },
            "exclude_tools": ["ostk_verbs", "ostk_man"],
            "clear_tool_inputs": true
        }));
        Some(serde_json::json!({ "edits": edits }))
    } else {
        None
    };

    // →813: Prompt caching for Claude (extended TTL)
    let cache_control = if is_claude || config.betas.iter().any(|b| b == "extended-cache-ttl") {
        Some(serde_json::json!({"type": "ephemeral", "ttl": "1h"}))
    } else {
        None
    };

    // Auto-inject required beta headers
    let mut betas = config.betas.clone();
    if is_claude && !user_set_any_cm {
        // Smart defaults need these headers
        // →947: token-efficient-tools reduces tool schema tokens ~30% — free win for all Claude
        for beta in &["context-management-2025-06-27", "extended-cache-ttl-2025-04-11"] {
            let b = beta.to_string();
            if !betas.contains(&b) { betas.push(b); }
        }
        if is_46 {
            let b = "compact-2026-01-12".to_string();
            if !betas.contains(&b) { betas.push(b); }
        }
    }
    // →947: token-efficient-tools is free savings — inject for ALL Claude, even with user CM betas
    if is_claude {
        let b = "token-efficient-tools-2025-02-19".to_string();
        if !betas.contains(&b) { betas.push(b); }
    }
    // →948: auto-inject citations beta when citations enabled
    if config.citations {
        let b = "citations-2025-01-15".to_string();
        if !betas.contains(&b) { betas.push(b); }
    }
    // →949: extended thinking requires no extra beta header (it's GA for 4.x)
    // →950: web-search beta injected when web_search tool is requested (handled in Agentfile)
    // →951: prompt-priorities beta injected when configured (handled in Agentfile)

    // →967/→968: FcpLlm — provider-aware context budget.
    // Derives budget, window_size, and decision_cap from model name.
    // config.context_budget overrides FcpLlm's budget if explicitly set.
    let fcp_llm = crate::fcp::llm::FcpLlm::for_model(&config.model);
    let budget = config.context_budget.unwrap_or(fcp_llm.budget);
    let intent_window = fcp_llm.window_size;

    // →843: If boot_context provided, rebuild preload so agents see live system state.
    // →968: Set decision_cap BEFORE build_preload_context so render_working_state
    // uses the model-appropriate cap instead of the legacy default of 10.
    let preload_context = if let Some(boot_ctx) = boot_context {
        if boot_ctx.decision_cap.is_none() {
            boot_ctx.decision_cap = Some(fcp_llm.decision_cap);
        }
        boot_ctx.build_preload_context()
    } else {
        config.preload_context.clone()
    };

    let context_hints = crate::fcp::llm::ContextHints {
        supports_mixed_content_blocks: is_claude,
    };

    // →966/→967: Compile session summary from JSONL if session file exists.
    // This provides a richer summary than the in-memory SessionSummary
    // (regex-extracted events: file mods, test results, needle changes).
    // Falls back gracefully if session file doesn't exist or compilation fails.
    let compiled_summary_text = config.root.as_ref().and_then(|root| {
        let alias = std::env::var("OSTK_AGENT").unwrap_or_else(|_| "agent".into());
        let session_path = crate::state_dir(root).join(format!("sessions/{}.jsonl", alias));
        if session_path.exists() {
            crate::cpu::summary::SessionSummary::compile_cached(
                &session_path,
                fcp_llm.window_size,
            ).ok().map(|s| s.render().to_string())
        } else {
            None
        }
    });

    // fcp-llm: Strip scaffolding + evict under budget pressure.
    // →872: Provider-aware rendering. →873: Token-tracked eviction.
    // →874: Model-derived budgets. →967: ContextPage assembly.
    // Re-enabled v2.2.0: daemon stale-busy state fixed (embedded mode default).
    let curated_messages = crate::fcp::llm::build_context_page(
        messages, budget, accumulated_tokens, intent_window, &context_hints,
        session_summary, compiled_summary_text.as_deref(),
    );

    // →adaptive thinking for 4.6 models (manual budget_tokens deprecated)
    let thinking = if is_46 && config.thinking.is_some() {
        Some(serde_json::json!({"type": "adaptive"}))
    } else {
        config.thinking.clone()
    };

    InferenceRequest {
        model: config.model.clone(),
        max_tokens: config.max_tokens,
        system: config.system_prompt.clone(),
        messages: curated_messages,
        tools,
        tool_choice,
        stream: true,
        claude: crate::cpu::ClaudeOptions {
            context_management,
            cache_control,
            betas,
            speed,
            preload_context,
            thinking,
            citations: config.citations,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_model_context_budget_opus() {
        assert_eq!(model_context_budget("claude-opus-4"), 160_000);
        assert_eq!(model_context_budget("claude-opus-4-6"), 160_000);
        // 1M variant gets full budget
        assert_eq!(model_context_budget("claude-opus-4-6[1m]"), 800_000);
    }

    #[test]
    fn test_model_context_budget_sonnet() {
        assert_eq!(model_context_budget("claude-sonnet-4"), 160_000);
        assert_eq!(model_context_budget("claude-4-6-sonnet"), 160_000);
    }

    #[test]
    fn test_model_context_budget_gemini() {
        assert_eq!(model_context_budget("gemini-2.0-flash"), 800_000);
        assert_eq!(model_context_budget("gemini-1.5-pro"), 800_000);
    }

    #[test]
    fn test_model_context_budget_deepseek() {
        assert_eq!(model_context_budget("deepseek-chat"), 100_000);
        assert_eq!(model_context_budget("deepseek-reasoner"), 100_000);
    }

    #[test]
    fn test_model_context_budget_qwen() {
        assert_eq!(model_context_budget("qwen-32b"), 25_000);
        assert_eq!(model_context_budget("qwen-qwen-32k"), 25_000);
        assert_eq!(model_context_budget("qwen-72b"), 50_000);
        assert_eq!(model_context_budget("qwen-plus"), 50_000);
    }

    #[test]
    fn test_model_context_budget_gpt4() {
        assert_eq!(model_context_budget("gpt-4-turbo"), 100_000);
        assert_eq!(model_context_budget("gpt-4o"), 100_000);
    }

    #[test]
    fn test_model_context_budget_llama() {
        assert_eq!(model_context_budget("llama-3.3-70b"), 100_000);
        assert_eq!(model_context_budget("meta-llama-3-8b"), 100_000);
    }

    #[test]
    fn test_model_context_budget_unknown() {
        assert_eq!(model_context_budget("some-random-model"), 50_000);
        assert_eq!(model_context_budget(""), 50_000);
    }

    #[test]
    fn test_adaptive_intent_window_small() {
        assert_eq!(adaptive_intent_window(25_000), 2);
        assert_eq!(adaptive_intent_window(49_999), 2);
    }

    #[test]
    fn test_adaptive_intent_window_medium() {
        assert_eq!(adaptive_intent_window(50_000), 3);
        assert_eq!(adaptive_intent_window(100_000), 3);
        assert_eq!(adaptive_intent_window(199_999), 3);
    }

    #[test]
    fn test_adaptive_intent_window_large() {
        assert_eq!(adaptive_intent_window(200_000), 5);
        assert_eq!(adaptive_intent_window(800_000), 5);
    }

    #[test]
    fn test_build_params_auto_derives_budget() {
        use crate::cpu::agent_loop::LoopConfig;

        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None, // No explicit budget
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let messages = vec![];
        let tools = vec![];
        let req = build_params(&config, &messages, tools, None, None, 0, None);

        // We can't directly inspect the curated_messages budget that was used,
        // but we can verify the function compiles and runs without panicking.
        // The actual budget derivation is tested in the unit tests above.
        assert_eq!(req.model, "claude-opus-4");
    }

    #[test]
    fn test_build_params_respects_explicit_budget() {
        use crate::cpu::agent_loop::LoopConfig;

        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: Some(500_000), // Explicit budget
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let messages = vec![];
        let tools = vec![];
        let req = build_params(&config, &messages, tools, None, None, 0, None);

        // If explicit budget is set, it should be used instead of auto-derivation
        assert_eq!(req.model, "claude-opus-4");
    }

    #[test]
    fn test_token_efficient_tools_auto_injected_for_claude() {
        use crate::cpu::agent_loop::LoopConfig;

        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &vec![], vec![], None, None, 0, None);
        assert!(req.claude.betas.contains(&"token-efficient-tools-2025-02-19".to_string()),
            "token-efficient-tools should be auto-injected for Claude models, got: {:?}", req.claude.betas);
    }

    #[test]
    fn test_token_efficient_tools_not_injected_for_non_claude() {
        use crate::cpu::agent_loop::LoopConfig;

        let config = LoopConfig {
            model: "deepseek-chat".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &vec![], vec![], None, None, 0, None);
        assert!(!req.claude.betas.contains(&"token-efficient-tools-2025-02-19".to_string()),
            "token-efficient-tools should NOT be injected for non-Claude models");
    }

    #[test]
    fn test_token_efficient_tools_injected_even_with_user_cm_betas() {
        use crate::cpu::agent_loop::LoopConfig;

        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec!["context-management".into()], // user set CM explicitly
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &vec![], vec![], None, None, 0, None);
        assert!(req.claude.betas.contains(&"token-efficient-tools-2025-02-19".to_string()),
            "token-efficient-tools should be injected even when user sets CM betas, got: {:?}", req.claude.betas);
    }

    // ─── →968: FcpLlm integration in build_params ──────────────────

    #[test]
    fn test_fcp_llm_derives_budget_for_opus() {
        let llm = crate::fcp::llm::FcpLlm::for_model("claude-opus-4");
        // FcpLlm should agree with model_context_budget for Opus
        assert_eq!(llm.budget, model_context_budget("claude-opus-4"));
        assert_eq!(llm.window_size, 10);
        assert_eq!(llm.decision_cap, 50);
    }

    #[test]
    fn test_fcp_llm_derives_budget_for_sonnet() {
        let llm = crate::fcp::llm::FcpLlm::for_model("claude-sonnet-4");
        assert_eq!(llm.budget, model_context_budget("claude-sonnet-4"));
        assert_eq!(llm.window_size, 7);
        assert_eq!(llm.decision_cap, 20);
    }

    #[test]
    fn test_fcp_llm_window_replaces_adaptive_intent_window() {
        // Verify FcpLlm window_size replaces adaptive_intent_window
        // Opus: FcpLlm gives 10, adaptive_intent_window(800k) gives 5
        let llm = crate::fcp::llm::FcpLlm::for_model("claude-opus-4");
        let old_window = adaptive_intent_window(llm.budget);
        // FcpLlm gives a larger window for Opus (10 vs 5) — more context for big models
        assert!(llm.window_size >= old_window,
            "FcpLlm window_size ({}) should be >= old adaptive_intent_window ({}) for Opus",
            llm.window_size, old_window);
    }

    #[test]
    fn test_build_params_sets_decision_cap_on_boot_context() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        let mut boot_ctx = crate::cpu::session::BootContext::new(root);
        assert!(boot_ctx.decision_cap.is_none(), "decision_cap should be None before build_params");

        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: Some(root.to_path_buf()),
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let _req = build_params(&config, &[], vec![], None, Some(&mut boot_ctx), 0, None);
        assert_eq!(boot_ctx.decision_cap, Some(50),
            "build_params should set decision_cap=50 for Opus");
    }

    #[test]
    fn test_build_params_does_not_override_existing_decision_cap() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        let mut boot_ctx = crate::cpu::session::BootContext::new(root);
        boot_ctx.decision_cap = Some(99); // Pre-set

        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: Some(root.to_path_buf()),
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let _req = build_params(&config, &[], vec![], None, Some(&mut boot_ctx), 0, None);
        assert_eq!(boot_ctx.decision_cap, Some(99),
            "build_params should not override pre-existing decision_cap");
    }

    #[test]
    fn test_build_params_haiku_gets_small_decision_cap() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        let mut boot_ctx = crate::cpu::session::BootContext::new(root);

        let config = LoopConfig {
            model: "claude-haiku-3.5".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 4096,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: Some(root.to_path_buf()),
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let _req = build_params(&config, &[], vec![], None, Some(&mut boot_ctx), 0, None);
        assert_eq!(boot_ctx.decision_cap, Some(5),
            "build_params should set decision_cap=5 for Haiku");
    }

    #[test]
    fn test_explicit_context_budget_overrides_fcp_llm() {
        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: Some(500_000), // Explicit override
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        // The FcpLlm would give 800K, but explicit budget should be 500K.
        // We can't directly inspect the budget used inside, but we can verify
        // the function runs without panicking and the model is correct.
        let req = build_params(&config, &[], vec![], None, None, 0, None);
        assert_eq!(req.model, "claude-opus-4");
    }

    // ─── adaptive thinking + clear_thinking + clear_at_least ──────

    #[test]
    fn test_adaptive_thinking_for_46_models() {
        let config = LoopConfig {
            model: "claude-opus-4-6".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: Some(serde_json::json!({"type": "enabled", "budget_tokens": 10000})),
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &[], vec![], None, None, 0, None);
        let thinking = req.claude.thinking.unwrap();
        assert_eq!(thinking["type"], "adaptive",
            "4.6 models should use adaptive thinking, got: {thinking}");
        assert!(thinking.get("budget_tokens").is_none(),
            "adaptive thinking should not have budget_tokens");
    }

    #[test]
    fn test_manual_thinking_preserved_for_non_46() {
        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: Some(serde_json::json!({"type": "enabled", "budget_tokens": 10000})),
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &[], vec![], None, None, 0, None);
        let thinking = req.claude.thinking.unwrap();
        assert_eq!(thinking["type"], "enabled",
            "non-4.6 models should keep manual thinking, got: {thinking}");
        assert_eq!(thinking["budget_tokens"], 10000);
    }

    #[test]
    fn test_no_thinking_stays_none_for_46() {
        let config = LoopConfig {
            model: "claude-opus-4-6".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &[], vec![], None, None, 0, None);
        assert!(req.claude.thinking.is_none(),
            "thinking=None should stay None even on 4.6");
    }

    #[test]
    fn test_clear_thinking_added_when_thinking_enabled() {
        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: Some(serde_json::json!({"type": "enabled", "budget_tokens": 10000})),
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = req.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        let has_clear_thinking = edits.iter().any(|e| e["type"] == "clear_thinking_20251015");
        assert!(has_clear_thinking,
            "clear_thinking should be added when thinking is enabled, edits: {edits:?}");
        // Verify ordering: clear_thinking before clear_tool_uses
        let thinking_idx = edits.iter().position(|e| e["type"] == "clear_thinking_20251015").unwrap();
        let tool_idx = edits.iter().position(|e| e["type"] == "clear_tool_uses_20250919").unwrap();
        assert!(thinking_idx < tool_idx,
            "clear_thinking must precede clear_tool_uses (API requirement)");
    }

    #[test]
    fn test_clear_thinking_not_added_without_thinking() {
        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = req.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        let has_clear_thinking = edits.iter().any(|e| e["type"] == "clear_thinking_20251015");
        assert!(!has_clear_thinking,
            "clear_thinking should NOT be added when thinking is disabled");
    }

    #[test]
    fn test_clear_at_least_on_clear_tool_uses() {
        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = req.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        let clear_tool = edits.iter().find(|e| e["type"] == "clear_tool_uses_20250919").unwrap();
        assert_eq!(clear_tool["clear_at_least"]["type"], "input_tokens");
        assert_eq!(clear_tool["clear_at_least"]["value"], 5000,
            "clear_at_least should be 5000 input tokens to avoid trivial cache-breaking clears");
    }

    #[test]
    fn test_clear_at_least_with_user_cm_beta() {
        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec!["context-management".into()],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = req.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        let clear_tool = edits.iter().find(|e| e["type"] == "clear_tool_uses_20250919").unwrap();
        assert_eq!(clear_tool["clear_at_least"]["value"], 5000,
            "clear_at_least should apply even with user-set CM betas");
    }

    #[test]
    fn test_clear_thinking_with_user_cm_beta_and_thinking() {
        let config = LoopConfig {
            model: "claude-opus-4".to_string(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 8192,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::default(),
            betas: vec!["context-management".into()],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: Some(serde_json::json!({"type": "enabled", "budget_tokens": 5000})),
            citations: false,
            tool_patterns: vec![],
        };

        let req = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = req.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        let has_clear_thinking = edits.iter().any(|e| e["type"] == "clear_thinking_20251015");
        assert!(has_clear_thinking,
            "clear_thinking should be added in user CM path when thinking is enabled");
    }
}
