use serde_json::Value;
use std::time::Duration;

use crate::cpu::anthropic::{
    ApiResponse, ContentBlock, InferenceRequest, Message, StreamEvent, Usage,
};

// ---------------------------------------------------------------------------
// OpenRouterClient — OpenAI-compatible API via OpenRouter
// ---------------------------------------------------------------------------

pub struct OpenRouterClient {
    client: reqwest::Client,
    api_key: String,
    base_url: String,
}

impl OpenRouterClient {
    /// Create a new client. Resolves API key through kernel secret management.
    pub fn new() -> Result<Self, crate::cpu::error::DriverError> {
        let api_key = crate::commands::secret::resolve_secret("OPENROUTER_API_KEY")
            .map_err(|_| crate::cpu::error::DriverError::MissingApiKey {
                provider: "openrouter".into(),
                key_name: "OPENROUTER_API_KEY".into(),
            })?;
        Ok(Self {
            client: reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(120))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new()),
            api_key,
            base_url: "https://openrouter.ai/api/v1".to_string(),
        })
    }

    /// Create a client with an explicit key and base URL.
    /// When base_url is not OpenRouter, provider-specific headers (HTTP-Referer, X-Title) are skipped.
    pub fn with_config(api_key: String, base_url: String) -> Self {
        Self {
            client: reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(120))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new()),
            api_key,
            base_url,
        }
    }

    // -- message translation ------------------------------------------------

    /// Translate our internal Message format to OpenAI chat format.
    fn translate_messages(messages: &[Message]) -> Vec<Value> {
        let mut out: Vec<Value> = Vec::new();
        for msg in messages {
            translate_message_to_openai(msg, &mut out);
        }
        out
    }

    /// Translate our tool schemas (Anthropic format) to OpenAI function-calling format.
    /// Delegates to shared `translate_tools_openai` (→850).
    fn translate_tools(tools: &[Value]) -> Vec<Value> {
        super::translate_tools_openai(tools)
    }

    /// Build the OpenAI-format request body.
    fn build_body(&self, params: &InferenceRequest) -> Value {
        // Strip provider prefix from model name for OpenRouter routing.
        // e.g. "openrouter/anthropic/claude-sonnet-4-5" -> "anthropic/claude-sonnet-4-5"
        let model = strip_openrouter_prefix(&params.model);

        let messages = Self::translate_messages(&params.messages);

        // Prepend system message if present.
        let mut all_messages = Vec::new();
        if let Some(sys) = &params.system {
            all_messages.push(serde_json::json!({
                "role": "system",
                "content": sys
            }));
        }
        all_messages.extend(messages);

        let mut body = serde_json::json!({
            "model": model,
            "max_tokens": params.max_tokens,
            "messages": all_messages,
        });

        let tools = Self::translate_tools(&params.tools);
        if !tools.is_empty() {
            body["tools"] = Value::Array(tools);
        }

        if params.stream {
            body["stream"] = Value::Bool(true);
        }

        body
    }

    // -- non-streaming ------------------------------------------------------

    pub async fn create(&self, params: InferenceRequest) -> Result<ApiResponse, String> {
        let body = self.build_body(&params);

        let resp = self
            .client
            .post(format!("{}/chat/completions", self.base_url))
            .header("Authorization", format!("Bearer {}", self.api_key))
            .header("HTTP-Referer", "https://ostk.ai")
            .header("X-Title", "ostk")
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("request failed: {e}"))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(format!("API error {status}: {msg}"));
        }

        let raw: Value = resp
            .json()
            .await
            .map_err(|e| format!("json parse: {e}"))?;
        parse_response(&raw)
    }

    // -- streaming ----------------------------------------------------------

    pub async fn create_stream(
        &self,
        params: InferenceRequest,
    ) -> Result<impl futures_util::Stream<Item = Result<StreamEvent, String>>, crate::cpu::error::DriverError> {
        let mut p = params;
        p.stream = true;
        let body = self.build_body(&p);

        let resp = self
            .client
            .post(format!("{}/chat/completions", self.base_url))
            .header("Authorization", format!("Bearer {}", self.api_key))
            .header("HTTP-Referer", "https://ostk.ai")
            .header("X-Title", "ostk")
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await
            .map_err(|e| crate::cpu::error::DriverError::StreamError(format!("request failed: {e}")))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(crate::cpu::error::DriverError::ApiError {
                status: status.as_u16(),
                body: msg,
            });
        }

        let byte_stream = resp.bytes_stream();

        // Shared state for the parser and on_done closures.
        let tool_calls = std::sync::Arc::new(std::sync::Mutex::new(Vec::<(String, String, String)>::new()));
        let usage = std::sync::Arc::new(std::sync::Mutex::new(Usage::default()));

        let tc = tool_calls.clone();
        let u = usage.clone();
        let parser = move |json_str: &str| -> Option<Vec<Result<StreamEvent, String>>> {
            let mut tc = tc.lock().unwrap();
            let mut u = u.lock().unwrap();
            parse_sse_chunk(json_str, &mut tc, &mut u)
        };

        let tc2 = tool_calls;
        let u2 = usage;
        let on_done = move || -> Vec<Result<StreamEvent, String>> {
            let tc = tc2.lock().unwrap();
            let u = u2.lock().unwrap();
            vec![Ok(StreamEvent::MessageStop {
                stop_reason: if tc.is_empty() {
                    "end_turn".to_string()
                } else {
                    "tool_use".to_string()
                },
                usage: u.clone(),
            })]
        };

        Ok(super::sse::buffered_sse_unfold(byte_stream, parser, on_done))
    }

    // -- token counting (stub) -----------------------------------------------

    /// OpenRouter does not have a native count_tokens endpoint.
    /// Returns an estimate based on rough character-to-token ratio.
    pub async fn count_tokens(&self, _params: &InferenceRequest) -> Result<u64, crate::cpu::error::DriverError> {
        // OpenRouter doesn't expose a token counting endpoint.
        // Return an error so the caller knows to skip pre-call counting.
        Err(crate::cpu::error::DriverError::StreamError("count_tokens not available for OpenRouter".into()))
    }
}

