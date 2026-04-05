use serde_json::Value;
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

use crate::cpu::anthropic::{
    ApiResponse, ContentBlock, InferenceRequest, Message, StreamEvent, Usage,
};

/// Gemini 3.x thoughtSignatures — must be echoed back in functionCall parts.
/// Keyed by our synthetic tool call ID ("gemini_{name}_{counter}").
static THOUGHT_SIGS: std::sync::LazyLock<Mutex<HashMap<String, Value>>> =
    std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

/// Global monotonic counter for tool call IDs — ensures unique IDs across turns.
/// Per-request counters reset to 0, causing ID collisions in THOUGHT_SIGS when
/// the same tool is called in consecutive turns.
static TOOL_CALL_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Gemini 3.x native function call ID → tool name mapping.
/// When the model sends functionCall with a native `id`, we store {id → name}
/// so we can recover the name when building functionResponse from ToolResult
/// (which only carries tool_use_id, not the name).
static TOOL_NAMES: std::sync::LazyLock<Mutex<HashMap<String, String>>> =
    std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

// ---------------------------------------------------------------------------
// GeminiClient — wraps the Google Gemini generateContent API and returns the
// same types as AnthropicClient so the agent loop doesn't need to change.
// ---------------------------------------------------------------------------

pub struct GeminiClient {
    client: reqwest::Client,
    api_key: String,
    base_url: String,
}

impl GeminiClient {
    /// Create a new client. Resolves API key through kernel secret management:
    /// Checks GEMINI_API_KEY first, then falls back to GOOGLE_API_KEY.
    pub fn new() -> Result<Self, crate::cpu::error::DriverError> {
        let api_key = crate::commands::secret::resolve_secret("GEMINI_API_KEY")
            .or_else(|_| crate::commands::secret::resolve_secret("GOOGLE_API_KEY"))
            .map_err(|_| crate::cpu::error::DriverError::MissingApiKey {
                provider: "google".into(),
                key_name: "GEMINI_API_KEY".into(),
            })?;
        Ok(Self {
            client: reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(120))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new()),
            api_key,
            base_url: "https://generativelanguage.googleapis.com".to_string(),
        })
    }

    /// Create a client with an explicit key and base URL.
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

    // -- helpers ----------------------------------------------------------

    /// Build the Gemini request body from our InferenceRequest.
    fn build_body(&self, params: &InferenceRequest) -> Value {
        let mut body = serde_json::json!({});

        // System instruction
        if let Some(sys) = &params.system {
            // Gemini models need explicit instruction to explain actions
            // and summarize findings — unlike Claude, they don't do this naturally.
            let gemini_mandate = "\n\n## Communication\n\
                - Explain Before Acting: provide a concise explanation of your intent before executing tool calls.\n\
                - Summarize Findings: after tool calls complete, summarize what you found and what it means.\n\
                - Never call tools in silence — always provide context for the human.\n";
            let full_sys = format!("{sys}{gemini_mandate}");
            body["system_instruction"] = serde_json::json!({
                "parts": [{"text": full_sys}]
            });
        }

        // Convert messages
        let contents = translate_messages_to_gemini(&params.messages);
        body["contents"] = Value::Array(contents);

        // Tools (function declarations)
        if !params.tools.is_empty() {
            let declarations = translate_tools_to_gemini(&params.tools);
            body["tools"] = serde_json::json!([{
                "functionDeclarations": declarations
            }]);
        }

        // Generation config — match gemini-cli's defaults for Gemini 3.x
        let is_gemini_3 = params.model.contains("3.1") || params.model.contains("3-");
        if is_gemini_3 {
            body["generationConfig"] = serde_json::json!({
                "maxOutputTokens": params.max_tokens,
                "temperature": 1,
                "topP": 0.95,
                "topK": 64,
                "thinkingConfig": {
                    "includeThoughts": true,
                    "thinkingLevel": "HIGH"
                }
            });
        } else {
            body["generationConfig"] = serde_json::json!({
                "maxOutputTokens": params.max_tokens
            });
        }

        body
    }

    // -- non-streaming ----------------------------------------------------

    pub async fn create(&self, params: InferenceRequest) -> Result<ApiResponse, String> {
        let body = self.build_body(&params);
        let url = format!(
            "{}/v1beta/models/{}:generateContent?key={}",
            self.base_url, params.model, self.api_key
        );

        let resp = self
            .client
            .post(&url)
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("request failed: {}", redact_key(&format!("{e}"))))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(format!("API error {status}: {msg}"));
        }

        let raw: Value = resp.json().await.map_err(|e| format!("json parse: {e}"))?;
        parse_gemini_response(&raw)
    }

    // -- streaming --------------------------------------------------------

    pub async fn create_stream(
        &self,
        params: InferenceRequest,
    ) -> Result<impl futures_util::Stream<Item = Result<StreamEvent, String>>, crate::cpu::error::DriverError> {
        let body = self.build_body(&params);
        let url = format!(
            "{}/v1beta/models/{}:streamGenerateContent?alt=sse&key={}",
            self.base_url, params.model, self.api_key
        );

        let resp = self
            .client
            .post(&url)
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .await
            .map_err(|e| crate::cpu::error::DriverError::StreamError(format!("request failed: {}", redact_key(&format!("{e}")))))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            eprintln!("[gemini] API error {status}: {msg}");
            return Err(crate::cpu::error::DriverError::ApiError {
                status: status.as_u16(),
                body: msg,
            });
        }

        let byte_stream = resp.bytes_stream();

        // Track whether we've seen a final chunk (with finishReason)
        let seen_final = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let sf = seen_final.clone();
        let parser = move |json_str: &str| -> Option<Vec<Result<StreamEvent, String>>> {
            // After we've seen the final chunk, ignore subsequent data
            if sf.load(std::sync::atomic::Ordering::Relaxed) {
                return None;
            }
            let events = parse_gemini_sse_chunk(json_str, &TOOL_CALL_COUNTER)?;
            if events.iter().any(|e| matches!(e, Ok(StreamEvent::MessageStop { .. }))) {
                sf.store(true, std::sync::atomic::Ordering::Relaxed);
            }
            Some(events)
        };

        let on_done = || -> Vec<Result<StreamEvent, String>> {
            vec![]
        };

        Ok(super::sse::buffered_sse_unfold(byte_stream, parser, on_done))
    }
}

