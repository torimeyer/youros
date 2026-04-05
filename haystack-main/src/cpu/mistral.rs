//! MistralClient — CpuDriver for the Mistral API.
//!
//! Mistral's API is OpenAI-compatible at the wire level. This client reuses
//! the OpenAI-compatible message translation and SSE parsing from `openrouter.rs`
//! but carries no OpenRouter-specific headers or model prefix stripping.
//! provider_name() returns "mistral", not "openrouter".

use serde_json::Value;
use std::time::Duration;

use crate::cpu::anthropic::{
    InferenceRequest, StreamEvent, Usage,
};

const MISTRAL_API_URL: &str = "https://api.mistral.ai/v1";

/// Normalize short model names to the exact IDs the Mistral API accepts.
/// Unknown names pass through unchanged.
pub fn normalize_mistral_model(model: &str) -> &str {
    match model {
        "devstral-medium" => "devstral-medium-2507",
        _ => model,
    }
}

pub struct MistralClient {
    client: reqwest::Client,
    api_key: String,
}

impl MistralClient {
    pub fn new() -> Result<Self, crate::cpu::error::DriverError> {
        let api_key = crate::commands::secret::resolve_secret("MISTRAL_API_KEY")
            .map_err(|_| crate::cpu::error::DriverError::MissingApiKey {
                provider: "mistral".into(),
                key_name: "MISTRAL_API_KEY".into(),
            })?;
        Ok(Self {
            client: reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(120))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new()),
            api_key,
        })
    }

    fn build_body(&self, params: &InferenceRequest) -> Value {
        let mut all_messages = Vec::new();
        if let Some(sys) = &params.system {
            all_messages.push(serde_json::json!({
                "role": "system",
                "content": sys
            }));
        }
        let mut translated = Vec::new();
        for msg in &params.messages {
            super::openrouter::translate_message_to_openai(msg, &mut translated);
        }
        // Mistral requires tool_call IDs to be exactly 9 alphanumeric chars.
        // Remap any Anthropic-format IDs (toolu_*) from restored sessions.
        for msg in &mut translated {
            remap_tool_ids(msg);
        }
        // Mistral requires "name" field in tool result messages.
        // Inject it by looking up tool_call_id → name from preceding assistant messages.
        inject_tool_names(&mut translated);
        all_messages.extend(translated);

        let model = normalize_mistral_model(&params.model);
        let mut body = serde_json::json!({
            "model": model,
            "max_tokens": params.max_tokens,
            "messages": all_messages,
        });

        let tools = translate_tools(&params.tools);
        if !tools.is_empty() {
            body["tools"] = Value::Array(tools);
        }

        if params.stream {
            body["stream"] = Value::Bool(true);
        }

        body
    }

    pub async fn create_stream(
        &self,
        params: InferenceRequest,
    ) -> Result<impl futures_util::Stream<Item = Result<StreamEvent, String>>, crate::cpu::error::DriverError> {
        let mut p = params;
        p.stream = true;
        let body = self.build_body(&p);

        let resp = self
            .client
            .post(format!("{}/chat/completions", MISTRAL_API_URL))
            .header("Authorization", format!("Bearer {}", self.api_key))
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
            super::openrouter::parse_sse_chunk(json_str, &mut tc, &mut u)
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
}

/// Inject the "name" field into tool-role messages for Mistral.
/// Mistral requires `{"role": "tool", "name": "...", "tool_call_id": "...", "content": "..."}`.
/// The name is looked up from the preceding assistant message's tool_calls array.
fn inject_tool_names(messages: &mut [Value]) {
    // Build a map of tool_call_id → function name from assistant tool_calls
    let mut id_to_name: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for msg in messages.iter() {
        if let Some(tool_calls) = msg.get("tool_calls").and_then(|v| v.as_array()) {
            for tc in tool_calls {
                if let (Some(id), Some(name)) = (
                    tc.get("id").and_then(|v| v.as_str()),
                    tc.get("function").and_then(|f| f.get("name")).and_then(|v| v.as_str()),
                ) {
                    id_to_name.insert(id.to_string(), name.to_string());
                }
            }
        }
    }
    // Inject "name" into tool-role messages
    for msg in messages.iter_mut() {
        if msg.get("role").and_then(|v| v.as_str()) == Some("tool")
            && let Some(tc_id) = msg.get("tool_call_id").and_then(|v| v.as_str())
                && let Some(name) = id_to_name.get(tc_id) {
                    msg["name"] = Value::String(name.clone());
                }
    }
}

/// Remap tool_call IDs to Mistral-compatible format (9 alphanumeric chars).
/// Anthropic uses `toolu_` + 24 chars; Mistral requires `[a-zA-Z0-9]{9}`.
/// Uses a deterministic hash so the same input ID always maps to the same output.
fn remap_tool_ids(msg: &mut Value) {
    // Remap tool_call_id in tool-role messages (tool results)
    if let Some(id) = msg.get("tool_call_id").and_then(|v| v.as_str()).map(|s| s.to_string())
        && (id.len() != 9 || !id.chars().all(|c| c.is_ascii_alphanumeric())) {
            msg["tool_call_id"] = Value::String(to_mistral_id(&id));
        }
    // Remap tool_calls[].id in assistant messages
    if let Some(tool_calls) = msg.get_mut("tool_calls").and_then(|v| v.as_array_mut()) {
        for tc in tool_calls {
            if let Some(id) = tc.get("id").and_then(|v| v.as_str()).map(|s| s.to_string())
                && (id.len() != 9 || !id.chars().all(|c| c.is_ascii_alphanumeric())) {
                    tc["id"] = Value::String(to_mistral_id(&id));
                }
        }
    }
}

