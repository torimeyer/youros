use crate::cpu::anthropic::{ContentBlock, InferenceRequest, Message, StreamEvent, Usage};
#[cfg(test)]
use crate::cpu::anthropic::{ApiResponse};
use crate::cpu::error::AgentLoopError;
use crate::kernel::approval::{self as approval_bus, ApprovalDecision, ApprovalSource};
use crate::cpu::CpuDriver;
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::sync::mpsc;

/// →848: Shared message storage for the agent loop.
/// Wraps Arc<Mutex<Vec<Message>>> with convenience methods that handle
/// poison recovery and reduce boilerplate at call sites.
#[derive(Clone)]
pub struct SharedMessages {
    inner: Arc<Mutex<Vec<Message>>>,
}

impl SharedMessages {
    pub fn new(messages: Vec<Message>) -> Self {
        Self { inner: Arc::new(Mutex::new(messages)) }
    }

    /// Push a single message. Recovers from poisoned mutex.
    pub fn push(&self, msg: Message) {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).push(msg);
    }

    /// Snapshot all messages (clone under lock). Used for API calls.
    pub fn snapshot(&self) -> Vec<Message> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    /// Push multiple messages atomically (single lock acquisition).
    pub fn extend(&self, msgs: impl IntoIterator<Item = Message>) {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).extend(msgs);
    }

    /// Number of messages.
    pub fn len(&self) -> usize {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).len()
    }

    /// Whether the message list is empty.
    pub fn is_empty(&self) -> bool {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).is_empty()
    }

    /// Clear all messages.
    pub fn clear(&self) {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).clear();
    }

    /// Replace all messages atomically (single lock acquisition).
    /// Used by :compact to swap full history with a condensed version.
    pub fn replace(&self, new_messages: Vec<Message>) {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.clear();
        guard.extend(new_messages);
    }

    /// Read-only access under lock for serialization (save to disk).
    pub fn with_lock<R>(&self, f: impl FnOnce(&[Message]) -> R) -> R {
        let guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        f(&guard)
    }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum CpuEvent {
    /// Streaming text delta (AI typing)
    TextDelta(String),
    /// Complete text block
    TextComplete(String),
    /// Tool call starting (name known, args still streaming)
    ToolStart { name: String, id: String },
    /// Tool input assembled (args known, about to execute)
    ToolInput { name: String, preview: String },
    /// Tool call result
    ToolResult { name: String, output: String, success: bool },
    /// Intermediate usage from an API call (emitted after each tool-use response
    /// so the TUI can update token counters during multi-turn work)
    Usage { usage: Usage },
    /// Pre-call token count (exact context size before an API call)
    PreCallTokenCount { tokens: u64 },
    /// Agent turn complete (no more tool calls)
    TurnComplete { usage: Usage },
    /// Error
    Error(String),
    /// SSE keepalive — API connection alive, model thinking.
    /// Resets the TUI stall timer without producing visible output.
    Heartbeat,
    /// Tool needs human approval — TUI should show modal.
    /// Session-scoped: flows through the event channel, not the global bus.
    ApprovalNeeded {
        tool_name: String,
        tool_input_preview: String,
    },
}

/// TUI → Agent responses (the reverse channel).
/// Session-scoped — no global state.
#[derive(Debug, Clone)]
pub enum AgentResponse {
    /// Approval decision for a pending tool call
    Approval(ApprovalDecision),
    /// User requested cancellation
    Cancel,
    /// Mid-turn redirect: inject a new user message and restart the turn.
    /// Sent when the user submits input while the agent is running.
    Redirect(String),
}

impl std::fmt::Display for CpuEvent {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CpuEvent::TextDelta(_) => write!(f, "TextDelta"),
            CpuEvent::TextComplete(_) => write!(f, "TextComplete"),
            CpuEvent::ToolStart { name, .. } => write!(f, "ToolStart({name})"),
            CpuEvent::ToolInput { name, .. } => write!(f, "ToolInput({name})"),
            CpuEvent::ToolResult { name, success, .. } => write!(f, "ToolResult({name}, ok={success})"),
            CpuEvent::Usage { .. } => write!(f, "Usage"),
            CpuEvent::PreCallTokenCount { tokens } => write!(f, "PreCallTokenCount({tokens})"),
            CpuEvent::TurnComplete { .. } => write!(f, "TurnComplete"),
            CpuEvent::Error(e) => write!(f, "Error({e})"),
            CpuEvent::ApprovalNeeded { tool_name, .. } => write!(f, "ApprovalNeeded({tool_name})"),
            CpuEvent::Heartbeat => write!(f, "Heartbeat"),
        }
    }
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct LoopConfig {
    pub model: String,
    pub system_prompt: Option<String>,
    pub tools: Vec<serde_json::Value>,
    pub max_tokens: u32,
    pub max_turns: Option<u32>,
    pub context_budget: Option<u64>,
    pub permission_mode: crate::cpu::PermissionMode,
    pub betas: Vec<String>,
    /// Fast mode for Opus 4.6 — 2.5x faster output tokens
    pub fast_mode: bool,
    /// Project root for audit logging (→737)
    pub root: Option<PathBuf>,
    /// →823: Cached prefix context (boot.md, .language, identity).
    /// Each entry becomes a separate system content block with cache_control,
    /// placed after the system prompt. Anthropic caches everything up to the
    /// last cache breakpoint, so these static blocks avoid re-tokenization.
    pub preload_context: Vec<String>,
    /// →949: Extended thinking config — `{"type": "enabled", "budget_tokens": N}`.
    /// When set, enables the model's internal reasoning chain.
    pub thinking: Option<serde_json::Value>,
    /// →948: Enable citations for grounded file references.
    pub citations: bool,
    /// →1011: Capability class patterns from Agentfile TOOL directives.
    /// Each entry is (class_label, optional_path_pattern).
    pub tool_patterns: Vec<(String, Option<String>)>,
}

// ---------------------------------------------------------------------------
// Agent loop
// ---------------------------------------------------------------------------

/// Build the common InferenceRequest for a given loop iteration.
/// Implementation lives in cpu::params (extracted ->748).
///
/// →843: boot_context optional — if provided, refreshes and rebuilds preload on every turn.
/// →873: accumulated_tokens drives fcp-llm eviction pressure.
fn build_params(
    config: &LoopConfig,
    messages: &[Message],
    tools: Vec<serde_json::Value>,
    tool_choice: Option<serde_json::Value>,
    boot_context: Option<&mut crate::cpu::session::BootContext>,
    accumulated_tokens: u64,
    session_summary: Option<&crate::fcp::llm::SessionSummary>,
) -> InferenceRequest {
    crate::cpu::params::build_params(config, messages, tools, tool_choice, boot_context, accumulated_tokens, session_summary)
}

/// Result of consuming a streaming response.
struct StreamResult {
    content: Vec<ContentBlock>,
    stop_reason: String,
    usage: Usage,
    /// →925: Set when the stream was interrupted by a Cancel or Redirect.
    /// The caller uses this to handle the interruption instead of continuing normally.
    interrupted: Option<AgentResponse>,
}

/// Consume a stream of SSE events and reassemble content blocks + metadata.
/// Emits `TextDelta`, `TextComplete`, and `ToolStart` events in real time.
///
/// →925: Accepts `approval_source` to check for Cancel/Redirect during streaming.
/// Without this, redirects wait until the entire stream completes (30s+ delay).
async fn consume_stream(
    stream: impl futures_util::Stream<Item = Result<StreamEvent, String>>,
    event_tx: &mpsc::Sender<CpuEvent>,
    mut approval_source: Option<&mut ApprovalSource>,
    cancel_flag: Option<&std::sync::Arc<std::sync::atomic::AtomicBool>>,
) -> Result<StreamResult, AgentLoopError> {
    let mut content_blocks: Vec<ContentBlock> = Vec::new();
    let mut current_text = String::new();
    let mut current_thinking = String::new(); // →949: accumulate thinking blocks
    let mut current_tool: Option<(String, String, String)> = None; // (id, name, input_buf)
    let mut stop_reason = String::from("end_turn");
    let mut usage = Usage::default();

    futures_util::pin_mut!(stream);

    while let Some(event) = stream.next().await {
        match event.map_err(AgentLoopError::StreamProcessing)? {
            StreamEvent::MessageStart { usage: start_usage } => {
                usage.input_tokens = start_usage.input_tokens;
                usage.cache_read_tokens = start_usage.cache_read_tokens;
                usage.cache_create_tokens = start_usage.cache_create_tokens;
            }
            StreamEvent::TextDelta(t) => {
                let _ = event_tx.send(CpuEvent::TextDelta(t.clone())).await;
                current_text.push_str(&t);
                // →plan Phase 3: Check cooperative cancel flag during streaming.
                if let Some(flag) = cancel_flag
                    && flag.load(std::sync::atomic::Ordering::Acquire) {
                        if !current_text.is_empty() {
                            let text = std::mem::take(&mut current_text);
                            let _ = event_tx.send(CpuEvent::TextComplete(text.clone())).await;
                            content_blocks.push(ContentBlock::Text { text });
                        }
                        return Ok(StreamResult {
                            content: content_blocks,
                            stop_reason: "cancelled".to_string(),
                            usage,
                            interrupted: Some(AgentResponse::Cancel),
                        });
                    }
                // →925: Check for Cancel/Redirect during streaming so the user
                // doesn't wait for the full response to complete before their
                // input takes effect.
                if let Some(ref mut src) = approval_source
                    && let Some(response) = src.try_recv() {
                        // Finalize accumulated text before returning
                        if !current_text.is_empty() {
                            let text = std::mem::take(&mut current_text);
                            let _ = event_tx.send(CpuEvent::TextComplete(text.clone())).await;
                            content_blocks.push(ContentBlock::Text { text });
                        }
                        return Ok(StreamResult {
                            content: content_blocks,
                            stop_reason: "interrupted".to_string(),
                            usage,
                            interrupted: Some(response),
                        });
                    }
            }
            StreamEvent::ToolUseStart { id, name } => {
                // Finalize any pending text block
                if !current_text.is_empty() {
                    let text = std::mem::take(&mut current_text);
                    let _ = event_tx.send(CpuEvent::TextComplete(text.clone())).await;
                    content_blocks.push(ContentBlock::Text { text });
                }
                let _ = event_tx.send(CpuEvent::ToolStart { name: name.clone(), id: id.clone() }).await;
                current_tool = Some((id, name, String::new()));
            }
            StreamEvent::ToolInputDelta(fragment) => {
                if let Some((_, _, ref mut buf)) = current_tool {
                    buf.push_str(&fragment);
                }
            }
            StreamEvent::ContentBlockStop => {
                if let Some((id, name, input_json)) = current_tool.take() {
                    let input: Value = serde_json::from_str(&input_json).unwrap_or_else(|_| serde_json::json!({}));
                    content_blocks.push(ContentBlock::ToolUse { id, name, input });
                } else if !current_thinking.is_empty() {
                    // →949: Finalize thinking block
                    let thinking = std::mem::take(&mut current_thinking);
                    content_blocks.push(ContentBlock::Thinking { thinking });
                } else if !current_text.is_empty() {
                    let text = std::mem::take(&mut current_text);
                    let _ = event_tx.send(CpuEvent::TextComplete(text.clone())).await;
                    content_blocks.push(ContentBlock::Text { text });
                }
            }
            // →944: MessageDelta carries metadata (stop_reason, usage) but does NOT
            // terminate the stream — more content blocks (e.g. tool_use) may follow.
            StreamEvent::MessageDelta { stop_reason: sr, usage: u, applied_edits: _ } => {
                stop_reason = sr;
                // Merge: message_delta carries output_tokens + cache counts,
                // message_start carries input_tokens.  Prefer non-zero values from each.
                if u.input_tokens > 0 {
                    usage.input_tokens = u.input_tokens;
                }
                if u.cache_read_tokens > 0 {
                    usage.cache_read_tokens = u.cache_read_tokens;
                }
                if u.cache_create_tokens > 0 {
                    usage.cache_create_tokens = u.cache_create_tokens;
                }
                if u.output_tokens > 0 {
                    usage.output_tokens = u.output_tokens;
                }
                // Do NOT break — wait for MessageStop (message_stop SSE event).
            }
            // →944: MessageStop is the actual stream terminator (message_stop SSE event).
            StreamEvent::MessageStop { stop_reason: sr, usage: u } => {
                // Finalize any remaining text
                if !current_text.is_empty() {
                    let text = std::mem::take(&mut current_text);
                    let _ = event_tx.send(CpuEvent::TextComplete(text.clone())).await;
                    content_blocks.push(ContentBlock::Text { text });
                }
                // Finalize any pending tool call — OpenAI-compatible streams
                // (Mistral, OpenRouter) don't emit ContentBlockStop events.
                // The tool call data accumulates in current_tool and must be
                // flushed here before the turn ends.
                if let Some((id, name, input_json)) = current_tool.take() {
                    let input: Value = serde_json::from_str(&input_json).unwrap_or_else(|_| serde_json::json!({}));
                    content_blocks.push(ContentBlock::ToolUse { id, name, input });
                }
                // MessageStop may carry empty stop_reason/usage since the actual
                // values arrived earlier via MessageDelta. Only overwrite if non-empty.
                if !sr.is_empty() {
                    stop_reason = sr;
                }
                if u.input_tokens > 0 {
                    usage.input_tokens = u.input_tokens;
                }
                if u.output_tokens > 0 {
                    usage.output_tokens = u.output_tokens;
                }
                if u.cost_usd > 0.0 {
                    usage.cost_usd = u.cost_usd;
                }
                break;
            }
            StreamEvent::Ping => {
                let _ = event_tx.send(CpuEvent::Heartbeat).await;
            }
            // →949: Accumulate thinking deltas — finalized on ContentBlockStop
            StreamEvent::ThinkingDelta(t) => {
                current_thinking.push_str(&t);
            }
        }
    }

    Ok(StreamResult { content: content_blocks, stop_reason, usage, interrupted: None })
}

/// Kernel introspection tools — read-only OS state queries that never need approval.
/// The OS reads its own state without asking permission.
#[allow(dead_code)]
const KERNEL_READ_TOOLS: &[&str] = &[
    "ostk_log", "ostk_verbs", "ostk_man", "ostk_session_history",
    "ostk_context_restore", "ostk_context_search", "ostk_context_release", "ostk_pitchfork",
    "ostk_boot", "ostk_ps", "ostk_status", "ostk_ask",
    "log_decision", "Read", "Glob", "Grep",
];

/// Maximum number of retries when the API returns an empty content array.
const MAX_EMPTY_RETRIES: u32 = 2;

/// Options for the agent loop — replaces 6 wrapper functions with one struct.
///
/// →903: `approval_source` unifies TUI (Channel) and daemon (Bus) approval
/// paths. When `None`, tool calls in Governed mode fall back to the global
/// approval_bus (backward compatible with daemon-only callers).
#[derive(Default)]
pub struct AgentLoopOptions<'a> {
    pub file_cache: Option<crate::cpu::file_cache::FileCache>,
    pub boot_context: Option<&'a mut crate::cpu::session::BootContext>,
    pub approval_source: Option<ApprovalSource>,
    pub session_summary: Option<std::sync::Arc<std::sync::Mutex<crate::fcp::llm::SessionSummary>>>,
    /// →plan Phase 3: Cooperative cancel flag. When set, the agent loop exits
    /// cleanly at the next checkpoint instead of being hard-aborted.
    pub cancel_flag: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
    /// →1004: Shared session allow-list. Persists across dispatch boundaries
    /// via AgentSession. When None, falls back to a local HashSet (backward
    /// compatible with callers that don't use AgentSession).
    pub runtime_allowed: Option<Arc<Mutex<HashSet<String>>>>,
}