// ---------------------------------------------------------------------------
// Message translation: Claude format <-> Gemini format
// ---------------------------------------------------------------------------

/// Translate our Claude-format Messages into Gemini "contents" array.
///
/// Key mappings:
/// - role "assistant" -> "model"
/// - role "user" -> "user"
/// - ContentBlock::Text -> {"text": "..."}
/// - ContentBlock::ToolUse -> {"functionCall": {"name": "...", "args": {...}}}
/// - ContentBlock::ToolResult -> {"functionResponse": {"name": "...", "response": {...}}}
fn translate_messages_to_gemini(messages: &[Message]) -> Vec<Value> {
    messages
        .iter()
        .map(|msg| {
            let role = match msg.role.as_str() {
                "assistant" => "model",
                other => other, // "user" stays "user"
            };

            // Gemini 3.x: thoughtSignature only on the FIRST functionCall per model turn.
            // Subsequent parallel calls in the same turn must NOT have it.
            let mut seen_first_fc = false;
            let parts: Vec<Value> = msg
                .content
                .iter()
                .map(|block| translate_content_block_to_gemini(block, &mut seen_first_fc))
                .collect();

            serde_json::json!({
                "role": role,
                "parts": parts
            })
        })
        .collect()
}

/// Translate a single ContentBlock to a Gemini "part".
/// `seen_first_fc` tracks whether we've already emitted thoughtSignature
/// for a functionCall in this message — only the first gets it.
fn translate_content_block_to_gemini(block: &ContentBlock, seen_first_fc: &mut bool) -> Value {
    match block {
        ContentBlock::Text { text } => {
            serde_json::json!({"text": text})
        }
        ContentBlock::Image { source } => {
            serde_json::json!({
                "inline_data": {
                    "mime_type": source.media_type,
                    "data": source.data
                }
            })
        }
        ContentBlock::ToolUse { id, name, input, .. } => {
            let mut part = serde_json::json!({
                "functionCall": {
                    "name": name,
                    "args": input,
                    "id": id
                }
            });
            // Gemini 3.x: thoughtSignature only on the FIRST functionCall per
            // model turn. Subsequent parallel calls must NOT have it.
            if !*seen_first_fc {
                *seen_first_fc = true;
                if let Ok(sigs) = THOUGHT_SIGS.lock() {
                    if let Some(ts) = sigs.get(id) {
                        part["thoughtSignature"] = ts.clone();
                    } else {
                        part["thoughtSignature"] = Value::String(
                            "skip_thought_signature_validator".to_string()
                        );
                    }
                }
            }
            part
        }
        ContentBlock::ToolResult {
            tool_use_id,
            content,
            is_error,
        } => {
            // Look up tool name from our id→name mapping
            let name = if let Ok(names) = TOOL_NAMES.lock() {
                names.get(tool_use_id).cloned()
            } else {
                None
            }.unwrap_or_else(|| extract_tool_name_from_id(tool_use_id));

            let response = if *is_error {
                serde_json::json!({
                    "error": true,
                    "message": content
                })
            } else {
                // Try to parse content as JSON; if it fails, wrap as string result
                match serde_json::from_str::<Value>(content) {
                    Ok(v) => serde_json::json!({"result": v}),
                    Err(_) => serde_json::json!({"result": content}),
                }
            };

            serde_json::json!({
                "functionResponse": {
                    "name": name,
                    "id": tool_use_id,
                    "response": response
                }
            })
        }
        // →949/→948/→950: Anthropic-specific content blocks — render as text for Gemini
        ContentBlock::Thinking { thinking } => {
            serde_json::json!({"text": format!("<thinking>{thinking}</thinking>")})
        }
        ContentBlock::Citation { cited_text, .. } => {
            serde_json::json!({"text": format!("[citation: {cited_text}]")})
        }
        ContentBlock::WebSearchResult { search_results } => {
            let summary: Vec<String> = search_results.iter()
                .filter_map(|r| r.get("title").and_then(|t| t.as_str()).map(String::from))
                .collect();
            serde_json::json!({"text": format!("[web results: {}]", summary.join(", "))})
        }
    }
}

/// Extract the tool name from our synthetic ID format "gemini_{name}_{counter}".
/// Falls back to the full ID if the format doesn't match.
fn extract_tool_name_from_id(id: &str) -> String {
    if let Some(rest) = id.strip_prefix("gemini_") {
        // Find the last underscore — everything before it is the name
        if let Some(pos) = rest.rfind('_') {
            let name = &rest[..pos];
            if !name.is_empty() {
                return name.to_string();
            }
        }
    }
    // Fallback: return the ID itself (works if the ID is just the tool name)
    id.to_string()
}

/// Generate a synthetic tool call ID for Gemini (which has no native IDs).
/// Format: "gemini_{name}_{counter}" — encodes the tool name for round-trip.
fn generate_tool_call_id(
    name: &str,
    counter: &std::sync::atomic::AtomicU64,
) -> String {
    let n = counter.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    format!("gemini_{name}_{n}")
}

// ---------------------------------------------------------------------------
// Tool schema translation
// ---------------------------------------------------------------------------

/// Convert Claude-format tool schemas to Gemini functionDeclarations.
///
/// Redact API keys from error messages to prevent leaking into logs/score files.
fn redact_key(msg: &str) -> String {
    // Strip ?key=... and &key=... from URLs
    let re = regex::Regex::new(r"[?&]key=[A-Za-z0-9_-]+").unwrap();
    re.replace_all(msg, "?key=REDACTED").to_string()
}