// ---------------------------------------------------------------------------
// Message translation helpers
// ---------------------------------------------------------------------------

/// Translate a single internal Message to one or more OpenAI-format messages.
///
/// The Anthropic format stores tool results as ContentBlocks within a user
/// message. The OpenAI format requires separate `tool` role messages for each
/// result and `tool_calls` on assistant messages.
pub(crate) fn translate_message_to_openai(msg: &Message, out: &mut Vec<Value>) {
    match msg.role.as_str() {
        "user" => {
            // →872: Partition content blocks by type. Previous code checked
            // `all_tool_results` and silently dropped ToolResult blocks when
            // mixed with Text. Now we always emit tool results correctly.
            let tool_results: Vec<&ContentBlock> = msg
                .content
                .iter()
                .filter(|b| matches!(b, ContentBlock::ToolResult { .. }))
                .collect();
            let other_blocks: Vec<&ContentBlock> = msg
                .content
                .iter()
                .filter(|b| !matches!(b, ContentBlock::ToolResult { .. }))
                .collect();

            // Emit tool results as separate "tool" role messages
            for block in &tool_results {
                if let ContentBlock::ToolResult {
                    tool_use_id,
                    content,
                    ..
                } = block
                {
                    out.push(serde_json::json!({
                        "role": "tool",
                        "tool_call_id": tool_use_id,
                        "content": content
                    }));
                }
            }

            // Emit remaining blocks as user message (if any)
            if !other_blocks.is_empty() {
                let has_images = other_blocks
                    .iter()
                    .any(|b| matches!(b, ContentBlock::Image { .. }));

                if has_images {
                    // Array content: text + image_url blocks
                    let parts: Vec<serde_json::Value> = other_blocks
                        .iter()
                        .filter_map(|b| match b {
                            ContentBlock::Text { text } => Some(serde_json::json!({
                                "type": "text",
                                "text": text
                            })),
                            ContentBlock::Image { source } => Some(serde_json::json!({
                                "type": "image_url",
                                "image_url": {
                                    "url": format!("data:{};base64,{}", source.media_type, source.data)
                                }
                            })),
                            _ => None,
                        })
                        .collect();
                    out.push(serde_json::json!({
                        "role": "user",
                        "content": parts
                    }));
                } else {
                    let text = other_blocks
                        .iter()
                        .filter_map(|b| match b {
                            ContentBlock::Text { text } => Some(text.as_str()),
                            _ => None,
                        })
                        .collect::<Vec<_>>()
                        .join("\n");
                    if !text.is_empty() {
                        out.push(serde_json::json!({
                            "role": "user",
                            "content": text
                        }));
                    }
                }
            }
        }
        "assistant" => {
            // Separate text content from tool_calls.
            let text_parts: Vec<&str> = msg
                .content
                .iter()
                .filter_map(|b| match b {
                    ContentBlock::Text { text } => Some(text.as_str()),
                    _ => None,
                })
                .collect();

            let tool_calls: Vec<Value> = msg
                .content
                .iter()
                .filter_map(|b| match b {
                    ContentBlock::ToolUse { id, name, input } => {
                        Some(serde_json::json!({
                            "id": id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": serde_json::to_string(input).unwrap_or_default()
                            }
                        }))
                    }
                    _ => None,
                })
                .collect();

            let mut assistant_msg = serde_json::json!({
                "role": "assistant"
            });

            // Content: text or null.
            if !text_parts.is_empty() {
                assistant_msg["content"] = Value::String(text_parts.join("\n"));
            } else {
                assistant_msg["content"] = Value::Null;
            }

            if !tool_calls.is_empty() {
                assistant_msg["tool_calls"] = Value::Array(tool_calls);
            }

            out.push(assistant_msg);
        }
        _ => {
            // Pass through unknown roles (shouldn't happen in practice).
            let text = msg
                .content
                .iter()
                .filter_map(|b| match b {
                    ContentBlock::Text { text } => Some(text.as_str()),
                    _ => None,
                })
                .collect::<Vec<_>>()
                .join("\n");
            out.push(serde_json::json!({
                "role": msg.role,
                "content": text
            }));
        }
    }
}

