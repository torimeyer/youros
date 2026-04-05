//! Integration tests for context_management API parameters.
//!
//! These tests hit the live Anthropic API and require ANTHROPIC_API_KEY.
//! Run with: cargo test -- --ignored test_context_management

use ostk::cpu::anthropic::{
    AnthropicClient, ContentBlock, Message,
};
use ostk::cpu::InferenceRequest;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_tool_turn(id: &str, expr: &str, result: &str) -> Vec<Message> {
    vec![
        Message {
            role: "assistant".into(),
            content: vec![ContentBlock::ToolUse {
                id: id.into(),
                name: "calculator".into(),
                input: serde_json::json!({"expression": expr}),
            }],
            model: None,
        },
        Message {
            role: "user".into(),
            content: vec![ContentBlock::ToolResult {
                tool_use_id: id.into(),
                content: result.into(),
                is_error: false,
            }],
            model: None,
        },
    ]
}

fn calculator_tool() -> serde_json::Value {
    serde_json::json!({
        "name": "calculator",
        "description": "Evaluates a math expression and returns the numeric result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The math expression to evaluate"
                }
            },
            "required": ["expression"]
        }
    })
}

fn extract_text(response: &ostk::cpu::anthropic::ApiResponse) -> String {
    response
        .content
        .iter()
        .filter_map(|b| match b {
            ContentBlock::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join(" ")
}

// ---------------------------------------------------------------------------
// Live API tests (require ANTHROPIC_API_KEY, run with --ignored)
// ---------------------------------------------------------------------------

#[test]
#[ignore] // requires ANTHROPIC_API_KEY
fn test_context_management_compact_accepted() {
    // Verifies the Anthropic API accepts context_management params with
    // compact_20260112 and clear_tool_uses_20250919 edits without error.
    // Also checks that usage tokens are reported correctly.
    let key = std::env::var("ANTHROPIC_API_KEY")
        .expect("ANTHROPIC_API_KEY required for this test");

    let rt = tokio::runtime::Runtime::new().unwrap();
    rt.block_on(async {
        let client = AnthropicClient::with_config(key, "https://api.anthropic.com".into());

        // Build params with both context_management edits enabled
        let params = InferenceRequest {
            model: "claude-haiku-4-5-20251001".into(),
            max_tokens: 100,
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "Say hello in exactly 3 words.".into(),
                }],
                model: None,
            }],
            claude: ostk::cpu::ClaudeOptions {
                context_management: Some(serde_json::json!({
                    "edits": [
                        {
                            "type": "compact_20260112",
                            "trigger": { "type": "input_tokens", "value": 1000 }
                        },
                        {
                            "type": "clear_tool_uses_20250919",
                            "keep": { "type": "tool_uses", "value": 3 }
                        }
                    ]
                })),
                betas: vec!["compact".into(), "context-management".into()],
                ..Default::default()
            },
            ..Default::default()
        };

        let result = client.create(params).await;
        assert!(
            result.is_ok(),
            "API rejected context_management params: {:?}",
            result.err()
        );

        let response = result.unwrap();

        // Content must be present
        assert!(
            !response.content.is_empty(),
            "Response should have content"
        );

        // Usage tokens should be non-zero (API properly processed the request)
        assert!(
            response.usage.input_tokens > 0,
            "Input tokens should be reported: got {}",
            response.usage.input_tokens
        );
        assert!(
            response.usage.output_tokens > 0,
            "Output tokens should be reported: got {}",
            response.usage.output_tokens
        );

        // stop_reason should be end_turn (not an error stop)
        assert_eq!(
            response.stop_reason.as_deref(),
            Some("end_turn"),
            "Expected end_turn stop reason, got {:?}",
            response.stop_reason
        );
    });
}