/// NOTE (→850): This is intentionally separate from `translate_tools_openai` in
/// `cpu/mod.rs`. Gemini uses a flat `functionDeclarations` format (no
/// `{"type":"function","function":{…}}` wrapper), requires non-null description,
/// and strips `additionalProperties` which it doesn't support.
///
/// Claude: {"name": "Bash", "description": "...", "input_schema": {...}}
/// Gemini: {"name": "Bash", "description": "...", "parameters": {...}}
fn translate_tools_to_gemini(tools: &[Value]) -> Vec<Value> {
    tools
        .iter()
        .filter_map(|tool| {
            let name = tool.get("name")?.as_str()?;
            let description = tool.get("description")?.as_str()?;
            let input_schema = tool.get("input_schema")?;

            // Gemini uses "parameters" instead of "input_schema"
            // Also strip "additionalProperties" which Gemini doesn't support
            let mut parameters = input_schema.clone();
            if let Some(obj) = parameters.as_object_mut() {
                obj.remove("additionalProperties");
            }

            Some(serde_json::json!({
                "name": name,
                "description": description,
                "parameters": parameters
            }))
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Response parsing
// ---------------------------------------------------------------------------

/// Parse a Gemini generateContent response into our ApiResponse type.
///
/// Gemini response shape:
/// ```json
/// {
///   "candidates": [{
///     "content": {"parts": [{"text": "..."}, {"functionCall": {...}}]},
///     "finishReason": "STOP"
///   }],
///   "usageMetadata": {
///     "promptTokenCount": 100,
///     "candidatesTokenCount": 50,
///     "totalTokenCount": 150
///   }
/// }
/// ```
fn parse_gemini_response(raw: &Value) -> Result<ApiResponse, String> {
    let candidate = raw
        .get("candidates")
        .and_then(|c| c.as_array())
        .and_then(|a| a.first())
        .ok_or("missing candidates array or empty")?;

    let parts = candidate
        .get("content")
        .and_then(|c| c.get("parts"))
        .and_then(|p| p.as_array())
        .unwrap_or(&Vec::new())
        .clone();

    let content: Vec<ContentBlock> = parts
        .iter()
        .filter_map(|part| parse_gemini_part(part, &TOOL_CALL_COUNTER))
        .collect();

    let finish_reason = candidate
        .get("finishReason")
        .and_then(|v| v.as_str())
        .unwrap_or("STOP");
    let stop_reason = map_finish_reason(finish_reason);

    let usage = parse_gemini_usage(raw);

    Ok(ApiResponse {
        content,
        stop_reason: Some(stop_reason),
        usage,
        applied_edits: vec![],
    })
}

/// Parse a single Gemini "part" into a ContentBlock.
fn parse_gemini_part(
    part: &Value,
    counter: &std::sync::atomic::AtomicU64,
) -> Option<ContentBlock> {
    if let Some(text) = part.get("text").and_then(|t| t.as_str()) {
        return Some(ContentBlock::Text {
            text: text.to_string(),
        });
    }

    if let Some(fc) = part.get("functionCall") {
        let name = fc.get("name")?.as_str()?.to_string();
        let args = fc.get("args").cloned().unwrap_or(Value::Object(Default::default()));
        // Gemini 3.x returns a native `id` on every functionCall; use it when
        // present so we can echo it back in functionResponse. Fall back to our
        // synthetic ID for Gemini 2.x which has no native ID.
        let native_id = fc.get("id").and_then(|v| v.as_str()).map(|s| s.to_string());
        let id = native_id.unwrap_or_else(|| generate_tool_call_id(&name, counter));
        // Store name mapping for functionResponse reconstruction
        if let Ok(mut names) = TOOL_NAMES.lock() {
            names.insert(id.clone(), name.clone());
        }
        // Gemini 3.x: capture thoughtSignature for echo-back in functionResponse
        if let Some(ts) = part.get("thoughtSignature")
            && let Ok(mut sigs) = THOUGHT_SIGS.lock() {
                sigs.insert(id.clone(), ts.clone());
            }
        return Some(ContentBlock::ToolUse {
            id,
            name,
            input: args,
        });
    }

    None
}

/// Map Gemini finishReason to our internal stop reason strings.
fn map_finish_reason(reason: &str) -> String {
    match reason {
        "STOP" => "end_turn".to_string(),
        "MAX_TOKENS" => "max_tokens".to_string(),
        "SAFETY" => "refusal".to_string(),
        "RECITATION" => "refusal".to_string(),
        "FINISH_REASON_UNSPECIFIED" => "end_turn".to_string(),
        other => other.to_lowercase(),
    }
}

/// Parse usage metadata from a Gemini response.
fn parse_gemini_usage(raw: &Value) -> Usage {
    let meta = raw.get("usageMetadata");
    Usage {
        input_tokens: meta
            .and_then(|u| u.get("promptTokenCount"))
            .and_then(|v| v.as_u64())
            .unwrap_or(0),
        output_tokens: meta
            .and_then(|u| u.get("candidatesTokenCount"))
            .and_then(|v| v.as_u64())
            .unwrap_or(0),
        ..Default::default()
    }
}

// ---------------------------------------------------------------------------
// SSE streaming parser
// ---------------------------------------------------------------------------

/// Parse a single Gemini SSE chunk and produce StreamEvents.
///
/// Each SSE chunk from streamGenerateContent contains a partial
/// GenerateContentResponse. We extract text deltas and function calls.
///
/// Returns None if the chunk doesn't produce any events.
fn parse_gemini_sse_chunk(
    json_str: &str,
    counter: &std::sync::atomic::AtomicU64,
) -> Option<Vec<Result<StreamEvent, String>>> {
    let v: Value = match serde_json::from_str(json_str) {
        Ok(v) => v,
        Err(e) => return Some(vec![Err(format!("SSE json parse: {e}"))]),
    };

    let candidate = v.get("candidates")?.as_array()?.first()?;
    let mut events = Vec::new();

    // Check for parts in this chunk
    if let Some(parts) = candidate
        .get("content")
        .and_then(|c| c.get("parts"))
        .and_then(|p| p.as_array())
    {
        for part in parts {
            // Gemini 3.x: skip thought parts (thinking summaries).
            // Like gemini-cli, we strip these from history. The
            // thoughtSignature on functionCall parts is what matters.
            if part.get("thought").and_then(|t| t.as_bool()).unwrap_or(false) {
                continue;
            }

            if let Some(text) = part.get("text").and_then(|t| t.as_str()) {
                // If the text is empty and there's a thoughtSignature present,
                // skip emitting a TextDelta — the empty text has no user-visible
                // value, and we capture the signature below for later echo-back.
                let has_sig = part.get("thoughtSignature").is_some();
                if !text.is_empty() {
                    events.push(Ok(StreamEvent::TextDelta(text.to_string())));
                }
                // Capture any thoughtSignature on a text part (including empty ones)
                // under a special sentinel key so translate_content_block_to_gemini
                // can attach it to the subsequent Text block.
                if has_sig
                    && let Some(ts) = part.get("thoughtSignature")
                        && let Ok(mut sigs) = THOUGHT_SIGS.lock() {
                            sigs.insert("gemini_text_sig_latest".to_string(), ts.clone());
                        }
            }

            if let Some(fc) = part.get("functionCall")
                && let (Some(name), args) = (
                    fc.get("name").and_then(|n| n.as_str()),
                    fc.get("args").cloned().unwrap_or(Value::Object(Default::default())),
                ) {
                    // Gemini 3.x returns a native `id` on every functionCall; use it
                    // when present. Fall back to synthetic ID for Gemini 2.x.
                    let native_id = fc.get("id").and_then(|v| v.as_str()).map(|s| s.to_string());
                    let id = native_id.unwrap_or_else(|| generate_tool_call_id(name, counter));
                    // Store name mapping for functionResponse reconstruction
                    if let Ok(mut names) = TOOL_NAMES.lock() {
                        names.insert(id.clone(), name.to_string());
                    }
                    // Gemini 3.x: capture thoughtSignature for echo-back
                    if let Some(ts) = part.get("thoughtSignature")
                        && let Ok(mut sigs) = THOUGHT_SIGS.lock() {
                            sigs.insert(id.clone(), ts.clone());
                        }
                    // Gemini delivers function calls atomically (not streamed).
                    // Emit ToolUseStart, then the complete input as a single delta,
                    // then ContentBlockStop.
                    events.push(Ok(StreamEvent::ToolUseStart {
                        id,
                        name: name.to_string(),
                    }));
                    // Emit the full args JSON as a single input delta
                    let args_str = serde_json::to_string(&args).unwrap_or_default();
                    events.push(Ok(StreamEvent::ToolInputDelta(args_str)));
                    events.push(Ok(StreamEvent::ContentBlockStop));
                }
        }
    }

    // Check for finishReason — indicates message complete
    if let Some(reason) = candidate.get("finishReason").and_then(|r| r.as_str()) {
        // Flush any pending text as ContentBlockStop
        if events.iter().any(|e| matches!(e, Ok(StreamEvent::TextDelta(_)))) {
            events.push(Ok(StreamEvent::ContentBlockStop));
        }
        let stop_reason = map_finish_reason(reason);
        let usage = parse_gemini_usage(&v);
        events.push(Ok(StreamEvent::MessageStop { stop_reason, usage }));
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
    use serde_json::json;

    // -- Message format translation ----------------------------------------

    #[test]
    fn translate_text_message_user() {
        let messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "Hello".into(),
            }],
            model: None,
        }];
        let gemini = translate_messages_to_gemini(&messages);
        assert_eq!(gemini.len(), 1);
        assert_eq!(gemini[0]["role"], "user");
        assert_eq!(gemini[0]["parts"][0]["text"], "Hello");
    }

    #[test]
    fn translate_text_message_assistant_to_model() {
        let messages = vec![Message {
            role: "assistant".into(),
            content: vec![ContentBlock::Text {
                text: "Hi there".into(),
            }],
            model: None,
        }];
        let gemini = translate_messages_to_gemini(&messages);
        assert_eq!(gemini[0]["role"], "model");
        assert_eq!(gemini[0]["parts"][0]["text"], "Hi there");
    }

    #[test]
    fn translate_tool_use_to_function_call() {
        let messages = vec![Message {
            role: "assistant".into(),
            content: vec![ContentBlock::ToolUse {
                id: "tu_1".into(),
                name: "Bash".into(),
                input: json!({"command": "ls"}),
            }],
            model: None,
        }];
        let gemini = translate_messages_to_gemini(&messages);
        let fc = &gemini[0]["parts"][0]["functionCall"];
        assert_eq!(fc["name"], "Bash");
        assert_eq!(fc["args"]["command"], "ls");
    }

    #[test]
    fn translate_tool_result_to_function_response() {
        let messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::ToolResult {
                tool_use_id: "gemini_Bash_0".into(),
                content: "file1.txt\nfile2.txt".into(),
                is_error: false,
            }],
            model: None,
        }];
        let gemini = translate_messages_to_gemini(&messages);
        let fr = &gemini[0]["parts"][0]["functionResponse"];
        assert_eq!(fr["name"], "Bash");
        assert_eq!(fr["response"]["result"], "file1.txt\nfile2.txt");
    }

    #[test]
    fn translate_tool_result_error_wraps_in_error_json() {
        let messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::ToolResult {
                tool_use_id: "gemini_Bash_0".into(),
                content: "command not found".into(),
                is_error: true,
            }],
            model: None,
        }];
        let gemini = translate_messages_to_gemini(&messages);
        let fr = &gemini[0]["parts"][0]["functionResponse"];
        assert_eq!(fr["name"], "Bash");
        assert_eq!(fr["response"]["error"], true);
        assert_eq!(fr["response"]["message"], "command not found");
    }

    // -- Round-trip: Claude -> Gemini -> Claude ----------------------------

    #[test]
    fn round_trip_text_message() {
        let original = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "What is 2+2?".into(),
            }],
            model: None,
        }];

        let gemini = translate_messages_to_gemini(&original);

        // Verify Gemini format
        assert_eq!(gemini[0]["role"], "user");
        assert_eq!(gemini[0]["parts"][0]["text"], "What is 2+2?");

        // Parse back from Gemini response format
        let gemini_response = json!({
            "candidates": [{
                "content": {
                    "parts": [{"text": "4"}]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 1,
                "totalTokenCount": 11
            }
        });

        let response = parse_gemini_response(&gemini_response).unwrap();
        assert_eq!(response.content.len(), 1);
        match &response.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "4"),
            _ => panic!("expected Text block"),
        }
    }

    #[test]
    fn round_trip_tool_call() {
        // Simulate assistant message with tool use -> Gemini format
        let messages = vec![Message {
            role: "assistant".into(),
            content: vec![
                ContentBlock::Text {
                    text: "Let me check.".into(),
                },
                ContentBlock::ToolUse {
                    id: "gemini_Bash_0".into(),
                    name: "Bash".into(),
                    input: json!({"command": "ls"}),
                },
            ],
            model: None,
        }];
        let gemini = translate_messages_to_gemini(&messages);
        assert_eq!(gemini[0]["role"], "model");
        assert_eq!(gemini[0]["parts"][0]["text"], "Let me check.");
        assert_eq!(gemini[0]["parts"][1]["functionCall"]["name"], "Bash");
        assert_eq!(gemini[0]["parts"][1]["functionCall"]["args"]["command"], "ls");

        // Now simulate Gemini returning a function call
        let gemini_response = json!({
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "I'll run that command."},
                        {"functionCall": {"name": "Bash", "args": {"command": "pwd"}}}
                    ]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 50,
                "candidatesTokenCount": 20,
                "totalTokenCount": 70
            }
        });

        let response = parse_gemini_response(&gemini_response).unwrap();
        assert_eq!(response.content.len(), 2);
        match &response.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "I'll run that command."),
            _ => panic!("expected Text"),
        }
        match &response.content[1] {
            ContentBlock::ToolUse { id, name, input } => {
                assert!(id.starts_with("gemini_Bash_"));
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "pwd");
            }
            _ => panic!("expected ToolUse"),
        }
    }

    // -- Tool schema translation ------------------------------------------

    #[test]
    fn tool_schema_translation() {
        let claude_tools = vec![json!({
            "name": "Bash",
            "description": "Execute a shell command",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to execute"}
                },
                "required": ["command"],
                "additionalProperties": false
            }
        })];

        let gemini_tools = translate_tools_to_gemini(&claude_tools);
        assert_eq!(gemini_tools.len(), 1);

        let tool = &gemini_tools[0];
        assert_eq!(tool["name"], "Bash");
        assert_eq!(tool["description"], "Execute a shell command");

        // Should use "parameters" not "input_schema"
        assert!(tool.get("input_schema").is_none());
        assert!(tool.get("parameters").is_some());

        let params = &tool["parameters"];
        assert_eq!(params["type"], "object");
        assert_eq!(params["properties"]["command"]["type"], "string");

        // additionalProperties should be stripped
        assert!(params.get("additionalProperties").is_none());

        // required should be preserved
        assert_eq!(params["required"][0], "command");
    }

    #[test]
    fn tool_schema_translation_multiple_tools() {
        let claude_tools = vec![
            json!({
                "name": "Bash",
                "description": "Execute a shell command",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"}
                    },
                    "required": ["command"],
                    "additionalProperties": false
                }
            }),
            json!({
                "name": "Read",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"}
                    },
                    "required": ["file_path"],
                    "additionalProperties": false
                }
            }),
        ];

        let gemini_tools = translate_tools_to_gemini(&claude_tools);
        assert_eq!(gemini_tools.len(), 2);
        assert_eq!(gemini_tools[0]["name"], "Bash");
        assert_eq!(gemini_tools[1]["name"], "Read");
    }

    // -- Response parsing -------------------------------------------------

    #[test]
    fn parse_text_response() {
        let raw = json!({
            "candidates": [{
                "content": {
                    "parts": [{"text": "Hello, world!"}]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 3,
                "totalTokenCount": 8
            }
        });

        let response = parse_gemini_response(&raw).unwrap();
        assert_eq!(response.content.len(), 1);
        match &response.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "Hello, world!"),
            _ => panic!("expected Text"),
        }
        assert_eq!(response.stop_reason, Some("end_turn".to_string()));
        assert_eq!(response.usage.input_tokens, 5);
        assert_eq!(response.usage.output_tokens, 3);
    }

    #[test]
    fn parse_function_call_response() {
        let raw = json!({
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "Bash",
                            "args": {"command": "ls -la"}
                        }
                    }]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 10,
                "totalTokenCount": 30
            }
        });

        let response = parse_gemini_response(&raw).unwrap();
        assert_eq!(response.content.len(), 1);
        match &response.content[0] {
            ContentBlock::ToolUse { id, name, input } => {
                assert!(id.starts_with("gemini_Bash_"));
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls -la");
            }
            _ => panic!("expected ToolUse"),
        }
    }

    #[test]
    fn parse_mixed_text_and_function_call() {
        let raw = json!({
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "Let me check that for you."},
                        {"functionCall": {"name": "Read", "args": {"file_path": "/tmp/test.txt"}}}
                    ]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 30,
                "candidatesTokenCount": 15,
                "totalTokenCount": 45
            }
        });

        let response = parse_gemini_response(&raw).unwrap();
        assert_eq!(response.content.len(), 2);
        assert!(matches!(&response.content[0], ContentBlock::Text { text } if text == "Let me check that for you."));
        assert!(matches!(&response.content[1], ContentBlock::ToolUse { name, .. } if name == "Read"));
    }

    #[test]
    fn parse_multiple_function_calls() {
        let raw = json!({
            "candidates": [{
                "content": {
                    "parts": [
                        {"functionCall": {"name": "Bash", "args": {"command": "ls"}}},
                        {"functionCall": {"name": "Read", "args": {"file_path": "/tmp/a.txt"}}}
                    ]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 40,
                "candidatesTokenCount": 20,
                "totalTokenCount": 60
            }
        });

        let response = parse_gemini_response(&raw).unwrap();
        assert_eq!(response.content.len(), 2);

        match &response.content[0] {
            ContentBlock::ToolUse { id, name, .. } => {
                assert!(id.starts_with("gemini_Bash_"));
                assert_eq!(name, "Bash");
            }
            _ => panic!("expected ToolUse"),
        }
        match &response.content[1] {
            ContentBlock::ToolUse { id, name, .. } => {
                assert!(id.starts_with("gemini_Read_"));
                assert_eq!(name, "Read");
            }
            _ => panic!("expected ToolUse"),
        }

        // IDs should be different
        let id0 = match &response.content[0] {
            ContentBlock::ToolUse { id, .. } => id.clone(),
            _ => unreachable!(),
        };
        let id1 = match &response.content[1] {
            ContentBlock::ToolUse { id, .. } => id.clone(),
            _ => unreachable!(),
        };
        assert_ne!(id0, id1, "synthetic IDs must be unique");
    }

    // -- Synthetic tool call ID generation --------------------------------

    #[test]
    fn synthetic_id_generation_unique() {
        let counter = std::sync::atomic::AtomicU64::new(0);
        let id1 = generate_tool_call_id("Bash", &counter);
        let id2 = generate_tool_call_id("Bash", &counter);
        let id3 = generate_tool_call_id("Read", &counter);

        assert_ne!(id1, id2);
        assert_ne!(id2, id3);
        assert!(id1.starts_with("gemini_Bash_"));
        assert!(id2.starts_with("gemini_Bash_"));
        assert!(id3.starts_with("gemini_Read_"));
    }

    #[test]
    fn synthetic_id_round_trip_name_extraction() {
        let counter = std::sync::atomic::AtomicU64::new(0);
        let id = generate_tool_call_id("Bash", &counter);
        let name = extract_tool_name_from_id(&id);
        assert_eq!(name, "Bash");
    }

    #[test]
    fn extract_name_from_id_various_formats() {
        assert_eq!(extract_tool_name_from_id("gemini_Bash_0"), "Bash");
        assert_eq!(extract_tool_name_from_id("gemini_Read_42"), "Read");
        assert_eq!(extract_tool_name_from_id("gemini_Edit_100"), "Edit");
        // Fallback for non-matching formats
        assert_eq!(extract_tool_name_from_id("some_other_id"), "some_other_id");
        assert_eq!(extract_tool_name_from_id("gemini_"), "gemini_");
    }

    // -- Stop reason mapping ----------------------------------------------

    #[test]
    fn stop_reason_mapping() {
        assert_eq!(map_finish_reason("STOP"), "end_turn");
        assert_eq!(map_finish_reason("MAX_TOKENS"), "max_tokens");
        assert_eq!(map_finish_reason("SAFETY"), "refusal");
        assert_eq!(map_finish_reason("RECITATION"), "refusal");
        assert_eq!(map_finish_reason("FINISH_REASON_UNSPECIFIED"), "end_turn");
    }

    #[test]
    fn stop_reason_unknown_lowercased() {
        assert_eq!(map_finish_reason("OTHER_REASON"), "other_reason");
    }

    // -- Usage parsing ----------------------------------------------------

    #[test]
    fn parse_usage_metadata() {
        let raw = json!({
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 50,
                "totalTokenCount": 150
            }
        });
        let usage = parse_gemini_usage(&raw);
        assert_eq!(usage.input_tokens, 100);
        assert_eq!(usage.output_tokens, 50);
    }

    #[test]
    fn parse_usage_metadata_missing() {
        let raw = json!({});
        let usage = parse_gemini_usage(&raw);
        assert_eq!(usage.input_tokens, 0);
        assert_eq!(usage.output_tokens, 0);
    }

    // -- SSE chunk parsing ------------------------------------------------

    #[test]
    fn parse_sse_text_chunk() {
        let counter = std::sync::atomic::AtomicU64::new(0);
        let chunk = r#"{"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}"#;
        let events = parse_gemini_sse_chunk(chunk, &counter).unwrap();
        assert_eq!(events.len(), 1);
        match &events[0] {
            Ok(StreamEvent::TextDelta(t)) => assert_eq!(t, "Hello"),
            other => panic!("expected TextDelta, got {:?}", other),
        }
    }

    #[test]
    fn parse_sse_function_call_chunk() {
        let counter = std::sync::atomic::AtomicU64::new(0);
        let chunk = r#"{"candidates":[{"content":{"parts":[{"functionCall":{"name":"Bash","args":{"command":"pwd"}}}]}}]}"#;
        let events = parse_gemini_sse_chunk(chunk, &counter).unwrap();
        // Should produce: ToolUseStart, ToolInputDelta, ContentBlockStop
        assert_eq!(events.len(), 3);
        match &events[0] {
            Ok(StreamEvent::ToolUseStart { name, id }) => {
                assert_eq!(name, "Bash");
                assert!(id.starts_with("gemini_Bash_"));
            }
            other => panic!("expected ToolUseStart, got {:?}", other),
        }
        match &events[1] {
            Ok(StreamEvent::ToolInputDelta(s)) => {
                let v: Value = serde_json::from_str(s).unwrap();
                assert_eq!(v["command"], "pwd");
            }
            other => panic!("expected ToolInputDelta, got {:?}", other),
        }
        assert!(matches!(&events[2], Ok(StreamEvent::ContentBlockStop)));
    }

    #[test]
    fn parse_sse_final_chunk_with_finish_reason() {
        let counter = std::sync::atomic::AtomicU64::new(0);
        let chunk = r#"{"candidates":[{"content":{"parts":[{"text":"Done."}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5,"totalTokenCount":15}}"#;
        let events = parse_gemini_sse_chunk(chunk, &counter).unwrap();
        // Should produce: TextDelta, ContentBlockStop (for text), MessageStop
        assert!(events.len() >= 2);

        // Find the MessageStop
        let msg_stop = events.iter().find(|e| matches!(e, Ok(StreamEvent::MessageStop { .. })));
        assert!(msg_stop.is_some());
        match msg_stop.unwrap() {
            Ok(StreamEvent::MessageStop { stop_reason, usage }) => {
                assert_eq!(stop_reason, "end_turn");
                assert_eq!(usage.input_tokens, 10);
                assert_eq!(usage.output_tokens, 5);
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn parse_sse_safety_finish_reason() {
        let counter = std::sync::atomic::AtomicU64::new(0);
        let chunk = r#"{"candidates":[{"finishReason":"SAFETY"}]}"#;
        let events = parse_gemini_sse_chunk(chunk, &counter).unwrap();
        let msg_stop = events.iter().find(|e| matches!(e, Ok(StreamEvent::MessageStop { .. })));
        assert!(msg_stop.is_some());
        match msg_stop.unwrap() {
            Ok(StreamEvent::MessageStop { stop_reason, .. }) => {
                assert_eq!(stop_reason, "refusal");
            }
            _ => unreachable!(),
        }
    }

    #[test]
    fn parse_sse_invalid_json() {
        let counter = std::sync::atomic::AtomicU64::new(0);
        let events = parse_gemini_sse_chunk("not json", &counter).unwrap();
        assert_eq!(events.len(), 1);
        assert!(events[0].is_err());
    }

    // -- Build body -------------------------------------------------------

    #[test]
    fn build_body_basic() {
        let client = GeminiClient::with_config("test-key".into(), "https://test".into());
        let params = InferenceRequest {
            model: "gemini-2.5-flash".into(),
            max_tokens: 8192,
            system: Some("You are helpful.".into()),
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "Hi".into() }],
                model: None,
            }],
            tools: vec![json!({
                "name": "Bash",
                "description": "Execute a command",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                    "additionalProperties": false
                }
            })],
            ..Default::default()
        };

        let body = client.build_body(&params);

        // System instruction — includes Gemini communication mandate
        let sys_text = body["system_instruction"]["parts"][0]["text"].as_str().unwrap();
        assert!(sys_text.starts_with("You are helpful."), "should start with original prompt");
        assert!(sys_text.contains("Explain Before Acting"), "should include Gemini mandate");

        // Contents
        assert_eq!(body["contents"][0]["role"], "user");
        assert_eq!(body["contents"][0]["parts"][0]["text"], "Hi");

        // Tools wrapped in functionDeclarations
        assert!(body["tools"][0]["functionDeclarations"].is_array());
        assert_eq!(body["tools"][0]["functionDeclarations"][0]["name"], "Bash");
        // Should use "parameters" not "input_schema"
        assert!(body["tools"][0]["functionDeclarations"][0].get("parameters").is_some());

        // Generation config
        assert_eq!(body["generationConfig"]["maxOutputTokens"], 8192);
    }

    #[test]
    fn build_body_no_system_no_tools() {
        let client = GeminiClient::with_config("test-key".into(), "https://test".into());
        let params = InferenceRequest {
            model: "gemini-2.5-flash".into(),
            max_tokens: 4096,
            system: None,
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "Hello".into(),
                }],
                model: None,
            }],
            tools: vec![],
            ..Default::default()
        };

        let body = client.build_body(&params);
        assert!(body.get("system_instruction").is_none());
        assert!(body.get("tools").is_none());
    }

    // -- Conversation with tool use cycle ---------------------------------

    #[test]
    fn full_tool_use_cycle_message_translation() {
        // Step 1: User sends initial message
        let user_msg = Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "List files in /tmp".into(),
            }],
            model: None,
        };

        // Step 2: Model responds with function call (simulated parse)
        let model_response_raw = json!({
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "I'll list the files."},
                        {"functionCall": {"name": "Bash", "args": {"command": "ls /tmp"}}}
                    ]
                },
                "finishReason": "STOP"
            }],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 15,
                "totalTokenCount": 35
            }
        });
        let model_response = parse_gemini_response(&model_response_raw).unwrap();

        // Verify the tool call got a synthetic ID
        let tool_call = model_response.content.iter().find_map(|b| match b {
            ContentBlock::ToolUse { id, name, input } => {
                Some((id.clone(), name.clone(), input.clone()))
            }
            _ => None,
        });
        assert!(tool_call.is_some());
        let (tool_id, tool_name, _tool_input) = tool_call.unwrap();
        assert_eq!(tool_name, "Bash");
        assert!(tool_id.starts_with("gemini_Bash_"));

        // Step 3: Build tool result message using the synthetic ID
        let tool_result_msg = Message {
            role: "user".into(),
            content: vec![ContentBlock::ToolResult {
                tool_use_id: tool_id.clone(),
                content: "file1.txt\nfile2.txt".into(),
                is_error: false,
            }],
            model: None,
        };

        // Step 4: Translate the full conversation to Gemini format
        let all_messages = vec![
            user_msg.clone(),
            Message {
                role: "assistant".into(),
                content: model_response.content,
                model: None,
            },
            tool_result_msg,
        ];
        let gemini_contents = translate_messages_to_gemini(&all_messages);

        assert_eq!(gemini_contents.len(), 3);

        // User message
        assert_eq!(gemini_contents[0]["role"], "user");

        // Model message with functionCall
        assert_eq!(gemini_contents[1]["role"], "model");
        assert!(gemini_contents[1]["parts"][1]["functionCall"].is_object());

        // Tool result as functionResponse
        assert_eq!(gemini_contents[2]["role"], "user");
        let fr = &gemini_contents[2]["parts"][0]["functionResponse"];
        assert_eq!(fr["name"], "Bash"); // name extracted from synthetic ID
        assert_eq!(fr["response"]["result"], "file1.txt\nfile2.txt");
    }
    #[test]
    fn thought_signature_round_trips_through_function_call() {
        use std::sync::atomic::AtomicU64;
        // Simulate Gemini 3.x response with thoughtSignature on a functionCall part
        let part_with_sig = serde_json::json!({
            "functionCall": {"name": "ostk_show", "args": {"query": "needles"}},
            "thoughtSignature": "dGhvdWdodF9zaWduYXR1cmVfYmFzZTY0"
        });
        let counter = AtomicU64::new(100);

        // Parse — should capture thoughtSignature
        let block = super::parse_gemini_part(&part_with_sig, &counter).unwrap();
        let tool_id = match &block {
            ContentBlock::ToolUse { id, name, .. } => {
                assert_eq!(name, "ostk_show");
                id.clone()
            }
            _ => panic!("expected ToolUse"),
        };

        // Translate back — should echo thoughtSignature
        let gemini_part = super::translate_content_block_to_gemini(&block, &mut false);
        assert_eq!(gemini_part["functionCall"]["name"], "ostk_show");
        assert_eq!(
            gemini_part["thoughtSignature"],
            "dGhvdWdodF9zaWduYXR1cmVfYmFzZTY0",
            "thoughtSignature must round-trip through functionCall translation"
        );

        // Clean up
        if let Ok(mut sigs) = super::THOUGHT_SIGS.lock() {
            sigs.remove(&tool_id);
        }
    }

    #[test]
    fn function_call_without_thought_signature_uses_escape_hatch() {
        // Gemini 3.x requires thoughtSignature to be echoed back on all
        // functionCall parts. When no signature was captured (e.g. Gemini 2.x,
        // history injection, or edge cases), we emit the documented escape-hatch
        // value so the API does not return a 400 error.
        use std::sync::atomic::AtomicU64;
        let part_no_sig = serde_json::json!({
            "functionCall": {"name": "Bash", "args": {"command": "ls"}}
        });
        let counter = AtomicU64::new(200);
        let block = super::parse_gemini_part(&part_no_sig, &counter).unwrap();
        let gemini_part = super::translate_content_block_to_gemini(&block, &mut false);
        assert_eq!(gemini_part["functionCall"]["name"], "Bash");
        // The escape hatch sentinel must be present, not absent
        assert_eq!(
            gemini_part.get("thoughtSignature").and_then(|v| v.as_str()),
            Some("skip_thought_signature_validator"),
            "escape hatch sentinel should be emitted when no signature was captured"
        );
    }

    #[test]
    fn native_id_round_trips_through_function_call_and_response() {
        use std::sync::atomic::AtomicU64;
        use crate::cpu::anthropic::ContentBlock;

        // Simulate Gemini 3.x response with native id
        let part = serde_json::json!({
            "functionCall": {"name": "Bash", "id": "frgxsyxq", "args": {"command": "ls"}},
            "thoughtSignature": "test_sig_123"
        });
        let counter = AtomicU64::new(0);
        let block = super::parse_gemini_part(&part, &counter).unwrap();

        // Verify ToolUse captures native id
        let tool_id = match &block {
            ContentBlock::ToolUse { id, name, .. } => {
                assert_eq!(id, "frgxsyxq", "should use native id");
                assert_eq!(name, "Bash");
                id.clone()
            }
            _ => panic!("expected ToolUse"),
        };

        // Translate ToolUse back — verify id is echoed
        let gemini_part = super::translate_content_block_to_gemini(&block, &mut false);
        assert_eq!(gemini_part["functionCall"]["name"], "Bash");
        assert_eq!(gemini_part["functionCall"]["id"], "frgxsyxq");
        assert!(gemini_part.get("thoughtSignature").is_some());

        // Create matching ToolResult
        let result_block = ContentBlock::ToolResult {
            tool_use_id: tool_id.clone(),
            content: r#"{"result": "file1.txt"}"#.into(),
            is_error: false,
        };
        let gemini_result = super::translate_content_block_to_gemini(&result_block, &mut false);

        // Verify functionResponse has correct name (not the id) AND echoes id
        assert_eq!(gemini_result["functionResponse"]["name"], "Bash",
            "name must be the tool name, not the native id");
        assert_eq!(gemini_result["functionResponse"]["id"], "frgxsyxq",
            "id must be echoed in functionResponse");

        // Clean up
        if let Ok(mut sigs) = super::THOUGHT_SIGS.lock() { sigs.remove(&tool_id); }
        if let Ok(mut names) = super::TOOL_NAMES.lock() { names.remove(&tool_id); }
    }

    #[test]
    fn test_gemini_image_translation() {
        use crate::cpu::anthropic::{ContentBlock, ImageSource, Message};
        let messages = vec![Message {
            role: "user".into(),
            content: vec![
                ContentBlock::Text { text: "Describe this.".into() },
                ContentBlock::Image {
                    source: ImageSource {
                        source_type: "base64".into(),
                        media_type: "image/jpeg".into(),
                        data: "cafebabe".into(),
                    },
                },
            ],
            model: None,
        }];
        let parts = super::translate_messages_to_gemini(&messages);
        assert_eq!(parts.len(), 1);
        let msg_parts = parts[0]["parts"].as_array().unwrap();
        assert_eq!(msg_parts.len(), 2);
        assert_eq!(msg_parts[0]["text"], "Describe this.");
        assert_eq!(msg_parts[1]["inline_data"]["mime_type"], "image/jpeg");
        assert_eq!(msg_parts[1]["inline_data"]["data"], "cafebabe");
    }


}