// ---------------------------------------------------------------------------
// Model name helpers
// ---------------------------------------------------------------------------

/// Strip provider prefixes from model names.
/// e.g. "openrouter/anthropic/claude-sonnet-4-5" -> "anthropic/claude-sonnet-4-5"
/// e.g. "local/codestral:22b" -> "codestral:22b"
/// e.g. "ollama/codestral:22b" -> "codestral:22b"
fn strip_openrouter_prefix(model: &str) -> &str {
    model.strip_prefix("openrouter/")
        .or_else(|| model.strip_prefix("local/"))
        .or_else(|| model.strip_prefix("ollama/"))
        .unwrap_or(model)
}

// ---------------------------------------------------------------------------
// Response parsing (OpenAI format -> our types)
// ---------------------------------------------------------------------------

/// Parse a non-streaming OpenAI chat completion response.
fn parse_response(raw: &Value) -> Result<ApiResponse, String> {
    let choices = raw
        .get("choices")
        .and_then(|v| v.as_array())
        .ok_or("missing choices array")?;

    if choices.is_empty() {
        return Ok(ApiResponse {
            content: vec![],
            stop_reason: Some("end_turn".to_string()),
            usage: parse_usage(raw),
            applied_edits: vec![],
        });
    }

    let choice = &choices[0];
    let message = choice.get("message").ok_or("missing message in choice")?;
    let finish_reason = choice
        .get("finish_reason")
        .and_then(|v| v.as_str())
        .unwrap_or("stop");

    let mut content_blocks: Vec<ContentBlock> = Vec::new();

    // Text content.
    if let Some(text) = message.get("content").and_then(|v| v.as_str())
        && !text.is_empty() {
            content_blocks.push(ContentBlock::Text {
                text: text.to_string(),
            });
        }

    // Tool calls.
    if let Some(tool_calls) = message.get("tool_calls").and_then(|v| v.as_array()) {
        for tc in tool_calls {
            if let Some(block) = parse_tool_call(tc) {
                content_blocks.push(block);
            }
        }
    }

    let stop_reason = translate_finish_reason(finish_reason);

    Ok(ApiResponse {
        content: content_blocks,
        stop_reason: Some(stop_reason),
        usage: parse_usage(raw),
        applied_edits: vec![],
    })
}

/// Parse a tool_call object from the OpenAI response into our ContentBlock::ToolUse.
fn parse_tool_call(tc: &Value) -> Option<ContentBlock> {
    let id = tc.get("id")?.as_str()?.to_string();
    let function = tc.get("function")?;
    let name = function.get("name")?.as_str()?.to_string();
    let arguments_str = function.get("arguments").and_then(|v| v.as_str()).unwrap_or("{}");
    let input: Value = serde_json::from_str(arguments_str).unwrap_or(Value::Null);
    Some(ContentBlock::ToolUse { id, name, input })
}

/// Map OpenAI finish_reason to Anthropic stop_reason.
fn translate_finish_reason(reason: &str) -> String {
    match reason {
        "stop" => "end_turn".to_string(),
        "length" => "max_tokens".to_string(),
        "tool_calls" => "tool_use".to_string(),
        "content_filter" => "refusal".to_string(),
        other => other.to_string(),
    }
}

/// Parse usage from an OpenAI response.
fn parse_usage(raw: &Value) -> Usage {
    raw.get("usage")
        .map(|u| Usage {
            input_tokens: u
                .get("prompt_tokens")
                .and_then(|v| v.as_u64())
                .unwrap_or(0),
            output_tokens: u
                .get("completion_tokens")
                .and_then(|v| v.as_u64())
                .unwrap_or(0),
            cost_usd: u
                .get("cost")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0),
            ..Default::default()
        })
        .unwrap_or_default()
}

// ---------------------------------------------------------------------------
// Streaming SSE parsing (OpenAI format)
// ---------------------------------------------------------------------------