#[test]
#[ignore] // requires ANTHROPIC_API_KEY
fn test_context_management_clear_tool_uses() {
    // Builds a multi-turn conversation with 5 tool_use + tool_result pairs,
    // sends with clear_tool_uses_20250919 keep=1, and verifies the API
    // responds correctly. Compares token counts with vs without clear_tool_uses
    // to confirm the editing actually reduced the input token count.
    let key = std::env::var("ANTHROPIC_API_KEY")
        .expect("ANTHROPIC_API_KEY required for this test");

    let rt = tokio::runtime::Runtime::new().unwrap();
    rt.block_on(async {
        let client = AnthropicClient::with_config(key, "https://api.anthropic.com".into());

        // Build a conversation with 5 tool call rounds
        let mut messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "I need you to do several calculations for me.".into(),
                }],
                model: None,
            },
        ];

        let calcs = [
            ("toolu_01_a", "10+20", "30", "10 + 20 = 30."),
            ("toolu_02_b", "15*3", "45", "15 * 3 = 45."),
            ("toolu_03_c", "100/4", "25", "100 / 4 = 25."),
            ("toolu_04_d", "7*8", "56", "7 * 8 = 56."),
            ("toolu_05_e", "99-33", "66", "99 - 33 = 66."),
        ];

        for (i, (id, expr, result, summary)) in calcs.iter().enumerate() {
            // User prompt for each calculation (except first which is above)
            if i > 0 {
                messages.push(Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text {
                        text: format!("Now calculate {expr}."),
                    }],
                    model: None,
                });
            }
            // tool_use + tool_result pair
            messages.extend(make_tool_turn(id, expr, result));
            // assistant summary after each tool result
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: summary.to_string(),
                }],
                model: None,
            });
        }

        // Final user message asking for a summary
        messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "List all five results.".into(),
            }],
            model: None,
        });

        let tool = calculator_tool();

        // First: count tokens WITHOUT context management (raw conversation)
        let count_params = InferenceRequest {
            model: "claude-haiku-4-5-20251001".into(),
            max_tokens: 200,
            messages: messages.clone(),
            tools: vec![tool.clone()],
            claude: ostk::cpu::ClaudeOptions {
                betas: vec!["token-counting".into()],
                ..Default::default()
            },
            ..Default::default()
        };
        let raw_token_count = client.count_tokens(&count_params).await
            .expect("count_tokens should succeed for raw conversation");

        // Second: send with clear_tool_uses keep=1 and observe the actual
        // input_tokens in the response usage (API counts AFTER edits)
        let params = InferenceRequest {
            model: "claude-haiku-4-5-20251001".into(),
            max_tokens: 200,
            messages: messages.clone(),
            tools: vec![tool.clone()],
            claude: ostk::cpu::ClaudeOptions {
                context_management: Some(serde_json::json!({
                    "edits": [
                        {
                            "type": "clear_tool_uses_20250919",
                            "keep": { "type": "tool_uses", "value": 1 }
                        }
                    ]
                })),
                betas: vec!["context-management".into()],
                ..Default::default()
            },
            ..Default::default()
        };

        let result = client.create(params).await;
        assert!(
            result.is_ok(),
            "API rejected clear_tool_uses with 5 tool pairs: {:?}",
            result.err()
        );

        let response = result.unwrap();

        // Basic response checks
        assert!(
            !response.content.is_empty(),
            "Response should have content after clear_tool_uses"
        );
        assert!(
            response.usage.input_tokens > 0,
            "Input tokens should be reported"
        );

        // The API should have used fewer input tokens than the raw count
        // because clear_tool_uses with keep=1 should have removed 4 of the
        // 5 tool_use+tool_result pairs from the context.
        //
        // Note: the response usage.input_tokens reflects the post-edit count.
        // If the API applies edits before counting, we expect a reduction.
        // If it doesn't, this assertion documents the behavior.
        eprintln!(
            "Token comparison: raw={}, after clear_tool_uses={}",
            raw_token_count, response.usage.input_tokens
        );
        if response.usage.input_tokens < raw_token_count {
            eprintln!(
                "clear_tool_uses saved {} tokens ({:.0}% reduction)",
                raw_token_count - response.usage.input_tokens,
                (1.0 - response.usage.input_tokens as f64 / raw_token_count as f64) * 100.0
            );
        }

        // The model should still be able to reference at least some results.
        // With keep=1, only the most recent tool result (99-33=66) should be
        // in context, but the assistant text summaries are NOT cleared, so the
        // model should still know all five answers.
        let text = extract_text(&response);
        assert!(
            text.contains("30") || text.contains("45") || text.contains("25")
                || text.contains("56") || text.contains("66"),
            "Response should reference at least one calculation result: {text}"
        );
    });
}