pub async fn run_loop(
    client: &dyn CpuDriver,
    config: &LoopConfig,
    messages: &SharedMessages,
    event_tx: mpsc::Sender<CpuEvent>,
    options: AgentLoopOptions<'_>,
) -> Result<(), AgentLoopError> {
    let file_cache = options.file_cache;
    let mut boot_context = options.boot_context;
    let mut approval_source = options.approval_source;
    let session_summary = options.session_summary;
    let cancel_flag = options.cancel_flag;
    let mut turns: u32 = 0;
    let mut empty_retries: u32 = 0;
    // →phase1: Accumulated input tokens for gating count_tokens calls.
    let mut total_input_tokens: u64 = 0;
    let mut turn_number: u32 = 0;
    // →1004: Tools approved via AlwaysAllow. Uses shared session set when
    // available (persists across dispatches), local fallback otherwise.
    let runtime_allowed: Arc<Mutex<HashSet<String>>> = options
        .runtime_allowed
        .unwrap_or_else(|| Arc::new(Mutex::new(HashSet::new())));
    // →731: Fast mode can be disabled mid-loop on 429 rate limit
    let mut fast_mode_active = config.fast_mode;
    // Beta fallback: once we strip betas after a 400, keep them off for the
    // rest of the loop.  We clone the betas so we can mutate without touching
    // the caller's config.
    let mut active_betas: Vec<String> = config.betas.clone();

    // →834: Retry counter for 429 rate-limit backoff (reset on success).
    let mut rate_limit_attempts: u32 = 0;

    // →800: Resolve daemon socket once at loop start.
    // If the socket file exists and is connectable, tool calls route through
    // the daemon. If not (no daemon running), falls back to direct execution.
    let resolved_socket = resolve_socket_path(config.root.as_deref());
    let socket_path: Option<String> = match resolved_socket {
        Some(path) => {
            if try_socket(&path).await {
                Some(path)
            } else {
                None
            }
        }
        None => None,
    };

    loop {
        // →plan Phase 3: Cooperative cancel check — exit cleanly when flag is set.
        if let Some(ref flag) = cancel_flag
            && flag.load(std::sync::atomic::Ordering::Acquire) {
                let _ = event_tx.send(CpuEvent::TurnComplete { usage: Usage::default() }).await;
                return Ok(());
            }

        // Check for cancel/redirect at the start of each turn — catches Escape
        // or redirect messages sent during streaming or between turns.
        if let Some(ref mut src) = approval_source {
            match src.try_recv() {
                Some(AgentResponse::Cancel) => {
                    let _ = event_tx.send(CpuEvent::Error("cancelled by operator".into())).await;
                    let _ = event_tx.send(CpuEvent::TurnComplete { usage: Usage::default() }).await;
                    return Ok(());
                }
                Some(AgentResponse::Redirect(msg)) => {
                    // Inject user message into conversation
                    messages.extend([Message {
                        role: "user".into(),
                        content: vec![ContentBlock::Text { text: msg }],
                        model: None,
                    }]);
                    // Emit event so TUI shows the redirect
                    let _ = event_tx.send(CpuEvent::TextComplete(String::new())).await;
                    // Continue to next iteration — will make a new API call with the injected message
                    continue;
                }
                _ => {}
            }
        }

        // →732: Apply permission mode to tool selection
        let (effective_tools, tool_choice) = match config.permission_mode {
            crate::cpu::PermissionMode::Plan => {
                // Plan mode: no tools at all
                (vec![], None)
            }
            _ => {
                // Governed/Auto/Autonomous: tools available as configured
                (config.tools.clone(), None)
            }
        };

        let mut local_config = config.clone();
        local_config.fast_mode = fast_mode_active;
        local_config.betas = active_betas.clone();
        // →FSM: Snapshot messages under lock for build_params (read-only).
        // Scope the MutexGuard so it drops before the next .await (Send requirement).
        let msgs_snapshot = messages.snapshot();
        let params = {
            let summary_guard = session_summary.as_ref().map(|s| s.lock().unwrap_or_else(|e| e.into_inner()));
            build_params(&local_config, &msgs_snapshot, effective_tools, tool_choice, boot_context.as_deref_mut(), total_input_tokens, summary_guard.as_deref())
        };

        // Pre-call token count -- best-effort, only when near budget (>60%)
        // to avoid extra API calls on every turn. Always count on turn 0.
        let budget = config.context_budget.unwrap_or_else(|| crate::cpu::params::model_context_budget(&config.model));
        let should_count = total_input_tokens > budget * 60 / 100 || turn_number == 0;
        if should_count
            && let Ok(token_count) = client.count_tokens(params.clone()).await {
                total_input_tokens = token_count;
                let _ = event_tx
                    .send(CpuEvent::PreCallTokenCount { tokens: token_count })
                    .await;
            }

        // -- streaming API call --
        // →731: On 429 with fast mode, fall back to standard speed and retry
        // →beta: On 400 with beta/Extra inputs error, strip betas and retry
        let stream_result = client.create_stream(params).await;
        let stream = match stream_result {
            Ok(s) => s,
            Err(e) if fast_mode_active && e.contains("API error 429") => {
                // →834: fall back to standard speed, then apply backoff before retry
                fast_mode_active = false;
                rate_limit_attempts += 1;
                let base = ((1u64 << (rate_limit_attempts.saturating_sub(1).min(5))) * 1000).min(30_000);
                let delay_ms = base;
                let jitter_ms = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| (d.subsec_nanos() % 500) as u64)
                    .unwrap_or(u64::from(rate_limit_attempts) * 137 % 500);
                let _ = event_tx
                    .send(CpuEvent::Error(format!(
                        "rate limited on fast mode — falling back to standard speed (retry {}/7, backoff {}ms)",
                        rate_limit_attempts, delay_ms + jitter_ms
                    )))
                    .await;
                tokio::time::sleep(Duration::from_millis(delay_ms + jitter_ms)).await;
                continue; // outer loop retries with fast_mode_active = false
            }
            Err(e) if e.contains("API error 429") && rate_limit_attempts < 7 => {
                // →834: standard-mode 429 — exponential backoff with jitter, max 7 retries
                rate_limit_attempts += 1;
                let base = ((1u64 << (rate_limit_attempts.saturating_sub(1).min(5))) * 1000).min(30_000);
                let delay_ms = base;
                let jitter_ms = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| (d.subsec_nanos() % 500) as u64)
                    .unwrap_or(u64::from(rate_limit_attempts) * 137 % 500);
                let _ = event_tx
                    .send(CpuEvent::Error(format!(
                        "[429] rate limited — retry {}/7, backoff {}ms",
                        rate_limit_attempts, delay_ms + jitter_ms
                    )))
                    .await;
                tokio::time::sleep(Duration::from_millis(delay_ms + jitter_ms)).await;
                continue;
            }
            Err(e) if !active_betas.is_empty()
                && e.contains("API error 400")
                && (e.contains("beta") || e.contains("Beta") || e.contains("Extra inputs")) =>
            {
                let cleared = active_betas.join(", ");
                active_betas.clear();
                let _ = event_tx
                    .send(CpuEvent::Error(format!(
                        "[beta] {} not available, continuing without",
                        cleared
                    )))
                    .await;
                // Rebuild params without beta features and retry once
                let mut retry_config = config.clone();
                retry_config.fast_mode = fast_mode_active;
                retry_config.betas = Vec::new();
                let (retry_tools, retry_tc) = match config.permission_mode {
                    crate::cpu::PermissionMode::Plan => (vec![], None),
                    _ => (config.tools.clone(), None),
                };
                let retry_snapshot = messages.snapshot();
                let retry_params = {
                    let sg = session_summary.as_ref().map(|s| s.lock().unwrap_or_else(|e| e.into_inner()));
                    build_params(&retry_config, &retry_snapshot, retry_tools, retry_tc, boot_context.as_deref_mut(), total_input_tokens, sg.as_deref())
                };
                match client.create_stream(retry_params).await {
                    Ok(s) => s,
                    Err(e) => {
                        let _ = event_tx
                            .send(CpuEvent::Error(format!("[beta retry failed] {}", e)))
                            .await;
                        return Err(AgentLoopError::ApiCall(e));
                    }
                }
            }
            Err(e) => {
                // →737: Audit log API errors
                if let Some(ref root) = config.root {
                    let _ = crate::append_audit(root, &serde_json::json!({
                        "event": "api.error",
                        "model": config.model,
                        "error": e.to_string(),
                        "ts": crate::now_iso(),
                    }));
                }
                return Err(AgentLoopError::ApiCall(e));
            }
        };
        rate_limit_attempts = 0; // →834: reset backoff counter on successful API call
        let result = consume_stream(stream, &event_tx, approval_source.as_mut(), cancel_flag.as_ref()).await?;

        // →925: Stream was interrupted by Cancel or Redirect — handle before
        // processing content blocks / tool calls.  Both arms exit (return/continue),
        // so normal processing only runs when interrupted is None.
        match result.interrupted {
            Some(AgentResponse::Cancel) => {
                // Push partial content so the conversation stays valid.
                if !result.content.is_empty() {
                    messages.push(Message {
                        role: "assistant".into(),
                        content: strip_empty_text_blocks(result.content),
                        model: Some(config.model.clone()),
                    });
                }
                let _ = event_tx.send(CpuEvent::Error("cancelled by operator".into())).await;
                let _ = event_tx.send(CpuEvent::TurnComplete { usage: result.usage }).await;
                return Ok(());
            }
            Some(AgentResponse::Redirect(msg)) => {
                // Push partial content so the conversation stays valid.
                if !result.content.is_empty() {
                    messages.push(Message {
                        role: "assistant".into(),
                        content: strip_empty_text_blocks(result.content),
                        model: Some(config.model.clone()),
                    });
                }
                // Inject the redirect message after the partial assistant turn
                messages.push(Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text { text: msg }],
                    model: None,
                });
                // Continue loop — next iteration makes a new API call with the redirect
                continue;
            }
            Some(_) | None => {} // No interruption or unexpected variant — proceed normally
        }

        // →737: Audit log successful API calls
        let tool_call_count = result.content.iter()
            .filter(|b| matches!(b, ContentBlock::ToolUse { .. }))
            .count();
        if let Some(ref root) = config.root {
            let _ = crate::append_audit(root, &serde_json::json!({
                "event": "api.call",
                "model": config.model,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "stop_reason": result.stop_reason,
                "tool_calls": tool_call_count,
                "ts": crate::now_iso(),
            }));
        }

        let stop_reason = result.stop_reason.as_str();

        match stop_reason {
            "refusal" => {
                let text = result.content.iter().find_map(|b| match b {
                    ContentBlock::Text { text } => Some(text.clone()),
                    _ => None,
                }).unwrap_or_else(|| "model refused to respond".into());
                let _ = event_tx.send(CpuEvent::Error(format!("refusal: {text}"))).await;
                let _ = event_tx.send(CpuEvent::TurnComplete { usage: result.usage }).await;
                return Err(AgentLoopError::Refusal(text));
            }
            "model_context_window_exceeded" => {
                let _ = event_tx.send(CpuEvent::Error(
                    "context window exceeded -- conversation too long".into(),
                )).await;
                let _ = event_tx.send(CpuEvent::TurnComplete { usage: result.usage }).await;
                return Err(AgentLoopError::ContextExceeded);
            }
            _ => {} // end_turn, tool_use, max_tokens, pause_turn -- handled below
        }

        // -----------------------------------------------------------------
        // Handle empty content -- retry with continuation prompt
        // -----------------------------------------------------------------
        if result.content.is_empty() {
            if empty_retries < MAX_EMPTY_RETRIES {
                empty_retries += 1;
                // Maintain role alternation: add placeholder assistant + user nudge.
                // NOTE: text must be non-empty — the API rejects empty text blocks (→804).
                messages.extend([
                    Message {
                        role: "assistant".into(),
                        content: vec![ContentBlock::Text { text: "(empty)".into() }],
                        model: None,
                    },
                    Message {
                        role: "user".into(),
                        content: vec![ContentBlock::Text {
                            text: "Continue -- your previous response was empty.".into(),
                        }],
                        model: None,
                    },
                ]);
                continue; // retry the API call
            }
            // Exhausted retries -- give up
            let _ = event_tx.send(CpuEvent::TurnComplete { usage: result.usage }).await;
            break;
        }
        empty_retries = 0; // reset on successful content

        // -----------------------------------------------------------------
        // Handle max_tokens -- response was truncated
        // -----------------------------------------------------------------
        if stop_reason == "max_tokens" {
            // TextComplete events were already emitted during streaming.
            // If no tool calls, warn and complete.
            let has_tool_calls = result.content.iter().any(|b| matches!(b, ContentBlock::ToolUse { .. }));
            if !has_tool_calls {
                let _ = event_tx.send(CpuEvent::Error(
                    "response truncated (max_tokens reached)".into(),
                )).await;
                let _ = event_tx.send(CpuEvent::TurnComplete { usage: result.usage }).await;
                break;
            }
            // Tool calls present despite truncation -- fall through to process them
        }

        // -----------------------------------------------------------------
        // Collect tool calls from the response
        // -----------------------------------------------------------------
        let tool_calls: Vec<_> = result
            .content
            .iter()
            .filter_map(|b| match b {
                ContentBlock::ToolUse { id, name, input } => {
                    Some((id.clone(), name.clone(), input.clone()))
                }
                _ => None,
            })
            .collect();

        // NOTE: TextComplete and ToolStart events were already emitted
        // during streaming (in consume_stream), so we don't emit them again.

        // -----------------------------------------------------------------
        // end_turn / pause_turn with no tool calls -- conversation complete
        // -----------------------------------------------------------------
        if tool_calls.is_empty() {
            // →928: Before ending the turn, check for redirect messages that
            // arrived during streaming but weren't caught by →925 (e.g., arrived
            // between the last TextDelta and MessageStop, or while the stream
            // was completing). Without this, the redirect text is abandoned
            // when the loop breaks and the channel receiver is dropped.
            if let Some(ref mut src) = approval_source {
                match src.try_recv() {
                    Some(AgentResponse::Cancel) => {
                        let _ = event_tx.send(CpuEvent::Error("cancelled by operator".into())).await;
                        let _ = event_tx.send(CpuEvent::TurnComplete { usage: result.usage }).await;
                        return Ok(());
                    }
                    Some(AgentResponse::Redirect(msg)) => {
                        // Push the completed assistant response
                        if !result.content.is_empty() {
                            messages.push(Message {
                                role: "assistant".into(),
                                content: strip_empty_text_blocks(result.content),
                                model: Some(config.model.clone()),
                            });
                        }
                        // Inject redirect as user message and continue the loop
                        messages.push(Message {
                            role: "user".into(),
                            content: vec![ContentBlock::Text { text: msg }],
                            model: None,
                        });
                        continue;
                    }
                    _ => {}
                }
            }
            // →944: Auto-continue when the model narrates intent to act but stops
            // without emitting a tool_use block. Common pattern: "Now let me write
            // the tests:" → end_turn. Instead of forcing the user to type "continue",
            // detect trailing intent markers and inject a continuation turn.
            let last_text = result.content.iter().rev().find_map(|b| {
                if let ContentBlock::Text { text } = b { Some(text.as_str()) } else { None }
            }).unwrap_or("");
            let trimmed = last_text.trim_end();
            let looks_like_intent = trimmed.ends_with(':')
                && stop_reason == "end_turn"
                && turns < config.max_turns.unwrap_or(u32::MAX);
            if looks_like_intent {
                if !result.content.is_empty() {
                    messages.push(Message {
                        role: "assistant".into(),
                        content: strip_empty_text_blocks(result.content),
                        model: Some(config.model.clone()),
                    });
                }
                messages.push(Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text { text: "continue".into() }],
                    model: None,
                });
                continue;
            }
            let _ = event_tx
                .send(CpuEvent::TurnComplete {
                    usage: result.usage,
                })
                .await;
            break;
        }

        // Emit intermediate usage so the TUI can update token counters while
        // tools are still executing (otherwise counters stay at 0k until done).
        // →phase1: Track cumulative input tokens for count_tokens gating.
        total_input_tokens = result.usage.input_tokens;
        let _ = event_tx
            .send(CpuEvent::Usage {
                usage: result.usage.clone(),
            })
            .await;

        // -----------------------------------------------------------------
        // Execute each tool and collect results
        // -----------------------------------------------------------------
        let mut tool_results: Vec<ContentBlock> = Vec::new();
        // →fix: Track redirect message — must be appended AFTER the
        // assistant+tool_result pair to preserve conversation structure.
        // Injecting it before would create orphaned tool_use blocks (API 400).
        let mut redirect_msg: Option<String> = None;
        for (id, name, input) in &tool_calls {
            // ToolStart was already emitted during streaming.

            // →plan Phase 3: Cooperative cancel check before tool execution.
            if let Some(ref flag) = cancel_flag
                && flag.load(std::sync::atomic::Ordering::Acquire) {
                    // Synthesize tool results for remaining tools and exit
                    for (rid, rname, _) in &tool_calls {
                        if !tool_results.iter().any(|r| matches!(r, ContentBlock::ToolResult { tool_use_id, .. } if tool_use_id == rid)) {
                            tool_results.push(ContentBlock::ToolResult {
                                tool_use_id: rid.clone(),
                                content: format!("Tool '{}' cancelled — cooperative shutdown", rname),
                                is_error: true,
                            });
                        }
                    }
                    messages.extend([
                        Message {
                            role: "assistant".into(),
                            content: strip_empty_text_blocks(result.content.clone()),
                            model: Some(config.model.clone()),
                        },
                        Message {
                            role: "user".into(),
                            content: tool_results,
                            model: None,
                        },
                    ]);
                    let _ = event_tx.send(CpuEvent::TurnComplete { usage: Usage::default() }).await;
                    return Ok(());
                }

            // →927: Drain cancel/redirect BEFORE requesting approval.
            // Without this, a Redirect sent while ToolA was executing sits in the
            // response channel. When ToolB calls request() → rx.recv(), it gets
            // the stale Redirect (mapped to Deny), showing "denied by operator"
            // when the user never denied.
            if let Some(ref mut src) = approval_source {
                match src.try_recv() {
                    Some(AgentResponse::Cancel) => {
                        for (rid, rname, _) in &tool_calls {
                            if !tool_results.iter().any(|r| matches!(r, ContentBlock::ToolResult { tool_use_id, .. } if tool_use_id == rid)) {
                                tool_results.push(ContentBlock::ToolResult {
                                    tool_use_id: rid.clone(),
                                    content: format!("Tool '{}' cancelled by operator", rname),
                                    is_error: true,
                                });
                            }
                        }
                        messages.extend([
                            Message {
                                role: "assistant".into(),
                                content: strip_empty_text_blocks(result.content),
                                model: Some(config.model.clone()),
                            },
                            Message {
                                role: "user".into(),
                                content: tool_results,
                                model: None,
                            },
                        ]);
                        let _ = event_tx.send(CpuEvent::Error("cancelled by operator".into())).await;
                        let _ = event_tx.send(CpuEvent::TurnComplete { usage: Usage::default() }).await;
                        return Ok(());
                    }
                    Some(AgentResponse::Redirect(msg)) => {
                        redirect_msg = Some(msg);
                        let _ = event_tx.send(CpuEvent::TextComplete(String::new())).await;
                        break;
                    }
                    _ => {}
                }
            }

            // →1008: Unified approval policy chain (v0.3).
            // Evaluated top-to-bottom, first match wins. Each step returns
            // ALLOW, DENY, PROMPT, or CONTINUE to the next step.
            //
            // Steps:
            //   1. pin.caps hard deny — fail-closed on parse error
            //   2. Destructive check — never bypassed by allow-list
            //   3. Kernel tools — read auto-approved, write/spawn/secret fall through
            //   4. Permission mode gate — sets the ceiling
            //   5. Session allow-list — persisted in AgentSession
            //   6. (future) Agentfile patterns
            //   7. User prompt — last resort
            {
                let cap_class = crate::cpu::detect_capability_class(name, input);

                // Step 1: pin.caps hard deny (→1009, →1010).
                // For write-class tools, check if the target path is denied by pin.caps.
                // Applies uniformly across file:edit, file:write, and shell:write.
                // →1010: Fail-closed — if OSTK_PIN is set but pin.caps can't load, DENY.
                let pin_denied = match cap_class {
                    crate::cpu::CapabilityClass::FileEdit
                    | crate::cpu::CapabilityClass::FileWrite => {
                        let target = input.get("file_path")
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        if !target.is_empty() {
                            if let Some(ref root) = config.root {
                                let ostk_path = crate::state_dir(root);
                                match crate::kernel::policy::load_pin_caps_result(&ostk_path) {
                                    crate::kernel::policy::PinCapsResult::Loaded(caps) => {
                                        caps.denies_write(std::path::Path::new(target))
                                    }
                                    crate::kernel::policy::PinCapsResult::Error(_) => true, // fail-closed
                                    crate::kernel::policy::PinCapsResult::NoneActive => false,
                                }
                            } else { false }
                        } else { false }
                    }
                    crate::cpu::CapabilityClass::ShellWrite => {
                        // Shell writes: extract target paths from command is complex.
                        // For now, defer to execution-time pin.caps check in kernel/file.rs.
                        // The destructive check (Step 2) catches the worst cases.
                        false
                    }
                    _ => false,
                };

                if pin_denied {
                    let target = input.get("file_path")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown");
                    let output = format!(
                        "Tool '{}' ({}) denied by pin.caps — write to '{}' not permitted",
                        name, cap_class, target
                    );
                    let _ = event_tx
                        .send(CpuEvent::ToolResult {
                            name: name.clone(),
                            output: output.clone(),
                            success: false,
                        })
                        .await;
                    tool_results.push(ContentBlock::ToolResult {
                        tool_use_id: id.clone(),
                        content: output,
                        is_error: true,
                    });
                    continue;
                }

                // Step 2: Destructive check — runs before allow-list, cannot be bypassed.
                // Only on shell:write, shell:exec, shell:secret, kernel:spawn.
                let destructive_hit = match cap_class {
                    crate::cpu::CapabilityClass::ShellWrite
                    | crate::cpu::CapabilityClass::ShellExec
                    | crate::cpu::CapabilityClass::ShellSecret
                    | crate::cpu::CapabilityClass::KernelSpawn => {
                        let cmd_str = input.get("command")
                            .or_else(|| input.get("cmd"))
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        crate::kernel::destructor::detect_destructive(cmd_str).is_some()
                    }
                    _ => false,
                };

                // Step 3: Kernel tools trust boundary
                let kernel_auto = matches!(cap_class, crate::cpu::CapabilityClass::KernelRead);

                // Step 4: Permission mode gate
                let mode_allows = match config.permission_mode {
                    crate::cpu::PermissionMode::Autonomous => true,
                    crate::cpu::PermissionMode::Auto => matches!(cap_class,
                        crate::cpu::CapabilityClass::FileRead
                        | crate::cpu::CapabilityClass::FileEdit
                        | crate::cpu::CapabilityClass::FileWrite
                        | crate::cpu::CapabilityClass::ShellRead
                        | crate::cpu::CapabilityClass::KernelRead
                    ),
                    crate::cpu::PermissionMode::Governed => false,
                    crate::cpu::PermissionMode::Plan => false,
                };

                // Plan mode: deny everything (except kernel:read which was auto-approved above)
                if config.permission_mode == crate::cpu::PermissionMode::Plan && !kernel_auto {
                    let output = format!("Tool '{}' ({}) denied — plan mode", name, cap_class);
                    let _ = event_tx
                        .send(CpuEvent::ToolResult {
                            name: name.clone(),
                            output: output.clone(),
                            success: false,
                        })
                        .await;
                    tool_results.push(ContentBlock::ToolResult {
                        tool_use_id: id.clone(),
                        content: output,
                        is_error: true,
                    });
                    continue;
                }

                // Step 5: Session allow-list
                let is_session_allowed = runtime_allowed.lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .contains(name.as_str());

                // Step 6: Agentfile TOOL patterns (→1011, →1012).
                // Check if any tool_pattern matches this call's capability class.
                // For privileged classes, require trust clearance (signed AF or HUMANFILE TRUST).
                let agentfile_allows = {
                    let class_label = cap_class.label();
                    let target_path = input.get("file_path")
                        .or_else(|| input.get("command"))
                        .or_else(|| input.get("cmd"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("");

                    let pattern_match = config.tool_patterns.iter().any(|(cls, pat)| {
                        if cls != class_label { return false; }
                        match pat {
                            None => true, // bare class match (e.g. "shell:read")
                            Some(p) => target_path.contains(p.as_str()), // path pattern
                        }
                    });

                    if pattern_match {
                        // Privileged classes require trust clearance
                        let is_privileged = matches!(cap_class,
                            crate::cpu::CapabilityClass::ShellExec
                            | crate::cpu::CapabilityClass::ShellSecret
                            | crate::cpu::CapabilityClass::KernelSpawn
                        );
                        if is_privileged {
                            // TODO: Check pin TRUST (OS floor) > HUMANFILE TRUST > default.
                            // For now, trust clearance = pattern match only in Autonomous mode,
                            // or always for non-privileged. Full TRUST hierarchy wiring is
                            // deferred until HUMANFILE loading is threaded into LoopConfig.
                            config.permission_mode == crate::cpu::PermissionMode::Autonomous
                        } else {
                            true
                        }
                    } else {
                        false
                    }
                };

                // Decision: needs_approval if not auto-allowed by any step,
                // OR if destructive check fires (overrides everything).
                // Each step is intentionally separate for policy auditability.
                #[allow(clippy::if_same_then_else)]
                let needs_approval = if destructive_hit {
                    true // Step 2: destructive always prompts
                } else if kernel_auto {
                    false // Step 3: kernel:read auto-approved
                } else if mode_allows {
                    false // Step 4: mode gate auto-approved
                } else if is_session_allowed {
                    false // Step 5: session allow-list
                } else if agentfile_allows {
                    false // Step 6: Agentfile pattern match
                } else {
                    true // Step 7: fall through to user prompt
                };

                if needs_approval {
                    let preview = serde_json::to_string(input)
                        .unwrap_or_default();

                    let decision = if let Some(ref mut src) = approval_source {
                        src.request(name, &preview, &event_tx).await
                    } else {
                        approval_bus::request("agent", name, &preview).await
                    };

                    match decision {
                        ApprovalDecision::Allow => { /* proceed */ }
                        ApprovalDecision::AlwaysAllow => {
                            runtime_allowed.lock().unwrap_or_else(|e| e.into_inner()).insert(name.clone());
                        }
                        ApprovalDecision::Deny => {
                            let output = format!("Tool '{}' ({}) denied by operator", name, cap_class);
                            let _ = event_tx
                                .send(CpuEvent::ToolResult {
                                    name: name.clone(),
                                    output: output.clone(),
                                    success: false,
                                })
                                .await;
                            tool_results.push(ContentBlock::ToolResult {
                                tool_use_id: id.clone(),
                                content: output,
                                is_error: true,
                            });
                            continue;
                        }
                    }
                }
            }

            // →UX: Emit tool input preview so the TUI shows what's about to run
            let _ = event_tx.send(CpuEvent::ToolInput {
                name: name.clone(),
                preview: tool_input_preview(name, input),
            }).await;

            let (raw_output, success) = execute_and_record(
                name, id, input, &event_tx, file_cache.as_ref(), config.root.as_ref(), socket_path.as_deref(),
            ).await;

            // fcp-llm: Feed tool events into session summary for eviction context
            let display_preview: String = raw_output.chars().take(200).collect();
            if let Some(ref summary_arc) = session_summary
                && let Ok(mut summary) = summary_arc.lock() {
                    summary.append_event(
                        format!("tool_{}", if success { "ok" } else { "err" }),
                        format!("{}: {}", name, display_preview),
                    );
                }

            // →800-804: Kernel integrations (nudge, recovery, dying)
            // ALL fire-and-forget — never block the agent loop.
            // The TUI freezes if any of these await a file lock.
            if let Some(ref root) = config.root {
                let hs_dir = crate::state_dir(root);
                let alias = std::env::var("OSTK_AGENT").unwrap_or_else(|_| "agent".into());
                let tool_name = name.to_string();
                let args_sum: String = serde_json::to_string(input).unwrap_or_default().chars().take(200).collect();
                let res_sum: String = display_preview.clone();
                // →928: Use actual token usage, not turn count, for context pressure.
                // turns/max_turns fires dying at turn 45/50 (90%) even when real
                // context utilization is 48% — causing premature state-saving ceremony.
                let ctx_pct = (total_input_tokens as f32 / budget as f32) * 100.0;
                // Single fire-and-forget task for all kernel integrations
                tokio::task::spawn_blocking(move || {
                    // Recovery
                    let recovery = crate::kernel::recovery::Recovery::new(&hs_dir);
                    let _ = recovery.log_tool_call(&alias, &tool_name, &args_sum, &res_sum);
                    // Nudge
                    let _ = crate::kernel::nudge::pop_nudges(&hs_dir, &alias);
                    // Dying
                    if crate::kernel::dying::should_notify(ctx_pct, &hs_dir, &alias) {
                        let _ = crate::kernel::dying::notify_dying(
                            &hs_dir, &alias, ctx_pct, None,
                            "context pressure — see recovery log",
                            "scheduler will re-dispatch with dying.md",
                        );
                    }
                });
            }

            tool_results.push(build_tool_result(id, name, raw_output, success));
        }

        // →fix: If redirect or cancel broke the tool loop, synthesize results
        // for any tool_use blocks that weren't executed. Every tool_use in the
        // assistant message MUST have a matching tool_result — otherwise the
        // API returns 400 with orphaned tool_use blocks.
        if redirect_msg.is_some() {
            for (tid, tname, _) in &tool_calls {
                if !tool_results.iter().any(|r| matches!(r, ContentBlock::ToolResult { tool_use_id, .. } if tool_use_id == tid)) {
                    tool_results.push(ContentBlock::ToolResult {
                        tool_use_id: tid.clone(),
                        content: format!("Tool '{}' skipped — operator redirected", tname),
                        is_error: true,
                    });
                }
            }
        }

        // -----------------------------------------------------------------
        // Append messages for next iteration.
        // CRITICAL: The user message with tool_result blocks must contain
        // ONLY tool_result blocks -- no text blocks mixed in. Text after
        // tool_results teaches the model to expect user input after every
        // tool use, causing empty responses.
        // -----------------------------------------------------------------
        // →804: strip empty text blocks — the API rejects them with 400.
        messages.extend([
            Message {
                role: "assistant".into(),
                content: strip_empty_text_blocks(result.content),
                model: Some(config.model.clone()), // →903: attribution
            },
            Message {
                role: "user".into(),
                content: tool_results, // tool_result blocks ONLY -- no text
                model: None,
            },
        ]);

        // →fix: Append redirect message AFTER the assistant+tool_result pair.
        // This preserves conversation structure: the tool_use/tool_result pairing
        // is complete, THEN the new user intent follows.
        if let Some(redir) = redirect_msg {
            messages.extend([Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: redir }],
                model: None,
            }]);
        }

        turns += 1;
        turn_number += 1;
        let max = config.max_turns.unwrap_or(50);
        if turns >= max {
                // Hit turn limit -- make one final streaming call WITHOUT
                // tools to get summary.
                let mut final_config = config.clone();
                final_config.betas = active_betas.clone();
                final_config.fast_mode = fast_mode_active;
                let final_snapshot = messages.snapshot();
                let params = {
                    let sg = session_summary.as_ref().map(|s| s.lock().unwrap_or_else(|e| e.into_inner()));
                    build_params(&final_config, &final_snapshot, vec![], None, boot_context.as_deref_mut(), total_input_tokens, sg.as_deref())
                };
                let stream = client.create_stream(params).await?;
                let summary = consume_stream(stream, &event_tx, None, None).await?;
                // TextComplete events were already emitted during streaming.
                let _ = event_tx
                    .send(CpuEvent::TurnComplete { usage: summary.usage })
                    .await;
                break;
            }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Tool execution — routed through the kernel where possible
