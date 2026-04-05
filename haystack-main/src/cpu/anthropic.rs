use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::time::Duration;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

pub struct AnthropicClient {
    client: reqwest::Client,
    api_key: String,
    base_url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: String,
    pub content: Vec<ContentBlock>,
    /// →903: Model that produced this message (internal attribution only).
    /// Skipped during serialization — not an API field.
    #[serde(skip)]
    pub model: Option<String>,
}

/// →903: Manual Default so `Message { role, content, ..Default::default() }` works in tests
/// without requiring ContentBlock to implement Default.
impl Default for Message {
    fn default() -> Self {
        Self { role: String::new(), content: Vec::new(), model: None }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ImageSource {
    #[serde(rename = "type")]
    pub source_type: String,
    pub media_type: String,
    pub data: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ContentBlock {
    Text { text: String },
    ToolUse { id: String, name: String, input: Value },
    ToolResult { tool_use_id: String, content: String, #[serde(default, skip_serializing_if = "std::ops::Not::not")] is_error: bool },
    /// Base64-encoded image. Serializes as Anthropic's image block format:
    /// `{"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}`
    Image { source: ImageSource },
    /// →949: Extended thinking block — model's internal reasoning chain.
    /// Only present when `thinking` is enabled in the request.
    Thinking { thinking: String },
    /// →948: Citation block — grounded reference to source content.
    /// Contains cited text and source location.
    #[serde(rename = "cite")]
    Citation { cited_text: String, #[serde(default)] source: Value },
    /// →950: Web search result block — server-side web search results.
    #[serde(rename = "web_search_tool_result")]
    WebSearchResult { #[serde(default)] search_results: Vec<Value> },
}

/// A context_management edit that was applied server-side.
#[derive(Debug, Clone)]
pub struct AppliedEdit {
    pub edit_type: String,
    /// Number of items cleared (tool_uses or thinking_turns)
    pub cleared_count: u64,
    /// Input tokens freed by this edit
    pub cleared_input_tokens: u64,
}

#[derive(Debug, Clone)]
pub struct ApiResponse {
    pub content: Vec<ContentBlock>,
    pub stop_reason: Option<String>,
    pub usage: Usage,
    /// Context management edits applied server-side (empty if none)
    pub applied_edits: Vec<AppliedEdit>,
}

#[derive(Debug, Clone, Default, serde::Serialize, serde::Deserialize)]
pub struct Usage {
    /// Uncached input tokens (full price)
    pub input_tokens: u64,
    /// Cached input tokens read from cache (10% price)
    #[serde(default)]
    pub cache_read_tokens: u64,
    /// Cache creation tokens (125% price)
    #[serde(default)]
    pub cache_create_tokens: u64,
    pub output_tokens: u64,
    /// Real cost in USD (populated by OpenRouter; 0.0 for direct API calls)
    #[serde(default)]
    pub cost_usd: f64,
}

#[derive(Debug, Clone)]
pub enum StreamEvent {
    MessageStart { usage: Usage },
    TextDelta(String),
    ToolUseStart { id: String, name: String },
    ToolInputDelta(String),
    ContentBlockStop,
    /// Metadata from message_delta — carries stop_reason + output token counts
    /// but does NOT terminate the stream (more content blocks may follow).
    MessageDelta { stop_reason: String, usage: Usage, applied_edits: Vec<AppliedEdit> },
    /// Actual stream terminator — emitted on message_stop SSE event.
    MessageStop { stop_reason: String, usage: Usage },
    /// SSE keepalive — API connection is alive, model is thinking.
    Ping,
    /// →949: Extended thinking delta — model's internal reasoning arriving incrementally.
    ThinkingDelta(String),
}

/// Model metadata returned by the GET /v1/models endpoint.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelInfo {
    pub id: String,
    pub display_name: String,
    pub created_at: Option<String>,
}

/// Metadata for a file uploaded via the Files API (beta: files-api-2025-04-14).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileInfo {
    pub id: String,
    pub filename: String,
    pub size_bytes: u64,
}

/// Re-export from cpu/mod.rs (→849: InferenceRequest alias removed).
pub use crate::cpu::InferenceRequest;
pub use crate::cpu::ClaudeOptions;

// ---------------------------------------------------------------------------
// Batch API types (→727)
// ---------------------------------------------------------------------------

/// A single request within a batch submission.
#[derive(Debug, Clone)]
pub struct BatchRequest {
    /// Caller-assigned identifier for correlating results back to requests.
    pub custom_id: String,
    /// The create-message parameters for this request.
    pub params: InferenceRequest,
}

/// Top-level result from GET /v1/messages/batches/{batch_id}.
#[derive(Debug, Clone)]
pub struct BatchResult {
    /// The batch identifier returned by the API.
    pub id: String,
    /// Processing status: "in_progress", "ended", "canceling", "canceled", "expired", etc.
    pub status: String,
    /// Individual results — only populated once the results JSONL is fetched.
    pub results: Vec<BatchResultItem>,
    /// URL to download results JSONL (populated when status is "ended").
    pub results_url: Option<String>,
}

/// One item from a completed batch.
#[derive(Debug, Clone)]
pub struct BatchResultItem {
    /// The custom_id from the originating BatchRequest.
    pub custom_id: String,
    /// The API response for this request.
    pub result: ApiResponse,
}

impl Default for InferenceRequest {
    fn default() -> Self {
        Self {
            model: String::new(),
            max_tokens: 4096,
            system: None,
            messages: Vec::new(),
            tools: Vec::new(),
            tool_choice: None,
            stream: false,
            claude: ClaudeOptions::default(),
        }
    }
}

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

impl AnthropicClient {
    /// Create a new client. Resolves API key through kernel secret management:
    /// 1. BYO vault (OSTK_SECRET_CMD)  2. Platform keychain  3. Env var
    pub fn new() -> Result<Self, crate::cpu::error::DriverError> {
        let api_key = crate::commands::secret::resolve_secret("ANTHROPIC_API_KEY")
            .map_err(|_| crate::cpu::error::DriverError::MissingApiKey {
                provider: "anthropic".into(),
                key_name: "ANTHROPIC_API_KEY".into(),
            })?;
        Ok(Self {
            client: reqwest::Client::builder()
                .connect_timeout(Duration::from_secs(10))
                .timeout(Duration::from_secs(120))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new()),
            api_key,
            base_url: "https://api.anthropic.com".to_string(),
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

    fn build_body(&self, params: &InferenceRequest) -> Value {
        let mut body = serde_json::json!({
            "model": params.model,
            "max_tokens": params.max_tokens,
            "messages": params.messages,
        });
        // →813/→823: Send system as content-block array with cache_control
        // breakpoints when prompt caching is active (Claude) or when
        // preload_context is present. This avoids re-tokenizing the system
        // prompt on every turn — Anthropic caches up to the last marked block.
        if let Some(sys) = &params.system {
            if params.claude.preload_context.is_empty() && params.claude.cache_control.is_none() {
                // No caching, no preload — plain string is fine
                body["system"] = Value::String(sys.clone());
            } else if params.claude.preload_context.is_empty() {
                // →813: No preload blocks, but cache_control is set — wrap
                // the system prompt in a content block so the cache marker
                // actually takes effect (plain strings can't carry it).
                let cache_marker = params.claude.cache_control.clone().unwrap();
                body["system"] = serde_json::json!([{
                    "type": "text",
                    "text": sys,
                    "cache_control": cache_marker,
                }]);
            } else {
                let cache_marker = params.claude.cache_control.clone()
                    .unwrap_or_else(|| serde_json::json!({"type": "ephemeral"}));
                let mut blocks: Vec<Value> = Vec::new();
                // Base system prompt — cacheable
                blocks.push(serde_json::json!({
                    "type": "text",
                    "text": sys,
                    "cache_control": cache_marker,
                }));
                // Preloaded context blocks — each gets a cache breakpoint
                for (i, ctx) in params.claude.preload_context.iter().enumerate() {
                    let mut block = serde_json::json!({
                        "type": "text",
                        "text": ctx,
                    });
                    // Last preload block gets the cache breakpoint (Anthropic
                    // caches everything up to the last marked block)
                    if i == params.claude.preload_context.len() - 1 {
                        block["cache_control"] = cache_marker.clone();
                    }
                    blocks.push(block);
                }
                body["system"] = Value::Array(blocks);
            }
        }
        if !params.tools.is_empty() {
            body["tools"] = Value::Array(params.tools.clone());
        }
        if let Some(tc) = &params.tool_choice {
            body["tool_choice"] = tc.clone();
        }
        if params.stream {
            body["stream"] = Value::Bool(true);
        }
        if let Some(cm) = &params.claude.context_management {
            body["context_management"] = cm.clone();
        }
        if let Some(cache_control) = &params.claude.cache_control {
            body["cache_control"] = cache_control.clone();
        }
        if let Some(speed) = &params.claude.speed {
            body["speed"] = Value::String(speed.clone());
        }
        // →949: Extended thinking — enables model's internal reasoning chain
        if let Some(thinking) = &params.claude.thinking {
            body["thinking"] = thinking.clone();
        }
        // →948: Citations — grounded references to source content
        if params.claude.citations {
            body["citations"] = serde_json::json!({"enabled": true});
        }
        body
    }

    /// Build the body for count_tokens — same shape as a messages request
    /// but without stream, context_management, or cache_control.
    fn build_count_body(&self, params: &InferenceRequest) -> Value {
        let mut body = serde_json::json!({
            "model": params.model,
            "messages": params.messages,
        });
        // →813/→823: Mirror build_body system format for accurate token counting
        if let Some(sys) = &params.system {
            if params.claude.preload_context.is_empty() && params.claude.cache_control.is_none() {
                body["system"] = Value::String(sys.clone());
            } else if params.claude.preload_context.is_empty() {
                // →813: Cache-active but no preload — send as array to match build_body
                body["system"] = serde_json::json!([{
                    "type": "text",
                    "text": sys,
                }]);
            } else {
                let mut blocks: Vec<Value> = vec![serde_json::json!({
                    "type": "text",
                    "text": sys,
                })];
                for ctx in &params.claude.preload_context {
                    blocks.push(serde_json::json!({
                        "type": "text",
                        "text": ctx,
                    }));
                }
                body["system"] = Value::Array(blocks);
            }
        }
        if !params.tools.is_empty() {
            body["tools"] = Value::Array(params.tools.clone());
        }
        body
    }

    // -- token counting ---------------------------------------------------

    /// Count the input tokens for a set of create parameters without
    /// making a full API call. Calls POST /v1/messages/count_tokens.
    pub async fn count_tokens(&self, params: &InferenceRequest) -> Result<u64, crate::cpu::error::DriverError> {
        let body = self.build_count_body(params);
        let resp = self.client
            .post(format!("{}/v1/messages/count_tokens", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("content-type", "application/json")
            .json(&body)
            .send()
            .await
            .map_err(|e| crate::cpu::error::DriverError::StreamError(format!("count_tokens request failed: {e}")))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(crate::cpu::error::DriverError::ApiError {
                status: status.as_u16(),
                body: format!("count_tokens: {msg}"),
            });
        }

        let raw: Value = resp.json().await.map_err(|e| crate::cpu::error::DriverError::StreamError(format!("count_tokens json parse: {e}")))?;
        raw.get("input_tokens")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| crate::cpu::error::DriverError::StreamError("count_tokens: missing input_tokens in response".into()))
    }

    // -- models -----------------------------------------------------------

    /// List available models. Calls GET /v1/models.
    pub async fn list_models(&self) -> Result<Vec<ModelInfo>, crate::cpu::error::DriverError> {
        let resp = self.client
            .get(format!("{}/v1/models", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .send()
            .await
            .map_err(|e| crate::cpu::error::DriverError::StreamError(format!("list_models request failed: {e}")))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(crate::cpu::error::DriverError::ApiError {
                status: status.as_u16(),
                body: format!("list_models: {msg}"),
            });
        }

        let raw: Value = resp.json().await.map_err(|e| crate::cpu::error::DriverError::StreamError(format!("list_models json parse: {e}")))?;
        parse_models_response(&raw).map_err(crate::cpu::error::DriverError::StreamError)
    }

    // -- Files API (beta) -------------------------------------------------

    /// Upload a file via POST /v1/files (multipart/form-data).
    /// Requires the `files-api-2025-04-14` beta header.
    pub async fn upload_file(&self, path: &std::path::Path) -> Result<FileInfo, String> {
        let filename = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("file")
            .to_string();

        let file_bytes = tokio::fs::read(path)
            .await
            .map_err(|e| format!("failed to read file for upload: {e}"))?;

        let file_size = file_bytes.len() as u64;

        let file_part = reqwest::multipart::Part::bytes(file_bytes)
            .file_name(filename.clone())
            .mime_str("application/octet-stream")
            .map_err(|e| format!("mime error: {e}"))?;

        let form = reqwest::multipart::Form::new()
            .part("file", file_part);

        let resp = self
            .client
            .post(format!("{}/v1/files", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("anthropic-beta", "files-api-2025-04-14")
            .multipart(form)
            .send()
            .await
            .map_err(|e| format!("upload_file request failed: {e}"))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(format!("upload_file API error {status}: {msg}"));
        }

        let raw: Value = resp
            .json()
            .await
            .map_err(|e| format!("upload_file json parse: {e}"))?;

        let id = raw
            .get("id")
            .and_then(|v| v.as_str())
            .ok_or("upload_file: missing id in response")?
            .to_string();

        let size_bytes = raw
            .get("size_bytes")
            .and_then(|v| v.as_u64())
            .unwrap_or(file_size);

        Ok(FileInfo {
            id,
            filename,
            size_bytes,
        })
    }

    /// Retrieve file metadata via GET /v1/files/{file_id}.
    /// Requires the `files-api-2025-04-14` beta header.
    pub async fn get_file(&self, file_id: &str) -> Result<FileInfo, String> {
        let resp = self
            .client
            .get(format!("{}/v1/files/{file_id}", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("anthropic-beta", "files-api-2025-04-14")
            .send()
            .await
            .map_err(|e| format!("get_file request failed: {e}"))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(format!("get_file API error {status}: {msg}"));
        }

        let raw: Value = resp
            .json()
            .await
            .map_err(|e| format!("get_file json parse: {e}"))?;

        let id = raw
            .get("id")
            .and_then(|v| v.as_str())
            .ok_or("get_file: missing id in response")?
            .to_string();
        let filename = raw
            .get("filename")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let size_bytes = raw
            .get("size_bytes")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);

        Ok(FileInfo {
            id,
            filename,
            size_bytes,
        })
    }

    // -- non-streaming ----------------------------------------------------

    /// Build the anthropic-beta header from Agentfile BETA directives.
    /// Maps short names to full beta header slugs.
    ///
    /// When `context_management` is `Some`, the required beta headers are
    /// auto-injected based on which edit types are present in the edits array:
    ///   - `compact_20260112` edits  -> `compact-2026-01-12`
    ///   - `clear_tool_uses_20250919` / `clear_thinking_20251015` -> `context-management-2025-06-27`
    fn beta_header(params: &InferenceRequest) -> Option<String> {
        let mut slugs: Vec<String> = params.claude.betas.iter().map(|b| match b.as_str() {
            "context-management" => "context-management-2025-06-27".to_string(),
            "compact"            => "compact-2026-01-12".to_string(),
            "fast-mode"          => "fast-mode-2026-02-01".to_string(),
            "token-counting"     => "token-counting-2024-11-01".to_string(),
            "code-execution"     => "code-execution-2025-05-22".to_string(),
            "files-api"          => "files-api-2025-04-14".to_string(),
            "extended-cache-ttl"    => "extended-cache-ttl-2025-04-11".to_string(),
            "output-128k"           => "output-128k-2025-02-19".to_string(),
            "context-1m"            => "context-1m-2025-08-07".to_string(),
            "token-efficient-tools" => "token-efficient-tools-2025-02-19".to_string(),
            "prompt-priorities"     => "prompt-priorities".to_string(),
            "citations"             => "citations-2025-01-15".to_string(),
            "web-search"            => "web-search-2025-03-05".to_string(),
            other                   => other.to_string(), // passthrough full slug
        }).collect();

        // Auto-inject required beta headers based on context_management edits
        if let Some(cm) = &params.claude.context_management
            && let Some(edits) = cm.get("edits").and_then(|e| e.as_array()) {
                let has_compact = edits.iter().any(|e| {
                    e.get("type").and_then(|t| t.as_str()) == Some("compact_20260112")
                });
                let has_editing = edits.iter().any(|e| {
                    let t = e.get("type").and_then(|t| t.as_str()).unwrap_or("");
                    t == "clear_tool_uses_20250919" || t == "clear_thinking_20251015"
                });
                if has_compact && !slugs.iter().any(|s| s == "compact-2026-01-12") {
                    slugs.push("compact-2026-01-12".to_string());
                }
                if has_editing && !slugs.iter().any(|s| s == "context-management-2025-06-27") {
                    slugs.push("context-management-2025-06-27".to_string());
                }
            }

        // →944: Always inject token-efficient-tools — free token savings, no behavior change.
        if !slugs.iter().any(|s| s.contains("token-efficient-tools")) {
            slugs.push("token-efficient-tools-2025-02-19".to_string());
        }

        if slugs.is_empty() { return None; }
        Some(slugs.join(","))
    }

    pub async fn create(&self, params: InferenceRequest) -> Result<ApiResponse, String> {
        let body = self.build_body(&params);
        let beta = Self::beta_header(&params);
        let mut req = self.client
            .post(format!("{}/v1/messages", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("content-type", "application/json");
        if let Some(b) = &beta { req = req.header("anthropic-beta", b); }
        let resp = req.json(&body)
            .send()
            .await
            .map_err(|e| format!("request failed: {e}"))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(format!("API error {status}: {msg}"));
        }

        let raw: Value = resp.json().await.map_err(|e| format!("json parse: {e}"))?;
        parse_response(&raw)
    }

    // -- streaming --------------------------------------------------------

    pub async fn create_stream(
        &self,
        params: InferenceRequest,
    ) -> Result<impl futures_util::Stream<Item = Result<StreamEvent, String>>, crate::cpu::error::DriverError> {
        let mut p = params;
        p.stream = true;
        let body = self.build_body(&p);

        let beta = Self::beta_header(&p);
        let mut req = self.client
            .post(format!("{}/v1/messages", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("content-type", "application/json");
        if let Some(b) = &beta { req = req.header("anthropic-beta", b); }
        let resp = req.json(&body)
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

        Ok(futures_util::stream::unfold(
            (byte_stream, String::new()),
            |(mut stream, mut buf)| async move {
                loop {
                    // Try to extract a complete line from the buffer.
                    if let Some(pos) = buf.find('\n') {
                        let line = buf[..pos].trim().to_string();
                        buf = buf[pos + 1..].to_string();

                        if line.is_empty() { continue; }

                        if let Some(json_str) = line.strip_prefix("data: ") {
                            if json_str == "[DONE]" {
                                return None;
                            }
                            match parse_sse_event(json_str) {
                                Some(Ok(ev)) => return Some((Ok(ev), (stream, buf))),
                                Some(Err(e)) => return Some((Err(e), (stream, buf))),
                                None => continue,
                            }
                        }
                        // skip non-data lines (e.g. `event:` lines)
                        continue;
                    }

                    // Need more data from the network.
                    match stream.next().await {
                        Some(Ok(bytes)) => {
                            buf.push_str(&String::from_utf8_lossy(&bytes));
                        }
                        Some(Err(e)) => {
                            return Some((Err(format!("stream read error: {e}")), (stream, buf)));
                        }
                        None => return None, // stream ended
                    }
                }
            },
        ))
    }

    // -- batch API (→727) -------------------------------------------------

    /// Submit a batch of message requests for asynchronous processing.
    ///
    /// Returns the batch ID for subsequent polling via `get_batch()`.
    /// POST /v1/messages/batches
    pub async fn create_batch(&self, requests: Vec<BatchRequest>) -> Result<String, String> {
        let batch_requests: Vec<Value> = requests
            .iter()
            .map(|r| {
                let body = self.build_body(&r.params);
                serde_json::json!({
                    "custom_id": r.custom_id,
                    "params": body,
                })
            })
            .collect();

        let payload = serde_json::json!({ "requests": batch_requests });

        let resp = self
            .client
            .post(format!("{}/v1/messages/batches", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("content-type", "application/json")
            .json(&payload)
            .send()
            .await
            .map_err(|e| format!("create_batch request failed: {e}"))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(format!("create_batch API error {status}: {msg}"));
        }

        let raw: Value = resp
            .json()
            .await
            .map_err(|e| format!("create_batch json parse: {e}"))?;
        raw.get("id")
            .and_then(|v| v.as_str())
            .map(String::from)
            .ok_or_else(|| "create_batch: missing id in response".to_string())
    }

    /// Poll a batch for its current status and (when ended) its results.
    ///
    /// GET /v1/messages/batches/{batch_id}
    pub async fn get_batch(&self, batch_id: &str) -> Result<BatchResult, String> {
        let resp = self
            .client
            .get(format!("{}/v1/messages/batches/{batch_id}", self.base_url))
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .send()
            .await
            .map_err(|e| format!("get_batch request failed: {e}"))?;

        let status = resp.status();
        if !status.is_success() {
            let err = resp.text().await.unwrap_or_default();
            let msg = crate::cpu::extract_api_error_message(&err);
            return Err(format!("get_batch API error {status}: {msg}"));
        }

        let raw: Value = resp
            .json()
            .await
            .map_err(|e| format!("get_batch json parse: {e}"))?;
        parse_batch_result(&raw)
    }
}

// ---------------------------------------------------------------------------
// Parsers
// ---------------------------------------------------------------------------

/// Parse applied_edits from a context_management response object.
fn parse_applied_edits(raw: &Value) -> Vec<AppliedEdit> {
    raw.get("context_management")
        .and_then(|cm| cm.get("applied_edits"))
        .and_then(|ae| ae.as_array())
        .map(|arr| arr.iter().filter_map(|e| {
            let edit_type = e.get("type")?.as_str()?.to_string();
            let cleared_count = e.get("cleared_tool_uses")
                .or_else(|| e.get("cleared_thinking_turns"))
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            let cleared_input_tokens = e.get("cleared_input_tokens")
                .and_then(|v| v.as_u64())
                .unwrap_or(0);
            Some(AppliedEdit { edit_type, cleared_count, cleared_input_tokens })
        }).collect())
        .unwrap_or_default()
}

fn parse_response(raw: &Value) -> Result<ApiResponse, String> {
    let content = raw.get("content")
        .and_then(|v| v.as_array())
        .ok_or("missing content array")?
        .iter()
        .filter_map(parse_content_block)
        .collect();

    let stop_reason = raw.get("stop_reason").and_then(|v| v.as_str()).map(String::from);

    let usage = raw.get("usage").map(|u| Usage {
        input_tokens: u.get("input_tokens").and_then(|v| v.as_u64()).unwrap_or(0),
        cache_read_tokens: u.get("cache_read_input_tokens").and_then(|v| v.as_u64()).unwrap_or(0),
        cache_create_tokens: u.get("cache_creation_input_tokens").and_then(|v| v.as_u64()).unwrap_or(0),
        output_tokens: u.get("output_tokens").and_then(|v| v.as_u64()).unwrap_or(0),
        ..Default::default()
    }).unwrap_or_default();

    let applied_edits = parse_applied_edits(raw);

    Ok(ApiResponse { content, stop_reason, usage, applied_edits })
}

fn parse_models_response(raw: &Value) -> Result<Vec<ModelInfo>, String> {
    let data = raw.get("data")
        .and_then(|v| v.as_array())
        .ok_or("list_models: missing data array in response")?;

    Ok(data.iter().filter_map(|m| {
        let id = m.get("id")?.as_str()?.to_string();
        let display_name = m.get("display_name")
            .and_then(|v| v.as_str())
            .unwrap_or(&id)
            .to_string();
        let created_at = m.get("created_at").and_then(|v| v.as_str()).map(String::from);
        Some(ModelInfo { id, display_name, created_at })
    }).collect())
}

fn parse_content_block(b: &Value) -> Option<ContentBlock> {
    match b.get("type")?.as_str()? {
        "text" => Some(ContentBlock::Text {
            text: b.get("text")?.as_str()?.to_string(),
        }),
        "tool_use" => Some(ContentBlock::ToolUse {
            id: b.get("id")?.as_str()?.to_string(),
            name: b.get("name")?.as_str()?.to_string(),
            input: b.get("input").cloned().unwrap_or(Value::Null),
        }),
        "image" => {
            let src = b.get("source")?;
            Some(ContentBlock::Image {
                source: ImageSource {
                    source_type: src.get("type").and_then(|v| v.as_str()).unwrap_or("base64").to_string(),
                    media_type: src.get("media_type")?.as_str()?.to_string(),
                    data: src.get("data")?.as_str()?.to_string(),
                },
            })
        }
        // →949: Extended thinking block
        "thinking" => Some(ContentBlock::Thinking {
            thinking: b.get("thinking").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        }),
        // →948: Citation block
        "cite" => Some(ContentBlock::Citation {
            cited_text: b.get("cited_text").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            source: b.get("source").cloned().unwrap_or(Value::Null),
        }),
        // →950: Web search result block
        "web_search_tool_result" => Some(ContentBlock::WebSearchResult {
            search_results: b.get("search_results")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default(),
        }),
        _ => None,
    }
}

fn parse_sse_event(json_str: &str) -> Option<Result<StreamEvent, String>> {
    let v: Value = match serde_json::from_str(json_str) {
        Ok(v) => v,
        Err(e) => return Some(Err(format!("SSE json parse: {e}"))),
    };
    let event_type = v.get("type")?.as_str()?;

    match event_type {
        "content_block_start" => {
            let cb = v.get("content_block")?;
            match cb.get("type")?.as_str()? {
                "text" => None, // nothing to emit yet; text arrives via deltas
                "tool_use" => Some(Ok(StreamEvent::ToolUseStart {
                    id: cb.get("id")?.as_str()?.to_string(),
                    name: cb.get("name")?.as_str()?.to_string(),
                })),
                "thinking" => None, // thinking text arrives via deltas
                _ => None,
            }
        }
        "content_block_delta" => {
            let delta = v.get("delta")?;
            match delta.get("type")?.as_str()? {
                "text_delta" => {
                    let text = delta.get("text")?.as_str()?.to_string();
                    Some(Ok(StreamEvent::TextDelta(text)))
                }
                "input_json_delta" => {
                    let partial = delta.get("partial_json")?.as_str()?.to_string();
                    Some(Ok(StreamEvent::ToolInputDelta(partial)))
                }
                // →949: thinking deltas arrive as "thinking_delta" type
                "thinking_delta" => {
                    let thinking = delta.get("thinking")?.as_str()?.to_string();
                    Some(Ok(StreamEvent::ThinkingDelta(thinking)))
                }
                _ => None,
            }
        }
        "content_block_stop" => Some(Ok(StreamEvent::ContentBlockStop)),
        "message_delta" => {
            let delta = v.get("delta")?;
            let stop_reason = delta.get("stop_reason")
                .and_then(|s| s.as_str())
                .unwrap_or("end_turn")
                .to_string();
            let usage_obj = v.get("usage");
            // message_delta carries the FINAL token counts (per API docs)
            // Keep cache tokens separate for accurate cost calculation
            let usage = Usage {
                input_tokens: usage_obj.and_then(|u| u.get("input_tokens")).and_then(|v| v.as_u64()).unwrap_or(0),
                cache_read_tokens: usage_obj.and_then(|u| u.get("cache_read_input_tokens")).and_then(|v| v.as_u64()).unwrap_or(0),
                cache_create_tokens: usage_obj.and_then(|u| u.get("cache_creation_input_tokens")).and_then(|v| v.as_u64()).unwrap_or(0),
                output_tokens: usage_obj
                    .and_then(|u| u.get("output_tokens"))
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0),
                ..Default::default()
            };
            let applied_edits = parse_applied_edits(&v);
            // →944: message_delta is NOT a stream terminator — more content blocks
            // (e.g. tool_use) may follow. Emit MessageDelta to capture metadata
            // without breaking the consume_stream loop.
            Some(Ok(StreamEvent::MessageDelta { stop_reason, usage, applied_edits }))
        }
        "message_start" => {
            let usage_obj = v.get("message").and_then(|m| m.get("usage"));
            let input_tokens = usage_obj.and_then(|u| u.get("input_tokens")).and_then(|t| t.as_u64()).unwrap_or(0);
            let cache_read = usage_obj.and_then(|u| u.get("cache_read_input_tokens")).and_then(|t| t.as_u64()).unwrap_or(0);
            let cache_create = usage_obj.and_then(|u| u.get("cache_creation_input_tokens")).and_then(|t| t.as_u64()).unwrap_or(0);
            if input_tokens + cache_read + cache_create > 0 {
                Some(Ok(StreamEvent::MessageStart { usage: Usage {
                    input_tokens,
                    cache_read_tokens: cache_read,
                    cache_create_tokens: cache_create,
                    ..Default::default()
                }}))
            } else {
                None
            }
        }
        // →944: message_stop is the actual stream terminator.
        "message_stop" => Some(Ok(StreamEvent::MessageStop {
            stop_reason: String::new(),
            usage: Usage::default(),
        })),
        "ping" => None,
        _ => None,
    }
}

/// Parse a batch result from the GET /v1/messages/batches/{id} response.
///
/// The top-level JSON contains `id`, `processing_status`, and optionally
/// `results_url`. Individual results must be fetched separately from the
/// results JSONL endpoint — this parser only handles the batch metadata.
fn parse_batch_result(raw: &Value) -> Result<BatchResult, String> {
    let id = raw
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or("get_batch: missing id in response")?
        .to_string();

    let status = raw
        .get("processing_status")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

    let results_url = raw
        .get("results_url")
        .and_then(|v| v.as_str())
        .map(String::from);

    // Individual results come from the results JSONL; this level only
    // carries the batch envelope.
    Ok(BatchResult {
        id,
        status,
        results: Vec::new(),
        results_url,
    })
}

/// Parse a single line from the batch results JSONL into a BatchResultItem.
///
/// Each line has the shape:
/// ```json
/// {"custom_id": "...", "result": { "type": "succeeded", "message": { ...ApiResponse... } }}
/// ```
pub fn parse_batch_result_line(raw: &Value) -> Option<BatchResultItem> {
    let custom_id = raw.get("custom_id")?.as_str()?.to_string();
    let result_obj = raw.get("result")?;
    let message = result_obj.get("message")?;
    let response = parse_response(message).ok()?;
    Some(BatchResultItem {
        custom_id,
        result: response,
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_block_serde_text() {
        let block = ContentBlock::Text { text: "hello".into() };
        let json = serde_json::to_value(&block).unwrap();
        assert_eq!(json["type"], "text");
        assert_eq!(json["text"], "hello");

        let back: ContentBlock = serde_json::from_value(json).unwrap();
        match back {
            ContentBlock::Text { text } => assert_eq!(text, "hello"),
            _ => panic!("expected Text"),
        }
    }

    #[test]
    fn content_block_serde_tool_use() {
        let block = ContentBlock::ToolUse {
            id: "tu_1".into(),
            name: "Bash".into(),
            input: serde_json::json!({"command": "ls"}),
        };
        let json = serde_json::to_value(&block).unwrap();
        assert_eq!(json["type"], "tool_use");
        assert_eq!(json["name"], "Bash");
        assert_eq!(json["input"]["command"], "ls");

        let back: ContentBlock = serde_json::from_value(json).unwrap();
        match back {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "tu_1");
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls");
            }
            _ => panic!("expected ToolUse"),
        }
    }

    #[test]
    fn content_block_serde_tool_result() {
        let block = ContentBlock::ToolResult {
            tool_use_id: "tu_1".into(),
            content: "output".into(), is_error: false,
        };
        let json = serde_json::to_value(&block).unwrap();
        assert_eq!(json["type"], "tool_result");
        assert_eq!(json["tool_use_id"], "tu_1");

        let back: ContentBlock = serde_json::from_value(json).unwrap();
        match back {
            ContentBlock::ToolResult { tool_use_id, content, .. } => {
                assert_eq!(tool_use_id, "tu_1");
                assert_eq!(content, "output");
            }
            _ => panic!("expected ToolResult"),
        }
    }

    #[test]
    fn content_block_serde_thinking() {
        let block = ContentBlock::Thinking { thinking: "Let me reason about this...".into() };
        let json = serde_json::to_value(&block).unwrap();
        assert_eq!(json["type"], "thinking");
        assert_eq!(json["thinking"], "Let me reason about this...");
        let back: ContentBlock = serde_json::from_value(json).unwrap();
        match back {
            ContentBlock::Thinking { thinking } => assert_eq!(thinking, "Let me reason about this..."),
            _ => panic!("expected Thinking"),
        }
    }

    #[test]
    fn content_block_parse_thinking() {
        let raw = serde_json::json!({"type": "thinking", "thinking": "internal monologue"});
        let block = parse_content_block(&raw);
        assert!(block.is_some());
        match block.unwrap() {
            ContentBlock::Thinking { thinking } => assert_eq!(thinking, "internal monologue"),
            _ => panic!("expected Thinking"),
        }
    }

    #[test]
    fn content_block_parse_citation() {
        let raw = serde_json::json!({
            "type": "cite",
            "cited_text": "the relevant passage",
            "source": {"type": "document", "document_index": 0, "start_char": 10, "end_char": 30}
        });
        let block = parse_content_block(&raw);
        assert!(block.is_some());
        match block.unwrap() {
            ContentBlock::Citation { cited_text, source } => {
                assert_eq!(cited_text, "the relevant passage");
                assert_eq!(source["type"], "document");
            }
            _ => panic!("expected Citation"),
        }
    }

    #[test]
    fn content_block_parse_web_search_result() {
        let raw = serde_json::json!({
            "type": "web_search_tool_result",
            "search_results": [
                {"title": "Rust 1.85", "url": "https://blog.rust-lang.org/"},
                {"title": "Rust Release Notes", "url": "https://doc.rust-lang.org/"}
            ]
        });
        let block = parse_content_block(&raw);
        assert!(block.is_some());
        match block.unwrap() {
            ContentBlock::WebSearchResult { search_results } => {
                assert_eq!(search_results.len(), 2);
                assert_eq!(search_results[0]["title"], "Rust 1.85");
            }
            _ => panic!("expected WebSearchResult"),
        }
    }

    #[test]
    fn build_body_includes_thinking_when_set() {
        let client = AnthropicClient::with_config("test-key".into(), "https://test".into());
        let params = InferenceRequest {
            model: "claude-opus-4".into(),
            max_tokens: 8096,
            system: Some("test".into()),
            messages: vec![],
            tools: vec![],
            tool_choice: None,
            stream: false,
            claude: ClaudeOptions {
                thinking: Some(serde_json::json!({"type": "enabled", "budget_tokens": 10000})),
                ..Default::default()
            },
        };
        let body = client.build_body(&params);
        assert_eq!(body["thinking"]["type"], "enabled");
        assert_eq!(body["thinking"]["budget_tokens"], 10000);
    }

    #[test]
    fn build_body_includes_citations_when_enabled() {
        let client = AnthropicClient::with_config("test-key".into(), "https://test".into());
        let params = InferenceRequest {
            model: "claude-opus-4".into(),
            max_tokens: 8096,
            system: Some("test".into()),
            messages: vec![],
            tools: vec![],
            tool_choice: None,
            stream: false,
            claude: ClaudeOptions {
                citations: true,
                ..Default::default()
            },
        };
        let body = client.build_body(&params);
        assert_eq!(body["citations"]["enabled"], true);
    }

    #[test]
    fn build_body_omits_thinking_and_citations_when_unset() {
        let client = AnthropicClient::with_config("test-key".into(), "https://test".into());
        let params = InferenceRequest {
            model: "claude-opus-4".into(),
            max_tokens: 8096,
            system: Some("test".into()),
            messages: vec![],
            tools: vec![],
            tool_choice: None,
            stream: false,
            claude: ClaudeOptions::default(),
        };
        let body = client.build_body(&params);
        assert!(body.get("thinking").is_none());
        assert!(body.get("citations").is_none());
    }

    #[test]
    fn parse_sse_thinking_delta() {
        let json = r#"{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"step 1: analyze"}}"#;
        let event = parse_sse_event(json);
        assert!(event.is_some());
        match event.unwrap().unwrap() {
            StreamEvent::ThinkingDelta(t) => assert_eq!(t, "step 1: analyze"),
            other => panic!("expected ThinkingDelta, got: {other:?}"),
        }
    }

    #[test]
    fn create_params_builds_correct_body() {
        let client = AnthropicClient::with_config("test-key".into(), "https://test".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 8096,
            system: Some("be helpful".into()),
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "hello".into() }],
                model: None,
            }],
            tools: vec![serde_json::json!({
                "name": "Bash",
                "description": "Run a command",
                "input_schema": { "type": "object", "properties": { "command": { "type": "string" } } }
            })],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);

        assert_eq!(body["model"], "claude-sonnet-4-5-20250514");
        assert_eq!(body["max_tokens"], 8096);
        assert_eq!(body["system"], "be helpful");
        assert!(body["messages"].is_array());
        assert_eq!(body["messages"][0]["role"], "user");
        assert!(body["tools"].is_array());
        assert_eq!(body["tools"][0]["name"], "Bash");
        assert!(body.get("stream").is_none()); // stream=false omits the key
    }

    #[test]
    fn create_params_omits_optional_fields() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: None,
            messages: vec![],
            tools: vec![],
            stream: false,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert!(body.get("system").is_none());
        assert!(body.get("tools").is_none());
        assert!(body.get("stream").is_none());
        assert!(body.get("context_management").is_none());
        assert!(body.get("cache_control").is_none());
        assert!(body.get("speed").is_none());
    }

    #[test]
    fn build_body_includes_speed_when_set() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-opus-4-6".into(),
            max_tokens: 4096,
            claude: ClaudeOptions { speed: Some("fast".into()), ..Default::default() },
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert_eq!(body["speed"], "fast");
    }

    #[test]
    fn build_body_omits_speed_when_none() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-opus-4-6".into(),
            max_tokens: 4096,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert!(body.get("speed").is_none());
    }

    #[test]
    fn parse_sse_text_delta() {
        let json = r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::TextDelta(t))) => assert_eq!(t, "Hello"),
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_tool_use_start() {
        let json = r#"{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"tu_abc","name":"Bash"}}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::ToolUseStart { id, name })) => {
                assert_eq!(id, "tu_abc");
                assert_eq!(name, "Bash");
            }
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_input_json_delta() {
        let json = r#"{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"com"}}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::ToolInputDelta(s))) => assert_eq!(s, "{\"com"),
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_content_block_stop() {
        let json = r#"{"type":"content_block_stop","index":0}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::ContentBlockStop)) => {}
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_message_delta() {
        let json = r#"{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::MessageDelta { stop_reason, usage, applied_edits })) => {
                assert_eq!(stop_reason, "end_turn");
                assert_eq!(usage.output_tokens, 42);
                assert!(applied_edits.is_empty());
            }
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_message_start_returns_none() {
        let json = r#"{"type":"message_start","message":{"id":"msg_1","role":"assistant"}}"#;
        assert!(parse_sse_event(json).is_none());
    }

    #[test]
    fn parse_sse_message_stop_emits_event() {
        let json = r#"{"type":"message_stop"}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::MessageStop { .. })) => {}
            other => panic!("expected MessageStop, got: {other:?}"),
        }
    }

    #[test]
    fn parse_response_full() {
        let raw = serde_json::json!({
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [
                { "type": "text", "text": "I'll run a command." },
                { "type": "tool_use", "id": "tu_1", "name": "Bash", "input": { "command": "ls" } }
            ],
            "stop_reason": "tool_use",
            "usage": { "input_tokens": 100, "output_tokens": 50 }
        });
        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.content.len(), 2);
        assert_eq!(resp.stop_reason.as_deref(), Some("tool_use"));
        assert_eq!(resp.usage.input_tokens, 100);
        assert_eq!(resp.usage.output_tokens, 50);

        match &resp.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "I'll run a command."),
            _ => panic!("expected text block"),
        }
        match &resp.content[1] {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "tu_1");
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls");
            }
            _ => panic!("expected tool_use block"),
        }
    }

    // -----------------------------------------------------------------------
    // 1. Message serialization round-trip — all ContentBlock variants
    // -----------------------------------------------------------------------

    #[test]
    fn message_roundtrip_all_content_block_variants() {
        let msg = Message {
            role: "assistant".into(),
            content: vec![
                ContentBlock::Text { text: "I will run a command and report back.".into() },
                ContentBlock::ToolUse {
                    id: "toolu_01A".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "ls -la"}),
                },
                ContentBlock::ToolResult {
                    tool_use_id: "toolu_01A".into(),
                    content: "drwxr-xr-x 5 user staff 160 Mar 16 10:00 .".into(), is_error: false,
                },
            ],
            model: None,
        };

        let json = serde_json::to_value(&msg).unwrap();

        // Verify top-level structure
        assert_eq!(json["role"], "assistant");
        assert!(json["content"].is_array());
        let content = json["content"].as_array().unwrap();
        assert_eq!(content.len(), 3);

        // Verify each block has correct type tag
        assert_eq!(content[0]["type"], "text");
        assert_eq!(content[0]["text"], "I will run a command and report back.");

        assert_eq!(content[1]["type"], "tool_use");
        assert_eq!(content[1]["id"], "toolu_01A");
        assert_eq!(content[1]["name"], "Bash");
        assert_eq!(content[1]["input"]["command"], "ls -la");

        assert_eq!(content[2]["type"], "tool_result");
        assert_eq!(content[2]["tool_use_id"], "toolu_01A");
        assert_eq!(content[2]["content"], "drwxr-xr-x 5 user staff 160 Mar 16 10:00 .");

        // Round-trip back to Message
        let back: Message = serde_json::from_value(json).unwrap();
        assert_eq!(back.role, "assistant");
        assert_eq!(back.content.len(), 3);
        match &back.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "I will run a command and report back."),
            _ => panic!("expected Text"),
        }
        match &back.content[1] {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "toolu_01A");
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls -la");
            }
            _ => panic!("expected ToolUse"),
        }
        match &back.content[2] {
            ContentBlock::ToolResult { tool_use_id, content, .. } => {
                assert_eq!(tool_use_id, "toolu_01A");
                assert!(content.contains("drwxr-xr-x"));
            }
            _ => panic!("expected ToolResult"),
        }
    }

    // -----------------------------------------------------------------------
    // 2. ToolResult serialization matches Anthropic API format exactly
    // -----------------------------------------------------------------------

    #[test]
    fn tool_result_serializes_to_anthropic_api_format() {
        // CRITICAL: The Anthropic API expects tool_result blocks with exactly:
        //   {"type": "tool_result", "tool_use_id": "...", "content": "..."}
        // If the field names are wrong (e.g. "id" instead of "tool_use_id"),
        // the API will reject the request.
        let block = ContentBlock::ToolResult {
            tool_use_id: "toolu_abc123".into(),
            content: "file contents here".into(), is_error: false,
        };
        let json_str = serde_json::to_string(&block).unwrap();
        let json: Value = serde_json::from_str(&json_str).unwrap();

        // Exact field names the Anthropic API requires
        assert_eq!(json["type"], "tool_result");
        assert_eq!(json["tool_use_id"], "toolu_abc123");
        assert_eq!(json["content"], "file contents here");

        // Must NOT have spurious fields
        let obj = json.as_object().unwrap();
        let keys: Vec<&String> = obj.keys().collect();
        assert_eq!(keys.len(), 3, "tool_result should have exactly 3 fields: type, tool_use_id, content; got {keys:?}");
        assert!(obj.contains_key("type"));
        assert!(obj.contains_key("tool_use_id"));
        assert!(obj.contains_key("content"));
    }

    #[test]
    fn tool_use_serializes_to_anthropic_api_format() {
        let block = ContentBlock::ToolUse {
            id: "toolu_xyz".into(),
            name: "Read".into(),
            input: serde_json::json!({"file_path": "/tmp/test.rs"}),
        };
        let json_str = serde_json::to_string(&block).unwrap();
        let json: Value = serde_json::from_str(&json_str).unwrap();

        assert_eq!(json["type"], "tool_use");
        assert_eq!(json["id"], "toolu_xyz");
        assert_eq!(json["name"], "Read");
        assert_eq!(json["input"]["file_path"], "/tmp/test.rs");

        let obj = json.as_object().unwrap();
        assert_eq!(obj.len(), 4, "tool_use should have exactly 4 fields: type, id, name, input");
    }

    // -----------------------------------------------------------------------
    // 3. Full message with mixed blocks serializes for API consumption
    // -----------------------------------------------------------------------

    #[test]
    fn messages_vec_serializes_as_api_expects() {
        // Simulate a multi-turn conversation as sent to the API
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "Read /tmp/foo.txt".into() }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![
                    ContentBlock::Text { text: "I'll read that file.".into() },
                    ContentBlock::ToolUse {
                        id: "toolu_1".into(),
                        name: "Read".into(),
                        input: serde_json::json!({"file_path": "/tmp/foo.txt"}),
                    },
                ],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "toolu_1".into(),
                    content: "hello world".into(), is_error: false,
                }],
                model: None,
            },
        ];

        let json = serde_json::to_value(&messages).unwrap();
        let arr = json.as_array().unwrap();
        assert_eq!(arr.len(), 3);

        // User message
        assert_eq!(arr[0]["role"], "user");
        assert_eq!(arr[0]["content"][0]["type"], "text");

        // Assistant message with tool_use
        assert_eq!(arr[1]["role"], "assistant");
        assert_eq!(arr[1]["content"][0]["type"], "text");
        assert_eq!(arr[1]["content"][1]["type"], "tool_use");
        assert_eq!(arr[1]["content"][1]["id"], "toolu_1");

        // User message with tool_result
        assert_eq!(arr[2]["role"], "user");
        assert_eq!(arr[2]["content"][0]["type"], "tool_result");
        assert_eq!(arr[2]["content"][0]["tool_use_id"], "toolu_1");
        assert_eq!(arr[2]["content"][0]["content"], "hello world");
    }

    // -----------------------------------------------------------------------
    // 4. parse_response error cases
    // -----------------------------------------------------------------------

    #[test]
    fn parse_response_missing_content_returns_error() {
        let raw = serde_json::json!({
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "stop_reason": "end_turn"
        });
        let err = parse_response(&raw).unwrap_err();
        assert!(err.contains("missing content"), "error should mention missing content: {err}");
    }

    #[test]
    fn parse_response_content_not_array_returns_error() {
        let raw = serde_json::json!({
            "content": "not an array",
            "stop_reason": "end_turn"
        });
        let err = parse_response(&raw).unwrap_err();
        assert!(err.contains("missing content"), "error: {err}");
    }

    #[test]
    fn parse_response_empty_content_array_is_ok() {
        let raw = serde_json::json!({
            "content": [],
            "stop_reason": "end_turn",
            "usage": { "input_tokens": 5, "output_tokens": 0 }
        });
        let resp = parse_response(&raw).unwrap();
        assert!(resp.content.is_empty());
        assert_eq!(resp.usage.input_tokens, 5);
    }

    #[test]
    fn parse_response_missing_usage_defaults_to_zero() {
        let raw = serde_json::json!({
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn"
        });
        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.usage.input_tokens, 0);
        assert_eq!(resp.usage.output_tokens, 0);
    }

    #[test]
    fn parse_response_unknown_content_block_type_skipped() {
        // If the API returns a content block type we don't know about, we skip it
        let raw = serde_json::json!({
            "content": [
                {"type": "text", "text": "known"},
                {"type": "some_future_block_type", "data": "opaque"},
                {"type": "text", "text": "also known"},
            ],
            "stop_reason": "end_turn",
            "usage": { "input_tokens": 10, "output_tokens": 20 }
        });
        let resp = parse_response(&raw).unwrap();
        // Only the 2 text blocks should survive
        assert_eq!(resp.content.len(), 2);
        match &resp.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "known"),
            _ => panic!("expected text"),
        }
        match &resp.content[1] {
            ContentBlock::Text { text } => assert_eq!(text, "also known"),
            _ => panic!("expected text"),
        }
    }

    // -----------------------------------------------------------------------
    // 5. build_body includes stream flag when true
    // -----------------------------------------------------------------------

    #[test]
    fn build_body_includes_stream_when_true() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: None,
            messages: vec![],
            tools: vec![],
            stream: true,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert_eq!(body["stream"], true);
    }

    // -----------------------------------------------------------------------
    // 6. SSE parsing edge cases
    // -----------------------------------------------------------------------

    #[test]
    fn parse_sse_invalid_json_returns_error() {
        let json = "{not valid json}}}";
        match parse_sse_event(json) {
            Some(Err(e)) => assert!(e.contains("SSE json parse"), "error: {e}"),
            other => panic!("expected parse error, got: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_missing_type_returns_none() {
        let json = r#"{"delta":{"text":"orphaned"}}"#;
        assert!(parse_sse_event(json).is_none());
    }

    #[test]
    fn parse_sse_unknown_event_type_returns_none() {
        let json = r#"{"type":"server_heartbeat","interval":30}"#;
        assert!(parse_sse_event(json).is_none());
    }

    #[test]
    fn parse_sse_message_delta_with_tool_use_stop() {
        // The API can return stop_reason "tool_use" on message_delta
        let json = r#"{"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":100}}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::MessageDelta { stop_reason, usage, .. })) => {
                assert_eq!(stop_reason, "tool_use");
                assert_eq!(usage.output_tokens, 100);
            }
            other => panic!("unexpected: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_ping_returns_none() {
        let json = r#"{"type":"ping"}"#;
        assert!(parse_sse_event(json).is_none());
    }

    #[test]
    fn parse_sse_message_start_extracts_input_tokens() {
        let json = r#"{"type":"message_start","message":{"id":"msg_1","role":"assistant","usage":{"input_tokens":500,"output_tokens":0}}}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::MessageStart { usage })) => {
                assert_eq!(usage.input_tokens, 500);
                assert_eq!(usage.cache_read_tokens, 0);
                assert_eq!(usage.cache_create_tokens, 0);
            }
            other => panic!("expected MessageStart, got: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_message_start_zero_tokens_returns_none() {
        // When input_tokens is 0, returns None (same as before)
        let json = r#"{"type":"message_start","message":{"id":"msg_1","role":"assistant","usage":{"input_tokens":0,"output_tokens":0}}}"#;
        assert!(parse_sse_event(json).is_none());
    }

    #[test]
    fn parse_sse_message_delta_has_output_tokens() {
        let json = r#"{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":150}}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::MessageDelta { usage, .. })) => {
                assert_eq!(usage.output_tokens, 150);
            }
            other => panic!("expected MessageDelta, got: {other:?}"),
        }
    }

    #[test]
    fn parse_sse_content_block_start_text_returns_none() {
        // Text block starts don't emit events — text arrives via deltas
        let json = r#"{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}"#;
        assert!(parse_sse_event(json).is_none());
    }

    // -----------------------------------------------------------------------
    // 7. ContentBlock deserialization from raw API JSON
    // -----------------------------------------------------------------------

    #[test]
    fn content_block_deserializes_from_api_json() {
        // Simulate exactly what the Anthropic API returns
        let api_text = r#"{"type":"text","text":"Hello, how can I help?"}"#;
        let block: ContentBlock = serde_json::from_str(api_text).unwrap();
        match block {
            ContentBlock::Text { text } => assert_eq!(text, "Hello, how can I help?"),
            _ => panic!("expected Text"),
        }

        let api_tool_use = r#"{"type":"tool_use","id":"toolu_01A","name":"Bash","input":{"command":"pwd"}}"#;
        let block: ContentBlock = serde_json::from_str(api_tool_use).unwrap();
        match block {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "toolu_01A");
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "pwd");
            }
            _ => panic!("expected ToolUse"),
        }
    }

    #[test]
    fn tool_result_with_empty_content_serializes_correctly() {
        let block = ContentBlock::ToolResult {
            tool_use_id: "toolu_1".into(),
            content: "".into(), is_error: false,
        };
        let json = serde_json::to_value(&block).unwrap();
        assert_eq!(json["type"], "tool_result");
        assert_eq!(json["tool_use_id"], "toolu_1");
        assert_eq!(json["content"], "");
    }

    #[test]
    fn tool_result_with_special_chars_roundtrips() {
        let output = "error: \"unexpected token\" at line 42\n\ttab\there\n\\backslash";
        let block = ContentBlock::ToolResult {
            tool_use_id: "toolu_esc".into(),
            content: output.into(), is_error: false,
        };
        let json_str = serde_json::to_string(&block).unwrap();
        let back: ContentBlock = serde_json::from_str(&json_str).unwrap();
        match back {
            ContentBlock::ToolResult { content, .. } => assert_eq!(content, output),
            _ => panic!("expected ToolResult"),
        }
    }

    // -----------------------------------------------------------------------
    // 8. Context management and cache control in build_body
    // -----------------------------------------------------------------------

    #[test]
    fn build_body_includes_context_management_with_compaction() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-opus-4-6".into(),
            max_tokens: 1024,
            claude: ClaudeOptions { context_management: Some(serde_json::json!({
                "edits": [
                    {
                        "type": "compact_20260112",
                        "trigger": { "type": "input_tokens", "value": 100000 }
                    }
                ]
            })), ..Default::default() },

            ..Default::default()
        };
        let body = client.build_body(&params);
        let edits = body["context_management"]["edits"].as_array().unwrap();
        assert_eq!(edits.len(), 1);
        assert_eq!(edits[0]["type"], "compact_20260112");
        assert_eq!(edits[0]["trigger"]["type"], "input_tokens");
        assert_eq!(edits[0]["trigger"]["value"], 100000);
    }

    #[test]
    fn build_body_includes_context_management_with_editing() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-opus-4-6".into(),
            max_tokens: 1024,
            claude: ClaudeOptions { context_management: Some(serde_json::json!({
                "edits": [
                    {
                        "type": "clear_tool_uses_20250919",
                        "keep": { "type": "tool_uses", "value": 5 }
                    },
                    {
                        "type": "clear_thinking_20251015",
                        "keep": { "type": "thinking_turns", "value": 2 }
                    }
                ]
            })), ..Default::default() },

            ..Default::default()
        };
        let body = client.build_body(&params);
        let edits = body["context_management"]["edits"].as_array().unwrap();
        assert_eq!(edits.len(), 2);
        assert_eq!(edits[0]["type"], "clear_tool_uses_20250919");
        assert_eq!(edits[0]["keep"]["type"], "tool_uses");
        assert_eq!(edits[0]["keep"]["value"], 5);
        assert_eq!(edits[1]["type"], "clear_thinking_20251015");
        assert_eq!(edits[1]["keep"]["type"], "thinking_turns");
        assert_eq!(edits[1]["keep"]["value"], 2);
    }

    // -----------------------------------------------------------------------
    // applied_edits parsing
    // -----------------------------------------------------------------------

    #[test]
    fn parse_applied_edits_from_response() {
        let raw: Value = serde_json::json!({
            "id": "msg_1",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "context_management": {
                "applied_edits": [
                    {
                        "type": "clear_tool_uses_20250919",
                        "cleared_tool_uses": 8,
                        "cleared_input_tokens": 50000
                    }
                ]
            }
        });
        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.applied_edits.len(), 1);
        assert_eq!(resp.applied_edits[0].edit_type, "clear_tool_uses_20250919");
        assert_eq!(resp.applied_edits[0].cleared_count, 8);
        assert_eq!(resp.applied_edits[0].cleared_input_tokens, 50000);
    }

    #[test]
    fn parse_applied_edits_clear_thinking() {
        let raw: Value = serde_json::json!({
            "id": "msg_1",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "context_management": {
                "applied_edits": [
                    {
                        "type": "clear_thinking_20251015",
                        "cleared_thinking_turns": 3,
                        "cleared_input_tokens": 15000
                    }
                ]
            }
        });
        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.applied_edits.len(), 1);
        assert_eq!(resp.applied_edits[0].edit_type, "clear_thinking_20251015");
        assert_eq!(resp.applied_edits[0].cleared_count, 3);
        assert_eq!(resp.applied_edits[0].cleared_input_tokens, 15000);
    }

    #[test]
    fn parse_applied_edits_multiple() {
        let raw: Value = serde_json::json!({
            "id": "msg_1",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "context_management": {
                "applied_edits": [
                    {
                        "type": "clear_thinking_20251015",
                        "cleared_thinking_turns": 2,
                        "cleared_input_tokens": 10000
                    },
                    {
                        "type": "clear_tool_uses_20250919",
                        "cleared_tool_uses": 5,
                        "cleared_input_tokens": 30000
                    }
                ]
            }
        });
        let resp = parse_response(&raw).unwrap();
        assert_eq!(resp.applied_edits.len(), 2);
        assert_eq!(resp.applied_edits[0].edit_type, "clear_thinking_20251015");
        assert_eq!(resp.applied_edits[1].edit_type, "clear_tool_uses_20250919");
    }

    #[test]
    fn parse_applied_edits_empty_when_no_context_management() {
        let raw: Value = serde_json::json!({
            "id": "msg_1",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 50}
        });
        let resp = parse_response(&raw).unwrap();
        assert!(resp.applied_edits.is_empty());
    }

    #[test]
    fn parse_sse_message_delta_with_applied_edits() {
        let json = r#"{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42},"context_management":{"applied_edits":[{"type":"clear_tool_uses_20250919","cleared_tool_uses":5,"cleared_input_tokens":25000}]}}"#;
        match parse_sse_event(json) {
            Some(Ok(StreamEvent::MessageDelta { applied_edits, .. })) => {
                assert_eq!(applied_edits.len(), 1);
                assert_eq!(applied_edits[0].edit_type, "clear_tool_uses_20250919");
                assert_eq!(applied_edits[0].cleared_count, 5);
                assert_eq!(applied_edits[0].cleared_input_tokens, 25000);
            }
            other => panic!("expected MessageDelta with applied_edits, got: {other:?}"),
        }
    }

    #[test]
    fn build_body_includes_cache_control_with_extended_ttl() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            claude: ClaudeOptions {
                cache_control: Some(serde_json::json!({"type": "ephemeral", "ttl": "1h"})),
                ..Default::default()
            },
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert_eq!(body["cache_control"]["type"], "ephemeral");
        assert_eq!(body["cache_control"]["ttl"], "1h");
    }

    #[test]
    fn build_body_omits_context_management_and_cache_control_when_none() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert!(body.get("context_management").is_none());
        assert!(body.get("cache_control").is_none());
    }

    // -----------------------------------------------------------------------
    // →823: preload_context in build_body
    // -----------------------------------------------------------------------

    #[test]
    fn build_body_system_as_string_without_preload() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: Some("You are an agent.".into()),
            claude: Default::default(),
            ..Default::default()
        };
        let body = client.build_body(&params);
        // Without preload, system is a plain string
        assert_eq!(body["system"], "You are an agent.");
    }

    #[test]
    fn build_body_system_as_cached_block_without_preload() {
        // →813: When cache_control is set but preload_context is empty,
        // system should be a content-block array (not a plain string)
        // so the cache_control marker takes effect.
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: Some("You are an agent.".into()),
            claude: ClaudeOptions {
                cache_control: Some(serde_json::json!({"type": "ephemeral", "ttl": "1h"})),
                ..Default::default()
            },
            ..Default::default()
        };
        let body = client.build_body(&params);
        // System should be an array with one content block
        let sys = body["system"].as_array().expect("system should be array when cache_control set");
        assert_eq!(sys.len(), 1);
        assert_eq!(sys[0]["type"], "text");
        assert_eq!(sys[0]["text"], "You are an agent.");
        assert_eq!(sys[0]["cache_control"]["type"], "ephemeral");
        assert_eq!(sys[0]["cache_control"]["ttl"], "1h");
    }

    #[test]
    fn build_body_count_mirrors_cached_system_format() {
        // →813: count_body should mirror build_body's system array format
        // when cache_control is set (without the cache markers themselves).
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: Some("You are an agent.".into()),
            claude: ClaudeOptions {
                cache_control: Some(serde_json::json!({"type": "ephemeral"})),
                ..Default::default()
            },
            ..Default::default()
        };
        let body = client.build_count_body(&params);
        // count_body should send system as array to match build_body format
        let sys = body["system"].as_array().expect("count system should be array");
        assert_eq!(sys.len(), 1);
        assert_eq!(sys[0]["type"], "text");
        assert_eq!(sys[0]["text"], "You are an agent.");
    }

    #[test]
    fn build_body_system_as_array_with_preload() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: Some("You are an agent.".into()),
            claude: ClaudeOptions { preload_context: vec![
                "# Boot state\n## 5 open needles".into(),
                "# .language\n:deploy | 3 | kernel".into(),
            ], ..Default::default() },

            ..Default::default()
        };
        let body = client.build_body(&params);
        // With preload, system becomes an array of content blocks
        let sys = body["system"].as_array().expect("system should be array");
        assert_eq!(sys.len(), 3, "1 base + 2 preload blocks");

        // First block: agentfile prompt with cache_control
        assert_eq!(sys[0]["type"], "text");
        assert_eq!(sys[0]["text"], "You are an agent.");
        assert_eq!(sys[0]["cache_control"]["type"], "ephemeral");

        // Second block: boot context (no cache_control — not last)
        assert_eq!(sys[1]["type"], "text");
        assert!(sys[1]["text"].as_str().unwrap().contains("Boot state"));
        assert!(sys[1].get("cache_control").is_none());

        // Third block: language (last preload — has cache_control)
        assert_eq!(sys[2]["type"], "text");
        assert!(sys[2]["text"].as_str().unwrap().contains(".language"));
        assert_eq!(sys[2]["cache_control"]["type"], "ephemeral");
    }

    #[test]
    fn build_body_preload_single_block_gets_cache_control() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: Some("Agent.".into()),
            claude: ClaudeOptions { preload_context: vec!["# Boot\nOK".into()], ..Default::default() },
            ..Default::default()
        };
        let body = client.build_body(&params);
        let sys = body["system"].as_array().expect("system should be array");
        assert_eq!(sys.len(), 2);
        // Single preload block is the last one — gets cache_control
        assert_eq!(sys[1]["cache_control"]["type"], "ephemeral");
    }

    #[test]
    fn count_body_mirrors_system_array_with_preload() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: Some("Agent.".into()),
            claude: ClaudeOptions { preload_context: vec!["# Boot\nOK".into()], ..Default::default() },
            ..Default::default()
        };
        let body = client.build_count_body(&params);
        let sys = body["system"].as_array().expect("count body system should be array");
        assert_eq!(sys.len(), 2);
        assert_eq!(sys[0]["text"], "Agent.");
        assert!(sys[1]["text"].as_str().unwrap().contains("Boot"));
    }

    // -----------------------------------------------------------------------
    // Prompt cache stability: cache_control placement with multiple preload blocks
    // -----------------------------------------------------------------------

    #[test]
    fn cache_control_placement_with_four_preload_blocks() {
        // Anthropic caches everything from the start of the system prompt
        // up to the last cache_control breakpoint. With multiple preload
        // blocks, only the FIRST (base prompt) and LAST (final preload)
        // should carry cache_control markers. Intermediate blocks must NOT
        // have cache_control — adding one would create an extra cache
        // breakpoint that wastes write-to-cache operations.
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: Some("You are an agent.".into()),
            claude: ClaudeOptions {
                cache_control: Some(serde_json::json!({"type": "ephemeral", "ttl": "1h"})),
                preload_context: vec![
                    "# kernel registers\nidentity: test\n".into(),
                    "# working state\ndecisions: []\n".into(),
                    "# Boot state\nneedles: 5 open\n".into(),
                    "# Tool surface\nResident: :hay, :draft\n".into(),
                ],
                ..Default::default()
            },
            ..Default::default()
        };
        let body = client.build_body(&params);
        let sys = body["system"].as_array().expect("system should be array");

        // 1 base prompt + 4 preload = 5 blocks
        assert_eq!(sys.len(), 5, "should have 5 system blocks");

        // Base prompt (index 0) should have cache_control
        assert!(
            sys[0].get("cache_control").is_some(),
            "base prompt should have cache_control"
        );

        // Intermediate preload blocks (1..4) should NOT have cache_control
        for i in 1..4 {
            assert!(
                sys[i].get("cache_control").is_none(),
                "intermediate preload block {} should not have cache_control",
                i
            );
        }

        // Last preload block (index 4) should have cache_control
        assert!(
            sys[4].get("cache_control").is_some(),
            "last preload block should have cache_control"
        );
    }

    #[test]
    fn cache_control_not_on_count_body() {
        // count_tokens body should NOT carry cache_control markers.
        // Sending cache_control to count_tokens would waste cache writes.
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: Some("Agent.".into()),
            claude: ClaudeOptions {
                cache_control: Some(serde_json::json!({"type": "ephemeral", "ttl": "1h"})),
                preload_context: vec![
                    "# registers\n".into(),
                    "# tools\n".into(),
                ],
                ..Default::default()
            },
            ..Default::default()
        };
        let body = client.build_count_body(&params);
        let sys = body["system"].as_array().expect("system should be array");
        for (i, block) in sys.iter().enumerate() {
            assert!(
                block.get("cache_control").is_none(),
                "count_body system block {} should not have cache_control",
                i
            );
        }
    }

    // -----------------------------------------------------------------------
    // →732: tool_choice in build_body
    // -----------------------------------------------------------------------

    #[test]
    fn build_body_includes_tool_choice_when_set() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            tool_choice: Some(serde_json::json!({"type": "none"})),
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert_eq!(body["tool_choice"]["type"], "none");
    }

    #[test]
    fn build_body_omits_tool_choice_when_none() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            tool_choice: None,
            ..Default::default()
        };
        let body = client.build_body(&params);
        assert!(body.get("tool_choice").is_none());
    }

    // -----------------------------------------------------------------------
    // →726: count_tokens request body
    // -----------------------------------------------------------------------

    #[test]
    fn count_body_includes_model_and_messages() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 8096,
            system: Some("be helpful".into()),
            messages: vec![Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "hello".into() }],
                model: None,
            }],
            tools: vec![serde_json::json!({
                "name": "Bash",
                "description": "Run a command",
                "input_schema": { "type": "object", "properties": { "command": { "type": "string" } } }
            })],
            ..Default::default()
        };
        let body = client.build_count_body(&params);

        // Must include model, messages, system, tools
        assert_eq!(body["model"], "claude-sonnet-4-5-20250514");
        assert!(body["messages"].is_array());
        assert_eq!(body["system"], "be helpful");
        assert!(body["tools"].is_array());
        assert_eq!(body["tools"][0]["name"], "Bash");

        // Must NOT include stream, context_management, cache_control, max_tokens
        assert!(body.get("stream").is_none());
        assert!(body.get("max_tokens").is_none());
        assert!(body.get("context_management").is_none());
        assert!(body.get("cache_control").is_none());
    }

    #[test]
    fn count_body_omits_optional_fields_when_none() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            system: None,
            messages: vec![],
            tools: vec![],
            ..Default::default()
        };
        let body = client.build_count_body(&params);
        assert!(body.get("system").is_none());
        assert!(body.get("tools").is_none());
    }

    // -----------------------------------------------------------------------
    // →733: list_models response parsing
    // -----------------------------------------------------------------------

    #[test]
    fn parse_models_response_full() {
        let raw = serde_json::json!({
            "data": [
                {
                    "type": "model",
                    "id": "claude-sonnet-4-5-20250514",
                    "display_name": "Claude Sonnet 4.5",
                    "created_at": "2025-05-14T00:00:00Z"
                },
                {
                    "type": "model",
                    "id": "claude-opus-4-6",
                    "display_name": "Claude Opus 4",
                    "created_at": "2025-06-01T00:00:00Z"
                }
            ],
            "has_more": false,
            "first_id": "claude-sonnet-4-5-20250514",
            "last_id": "claude-opus-4-6"
        });
        let models = parse_models_response(&raw).unwrap();
        assert_eq!(models.len(), 2);
        assert_eq!(models[0].id, "claude-sonnet-4-5-20250514");
        assert_eq!(models[0].display_name, "Claude Sonnet 4.5");
        assert_eq!(models[0].created_at.as_deref(), Some("2025-05-14T00:00:00Z"));
        assert_eq!(models[1].id, "claude-opus-4-6");
        assert_eq!(models[1].display_name, "Claude Opus 4");
    }

    #[test]
    fn parse_models_response_empty_data() {
        let raw = serde_json::json!({ "data": [] });
        let models = parse_models_response(&raw).unwrap();
        assert!(models.is_empty());
    }

    #[test]
    fn parse_models_response_missing_data_returns_error() {
        let raw = serde_json::json!({ "error": "unauthorized" });
        let err = parse_models_response(&raw).unwrap_err();
        assert!(err.contains("missing data"), "error: {err}");
    }

    #[test]
    fn parse_models_response_missing_display_name_uses_id() {
        let raw = serde_json::json!({
            "data": [
                { "id": "claude-test-1", "type": "model" }
            ]
        });
        let models = parse_models_response(&raw).unwrap();
        assert_eq!(models.len(), 1);
        assert_eq!(models[0].id, "claude-test-1");
        assert_eq!(models[0].display_name, "claude-test-1");
        assert!(models[0].created_at.is_none());
    }

    #[test]
    fn parse_models_response_skips_entries_without_id() {
        let raw = serde_json::json!({
            "data": [
                { "display_name": "No ID Model" },
                { "id": "claude-valid", "display_name": "Valid" }
            ]
        });
        let models = parse_models_response(&raw).unwrap();
        assert_eq!(models.len(), 1);
        assert_eq!(models[0].id, "claude-valid");
    }

    #[test]
    fn model_info_serde_roundtrip() {
        let info = ModelInfo {
            id: "claude-sonnet-4-5-20250514".into(),
            display_name: "Claude Sonnet 4.5".into(),
            created_at: Some("2025-05-14T00:00:00Z".into()),
        };
        let json = serde_json::to_value(&info).unwrap();
        assert_eq!(json["id"], "claude-sonnet-4-5-20250514");
        assert_eq!(json["display_name"], "Claude Sonnet 4.5");
        assert_eq!(json["created_at"], "2025-05-14T00:00:00Z");

        let back: ModelInfo = serde_json::from_value(json).unwrap();
        assert_eq!(back.id, "claude-sonnet-4-5-20250514");
        assert_eq!(back.display_name, "Claude Sonnet 4.5");
        assert_eq!(back.created_at.as_deref(), Some("2025-05-14T00:00:00Z"));
    }

    // -----------------------------------------------------------------------
    // Beta header tests — these features are beta and may change
    // -----------------------------------------------------------------------

    #[test]
    fn beta_header_always_has_token_efficient_tools() {
        // Even with no explicit betas, token-efficient-tools is auto-injected
        let params = InferenceRequest::default();
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert_eq!(header, "token-efficient-tools-2025-02-19");
    }

    #[test]
    fn beta_header_short_name_maps_to_slug() {
        let params = InferenceRequest {
            claude: ClaudeOptions { betas: vec!["context-management".into()], ..Default::default() },
            ..Default::default()
        };
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert!(header.contains("context-management-2025-06-27"));
        assert!(header.contains("token-efficient-tools-2025-02-19"));
    }

    #[test]
    fn beta_header_multiple_comma_separated() {
        let params = InferenceRequest {
            claude: ClaudeOptions { betas: vec!["context-management".into(), "fast-mode".into(), "token-counting".into()], ..Default::default() },
            ..Default::default()
        };
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert!(header.contains("context-management-2025-06-27"));
        assert!(header.contains("fast-mode-2026-02-01"));
        assert!(header.contains("token-counting-2024-11-01"));
        assert!(header.contains("token-efficient-tools-2025-02-19"));
    }

    #[test]
    fn beta_header_raw_slug_passthrough() {
        let params = InferenceRequest {
            claude: ClaudeOptions { betas: vec!["some-future-beta-2027-01-01".into()], ..Default::default() },
            ..Default::default()
        };
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert!(header.contains("some-future-beta-2027-01-01"));
        assert!(header.contains("token-efficient-tools-2025-02-19"));
    }

    #[test]
    fn beta_header_all_known_slugs() {
        for (short, full) in [
            ("context-management", "context-management-2025-06-27"),
            ("compact", "compact-2026-01-12"),
            ("fast-mode", "fast-mode-2026-02-01"),
            ("token-counting", "token-counting-2024-11-01"),
            ("code-execution", "code-execution-2025-05-22"),
            ("files-api", "files-api-2025-04-14"),
            ("extended-cache-ttl", "extended-cache-ttl-2025-04-11"),
            ("output-128k", "output-128k-2025-02-19"),
            ("token-efficient-tools", "token-efficient-tools-2025-02-19"),
            ("prompt-priorities", "prompt-priorities"),
            ("citations", "citations-2025-01-15"),
            ("web-search", "web-search-2025-03-05"),
        ] {
            let params = InferenceRequest { claude: ClaudeOptions { betas: vec![short.into()], ..Default::default() }, ..Default::default() };
            let header = AnthropicClient::beta_header(&params).unwrap();
            assert!(header.contains(full), "failed for {short}: {header}");
        }
    }

    #[test]
    fn beta_header_auto_injects_compact_from_context_management() {
        // No betas in the list, but context_management has a compact edit
        let params = InferenceRequest {
            claude: ClaudeOptions { context_management: Some(serde_json::json!({
                "edits": [{ "type": "compact_20260112" }]
            })), ..Default::default() },

            ..Default::default()
        };
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert!(header.contains("compact-2026-01-12"), "header: {header}");
    }

    #[test]
    fn beta_header_auto_injects_context_management_from_clear_edits() {
        let params = InferenceRequest {
            claude: ClaudeOptions { context_management: Some(serde_json::json!({
                "edits": [{ "type": "clear_tool_uses_20250919" }]
            })), ..Default::default() },

            ..Default::default()
        };
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert!(header.contains("context-management-2025-06-27"), "header: {header}");
    }

    #[test]
    fn beta_header_auto_injects_both_from_mixed_edits() {
        let params = InferenceRequest {
            claude: ClaudeOptions { context_management: Some(serde_json::json!({
                "edits": [
                    { "type": "compact_20260112" },
                    { "type": "clear_tool_uses_20250919" }
                ]
            })), ..Default::default() },

            ..Default::default()
        };
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert!(header.contains("compact-2026-01-12"), "header: {header}");
        assert!(header.contains("context-management-2025-06-27"), "header: {header}");
    }

    #[test]
    fn beta_header_no_duplicate_when_already_in_betas() {
        // compact is already in betas list AND context_management has compact edits
        let params = InferenceRequest {
            claude: ClaudeOptions {
                betas: vec!["compact".into()],
                context_management: Some(serde_json::json!({
                    "edits": [{ "type": "compact_20260112" }]
                })),
                ..Default::default()
            },
            ..Default::default()
        };
        let header = AnthropicClient::beta_header(&params).unwrap();
        // Should appear exactly once
        assert_eq!(header.matches("compact-2026-01-12").count(), 1, "header: {header}");
    }

    #[test]
    fn beta_header_default_has_token_efficient_tools_only() {
        let params = InferenceRequest::default();
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert_eq!(header, "token-efficient-tools-2025-02-19");
    }

    #[test]
    fn beta_header_auto_injects_for_clear_thinking() {
        let params = InferenceRequest {
            claude: ClaudeOptions { context_management: Some(serde_json::json!({
                "edits": [{ "type": "clear_thinking_20251015" }]
            })), ..Default::default() },

            ..Default::default()
        };
        let header = AnthropicClient::beta_header(&params).unwrap();
        assert!(header.contains("context-management-2025-06-27"), "header: {header}");
    }

    // -----------------------------------------------------------------------
    // →728: FileInfo serialization tests
    // -----------------------------------------------------------------------

    #[test]
    fn file_info_serde_roundtrip() {
        let info = FileInfo {
            id: "file_abc123".to_string(),
            filename: "main.rs".to_string(),
            size_bytes: 4096,
        };
        let json = serde_json::to_string(&info).unwrap();
        let back: FileInfo = serde_json::from_str(&json).unwrap();
        assert_eq!(back.id, "file_abc123");
        assert_eq!(back.filename, "main.rs");
        assert_eq!(back.size_bytes, 4096);
    }

    #[test]
    fn file_info_json_structure() {
        let info = FileInfo {
            id: "file_xyz".to_string(),
            filename: "lib.rs".to_string(),
            size_bytes: 2048,
        };
        let val = serde_json::to_value(&info).unwrap();
        assert_eq!(val["id"], "file_xyz");
        assert_eq!(val["filename"], "lib.rs");
        assert_eq!(val["size_bytes"], 2048);
    }

    #[test]
    fn file_info_deserialize_from_api_shape() {
        // Simulate what the API might return (with extra fields)
        let raw = serde_json::json!({
            "id": "file_001",
            "filename": "test.py",
            "size_bytes": 512,
            "created_at": "2026-03-16T20:00:00Z"
        });
        // FileInfo ignores unknown fields by default with serde
        let info: FileInfo = serde_json::from_value(raw).unwrap();
        assert_eq!(info.id, "file_001");
        assert_eq!(info.filename, "test.py");
        assert_eq!(info.size_bytes, 512);
    }

    // -----------------------------------------------------------------------
    // →727: Batch API tests
    // -----------------------------------------------------------------------

    #[test]
    fn batch_request_serialization() {
        // Verify that BatchRequest params get serialized into the right shape
        // for the POST /v1/messages/batches payload.
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let req = BatchRequest {
            custom_id: "needle-42".into(),
            params: InferenceRequest {
                model: "claude-sonnet-4-5-20250514".into(),
                max_tokens: 4096,
                system: Some("You are a helpful agent.".into()),
                messages: vec![Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text { text: "Hello".into() }],
                    model: None,
                }],
                ..Default::default()
            },
        };

        let body = client.build_body(&req.params);
        let batch_item = serde_json::json!({
            "custom_id": req.custom_id,
            "params": body,
        });

        assert_eq!(batch_item["custom_id"], "needle-42");
        assert_eq!(batch_item["params"]["model"], "claude-sonnet-4-5-20250514");
        assert_eq!(batch_item["params"]["max_tokens"], 4096);
        assert_eq!(batch_item["params"]["system"], "You are a helpful agent.");
        assert!(batch_item["params"]["messages"].is_array());
    }

    #[test]
    fn batch_request_multiple_serialization() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let requests = [BatchRequest {
                custom_id: "req-1".into(),
                params: InferenceRequest {
                    model: "claude-sonnet-4-5-20250514".into(),
                    max_tokens: 2048,
                    messages: vec![Message {
                        role: "user".into(),
                        content: vec![ContentBlock::Text { text: "First".into() }],
                        model: None,
                    }],
                    ..Default::default()
                },
            },
            BatchRequest {
                custom_id: "req-2".into(),
                params: InferenceRequest {
                    model: "claude-sonnet-4-5-20250514".into(),
                    max_tokens: 2048,
                    messages: vec![Message {
                        role: "user".into(),
                        content: vec![ContentBlock::Text { text: "Second".into() }],
                        model: None,
                    }],
                    ..Default::default()
                },
            }];

        let batch_items: Vec<serde_json::Value> = requests
            .iter()
            .map(|r| {
                let body = client.build_body(&r.params);
                serde_json::json!({
                    "custom_id": r.custom_id,
                    "params": body,
                })
            })
            .collect();

        let payload = serde_json::json!({ "requests": batch_items });
        let arr = payload["requests"].as_array().unwrap();
        assert_eq!(arr.len(), 2);
        assert_eq!(arr[0]["custom_id"], "req-1");
        assert_eq!(arr[1]["custom_id"], "req-2");
    }

    #[test]
    fn parse_batch_result_in_progress() {
        let raw = serde_json::json!({
            "id": "batch_abc123",
            "type": "message_batch",
            "processing_status": "in_progress",
            "request_counts": {
                "processing": 5,
                "succeeded": 0,
                "errored": 0,
                "canceled": 0,
                "expired": 0
            }
        });
        let result = parse_batch_result(&raw).unwrap();
        assert_eq!(result.id, "batch_abc123");
        assert_eq!(result.status, "in_progress");
        assert!(result.results.is_empty());
        assert!(result.results_url.is_none());
    }

    #[test]
    fn parse_batch_result_ended_with_results_url() {
        let raw = serde_json::json!({
            "id": "batch_xyz789",
            "type": "message_batch",
            "processing_status": "ended",
            "results_url": "https://api.anthropic.com/v1/messages/batches/batch_xyz789/results",
            "request_counts": {
                "processing": 0,
                "succeeded": 3,
                "errored": 0,
                "canceled": 0,
                "expired": 0
            }
        });
        let result = parse_batch_result(&raw).unwrap();
        assert_eq!(result.id, "batch_xyz789");
        assert_eq!(result.status, "ended");
        assert_eq!(
            result.results_url.as_deref(),
            Some("https://api.anthropic.com/v1/messages/batches/batch_xyz789/results")
        );
        // Results are not inline — they come from the JSONL endpoint
        assert!(result.results.is_empty());
    }

    #[test]
    fn parse_batch_result_missing_id_returns_error() {
        let raw = serde_json::json!({
            "processing_status": "in_progress"
        });
        let err = parse_batch_result(&raw).unwrap_err();
        assert!(err.contains("missing id"), "error: {err}");
    }

    #[test]
    fn parse_batch_result_missing_status_defaults_unknown() {
        let raw = serde_json::json!({
            "id": "batch_no_status"
        });
        let result = parse_batch_result(&raw).unwrap();
        assert_eq!(result.status, "unknown");
    }

    #[test]
    fn parse_batch_result_line_succeeded() {
        let raw = serde_json::json!({
            "custom_id": "needle-42",
            "result": {
                "type": "succeeded",
                "message": {
                    "id": "msg_001",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        { "type": "text", "text": "Here is the result." }
                    ],
                    "stop_reason": "end_turn",
                    "usage": { "input_tokens": 50, "output_tokens": 25 }
                }
            }
        });
        let item = parse_batch_result_line(&raw).unwrap();
        assert_eq!(item.custom_id, "needle-42");
        assert_eq!(item.result.stop_reason.as_deref(), Some("end_turn"));
        assert_eq!(item.result.usage.input_tokens, 50);
        assert_eq!(item.result.usage.output_tokens, 25);
        assert_eq!(item.result.content.len(), 1);
        match &item.result.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "Here is the result."),
            _ => panic!("expected text block"),
        }
    }

    #[test]
    fn parse_batch_result_line_missing_custom_id() {
        let raw = serde_json::json!({
            "result": {
                "type": "succeeded",
                "message": {
                    "content": [{"type": "text", "text": "hi"}],
                    "stop_reason": "end_turn",
                    "usage": { "input_tokens": 1, "output_tokens": 1 }
                }
            }
        });
        assert!(parse_batch_result_line(&raw).is_none());
    }

    #[test]
    fn parse_batch_result_line_missing_message() {
        let raw = serde_json::json!({
            "custom_id": "needle-99",
            "result": {
                "type": "errored",
                "error": { "type": "server_error", "message": "Internal error" }
            }
        });
        // No "message" key -> parse_batch_result_line returns None
        assert!(parse_batch_result_line(&raw).is_none());
    }

    #[test]
    fn parse_batch_result_line_with_tool_use() {
        let raw = serde_json::json!({
            "custom_id": "needle-7",
            "result": {
                "type": "succeeded",
                "message": {
                    "id": "msg_002",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        { "type": "text", "text": "I will read the file." },
                        { "type": "tool_use", "id": "toolu_01", "name": "Read",
                          "input": { "file_path": "/tmp/test.rs" } }
                    ],
                    "stop_reason": "tool_use",
                    "usage": { "input_tokens": 100, "output_tokens": 60 }
                }
            }
        });
        let item = parse_batch_result_line(&raw).unwrap();
        assert_eq!(item.custom_id, "needle-7");
        assert_eq!(item.result.stop_reason.as_deref(), Some("tool_use"));
        assert_eq!(item.result.content.len(), 2);
        match &item.result.content[1] {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "toolu_01");
                assert_eq!(name, "Read");
                assert_eq!(input["file_path"], "/tmp/test.rs");
            }
            _ => panic!("expected tool_use block"),
        }
    }
    // →824: ContentBlock::Image tests

    #[test]
    fn test_content_block_image_serde() {
        let block = ContentBlock::Image {
            source: ImageSource {
                source_type: "base64".into(),
                media_type: "image/png".into(),
                data: "abc123".into(),
            },
        };
        let json = serde_json::to_value(&block).unwrap();
        assert_eq!(json["type"], "image");
        assert_eq!(json["source"]["type"], "base64");
        assert_eq!(json["source"]["media_type"], "image/png");
        assert_eq!(json["source"]["data"], "abc123");

        // Round-trip via parse_content_block (deserialization path used by API responses)
        let back = parse_content_block(&json).expect("parse_content_block should handle image");
        match back {
            ContentBlock::Image { source } => {
                assert_eq!(source.media_type, "image/png");
                assert_eq!(source.data, "abc123");
                assert_eq!(source.source_type, "base64");
            }
            _ => panic!("expected Image"),
        }
    }

    #[test]
    fn test_anthropic_image_params() {
        let client = AnthropicClient::with_config("k".into(), "https://x".into());
        let params = InferenceRequest {
            model: "claude-sonnet-4-5-20250514".into(),
            max_tokens: 1024,
            messages: vec![Message {
                role: "user".into(),
                content: vec![
                    ContentBlock::Text { text: "Describe this image.".into() },
                    ContentBlock::Image {
                        source: ImageSource {
                            source_type: "base64".into(),
                            media_type: "image/jpeg".into(),
                            data: "deadbeef".into(),
                        },
                    },
                ],
                model: None,
            }],
            ..Default::default()
        };
        let body = client.build_body(&params);
        let img = &body["messages"][0]["content"][1];
        assert_eq!(img["type"], "image");
        assert_eq!(img["source"]["type"], "base64");
        assert_eq!(img["source"]["media_type"], "image/jpeg");
        assert_eq!(img["source"]["data"], "deadbeef");
    }

    #[test]
    fn test_content_block_image_in_message() {
        // Full message with text + image roundtrips through serde
        let msg = Message {
            role: "user".into(),
            content: vec![
                ContentBlock::Text { text: "What is in this image?".into() },
                ContentBlock::Image {
                    source: ImageSource {
                        source_type: "base64".into(),
                        media_type: "image/png".into(),
                        data: "iVBORw0KGgo=".into(),
                    },
                },
            ],
            model: None,
        };
        let json = serde_json::to_value(&msg).unwrap();
        let back: Message = serde_json::from_value(json).unwrap();
        assert_eq!(back.content.len(), 2);
        match &back.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "What is in this image?"),
            _ => panic!("expected Text"),
        }
        match &back.content[1] {
            ContentBlock::Image { source } => {
                assert_eq!(source.media_type, "image/png");
                assert_eq!(source.data, "iVBORw0KGgo=");
            }
            _ => panic!("expected Image"),
        }
    }


}