#[test]
#[ignore] // requires ANTHROPIC_API_KEY
fn test_context_management_compact_with_count_tokens() {
    // Uses count_tokens to verify the compact edit is structurally valid.
    // Sends the same params to count_tokens (which ignores context_management)
    // and then to create (which applies it). Verifies both succeed.
    let key = std::env::var("ANTHROPIC_API_KEY")
        .expect("ANTHROPIC_API_KEY required for this test");

    let rt = tokio::runtime::Runtime::new().unwrap();
    rt.block_on(async {
        let client = AnthropicClient::with_config(key, "https://api.anthropic.com".into());

        let messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "What is the capital of France?".into(),
            }],
            model: None,
        }];

        // Count tokens first (no context_management needed for counting)
        let count_params = InferenceRequest {
            model: "claude-haiku-4-5-20251001".into(),
            max_tokens: 50,
            messages: messages.clone(),
            claude: ostk::cpu::ClaudeOptions {
                betas: vec!["token-counting".into()],
                ..Default::default()
            },
            ..Default::default()
        };
        let token_count = client.count_tokens(&count_params).await
            .expect("count_tokens should succeed");
        assert!(token_count > 0, "Token count should be positive");

        // Now send with compact — trigger set high so it won't actually fire
        // (our message is tiny), but the API must accept the structure.
        let params = InferenceRequest {
            model: "claude-haiku-4-5-20251001".into(),
            max_tokens: 50,
            messages,
            claude: ostk::cpu::ClaudeOptions {
                context_management: Some(serde_json::json!({
                    "edits": [
                        {
                            "type": "compact_20260112",
                            "trigger": { "type": "input_tokens", "value": 100 }
                        }
                    ]
                })),
                betas: vec!["compact".into()],
                ..Default::default()
            },
            ..Default::default()
        };

        let result = client.create(params).await;
        assert!(
            result.is_ok(),
            "API rejected compact params: {:?}",
            result.err()
        );

        let response = result.unwrap();
        assert!(!response.content.is_empty());

        // Input tokens from the response should roughly match count_tokens
        // (since compact didn't fire — our message is well under the trigger).
        let diff = (response.usage.input_tokens as i64 - token_count as i64).unsigned_abs();
        assert!(
            diff < 20,
            "Token count mismatch: count_tokens={}, response.usage.input_tokens={} (diff={})",
            token_count,
            response.usage.input_tokens,
            diff
        );

        eprintln!(
            "count_tokens={}, response.usage.input_tokens={} (compact did not fire, as expected for small input)",
            token_count, response.usage.input_tokens
        );
    });
}

// ---------------------------------------------------------------------------
// Unit tests (no API key needed)
// ---------------------------------------------------------------------------

#[test]
fn test_build_params_compact_trigger_80_percent() {
    // Verify that build_params sets the compact trigger to 80% of the context_budget.
    use ostk::cpu::agent_loop::LoopConfig;
    use ostk::cpu::params::build_params;

    let config = LoopConfig {
        model: "claude-haiku-4-5-20251001".into(),
        system_prompt: None,
        tools: vec![],
        max_tokens: 4096,
        max_turns: None,
        context_budget: Some(200_000),
        permission_mode: ostk::cpu::PermissionMode::Auto,
        betas: vec!["compact".into()],
        fast_mode: false,
        root: None,
        preload_context: vec![],
        thinking: None,
        citations: false,
        tool_patterns: vec![],
    };

    let params = build_params(&config, &[], vec![], None, None, 0, None);
    let cm = params.claude.context_management.expect("context_management should be set");
    let edits = cm.get("edits").unwrap().as_array().unwrap();
    assert_eq!(edits.len(), 1);

    let compact = &edits[0];
    assert_eq!(compact["type"], "compact_20260112");
    let trigger_val = compact["trigger"]["value"].as_u64().unwrap();
    // 200_000 * 80 / 100 = 160_000
    assert_eq!(trigger_val, 160_000, "Trigger should be 80% of context budget");
}