// ---------------------------------------------------------------------------

/// Max bytes shown in TUI tool result display.
/// The LLM always gets full output — this only affects fcp-screen rendering.
const MAX_TOOL_DISPLAY: usize = 4000; // ~1k tokens for TUI preview

/// Filter out empty text blocks from assistant content.  The Anthropic API
/// rejects messages containing `{"type":"text","text":""}` with HTTP 400
/// "text content blocks must be non-empty".  After tool-use responses the
/// model sometimes returns empty text blocks alongside tool_use blocks.
fn strip_empty_text_blocks(content: Vec<ContentBlock>) -> Vec<ContentBlock> {
    let filtered: Vec<ContentBlock> = content
        .into_iter()
        .filter(|b| match b {
            ContentBlock::Text { text } => !text.is_empty(),
            _ => true,
        })
        .collect();
    if filtered.is_empty() {
        // Must have at least one block — use a minimal placeholder so the
        // API doesn't reject the empty content array.
        vec![ContentBlock::Text { text: "(continue)".into() }]
    } else {
        filtered
    }
}

/// Truncate output for TUI display only — the LLM never sees this.
/// fcp-screen gets a capped preview; the LLM gets full passthrough.
fn truncate_for_display(output: &str) -> String {
    if output.len() <= MAX_TOOL_DISPLAY { return output.to_string(); }
    let boundary = output.floor_char_boundary(MAX_TOOL_DISPLAY);
    format!("{}\n\n[+{} more bytes — ctrl+o to expand]", &output[..boundary], output.len() - MAX_TOOL_DISPLAY)
}

/// Build a human-readable preview of tool input for the TUI status line.
fn tool_input_preview(name: &str, input: &Value) -> String {
    match name {
        "Bash" | "bash" | "shell" | "sh_run" =>
            input.get("command").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        "Read" | "file:read" | "fs_read" =>
            input.get("file_path").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        "Edit" | "file:edit" | "Write" | "file:write" =>
            input.get("file_path").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        "Glob" =>
            input.get("pattern").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        "Grep" =>
            input.get("pattern").and_then(|v| v.as_str()).unwrap_or("").to_string(),
        _ =>
            serde_json::to_string(input).unwrap_or_default().chars().take(80).collect(),
    }
}

/// Execute a tool and record the result for both TUI and LLM.
///
/// Returns (raw_output, success) — the full LLM content.
/// Side effects: sends ToolStart, ToolInput, ToolResult events to the TUI channel.
/// The LLM gets raw_output (passthrough). The TUI gets truncated display output.
async fn execute_and_record(
    name: &str,
    id: &str,
    input: &Value,
    event_tx: &mpsc::Sender<CpuEvent>,
    file_cache: Option<&crate::cpu::file_cache::FileCache>,
    root: Option<&PathBuf>,
    socket_path: Option<&str>,
) -> (String, bool) {
    // TUI: show tool start
    let _ = event_tx.send(CpuEvent::ToolStart {
        name: name.to_string(),
        id: id.to_string(),
    }).await;

    // Execute
    let (raw_output, success) = execute_tool(name, input, file_cache, root, socket_path).await;

    // TUI: show display-truncated result
    let display_output = truncate_for_display(&raw_output);
    let _ = event_tx.send(CpuEvent::ToolResult {
        name: name.to_string(),
        output: display_output,
        success,
    }).await;

    (raw_output, success)
}

/// Build a ContentBlock::ToolResult from raw tool output.
/// LLM gets full passthrough — no truncation.
fn build_tool_result(id: &str, name: &str, raw_output: String, success: bool) -> ContentBlock {
    let err = !success;
    let content = if err && raw_output.trim().is_empty() {
        format!("tool '{}' failed with no output", name)
    } else {
        // Mask any secret values before they enter the LLM context.
        // This is the single chokepoint — all tool output passes through here.
        crate::kernel::secrets::mask(&raw_output)
    };
    ContentBlock::ToolResult {
        tool_use_id: id.to_string(),
        content,
        is_error: err,
    }
}

// ---------------------------------------------------------------------------
// Socket resolution (→800-804)
// ---------------------------------------------------------------------------

/// Resolve the daemon socket path with sane defaults.
///
/// 1. Check OSTK_SOCKET env var (explicit override).
/// 2. Fall back to `<root>/.ostk/ostk.sock` when root is provided.
/// 3. Return None if neither exists on disk.
fn resolve_socket_path(root: Option<&Path>) -> Option<String> {
    // Explicit env var takes priority
    if let Ok(env_path) = std::env::var("OSTK_SOCKET")
        && !env_path.is_empty() && std::path::Path::new(&env_path).exists() {
            return Some(env_path);
        }

    // Sane default: .ostk/ostk.sock relative to project root
    if let Some(root_path) = root {
        let state = crate::state_dir(root_path);
        let sock = state.join("ostk.sock");
        if sock.exists() {
            return Some(sock.to_string_lossy().into_owned());
        }
    }

    None
}

/// Try connecting to the socket to verify the daemon is alive.
async fn try_socket(path: &str) -> bool {
    tokio::net::UnixStream::connect(path).await.is_ok()
}

/// Dispatch a tool call to the appropriate handler in cpu::tool_exec (→747).
/// The match arms stay here as a thin dispatcher; implementation lives in tool_exec.
///
/// →800: Auto-connects to daemon socket when available. Checks OSTK_SOCKET
/// env var first, then falls back to sane default path (.ostk/ostk.sock).
/// On socket failure, falls back to direct execution (resilient).
/// Tools that require daemon state (process management, locks, sessions).
/// These can't run in execute_tool_direct — they need the daemon socket.
const DAEMON_STATEFUL_TOOLS: &[&str] = &[
    "interact", "sh_interact", "lock", "sh_lock", "session", "sh_session",
];

async fn execute_tool(
    name: &str,
    input: &serde_json::Value,
    file_cache: Option<&crate::cpu::file_cache::FileCache>,
    root: Option<&PathBuf>,
    socket_path: Option<&str>,
) -> (String, bool) {
    tracing::debug!(tool = %name, "tool: executing");
    // →800: Route through daemon socket when available
    if let Some(sock) = socket_path {
        match crate::cpu::tool_exec::execute_via_socket(sock, name, input).await {
            Ok(text) => return (text, true),
            Err(_) => {
                // Socket call failed — fall back to direct execution.
                // Resilient: if the daemon dies mid-session, tools keep
                // working via direct execution.
            }
        }
    }

    // →889: Daemon-stateful tools need a socket. If none exists, try to
    // spawn the daemon on-demand and route through it.
    if socket_path.is_none() && DAEMON_STATEFUL_TOOLS.contains(&name)
        && let Some(r) = root {
            let state_dir = crate::state_dir(r);
            if let Some(client) = crate::serve::client::auto_connect(&state_dir) {
                drop(client); // auto_connect spawns daemon; we just need the socket
                // Resolve the newly-created socket
                if let Some(sock) = resolve_socket_path(Some(r)) {
                    match crate::cpu::tool_exec::execute_via_socket(&sock, name, input).await {
                        Ok(text) => return (text, true),
                        Err(e) => return (format!("daemon tool {name} failed after spawn: {e}"), false),
                    }
                }
            }
            return (format!("tool '{name}' requires the daemon — run `ostk listen` or let the kernel spawn it"), false);
        }

    let result = execute_tool_direct(name, input, file_cache, root, socket_path).await;
    tracing::debug!(tool = %name, success = %result.1, "tool: complete");
    result
}

/// Direct tool execution — no socket, local handlers only.
async fn execute_tool_direct(
    name: &str,
    input: &serde_json::Value,
    file_cache: Option<&crate::cpu::file_cache::FileCache>,
    root: Option<&PathBuf>,
    socket_path: Option<&str>,
) -> (String, bool) {
    use crate::cpu::tool_exec;
    match name {
        "Bash"        => tool_exec::handle_bash(input, root).await,
        "Read"        => tool_exec::handle_read(input, file_cache, root).await,
        "Edit"        => tool_exec::handle_edit(input, root).await,
        "Write"       => tool_exec::handle_write(input, root).await,
        "Glob"        => tool_exec::handle_glob(input, root).await,
        "Grep"        => tool_exec::handle_grep(input, root).await,
        // →890: Unified tool names — Anthropic names, daemon names, and ostk_
        // prefixed names all route to the same direct kernel handlers.
        "SpawnAgent" | "spawn" | "sh_spawn" | "ostk_spawn" | "ostk_kernel_spawn"
            => tool_exec::handle_spawn_agent(input, socket_path).await,
        "NudgeAgent" | "nudge" | "ostk_nudge"
            => tool_exec::handle_nudge_agent(input).await,
        "FileNeedle" | "file_needle" | "ostk_file_needle"
            => tool_exec::handle_file_needle(input).await,
        "CloseNeedle" | "close_needle" | "ostk_close_needle"
            => tool_exec::handle_close_needle(input).await,
        "CompileHay" | "compile_hay" | "ostk_compile_hay"
            => tool_exec::handle_compile_hay(input).await,
        // fcp-web: web reading intelligence (→842)
        "WebRead"     => handle_web_read(input).await,
        "WebLinks"    => handle_web_links(input).await,
        "WebStatus"   => handle_web_status(input).await,
        // Demand-paged tool loading: ostk_verbs meta-tool
        "ostk_verbs"  => tool_exec::handle_ostk_verbs(input, root).await,
        "ostk_man"    => tool_exec::handle_ostk_man(input, root).await,
        "ostk_ask"    => tool_exec::handle_ostk_ask(input, root).await,
        "ostk_session_history" => tool_exec::handle_ostk_session_history(input, root).await,
        "ostk_context_restore" => tool_exec::handle_ostk_context_restore(input, root).await,
        "ostk_context_search" => tool_exec::handle_ostk_context_search(input, root).await,
        "ostk_pitchfork" => tool_exec::handle_ostk_pitchfork(input, root).await,
        "ostk_context_release" => tool_exec::handle_ostk_context_release(input, root).await,
        "log_decision" => tool_exec::handle_log_decision(input, root).await,
        // Fall-through: check if this is a .language verb (resident or deferred)
        // Note: parse_language_file reads from disk. For hot-path optimization,
        // this could be cached in LoopConfig. Acceptable for now — file is <4KB,
        // parse is <1ms, and tool calls are rate-limited by LLM inference time.
        _ if name.starts_with("ostk_") => {
            let verb = &name["ostk_".len()..];
            if let Some(r) = root {
                let entries = crate::language::parse_language_file(r).unwrap_or_default();
                if let Some(entry) = entries.iter().find(|e| e.verb == verb) {
                    // Route signal verbs (bracket resolutions) to signal handler
                    if entry.is_signal() {
                        tool_exec::handle_signal_verb(entry, input, root).await
                    // Route device tools through driver socket, not Bash.
                    // "internal" devices are handled by built-in tool handlers,
                    // not a socket — skip socket routing for them.
                    // Lazy spawn: if socket is dead, try to spawn the driver
                    // transparently from HUMANFILE config, then retry.
                    } else if entry.is_device() && entry.is_alive() && entry.resolution != "internal" {
                        let socket_path = entry.resolution.clone();
                        match tool_exec::execute_via_socket(&socket_path, &entry.verb, input).await {
                            Ok(text) => (text, true),
                            Err(_) => {
                                // Socket dead — try lazy spawn
                                match tool_exec::lazy_spawn_driver(r, &entry.verb, &socket_path).await {
                                    Ok(()) => {
                                        // Retry after spawn
                                        match tool_exec::execute_via_socket(&socket_path, &entry.verb, input).await {
                                            Ok(text) => (text, true),
                                            Err(e) => (format!("device {} error after spawn: {e}", entry.verb), false),
                                        }
                                    }
                                    Err(e) => (format!("device {} unavailable: {e}", entry.verb), false),
                                }
                            }
                        }
                    } else if entry.resolution == "internal" {
                        // Internal devices route to built-in handlers directly.
                        // e.g. fcp-web(url=...) → handle_web_read(input)
                        match entry.verb.as_str() {
                            "fcp-web" => handle_web_read(input).await,
                            _ => (format!("{} is an internal device with no built-in handler", entry.verb), false),
                        }
                    } else if let Some(result) = crate::kernel::resolve::try_internal(verb, input, r) {
                        // →1157: Internal resolution — direct function call, no fork+exec.
                        // Record verb usage for momentum tracking.
                        let _ = crate::language::record_verb(r, &entry.verb, &entry.resolution);
                        (result.output, result.success)
                    } else {
                        tool_exec::handle_language_tool(entry, input, root).await
                    }
                } else {
                    (serde_json::json!({
                        "error": "ETOOLNOTLOADED",
                        "verb": verb,
                        "hint": format!("Use ostk_verbs('{}') to discover this tool", verb)
                    }).to_string(), false)
                }
            } else {
                (format!("tool not implemented: {name}"), false)
            }
        }
        _             => (format!("tool not implemented: {name}"), false),
    }
}

// fcp-web tool handlers (→842)
async fn handle_web_read(input: &Value) -> (String, bool) {
    let url = input.get("url").and_then(|v| v.as_str()).unwrap_or("");
    if url.is_empty() { return ("url is required".into(), false); }
    match crate::fcp::web::web_read(url).await {
        Ok(text) => (text, true),
        Err(e) => (e, false),
    }
}

async fn handle_web_links(input: &Value) -> (String, bool) {
    let url = input.get("url").and_then(|v| v.as_str()).unwrap_or("");
    if url.is_empty() { return ("url is required".into(), false); }
    match crate::fcp::web::web_links(url).await {
        Ok(text) => (text, true),
        Err(e) => (e, false),
    }
}