/// Parse a single SSE chunk from an OpenAI streaming response.
///
/// Returns a list of StreamEvents to emit (usually 0 or 1).
/// Mutates the tool_calls accumulator to track partial tool call deltas.
pub(crate) fn parse_sse_chunk(
    json_str: &str,
    tool_calls: &mut Vec<(String, String, String)>,
    usage: &mut Usage,
) -> Option<Vec<Result<StreamEvent, String>>> {
    let v: Value = match serde_json::from_str(json_str) {
        Ok(v) => v,
        Err(e) => return Some(vec![Err(format!("SSE json parse: {e}"))]),
    };

    // Extract usage if present (some providers send it in the final chunk).
    if let Some(u) = v.get("usage") {
        if let Some(pt) = u.get("prompt_tokens").and_then(|v| v.as_u64()) {
            usage.input_tokens = pt;
        }
        if let Some(ct) = u.get("completion_tokens").and_then(|v| v.as_u64()) {
            usage.output_tokens = ct;
        }
        // OpenRouter returns real cost in usage.cost (float, USD)
        if let Some(cost) = u.get("cost").and_then(|v| v.as_f64()) {
            usage.cost_usd = cost;
        }
    }

    let choices = v.get("choices")?.as_array()?;
    if choices.is_empty() {
        return None;
    }

    let choice = &choices[0];
    let delta = choice.get("delta")?;
    let finish_reason = choice.get("finish_reason").and_then(|v| v.as_str());

    let mut events: Vec<Result<StreamEvent, String>> = Vec::new();

    // Text delta.
    if let Some(content) = delta.get("content").and_then(|v| v.as_str())
        && !content.is_empty() {
            events.push(Ok(StreamEvent::TextDelta(content.to_string())));
        }

    // Tool call deltas.
    if let Some(tc_deltas) = delta.get("tool_calls").and_then(|v| v.as_array()) {
        for tc_delta in tc_deltas {
            let index = tc_delta
                .get("index")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as usize;

            // First chunk for this tool call includes id and function name.
            if let Some(id) = tc_delta.get("id").and_then(|v| v.as_str()) {
                let name = tc_delta
                    .get("function")
                    .and_then(|f| f.get("name"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();

                // Ensure the vector is large enough.
                while tool_calls.len() <= index {
                    tool_calls.push((String::new(), String::new(), String::new()));
                }
                tool_calls[index] = (id.to_string(), name.clone(), String::new());

                events.push(Ok(StreamEvent::ToolUseStart {
                    id: id.to_string(),
                    name,
                }));
            }

            // Argument fragment.
            if let Some(args_fragment) = tc_delta
                .get("function")
                .and_then(|f| f.get("arguments"))
                .and_then(|v| v.as_str())
                && !args_fragment.is_empty() {
                    // Ensure the vector is large enough.
                    while tool_calls.len() <= index {
                        tool_calls.push((String::new(), String::new(), String::new()));
                    }
                    tool_calls[index].2.push_str(args_fragment);
                    events.push(Ok(StreamEvent::ToolInputDelta(
                        args_fragment.to_string(),
                    )));
                }
        }
    }

    // Finish reason — emit ContentBlockStop for each pending tool call,
    // then (optionally) a MessageStop. We handle this here rather than
    // on [DONE] because the finish_reason arrives before [DONE].
    if let Some(reason) = finish_reason
        && (reason == "tool_calls" || reason == "stop" || reason == "length") {
            // Emit ContentBlockStop for each accumulated tool call.
            for _ in 0..tool_calls.len() {
                events.push(Ok(StreamEvent::ContentBlockStop));
            }
            // Also emit ContentBlockStop for any pending text.
            if reason == "stop" || reason == "length" {
                events.push(Ok(StreamEvent::ContentBlockStop));
            }
        }

    if events.is_empty() {
        None
    } else {
        Some(events)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cpu::anthropic::{ContentBlock, Message};

    // -----------------------------------------------------------------------
    // Model name handling
    // -----------------------------------------------------------------------

    #[test]
    fn strip_openrouter_prefix_removes_prefix() {
        assert_eq!(
            strip_openrouter_prefix("openrouter/anthropic/claude-sonnet-4-5"),
            "anthropic/claude-sonnet-4-5"
        );
    }

    #[test]
    fn strip_openrouter_prefix_no_prefix() {
        assert_eq!(
            strip_openrouter_prefix("anthropic/claude-sonnet-4-5"),
            "anthropic/claude-sonnet-4-5"
        );
    }

    #[test]
    fn strip_openrouter_prefix_various_providers() {
        assert_eq!(
            strip_openrouter_prefix("openrouter/google/gemini-2.5-pro"),
            "google/gemini-2.5-pro"
        );
        assert_eq!(
            strip_openrouter_prefix("openrouter/openai/gpt-4o"),
            "openai/gpt-4o"
        );
        assert_eq!(
            strip_openrouter_prefix("openrouter/meta-llama/llama-3.1-405b"),
            "meta-llama/llama-3.1-405b"
        );
    }

    // -----------------------------------------------------------------------
    // Message format translation
    // -----------------------------------------------------------------------

    #[test]
    fn translate_user_text_message() {
        let messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "Hello, world!".into(),
            }],
            model: None,
        }];
        let translated = OpenRouterClient::translate_messages(&messages);
        assert_eq!(translated.len(), 1);
        assert_eq!(translated[0]["role"], "user");
        assert_eq!(translated[0]["content"], "Hello, world!");
    }

    #[test]
    fn translate_assistant_text_message() {
        let messages = vec![Message {
            role: "assistant".into(),
            content: vec![ContentBlock::Text {
                text: "I can help with that.".into(),
            }],
            model: None,
        }];
        let translated = OpenRouterClient::translate_messages(&messages);
        assert_eq!(translated.len(), 1);
        assert_eq!(translated[0]["role"], "assistant");
        assert_eq!(translated[0]["content"], "I can help with that.");
    }

    #[test]
    fn translate_assistant_with_tool_calls() {
        let messages = vec![Message {
            role: "assistant".into(),
            content: vec![
                ContentBlock::Text {
                    text: "I'll read the file.".into(),
                },
                ContentBlock::ToolUse {
                    id: "call_abc".into(),
                    name: "Read".into(),
                    input: serde_json::json!({"file_path": "/tmp/test.rs"}),
                },
            ],
            model: None,
        }];
        let translated = OpenRouterClient::translate_messages(&messages);
        assert_eq!(translated.len(), 1);
        assert_eq!(translated[0]["role"], "assistant");
        assert_eq!(translated[0]["content"], "I'll read the file.");

        let tool_calls = translated[0]["tool_calls"].as_array().unwrap();
        assert_eq!(tool_calls.len(), 1);
        assert_eq!(tool_calls[0]["id"], "call_abc");
        assert_eq!(tool_calls[0]["type"], "function");
        assert_eq!(tool_calls[0]["function"]["name"], "Read");

        // Arguments should be a JSON string.
        let args_str = tool_calls[0]["function"]["arguments"].as_str().unwrap();
        let args: Value = serde_json::from_str(args_str).unwrap();
        assert_eq!(args["file_path"], "/tmp/test.rs");
    }

    #[test]
    fn translate_tool_result_messages() {
        let messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::ToolResult {
                tool_use_id: "call_abc".into(),
                content: "file contents here".into(),
                is_error: false,
            }],
            model: None,
        }];
        let translated = OpenRouterClient::translate_messages(&messages);
        assert_eq!(translated.len(), 1);
        assert_eq!(translated[0]["role"], "tool");
        assert_eq!(translated[0]["tool_call_id"], "call_abc");
        assert_eq!(translated[0]["content"], "file contents here");
    }

    #[test]
    fn translate_multiple_tool_results() {
        let messages = vec![Message {
            role: "user".into(),
            content: vec![
                ContentBlock::ToolResult {
                    tool_use_id: "call_1".into(),
                    content: "result 1".into(),
                    is_error: false,
                },
                ContentBlock::ToolResult {
                    tool_use_id: "call_2".into(),
                    content: "result 2".into(),
                    is_error: false,
                },
            ],
            model: None,
        }];
        let translated = OpenRouterClient::translate_messages(&messages);
        assert_eq!(translated.len(), 2);
        assert_eq!(translated[0]["role"], "tool");
        assert_eq!(translated[0]["tool_call_id"], "call_1");
        assert_eq!(translated[1]["role"], "tool");
        assert_eq!(translated[1]["tool_call_id"], "call_2");
    }

    #[test]
    fn translate_full_conversation() {
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "Read /tmp/foo.txt".into(),
                }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![
                    ContentBlock::Text {
                        text: "I'll read that.".into(),
                    },
                    ContentBlock::ToolUse {
                        id: "call_1".into(),
                        name: "Read".into(),
                        input: serde_json::json!({"file_path": "/tmp/foo.txt"}),
                    },
                ],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "call_1".into(),
                    content: "hello world".into(),
                    is_error: false,
                }],
                model: None,
            },
        ];
        let translated = OpenRouterClient::translate_messages(&messages);
        assert_eq!(translated.len(), 3);
        assert_eq!(translated[0]["role"], "user");
        assert_eq!(translated[1]["role"], "assistant");
        assert!(translated[1].get("tool_calls").is_some());
        assert_eq!(translated[2]["role"], "tool");
    }

    // -----------------------------------------------------------------------
    // Tool schema translation — tests moved to cpu/mod.rs (→850)
    // Smoke test: delegation still works through the OpenRouterClient wrapper.
    // -----------------------------------------------------------------------

    #[test]
    fn translate_tools_delegates_to_shared() {
        let tools = vec![serde_json::json!({
            "name": "Bash",
            "description": "Run a command",
            "input_schema": {
                "type": "object",
                "properties": { "command": {"type": "string"} },
                "required": ["command"]
            }
        })];
        let result = OpenRouterClient::translate_tools(&tools);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0]["type"], "function");
        assert_eq!(result[0]["function"]["name"], "Bash");
    }

    // -----------------------------------------------------------------------
    // Response parsing (OpenAI -> our types)
    // -----------------------------------------------------------------------

    #[test]
    fn parse_text_response() {
        let raw = serde_json::json!({
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help?"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "total_tokens": 18
            }
        });

        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.content.len(), 1);
        match &resp.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "Hello! How can I help?"),
            _ => panic!("expected text block"),
        }
        assert_eq!(resp.stop_reason.as_deref(), Some("end_turn"));
        assert_eq!(resp.usage.input_tokens, 10);
        assert_eq!(resp.usage.output_tokens, 8);
    }

    #[test]
    fn parse_tool_call_response() {
        let raw = serde_json::json!({
            "id": "chatcmpl-456",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "I'll run that command.",
                    "tool_calls": [{
                        "id": "call_xyz",
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "arguments": "{\"command\": \"ls -la\"}"
                        }
                    }]
                },
                "finish_reason": "tool_calls"
            }],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 30,
                "total_tokens": 80
            }
        });

        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.content.len(), 2); // text + tool_use
        assert_eq!(resp.stop_reason.as_deref(), Some("tool_use"));
        assert_eq!(resp.usage.input_tokens, 50);
        assert_eq!(resp.usage.output_tokens, 30);

        match &resp.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "I'll run that command."),
            _ => panic!("expected text block"),
        }
        match &resp.content[1] {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "call_xyz");
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls -la");
            }
            _ => panic!("expected tool_use block"),
        }
    }

    #[test]
    fn parse_response_no_text_only_tool_calls() {
        let raw = serde_json::json!({
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": null,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "Read",
                            "arguments": "{\"file_path\": \"/tmp/x\"}"
                        }
                    }]
                },
                "finish_reason": "tool_calls"
            }],
            "usage": { "prompt_tokens": 20, "completion_tokens": 10 }
        });

        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.content.len(), 1);
        match &resp.content[0] {
            ContentBlock::ToolUse { name, .. } => assert_eq!(name, "Read"),
            _ => panic!("expected tool_use"),
        }
    }

    #[test]
    fn parse_response_empty_choices() {
        let raw = serde_json::json!({
            "choices": [],
            "usage": { "prompt_tokens": 5, "completion_tokens": 0 }
        });

        let resp = parse_response(&raw).unwrap();
        assert!(resp.content.is_empty());
    }

    #[test]
    fn parse_response_length_finish() {
        let raw = serde_json::json!({
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Partial response that got cut"
                },
                "finish_reason": "length"
            }],
            "usage": { "prompt_tokens": 100, "completion_tokens": 4096 }
        });

        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.stop_reason.as_deref(), Some("max_tokens"));
    }

    #[test]
    fn parse_response_content_filter() {
        let raw = serde_json::json!({
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": ""
                },
                "finish_reason": "content_filter"
            }],
            "usage": { "prompt_tokens": 10, "completion_tokens": 0 }
        });

        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.stop_reason.as_deref(), Some("refusal"));
    }

    // -----------------------------------------------------------------------
    // Finish reason translation
    // -----------------------------------------------------------------------

    #[test]
    fn finish_reason_translation() {
        assert_eq!(translate_finish_reason("stop"), "end_turn");
        assert_eq!(translate_finish_reason("length"), "max_tokens");
        assert_eq!(translate_finish_reason("tool_calls"), "tool_use");
        assert_eq!(translate_finish_reason("content_filter"), "refusal");
        assert_eq!(translate_finish_reason("unknown_reason"), "unknown_reason");
    }

    // -----------------------------------------------------------------------
    // Usage parsing
    // -----------------------------------------------------------------------

    #[test]
    fn parse_usage_from_response() {
        let raw = serde_json::json!({
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 75,
                "total_tokens": 225
            }
        });
        let u = parse_usage(&raw);
        assert_eq!(u.input_tokens, 150);
        assert_eq!(u.output_tokens, 75);
    }

    #[test]
    fn parse_usage_missing() {
        let raw = serde_json::json!({});
        let u = parse_usage(&raw);
        assert_eq!(u.input_tokens, 0);
        assert_eq!(u.output_tokens, 0);
    }

    // -----------------------------------------------------------------------
    // Streaming delta parsing
    // -----------------------------------------------------------------------

    #[test]
    fn parse_sse_text_delta() {
        let json = r#"{"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}"#;
        let mut tool_calls = Vec::new();
        let mut usage = Usage::default();
        let events = parse_sse_chunk(json, &mut tool_calls, &mut usage).unwrap();
        assert_eq!(events.len(), 1);
        match &events[0] {
            Ok(StreamEvent::TextDelta(t)) => assert_eq!(t, "Hello"),
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_tool_call_start() {
        let json = r#"{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"Bash","arguments":""}}]},"finish_reason":null}]}"#;
        let mut tool_calls = Vec::new();
        let mut usage = Usage::default();
        let events = parse_sse_chunk(json, &mut tool_calls, &mut usage).unwrap();

        // Should emit ToolUseStart.
        let has_tool_start = events.iter().any(|e| matches!(e, Ok(StreamEvent::ToolUseStart { .. })));
        assert!(has_tool_start, "should contain ToolUseStart event");

        // Tool call state should be tracked.
        assert_eq!(tool_calls.len(), 1);
        assert_eq!(tool_calls[0].0, "call_abc");
        assert_eq!(tool_calls[0].1, "Bash");
    }

    #[test]
    fn parse_sse_tool_call_arguments_delta() {
        let mut tool_calls = vec![("call_abc".to_string(), "Bash".to_string(), String::new())];
        let mut usage = Usage::default();

        let json = r#"{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"com"}}]},"finish_reason":null}]}"#;
        let events = parse_sse_chunk(json, &mut tool_calls, &mut usage).unwrap();

        let has_delta = events
            .iter()
            .any(|e| matches!(e, Ok(StreamEvent::ToolInputDelta(_))));
        assert!(has_delta, "should contain ToolInputDelta event");
        assert_eq!(tool_calls[0].2, "{\"com");

        // Append more.
        let json2 = r#"{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"mand\": \"ls\"}"}}]},"finish_reason":null}]}"#;
        parse_sse_chunk(json2, &mut tool_calls, &mut usage);
        assert_eq!(tool_calls[0].2, "{\"command\": \"ls\"}");
    }

    #[test]
    fn parse_sse_finish_reason_stop() {
        let json = r#"{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}"#;
        let mut tool_calls = Vec::new();
        let mut usage = Usage::default();
        let events = parse_sse_chunk(json, &mut tool_calls, &mut usage).unwrap();

        let has_block_stop = events
            .iter()
            .any(|e| matches!(e, Ok(StreamEvent::ContentBlockStop)));
        assert!(has_block_stop, "should emit ContentBlockStop on finish");
    }

    #[test]
    fn parse_sse_finish_reason_tool_calls() {
        let mut tool_calls = vec![
            ("call_1".to_string(), "Bash".to_string(), "{}".to_string()),
        ];
        let mut usage = Usage::default();
        let json = r#"{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}"#;
        let events = parse_sse_chunk(json, &mut tool_calls, &mut usage).unwrap();

        let block_stops = events
            .iter()
            .filter(|e| matches!(e, Ok(StreamEvent::ContentBlockStop)))
            .count();
        assert!(block_stops >= 1, "should emit ContentBlockStop(s) for tool_calls finish");
    }

    #[test]
    fn parse_sse_usage_in_chunk() {
        let json = r#"{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":50}}"#;
        let mut tool_calls = Vec::new();
        let mut usage = Usage::default();
        parse_sse_chunk(json, &mut tool_calls, &mut usage);
        assert_eq!(usage.input_tokens, 100);
        assert_eq!(usage.output_tokens, 50);
    }

    #[test]
    fn parse_sse_empty_delta() {
        let json = r#"{"choices":[{"index":0,"delta":{},"finish_reason":null}]}"#;
        let mut tool_calls = Vec::new();
        let mut usage = Usage::default();
        let result = parse_sse_chunk(json, &mut tool_calls, &mut usage);
        assert!(result.is_none(), "empty delta should produce no events");
    }

    // -----------------------------------------------------------------------
    // build_body
    // -----------------------------------------------------------------------

    #[test]
    fn build_body_includes_system_as_message() {
        let client = OpenRouterClient::with_config("test-key".into(), "https://test".into());
        let params = InferenceRequest {
            model: "openrouter/anthropic/claude-sonnet-4-5".into(),
            max_tokens: 8192,
            system: Some("You are a helpful assistant.".into()),
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "Hello".into(),
                }],
                model: None,
            }],
            tools: vec![],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);

        // Model should have openrouter/ prefix stripped.
        assert_eq!(body["model"], "anthropic/claude-sonnet-4-5");

        // Messages should have system message prepended.
        let messages = body["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 2); // system + user
        assert_eq!(messages[0]["role"], "system");
        assert_eq!(messages[0]["content"], "You are a helpful assistant.");
        assert_eq!(messages[1]["role"], "user");
        assert_eq!(messages[1]["content"], "Hello");
    }

    #[test]
    fn build_body_no_system_no_tools() {
        let client = OpenRouterClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "openrouter/openai/gpt-4o".into(),
            max_tokens: 4096,
            system: None,
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "Hi".into(),
                }],
                model: None,
            }],
            tools: vec![],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);

        assert_eq!(body["model"], "openai/gpt-4o");
        let messages = body["messages"].as_array().unwrap();
        assert_eq!(messages.len(), 1); // no system message
        assert!(body.get("tools").is_none());
    }

    #[test]
    fn build_body_includes_tools() {
        let client = OpenRouterClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "openrouter/anthropic/claude-sonnet-4-5".into(),
            max_tokens: 4096,
            system: None,
            messages: vec![],
            tools: vec![serde_json::json!({
                "name": "Bash",
                "description": "Run a command",
                "input_schema": {
                    "type": "object",
                    "properties": { "command": {"type": "string"} },
                    "required": ["command"],
                    "additionalProperties": false
                }
            })],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);

        let tools = body["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["type"], "function");
        assert_eq!(tools[0]["function"]["name"], "Bash");
    }

    #[test]
    fn build_body_stream_flag() {
        let client = OpenRouterClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "m".into(),
            max_tokens: 1024,
            messages: vec![],
            stream: true,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert_eq!(body["stream"], true);

        let params_no_stream = InferenceRequest {
            model: "m".into(),
            max_tokens: 1024,
            messages: vec![],
            stream: false,
            ..Default::default()
        };
        let body2 = client.build_body(&params_no_stream);
        assert!(body2.get("stream").is_none());
    }

    // -----------------------------------------------------------------------
    // Assistant with tool_calls but no text
    // -----------------------------------------------------------------------

    #[test]
    fn translate_assistant_tool_calls_no_text() {
        let messages = vec![Message {
            role: "assistant".into(),
            content: vec![ContentBlock::ToolUse {
                id: "call_1".into(),
                name: "Bash".into(),
                input: serde_json::json!({"command": "pwd"}),
            }],
            model: None,
        }];
        let translated = OpenRouterClient::translate_messages(&messages);
        assert_eq!(translated.len(), 1);
        assert_eq!(translated[0]["role"], "assistant");
        assert!(translated[0]["content"].is_null());
        assert!(translated[0].get("tool_calls").is_some());
    }

    // -----------------------------------------------------------------------
    // Multiple tool calls in one response
    // -----------------------------------------------------------------------

    #[test]
    fn parse_response_multiple_tool_calls() {
        let raw = serde_json::json!({
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": null,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "Read",
                                "arguments": "{\"file_path\": \"/tmp/a.txt\"}"
                            }
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "Bash",
                                "arguments": "{\"command\": \"ls\"}"
                            }
                        }
                    ]
                },
                "finish_reason": "tool_calls"
            }],
            "usage": { "prompt_tokens": 100, "completion_tokens": 50 }
        });

        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.content.len(), 2);
        match &resp.content[0] {
            ContentBlock::ToolUse { name, .. } => assert_eq!(name, "Read"),
            _ => panic!("expected ToolUse"),
        }
        match &resp.content[1] {
            ContentBlock::ToolUse { name, .. } => assert_eq!(name, "Bash"),
            _ => panic!("expected ToolUse"),
        }
    }

    // -----------------------------------------------------------------------
    // Tool call argument parsing with invalid JSON
    // -----------------------------------------------------------------------

    #[test]
    fn parse_tool_call_invalid_arguments_json() {
        let tc = serde_json::json!({
            "id": "call_bad",
            "type": "function",
            "function": {
                "name": "Bash",
                "arguments": "not valid json"
            }
        });
        let block = parse_tool_call(&tc).unwrap();
        match block {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "call_bad");
                assert_eq!(name, "Bash");
                assert!(input.is_null(), "invalid JSON arguments should parse as null");
            }
            _ => panic!("expected ToolUse"),
        }
    }
    #[test]
    fn test_openrouter_image_translation() {
        use crate::cpu::anthropic::{ContentBlock, ImageSource, Message};
        let msg = Message {
            role: "user".into(),
            content: vec![
                ContentBlock::Text { text: "What do you see?".into() },
                ContentBlock::Image {
                    source: ImageSource {
                        source_type: "base64".into(),
                        media_type: "image/png".into(),
                        data: "deadbeef".into(),
                    },
                },
            ],
            model: None,
        };
        let mut out = Vec::new();
        super::translate_message_to_openai(&msg, &mut out);
        assert_eq!(out.len(), 1);
        let content = out[0]["content"].as_array().unwrap();
        assert_eq!(content.len(), 2);
        assert_eq!(content[0]["type"], "text");
        assert_eq!(content[0]["text"], "What do you see?");
        assert_eq!(content[1]["type"], "image_url");
        assert_eq!(content[1]["image_url"]["url"], "data:image/png;base64,deadbeef");
    }


}