/// Deterministic 9-char alphanumeric ID from any input string.
fn to_mistral_id(id: &str) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    id.hash(&mut hasher);
    let hash = hasher.finish();
    // Encode as base36 (0-9, a-z), take 9 chars
    let chars: Vec<char> = "0123456789abcdefghijklmnopqrstuvwxyz".chars().collect();
    let mut result = String::with_capacity(9);
    let mut h = hash;
    for _ in 0..9 {
        result.push(chars[(h % 36) as usize]);
        h /= 36;
    }
    result
}

/// Translate our tool schemas (Anthropic format) to OpenAI function-calling format.
/// Delegates to shared `translate_tools_openai` (→850).
fn translate_tools(tools: &[Value]) -> Vec<Value> {
    super::translate_tools_openai(tools)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cpu::anthropic::{ContentBlock, Message};

    #[test]
    fn normalize_devstral_medium() {
        assert_eq!(normalize_mistral_model("devstral-medium"), "devstral-medium-2507");
    }

    #[test]
    fn normalize_passthrough() {
        // Models that the API already accepts should pass through unchanged.
        assert_eq!(normalize_mistral_model("devstral-2512"), "devstral-2512");
        assert_eq!(normalize_mistral_model("devstral-small-latest"), "devstral-small-latest");
        assert_eq!(normalize_mistral_model("codestral-2508"), "codestral-2508");
        assert_eq!(normalize_mistral_model("mistral-large-latest"), "mistral-large-latest");
    }

    #[test]
    fn build_body_normalizes_model() {
        let client = MistralClient {
            client: reqwest::Client::new(),
            api_key: "test".into(),
        };
        let params = InferenceRequest {
            model: "devstral-medium".into(),
            max_tokens: 1024,
            messages: vec![],
            system: None,
            tools: vec![],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert_eq!(body["model"], "devstral-medium-2507");
    }

    #[test]
    fn build_body_basic() {
        let client = MistralClient {
            client: reqwest::Client::new(),
            api_key: "test".into(),
        };
        let params = InferenceRequest {
            model: "mistral-large-latest".into(),
            max_tokens: 1024,
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "hello".into() }],
                model: None,
            }],
            system: Some("You are helpful.".into()),
            tools: vec![],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert_eq!(body["model"], "mistral-large-latest");
        assert_eq!(body["max_tokens"], 1024);
        let msgs = body["messages"].as_array().unwrap();
        assert_eq!(msgs[0]["role"], "system");
        assert_eq!(msgs[1]["role"], "user");
    }

    #[test]
    fn build_body_no_prefix_stripping() {
        let client = MistralClient {
            client: reqwest::Client::new(),
            api_key: "test".into(),
        };
        let params = InferenceRequest {
            model: "mistral-large-latest".into(),
            max_tokens: 100,
            messages: vec![],
            system: None,
            tools: vec![],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert_eq!(body["model"], "mistral-large-latest");
    }

    #[test]
    fn to_mistral_id_format() {
        let id = to_mistral_id("toolu_012yjfGEerbbPPFYwCT8Mm26");
        assert_eq!(id.len(), 9);
        assert!(id.chars().all(|c| c.is_ascii_alphanumeric()), "id must be alphanumeric: {id}");
    }

    #[test]
    fn to_mistral_id_deterministic() {
        let a = to_mistral_id("toolu_abc");
        let b = to_mistral_id("toolu_abc");
        assert_eq!(a, b);
    }

    #[test]
    fn to_mistral_id_distinct() {
        let a = to_mistral_id("toolu_abc");
        let b = to_mistral_id("toolu_xyz");
        assert_ne!(a, b);
    }

    #[test]
    fn remap_tool_ids_tool_message() {
        let mut msg = serde_json::json!({
            "role": "tool",
            "tool_call_id": "toolu_012yjfGEerbbPPFYwCT8Mm26",
            "content": "result"
        });
        remap_tool_ids(&mut msg);
        let remapped = msg["tool_call_id"].as_str().unwrap();
        assert_eq!(remapped.len(), 9);
        assert!(remapped.chars().all(|c| c.is_ascii_alphanumeric()));
    }

    #[test]
    fn remap_tool_ids_assistant_tool_calls() {
        let mut msg = serde_json::json!({
            "role": "assistant",
            "tool_calls": [{"id": "toolu_abc123def456", "function": {"name": "shell"}}]
        });
        remap_tool_ids(&mut msg);
        let remapped = msg["tool_calls"][0]["id"].as_str().unwrap();
        assert_eq!(remapped.len(), 9);
    }

    #[test]
    fn remap_tool_ids_already_valid() {
        let mut msg = serde_json::json!({
            "role": "tool",
            "tool_call_id": "abc123def",
            "content": "ok"
        });
        remap_tool_ids(&mut msg);
        // 9-char alphanumeric IDs should pass through unchanged
        assert_eq!(msg["tool_call_id"], "abc123def");
    }

    #[test]
    fn build_body_with_tools() {
        let client = MistralClient {
            client: reqwest::Client::new(),
            api_key: "test".into(),
        };
        let tool = serde_json::json!({
            "name": "shell",
            "description": "run a command",
            "input_schema": { "type": "object", "properties": { "cmd": { "type": "string" } }, "required": ["cmd"] }
        });
        let params = InferenceRequest {
            model: "mistral-large-latest".into(),
            max_tokens: 100,
            messages: vec![],
            system: None,
            tools: vec![tool],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);
        let tools = body["tools"].as_array().unwrap();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["type"], "function");
        assert_eq!(tools[0]["function"]["name"], "shell");
    }
}