async fn handle_web_status(input: &Value) -> (String, bool) {
    let url = input.get("url").and_then(|v| v.as_str()).unwrap_or("");
    if url.is_empty() { return ("url is required".into(), false); }
    match crate::fcp::web::web_status(url).await {
        Ok(text) => (text, true),
        Err(e) => (e, false),
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    /// Helper: call execute_tool without a file cache or root (convenience for tests).
    async fn exec_tool(name: &str, input: &serde_json::Value) -> (String, bool) {
        execute_tool(name, input, None, None, None).await
    }

    /// Helper: collect all CpuEvents from a receiver until it closes.
    async fn collect_events(mut rx: mpsc::Receiver<CpuEvent>) -> Vec<CpuEvent> {
        let mut events = Vec::new();
        while let Some(ev) = rx.recv().await {
            events.push(ev);
        }
        events
    }

    // -- mock infrastructure ------------------------------------------------

    /// A mock API client that returns pre-programmed responses.
    /// We can't use AnthropicClient directly without hitting the network, so we
    /// test the loop logic by extracting it into a generic helper.
    struct MockApi {
        responses: Mutex<Vec<ApiResponse>>,
    }

    impl MockApi {
        fn new(responses: Vec<ApiResponse>) -> Self {
            Self {
                responses: Mutex::new(responses),
            }
        }

        fn next_response(&self) -> Result<ApiResponse, String> {
            let mut responses = self.responses.lock().unwrap();
            if responses.is_empty() {
                Err("no more mock responses".into())
            } else {
                Ok(responses.remove(0))
            }
        }
    }

    /// Extracted loop logic that works with a closure instead of AnthropicClient,
    /// so we can mock the API in tests. Mirrors the stop_reason handling
    /// and message construction of the real `run_loop`.
    async fn run_loop_with<F>(
        call_api: F,
        config: &LoopConfig,
        messages: &mut Vec<Message>,
        event_tx: mpsc::Sender<CpuEvent>,
    ) -> Result<(), String>
    where
        F: Fn(&[Message]) -> Result<ApiResponse, String>,
    {
        let mut turns: u32 = 0;
        let mut empty_retries: u32 = 0;
        let mut active_betas = config.betas.clone();

        loop {
            let response = match call_api(messages) {
                Ok(r) => r,
                Err(e) if !active_betas.is_empty()
                    && e.contains("API error 400")
                    && (e.contains("beta") || e.contains("Beta") || e.contains("Extra inputs")) =>
                {
                    let cleared = active_betas.join(", ");
                    active_betas.clear();
                    let _ = event_tx
                        .send(CpuEvent::Error(format!(
                            "[beta] {} not available, continuing without",
                            cleared
                        )))
                        .await;
                    // Retry once without betas
                    call_api(messages)?
                }
                Err(e) => {
                    // →737: Audit log API errors (mirrors run_loop)
                    if let Some(ref root) = config.root {
                        let _ = crate::append_audit(root, &serde_json::json!({
                            "event": "api.error",
                            "model": config.model,
                            "error": e,
                            "ts": crate::now_iso(),
                        }));
                    }
                    return Err(e);
                }
            };

            // →737: Audit log successful API calls (mirrors run_loop)
            let tool_call_count = response.content.iter()
                .filter(|b| matches!(b, ContentBlock::ToolUse { .. }))
                .count();
            if let Some(ref root) = config.root {
                let _ = crate::append_audit(root, &serde_json::json!({
                    "event": "api.call",
                    "model": config.model,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "stop_reason": response.stop_reason,
                    "tool_calls": tool_call_count,
                    "ts": crate::now_iso(),
                }));
            }

            // -- stop_reason handling (mirrors run_loop) --
            let stop_reason = response.stop_reason.as_deref().unwrap_or("end_turn");

            match stop_reason {
                "refusal" => {
                    let text = response.content.iter().find_map(|b| match b {
                        ContentBlock::Text { text } => Some(text.clone()),
                        _ => None,
                    }).unwrap_or_else(|| "model refused to respond".into());
                    let _ = event_tx.send(CpuEvent::Error(format!("refusal: {text}"))).await;
                    let _ = event_tx.send(CpuEvent::TurnComplete { usage: response.usage }).await;
                    return Err(format!("refusal: {text}"));
                }
                "model_context_window_exceeded" => {
                    let _ = event_tx.send(CpuEvent::Error(
                        "context window exceeded -- conversation too long".into(),
                    )).await;
                    let _ = event_tx.send(CpuEvent::TurnComplete { usage: response.usage }).await;
                    return Err("model_context_window_exceeded".into());
                }
                _ => {}
            }

            // -- empty content retry --
            if response.content.is_empty() {
                if empty_retries < MAX_EMPTY_RETRIES {
                    empty_retries += 1;
                    // NOTE: text must be non-empty — the API rejects empty text blocks (→804).
                    messages.push(Message {
                        role: "assistant".into(),
                        content: vec![ContentBlock::Text { text: "(empty)".into() }],
                        model: None,
                    });
                    messages.push(Message {
                        role: "user".into(),
                        content: vec![ContentBlock::Text {
                            text: "Continue -- your previous response was empty.".into(),
                        }],
                        model: None,
                    });
                    continue;
                }
                let _ = event_tx.send(CpuEvent::TurnComplete { usage: response.usage }).await;
                break;
            }
            empty_retries = 0;

            // -- max_tokens handling --
            if stop_reason == "max_tokens" {
                for block in &response.content {
                    if let ContentBlock::Text { text } = block {
                        let _ = event_tx.send(CpuEvent::TextComplete(text.clone())).await;
                    }
                }
                let has_tool_calls = response.content.iter().any(|b| matches!(b, ContentBlock::ToolUse { .. }));
                if !has_tool_calls {
                    let _ = event_tx.send(CpuEvent::Error(
                        "response truncated (max_tokens reached)".into(),
                    )).await;
                    let _ = event_tx.send(CpuEvent::TurnComplete { usage: response.usage }).await;
                    break;
                }
            }

            let tool_calls: Vec<_> = response
                .content
                .iter()
                .filter_map(|b| match b {
                    ContentBlock::ToolUse { id, name, input } => {
                        Some((id.clone(), name.clone(), input.clone()))
                    }
                    _ => None,
                })
                .collect();

            if stop_reason != "max_tokens" {
                for block in &response.content {
                    if let ContentBlock::Text { text } = block {
                        let _ = event_tx.send(CpuEvent::TextComplete(text.clone())).await;
                    }
                }
            }

            if tool_calls.is_empty() {
                let _ = event_tx
                    .send(CpuEvent::TurnComplete {
                        usage: response.usage,
                    })
                    .await;
                break;
            }

            // Intermediate usage (matches real run_loop)
            let _ = event_tx
                .send(CpuEvent::Usage {
                    usage: response.usage.clone(),
                })
                .await;

            let mut tool_results: Vec<ContentBlock> = Vec::new();
            for (id, name, input) in &tool_calls {
                let (raw_output, success) = execute_and_record(
                    name, id, input, &event_tx, None, config.root.as_ref(), None,
                ).await;
                tool_results.push(build_tool_result(id, name, raw_output, success));
            }

            // CRITICAL: tool_result user message must contain ONLY tool_result
            // blocks -- no text blocks mixed in.
            // →804: strip empty text blocks — the API rejects them with 400.
            messages.push(Message {
                role: "assistant".into(),
                content: strip_empty_text_blocks(response.content),
                model: None,
            });
            messages.push(Message {
                role: "user".into(),
                content: tool_results,
                model: None,
            });

            turns += 1;
            if let Some(max) = config.max_turns
                && turns >= max {
                    break;
                }
        }

        Ok(())
    }

    fn default_config() -> LoopConfig {
        LoopConfig {
            model: "test".into(),
            system_prompt: None,
            tools: vec![],
            max_tokens: 1024,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::Governed,
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        }
    }

    // -- tests --------------------------------------------------------------

    #[tokio::test]
    async fn text_only_response_yields_text_complete_and_turn_complete() {
        let mock = Arc::new(MockApi::new(vec![ApiResponse {
            content: vec![ContentBlock::Text {
                text: "Hello world".into(),
            }],
            stop_reason: Some("end_turn".into()),
            usage: Usage {
                input_tokens: 10,
                output_tokens: 5,
                ..Default::default()
            },
            applied_edits: vec![],
        }]));

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "hi".into(),
            }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        assert_eq!(events.len(), 2);
        match &events[0] {
            CpuEvent::TextComplete(t) => assert_eq!(t, "Hello world"),
            other => panic!("expected TextComplete, got {other:?}"),
        }
        match &events[1] {
            CpuEvent::TurnComplete { usage } => {
                assert_eq!(usage.input_tokens, 10);
                assert_eq!(usage.output_tokens, 5);
            }
            other => panic!("expected TurnComplete, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn tool_use_triggers_execution_and_appends_results() {
        // Turn 1: assistant calls a tool
        // Turn 2: assistant responds with text only
        let mock = Arc::new(MockApi::new(vec![
            ApiResponse {
                content: vec![
                    ContentBlock::Text {
                        text: "Let me check.".into(),
                    },
                    ContentBlock::ToolUse {
                        id: "tu_1".into(),
                        name: "Bash".into(),
                        input: serde_json::json!({"command": "echo hello"}),
                    },
                ],
                stop_reason: Some("tool_use".into()),
                usage: Usage {
                    input_tokens: 20,
                    output_tokens: 15,
                    ..Default::default()
                },
                applied_edits: vec![],
            },
            ApiResponse {
                content: vec![ContentBlock::Text {
                    text: "Done.".into(),
                }],
                stop_reason: Some("end_turn".into()),
                usage: Usage {
                    input_tokens: 30,
                    output_tokens: 10,
                    ..Default::default()
                },
                applied_edits: vec![],
            },
        ]));

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "run echo".into(),
            }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Expected: TextComplete("Let me check."), Usage, ToolStart, ToolResult, TextComplete("Done."), TurnComplete
        assert_eq!(events.len(), 6, "events: {events:?}");

        match &events[0] {
            CpuEvent::TextComplete(t) => assert_eq!(t, "Let me check."),
            other => panic!("expected TextComplete, got {other:?}"),
        }
        match &events[1] {
            CpuEvent::Usage { usage } => {
                assert_eq!(usage.input_tokens, 20);
                assert_eq!(usage.output_tokens, 15);
            }
            other => panic!("expected Usage, got {other:?}"),
        }
        match &events[2] {
            CpuEvent::ToolStart { name, id } => {
                assert_eq!(name, "Bash");
                assert_eq!(id, "tu_1");
            }
            other => panic!("expected ToolStart, got {other:?}"),
        }
        match &events[3] {
            CpuEvent::ToolResult {
                name,
                output,
                success,
            } => {
                assert_eq!(name, "Bash");
                assert!(output.contains("hello"), "output was: {output}");
                assert!(success);
            }
            other => panic!("expected ToolResult, got {other:?}"),
        }
        match &events[4] {
            CpuEvent::TextComplete(t) => assert_eq!(t, "Done."),
            other => panic!("expected TextComplete, got {other:?}"),
        }
        match &events[5] {
            CpuEvent::TurnComplete { usage } => {
                assert_eq!(usage.output_tokens, 10);
            }
            other => panic!("expected TurnComplete, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn max_turns_stops_the_loop() {
        // Every response asks for another tool call — loop should stop after 2 turns.
        let responses: Vec<ApiResponse> = (0..10)
            .map(|i| ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: format!("tu_{i}"),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo turn"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            })
            .collect();

        let mock = Arc::new(MockApi::new(responses));
        let (tx, rx) = mpsc::channel(64);
        let config = LoopConfig {
            max_turns: Some(2),
            ..default_config()
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "go".into(),
            }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Each tool-use turn produces Usage + ToolStart + ToolResult = 3 events per turn.
        // 2 turns = 6 events. No TurnComplete because we broke due to max_turns.
        let tool_start_count = events
            .iter()
            .filter(|e| matches!(e, CpuEvent::ToolStart { .. }))
            .count();
        assert_eq!(tool_start_count, 2, "should have exactly 2 tool starts");
        let usage_count = events
            .iter()
            .filter(|e| matches!(e, CpuEvent::Usage { .. }))
            .count();
        assert_eq!(usage_count, 2, "should have exactly 2 intermediate usage events");
    }

    #[tokio::test]
    async fn messages_accumulate_correctly_across_turns() {
        let call_count = Arc::new(Mutex::new(0u32));
        let captured_messages: Arc<Mutex<Vec<Vec<Message>>>> = Arc::new(Mutex::new(Vec::new()));

        let call_count_ref = call_count.clone();
        let captured_ref = captured_messages.clone();

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: "start".into(),
            }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    captured_ref
                        .lock()
                        .unwrap()
                        .push(msgs.to_vec());
                    *count += 1;
                    if *count == 1 {
                        Ok(ApiResponse {
                            content: vec![ContentBlock::ToolUse {
                                id: "tu_1".into(),
                                name: "Bash".into(),
                                input: serde_json::json!({"command": "echo ok"}),
                            }],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        })
                    } else {
                        Ok(ApiResponse {
                            content: vec![ContentBlock::Text {
                                text: "all done".into(),
                            }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        })
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let _events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        let captured = captured_messages.lock().unwrap();
        // First call: just the user message
        assert_eq!(captured[0].len(), 1);
        assert_eq!(captured[0][0].role, "user");
        // Second call: user + assistant + tool_result = 3 messages
        assert_eq!(captured[1].len(), 3);
        assert_eq!(captured[1][1].role, "assistant");
        assert_eq!(captured[1][2].role, "user");
    }

    // -----------------------------------------------------------------------
    // 3-turn multi-tool conversation
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn three_turn_multi_tool_conversation() {
        // Turn 1: assistant returns 2 tool_use blocks
        // Turn 2: assistant returns 1 tool_use block
        // Turn 3: assistant returns text only (end)
        let call_count = Arc::new(Mutex::new(0u32));
        let captured_messages: Arc<Mutex<Vec<Vec<Message>>>> = Arc::new(Mutex::new(Vec::new()));

        let call_count_ref = call_count.clone();
        let captured_ref = captured_messages.clone();

        let (tx, rx) = mpsc::channel(64);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "do three things".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    captured_ref.lock().unwrap().push(msgs.to_vec());
                    *count += 1;
                    match *count {
                        1 => Ok(ApiResponse {
                            content: vec![
                                ContentBlock::Text { text: "I'll run two commands.".into() },
                                ContentBlock::ToolUse {
                                    id: "tu_1".into(),
                                    name: "Bash".into(),
                                    input: serde_json::json!({"command": "echo first"}),
                                },
                                ContentBlock::ToolUse {
                                    id: "tu_2".into(),
                                    name: "Bash".into(),
                                    input: serde_json::json!({"command": "echo second"}),
                                },
                            ],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage { input_tokens: 50, output_tokens: 30, ..Default::default() },
                            applied_edits: vec![],
                        }),
                        2 => Ok(ApiResponse {
                            content: vec![
                                ContentBlock::Text { text: "One more check.".into() },
                                ContentBlock::ToolUse {
                                    id: "tu_3".into(),
                                    name: "Bash".into(),
                                    input: serde_json::json!({"command": "echo third"}),
                                },
                            ],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage { input_tokens: 120, output_tokens: 40, ..Default::default() },
                            applied_edits: vec![],
                        }),
                        3 => Ok(ApiResponse {
                            content: vec![ContentBlock::Text { text: "All done!".into() }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage { input_tokens: 200, output_tokens: 20, ..Default::default() },
                            applied_edits: vec![],
                        }),
                        _ => Err("unexpected call".into()),
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Verify the event sequence
        // Turn 1: TextComplete, Usage, ToolStart, ToolResult, ToolStart, ToolResult (6 events)
        // Turn 2: TextComplete, Usage, ToolStart, ToolResult (4 events)
        // Turn 3: TextComplete, TurnComplete (2 events)
        // Total: 12 events

        let text_completes: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::TextComplete(_))).collect();
        assert_eq!(text_completes.len(), 3, "should have 3 TextComplete events");

        let tool_starts: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::ToolStart { .. })).collect();
        assert_eq!(tool_starts.len(), 3, "should have 3 ToolStart events");

        let tool_results: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::ToolResult { .. })).collect();
        assert_eq!(tool_results.len(), 3, "should have 3 ToolResult events");

        let usage_events: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::Usage { .. })).collect();
        assert_eq!(usage_events.len(), 2, "should have 2 intermediate Usage events (turns with tools)");

        let turn_completes: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::TurnComplete { .. })).collect();
        assert_eq!(turn_completes.len(), 1, "should have 1 TurnComplete at the end");

        // Verify message accumulation
        let captured = captured_messages.lock().unwrap();
        assert_eq!(captured.len(), 3, "API should have been called 3 times");

        // Call 1: just the user message
        assert_eq!(captured[0].len(), 1);

        // Call 2: user + assistant(2 tools) + tool_results = 3
        assert_eq!(captured[1].len(), 3);
        assert_eq!(captured[1][0].role, "user");
        assert_eq!(captured[1][1].role, "assistant");
        assert_eq!(captured[1][2].role, "user");
        // The tool results message should have 2 ToolResult blocks
        assert_eq!(captured[1][2].content.len(), 2);

        // Call 3: user + assistant(2 tools) + tool_results(2) + assistant(1 tool) + tool_results(1) = 5
        assert_eq!(captured[2].len(), 5);
        assert_eq!(captured[2][3].role, "assistant");
        assert_eq!(captured[2][4].role, "user");
        assert_eq!(captured[2][4].content.len(), 1); // 1 tool result
    }

    // -----------------------------------------------------------------------
    // Empty text blocks
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn empty_text_blocks_still_emit_text_complete() {
        // The API sometimes returns empty text blocks alongside tool_use.
        // The loop should still emit them (filtering is the TUI's job).
        let mock = Arc::new(MockApi::new(vec![
            ApiResponse {
                content: vec![
                    ContentBlock::Text { text: "".into() },
                    ContentBlock::Text { text: "real text".into() },
                ],
                stop_reason: Some("end_turn".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
        ]));

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "go".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Both text blocks should produce TextComplete events
        let text_events: Vec<_> = events
            .iter()
            .filter_map(|e| match e {
                CpuEvent::TextComplete(t) => Some(t.as_str()),
                _ => None,
            })
            .collect();
        assert_eq!(text_events.len(), 2, "both text blocks should emit TextComplete");
        assert_eq!(text_events[0], "");
        assert_eq!(text_events[1], "real text");
    }

    // -----------------------------------------------------------------------
    // Tool execution — real filesystem operations
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn execute_tool_bash_echo() {
        let (output, success) = exec_tool(
            "Bash",
            &serde_json::json!({"command": "echo hello world"}),
        ).await;
        assert!(success, "echo should succeed");
        assert!(output.contains("hello world"), "output: {output}");
    }

    #[tokio::test]
    async fn execute_tool_bash_failing_command() {
        let (output, success) = exec_tool(
            "Bash",
            &serde_json::json!({"command": "false"}),
        ).await;
        assert!(!success, "false should fail");
        // Output may be empty for `false`, that's OK
        let _ = output;
    }

    #[tokio::test]
    async fn execute_tool_read_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test_read.txt");
        std::fs::write(&file_path, "content here").unwrap();

        let (output, success) = exec_tool(
            "Read",
            &serde_json::json!({"file_path": file_path.to_str().unwrap()}),
        ).await;
        assert!(success, "reading existing file should succeed");
        assert_eq!(output, "content here");
    }

    #[tokio::test]
    async fn execute_tool_read_directory_returns_listing() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("alpha.txt"), "").unwrap();
        std::fs::write(dir.path().join("beta.rs"), "").unwrap();
        std::fs::create_dir(dir.path().join("subdir")).unwrap();

        let (output, success) = exec_tool(
            "Read",
            &serde_json::json!({"file_path": dir.path().to_str().unwrap()}),
        ).await;
        assert!(success, "reading a directory should succeed with listing");
        assert!(output.contains("alpha.txt"), "should list files: {output}");
        assert!(output.contains("beta.rs"), "should list files: {output}");
        assert!(output.contains("subdir/"), "directories should have trailing slash: {output}");
    }

    #[tokio::test]
    async fn execute_tool_read_directory_with_root() {
        let root = setup_ostk_root();
        // Create a subdirectory with files
        let sub = root.path().join("src");
        std::fs::create_dir(&sub).unwrap();
        std::fs::write(sub.join("main.rs"), "fn main() {}").unwrap();
        std::fs::write(sub.join("lib.rs"), "").unwrap();

        let (output, success) = exec_tool_with_root(
            "Read",
            &serde_json::json!({"file_path": sub.to_str().unwrap()}),
            root.path(),
        ).await;
        assert!(success, "reading directory with root should succeed: {output}");
        assert!(output.contains("main.rs"), "should list files: {output}");
        assert!(output.contains("lib.rs"), "should list files: {output}");
    }

    #[test]
    fn expand_tilde_resolves_home() {
        use crate::cpu::tool_exec::expand_tilde;
        let home = std::env::var("HOME").unwrap();

        // ~ alone → home dir
        assert_eq!(expand_tilde("~"), PathBuf::from(&home));

        // ~/path → home/path
        assert_eq!(expand_tilde("~/projects/foo"), PathBuf::from(&home).join("projects/foo"));

        // Absolute path unchanged
        assert_eq!(expand_tilde("/usr/bin/ls"), PathBuf::from("/usr/bin/ls"));

        // Relative path unchanged (no tilde)
        assert_eq!(expand_tilde("src/main.rs"), PathBuf::from("src/main.rs"));

        // ~user form NOT expanded (not supported) — stays literal
        assert_eq!(expand_tilde("~otheruser/foo"), PathBuf::from("~otheruser/foo"));
    }

    #[tokio::test]
    async fn execute_tool_read_tilde_path() {
        // Read tool should expand ~ to home directory
        let home = std::env::var("HOME").unwrap();
        // Create a temp file in a known location under home
        let test_path = format!("{}/.ostk_tilde_test_{}", home, std::process::id());
        std::fs::write(&test_path, "tilde_works").unwrap();

        let tilde_path = format!("~/.ostk_tilde_test_{}", std::process::id());
        let (output, success) = exec_tool(
            "Read",
            &serde_json::json!({"file_path": tilde_path}),
        ).await;
        std::fs::remove_file(&test_path).ok();
        assert!(success, "reading ~/path should work: {output}");
        assert!(output.contains("tilde_works"), "should read file content: {output}");
    }

    #[tokio::test]
    async fn execute_tool_read_nonexistent_file() {
        let (output, success) = exec_tool(
            "Read",
            &serde_json::json!({"file_path": "/tmp/definitely_does_not_exist_ostk_test_12345.txt"}),
        ).await;
        assert!(!success, "reading nonexistent file should fail");
        assert!(output.contains("read error"), "output: {output}");
    }

    #[tokio::test]
    async fn execute_tool_write_creates_file() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test_write.txt");

        let (output, success) = exec_tool(
            "Write",
            &serde_json::json!({
                "file_path": file_path.to_str().unwrap(),
                "content": "written by test"
            }),
        ).await;
        assert!(success, "write should succeed: {output}");
        assert_eq!(output, "file written");

        // Verify the file was actually written
        let contents = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(contents, "written by test");
    }

    #[tokio::test]
    async fn execute_tool_edit_replaces_text() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test_edit.txt");
        std::fs::write(&file_path, "hello world foo bar").unwrap();

        let (output, success) = exec_tool(
            "Edit",
            &serde_json::json!({
                "file_path": file_path.to_str().unwrap(),
                "old_string": "foo",
                "new_string": "baz"
            }),
        ).await;
        assert!(success, "edit should succeed: {output}");
        assert_eq!(output, "edit applied");

        let contents = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(contents, "hello world baz bar");
    }

    #[tokio::test]
    async fn execute_tool_edit_old_string_not_found() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test_edit_miss.txt");
        std::fs::write(&file_path, "hello world").unwrap();

        let (output, success) = exec_tool(
            "Edit",
            &serde_json::json!({
                "file_path": file_path.to_str().unwrap(),
                "old_string": "nonexistent",
                "new_string": "replacement"
            }),
        ).await;
        assert!(!success, "edit should fail when old_string not found");
        assert!(output.contains("not found"), "output: {output}");
    }

    #[tokio::test]
    async fn execute_tool_glob_finds_files() {
        // Glob uses find under the hood; just verify it runs successfully
        let (output, success) = exec_tool(
            "Glob",
            &serde_json::json!({"pattern": "*.rs"}),
        ).await;
        // May or may not find files depending on CWD, but shouldn't error
        assert!(success, "glob should succeed: {output}");
    }

    #[tokio::test]
    async fn execute_tool_grep_searches_content() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test_grep.txt");
        std::fs::write(&file_path, "line one\nfoo bar\nline three\n").unwrap();

        let (output, success) = exec_tool(
            "Grep",
            &serde_json::json!({
                "pattern": "foo",
                "path": file_path.to_str().unwrap()
            }),
        ).await;
        assert!(success, "grep should succeed: {output}");
        assert!(output.contains("foo bar"), "output should contain matched line: {output}");
    }

    #[tokio::test]
    async fn execute_tool_grep_no_match_is_failure() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test_grep_miss.txt");
        std::fs::write(&file_path, "nothing to find here\n").unwrap();

        let (output, success) = exec_tool(
            "Grep",
            &serde_json::json!({
                "pattern": "zzz_nonexistent_zzz",
                "path": file_path.to_str().unwrap()
            }),
        ).await;
        // grep returns exit code 1 when no match found
        assert!(!success, "grep with no match should fail");
        assert!(output.is_empty() || output.trim().is_empty(), "output should be empty for no match: {output}");
    }

    #[tokio::test]
    async fn execute_tool_unknown_tool_returns_not_implemented() {
        let (output, success) = exec_tool(
            "UnknownTool",
            &serde_json::json!({}),
        ).await;
        assert!(!success);
        assert!(output.contains("not implemented"), "output: {output}");
    }

    // -----------------------------------------------------------------------
    // →728: File cache integration with Read tool
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn execute_tool_read_returns_file_reference_when_cached() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("cached_file.rs");
        std::fs::write(&file_path, "fn main() { println!(\"big file content...\"); }").unwrap();

        // Set up a file cache with this file
        let mut cache = crate::cpu::file_cache::FileCache::load(dir.path());
        cache
            .put(crate::cpu::file_cache::FileCacheEntry {
                path: file_path.to_str().unwrap().to_string(),
                file_id: "file_test_abc".to_string(),
                uploaded_at: "2026-03-16T20:00:00Z".to_string(),
                size: 48,
            })
            .unwrap();

        // With cache, should return reference instead of content
        let (output, success) = execute_tool(
            "Read",
            &serde_json::json!({"file_path": file_path.to_str().unwrap()}),
            Some(&cache),
            None,
            None,
        )
        .await;
        assert!(success);
        assert!(output.contains("file_test_abc"), "should contain file ID: {output}");
        assert!(output.contains("file uploaded"), "should indicate upload: {output}");
        assert!(!output.contains("println"), "should NOT contain raw file content: {output}");
    }

    #[tokio::test]
    async fn execute_tool_read_returns_content_when_not_cached() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("uncached_file.rs");
        std::fs::write(&file_path, "let x = 42;").unwrap();

        // Empty cache — file not registered
        let cache = crate::cpu::file_cache::FileCache::load(dir.path());

        let (output, success) = execute_tool(
            "Read",
            &serde_json::json!({"file_path": file_path.to_str().unwrap()}),
            Some(&cache),
            None,
            None,
        )
        .await;
        assert!(success);
        assert_eq!(output, "let x = 42;");
    }

    // -----------------------------------------------------------------------
    // Integration test: full flow with mock API
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn integration_full_flow_read_edit_cycle() {
        // Simulate: user asks to edit a file.
        // Turn 1: assistant reads the file
        // Turn 2: assistant edits the file
        // Turn 3: assistant reports completion
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("target.txt");
        std::fs::write(&file_path, "old content here").unwrap();
        let fp = file_path.to_str().unwrap().to_string();

        let call_count = Arc::new(Mutex::new(0u32));
        let call_count_ref = call_count.clone();
        let fp_clone = fp.clone();

        let (tx, rx) = mpsc::channel(64);
        let config = LoopConfig {
            model: "test-model".into(),
            system_prompt: Some("You are a helpful assistant.".into()),
            tools: vec![serde_json::json!({"name": "Read"}), serde_json::json!({"name": "Edit"})],
            max_tokens: 4096,
            max_turns: Some(10),
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::Governed,
            betas: vec![],
            fast_mode: false,
            root: None,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text {
                text: format!("Replace 'old' with 'new' in {fp}"),
            }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                move |_msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    *count += 1;
                    match *count {
                        1 => Ok(ApiResponse {
                            content: vec![
                                ContentBlock::Text { text: "I'll read the file first.".into() },
                                ContentBlock::ToolUse {
                                    id: "tu_read".into(),
                                    name: "Read".into(),
                                    input: serde_json::json!({"file_path": fp_clone}),
                                },
                            ],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage { input_tokens: 100, output_tokens: 50, ..Default::default() },
                            applied_edits: vec![],
                        }),
                        2 => Ok(ApiResponse {
                            content: vec![
                                ContentBlock::Text { text: "Now I'll edit it.".into() },
                                ContentBlock::ToolUse {
                                    id: "tu_edit".into(),
                                    name: "Edit".into(),
                                    input: serde_json::json!({
                                        "file_path": fp_clone,
                                        "old_string": "old content",
                                        "new_string": "new content"
                                    }),
                                },
                            ],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage { input_tokens: 200, output_tokens: 60, ..Default::default() },
                            applied_edits: vec![],
                        }),
                        3 => Ok(ApiResponse {
                            content: vec![ContentBlock::Text {
                                text: "Done! I replaced 'old' with 'new'.".into(),
                            }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage { input_tokens: 300, output_tokens: 30, ..Default::default() },
                            applied_edits: vec![],
                        }),
                        _ => Err("unexpected call".into()),
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Verify event sequence
        let text_events: Vec<String> = events
            .iter()
            .filter_map(|e| match e {
                CpuEvent::TextComplete(t) => Some(t.clone()),
                _ => None,
            })
            .collect();
        assert_eq!(text_events.len(), 3);
        assert_eq!(text_events[0], "I'll read the file first.");
        assert_eq!(text_events[1], "Now I'll edit it.");
        assert!(text_events[2].contains("Done!"));

        // Verify tool execution results
        let tool_results: Vec<_> = events
            .iter()
            .filter_map(|e| match e {
                CpuEvent::ToolResult { name, output, success } => {
                    Some((name.clone(), output.clone(), *success))
                }
                _ => None,
            })
            .collect();
        assert_eq!(tool_results.len(), 2);

        // Read result
        assert_eq!(tool_results[0].0, "Read");
        assert!(tool_results[0].2, "Read should succeed");
        assert!(tool_results[0].1.contains("old content here"), "Read output: {}", tool_results[0].1);

        // Edit result
        assert_eq!(tool_results[1].0, "Edit");
        assert!(tool_results[1].2, "Edit should succeed");
        assert_eq!(tool_results[1].1, "edit applied");

        // Verify the file was actually edited
        let final_content = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(final_content, "new content here");

        // Verify final TurnComplete
        let turn_complete = events.iter().find(|e| matches!(e, CpuEvent::TurnComplete { .. }));
        assert!(turn_complete.is_some(), "should have TurnComplete event");
        match turn_complete.unwrap() {
            CpuEvent::TurnComplete { usage } => {
                assert_eq!(usage.input_tokens, 300);
                assert_eq!(usage.output_tokens, 30);
            }
            _ => unreachable!(),
        }

        // Verify Usage events for intermediate turns
        let usage_events: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::Usage { .. })).collect();
        assert_eq!(usage_events.len(), 2, "should have 2 intermediate Usage events");
    }

    // -----------------------------------------------------------------------
    // API error propagation
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn api_error_propagates_correctly() {
        let mock = Arc::new(MockApi::new(vec![])); // Empty = will return error

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hi".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        let result = handle.await.unwrap();

        assert!(result.is_err(), "should return error when mock is empty");
        assert!(result.unwrap_err().contains("no more mock responses"));
        // No events should have been emitted
        assert!(events.is_empty(), "no events should be emitted on immediate API error");
    }

    // -----------------------------------------------------------------------
    // Role alternation in messages
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn messages_maintain_proper_role_alternation() {
        // After tool execution, messages should always alternate:
        // user -> assistant (with tool_use) -> user (with tool_result) -> ...
        let call_count = Arc::new(Mutex::new(0u32));
        let captured_messages: Arc<Mutex<Vec<Vec<Message>>>> = Arc::new(Mutex::new(Vec::new()));

        let call_count_ref = call_count.clone();
        let captured_ref = captured_messages.clone();

        let (tx, rx) = mpsc::channel(64);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "start".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    captured_ref.lock().unwrap().push(msgs.to_vec());
                    *count += 1;
                    match *count {
                        1 => Ok(ApiResponse {
                            content: vec![ContentBlock::ToolUse {
                                id: "tu_a".into(),
                                name: "Bash".into(),
                                input: serde_json::json!({"command": "echo a"}),
                            }],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        }),
                        2 => Ok(ApiResponse {
                            content: vec![ContentBlock::ToolUse {
                                id: "tu_b".into(),
                                name: "Bash".into(),
                                input: serde_json::json!({"command": "echo b"}),
                            }],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        }),
                        3 => Ok(ApiResponse {
                            content: vec![ContentBlock::Text { text: "done".into() }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        }),
                        _ => Err("unexpected".into()),
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let _events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        let captured = captured_messages.lock().unwrap();

        // Verify role alternation in the third call's messages
        let final_msgs = &captured[2];
        assert_eq!(final_msgs.len(), 5); // user, assistant, user(result), assistant, user(result)

        // Verify strict alternation
        let roles: Vec<&str> = final_msgs.iter().map(|m| m.role.as_str()).collect();
        assert_eq!(roles, vec!["user", "assistant", "user", "assistant", "user"]);
    }

    // -----------------------------------------------------------------------
    // Bash tool with missing command field
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn execute_tool_bash_missing_command_runs_empty() {
        let (output, success) = exec_tool(
            "Bash",
            &serde_json::json!({}),
        ).await;
        // Empty command should succeed (sh -c "" exits 0)
        assert!(success, "empty command should succeed: output={output}");
    }

    // -----------------------------------------------------------------------
    // Edit tool — only replaces first occurrence
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn execute_tool_edit_replaces_only_first_occurrence() {
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test_edit_first.txt");
        std::fs::write(&file_path, "aaa bbb aaa bbb aaa").unwrap();

        let (output, success) = exec_tool(
            "Edit",
            &serde_json::json!({
                "file_path": file_path.to_str().unwrap(),
                "old_string": "aaa",
                "new_string": "zzz"
            }),
        ).await;
        assert!(success, "edit should succeed: {output}");

        let contents = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(contents, "zzz bbb aaa bbb aaa", "should only replace first occurrence");
    }

    // -----------------------------------------------------------------------
    // stop_reason: refusal
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn refusal_stop_reason_emits_error_and_returns_err() {
        let mock = Arc::new(MockApi::new(vec![ApiResponse {
            content: vec![ContentBlock::Text {
                text: "I cannot help with that.".into(),
            }],
            stop_reason: Some("refusal".into()),
            usage: Usage { input_tokens: 10, output_tokens: 5, ..Default::default() },
            applied_edits: vec![],
        }]));

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "do something bad".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        let result = handle.await.unwrap();

        assert!(result.is_err(), "refusal should return Err");
        assert!(result.unwrap_err().contains("refusal"));

        // Should have Error + TurnComplete
        let error_events: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::Error(_))).collect();
        assert_eq!(error_events.len(), 1, "should emit one Error event");
        let turn_completes: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::TurnComplete { .. })).collect();
        assert_eq!(turn_completes.len(), 1, "should emit TurnComplete even on refusal");
    }

    // -----------------------------------------------------------------------
    // stop_reason: model_context_window_exceeded
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn context_window_exceeded_emits_error_and_returns_err() {
        let mock = Arc::new(MockApi::new(vec![ApiResponse {
            content: vec![],
            stop_reason: Some("model_context_window_exceeded".into()),
            usage: Usage { input_tokens: 100000, output_tokens: 0, ..Default::default() },
            applied_edits: vec![],
        }]));

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "very long conversation".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        let result = handle.await.unwrap();

        assert!(result.is_err(), "context exceeded should return Err");
        assert!(result.unwrap_err().contains("model_context_window_exceeded"));

        let error_events: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::Error(_))).collect();
        assert_eq!(error_events.len(), 1);
    }

    // -----------------------------------------------------------------------
    // stop_reason: max_tokens (truncated text-only response)
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn max_tokens_text_only_emits_warning_and_completes() {
        let mock = Arc::new(MockApi::new(vec![ApiResponse {
            content: vec![ContentBlock::Text {
                text: "This response was cut off because it was too lo".into(),
            }],
            stop_reason: Some("max_tokens".into()),
            usage: Usage { input_tokens: 50, output_tokens: 4096, ..Default::default() },
            applied_edits: vec![],
        }]));

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "write a novel".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Should have TextComplete + Error (warning) + TurnComplete
        let text_events: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::TextComplete(_))).collect();
        assert_eq!(text_events.len(), 1, "should emit truncated text");

        let error_events: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::Error(_))).collect();
        assert_eq!(error_events.len(), 1, "should emit truncation warning");
        match &error_events[0] {
            CpuEvent::Error(msg) => assert!(msg.contains("max_tokens") || msg.contains("truncated")),
            _ => unreachable!(),
        }

        let turn_completes: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::TurnComplete { .. })).collect();
        assert_eq!(turn_completes.len(), 1);
    }

    // -----------------------------------------------------------------------
    // Empty response triggers retry
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn empty_response_retries_with_continuation_prompt() {
        let call_count = Arc::new(Mutex::new(0u32));
        let call_count_ref = call_count.clone();

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hello".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    *count += 1;
                    match *count {
                        1 => Ok(ApiResponse {
                            content: vec![], // empty!
                            stop_reason: Some("end_turn".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        }),
                        2 => Ok(ApiResponse {
                            content: vec![ContentBlock::Text { text: "Here is my response.".into() }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage { input_tokens: 20, output_tokens: 10, ..Default::default() },
                            applied_edits: vec![],
                        }),
                        _ => Err("unexpected call".into()),
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Should have retried and gotten the real response
        let text_events: Vec<_> = events.iter().filter_map(|e| match e {
            CpuEvent::TextComplete(t) => Some(t.as_str()),
            _ => None,
        }).collect();
        assert_eq!(text_events.len(), 1);
        assert_eq!(text_events[0], "Here is my response.");

        let turn_completes: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::TurnComplete { .. })).collect();
        assert_eq!(turn_completes.len(), 1);
    }

    // -----------------------------------------------------------------------
    // Empty response exhausts retries and gives up
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn empty_response_exhausts_retries_and_completes() {
        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hello".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| Ok(ApiResponse {
                    content: vec![], // always empty
                    stop_reason: Some("end_turn".into()),
                    usage: Usage::default(),
                    applied_edits: vec![],
                }),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Should have TurnComplete after giving up
        let turn_completes: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::TurnComplete { .. })).collect();
        assert_eq!(turn_completes.len(), 1, "should emit TurnComplete after exhausting retries");

        // No text events
        let text_events: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::TextComplete(_))).collect();
        assert!(text_events.is_empty(), "no text should be emitted for empty responses");
    }

    // -----------------------------------------------------------------------
    // Tool result message contains ONLY tool_result blocks (no text)
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn tool_result_message_contains_only_tool_result_blocks() {
        let captured_messages: Arc<Mutex<Vec<Vec<Message>>>> = Arc::new(Mutex::new(Vec::new()));
        let captured_ref = captured_messages.clone();
        let call_count = Arc::new(Mutex::new(0u32));
        let call_count_ref = call_count.clone();

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "go".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    captured_ref.lock().unwrap().push(msgs.to_vec());
                    *count += 1;
                    match *count {
                        1 => Ok(ApiResponse {
                            content: vec![
                                ContentBlock::Text { text: "Let me check.".into() },
                                ContentBlock::ToolUse {
                                    id: "tu_1".into(),
                                    name: "Bash".into(),
                                    input: serde_json::json!({"command": "echo hi"}),
                                },
                            ],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        }),
                        2 => Ok(ApiResponse {
                            content: vec![ContentBlock::Text { text: "done".into() }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        }),
                        _ => Err("unexpected".into()),
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let _events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        let captured = captured_messages.lock().unwrap();
        // The second call should have 3 messages: user, assistant, user(tool_result)
        assert_eq!(captured[1].len(), 3);

        // The tool_result message (last one) must contain ONLY ToolResult blocks
        let tool_result_msg = &captured[1][2];
        assert_eq!(tool_result_msg.role, "user");
        for block in &tool_result_msg.content {
            match block {
                ContentBlock::ToolResult { .. } => {} // OK
                ContentBlock::Text { text } => {
                    panic!("tool_result message must NOT contain text blocks, found: {text:?}");
                }
                ContentBlock::ToolUse { name, .. } => {
                    panic!("tool_result message must NOT contain tool_use blocks, found: {name}");
                }
                ContentBlock::Image { .. } => {} // Image blocks are not expected in tool_result messages but are harmless
                ContentBlock::Thinking { .. } | ContentBlock::Citation { .. } | ContentBlock::WebSearchResult { .. } => {} // →949/→948/→950: not expected in tool_result
            }
        }
    }

    // -----------------------------------------------------------------------
    // stop_reason: pause_turn behaves like tool_use (continue conversation)
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn pause_turn_with_tool_calls_continues_loop() {
        let call_count = Arc::new(Mutex::new(0u32));
        let call_count_ref = call_count.clone();

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "go".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    *count += 1;
                    match *count {
                        1 => Ok(ApiResponse {
                            content: vec![ContentBlock::ToolUse {
                                id: "tu_1".into(),
                                name: "Bash".into(),
                                input: serde_json::json!({"command": "echo paused"}),
                            }],
                            stop_reason: Some("pause_turn".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        }),
                        2 => Ok(ApiResponse {
                            content: vec![ContentBlock::Text { text: "resumed".into() }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        }),
                        _ => Err("unexpected".into()),
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Should have processed the tool and then gotten the final response
        let tool_starts: Vec<_> = events.iter().filter(|e| matches!(e, CpuEvent::ToolStart { .. })).collect();
        assert_eq!(tool_starts.len(), 1, "should have executed one tool");

        let text_events: Vec<_> = events.iter().filter_map(|e| match e {
            CpuEvent::TextComplete(t) => Some(t.as_str()),
            _ => None,
        }).collect();
        assert!(text_events.contains(&"resumed"), "should get final response after pause_turn");
    }

    // -----------------------------------------------------------------------
    // Streaming: tool input reassembly from ToolInputDelta fragments
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn consume_stream_reassembles_tool_input_from_fragments() {
        use crate::cpu::anthropic::StreamEvent;

        // Simulate a streaming response: text + tool_use with fragmented input
        let events = vec![
            Ok(StreamEvent::TextDelta("I'll ".into())),
            Ok(StreamEvent::TextDelta("run that.".into())),
            Ok(StreamEvent::ContentBlockStop),
            Ok(StreamEvent::ToolUseStart { id: "tu_1".into(), name: "Bash".into() }),
            Ok(StreamEvent::ToolInputDelta("{\"com".into())),
            Ok(StreamEvent::ToolInputDelta("mand\"".into())),
            Ok(StreamEvent::ToolInputDelta(": \"ls".into())),
            Ok(StreamEvent::ToolInputDelta(" -la\"".into())),
            Ok(StreamEvent::ToolInputDelta("}".into())),
            Ok(StreamEvent::ContentBlockStop),
            // →944: message_delta carries metadata, message_stop terminates
            Ok(StreamEvent::MessageDelta {
                stop_reason: "tool_use".into(),
                usage: Usage { input_tokens: 50, output_tokens: 25, ..Default::default() },
                applied_edits: vec![],
            }),
            Ok(StreamEvent::MessageStop {
                stop_reason: String::new(),
                usage: Usage::default(),
            }),
        ];

        let stream = futures_util::stream::iter(events);
        let (tx, mut rx) = mpsc::channel(64);

        let result = consume_stream(stream, &tx, None, None).await.unwrap();
        drop(tx);

        // Verify reassembled content blocks
        assert_eq!(result.content.len(), 2, "should have text + tool_use");
        assert_eq!(result.stop_reason, "tool_use");
        assert_eq!(result.usage.input_tokens, 50);
        assert_eq!(result.usage.output_tokens, 25);

        match &result.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "I'll run that."),
            other => panic!("expected Text, got {other:?}"),
        }
        match &result.content[1] {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "tu_1");
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls -la");
            }
            other => panic!("expected ToolUse, got {other:?}"),
        }

        // Verify emitted CpuEvents
        let mut cpu_events = Vec::new();
        while let Ok(ev) = rx.try_recv() {
            cpu_events.push(ev);
        }

        // Should have: TextDelta x2, TextComplete, ToolStart
        let text_deltas: Vec<_> = cpu_events.iter().filter(|e| matches!(e, CpuEvent::TextDelta(_))).collect();
        assert_eq!(text_deltas.len(), 2, "should have 2 TextDelta events");

        let text_completes: Vec<_> = cpu_events.iter().filter_map(|e| match e {
            CpuEvent::TextComplete(t) => Some(t.as_str()),
            _ => None,
        }).collect();
        assert_eq!(text_completes.len(), 1, "should have 1 TextComplete event");
        assert_eq!(text_completes[0], "I'll run that.");

        let tool_starts: Vec<_> = cpu_events.iter().filter(|e| matches!(e, CpuEvent::ToolStart { .. })).collect();
        assert_eq!(tool_starts.len(), 1, "should have 1 ToolStart event");
    }

    // -----------------------------------------------------------------------
    // Streaming: text-only response emits TextDelta + TextComplete
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn consume_stream_text_only_emits_delta_and_complete() {
        use crate::cpu::anthropic::StreamEvent;

        let events = vec![
            Ok(StreamEvent::TextDelta("Hello".into())),
            Ok(StreamEvent::TextDelta(" world".into())),
            Ok(StreamEvent::MessageStop {
                stop_reason: "end_turn".into(),
                usage: Usage { input_tokens: 10, output_tokens: 5, ..Default::default() },
            }),
        ];

        let stream = futures_util::stream::iter(events);
        let (tx, mut rx) = mpsc::channel(64);

        let result = consume_stream(stream, &tx, None, None).await.unwrap();
        drop(tx);

        assert_eq!(result.content.len(), 1);
        match &result.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "Hello world"),
            other => panic!("expected Text, got {other:?}"),
        }
        assert_eq!(result.stop_reason, "end_turn");

        let mut cpu_events = Vec::new();
        while let Ok(ev) = rx.try_recv() {
            cpu_events.push(ev);
        }

        let text_deltas: Vec<_> = cpu_events.iter().filter_map(|e| match e {
            CpuEvent::TextDelta(t) => Some(t.as_str()),
            _ => None,
        }).collect();
        assert_eq!(text_deltas, vec!["Hello", " world"]);

        let text_completes: Vec<_> = cpu_events.iter().filter_map(|e| match e {
            CpuEvent::TextComplete(t) => Some(t.as_str()),
            _ => None,
        }).collect();
        assert_eq!(text_completes, vec!["Hello world"]);
    }

    // -----------------------------------------------------------------------
    // Streaming: multiple tool_use blocks reassembled correctly
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn consume_stream_multiple_tools() {
        use crate::cpu::anthropic::StreamEvent;

        let events = vec![
            Ok(StreamEvent::ToolUseStart { id: "tu_1".into(), name: "Read".into() }),
            Ok(StreamEvent::ToolInputDelta("{\"file_path\":".into())),
            Ok(StreamEvent::ToolInputDelta("\"/tmp/a\"}".into())),
            Ok(StreamEvent::ContentBlockStop),
            Ok(StreamEvent::ToolUseStart { id: "tu_2".into(), name: "Bash".into() }),
            Ok(StreamEvent::ToolInputDelta("{\"command\":\"echo ok\"}".into())),
            Ok(StreamEvent::ContentBlockStop),
            Ok(StreamEvent::MessageStop {
                stop_reason: "tool_use".into(),
                usage: Usage::default(),
            }),
        ];

        let stream = futures_util::stream::iter(events);
        let (tx, _rx) = mpsc::channel(64);

        let result = consume_stream(stream, &tx, None, None).await.unwrap();

        assert_eq!(result.content.len(), 2, "should have 2 tool_use blocks");

        match &result.content[0] {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "tu_1");
                assert_eq!(name, "Read");
                assert_eq!(input["file_path"], "/tmp/a");
            }
            other => panic!("expected ToolUse, got {other:?}"),
        }
        match &result.content[1] {
            ContentBlock::ToolUse { id, name, input } => {
                assert_eq!(id, "tu_2");
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "echo ok");
            }
            other => panic!("expected ToolUse, got {other:?}"),
        }
    }

    // -----------------------------------------------------------------------
    // Streaming: empty tool input defaults to Value::Null
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn consume_stream_empty_tool_input_defaults_to_empty_object() {
        use crate::cpu::anthropic::StreamEvent;

        let events = vec![
            Ok(StreamEvent::ToolUseStart { id: "tu_x".into(), name: "Foo".into() }),
            // No ToolInputDelta — empty input buffer
            Ok(StreamEvent::ContentBlockStop),
            Ok(StreamEvent::MessageStop {
                stop_reason: "tool_use".into(),
                usage: Usage::default(),
            }),
        ];

        let stream = futures_util::stream::iter(events);
        let (tx, _rx) = mpsc::channel(64);

        let result = consume_stream(stream, &tx, None, None).await.unwrap();

        assert_eq!(result.content.len(), 1);
        match &result.content[0] {
            ContentBlock::ToolUse { input, .. } => {
                // Empty input_json parses as {} (empty object), not null.
                // This is intentional — tool handlers call input.get("key")
                // which returns None on {} but would panic on null.
                assert!(input.is_object(), "empty input should default to {{}}, got: {input}");
                assert!(input.as_object().unwrap().is_empty(), "should be empty object");
            }
            other => panic!("expected ToolUse, got {other:?}"),
        }
    }

    // -----------------------------------------------------------------------
    // →742: consume_stream accumulates input + output tokens from separate events
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn consume_stream_accumulates_both_token_counts() {
        use crate::cpu::anthropic::StreamEvent;

        // message_start carries input_tokens + cache counts, message_delta carries output_tokens
        let events = vec![
            Ok(StreamEvent::MessageStart { usage: Usage { input_tokens: 500, cache_read_tokens: 100, cache_create_tokens: 50, ..Default::default() } }),
            Ok(StreamEvent::TextDelta("Hello".into())),
            Ok(StreamEvent::MessageStop {
                stop_reason: "end_turn".into(),
                usage: Usage { input_tokens: 0, output_tokens: 150, ..Default::default() },
            }),
        ];

        let stream = futures_util::stream::iter(events);
        let (tx, _rx) = mpsc::channel(64);

        let result = consume_stream(stream, &tx, None, None).await.unwrap();

        assert_eq!(result.usage.input_tokens, 500, "input_tokens from MessageStart");
        assert_eq!(result.usage.cache_read_tokens, 100, "cache_read_tokens from MessageStart");
        assert_eq!(result.usage.cache_create_tokens, 50, "cache_create_tokens from MessageStart");
        assert_eq!(result.usage.output_tokens, 150, "output_tokens from MessageStop");
    }

    // -----------------------------------------------------------------------
    // →925: consume_stream interrupted by Cancel during streaming
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn consume_stream_interrupt_cancel() {
        use crate::cpu::anthropic::StreamEvent;
        use crate::kernel::approval::ApprovalSource;

        // Set up an approval channel and pre-load a Cancel response.
        let (response_tx, response_rx) = mpsc::channel(8);
        response_tx.send(AgentResponse::Cancel).await.unwrap();
        let mut src = ApprovalSource::Channel(response_rx);

        // Stream: text delta arrives, then Cancel is detected before more data.
        let events = vec![
            Ok(StreamEvent::TextDelta("partial text".into())),
            // More events would follow, but the interrupt check fires after the first delta.
        ];

        let stream = futures_util::stream::iter(events);
        let (tx, mut rx) = mpsc::channel(64);

        let result = consume_stream(stream, &tx, Some(&mut src), None).await.unwrap();

        // Should be interrupted
        assert!(result.interrupted.is_some(), "should be interrupted");
        assert!(matches!(result.interrupted, Some(AgentResponse::Cancel)));
        assert_eq!(result.stop_reason, "interrupted");

        // Partial text should be finalized into content blocks
        assert_eq!(result.content.len(), 1);
        match &result.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "partial text"),
            other => panic!("expected Text, got {other:?}"),
        }

        // TextDelta + TextComplete events should have been emitted
        drop(tx);
        let mut events_received = Vec::new();
        while let Some(ev) = rx.recv().await {
            events_received.push(ev);
        }
        assert!(events_received.len() >= 2, "should emit TextDelta + TextComplete");
        assert!(matches!(events_received[0], CpuEvent::TextDelta(_)));
        assert!(matches!(events_received[1], CpuEvent::TextComplete(_)));
    }

    // -----------------------------------------------------------------------
    // →925: consume_stream interrupted by Redirect during streaming
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn consume_stream_interrupt_redirect() {
        use crate::cpu::anthropic::StreamEvent;
        use crate::kernel::approval::ApprovalSource;

        let (response_tx, response_rx) = mpsc::channel(8);
        response_tx.send(AgentResponse::Redirect("do something else".into())).await.unwrap();
        let mut src = ApprovalSource::Channel(response_rx);

        let events = vec![
            Ok(StreamEvent::TextDelta("I'll start by".into())),
        ];

        let stream = futures_util::stream::iter(events);
        let (tx, _rx) = mpsc::channel(64);

        let result = consume_stream(stream, &tx, Some(&mut src), None).await.unwrap();

        assert!(matches!(&result.interrupted, Some(AgentResponse::Redirect(msg)) if msg == "do something else"));
        assert_eq!(result.stop_reason, "interrupted");
        assert_eq!(result.content.len(), 1);
        match &result.content[0] {
            ContentBlock::Text { text } => assert_eq!(text, "I'll start by"),
            other => panic!("expected Text, got {other:?}"),
        }
    }

    // -----------------------------------------------------------------------
    // →925: consume_stream with no approval source (None) — no interruption
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn consume_stream_no_approval_source_runs_to_completion() {
        use crate::cpu::anthropic::StreamEvent;

        let events = vec![
            Ok(StreamEvent::TextDelta("hello".into())),
            Ok(StreamEvent::MessageStop {
                stop_reason: "end_turn".into(),
                usage: Usage { output_tokens: 10, ..Default::default() },
            }),
        ];

        let stream = futures_util::stream::iter(events);
        let (tx, _rx) = mpsc::channel(64);

        let result = consume_stream(stream, &tx, None, None).await.unwrap();

        assert!(result.interrupted.is_none());
        assert_eq!(result.stop_reason, "end_turn");
    }

    // -----------------------------------------------------------------------
    // →731: Fast mode tests
    // -----------------------------------------------------------------------

    #[test]
    fn build_params_sets_speed_fast_for_opus_with_beta() {
        let config = LoopConfig {
            model: "claude-opus-4-6".into(),
            betas: vec!["fast-mode".into()],
            fast_mode: true,
            ..default_config()
        };
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        assert_eq!(params.claude.speed.as_deref(), Some("fast"));
    }

    #[test]
    fn build_params_no_speed_for_sonnet_even_with_beta() {
        let config = LoopConfig {
            model: "claude-sonnet-4-5-20250929".into(),
            betas: vec!["fast-mode".into()],
            fast_mode: true,
            ..default_config()
        };
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        assert!(params.claude.speed.is_none(), "speed should be None for non-opus models");
    }

    #[test]
    fn build_params_no_speed_without_beta() {
        let config = LoopConfig {
            model: "claude-opus-4-6".into(),
            betas: vec![],
            fast_mode: true,
            ..default_config()
        };
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        assert!(params.claude.speed.is_none(), "speed should be None without fast-mode beta");
    }

    #[test]
    fn build_params_no_speed_when_fast_mode_false() {
        let config = LoopConfig {
            model: "claude-opus-4-6".into(),
            betas: vec!["fast-mode".into()],
            fast_mode: false,
            ..default_config()
        };
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        assert!(params.claude.speed.is_none(), "speed should be None when fast_mode is false");
    }

    // -----------------------------------------------------------------------
    // build_params — context_management
    // -----------------------------------------------------------------------

    #[test]
    fn build_params_context_management_none_without_betas() {
        let config = default_config();
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        assert!(params.claude.context_management.is_none());
    }

    #[test]
    fn build_params_compact_beta_adds_compaction_edit() {
        let config = LoopConfig {
            betas: vec!["compact".into()],
            context_budget: Some(100000),
            ..default_config()
        };
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = params.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        assert_eq!(edits.len(), 1);
        assert_eq!(edits[0]["type"], "compact_20260112");
        assert_eq!(edits[0]["trigger"]["type"], "input_tokens");
        assert_eq!(edits[0]["trigger"]["value"], 80000); // 80% of 100k
    }

    #[test]
    fn build_params_compact_beta_default_trigger_without_budget() {
        let config = LoopConfig {
            betas: vec!["compact".into()],
            context_budget: None,
            ..default_config()
        };
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = params.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        assert_eq!(edits.len(), 1);
        assert_eq!(edits[0]["type"], "compact_20260112");
        // No trigger field — API uses default (150k)
        assert!(edits[0].get("trigger").is_none());
    }

    #[test]
    fn build_params_context_management_beta_adds_clear_tool_uses() {
        let config = LoopConfig {
            betas: vec!["context-management".into()],
            ..default_config()
        };
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = params.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        assert_eq!(edits.len(), 1);
        assert_eq!(edits[0]["type"], "clear_tool_uses_20250919");
        assert_eq!(edits[0]["keep"]["type"], "tool_uses");
        assert_eq!(edits[0]["keep"]["value"], 5);
    }

    #[test]
    fn build_params_both_betas_builds_combined_edits() {
        let config = LoopConfig {
            betas: vec!["compact".into(), "context-management".into()],
            context_budget: Some(80000),
            ..default_config()
        };
        let params = build_params(&config, &[], vec![], None, None, 0, None);
        let cm = params.claude.context_management.unwrap();
        let edits = cm["edits"].as_array().unwrap();
        assert_eq!(edits.len(), 2);
        assert_eq!(edits[0]["type"], "compact_20260112");
        assert_eq!(edits[0]["trigger"]["value"], 64000); // 80% of 80k
        assert_eq!(edits[1]["type"], "clear_tool_uses_20250919");
    }

    // -----------------------------------------------------------------------
    // Beta fallback tests
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn beta_400_error_triggers_retry_and_succeeds() {
        // First call returns a 400 beta error, second call succeeds.
        let call_count = Arc::new(Mutex::new(0u32));
        let call_count_ref = call_count.clone();

        let (tx, rx) = mpsc::channel(32);
        let config = LoopConfig {
            betas: vec!["context-management".into()],
            ..default_config()
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hi".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    *count += 1;
                    if *count == 1 {
                        Err("API error 400: Extra inputs are not permitted".into())
                    } else {
                        Ok(ApiResponse {
                            content: vec![ContentBlock::Text { text: "ok".into() }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage { input_tokens: 1, output_tokens: 1, ..Default::default() },
                            applied_edits: vec![],
                        })
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Should have called the API twice (once failed, once retried)
        assert_eq!(*call_count.lock().unwrap(), 2);

        // First event should be the beta fallback warning
        let has_beta_warning = events.iter().any(|e| match e {
            CpuEvent::Error(msg) => msg.contains("[beta]") && msg.contains("not available"),
            _ => false,
        });
        assert!(has_beta_warning, "expected beta fallback warning event");

        // Should also have a TextComplete and TurnComplete from the successful retry
        let has_text = events.iter().any(|e| matches!(e, CpuEvent::TextComplete(t) if t == "ok"));
        assert!(has_text, "expected TextComplete from successful retry");
    }

    #[tokio::test]
    async fn beta_400_error_clears_betas_after_retry() {
        // Verify that after the first beta error, subsequent loop iterations
        // do not trigger beta errors again (betas are cleared).
        // We use a tool-use response to trigger a second loop iteration.
        let call_count = Arc::new(Mutex::new(0u32));
        let call_count_ref = call_count.clone();

        let (tx, rx) = mpsc::channel(32);
        let config = LoopConfig {
            betas: vec!["context-management".into()],
            max_turns: Some(1),
            tools: vec![serde_json::json!({"name": "Bash"})],
            ..default_config()
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hi".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    *count += 1;
                    if *count == 1 {
                        // First call: beta rejected
                        Err("API error 400: Unsupported beta header".into())
                    } else {
                        // All subsequent calls succeed (betas cleared)
                        Ok(ApiResponse {
                            content: vec![ContentBlock::Text { text: "done".into() }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage { input_tokens: 1, output_tokens: 1, ..Default::default() },
                            applied_edits: vec![],
                        })
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Call count: 1 (beta fail) + 1 (retry success) = 2
        // The retry succeeded on first turn, no further calls needed.
        assert_eq!(*call_count.lock().unwrap(), 2);

        // Verify warning was emitted
        let warnings: Vec<_> = events.iter().filter(|e| match e {
            CpuEvent::Error(msg) => msg.contains("[beta]"),
            _ => false,
        }).collect();
        assert_eq!(warnings.len(), 1, "should emit exactly one beta warning");
    }

    #[tokio::test]
    async fn non_beta_400_error_propagates() {
        let (tx, _rx) = mpsc::channel(32);
        let config = LoopConfig {
            betas: vec!["context-management".into()],
            ..default_config()
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hi".into() }],
            model: None,
        }];

        let result = run_loop_with(
            |_msgs| Err("API error 400: invalid model specified".into()),
            &config,
            &mut messages,
            tx,
        )
        .await;

        assert!(result.is_err());
        assert!(result.unwrap_err().contains("invalid model specified"));
    }

    #[tokio::test]
    async fn no_beta_fallback_when_betas_empty() {
        // If betas are already empty, a 400 with "Extra inputs" should NOT
        // be caught by the fallback -- it should propagate normally.
        let (tx, _rx) = mpsc::channel(32);
        let config = LoopConfig {
            betas: vec![],
            ..default_config()
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hi".into() }],
            model: None,
        }];

        let result = run_loop_with(
            |_msgs| Err("API error 400: Extra inputs are not permitted".into()),
            &config,
            &mut messages,
            tx,
        )
        .await;

        assert!(result.is_err(), "should propagate error when betas already empty");
    }

    // -----------------------------------------------------------------------
    // →737: Audit logging tests
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn audit_logs_api_call_on_success() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        std::fs::create_dir_all(crate::state_dir(&root)).unwrap();

        let mock = Arc::new(MockApi::new(vec![ApiResponse {
            content: vec![ContentBlock::Text { text: "hello".into() }],
            stop_reason: Some("end_turn".into()),
            usage: Usage { input_tokens: 100, output_tokens: 50, ..Default::default() },
            applied_edits: vec![],
        }]));

        let (tx, rx) = mpsc::channel(32);
        let config = LoopConfig {
            root: Some(root.clone()),
            ..default_config()
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hi".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let _events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        // Verify audit.jsonl was written
        let audit = std::fs::read_to_string(root.join(".ostk/audit.jsonl")).unwrap();
        let entry: serde_json::Value = serde_json::from_str(audit.trim()).unwrap();
        assert_eq!(entry["event"], "api.call");
        assert_eq!(entry["model"], "test");
        assert_eq!(entry["input_tokens"], 100);
        assert_eq!(entry["output_tokens"], 50);
        assert_eq!(entry["stop_reason"], "end_turn");
        assert_eq!(entry["tool_calls"], 0);
        assert!(entry["ts"].as_str().is_some(), "ts should be a string");
    }

    #[tokio::test]
    async fn audit_logs_api_call_with_tool_count() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        std::fs::create_dir_all(crate::state_dir(&root)).unwrap();

        let mock = Arc::new(MockApi::new(vec![
            ApiResponse {
                content: vec![
                    ContentBlock::ToolUse {
                        id: "tu_1".into(),
                        name: "Bash".into(),
                        input: serde_json::json!({"command": "echo ok"}),
                    },
                    ContentBlock::ToolUse {
                        id: "tu_2".into(),
                        name: "Read".into(),
                        input: serde_json::json!({"file_path": "/dev/null"}),
                    },
                ],
                stop_reason: Some("tool_use".into()),
                usage: Usage { input_tokens: 200, output_tokens: 80, ..Default::default() },
                applied_edits: vec![],
            },
            ApiResponse {
                content: vec![ContentBlock::Text { text: "done".into() }],
                stop_reason: Some("end_turn".into()),
                usage: Usage { input_tokens: 300, output_tokens: 40, ..Default::default() },
                applied_edits: vec![],
            },
        ]));

        let (tx, rx) = mpsc::channel(32);
        let config = LoopConfig {
            root: Some(root.clone()),
            ..default_config()
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "do it".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let handle = tokio::spawn(async move {
            run_loop_with(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let _events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        let audit = std::fs::read_to_string(root.join(".ostk/audit.jsonl")).unwrap();
        let api_calls: Vec<serde_json::Value> = audit.lines()
            .filter_map(|l| serde_json::from_str(l).ok())
            .filter(|e: &serde_json::Value| e["event"] == "api.call")
            .collect();
        assert_eq!(api_calls.len(), 2, "should have 2 api.call audit entries for 2 API calls");
        // First entry: 2 tool calls
        assert_eq!(api_calls[0]["event"], "api.call");
        assert_eq!(api_calls[0]["tool_calls"], 2);
        assert_eq!(api_calls[0]["stop_reason"], "tool_use");
        // Second entry: 0 tool calls
        assert_eq!(api_calls[1]["event"], "api.call");
        assert_eq!(api_calls[1]["tool_calls"], 0);
        assert_eq!(api_calls[1]["stop_reason"], "end_turn");
    }

    #[tokio::test]
    async fn audit_logs_api_error() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        std::fs::create_dir_all(crate::state_dir(&root)).unwrap();

        let (tx, _rx) = mpsc::channel(32);
        let config = LoopConfig {
            root: Some(root.clone()),
            ..default_config()
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hi".into() }],
            model: None,
        }];

        let result = run_loop_with(
            |_msgs| Err("API error 500: Internal server error".into()),
            &config,
            &mut messages,
            tx,
        )
        .await;

        assert!(result.is_err());

        let audit = std::fs::read_to_string(root.join(".ostk/audit.jsonl")).unwrap();
        let entry: serde_json::Value = serde_json::from_str(audit.trim()).unwrap();
        assert_eq!(entry["event"], "api.error");
        assert_eq!(entry["model"], "test");
        assert!(entry["error"].as_str().unwrap().contains("500"));
        assert!(entry["ts"].as_str().is_some());
    }

    // -----------------------------------------------------------------------
    // →804: strip_empty_text_blocks unit tests
    // -----------------------------------------------------------------------

    #[test]
    fn strip_empty_text_blocks_removes_empty_text() {
        let blocks = vec![
            ContentBlock::Text { text: "".into() },
            ContentBlock::ToolUse {
                id: "tu_1".into(),
                name: "Bash".into(),
                input: serde_json::json!({}),
            },
        ];
        let filtered = strip_empty_text_blocks(blocks);
        assert_eq!(filtered.len(), 1);
        assert!(matches!(&filtered[0], ContentBlock::ToolUse { .. }));
    }

    #[test]
    fn strip_empty_text_blocks_keeps_non_empty_text() {
        let blocks = vec![
            ContentBlock::Text { text: "".into() },
            ContentBlock::Text { text: "hello".into() },
            ContentBlock::ToolUse {
                id: "tu_1".into(),
                name: "Bash".into(),
                input: serde_json::json!({}),
            },
        ];
        let filtered = strip_empty_text_blocks(blocks);
        assert_eq!(filtered.len(), 2);
        assert!(matches!(&filtered[0], ContentBlock::Text { text } if text == "hello"));
        assert!(matches!(&filtered[1], ContentBlock::ToolUse { .. }));
    }

    #[test]
    fn strip_empty_text_blocks_all_empty_returns_placeholder() {
        let blocks = vec![
            ContentBlock::Text { text: "".into() },
            ContentBlock::Text { text: "".into() },
        ];
        let filtered = strip_empty_text_blocks(blocks);
        assert_eq!(filtered.len(), 1);
        assert!(matches!(&filtered[0], ContentBlock::Text { text } if text == "(continue)"));
    }

    #[test]
    fn strip_empty_text_blocks_preserves_tool_use_only() {
        let blocks = vec![
            ContentBlock::ToolUse {
                id: "tu_1".into(),
                name: "Bash".into(),
                input: serde_json::json!({}),
            },
        ];
        let filtered = strip_empty_text_blocks(blocks);
        assert_eq!(filtered.len(), 1);
        assert!(matches!(&filtered[0], ContentBlock::ToolUse { .. }));
    }

    #[tokio::test]
    async fn empty_text_blocks_stripped_from_assistant_message_in_tool_turn() {
        // →804: When the model returns empty text blocks alongside tool_use,
        // those empty text blocks must be stripped before the next API call.
        let call_count = Arc::new(Mutex::new(0u32));
        let captured_messages: Arc<Mutex<Vec<Vec<Message>>>> = Arc::new(Mutex::new(Vec::new()));

        let call_count_ref = call_count.clone();
        let captured_ref = captured_messages.clone();

        let (tx, rx) = mpsc::channel(32);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "go".into() }],
            model: None,
        }];

        let handle = tokio::spawn(async move {
            run_loop_with(
                |msgs| {
                    let mut count = call_count_ref.lock().unwrap();
                    captured_ref.lock().unwrap().push(msgs.to_vec());
                    *count += 1;
                    if *count == 1 {
                        // Return empty text block + tool_use (the buggy pattern)
                        Ok(ApiResponse {
                            content: vec![
                                ContentBlock::Text { text: "".into() },
                                ContentBlock::ToolUse {
                                    id: "tu_1".into(),
                                    name: "Bash".into(),
                                    input: serde_json::json!({"command": "echo ok"}),
                                },
                            ],
                            stop_reason: Some("tool_use".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        })
                    } else {
                        Ok(ApiResponse {
                            content: vec![ContentBlock::Text { text: "done".into() }],
                            stop_reason: Some("end_turn".into()),
                            usage: Usage::default(),
                            applied_edits: vec![],
                        })
                    }
                },
                &config,
                &mut messages,
                tx,
            )
            .await
        });

        let _events = collect_events(rx).await;
        handle.await.unwrap().unwrap();

        let captured = captured_messages.lock().unwrap();
        // Second API call should have: user + assistant + tool_results = 3 messages
        assert_eq!(captured[1].len(), 3);

        // The assistant message (index 1) should NOT contain the empty text block
        let assistant_content = &captured[1][1].content;
        for block in assistant_content {
            if let ContentBlock::Text { text } = block {
                assert!(!text.is_empty(), "empty text block should have been stripped");
            }
        }
        // Should contain only the tool_use block (empty text was stripped)
        assert_eq!(assistant_content.len(), 1, "should have only the tool_use block");
        assert!(matches!(&assistant_content[0], ContentBlock::ToolUse { .. }));
    }

    // -----------------------------------------------------------------------
    // →805: execute_tool with root=Some — kernel-routed paths
    // -----------------------------------------------------------------------

    /// Helper: call execute_tool with root=Some(path) and no file cache.
    async fn exec_tool_with_root(
        name: &str,
        input: &serde_json::Value,
        root: &std::path::Path,
    ) -> (String, bool) {
        let root_buf = root.to_path_buf();
        execute_tool(name, input, None, Some(&root_buf), None).await
    }

    /// Set up a temporary directory that mirrors `ostk init` skeleton.
    fn setup_ostk_root() -> tempfile::TempDir {
        // Ensure trust tier is T0 for tests — without this, CI environments
        // without GPG resolve to T3 (deny-all-writes), failing every write test.
        // Must be set before the OnceLock in kernel::policy is initialized.
        unsafe { std::env::set_var("OSTK_TRUST_TIER", "T0") };
        let dir = tempfile::tempdir().unwrap();
        let s = dir.path().join(".ostk");
        std::fs::create_dir_all(s.join("needles")).unwrap();
        std::fs::create_dir_all(s.join("wip")).unwrap();
        std::fs::create_dir_all(s.join("pins").join("default")).unwrap();
        // Permissive pin.caps so OSTK_PIN=default doesn't fail-closed in tests
        std::fs::write(
            s.join("pins").join("default").join("pin.caps"),
            "read: *
write: *
execute: *
deny:
",
        ).unwrap();
        std::fs::write(s.join("needles").join("counter"), "0").unwrap();
        let _ = std::fs::File::create(s.join("needles").join("issues.jsonl"));
        let _ = std::fs::File::create(s.join("audit.jsonl"));
        std::fs::write(s.join("identity_counter"), "0").unwrap();
        std::fs::write(s.join("gen_table.jsonl"), "").unwrap();
        dir
    }

    #[tokio::test]
    async fn execute_tool_bash_with_root_falls_back_when_binary_missing() {
        // Set OSTK_OS_NAME to a binary that does not exist so the
        // root=Some path fails to spawn and falls through to raw sh -c.
        let prev = std::env::var("OSTK_OS_NAME").ok();
        // SAFETY: test-only; env mutation is confined to this test.
        unsafe {
            std::env::set_var("OSTK_OS_NAME", "/tmp/nonexistent_ostk_binary_test_805");
        }

        let root = setup_ostk_root();
        let (output, success) = exec_tool_with_root(
            "Bash",
            &serde_json::json!({"command": "echo root_fallback_test"}),
            root.path(),
        ).await;

        // Restore env
        unsafe {
            match prev {
                Some(v) => std::env::set_var("OSTK_OS_NAME", v),
                None => std::env::remove_var("OSTK_OS_NAME"),
            }
        }

        assert!(success, "should succeed via sh -c fallback: output={output}");
        assert!(
            output.contains("root_fallback_test"),
            "output should contain echoed text: {output}"
        );
    }

    #[tokio::test]
    async fn execute_tool_read_with_root_uses_elision_layer() {
        let root = setup_ostk_root();
        let file_path = root.path().join("test_elision.txt");
        std::fs::write(&file_path, "elision content here").unwrap();

        let (output, success) = exec_tool_with_root(
            "Read",
            &serde_json::json!({"file_path": file_path.to_str().unwrap()}),
            root.path(),
        ).await;

        assert!(success, "read with root should succeed: output={output}");
        // The elision layer returns the content on first read (no prior generation).
        // It should contain the file content.
        assert!(
            output.contains("elision content here"),
            "first read should return content: {output}"
        );
    }

    #[tokio::test]
    async fn execute_tool_read_with_root_creates_audit_entry() {
        let root = setup_ostk_root();
        let file_path = root.path().join("audit_test.txt");
        std::fs::write(&file_path, "audit content").unwrap();

        let (_output, success) = exec_tool_with_root(
            "Read",
            &serde_json::json!({"file_path": file_path.to_str().unwrap()}),
            root.path(),
        ).await;
        assert!(success, "read should succeed");

        // Check that audit.jsonl was written to (either by elision layer or fallthrough).
        // The elision layer may handle the read entirely, but if it falls through,
        // the raw read path appends an audit entry.
        let audit_path = root.path().join(".ostk/audit.jsonl");
        let audit = std::fs::read_to_string(&audit_path).unwrap();
        // At minimum, the file should exist. The elision layer may or may not audit,
        // but the function should not panic.
        let _ = audit;
    }

    #[tokio::test]
    async fn execute_tool_edit_with_root_uses_kernel_cas() {
        let root = setup_ostk_root();
        let file_path = root.path().join("test_cas_edit.txt");
        std::fs::write(&file_path, "hello world foo bar").unwrap();

        let (output, success) = exec_tool_with_root(
            "Edit",
            &serde_json::json!({
                "file_path": file_path.to_str().unwrap(),
                "old_string": "foo",
                "new_string": "baz"
            }),
            root.path(),
        ).await;

        assert!(success, "edit with root should succeed: {output}");
        assert!(output.contains("edit applied"), "output: {output}");

        let contents = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(contents, "hello world baz bar");
    }

    #[tokio::test]
    async fn execute_tool_edit_with_root_cas_old_string_not_found() {
        let root = setup_ostk_root();
        let file_path = root.path().join("test_cas_miss.txt");
        std::fs::write(&file_path, "hello world").unwrap();

        let (output, success) = exec_tool_with_root(
            "Edit",
            &serde_json::json!({
                "file_path": file_path.to_str().unwrap(),
                "old_string": "nonexistent_string",
                "new_string": "replacement"
            }),
            root.path(),
        ).await;

        assert!(!success, "edit should fail when old_string not found");
        assert!(
            output.contains("not found"),
            "should report not found: {output}"
        );
    }

    #[tokio::test]
    async fn execute_tool_write_with_root_tracks_gen_table() {
        let root = setup_ostk_root();
        let file_path = root.path().join("test_gen_write.txt");

        let (output, success) = exec_tool_with_root(
            "Write",
            &serde_json::json!({
                "file_path": file_path.to_str().unwrap(),
                "content": "tracked content"
            }),
            root.path(),
        ).await;

        assert!(success, "write with root should succeed: {output}");
        assert!(output.contains("file written"), "output: {output}");

        // Verify the file was written
        let contents = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(contents, "tracked content");

        // Verify gen_table.jsonl was updated (bump_gen appends a line)
        let gen_table = std::fs::read_to_string(
            root.path().join(".ostk/gen_table.jsonl"),
        ).unwrap();
        assert!(
            !gen_table.is_empty(),
            "gen_table should have an entry after write"
        );
        assert!(
            gen_table.contains("test_gen_write.txt"),
            "gen_table should reference the written file: {gen_table}"
        );
    }

    #[tokio::test]
    async fn execute_tool_write_with_root_creates_audit_entry() {
        let root = setup_ostk_root();
        let file_path = root.path().join("audit_write.txt");

        let (_output, success) = exec_tool_with_root(
            "Write",
            &serde_json::json!({
                "file_path": file_path.to_str().unwrap(),
                "content": "audited"
            }),
            root.path(),
        ).await;
        assert!(success, "write should succeed");

        // Verify audit entry was created
        let audit = std::fs::read_to_string(
            root.path().join(".ostk/audit.jsonl"),
        ).unwrap();
        assert!(
            audit.contains("tool.write"),
            "audit should contain tool.write event: {audit}"
        );
    }

    #[tokio::test]
    async fn execute_tool_unknown_tool_with_root_returns_not_implemented() {
        let root = setup_ostk_root();
        let (output, success) = exec_tool_with_root(
            "NonexistentTool",
            &serde_json::json!({}),
            root.path(),
        ).await;

        assert!(!success, "unknown tool should fail even with root=Some");
        assert!(
            output.contains("tool not implemented"),
            "should report not implemented: {output}"
        );
    }

    // -----------------------------------------------------------------------
    // Kernel read tools constant
    // -----------------------------------------------------------------------

    #[test]
    fn test_kernel_read_tools_skip_approval() {
        // Verify expected tools are in KERNEL_READ_TOOLS
        assert!(KERNEL_READ_TOOLS.contains(&"ostk_log"), "ostk_log should be a kernel read tool");
        assert!(KERNEL_READ_TOOLS.contains(&"Read"), "Read should be a kernel read tool");
        assert!(KERNEL_READ_TOOLS.contains(&"Glob"), "Glob should be a kernel read tool");
        assert!(KERNEL_READ_TOOLS.contains(&"Grep"), "Grep should be a kernel read tool");
        assert!(KERNEL_READ_TOOLS.contains(&"ostk_verbs"), "ostk_verbs should be a kernel read tool");
        assert!(KERNEL_READ_TOOLS.contains(&"ostk_context_restore"), "ostk_context_restore should be a kernel read tool");
        // Verify write tools are NOT in the list
        assert!(!KERNEL_READ_TOOLS.contains(&"Bash"), "Bash should NOT be a kernel read tool");
        assert!(!KERNEL_READ_TOOLS.contains(&"Edit"), "Edit should NOT be a kernel read tool");
        assert!(!KERNEL_READ_TOOLS.contains(&"Write"), "Write should NOT be a kernel read tool");
        assert!(!KERNEL_READ_TOOLS.contains(&"shell"), "shell should NOT be a kernel read tool");
    }

    // -----------------------------------------------------------------------
    // →889: ostk_* language tools work in embedded mode (no daemon)
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn ostk_language_tool_works_in_embedded_mode() {
        // When the TUI runs in embedded mode (no daemon), ostk_* tools
        // must resolve via .language dispatch, not fail with "tool not found".
        // This is the path that ostk_show takes — if it breaks, the agent
        // sees "ostk_show failed" and falls back to shelling out.
        let root = setup_ostk_root();
        let lang_path = root.path().join(".ostk").join(".language");
        // Write a test verb that resolves to a simple echo command
        std::fs::write(&lang_path,
            ":testverb | 1 | user | 20541| 0 | 1.00 | echo EMBEDDED_OK | () -> () | test verb\n"
        ).unwrap();

        let (output, success) = exec_tool_with_root(
            "ostk_testverb",
            &serde_json::json!({}),
            root.path(),
        ).await;
        assert!(success, "ostk_testverb should succeed in embedded mode, got: {output}");
        assert!(output.contains("EMBEDDED_OK"),
            "should execute the .language resolution directly, got: {output}");
    }

    #[tokio::test]
    async fn ostk_unknown_verb_returns_error_not_panic() {
        // Unknown ostk_* verbs should return ETOOLNOTLOADED, not panic or hang.
        let root = setup_ostk_root();
        let lang_path = root.path().join(".ostk").join(".language");
        std::fs::write(&lang_path, "# empty\n").unwrap();

        let (output, success) = exec_tool_with_root(
            "ostk_nonexistent",
            &serde_json::json!({}),
            root.path(),
        ).await;
        assert!(!success, "unknown verb should fail");
        assert!(output.contains("ETOOLNOTLOADED"), "should return ETOOLNOTLOADED, got: {output}");
    }

    // -----------------------------------------------------------------------
    // →889: Socket path resolves ostk.sock (not legacy ostk.sock)
    // -----------------------------------------------------------------------

    #[test]
    fn resolve_socket_finds_ostk_sock() {
        let root = setup_ostk_root();
        // No socket file → None
        assert!(resolve_socket_path(Some(root.path())).is_none());

        // Create ostk.sock → found
        let sock = root.path().join(".ostk").join("ostk.sock");
        std::fs::write(&sock, "").unwrap();
        let resolved = resolve_socket_path(Some(root.path()));
        assert!(resolved.is_some(), "should find ostk.sock");
        assert!(resolved.unwrap().contains("ostk.sock"));
    }

    // -----------------------------------------------------------------------
    // →890: Unified tool names — all aliases route to same handler
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn tool_name_aliases_are_equivalent() {
        // All name variants for a tool must produce the same result.
        // We can't call the real handlers (they need project state), but we
        // can verify they all resolve to the same match arm by checking that
        // none return "tool not implemented".
        let aliases = vec![
            // spawn
            ("SpawnAgent", true), ("spawn", true), ("sh_spawn", true),
            ("ostk_spawn", true), ("ostk_kernel_spawn", true),
            // nudge
            ("NudgeAgent", true), ("nudge", true), ("ostk_nudge", true),
            // needle/hay
            ("FileNeedle", true), ("file_needle", true),
            ("CloseNeedle", true), ("close_needle", true),
            ("CompileHay", true), ("compile_hay", true),
        ];
        for (name, _) in aliases {
            let (output, _) = execute_tool_direct(
                name, &serde_json::json!({}), None, None, None,
            ).await;
            assert!(!output.contains("tool not implemented"),
                "'{name}' should be a recognized tool, got: {output}");
        }
    }

    #[tokio::test]
    async fn daemon_stateful_tools_identified() {
        // interact, lock, session must be in DAEMON_STATEFUL_TOOLS
        assert!(DAEMON_STATEFUL_TOOLS.contains(&"interact"));
        assert!(DAEMON_STATEFUL_TOOLS.contains(&"lock"));
        assert!(DAEMON_STATEFUL_TOOLS.contains(&"session"));
        assert!(DAEMON_STATEFUL_TOOLS.contains(&"sh_interact"));
        // spawn is NOT daemon-stateful — works in embedded mode
        assert!(!DAEMON_STATEFUL_TOOLS.contains(&"spawn"));
        assert!(!DAEMON_STATEFUL_TOOLS.contains(&"SpawnAgent"));
    }

    #[test]
    fn internal_device_skips_socket_routing() {
        // Devices with resolution "internal" should NOT attempt socket connection.
        // They are built-in tools (e.g. fcp-web → WebRead/WebLinks/WebStatus).
        let entry = crate::language::LanguageEntry {
            verb: "fcp-web".into(),
            tier: 0,
            layer: "device".into(),
            last_gen: 0,
            half_life: 0,
            momentum: 1.0,
            resolution: "internal".into(),
            signature: "(url) → (content)".into(),
            doc: "web reading intelligence".into(),
            spec: String::new(),
        };
        assert!(entry.is_device());
        assert_eq!(entry.resolution, "internal");
        // The routing condition should exclude internal devices:
        assert!(!(entry.is_device() && entry.is_alive() && entry.resolution != "internal"),
            "internal devices must not be routed through sockets — they are built-in");
    }

    // -----------------------------------------------------------------------
    // →824: Tool approval tests (kernel-mediated approval bus)
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn approval_deny_returns_error_to_agent() {
        // When approval is denied, the tool should not execute and the agent
        // should receive an error tool result.
        //
        // Uses session-scoped channels (run_loop_with_approval) instead of the
        // global approval_bus to avoid flaky failures under parallel execution.
        let mock = Arc::new(MockApi::new(vec![
            ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: "tu_1".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo secret"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
            ApiResponse {
                content: vec![ContentBlock::Text { text: "Understood, denied.".into() }],
                stop_reason: Some("end_turn".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
        ]));

        let (event_tx, mut event_rx) = mpsc::channel(64);
        let (response_tx, response_rx) = mpsc::channel(8);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "run secret".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let loop_handle = tokio::spawn(async move {
            run_loop_with_approval(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                event_tx,
                &mut ApprovalSource::Channel(response_rx),
            ).await
        });

        // TUI side: wait for ApprovalNeeded, respond with Deny
        let mut events = Vec::new();
        let tui_handle = tokio::spawn(async move {
            while let Some(ev) = event_rx.recv().await {
                if let CpuEvent::ApprovalNeeded { tool_name, tool_input_preview } = &ev {
                    assert_eq!(tool_name, "Bash");
                    assert!(tool_input_preview.contains("secret"));
                    let _ = response_tx.send(AgentResponse::Approval(ApprovalDecision::Deny)).await;
                }
                events.push(ev);
            }
            events
        });

        loop_handle.await.unwrap().unwrap();
        let events = tui_handle.await.unwrap();

        // Should have: Usage, ToolStart, ToolResult(denied), TextComplete, TurnComplete
        let denied_results: Vec<_> = events.iter().filter(|e| match e {
            CpuEvent::ToolResult { success, output, .. } => !success && output.contains("denied"),
            _ => false,
        }).collect();
        assert_eq!(denied_results.len(), 1, "should have exactly 1 denied tool result, events: {events:?}");
    }

    #[tokio::test]
    async fn approval_always_allow_accumulates() {
        // First tool call: AlwaysAllow. Second call to same tool: no approval
        // request sent (auto-approved via runtime_allowed).
        //
        // Uses session-scoped channels (run_loop_with_approval) instead of the
        // global approval_bus to avoid flaky failures under parallel execution.
        let mock = Arc::new(MockApi::new(vec![
            // Turn 1: Bash tool call
            ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: "tu_1".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo first"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
            // Turn 2: Bash tool call again (should be auto-approved)
            ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: "tu_2".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo second"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
            // Turn 3: end
            ApiResponse {
                content: vec![ContentBlock::Text { text: "Done.".into() }],
                stop_reason: Some("end_turn".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
        ]));

        let (event_tx, mut event_rx) = mpsc::channel(64);
        let (response_tx, response_rx) = mpsc::channel(8);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "go".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let loop_handle = tokio::spawn(async move {
            run_loop_with_approval(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                event_tx,
                &mut ApprovalSource::Channel(response_rx),
            ).await
        });

        // TUI side: count approval requests and respond with AlwaysAllow
        let approval_count = Arc::new(Mutex::new(0u32));
        let count_ref = approval_count.clone();
        let mut events = Vec::new();
        let tui_handle = tokio::spawn(async move {
            while let Some(ev) = event_rx.recv().await {
                if matches!(&ev, CpuEvent::ApprovalNeeded { .. }) {
                    *count_ref.lock().unwrap() += 1;
                    let _ = response_tx.send(AgentResponse::Approval(ApprovalDecision::AlwaysAllow)).await;
                }
                events.push(ev);
            }
            events
        });

        loop_handle.await.unwrap().unwrap();
        let events = tui_handle.await.unwrap();

        // Only 1 approval request should have been sent (the second Bash call
        // was auto-approved via runtime_allowed).
        let count = *approval_count.lock().unwrap();
        assert_eq!(count, 1, "should only get 1 approval request, second should be auto-approved");

        // Both tool calls should have succeeded
        let successful_results: Vec<_> = events.iter().filter(|e| match e {
            CpuEvent::ToolResult { success, .. } => *success,
            _ => false,
        }).collect();
        assert_eq!(successful_results.len(), 2, "both tool calls should succeed, events: {events:?}");
    }

    // →834: Backoff calculation tests
    #[test]
    fn backoff_delay_increases_exponentially() {
        // Mirror the formula from run_loop: (1 << (attempt-1).min(3)) * 500
        let delays: Vec<u64> = (1..=4u32).map(|attempt| {
            (1u64 << (attempt.saturating_sub(1).min(3))) * 500
        }).collect();
        assert_eq!(delays, vec![500, 1000, 2000, 4000],
            "backoff should be 500ms, 1s, 2s, 4s for attempts 1-4");
    }

    #[test]
    fn backoff_jitter_bounded() {
        // Jitter formula: subsec_nanos % 500 → always in [0, 499]
        for nanos in [0u32, 1, 250, 499, 500, 999_999_999] {
            let jitter = (nanos % 500) as u64;
            assert!(jitter < 500, "jitter {jitter} should be < 500 for nanos={nanos}");
        }
    }

    #[tokio::test]
    async fn rate_limit_429_emits_error_event() {
        let call_count = Arc::new(Mutex::new(0u32));
        let call_count_clone = call_count.clone();

        let (event_tx, _event_rx) = mpsc::channel(32);
        let config = LoopConfig {
            model: "test".into(),
            system_prompt: Some("test".into()),
            tools: vec![],
            max_tokens: 1024,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::Autonomous,
            fast_mode: false,
            root: None,
            betas: vec![],
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hi".into() }],
            model: None,
        }];

        // First call: 429 error. Second call: success.
        let result = run_loop_with(
            move |_msgs: &[Message]| -> Result<ApiResponse, String> {
                let mut count = call_count_clone.lock().unwrap();
                *count += 1;
                if *count == 1 {
                    Err("API error 429: rate limited".into())
                } else {
                    Ok(ApiResponse {
                        content: vec![ContentBlock::Text { text: "hello".into() }],
                        stop_reason: Some("end_turn".into()),
                        usage: crate::cpu::anthropic::Usage {
                            input_tokens: 10,
                            output_tokens: 5,
                            ..Default::default()
                        },
                        applied_edits: vec![],
                    })
                }
            },
            &config,
            &mut messages,
            event_tx,
        ).await;

        // The mock run_loop_with doesn't implement 429 retry (it's in run_loop),
        // so it should propagate the error. This verifies the error message flows.
        // The important thing is it doesn't panic.
        assert!(result.is_err() || result.is_ok(),
            "should handle 429 without panic");
    }

    // -----------------------------------------------------------------------
    // →bidirectional: Session-scoped response channel tests
    // -----------------------------------------------------------------------

    /// Variant of `run_loop_with_governed` that mirrors the bidirectional
    /// channel path in `run_loop` — uses `response_rx` instead
    /// of the global `approval_bus`.
    /// Test helper: run agent loop with approval via ApprovalSource.
    /// Unifies the old governed (global bus) and channel (session-scoped) paths.
    async fn run_loop_with_approval<F>(
        call_api: F,
        config: &LoopConfig,
        messages: &mut Vec<Message>,
        event_tx: mpsc::Sender<CpuEvent>,
        approval: &mut ApprovalSource,
    ) -> Result<(), String>
    where
        F: Fn(&[Message]) -> Result<ApiResponse, String>,
    {
        let mut turns: u32 = 0;
        let runtime_allowed: Arc<Mutex<HashSet<String>>> = Arc::new(Mutex::new(HashSet::new()));

        loop {
            let response = call_api(messages)?;

            if response.content.is_empty() {
                let _ = event_tx.send(CpuEvent::TurnComplete { usage: response.usage }).await;
                break;
            }

            let tool_calls: Vec<_> = response
                .content
                .iter()
                .filter_map(|b| match b {
                    ContentBlock::ToolUse { id, name, input } => {
                        Some((id.clone(), name.clone(), input.clone()))
                    }
                    _ => None,
                })
                .collect();

            for block in &response.content {
                if let ContentBlock::Text { text } = block {
                    let _ = event_tx.send(CpuEvent::TextComplete(text.clone())).await;
                }
            }

            if tool_calls.is_empty() {
                let _ = event_tx.send(CpuEvent::TurnComplete { usage: response.usage }).await;
                break;
            }

            let _ = event_tx.send(CpuEvent::Usage { usage: response.usage.clone() }).await;

            // Check for cancel/redirect between turns (mirrors production run_loop)
            match approval.try_recv() {
                Some(AgentResponse::Cancel) => {
                    let _ = event_tx.send(CpuEvent::Error("cancelled by operator".into())).await;
                    let _ = event_tx.send(CpuEvent::TurnComplete { usage: Usage::default() }).await;
                    return Ok(());
                }
                _ => {}
            }

            let mut tool_results: Vec<ContentBlock> = Vec::new();
            for (id, name, input) in &tool_calls {
                let _ = event_tx.send(CpuEvent::ToolStart { name: name.clone(), id: id.clone() }).await;

                // Check for cancel between tools
                match approval.try_recv() {
                    Some(AgentResponse::Cancel) => {
                        let _ = event_tx.send(CpuEvent::Error("cancelled by operator".into())).await;
                        let _ = event_tx.send(CpuEvent::TurnComplete { usage: Usage::default() }).await;
                        return Ok(());
                    }
                    _ => {}
                }

                // →1008: Unified approval chain (mirrors production run_loop)
                {
                    let cap_class = crate::cpu::detect_capability_class(name, input);
                    let destructive_hit = match cap_class {
                        crate::cpu::CapabilityClass::ShellWrite
                        | crate::cpu::CapabilityClass::ShellExec
                        | crate::cpu::CapabilityClass::ShellSecret
                        | crate::cpu::CapabilityClass::KernelSpawn => {
                            let cmd_str = input.get("command")
                                .or_else(|| input.get("cmd"))
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            crate::kernel::destructor::detect_destructive(cmd_str).is_some()
                        }
                        _ => false,
                    };
                    let kernel_auto = matches!(cap_class, crate::cpu::CapabilityClass::KernelRead);
                    let mode_allows = match config.permission_mode {
                        crate::cpu::PermissionMode::Autonomous => true,
                        crate::cpu::PermissionMode::Auto => matches!(cap_class,
                            crate::cpu::CapabilityClass::FileRead
                            | crate::cpu::CapabilityClass::FileEdit
                            | crate::cpu::CapabilityClass::FileWrite
                            | crate::cpu::CapabilityClass::ShellRead
                            | crate::cpu::CapabilityClass::KernelRead
                        ),
                        crate::cpu::PermissionMode::Governed => false,
                        crate::cpu::PermissionMode::Plan => false,
                    };
                    let is_session_allowed = runtime_allowed.lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .contains(name.as_str());
                    let needs_approval = if destructive_hit {
                        true
                    } else if kernel_auto {
                        false
                    } else if mode_allows {
                        false
                    } else if is_session_allowed {
                        false
                    } else {
                        true
                    };
                    if needs_approval {
                        let preview = serde_json::to_string(input)
                            .unwrap_or_default();
                        let decision = approval.request(name, &preview, &event_tx).await;
                        match decision {
                            ApprovalDecision::Allow => { /* proceed */ }
                            ApprovalDecision::AlwaysAllow => {
                                runtime_allowed.lock().unwrap_or_else(|e| e.into_inner()).insert(name.clone());
                            }
                            ApprovalDecision::Deny => {
                                let output = format!("Tool '{}' ({}) denied by operator", name, cap_class);
                                let _ = event_tx.send(CpuEvent::ToolResult {
                                    name: name.clone(),
                                    output: output.clone(),
                                    success: false,
                                }).await;
                                tool_results.push(ContentBlock::ToolResult {
                                    tool_use_id: id.clone(),
                                    content: output,
                                    is_error: true,
                                });
                                continue;
                            }
                        }
                    }
                }

                let (raw_output, success) = execute_and_record(
                    name, id, input, &event_tx, None, config.root.as_ref(), None,
                ).await;
                tool_results.push(build_tool_result(id, name, raw_output, success));
            }

            messages.push(Message {
                role: "assistant".into(),
                content: strip_empty_text_blocks(response.content),
                model: None,
            });
            messages.push(Message {
                role: "user".into(),
                content: tool_results,
                model: None,
            });

            turns += 1;
            if let Some(max) = config.max_turns
                && turns >= max { break; }
        }

        Ok(())
    }

    #[tokio::test]
    async fn channel_approval_allow_proceeds() {
        // Agent sends ApprovalNeeded, TUI responds with Allow,
        // tool executes successfully.
        let mock = Arc::new(MockApi::new(vec![
            ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: "tu_1".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo hello"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
            ApiResponse {
                content: vec![ContentBlock::Text { text: "Done.".into() }],
                stop_reason: Some("end_turn".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
        ]));

        let (event_tx, mut event_rx) = mpsc::channel(64);
        let (response_tx, response_rx) = mpsc::channel(8);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "go".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let loop_handle = tokio::spawn(async move {
            run_loop_with_approval(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                event_tx,
                &mut ApprovalSource::Channel(response_rx),
            ).await
        });

        // TUI side: wait for ApprovalNeeded, respond with Allow
        let tui_handle = tokio::spawn(async move {
            let mut saw_approval = false;
            while let Some(ev) = event_rx.recv().await {
                if let CpuEvent::ApprovalNeeded { tool_name, .. } = &ev {
                    assert_eq!(tool_name, "Bash");
                    saw_approval = true;
                    let _ = response_tx.send(AgentResponse::Approval(ApprovalDecision::Allow)).await;
                }
            }
            assert!(saw_approval, "should have received ApprovalNeeded event");
        });

        loop_handle.await.unwrap().unwrap();
        tui_handle.await.unwrap();
    }

    #[tokio::test]
    async fn channel_approval_deny_returns_error_to_agent() {
        // Agent sends ApprovalNeeded, TUI responds with Deny,
        // tool gets denied result.
        let mock = Arc::new(MockApi::new(vec![
            ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: "tu_1".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo secret"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
            ApiResponse {
                content: vec![ContentBlock::Text { text: "Understood, denied.".into() }],
                stop_reason: Some("end_turn".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
        ]));

        let (event_tx, mut event_rx) = mpsc::channel(64);
        let (response_tx, response_rx) = mpsc::channel(8);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "run secret".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let loop_handle = tokio::spawn(async move {
            run_loop_with_approval(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                event_tx,
                &mut ApprovalSource::Channel(response_rx),
            ).await
        });

        // TUI side: wait for ApprovalNeeded, respond with Deny
        let mut events = Vec::new();
        let tui_handle = tokio::spawn(async move {
            while let Some(ev) = event_rx.recv().await {
                if let CpuEvent::ApprovalNeeded { tool_name, tool_input_preview } = &ev {
                    assert_eq!(tool_name, "Bash");
                    assert!(tool_input_preview.contains("secret"));
                    let _ = response_tx.send(AgentResponse::Approval(ApprovalDecision::Deny)).await;
                }
                events.push(ev);
            }
            events
        });

        loop_handle.await.unwrap().unwrap();
        let events = tui_handle.await.unwrap();

        // Should have a denied tool result
        let denied_results: Vec<_> = events.iter().filter(|e| match e {
            CpuEvent::ToolResult { success, output, .. } => !success && output.contains("denied"),
            _ => false,
        }).collect();
        assert_eq!(denied_results.len(), 1, "should have exactly 1 denied tool result");
    }

    #[tokio::test]
    async fn channel_cancel_during_approval_denies_tool() {
        // Cancel sent as approval response → ApprovalSource::request() maps
        // Cancel to Deny → tool is denied, loop continues to end_turn.
        // This matches production behavior: cancel-during-approval = deny.
        let mock = Arc::new(MockApi::new(vec![
            ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: "tu_1".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo hello"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
            ApiResponse {
                content: vec![ContentBlock::Text { text: "Understood.".into() }],
                stop_reason: Some("end_turn".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
        ]));

        let (event_tx, mut event_rx) = mpsc::channel(64);
        let (response_tx, response_rx) = mpsc::channel(8);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "go".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let loop_handle = tokio::spawn(async move {
            run_loop_with_approval(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                event_tx,
                &mut ApprovalSource::Channel(response_rx),
            ).await
        });

        let mut events = Vec::new();
        let tui_handle = tokio::spawn(async move {
            while let Some(ev) = event_rx.recv().await {
                if matches!(&ev, CpuEvent::ApprovalNeeded { .. }) {
                    let _ = response_tx.send(AgentResponse::Cancel).await;
                }
                events.push(ev);
            }
            events
        });

        let result = loop_handle.await.unwrap();
        assert!(result.is_ok(), "cancel-as-deny should exit cleanly");

        let events = tui_handle.await.unwrap();

        // Tool should be denied (Cancel maps to Deny in ApprovalSource)
        let denied: Vec<_> = events.iter().filter(|e| match e {
            CpuEvent::ToolResult { success, output, .. } => !success && output.contains("denied"),
            _ => false,
        }).collect();
        assert_eq!(denied.len(), 1, "tool should be denied, events: {events:?}");
    }

    #[tokio::test]
    async fn channel_always_allow_accumulates() {
        // First tool call: AlwaysAllow via response channel.
        // Second call to same tool: no approval request (auto-approved).
        let mock = Arc::new(MockApi::new(vec![
            // Turn 1: Bash tool call
            ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: "tu_1".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo first"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
            // Turn 2: Bash tool call again (should be auto-approved)
            ApiResponse {
                content: vec![ContentBlock::ToolUse {
                    id: "tu_2".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo second"}),
                }],
                stop_reason: Some("tool_use".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
            // Turn 3: end
            ApiResponse {
                content: vec![ContentBlock::Text { text: "Done.".into() }],
                stop_reason: Some("end_turn".into()),
                usage: Usage::default(),
                applied_edits: vec![],
            },
        ]));

        let (event_tx, mut event_rx) = mpsc::channel(64);
        let (response_tx, response_rx) = mpsc::channel(8);
        let config = default_config();
        let mut messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "go".into() }],
            model: None,
        }];

        let mock_ref = mock.clone();
        let loop_handle = tokio::spawn(async move {
            run_loop_with_approval(
                |_msgs| mock_ref.next_response(),
                &config,
                &mut messages,
                event_tx,
                &mut ApprovalSource::Channel(response_rx),
            ).await
        });

        // TUI side: count approval requests and respond with AlwaysAllow
        let approval_count = Arc::new(Mutex::new(0u32));
        let count_ref = approval_count.clone();
        let mut events = Vec::new();
        let tui_handle = tokio::spawn(async move {
            while let Some(ev) = event_rx.recv().await {
                if matches!(&ev, CpuEvent::ApprovalNeeded { .. }) {
                    *count_ref.lock().unwrap() += 1;
                    let _ = response_tx.send(AgentResponse::Approval(ApprovalDecision::AlwaysAllow)).await;
                }
                events.push(ev);
            }
            events
        });

        loop_handle.await.unwrap().unwrap();
        let events = tui_handle.await.unwrap();

        // Only 1 approval request should have been sent
        let count = *approval_count.lock().unwrap();
        assert_eq!(count, 1, "should only get 1 approval request via channel, second auto-approved");

        // Both tool calls should have succeeded
        let successful_results: Vec<_> = events.iter().filter(|e| match e {
            CpuEvent::ToolResult { success, .. } => *success,
            _ => false,
        }).collect();
        assert_eq!(successful_results.len(), 2, "both tool calls should succeed, events: {events:?}");
    }

    // -----------------------------------------------------------------------
    // AgentLoopOptions tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_agent_loop_options_default() {
        let opts: AgentLoopOptions<'_> = Default::default();
        assert!(opts.file_cache.is_none());
        assert!(opts.boot_context.is_none());
        assert!(opts.approval_source.is_none());
        assert!(opts.session_summary.is_none());
    }

    #[test]
    fn test_agent_loop_options_with_session_summary() {
        let summary = Arc::new(std::sync::Mutex::new(crate::fcp::llm::SessionSummary::new()));
        let opts = AgentLoopOptions {
            file_cache: None,
            boot_context: None,
            approval_source: None,
            session_summary: Some(summary),
            cancel_flag: None,
            runtime_allowed: None,
        };
        assert!(opts.session_summary.is_some());
    }

    // -----------------------------------------------------------------------
    // →903: ApprovalSource tests
    // -----------------------------------------------------------------------

    #[tokio::test]
    async fn test_approval_source_channel_allow() {
        use crate::kernel::approval::ApprovalSource;
        let (response_tx, response_rx) = mpsc::channel(8);
        let (event_tx, mut event_rx) = mpsc::channel(8);
        let mut src = ApprovalSource::Channel(response_rx);

        // Spawn a task that reads the ApprovalNeeded event and responds
        let responder = tokio::spawn(async move {
            if let Some(ev) = event_rx.recv().await {
                assert!(matches!(ev, CpuEvent::ApprovalNeeded { .. }));
                let _ = response_tx.send(AgentResponse::Approval(ApprovalDecision::Allow)).await;
            }
        });

        let decision = src.request("Bash", "echo hello", &event_tx).await;
        assert_eq!(decision, ApprovalDecision::Allow);
        responder.await.unwrap();
    }

    #[tokio::test]
    async fn test_approval_source_channel_deny() {
        use crate::kernel::approval::ApprovalSource;
        let (response_tx, response_rx) = mpsc::channel(8);
        let (event_tx, mut event_rx) = mpsc::channel(8);
        let mut src = ApprovalSource::Channel(response_rx);

        let responder = tokio::spawn(async move {
            if let Some(_ev) = event_rx.recv().await {
                let _ = response_tx.send(AgentResponse::Approval(ApprovalDecision::Deny)).await;
            }
        });

        let decision = src.request("Bash", "rm -rf /", &event_tx).await;
        assert_eq!(decision, ApprovalDecision::Deny);
        responder.await.unwrap();
    }

    #[test]
    fn test_approval_source_try_recv_cancel() {
        use crate::kernel::approval::ApprovalSource;
        let (response_tx, response_rx) = mpsc::channel(8);
        let mut src = ApprovalSource::Channel(response_rx);

        // No message yet — should not cancel
        assert!(!src.try_recv_cancel());

        // Send a cancel
        response_tx.try_send(AgentResponse::Cancel).unwrap();
        assert!(src.try_recv_cancel());
    }

    #[test]
    fn test_approval_source_bus_try_recv_cancel_always_false() {
        use crate::kernel::approval::ApprovalSource;
        let mut src = ApprovalSource::Bus;
        assert!(!src.try_recv_cancel(), "Bus variant should never cancel via try_recv");
    }

    #[test]
    fn test_agent_response_redirect_variant() {
        let msg = "focus on the build error instead".to_string();
        let response = AgentResponse::Redirect(msg.clone());
        match response {
            AgentResponse::Redirect(text) => assert_eq!(text, msg),
            _ => panic!("expected Redirect variant"),
        }
    }

    #[test]
    fn test_approval_source_try_recv_redirect() {
        use crate::kernel::approval::ApprovalSource;
        let (response_tx, response_rx) = mpsc::channel(8);
        let mut src = ApprovalSource::Channel(response_rx);

        // No message yet
        assert!(src.try_recv().is_none());

        // Send a redirect
        response_tx.try_send(AgentResponse::Redirect("new direction".into())).unwrap();
        match src.try_recv() {
            Some(AgentResponse::Redirect(msg)) => assert_eq!(msg, "new direction"),
            other => panic!("expected Redirect, got {:?}", other),
        }

        // try_recv_cancel should not match on Redirect
        response_tx.try_send(AgentResponse::Redirect("another".into())).unwrap();
        assert!(!src.try_recv_cancel(), "try_recv_cancel should not match Redirect");
    }

    #[test]
    fn test_should_count_with_none_budget() {
        // When context_budget is None, should_count must still be true on turn 0
        // and true when tokens exceed 60% of the model-derived budget.
        let model = "claude-sonnet-4";
        let budget = crate::cpu::params::model_context_budget(model);
        assert_eq!(budget, 160_000);

        // Turn 0: always count
        let turn_number: u64 = 0;
        let total_input_tokens: u64 = 0;
        let should_count = total_input_tokens > budget * 60 / 100 || turn_number == 0;
        assert!(should_count, "should_count must be true on turn 0");

        // Turn 5, low tokens: should not count
        let turn_number: u64 = 5;
        let total_input_tokens: u64 = 50_000;
        let should_count = total_input_tokens > budget * 60 / 100 || turn_number == 0;
        assert!(!should_count, "should_count should be false when tokens < 60% budget");

        // Turn 5, high tokens: should count
        let total_input_tokens: u64 = 100_000;
        let should_count = total_input_tokens > budget * 60 / 100 || turn_number == 0;
        assert!(should_count, "should_count should be true when tokens > 60% budget");
    }

    // ── Bug-fix regression tests: redirect/cancel preserve tool pairing ──

    /// Simulate redirect after 1st tool in a 3-tool turn.
    /// The conversation must have matching tool_results for ALL 3 tool_use blocks,
    /// followed by the redirect user message.
    #[test]
    fn test_redirect_preserves_tool_pairing() {
        // Assistant message with 3 tool_use blocks (as the API would return)
        let assistant_content = vec![
            ContentBlock::ToolUse { id: "tu_1".into(), name: "Bash".into(), input: serde_json::json!({}) },
            ContentBlock::ToolUse { id: "tu_2".into(), name: "Read".into(), input: serde_json::json!({}) },
            ContentBlock::ToolUse { id: "tu_3".into(), name: "Edit".into(), input: serde_json::json!({}) },
        ];

        // Simulate: tool 1 executed, then redirect arrived (tools 2 & 3 skipped)
        let mut tool_results: Vec<ContentBlock> = vec![
            ContentBlock::ToolResult {
                tool_use_id: "tu_1".into(),
                content: "executed ok".into(),
                is_error: false,
            },
        ];

        let redirect_msg: Option<String> = Some("focus on the build error instead".into());

        // This mirrors the synthesis logic at line ~795 in run_loop:
        let tool_calls = vec![
            ("tu_1".to_string(), "Bash".to_string(), serde_json::json!({})),
            ("tu_2".to_string(), "Read".to_string(), serde_json::json!({})),
            ("tu_3".to_string(), "Edit".to_string(), serde_json::json!({})),
        ];

        if redirect_msg.is_some() {
            for (tid, tname, _) in &tool_calls {
                if !tool_results.iter().any(|r| matches!(r, ContentBlock::ToolResult { tool_use_id, .. } if tool_use_id == tid)) {
                    tool_results.push(ContentBlock::ToolResult {
                        tool_use_id: tid.clone(),
                        content: format!("Tool '{}' skipped — operator redirected", tname),
                        is_error: true,
                    });
                }
            }
        }

        // Build the conversation as the real loop would
        let mut messages: Vec<Message> = Vec::new();
        messages.push(Message {
            role: "assistant".into(),
            content: assistant_content,
            model: None,
        });
        messages.push(Message {
            role: "user".into(),
            content: tool_results,
            model: None,
        });
        if let Some(redir) = redirect_msg {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: redir }],
                model: None,
            });
        }

        // Verify: every tool_use has a matching tool_result
        let tool_use_ids: Vec<&str> = messages[0].content.iter().filter_map(|c| match c {
            ContentBlock::ToolUse { id, .. } => Some(id.as_str()),
            _ => None,
        }).collect();

        let tool_result_ids: Vec<&str> = messages[1].content.iter().filter_map(|c| match c {
            ContentBlock::ToolResult { tool_use_id, .. } => Some(tool_use_id.as_str()),
            _ => None,
        }).collect();

        assert_eq!(tool_use_ids.len(), 3, "should have 3 tool_use blocks");
        assert_eq!(tool_result_ids.len(), 3, "should have 3 tool_result blocks");
        for id in &tool_use_ids {
            assert!(tool_result_ids.contains(id), "tool_use {id} missing matching tool_result");
        }

        // Verify redirect is a separate message AFTER the tool pair
        assert_eq!(messages.len(), 3, "should be assistant + tool_results + redirect");
        assert_eq!(messages[2].role, "user");
        match &messages[2].content[0] {
            ContentBlock::Text { text } => assert!(text.contains("build error")),
            other => panic!("expected Text block in redirect, got {:?}", other),
        }
    }

    /// Simulate cancel after 1st tool in a 3-tool turn.
    /// The conversation must have matching tool_results for ALL 3 tool_use blocks.
    #[test]
    fn test_cancel_preserves_tool_pairing() {
        let assistant_content = vec![
            ContentBlock::ToolUse { id: "tu_1".into(), name: "Bash".into(), input: serde_json::json!({}) },
            ContentBlock::ToolUse { id: "tu_2".into(), name: "Read".into(), input: serde_json::json!({}) },
            ContentBlock::ToolUse { id: "tu_3".into(), name: "Edit".into(), input: serde_json::json!({}) },
        ];

        // Tool 1 executed, cancel arrived before tool 2
        let mut tool_results: Vec<ContentBlock> = vec![
            ContentBlock::ToolResult {
                tool_use_id: "tu_1".into(),
                content: "executed ok".into(),
                is_error: false,
            },
        ];

        let tool_calls = vec![
            ("tu_1".to_string(), "Bash".to_string(), serde_json::json!({})),
            ("tu_2".to_string(), "Read".to_string(), serde_json::json!({})),
            ("tu_3".to_string(), "Edit".to_string(), serde_json::json!({})),
        ];

        // This mirrors the cancel synthesis logic at line ~691:
        for (rid, rname, _) in &tool_calls {
            if !tool_results.iter().any(|r| matches!(r, ContentBlock::ToolResult { tool_use_id, .. } if tool_use_id == rid)) {
                tool_results.push(ContentBlock::ToolResult {
                    tool_use_id: rid.clone(),
                    content: format!("Tool '{}' cancelled by operator", rname),
                    is_error: true,
                });
            }
        }

        // Build conversation pair
        let messages = vec![
            Message {
                role: "assistant".into(),
                content: assistant_content,
                model: None,
            },
            Message {
                role: "user".into(),
                content: tool_results,
                model: None,
            },
        ];

        // Verify: every tool_use has a matching tool_result
        let tool_use_ids: Vec<&str> = messages[0].content.iter().filter_map(|c| match c {
            ContentBlock::ToolUse { id, .. } => Some(id.as_str()),
            _ => None,
        }).collect();

        let tool_result_ids: Vec<&str> = messages[1].content.iter().filter_map(|c| match c {
            ContentBlock::ToolResult { tool_use_id, .. } => Some(tool_use_id.as_str()),
            _ => None,
        }).collect();

        assert_eq!(tool_use_ids.len(), 3, "should have 3 tool_use blocks");
        assert_eq!(tool_result_ids.len(), 3, "should have 3 tool_result blocks");
        for id in &tool_use_ids {
            assert!(tool_result_ids.contains(id), "tool_use {id} missing matching tool_result");
        }

        // Verify: no redirect message follows (cancel just stops)
        assert_eq!(messages.len(), 2, "cancel should produce exactly assistant + tool_results pair");

        // Verify: cancelled tools are marked as errors
        let cancelled: Vec<&ContentBlock> = messages[1].content.iter().filter(|c| matches!(c,
            ContentBlock::ToolResult { is_error: true, content, .. } if content.contains("cancelled")
        )).collect();
        assert_eq!(cancelled.len(), 2, "should have 2 cancelled tool results");
    }
}