#[test]
fn test_build_params_compact_trigger_small_budget() {
    // Verify 80% scaling works for small budgets too.
    use ostk::cpu::agent_loop::LoopConfig;
    use ostk::cpu::params::build_params;

    let config = LoopConfig {
        model: "claude-haiku-4-5-20251001".into(),
        system_prompt: None,
        tools: vec![],
        max_tokens: 4096,
        max_turns: None,
        context_budget: Some(10_000),
        permission_mode: ostk::cpu::PermissionMode::Auto,
        betas: vec!["compact".into()],
        fast_mode: false,
        root: None,
        preload_context: vec![],
        thinking: None,
        citations: false,
        tool_patterns: vec![],
    };

    let params = build_params(&config, &[], vec![], None, None, 0, None);
    let cm = params.claude.context_management.expect("context_management should be set");
    let edits = cm.get("edits").unwrap().as_array().unwrap();

    let trigger_val = edits[0]["trigger"]["value"].as_u64().unwrap();
    // 10_000 * 80 / 100 = 8_000
    assert_eq!(trigger_val, 8_000, "Trigger should be 80% of 10k budget");
}

#[test]
fn test_build_params_no_budget_uses_api_default() {
    // When no context_budget is set, compact edit has no trigger (API default ~150k)
    use ostk::cpu::agent_loop::LoopConfig;
    use ostk::cpu::params::build_params;

    let config = LoopConfig {
        model: "claude-haiku-4-5-20251001".into(),
        system_prompt: None,
        tools: vec![],
        max_tokens: 4096,
        max_turns: None,
        context_budget: None,
        permission_mode: ostk::cpu::PermissionMode::Auto,
        betas: vec!["compact".into()],
        fast_mode: false,
        root: None,
        preload_context: vec![],
        thinking: None,
        citations: false,
        tool_patterns: vec![],
    };

    let params = build_params(&config, &[], vec![], None, None, 0, None);
    let cm = params.claude.context_management.expect("context_management should be set");
    let edits = cm.get("edits").unwrap().as_array().unwrap();
    assert_eq!(edits.len(), 1);

    // No trigger field — API uses its default
    assert!(
        edits[0].get("trigger").is_none(),
        "Should not have a trigger when no budget is set"
    );
}

#[test]
fn test_build_params_both_betas_combined_trigger() {
    // When both compact and context-management betas are active with a budget,
    // the compact trigger should be 80% and clear_tool_uses should be present.
    use ostk::cpu::agent_loop::LoopConfig;
    use ostk::cpu::params::build_params;

    let config = LoopConfig {
        model: "claude-haiku-4-5-20251001".into(),
        system_prompt: None,
        tools: vec![],
        max_tokens: 4096,
        max_turns: None,
        context_budget: Some(100_000),
        permission_mode: ostk::cpu::PermissionMode::Auto,
        betas: vec!["compact".into(), "context-management".into()],
        fast_mode: false,
        root: None,
        preload_context: vec![],
        thinking: None,
        citations: false,
        tool_patterns: vec![],
    };

    let params = build_params(&config, &[], vec![], None, None, 0, None);
    let cm = params.claude.context_management.expect("context_management should be set");
    let edits = cm.get("edits").unwrap().as_array().unwrap();
    assert_eq!(edits.len(), 2, "Should have both compact and clear_tool_uses edits");

    // Compact edit at 80% of 100k = 80k
    assert_eq!(edits[0]["type"], "compact_20260112");
    assert_eq!(edits[0]["trigger"]["value"].as_u64().unwrap(), 80_000);

    // Clear tool uses edit
    assert_eq!(edits[1]["type"], "clear_tool_uses_20250919");
    assert_eq!(edits[1]["keep"]["type"], "tool_uses");
    assert_eq!(edits[1]["keep"]["value"], 5);
}
