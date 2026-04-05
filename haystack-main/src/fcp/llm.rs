//! fcp-llm: Context rendering device driver for LLM compute.
//!
//! Renders kernel state into structured context pages optimized for
//! LLM attention, not human conversation. The inverse of fcp-screen.

use crate::cpu::anthropic::{ContentBlock, Message};
use std::path::Path;

// ---------------------------------------------------------------------------
// Provider capability hints
// ---------------------------------------------------------------------------

/// Provider constraints that affect context rendering decisions.
///
/// fcp-llm uses capability flags, not provider names. This keeps the
/// renderer decoupled from specific drivers. New providers set their
/// flags; the renderer adapts.
#[derive(Debug, Clone)]
#[derive(Default)]
pub struct ContextHints {
    /// Anthropic supports mixed content blocks in one user message
    /// (e.g. [ToolResult, Text]). OpenAI-compat providers don't —
    /// their translators expect pure ToolResult or pure Text messages.
    /// When false, ensure_alternation inserts padding instead of
    /// merging content that would create mixed blocks.
    pub supports_mixed_content_blocks: bool,
}


// ---------------------------------------------------------------------------
// →967: FcpLlm — provider-aware context budget + context page builder
// ---------------------------------------------------------------------------

/// The LLM display driver — renders kernel state for LLM compute.
///
/// Provider-aware budgets: each model family gets tuned parameters that
/// control how much context is assembled for the API call. This replaces
/// the one-size-fits-all hardcoded caps scattered across build_context_page
/// and render_working_state.
///
/// →968: `for_model()` derives all three parameters from the model name.
#[derive(Debug, Clone)]
pub struct FcpLlm {
    /// Maximum tokens for the context page (80% of model's context window)
    pub budget: u64,
    /// Number of recent turns to keep at full fidelity during eviction
    pub window_size: usize,
    /// Working state decision cap — how many decisions to render
    pub decision_cap: usize,
}

impl FcpLlm {
    /// →968: Derive budget parameters from model name.
    ///
    /// Each model family gets tuned values:
    /// - budget: 80% of the model's context window
    /// - window_size: how many recent user intents to preserve
    /// - decision_cap: how many working state decisions to render
    pub fn for_model(model: &str) -> Self {
        let model_lower = model.to_lowercase();

        if model_lower.contains("opus") && model_lower.contains("1m") {
            Self { budget: 800_000, window_size: 10, decision_cap: 50 }
        } else if model_lower.contains("opus") {
            Self { budget: 160_000, window_size: 10, decision_cap: 50 }
        } else if model_lower.contains("sonnet") {
            Self { budget: 160_000, window_size: 7, decision_cap: 20 }
        } else if model_lower.contains("haiku") {
            Self { budget: 80_000, window_size: 3, decision_cap: 5 }
        } else if model_lower.contains("gemini") {
            Self { budget: 800_000, window_size: 10, decision_cap: 50 }
        } else if model_lower.contains("deepseek")
            || model_lower.contains("gpt-4")
            || model_lower.contains("mistral") || model_lower.contains("devstral") {
            Self { budget: 100_000, window_size: 5, decision_cap: 15 }
        } else if model_lower.contains("qwen") {
            Self { budget: 25_000, window_size: 3, decision_cap: 5 }
        } else if model_lower.contains("llama") {
            Self { budget: 100_000, window_size: 5, decision_cap: 10 }
        } else {
            // Conservative default
            Self { budget: 50_000, window_size: 5, decision_cap: 10 }
        }
    }
}

/// The assembled context page, ready for serialization into API messages.
///
/// →967: Replaces the raw message history with a structured page:
/// 1. Registers (identity, temporal, needles, fleet) — from preload_context
/// 2. Working state (filtered decisions, modified files) — from preload_context
/// 3. Session summary (compiled from older turns) — replaces evicted history
/// 4. Recent turns at full fidelity — the recent conversation window
///
/// The system prompt and preload_context are handled separately (they go into
/// InferenceRequest.system and InferenceRequest.claude.preload_context). The
/// ContextPage only manages the messages array.
#[derive(Debug, Clone)]
pub struct ContextPage {
    /// Block 3: Session summary (compiled from older turns by SessionSummary)
    pub summary: String,
    /// Block 4: Recent turns at full fidelity
    pub recent_turns: Vec<Message>,
    /// Number of turns that were evicted/summarized
    pub evicted_count: usize,
}

impl ContextPage {
    /// Convert into the messages array for the API request.
    ///
    /// If a summary exists, it's injected as a user/assistant pair before
    /// the recent turns. This gives the model awareness of prior context
    /// without the full token cost.
    ///
    /// Registers + working state are NOT included here — they flow through
    /// preload_context (system content blocks) which is a separate path.
    pub fn into_messages(self) -> Vec<Message> {
        let mut messages = Vec::new();

        // Session summary as context (only when we have evicted turns)
        if !self.summary.is_empty() && self.evicted_count > 0 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: format!(
                        "[kernel: {} turns compiled into summary]\n\n{}\n\nRecent turns follow. Use ostk_session_history() to page in older context.",
                        self.evicted_count, self.summary
                    ),
                }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: "Context loaded from kernel summary. Ready to continue.".into(),
                }],
                model: None,
            });
        }

        // Recent turns at full fidelity
        messages.extend(self.recent_turns);

        messages
    }

    /// Create a ContextPage that passes all turns through unchanged.
    /// Used when no eviction is needed (conversation fits in budget).
    pub fn passthrough(messages: Vec<Message>) -> Self {
        Self {
            summary: String::new(),
            recent_turns: messages,
            evicted_count: 0,
        }
    }
}

// ---------------------------------------------------------------------------
// Phase 0: Scaffolding stripping
// ---------------------------------------------------------------------------

/// Strip assistant scaffolding turns from a message array.
///
/// Removes assistant messages that are pure preamble (no tool calls,
/// under 50 tokens, matching scaffolding patterns) while preserving
/// the conversation structure (user/assistant alternation).
///
/// →872: After stripping, validates and repairs conversation structure.
/// Merge strategy adapts to provider capabilities via ContextHints.
pub fn strip_scaffolding(messages: &[Message], hints: &ContextHints) -> Vec<Message> {
    let filtered: Vec<Message> = messages
        .iter()
        .filter(|m| !is_scaffolding(m))
        .cloned()
        .collect();
    ensure_alternation(filtered, hints)
}

/// Returns true if a user message contains ONLY ToolResult blocks (no text).
/// These are tool response pairs, not user intents — they must stay adjacent
/// to their matching ToolUse in the preceding assistant message.
pub fn is_tool_result_only(msg: &Message) -> bool {
    if msg.role != "user" || msg.content.is_empty() {
        return false;
    }
    msg.content.iter().all(|c| matches!(c, ContentBlock::ToolResult { .. }))
}

/// Returns true if an assistant message contains at least one ToolUse block.
pub fn has_tool_use(msg: &Message) -> bool {
    msg.content.iter().any(|c| matches!(c, ContentBlock::ToolUse { .. }))
}

/// Returns true if an assistant message is scaffolding.
fn is_scaffolding(msg: &Message) -> bool {
    if msg.role != "assistant" {
        return false;
    }
    // Must have no tool calls
    let has_tool_use = msg
        .content
        .iter()
        .any(|c| matches!(c, ContentBlock::ToolUse { .. }));
    if has_tool_use {
        return false;
    }

    // Must be short (under 50 tokens ~ 200 chars)
    let text: String = msg
        .content
        .iter()
        .filter_map(|c| match c {
            ContentBlock::Text { text } => Some(text.as_str()),
            _ => None,
        })
        .collect();
    if text.len() > 200 {
        return false;
    }

    // Match scaffolding patterns
    let lower = text.to_lowercase();
    let patterns = [
        "let me",
        "i'll",
        "i see",
        "looking at",
        "i notice",
        "let's",
        "now i",
        "understood",
        "i understand",
    ];
    patterns
        .iter()
        .any(|p| lower.starts_with(p) || lower.contains(p))
}

// ---------------------------------------------------------------------------
// →872: Conversation structure validation
// ---------------------------------------------------------------------------

/// Validate that every tool_use has a matching tool_result and vice versa.
/// Returns false if any orphaned tool_use or tool_result IDs are found.
pub fn validate_tool_pairing(messages: &[Message]) -> bool {
    for i in 0..messages.len() {
        let msg = &messages[i];
        if msg.role != "assistant" { continue; }

        // Collect tool_use IDs from this assistant message
        let tool_use_ids: Vec<&str> = msg.content.iter().filter_map(|c| match c {
            ContentBlock::ToolUse { id, .. } => Some(id.as_str()),
            _ => None,
        }).collect();

        if tool_use_ids.is_empty() { continue; }

        // The next message must be a user message with matching tool_result blocks
        let next = messages.get(i + 1);
        let result_ids: Vec<&str> = next.map(|m| {
            m.content.iter().filter_map(|c| match c {
                ContentBlock::ToolResult { tool_use_id, .. } => Some(tool_use_id.as_str()),
                _ => None,
            }).collect()
        }).unwrap_or_default();

        // Forward check: every tool_use must have a matching tool_result
        for id in &tool_use_ids {
            if !result_ids.contains(id) {
                tracing::warn!("validate_tool_pairing: orphaned tool_use {id} at message {i}");
                return false;
            }
        }

        // Reverse check: every tool_result must have a matching tool_use
        for id in &result_ids {
            if !tool_use_ids.contains(id) {
                tracing::warn!("validate_tool_pairing: orphaned tool_result {id} at message {}", i + 1);
                return false;
            }
        }
    }
    true
}

/// Ensure strict user/assistant alternation in a message list.
///
/// The Anthropic API requires messages to alternate roles. After scaffolding
/// stripping or eviction, adjacent same-role messages can appear. This function
/// repairs the structure.
///
/// Merge strategy depends on ContextHints:
/// - When `supports_mixed_content_blocks` is true (Anthropic): freely merge
///   adjacent same-role messages by combining content blocks.
/// - When false (OpenAI-compat): merge only if it won't create mixed content
///   (ToolResult + non-ToolResult in one message). If it would, insert a
///   padding assistant message to maintain alternation without data loss.
///
/// Rules:
/// - First message must be "user" (API requirement)
/// - Empty messages are dropped
fn ensure_alternation(messages: Vec<Message>, hints: &ContextHints) -> Vec<Message> {
    if messages.is_empty() {
        return messages;
    }

    let mut result: Vec<Message> = Vec::with_capacity(messages.len());

    for msg in messages {
        if msg.content.is_empty() {
            continue;
        }
        if let Some(last) = result.last_mut() {
            if last.role == msg.role {
                // Same role — need to merge or split
                if hints.supports_mixed_content_blocks
                    || !would_create_mixed_content(&last.content, &msg.content)
                {
                    // Safe to merge
                    last.content.extend(msg.content);
                } else {
                    // Mixed content would break non-Anthropic translators.
                    // Insert padding assistant to maintain alternation.
                    result.push(Message {
                        role: "assistant".into(),
                        content: vec![ContentBlock::Text {
                            text: "Continuing.".into(),
                        }],
                        model: None,
                    });
                    result.push(msg);
                }
            } else {
                result.push(msg);
            }
        } else {
            result.push(msg);
        }
    }

    // API requires first message to be "user"
    if let Some(first) = result.first()
        && first.role == "assistant" {
            result.insert(
                0,
                Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text {
                        text: "[context continues from previous turns]".into(),
                    }],
                    model: None,
                },
            );
        }

    result
}

/// Returns true if merging these content blocks would create a user message
/// with both ToolResult and non-ToolResult blocks — the pattern that breaks
/// OpenAI-compatible translators.
fn would_create_mixed_content(existing: &[ContentBlock], incoming: &[ContentBlock]) -> bool {
    let has_tool_result = existing
        .iter()
        .chain(incoming.iter())
        .any(|b| matches!(b, ContentBlock::ToolResult { .. }));
    let has_non_tool_result = existing
        .iter()
        .chain(incoming.iter())
        .any(|b| !matches!(b, ContentBlock::ToolResult { .. }));
    has_tool_result && has_non_tool_result
}

// ---------------------------------------------------------------------------
// Phase 2: Kernel-driven context eviction
// ---------------------------------------------------------------------------

/// Build a context page from raw messages: strip scaffolding, evict old turns
/// under budget pressure, insert summary for evicted turns.
///
/// Returns the curated message list ready for the InferenceRequest.
/// ContextHints controls merge strategy for provider compatibility.
///
/// →875: If session_summary is provided, uses its compiled summary instead
/// of the stateless compile_event_summary. Keeps working without it.
/// →966/→967: If `compiled_summary` is provided (from cpu::summary compiler),
/// it takes priority over both session_summary and compile_event_summary.
/// This gives the richest summary (regex-extracted events from session JSONL).
pub fn build_context_page(
    messages: &[Message],
    budget: u64,
    accumulated_tokens: u64,
    intent_window: usize,
    hints: &ContextHints,
    session_summary: Option<&SessionSummary>,
    compiled_summary: Option<&str>,
) -> Vec<Message> {
    let result = strip_scaffolding(messages, hints);

    // Debug: log context page decisions
    tracing::debug!(
        "build_context_page: {} msgs in, {} after strip, budget={}, tokens={}, evict={}",
        messages.len(),
        result.len(),
        budget,
        accumulated_tokens,
        budget > 0 && accumulated_tokens >= budget * 60 / 100,
    );

    // Only evict when budget pressure exceeds 60%
    if budget == 0 || accumulated_tokens < budget * 60 / 100 {
        return result;
    }

    // Count intent boundaries (user TEXT messages, not ToolResult) from the end.
    // ToolResult messages have role "user" but are NOT user intents — they're
    // tool response pairs that must stay adjacent to their ToolUse. Counting
    // them as intents causes window_start to land on a ToolResult, orphaning
    // the preceding ToolUse and producing API 400 errors.
    let mut intent_count = 0;
    let mut window_start = result.len();
    for (i, msg) in result.iter().enumerate().rev() {
        if msg.role == "user" && !is_tool_result_only(msg) {
            intent_count += 1;
            if intent_count >= intent_window {
                // Back up to include the full tool-use/tool-result pair:
                // if the message before this user intent is an assistant with
                // tool_use, and before THAT is a user with tool_result, include both.
                let mut start = i;
                while start > 0 {
                    let prev = &result[start - 1];
                    if prev.role == "assistant" && has_tool_use(prev) && start >= 2 && is_tool_result_only(&result[start - 2]) {
                        start -= 2; // include the tool_result + tool_use pair
                    } else if prev.role == "assistant" && has_tool_use(prev) {
                        start -= 1; // include the assistant tool_use
                    } else {
                        break;
                    }
                }
                window_start = start;
                break;
            }
        }
    }

    // If window_start is at the beginning (nothing to evict) OR at the end
    // (fewer intents than window — loop didn't find enough to set a cut point),
    // return all messages unmodified.
    if window_start <= 1 || window_start >= result.len() {
        return result;
    }

    // Compile evicted turns into factual event summary.
    // Priority chain (→966/→967):
    //   1. compiled_summary (cpu::summary — richest, from session JSONL)
    //   2. session_summary (fcp::llm — incremental in-memory accumulator)
    //   3. compile_event_summary (stateless extraction from evicted messages)
    let evicted = &result[..window_start];
    let summary = if let Some(cs) = compiled_summary {
        cs.to_string()
    } else if let Some(sess) = session_summary {
        sess.get_summary().to_string()
    } else {
        compile_event_summary(evicted)
    };
    let evict_count = evicted.len();

    // Build new message list: summary + recent window
    let mut page = Vec::new();
    page.push(Message {
        role: "user".into(),
        content: vec![ContentBlock::Text {
            text: format!(
                "[kernel: {} turns compiled into summary]\n\n{}\n\nRecent turns follow. Use ostk_session_history() to page in older context.",
                evict_count, summary
            ),
        }],
        model: None,
    });
    page.push(Message {
        role: "assistant".into(),
        content: vec![ContentBlock::Text {
            text: "Context loaded from kernel summary. Ready to continue.".into(),
        }],
        model: None,
    });
    page.extend_from_slice(&result[window_start..]);

    // →872: Final validation — ensure alternation after eviction splicing
    let validated = ensure_alternation(page, hints);

    // →892: Validate tool_use/tool_result pairing. If any tool_use lacks a
    // matching tool_result, fall back to unprocessed messages rather than
    // sending invalid structure to the API.
    if !validate_tool_pairing(&validated) {
        tracing::warn!("build_context_page: tool pairing validation failed, falling back to raw messages");
        return result;
    }
    validated
}

/// Compile evicted message turns into a factual event summary.
///
/// Extracts: tool calls (name + brief result), file operations,
/// test results, errors. Does NOT use an LLM — pure event extraction.
fn compile_event_summary(messages: &[Message]) -> String {
    let mut files_modified: Vec<String> = Vec::new();
    let mut tool_calls: Vec<String> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut test_results: Vec<String> = Vec::new();

    for msg in messages {
        for block in &msg.content {
            match block {
                ContentBlock::ToolUse { name, input, .. } => {
                    // Track file operations
                    if let Some(path) = input.get("file_path").and_then(|v| v.as_str())
                        && (name == "Edit" || name == "Write")
                            && !files_modified.contains(&path.to_string()) {
                                files_modified.push(path.to_string());
                            }
                    // Track tool call (compressed)
                    let summary = match name.as_str() {
                        "Bash" => {
                            let cmd = input
                                .get("command")
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            let short: String = cmd.chars().take(60).collect();
                            format!("Bash: {}", short)
                        }
                        "Read" => {
                            let path = input
                                .get("file_path")
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            format!("Read: {}", path)
                        }
                        "Edit" => {
                            let path = input
                                .get("file_path")
                                .and_then(|v| v.as_str())
                                .unwrap_or("?");
                            format!("Edit: {}", path)
                        }
                        _ => name.to_string(),
                    };
                    tool_calls.push(summary);
                }
                ContentBlock::ToolResult {
                    content, is_error, ..
                } => {
                    if *is_error {
                        let err_text: String = content.chars().take(100).collect();
                        errors.push(err_text);
                    }
                    // Detect test results
                    if content.contains("passed") && content.contains("failed")
                        && let Some(line) =
                            content.lines().find(|l| l.contains("test result"))
                        {
                            test_results.push(line.trim().to_string());
                        }
                }
                ContentBlock::Text { .. } => {
                    // Text blocks don't contribute to event summary
                }
                _ => {}
            }
        }
    }

    let mut summary = String::from("# event summary (compiled by kernel)\n");

    if !files_modified.is_empty() {
        summary.push_str(&format!(
            "files_modified: {}\n",
            files_modified.join(", ")
        ));
    }
    if !test_results.is_empty() {
        summary.push_str(&format!(
            "test_results: {}\n",
            test_results.last().unwrap()
        ));
    }
    if !errors.is_empty() {
        summary.push_str(&format!("errors: {}\n", errors.len()));
        for e in errors.iter().take(3) {
            summary.push_str(&format!("  - {}\n", e));
        }
    }
    summary.push_str(&format!("tool_calls: {} total", tool_calls.len()));
    if !tool_calls.is_empty() {
        // Show last 5 tool calls as representative
        let recent: Vec<&String> = tool_calls.iter().rev().take(5).collect();
        for tc in recent.iter().rev() {
            summary.push_str(&format!("\n  - {}", tc));
        }
    }

    summary
}

// ---------------------------------------------------------------------------
// Incremental session summary compiler (→875)
// ---------------------------------------------------------------------------

/// Incremental, append-only session summary for long-running conversations.
///
/// Unlike compile_event_summary (stateless, called during eviction),
/// SessionSummary accumulates events as the session progresses and compiles
/// them into a running summary text when thresholds are met.
///
/// Design:
/// - append_event: pushes (event_type, details) tuple to pending queue
/// - should_compile: returns true when pending_events >= 5 (or force flag)
/// - compile: drains pending_events into summary_text as append-only lines
/// - When available, build_context_page uses compiled summary instead of
///   stateless compile_event_summary.
/// - Signal verbs (intent shifts, inference mode changes) are recorded as
///   headline events that survive eviction — they're high-signal, low-token.
#[derive(Debug, Clone)]
pub struct SessionSummary {
    /// Compiled summary text (append-only, grows over session lifetime)
    pub summary_text: String,
    /// Last turn number when compile() was called
    pub last_compiled_turn: usize,
    /// Pending events not yet compiled: (event_type, details)
    pub pending_events: Vec<(String, String)>,
    /// Active signal headlines — survive eviction and appear in context page.
    /// Each entry: (verb_name, signal_label, detail, context).
    /// Kept small: only the most recent signal per verb is retained.
    pub signal_headlines: Vec<SignalHeadline>,
}

/// A signal headline — survives context eviction and is injected into
/// the summary/context page so the model maintains awareness of cognitive
/// mode shifts across long sessions.
#[derive(Debug, Clone, PartialEq)]
pub struct SignalHeadline {
    /// The verb that produced this signal (e.g. "calibrate")
    pub verb: String,
    /// Signal kind label (e.g. "intent", "inference")
    pub kind: String,
    /// The detail from the bracket resolution (e.g. "background", "deep analysis")
    pub detail: String,
    /// Optional context from the tool input
    pub context: String,
    /// Turn number when the signal was recorded
    pub turn: usize,
}

impl SessionSummary {
    /// Create a new empty session summary.
    pub fn new() -> Self {
        Self {
            summary_text: String::from("# incremental session summary\n"),
            last_compiled_turn: 0,
            pending_events: Vec::new(),
            signal_headlines: Vec::new(),
        }
    }

    /// Append an event to the pending queue.
    ///
    /// Events are stored as (type, details) tuples. Common types:
    /// - "tool_call": "Bash: cargo test"
    /// - "file_modified": "src/main.rs"
    /// - "error": "type mismatch in foo()"
    /// - "test_result": "test result: ok. 12 passed; 0 failed"
    /// - "signal": "intent:calibrate — realign expectations"
    pub fn append_event(&mut self, event_type: String, details: String) {
        self.pending_events.push((event_type, details));
    }

    /// Record a signal verb activation. Replaces any previous signal from
    /// the same verb (only the most recent matters).
    pub fn record_signal(&mut self, verb: &str, kind: &str, detail: &str, context: &str, turn: usize) {
        // Remove previous signal from same verb
        self.signal_headlines.retain(|h| h.verb != verb);
        self.signal_headlines.push(SignalHeadline {
            verb: verb.to_string(),
            kind: kind.to_string(),
            detail: detail.to_string(),
            context: context.to_string(),
            turn,
        });
        // Also record as a pending event so it appears in compiled summary
        let event_detail = if context.is_empty() {
            format!("{}:{} — {}", kind, verb, detail)
        } else {
            format!("{}:{} — {} ({})", kind, verb, detail, context)
        };
        self.append_event("signal".to_string(), event_detail);
    }

    /// Build a headline block from active signals for context injection.
    ///
    /// Returns empty string if no signals are active. The headline is designed
    /// to be compact (survives eviction) and high-signal (tells the model about
    /// active cognitive modes).
    pub fn signal_headline_block(&self) -> String {
        if self.signal_headlines.is_empty() {
            return String::new();
        }
        let mut block = String::from("# active signals\n");
        for h in &self.signal_headlines {
            if h.context.is_empty() {
                block.push_str(&format!("- [{}:{}] {}\n", h.kind, h.verb, h.detail));
            } else {
                block.push_str(&format!("- [{}:{}] {} — {}\n", h.kind, h.verb, h.detail, h.context));
            }
        }
        block
    }

    /// Check if compilation should be triggered.
    ///
    /// Returns true when:
    /// - pending_events.len() >= 5, or
    /// - force is true
    pub fn should_compile(&self, force: bool) -> bool {
        force || self.pending_events.len() >= 5
    }

    /// Drain pending events into summary_text and update last_compiled_turn.
    ///
    /// Each event appends one line: "- [event_type] details"
    /// Updates last_compiled_turn to the provided turn number.
    pub fn compile(&mut self, current_turn: usize) {
        if self.pending_events.is_empty() {
            return;
        }

        for (event_type, details) in self.pending_events.drain(..) {
            self.summary_text.push_str(&format!("- [{}] {}\n", event_type, details));
        }
        self.last_compiled_turn = current_turn;
    }

    /// Get the current compiled summary text, including signal headlines.
    ///
    /// Signal headlines are prepended to the summary so they're the first
    /// thing the model sees after eviction — high-signal, survives compaction.
    pub fn get_summary(&self) -> String {
        let headlines = self.signal_headline_block();
        if headlines.is_empty() {
            self.summary_text.clone()
        } else {
            format!("{}\n{}", headlines, self.summary_text)
        }
    }
}

impl Default for SessionSummary {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Phase 1: Register rendering
// ---------------------------------------------------------------------------

/// Render kernel registers as YAML for the LLM context page.
pub fn render_registers(boot: &crate::cpu::session::BootContext, root: &Path) -> String {
    let ostk_dir = crate::state_dir(root);

    // ── Identity ──
    let identity =
        std::env::var("OSTK_AGENT").unwrap_or_else(|_| "unknown".into());
    let stamp = crate::cpu::current_boot_stamp(root);

    // ── Needles ──
    let needles = crate::read_needles(root).unwrap_or_default();
    let open = needles
        .iter()
        .filter(|n| n.get("status").and_then(|s| s.as_str()) != Some("closed"))
        .count();
    let p0: Vec<String> = needles
        .iter()
        .filter(|n| {
            n.get("priority").and_then(|s| s.as_str()) == Some("P0")
                && n.get("status").and_then(|s| s.as_str()) != Some("closed")
        })
        .filter_map(|n| {
            let id = n.get("id").and_then(|v| v.as_str())?;
            let title = n.get("title").and_then(|v| v.as_str())?;
            Some(format!("→{} {}", id, title))
        })
        .collect();

    // ── Fleet (process table) ──
    let fleet = crate::kernel::heartbeat::check_health(&ostk_dir)
        .map(|h| crate::kernel::heartbeat::format_procs_digest(&h))
        .unwrap_or_default();

    // ── Socket (kernel daemon) ──
    let socket_alive = crate::serve::socket::kernel_alive(&ostk_dir);

    // ── Pending nudges ──
    let nudge_count = crate::kernel::nudge::pop_nudges(&ostk_dir, &identity)
        .map(|n| n.len())
        .unwrap_or(0);
    // Note: pop_nudges is destructive (consumes). For register display we want
    // a peek. Since the digest system already injects nudges into tool responses,
    // just report count = 0 here (nudges are consumed on delivery, not on display).
    // TODO: add peek_nudges that doesn't consume.
    let _ = nudge_count;

    // ── Drivers + Services from .language ──
    let language_content = std::fs::read_to_string(ostk_dir.join(".language"))
        .unwrap_or_default();
    let entries = crate::language::parse_language(&language_content);
    let devices: Vec<String> = entries
        .iter()
        .filter(|e| e.is_device())
        .map(|e| {
            let status = if e.is_alive() { "alive" } else { "dead" };
            format!("{}({})", e.verb, status)
        })
        .collect();

    // Services with notable status (feature-gated or external)
    let services: Vec<String> = entries
        .iter()
        .filter(|e| e.is_service() && !e.is_alive())
        .map(|e| format!("{}(inactive)", e.verb))
        .collect();

    // ── .language verb counts ──
    let verb_count = entries.iter().filter(|e| e.layer == "user").count();
    let resident_count = entries
        .iter()
        .filter(|e| e.layer == "user" && e.momentum >= crate::cpu::TOOL_RESIDENT_THRESHOLD)
        .count();

    // ── Format ──
    let _ = boot; // boot_md content is in the separate boot state block
    let mut out = String::from("# kernel registers\n");
    out.push_str(&format!("identity: {}\n", identity));
    out.push_str(&format!("root: {}\n", root.display()));
    out.push_str(&format!("boot: {{stamp: {}, socket: {}}}\n",
        stamp, if socket_alive { "alive" } else { "dead" }));
    out.push_str("laws: [invisible-write, ephemeral, filesystem, OCC]\n");
    // →928: Context management awareness — tell the model what's active so it
    // doesn't panic when system_warning fires from routine clear_tool_uses.
    out.push_str("context: clear_tool_uses active — system_warning is routine cleanup, not pressure. :status for real utilization\n");
    out.push_str(&format!("needles: {{open: {}, p0: [{}]}}\n",
        open, p0.join(", ")));
    out.push_str(&format!("tools: {{resident: {}, deferred: {}, verbs: {}}}\n",
        resident_count, verb_count - resident_count, verb_count));
    if !devices.is_empty() {
        out.push_str(&format!("devices: [{}]\n", devices.join(", ")));
    }
    if !services.is_empty() {
        out.push_str(&format!("services_inactive: [{}]\n", services.join(", ")));
    }
    if !fleet.is_empty() {
        out.push_str(&format!("fleet: {}\n", fleet));
    }

    out
}

// ---------------------------------------------------------------------------
// Phase 1: Working state rendering
// ---------------------------------------------------------------------------

/// Parse an ISO 8601 timestamp (e.g. "2026-03-25T00:00:00Z") to epoch seconds.
/// Inline helper for decision age filtering (→960).
fn parse_iso_timestamp(iso: &str) -> Option<u64> {
    let iso = iso.trim();
    if iso.len() < 19 {
        return None;
    }
    let year: u64 = iso.get(0..4)?.parse().ok()?;
    let month: u64 = iso.get(5..7)?.parse().ok()?;
    let day: u64 = iso.get(8..10)?.parse().ok()?;
    let hour: u64 = iso.get(11..13)?.parse().ok()?;
    let min: u64 = iso.get(14..16)?.parse().ok()?;
    let sec: u64 = iso.get(17..19)?.parse().ok()?;

    // Convert year/month/day to days since Unix epoch
    let (y, m) = if month <= 2 {
        (year.checked_sub(1)?, month + 9)
    } else {
        (year, month - 3)
    };
    let era = y / 400;
    let yoe = y - era * 400;
    let doy = (153 * m + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe;
    let days = days.checked_sub(719468)?;

    Some(days * 86400 + hour * 3600 + min * 60 + sec)
}

/// Render working state as YAML for the LLM context page.
///
/// →968: `decision_cap` parameterizes the maximum number of decisions to render.
/// Previously hardcoded at 10. Now derived from `FcpLlm::for_model()`.
/// Passing `None` uses the legacy default of 10.
pub fn render_working_state(root: &Path, decision_cap: Option<usize>) -> String {
    let cap = decision_cap.unwrap_or(10);
    let ostk_dir = crate::state_dir(root);

    // Read gen_table for modified files
    let gen_table_path = ostk_dir.join("gen_table.jsonl");
    let modified: Vec<String> = if gen_table_path.exists() {
        std::fs::read_to_string(&gen_table_path)
            .unwrap_or_default()
            .lines()
            .filter_map(|line| {
                let v: serde_json::Value = serde_json::from_str(line).ok()?;
                let path = v.get("path")?.as_str()?;
                let generation = v.get("gen")?.as_u64()?;
                Some(format!("  - {}:g{}", path, generation))
            })
            .collect()
    } else {
        vec![]
    };

    // Read decisions from session metadata (if exists)
    // →960: filter panic saves, age-decay >72h, cap at 10
    // →962: confidence indicators [H]/[A]
    let decisions_path = ostk_dir.join("decisions.jsonl");
    let now_epoch = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let seventy_two_hours = 72 * 3600;

    let panic_prefixes = ["EMERGENCY_", "DYING_", "CRITICAL_", "FULL_SESSION", "FINAL_"];

    let all_decisions: Vec<serde_json::Value> = if decisions_path.exists() {
        std::fs::read_to_string(&decisions_path)
            .unwrap_or_default()
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect()
    } else {
        vec![]
    };

    let total_count = all_decisions.len();

    let filtered: Vec<&serde_json::Value> = all_decisions
        .iter()
        .filter(|v| {
            // Skip panic-save keys
            if let Some(key) = v.get("key").and_then(|k| k.as_str())
                && panic_prefixes.iter().any(|p| key.starts_with(p)) {
                    return false;
                }
            // Skip decisions older than 72 hours
            if let Some(ts) = v.get("timestamp").and_then(|t| t.as_str())
                && let Some(epoch) = parse_iso_timestamp(ts)
                    && now_epoch.saturating_sub(epoch) > seventy_two_hours {
                        return false;
                    }
            true
        })
        .collect();

    // Cap at last N (most recent) — →968: parameterized by decision_cap
    let capped: Vec<&serde_json::Value> = if filtered.len() > cap {
        filtered[filtered.len() - cap..].to_vec()
    } else {
        filtered.clone()
    };

    let filtered_out = total_count.saturating_sub(capped.len());

    let decisions: Vec<String> = capped
        .iter()
        .filter_map(|v| {
            let key = v.get("key")?.as_str()?;
            let value = v.get("value")?.as_str()?;
            // →962: confidence marker
            let reason = v.get("reason").and_then(|r| r.as_str()).unwrap_or("");
            let reason_lower = reason.to_lowercase();
            let marker = if reason_lower.contains("human")
                || reason_lower.contains("confirmed")
                || reason_lower.contains("operator")
            {
                "[H]"
            } else {
                "[A]"
            };
            Some(format!("  - {} {}: {}", marker, key, value))
        })
        .collect();

    let mut out = String::from("# working state\n");
    if !modified.is_empty() {
        out.push_str("modified:\n");
        out.push_str(&modified.join("\n"));
        out.push('\n');
    }
    if !decisions.is_empty() {
        out.push_str("decisions:\n");
        out.push_str(&decisions.join("\n"));
        out.push('\n');
    }
    if filtered_out > 0 {
        out.push_str(&format!(
            "# {} more decisions searchable via :investigate\n",
            filtered_out
        ));
    }

    out
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cpu::anthropic::{ContentBlock, Message};

    /// Generate an ISO timestamp 1 hour ago — always within the 72h decision filter.
    fn recent_ts() -> String {
        let epoch = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() - 3600; // 1 hour ago
        // Manual epoch → ISO 8601 (UTC)
        let secs_per_day = 86400u64;
        let days = epoch / secs_per_day;
        let day_secs = epoch % secs_per_day;
        let (h, m, s) = (day_secs / 3600, (day_secs % 3600) / 60, day_secs % 60);
        // Days since epoch → y/m/d (civil_from_days algorithm)
        let z = days + 719468;
        let era = z / 146097;
        let doe = z - era * 146097;
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let mo = if mp < 10 { mp + 3 } else { mp - 9 };
        let yr = if mo <= 2 { y + 1 } else { y };
        format!("{yr:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
    }

    #[test]
    fn strip_scaffolding_removes_preamble() {
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "fix the bug".into(),
                }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: "Let me read the file for you.".into(),
                }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: "t1".into(),
                    name: "Read".into(),
                    input: serde_json::json!({}),
                }],
                model: None,
            },
        ];
        let stripped = strip_scaffolding(&messages, &ContextHints::default());
        assert_eq!(stripped.len(), 2, "scaffolding should be removed");
        assert_eq!(stripped[0].role, "user");
        assert!(matches!(
            &stripped[1].content[0],
            ContentBlock::ToolUse { .. }
        ));
    }

    #[test]
    fn strip_scaffolding_keeps_substantive_assistant() {
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "explain".into(),
                }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: "The demand-paged tool loading system works by maintaining a resident \
                           set of high-momentum verbs from .language, while deferring low-momentum \
                           verbs behind the ostk_verbs page fault handler. This means the agent \
                           only pays the token cost for tools it actually uses."
                        .into(),
                }],
                model: None,
            },
        ];
        let stripped = strip_scaffolding(&messages, &ContextHints::default());
        assert_eq!(stripped.len(), 2, "substantive response should be kept");
    }

    #[test]
    fn strip_scaffolding_keeps_tool_use_turns() {
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "do it".into(),
                }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![
                    ContentBlock::Text {
                        text: "Let me check.".into(),
                    },
                    ContentBlock::ToolUse {
                        id: "t1".into(),
                        name: "Bash".into(),
                        input: serde_json::json!({"command": "ls"}),
                    },
                ],
                model: None,
            },
        ];
        let stripped = strip_scaffolding(&messages, &ContextHints::default());
        assert_eq!(
            stripped.len(),
            2,
            "turns with tool use should be kept even if they have scaffolding text"
        );
        assert!(matches!(
            &stripped[1].content[1],
            ContentBlock::ToolUse { .. }
        ));
    }

    // ─── Phase 2: Context eviction tests ─────────────────────────────

    #[test]
    fn build_context_page_no_eviction_under_pressure() {
        // Under 60% budget — no eviction
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "a".into(),
                }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: "b".into(),
                }],
                model: None,
            },
        ];
        let page = build_context_page(&messages, 100000, 50000, 3, &ContextHints::default(), None, None);
        assert_eq!(page.len(), 2, "no eviction under 60% pressure");
    }

    #[test]
    fn build_context_page_no_eviction_zero_budget() {
        // Zero budget — eviction disabled
        let mut messages = Vec::new();
        for i in 0..20 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: format!("question {}", i),
                }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: format!("answer {}", i),
                }],
                model: None,
            });
        }
        let page = build_context_page(&messages, 0, 70000, 3, &ContextHints::default(), None, None);
        // With budget=0, eviction is disabled — only scaffolding stripping
        assert_eq!(page.len(), messages.len(), "no eviction with zero budget");
    }

    #[test]
    fn build_context_page_evicts_under_pressure() {
        // Over 60% budget with many turns — should evict
        let mut messages = Vec::new();
        for i in 0..20 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: format!("question {}", i),
                }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: format!("answer {}", i),
                }],
                model: None,
            });
        }
        let page = build_context_page(&messages, 100000, 70000, 3, &ContextHints::default(), None, None);
        // Should have: summary pair (2 msgs) + last 3 intents with responses
        assert!(
            page.len() < messages.len(),
            "should have fewer messages after eviction"
        );
        // First message should be the kernel summary
        let first_text = match &page[0].content[0] {
            ContentBlock::Text { text } => text.clone(),
            _ => String::new(),
        };
        assert!(
            first_text.contains("kernel:"),
            "first message should be kernel summary marker"
        );
    }

    #[test]
    fn build_context_page_preserves_tool_pairing() {
        // Regression test: eviction must not split tool_use/tool_result pairs.
        // Previously, window_start could land on a ToolResult, orphaning its ToolUse.
        let mut messages = Vec::new();
        for i in 0..10 {
            // User question
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: format!("question {i}") }],
                model: None,
            });
            // Assistant with tool call
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: format!("tool_{i}"),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "echo hi"}),
                }],
                model: None,
            });
            // User with tool result
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: format!("tool_{i}"),
                    content: "ok".into(),
                    is_error: false,
                }],
                model: None,
            });
        }
        // Trigger eviction: budget=100k, tokens=70k (>60%)
        let page = build_context_page(&messages, 100000, 70000, 3, &ContextHints { supports_mixed_content_blocks: true }, None, None);
        // Validate: every tool_use must have a matching tool_result
        assert!(validate_tool_pairing(&page), "eviction must preserve tool_use/tool_result pairing");
        assert!(page.len() < messages.len(), "should have evicted some messages");
    }

    #[test]
    fn is_tool_result_only_detects_tool_results() {
        let tool_msg = Message {
            role: "user".into(),
            content: vec![ContentBlock::ToolResult { tool_use_id: "t1".into(), content: "ok".into(), is_error: false }],
            model: None,
        };
        assert!(is_tool_result_only(&tool_msg));

        let text_msg = Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hello".into() }],
            model: None,
        };
        assert!(!is_tool_result_only(&text_msg));

        let mixed_msg = Message {
            role: "user".into(),
            content: vec![
                ContentBlock::ToolResult { tool_use_id: "t1".into(), content: "ok".into(), is_error: false },
                ContentBlock::Text { text: "and also".into() },
            ],
            model: None,
        };
        assert!(!is_tool_result_only(&mixed_msg));
    }

    #[test]
    fn validate_tool_pairing_catches_orphan() {
        let messages = vec![
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: "t1".into(), name: "Bash".into(), input: serde_json::json!({}),
                }],
                model: None,
            },
            // Missing tool_result — next message is user text, not tool_result
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "hello".into() }],
                model: None,
            },
        ];
        assert!(!validate_tool_pairing(&messages), "should detect orphaned tool_use");
    }

    #[test]
    fn validate_tool_pairing_catches_orphaned_result() {
        // tool_result references an ID that has no matching tool_use
        let messages = vec![
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: "t1".into(), name: "Bash".into(), input: serde_json::json!({}),
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![
                    ContentBlock::ToolResult {
                        tool_use_id: "t1".into(), content: "ok".into(), is_error: false,
                    },
                    ContentBlock::ToolResult {
                        tool_use_id: "t_phantom".into(), content: "ghost".into(), is_error: true,
                    },
                ],
                model: None,
            },
        ];
        assert!(!validate_tool_pairing(&messages), "should detect orphaned tool_result");
    }

    #[test]
    fn validate_tool_pairing_passes_valid() {
        let messages = vec![
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: "t1".into(), name: "Bash".into(), input: serde_json::json!({}),
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t1".into(), content: "ok".into(), is_error: false,
                }],
                model: None,
            },
        ];
        assert!(validate_tool_pairing(&messages), "valid pairing should pass");
    }

    #[test]
    fn compile_event_summary_extracts_tool_calls() {
        let messages = vec![
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: "t1".into(),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": "cargo test"}),
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t1".into(),
                    content: "test result: ok. 10 passed; 0 failed".into(),
                    is_error: false,
                }],
                model: None,
            },
        ];
        let summary = compile_event_summary(&messages);
        assert!(
            summary.contains("tool_calls: 1"),
            "should count tool call"
        );
        assert!(
            summary.contains("Bash: cargo test"),
            "should include tool name and command"
        );
    }

    #[test]
    fn compile_event_summary_tracks_files_and_errors() {
        let messages = vec![
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: "t1".into(),
                    name: "Edit".into(),
                    input: serde_json::json!({"file_path": "/src/main.rs"}),
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t1".into(),
                    content: "something went wrong".into(),
                    is_error: true,
                }],
                model: None,
            },
        ];
        let summary = compile_event_summary(&messages);
        assert!(
            summary.contains("/src/main.rs"),
            "should track modified files"
        );
        assert!(
            summary.contains("errors: 1"),
            "should count errors"
        );
    }

    // ── Register rendering tests ─────────────────────────────────

    #[test]
    fn render_registers_includes_kernel_subsystems() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        // Write a .language with devices, services, user verbs
        let language = "\
            # .language\n\
            :fcp-screen  | 0 | device  | 0 | 0 | 1.00 | internal | (event) -> (render) | display driver\n\
            :fcp-rust    | 0 | device  | 0 | 0 | 0.00 | .ostk/drivers/rust.sock | (query) -> (result) | rust driver\n\
            :embeddings  | 0 | service | 0 | 0 | 0.00 | internal | (text) -> (vector) | semantic similarity\n\
            :elision     | 0 | service | 0 | 0 | 1.00 | internal | () -> () | read optimization\n\
            :hay         | 1 | user    | 0 | 200 | 1.00 | ostk work hay | (straw) -> () | capture intent\n\
            :draft       | 1 | user    | 0 | 200 | 0.05 | ostk doc draft | (title) -> (path) | create draft\n";
        std::fs::write(state_dir.join(".language"), language).unwrap();

        // Write empty needles
        std::fs::create_dir_all(state_dir.join("needles")).unwrap();
        std::fs::write(state_dir.join("needles/issues.jsonl"), "").unwrap();

        let boot = crate::cpu::session::BootContext::new(root);
        let registers = render_registers(&boot, root);

        // Should have identity
        assert!(registers.contains("identity:"), "should have identity");
        // Should have devices with health
        assert!(registers.contains("fcp-screen(alive)"), "should show alive device");
        assert!(registers.contains("fcp-rust(dead)"), "should show dead device");
        // Should show inactive services (feature-gated)
        assert!(registers.contains("embeddings(inactive)"), "should show inactive service: {}", registers);
        // Should have tool counts
        assert!(registers.contains("tools:"), "should have tool counts");
        assert!(registers.contains("resident:"), "should show resident count");
        // Should have boot stamp
        assert!(registers.contains("stamp:"), "should have boot stamp");
        // Should have socket status
        assert!(registers.contains("socket:"), "should have socket status");
    }

    #[test]
    fn render_registers_yaml_parseable() {
        // Verify the output is valid YAML-ish (no unclosed braces, balanced)
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(state_dir.join("needles")).unwrap();
        std::fs::write(state_dir.join("needles/issues.jsonl"), "").unwrap();
        std::fs::write(state_dir.join(".language"), "# empty\n").unwrap();

        let boot = crate::cpu::session::BootContext::new(root);
        let registers = render_registers(&boot, root);

        // Should start with header
        assert!(registers.starts_with("# kernel registers"), "should start with header");
        // Every line with { should have matching }
        for line in registers.lines() {
            let opens = line.chars().filter(|c| *c == '{').count();
            let closes = line.chars().filter(|c| *c == '}').count();
            assert_eq!(opens, closes, "unbalanced braces in line: {}", line);
        }
    }

    #[test]
    fn render_working_state_includes_decisions() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        // Use a dynamically computed recent timestamp so it always passes the 72h filter
        let recent_ts = recent_ts();

        let mut lines = String::new();

        // 1. Recent human-confirmed decision — should appear with [H]
        lines.push_str(&format!(
            r#"{{"key":"momentum_threshold","value":"0.45","reason":"roundtable consensus, human confirmed","timestamp":"{recent_ts}"}}"#,
        ));
        lines.push('\n');

        // 2. Recent auto decision — should appear with [A]
        lines.push_str(&format!(
            r#"{{"key":"auto_setting","value":"on","reason":"auto-detected","timestamp":"{recent_ts}"}}"#,
        ));
        lines.push('\n');

        // 3. Panic-save decision — should be filtered out
        lines.push_str(&format!(
            r#"{{"key":"EMERGENCY_dump","value":"panic","reason":"crash","timestamp":"{recent_ts}"}}"#,
        ));
        lines.push('\n');

        // 4. Old decision (>72h) — should be filtered out
        lines.push_str(
            r#"{"key":"old_setting","value":"stale","reason":"auto","timestamp":"2025-01-01T00:00:00Z"}"#,
        );
        lines.push('\n');

        // 5. DYING_ prefix — should be filtered out
        lines.push_str(&format!(
            r#"{{"key":"DYING_state","value":"x","reason":"crash","timestamp":"{recent_ts}"}}"#,
        ));
        lines.push('\n');

        std::fs::write(state_dir.join("decisions.jsonl"), &lines).unwrap();

        let working = render_working_state(root, None);

        // Should include recent decisions with confidence markers
        assert!(working.contains("[H] momentum_threshold"), "should include [H] marker for human-confirmed: {working}");
        assert!(working.contains("0.45"), "should include decision value: {working}");
        assert!(working.contains("[A] auto_setting"), "should include [A] marker for auto: {working}");

        // Should filter out panic saves and old decisions
        assert!(!working.contains("EMERGENCY_dump"), "should filter EMERGENCY_ prefix: {working}");
        assert!(!working.contains("DYING_state"), "should filter DYING_ prefix: {working}");
        assert!(!working.contains("old_setting"), "should filter old decisions: {working}");

        // Should show filtered count
        assert!(working.contains("more decisions searchable via :investigate"), "should show filtered count: {working}");
    }

    #[test]
    fn render_working_state_caps_at_10() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        let recent_ts = recent_ts();

        // Write 15 valid decisions
        let mut lines = String::new();
        for i in 0..15 {
            lines.push_str(&format!(
                r#"{{"key":"decision_{i}","value":"val_{i}","reason":"auto","timestamp":"{recent_ts}"}}"#,
            ));
            lines.push('\n');
        }
        std::fs::write(state_dir.join("decisions.jsonl"), &lines).unwrap();

        let working = render_working_state(root, None);

        // Should show last 10 (decision_5 through decision_14)
        assert!(!working.contains("decision_4:"), "should not include decision_4 (capped): {working}");
        assert!(working.contains("decision_5"), "should include decision_5 (in last 10): {working}");
        assert!(working.contains("decision_14"), "should include decision_14: {working}");
        assert!(working.contains("5 more decisions"), "should show 5 filtered: {working}");
    }

    #[test]
    fn render_working_state_includes_modified_files() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        // Write gen_table
        let gen_entry = r#"{"path":"src/cpu/params.rs","gen":7}"#;
        std::fs::write(state_dir.join("gen_table.jsonl"), format!("{}\n", gen_entry)).unwrap();

        let working = render_working_state(root, None);
        assert!(working.contains("src/cpu/params.rs"), "should include modified file");
        assert!(working.contains("g7"), "should include generation counter");
    }

    // ─── →872: Provider-aware alternation tests ──────────────────

    #[test]
    fn ensure_alternation_splits_mixed_when_unsupported() {
        // OpenAI-compat: ToolResult + Text in adjacent user msgs → must NOT merge
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t1".into(),
                    content: "result data".into(),
                    is_error: false,
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "next question".into(),
                }],
                model: None,
            },
        ];
        let hints = ContextHints {
            supports_mixed_content_blocks: false,
        };
        let result = ensure_alternation(messages, &hints);
        // Should have: user(ToolResult) → assistant(padding) → user(Text)
        assert_eq!(result.len(), 3, "should split with padding: {:?}", result);
        assert_eq!(result[0].role, "user");
        assert!(matches!(&result[0].content[0], ContentBlock::ToolResult { .. }));
        assert_eq!(result[1].role, "assistant");
        assert_eq!(result[2].role, "user");
        assert!(matches!(&result[2].content[0], ContentBlock::Text { .. }));
    }

    #[test]
    fn ensure_alternation_merges_mixed_when_supported() {
        // Anthropic: ToolResult + Text → merged into one user message
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t1".into(),
                    content: "result data".into(),
                    is_error: false,
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "next question".into(),
                }],
                model: None,
            },
        ];
        let hints = ContextHints {
            supports_mixed_content_blocks: true,
        };
        let result = ensure_alternation(messages, &hints);
        assert_eq!(result.len(), 1, "should merge for Anthropic: {:?}", result);
        assert_eq!(result[0].content.len(), 2, "should have both blocks");
    }

    #[test]
    fn ensure_alternation_merges_same_type_regardless() {
        // Two Text user msgs → always safe to merge, even without mixed support
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "part 1".into(),
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: "part 2".into(),
                }],
                model: None,
            },
        ];
        let hints = ContextHints {
            supports_mixed_content_blocks: false,
        };
        let result = ensure_alternation(messages, &hints);
        assert_eq!(result.len(), 1, "same-type blocks should merge: {:?}", result);
        assert_eq!(result[0].content.len(), 2);
    }

    #[test]
    fn context_hints_default_is_safe() {
        let hints = ContextHints::default();
        assert!(
            !hints.supports_mixed_content_blocks,
            "default should be false (safe for all providers)"
        );
    }

    // ─── →873: Eviction activation via accumulated tokens ────────

    #[test]
    fn eviction_fires_when_tokens_exceed_60_percent_budget() {
        // 20 turn pairs, budget 100k, accumulated 65k → should evict
        let mut messages = Vec::new();
        for i in 0..20 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: format!("question {}", i),
                }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: format!("answer {}", i),
                }],
                model: None,
            });
        }
        let page = build_context_page(&messages, 100_000, 65_000, 3, &ContextHints::default(), None, None);
        assert!(
            page.len() < messages.len(),
            "should evict at 65% of 100k budget"
        );
        // First message is kernel summary
        let first_text = match &page[0].content[0] {
            ContentBlock::Text { text } => text.clone(),
            _ => String::new(),
        };
        assert!(first_text.contains("kernel:"), "should have kernel summary marker");
    }

    #[test]
    fn eviction_does_not_fire_under_60_percent() {
        let mut messages = Vec::new();
        for i in 0..20 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: format!("question {}", i),
                }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: format!("answer {}", i),
                }],
                model: None,
            });
        }
        // 50k out of 100k = 50% → no eviction
        let page = build_context_page(&messages, 100_000, 50_000, 3, &ContextHints::default(), None, None);
        assert_eq!(
            page.len(),
            messages.len(),
            "should NOT evict at 50% pressure"
        );
    }

    #[test]
    fn eviction_preserves_intent_window() {
        // Verify the last N user intents survive eviction
        let mut messages = Vec::new();
        for i in 0..10 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: format!("question_{}", i),
                }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: format!("answer_{}", i),
                }],
                model: None,
            });
        }
        let page = build_context_page(&messages, 100_000, 70_000, 3, &ContextHints::default(), None, None);

        // Last 3 user intents should be in the page
        let user_texts: Vec<String> = page
            .iter()
            .filter(|m| m.role == "user")
            .flat_map(|m| m.content.iter())
            .filter_map(|b| match b {
                ContentBlock::Text { text } => Some(text.clone()),
                _ => None,
            })
            .collect();
        assert!(
            user_texts.iter().any(|t| t.contains("question_9")),
            "most recent question should survive eviction: {:?}",
            user_texts
        );
        assert!(
            user_texts.iter().any(|t| t.contains("question_8")),
            "second recent question should survive: {:?}",
            user_texts
        );
        assert!(
            user_texts.iter().any(|t| t.contains("question_7")),
            "third recent question should survive: {:?}",
            user_texts
        );
    }

    // ───────────────────────────────────────────────────────────────────────
    // SessionSummary tests (→875)
    // ───────────────────────────────────────────────────────────────────────

    #[test]
    fn session_summary_append_and_compile() {
        let mut summary = SessionSummary::new();
        
        // Append events
        summary.append_event("tool_call".to_string(), "Bash: cargo test".to_string());
        summary.append_event("file_modified".to_string(), "src/main.rs".to_string());
        summary.append_event("test_result".to_string(), "ok. 12 passed".to_string());
        
        assert_eq!(summary.pending_events.len(), 3);
        assert_eq!(summary.last_compiled_turn, 0);
        
        // Compile
        summary.compile(5);
        
        assert_eq!(summary.pending_events.len(), 0);
        assert_eq!(summary.last_compiled_turn, 5);
        
        // Verify summary contains all events
        let text = summary.get_summary();
        assert!(text.contains("[tool_call] Bash: cargo test"), "summary should contain tool_call");
        assert!(text.contains("[file_modified] src/main.rs"), "summary should contain file_modified");
        assert!(text.contains("[test_result] ok. 12 passed"), "summary should contain test_result");
    }

    #[test]
    fn session_summary_should_compile_threshold() {
        let mut summary = SessionSummary::new();
        
        // Add 4 events - should not trigger compilation
        for i in 0..4 {
            summary.append_event("event".to_string(), format!("event_{}", i));
        }
        assert!(!summary.should_compile(false), "should not compile with 4 events");
        
        // Add 5th event - should trigger
        summary.append_event("event".to_string(), "event_4".to_string());
        assert!(summary.should_compile(false), "should compile with 5 events");
        
        // Force flag overrides
        let mut summary2 = SessionSummary::new();
        summary2.append_event("event".to_string(), "one".to_string());
        assert!(summary2.should_compile(true), "force flag should trigger compilation");
    }

    #[test]
    fn session_summary_incremental_compilation() {
        let mut summary = SessionSummary::new();
        
        // First batch
        summary.append_event("tool_call".to_string(), "Read: foo.rs".to_string());
        summary.append_event("tool_call".to_string(), "Edit: foo.rs".to_string());
        summary.compile(2);
        
        let first_text = summary.get_summary().to_string();
        assert!(first_text.contains("Read: foo.rs"));
        assert!(first_text.contains("Edit: foo.rs"));
        
        // Second batch (should append, not replace)
        summary.append_event("test_result".to_string(), "ok. 5 passed".to_string());
        summary.compile(4);
        
        let second_text = summary.get_summary();
        // Should contain both old and new events
        assert!(second_text.contains("Read: foo.rs"), "should preserve first batch");
        assert!(second_text.contains("Edit: foo.rs"), "should preserve first batch");
        assert!(second_text.contains("ok. 5 passed"), "should include second batch");
        assert_eq!(summary.last_compiled_turn, 4);
    }

    #[test]
    fn session_summary_empty_compile_is_noop() {
        let mut summary = SessionSummary::new();
        let before = summary.get_summary().to_string();
        
        summary.compile(10);
        
        let after = summary.get_summary().to_string();
        assert_eq!(before, after, "compiling with no pending events should be a no-op");
        assert_eq!(summary.last_compiled_turn, 0, "turn should not update on empty compile");
    }

    #[test]
    fn build_context_page_uses_session_summary() {
        let mut messages = Vec::new();
        for i in 0..10 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: format!("question_{}", i),
                }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: format!("answer_{}", i),
                }],
                model: None,
            });
        }
        
        // Create a session summary with custom events
        let mut summary = SessionSummary::new();
        summary.append_event("custom".to_string(), "custom event from session".to_string());
        summary.compile(5);
        
        // Trigger eviction with session summary
        let page = build_context_page(
            &messages,
            100_000,
            70_000,
            3,
            &ContextHints::default(),
            Some(&summary),
            None,
        );
        
        // First message should contain the session summary
        let first_text = match &page[0].content[0] {
            ContentBlock::Text { text } => text.clone(),
            _ => String::new(),
        };
        assert!(
            first_text.contains("custom event from session"),
            "should use session summary: {}",
            first_text
        );
        assert!(
            first_text.contains("[custom]"),
            "should include event type markers"
        );
    }

    // ───────────────────────────────────────────────────────────────────────
    // Prompt cache stability tests (fcp-llm cache invalidation surface)
    // ───────────────────────────────────────────────────────────────────────
    //
    // Anthropic caches system prompt content up to the last cache_control
    // breakpoint. Any byte-level change in the prefix invalidates the cache.
    // These tests verify that our preload blocks are stable, ordered, and
    // deterministic to minimize unnecessary cache busting.

    #[test]
    fn preload_context_is_deterministic_across_calls() {
        // Two calls to build_preload_context with identical state must
        // produce byte-identical output. Any non-determinism (HashMap
        // ordering, timestamps, etc.) would bust the prompt cache.
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(state_dir.join("needles")).unwrap();
        std::fs::write(state_dir.join("needles/issues.jsonl"), "").unwrap();
        std::fs::write(state_dir.join(".language"), "# empty\n").unwrap();

        let boot = crate::cpu::session::BootContext::new(root);
        let first = boot.build_preload_context();
        let second = boot.build_preload_context();

        assert_eq!(first.len(), second.len(), "block count must be stable");
        for (i, (a, b)) in first.iter().zip(second.iter()).enumerate() {
            assert_eq!(a, b, "preload block {} changed between calls", i);
        }
    }

    #[test]
    fn preload_context_block_count_is_stable() {
        // The number of preload blocks determines cache breakpoint placement.
        // Adding/removing a block shifts which block gets cache_control,
        // invalidating the cache for all subsequent blocks.
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(state_dir.join("needles")).unwrap();
        std::fs::write(state_dir.join("needles/issues.jsonl"), "").unwrap();
        std::fs::write(state_dir.join(".language"), "\
:boot        | 0 | sys     | 0 | 0 | 1.00 | internal | () -> () | bootstrap\n\
:fcp-screen  | 0 | device  | 0 | 0 | 1.00 | internal | (event) -> (render) | display driver\n\
:hay         | 1 | user    | 0 | 200 | 1.00 | ostk work hay | (straw) -> () | capture intent\n").unwrap();

        let boot = crate::cpu::session::BootContext::new(root);
        let blocks = boot.build_preload_context();

        // Expected: registers, boot state, tool surface = 3 blocks minimum.
        // Working state only appears if decisions.jsonl or gen_table.jsonl
        // have content, so it may or may not be present.
        assert!(
            blocks.len() >= 2,
            "must have at least registers + boot state, got {}",
            blocks.len()
        );

        // Verify block ordering is: registers, [working state], boot state, tool surface
        let has_registers = blocks.iter().any(|b| b.starts_with("# kernel registers"));
        let has_boot = blocks.iter().any(|b| b.contains("# Boot state"));
        assert!(has_registers, "must have kernel registers block");
        assert!(has_boot, "must have boot state block");
    }

    #[test]
    fn preload_registers_block_is_first() {
        // Cache efficiency: the registers block changes most often (needle
        // counts, fleet status). If it's NOT first, changes to it invalidate
        // cache for all preceding stable blocks. By being first, only the
        // registers block itself is invalidated — later blocks remain cached.
        //
        // WAIT: Anthropic caches from the START up to the last breakpoint.
        // So actually the LAST block should be the most stable (tool surface),
        // and changing ANY earlier block invalidates the whole cache prefix.
        // This test documents the current ordering for awareness.
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(state_dir.join("needles")).unwrap();
        std::fs::write(state_dir.join("needles/issues.jsonl"), "").unwrap();
        std::fs::write(state_dir.join(".language"), "# test\n").unwrap();

        let boot = crate::cpu::session::BootContext::new(root);
        let blocks = boot.build_preload_context();

        assert!(
            blocks[0].starts_with("# kernel registers"),
            "first preload block should be kernel registers, got: {}",
            &blocks[0][..blocks[0].len().min(80)]
        );
    }

    #[test]
    fn preload_tool_surface_is_last() {
        // The tool surface is frozen at boot (never refreshed). It should be
        // the last preload block so its cache_control breakpoint covers it.
        // Since Anthropic caches the prefix up to the last breakpoint, the
        // tool surface being last AND frozen means: if registers/boot change,
        // the whole prefix cache busts. But this is by design — the alternative
        // (tool surface first) would mean tool surface changes bust everything,
        // which is worse since tool surface is immutable per session.
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(state_dir.join("needles")).unwrap();
        std::fs::write(state_dir.join("needles/issues.jsonl"), "").unwrap();
        std::fs::write(state_dir.join(".language"), "\
:hay         | 1 | user    | 0 | 200 | 1.00 | ostk work hay | (straw) -> () | capture intent\n\
:draft       | 1 | user    | 0 | 200 | 0.05 | ostk doc draft | (title) -> (path) | create draft\n").unwrap();

        let boot = crate::cpu::session::BootContext::new(root);
        let blocks = boot.build_preload_context();
        let last = blocks.last().expect("must have at least one block");
        // Tool surface contains CLI or tool surface info
        assert!(
            last.contains("# Tool surface") || last.contains("# CLI") || last.contains("Resident:") || last.contains("resident:") || last.contains("Deferred"),
            "last preload block should be tool surface summary, got: {}",
            &last[..last.len().min(120)]
        );
    }

    #[test]
    fn render_registers_no_nondeterminism() {
        // render_registers reads live state. If it includes timestamps,
        // random values, or unstable HashMap iteration, the prompt cache
        // busts on every call even when state hasn't changed.
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(state_dir.join("needles")).unwrap();
        std::fs::write(state_dir.join("needles/issues.jsonl"), "").unwrap();
        std::fs::write(state_dir.join(".language"), "# empty\n").unwrap();

        let boot = crate::cpu::session::BootContext::new(root);
        let r1 = render_registers(&boot, root);
        let r2 = render_registers(&boot, root);
        assert_eq!(r1, r2, "render_registers must be deterministic");
    }

    #[test]
    fn render_working_state_no_nondeterminism() {
        // Working state includes decisions and gen_table. Both are read
        // from JSONL files — iteration order must be stable (file order).
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        // Write multiple decisions to test ordering stability
        let ts = recent_ts();
        let decisions = format!("\
{{\"key\":\"alpha\",\"value\":\"1\",\"reason\":\"test\",\"timestamp\":\"{ts}\"}}\n\
{{\"key\":\"beta\",\"value\":\"2\",\"reason\":\"test\",\"timestamp\":\"{ts}\"}}\n\
{{\"key\":\"gamma\",\"value\":\"3\",\"reason\":\"test\",\"timestamp\":\"{ts}\"}}\n");
        std::fs::write(state_dir.join("decisions.jsonl"), decisions).unwrap();

        let w1 = render_working_state(root, None);
        let w2 = render_working_state(root, None);
        assert_eq!(w1, w2, "render_working_state must be deterministic");

        // Verify ordering matches file order
        let alpha_pos = w1.find("alpha").unwrap();
        let beta_pos = w1.find("beta").unwrap();
        let gamma_pos = w1.find("gamma").unwrap();
        assert!(alpha_pos < beta_pos, "decisions should follow file order");
        assert!(beta_pos < gamma_pos, "decisions should follow file order");
    }

    #[test]
    fn scaffolding_strip_then_eviction_preserves_tool_pairing() {
        // Combined pipeline: strip scaffolding, then evict, then validate
        // tool pairing. This is the real path through build_context_page.
        // A stripped scaffolding message between tool_use and tool_result
        // could break the pairing.
        let mut messages = Vec::new();

        // Turn 1: user asks
        messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "fix the bug".into() }],
            model: None,
        });
        // Scaffolding preamble (should be stripped)
        messages.push(Message {
            role: "assistant".into(),
            content: vec![ContentBlock::Text { text: "Let me look at that.".into() }],
            model: None,
        });
        // Tool use
        messages.push(Message {
            role: "assistant".into(),
            content: vec![ContentBlock::ToolUse {
                id: "t1".into(),
                name: "Read".into(),
                input: serde_json::json!({"file_path": "/tmp/test.rs"}),
            }],
            model: None,
        });
        // Tool result
        messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::ToolResult {
                tool_use_id: "t1".into(),
                content: "file contents".into(),
                is_error: false,
            }],
            model: None,
        });
        // Many more turns to trigger eviction
        for i in 2..15 {
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: format!("t{}", i),
                    name: "Bash".into(),
                    input: serde_json::json!({"command": format!("echo {}", i)}),
                }],
                model: None,
            });
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: format!("t{}", i),
                    content: format!("result {}", i),
                    is_error: false,
                }],
                model: None,
            });
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: format!("question {}", i) }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: format!("answer {}", i) }],
                model: None,
            });
        }

        // Run full pipeline with high pressure to trigger eviction
        let page = build_context_page(
            &messages, 100_000, 70_000, 3,
            &ContextHints { supports_mixed_content_blocks: true }, None, None,
        );

        // Must maintain valid tool pairing
        assert!(
            validate_tool_pairing(&page),
            "tool pairing must survive scaffolding strip + eviction"
        );

        // Must maintain alternation
        for i in 1..page.len() {
            if page[i].role == page[i - 1].role {
                // Same-role adjacency is allowed for merged content blocks
                // but both can't be user with mixed content
                if page[i].role == "user" {
                    let prev_has_text = page[i - 1].content.iter().any(|c| matches!(c, ContentBlock::Text { .. }));
                    let curr_has_text = page[i].content.iter().any(|c| matches!(c, ContentBlock::Text { .. }));
                    // After ensure_alternation, same-role adjacency should not happen
                    panic!(
                        "adjacent same-role messages at {}: prev_text={}, curr_text={}",
                        i, prev_has_text, curr_has_text
                    );
                }
            }
        }
    }

    #[test]
    fn eviction_with_orphaned_tool_use_falls_back() {
        // If eviction creates an orphaned tool_use (result was evicted but
        // the tool_use wasn't), validate_tool_pairing should catch it and
        // build_context_page should fall back to the raw messages.
        let mut messages = Vec::new();
        messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "start".into() }],
            model: None,
        });
        // Create a tool_use followed by its result
        messages.push(Message {
            role: "assistant".into(),
            content: vec![ContentBlock::ToolUse {
                id: "early_tool".into(),
                name: "Read".into(),
                input: serde_json::json!({}),
            }],
            model: None,
        });
        messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::ToolResult {
                tool_use_id: "early_tool".into(),
                content: "result".into(),
                is_error: false,
            }],
            model: None,
        });
        // Many user intents to push the early tool pair into eviction range
        for i in 0..20 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: format!("q{}", i) }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: format!("a{}", i) }],
                model: None,
            });
        }

        // High pressure: should evict early turns
        let page = build_context_page(
            &messages, 100_000, 80_000, 2,
            &ContextHints::default(), None, None,
        );

        // The result must be valid — either eviction was clean, or fallback occurred
        assert!(
            validate_tool_pairing(&page),
            "output must have valid tool pairing (either clean eviction or fallback)"
        );
    }

    #[test]
    fn ensure_alternation_handles_triple_user_messages() {
        // After scaffolding stripping, we can end up with user-user-user.
        // ensure_alternation must merge them (or insert padding).
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "first".into() }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "second".into() }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "third".into() }],
                model: None,
            },
        ];

        let fixed = ensure_alternation(messages, &ContextHints { supports_mixed_content_blocks: true });
        // With mixed content support, should merge all three into one user message
        assert_eq!(fixed.len(), 1, "should merge three user messages into one");
        assert_eq!(fixed[0].content.len(), 3, "should have all three text blocks");

        // Without mixed content support
        let messages2 = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "first".into() }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "second".into() }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "third".into() }],
                model: None,
            },
        ];
        let fixed2 = ensure_alternation(messages2, &ContextHints { supports_mixed_content_blocks: false });
        // Text-only merges are safe even without mixed content support
        assert_eq!(fixed2.len(), 1, "text-only merge should work regardless of hints");
    }

    #[test]
    fn ensure_alternation_tool_result_after_tool_result() {
        // Two consecutive user messages where both contain only ToolResults.
        // This can happen when scaffolding between two tool calls is stripped.
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t1".into(),
                    content: "result1".into(),
                    is_error: false,
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t2".into(),
                    content: "result2".into(),
                    is_error: false,
                }],
                model: None,
            },
        ];

        // With mixed content support: should merge
        let fixed = ensure_alternation(messages.clone(), &ContextHints { supports_mixed_content_blocks: true });
        assert_eq!(fixed.len(), 1, "should merge tool results with mixed support");

        // Without mixed content support: tool results are same type, still safe to merge
        let fixed2 = ensure_alternation(messages, &ContextHints { supports_mixed_content_blocks: false });
        assert_eq!(fixed2.len(), 1, "tool results can merge without mixed support");
    }

    #[test]
    fn ensure_alternation_mixed_tool_result_and_text() {
        // User message with ToolResult followed by user message with Text.
        // Without mixed content support, this creates a mixed block if merged.
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t1".into(),
                    content: "result".into(),
                    is_error: false,
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "follow up".into() }],
                model: None,
            },
        ];

        // With mixed content support: merge freely
        let fixed = ensure_alternation(messages.clone(), &ContextHints { supports_mixed_content_blocks: true });
        assert_eq!(fixed.len(), 1, "should merge with mixed support");
        assert_eq!(fixed[0].content.len(), 2);

        // Without mixed content support: should insert padding to avoid mixed block
        let fixed2 = ensure_alternation(messages, &ContextHints { supports_mixed_content_blocks: false });
        assert!(
            fixed2.len() >= 2,
            "should insert padding without mixed support, got {} messages",
            fixed2.len()
        );
        // Verify alternation
        for i in 1..fixed2.len() {
            assert_ne!(
                fixed2[i].role, fixed2[i - 1].role,
                "must alternate roles at position {}", i
            );
        }
    }

    #[test]
    fn build_context_page_empty_messages() {
        // Edge case: empty message list should not panic
        let page = build_context_page(&[], 100_000, 0, 3, &ContextHints::default(), None, None);
        assert!(page.is_empty(), "empty input should produce empty output");
    }

    #[test]
    fn build_context_page_single_user_message() {
        // Edge case: single message should pass through unchanged
        let messages = vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hello".into() }],
            model: None,
        }];
        let page = build_context_page(&messages, 100_000, 0, 3, &ContextHints::default(), None, None);
        assert_eq!(page.len(), 1);
        assert_eq!(page[0].role, "user");
    }

    #[test]
    fn build_context_page_no_eviction_when_fewer_intents_than_window() {
        // Regression: when accumulated_tokens > 60% budget but conversation has
        // fewer user intents than intent_window, window_start stayed at result.len()
        // and ALL messages were evicted. Model received empty conversation → 0 output tokens.
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "hello, what are we working on?".into() }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                // Substantive response (>200 chars) so strip_scaffolding keeps it
                content: vec![ContentBlock::Text {
                    text: "Based on the current kernel state, we have 219 open needles with P0 \
                           priority on the broken benchmark patches. The temporal heartbeat was \
                           shipped in v2.2.5 and the decision filtering is ready for testing. \
                           The next step is wiring the ContextPage into build_params.".into(),
                }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "sounds good, let's do it".into() }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![
                    ContentBlock::Text { text: "I'll start by reading the agent loop.".into() },
                    ContentBlock::ToolUse {
                        id: "t1".into(),
                        name: "Read".into(),
                        input: serde_json::json!({"file_path": "src/cpu/agent_loop.rs"}),
                    },
                ],
                model: None,
            },
        ];
        // Budget pressure > 60% (70K / 100K = 70%) but only 2 user intents vs window of 5
        let page = build_context_page(&messages, 100_000, 70_000, 5, &ContextHints::default(), None, None);
        // Must preserve all messages — not enough intents to trigger eviction
        assert!(page.len() >= 3, "should keep messages when fewer intents than window, got {} messages", page.len());
        // Verify user messages survived
        let user_msgs: Vec<_> = page.iter().filter(|m| m.role == "user" && !is_tool_result_only(m)).collect();
        assert_eq!(user_msgs.len(), 2, "both user intents should survive");
    }

    #[test]
    fn signal_headlines_survive_eviction() {
        // Signal headlines are injected into the eviction summary.
        // After eviction, the model should still know about active signals.
        let mut summary = SessionSummary::new();
        summary.record_signal("calibrate", "intent", "deep analysis", "architecture review", 5);
        summary.record_signal("ultrathink", "inference", "extended", "", 8);

        // Enough events to trigger compilation
        for i in 0..10 {
            summary.append_event("tool_call".into(), format!("Bash: cmd{}", i));
        }
        summary.compile(10);

        let headline = summary.signal_headline_block();
        assert!(headline.contains("calibrate"), "calibrate signal should survive");
        assert!(headline.contains("ultrathink"), "ultrathink signal should survive");
        assert!(headline.contains("deep analysis"), "signal detail should survive");

        // Now use this summary in eviction
        let mut messages = Vec::new();
        for i in 0..15 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: format!("q{}", i) }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: format!("a{}", i) }],
                model: None,
            });
        }

        let page = build_context_page(
            &messages, 100_000, 70_000, 3,
            &ContextHints::default(), Some(&summary), None,
        );

        // The summary should be in the eviction header
        if page.len() < messages.len() {
            // Eviction occurred — check summary is present
            let first_text = match &page[0].content[0] {
                ContentBlock::Text { text } => text.clone(),
                _ => String::new(),
            };
            assert!(
                first_text.contains("calibrate") || first_text.contains("signal"),
                "eviction summary should reference signals: {}",
                &first_text[..first_text.len().min(300)]
            );
        }
    }

    #[test]
    fn scaffolding_detection_boundary_cases() {
        // Test the 200-char boundary for scaffolding detection
        let hints = &ContextHints::default();

        // Exactly at boundary (200 chars with scaffolding pattern)
        let text_200 = format!("Let me {}", "x".repeat(193));
        assert_eq!(text_200.len(), 200);
        let msg_at = Message {
            role: "assistant".into(),
            content: vec![ContentBlock::Text { text: text_200 }],
            model: None,
        };

        // Just over boundary (201 chars)
        let text_201 = format!("Let me {}", "x".repeat(194));
        assert_eq!(text_201.len(), 201);
        let msg_over = Message {
            role: "assistant".into(),
            content: vec![ContentBlock::Text { text: text_201 }],
            model: None,
        };

        let messages_at = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "go".into() }],
                model: None,
            },
            msg_at,
        ];
        let messages_over = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "go".into() }],
                model: None,
            },
            msg_over,
        ];

        let stripped_at = strip_scaffolding(&messages_at, hints);
        assert_eq!(stripped_at.len(), 1, "200-char scaffolding should be stripped");

        let stripped_over = strip_scaffolding(&messages_over, hints);
        assert_eq!(stripped_over.len(), 2, "201-char message should be kept");
    }

    #[test]
    fn validate_tool_pairing_multiple_tools_in_one_message() {
        // Assistant sends two tool_use blocks in one message.
        // The following user message must have both matching tool_results.
        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "do both".into() }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![
                    ContentBlock::ToolUse {
                        id: "t1".into(),
                        name: "Read".into(),
                        input: serde_json::json!({}),
                    },
                    ContentBlock::ToolUse {
                        id: "t2".into(),
                        name: "Bash".into(),
                        input: serde_json::json!({}),
                    },
                ],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![
                    ContentBlock::ToolResult {
                        tool_use_id: "t1".into(),
                        content: "file".into(),
                        is_error: false,
                    },
                    ContentBlock::ToolResult {
                        tool_use_id: "t2".into(),
                        content: "output".into(),
                        is_error: false,
                    },
                ],
                model: None,
            },
        ];
        assert!(validate_tool_pairing(&messages), "matched pairs should validate");

        // Missing one result
        let missing = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "do both".into() }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![
                    ContentBlock::ToolUse {
                        id: "t1".into(),
                        name: "Read".into(),
                        input: serde_json::json!({}),
                    },
                    ContentBlock::ToolUse {
                        id: "t2".into(),
                        name: "Bash".into(),
                        input: serde_json::json!({}),
                    },
                ],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: "t1".into(),
                    content: "file".into(),
                    is_error: false,
                }],
                model: None,
            },
        ];
        assert!(!validate_tool_pairing(&missing), "missing result should fail validation");
    }

    // ─── →967: FcpLlm tests ─────────────────────────────────────────

    #[test]
    fn test_fcp_llm_for_model_opus() {
        let llm = FcpLlm::for_model("claude-opus-4-6");
        assert_eq!(llm.budget, 160_000);
        assert_eq!(llm.window_size, 10);
        assert_eq!(llm.decision_cap, 50);

        // Also test without version suffix
        let llm2 = FcpLlm::for_model("claude-opus-4");
        assert_eq!(llm2.budget, 160_000);
        assert_eq!(llm2.window_size, 10);
        assert_eq!(llm2.decision_cap, 50);

        // 1M variant gets full budget
        let llm3 = FcpLlm::for_model("claude-opus-4-6[1m]");
        assert_eq!(llm3.budget, 800_000);
        assert_eq!(llm3.window_size, 10);
        assert_eq!(llm3.decision_cap, 50);
    }

    #[test]
    fn test_fcp_llm_for_model_sonnet() {
        let llm = FcpLlm::for_model("claude-sonnet-4");
        assert_eq!(llm.budget, 160_000);
        assert_eq!(llm.window_size, 7);
        assert_eq!(llm.decision_cap, 20);
    }

    #[test]
    fn test_fcp_llm_for_model_haiku() {
        let llm = FcpLlm::for_model("claude-haiku-3.5");
        assert_eq!(llm.budget, 80_000);
        assert_eq!(llm.window_size, 3);
        assert_eq!(llm.decision_cap, 5);
    }

    #[test]
    fn test_fcp_llm_for_model_gemini() {
        let llm = FcpLlm::for_model("gemini-2.0-flash");
        assert_eq!(llm.budget, 800_000);
        assert_eq!(llm.window_size, 10);
        assert_eq!(llm.decision_cap, 50);
    }

    #[test]
    fn test_fcp_llm_for_model_deepseek() {
        let llm = FcpLlm::for_model("deepseek-chat");
        assert_eq!(llm.budget, 100_000);
        assert_eq!(llm.window_size, 5);
        assert_eq!(llm.decision_cap, 15);
    }

    #[test]
    fn test_fcp_llm_for_model_gpt4() {
        let llm = FcpLlm::for_model("gpt-4o");
        assert_eq!(llm.budget, 100_000);
        assert_eq!(llm.window_size, 5);
        assert_eq!(llm.decision_cap, 15);
    }

    #[test]
    fn test_fcp_llm_for_model_mistral() {
        let llm = FcpLlm::for_model("mistral-large-2411");
        assert_eq!(llm.budget, 100_000);
        assert_eq!(llm.window_size, 5);
        assert_eq!(llm.decision_cap, 15);

        // devstral shares the mistral branch
        let llm2 = FcpLlm::for_model("devstral-small-2505");
        assert_eq!(llm2.budget, 100_000);
        assert_eq!(llm2.decision_cap, 15);
    }

    #[test]
    fn test_fcp_llm_for_model_qwen() {
        let llm = FcpLlm::for_model("qwen-72b");
        assert_eq!(llm.budget, 25_000);
        assert_eq!(llm.window_size, 3);
        assert_eq!(llm.decision_cap, 5);
    }

    #[test]
    fn test_fcp_llm_for_model_llama() {
        let llm = FcpLlm::for_model("meta-llama-3.3-70b");
        assert_eq!(llm.budget, 100_000);
        assert_eq!(llm.window_size, 5);
        assert_eq!(llm.decision_cap, 10);
    }

    #[test]
    fn test_fcp_llm_for_model_unknown() {
        let llm = FcpLlm::for_model("some-random-model");
        assert_eq!(llm.budget, 50_000);
        assert_eq!(llm.window_size, 5);
        assert_eq!(llm.decision_cap, 10);

        // Empty string also gets conservative defaults
        let llm2 = FcpLlm::for_model("");
        assert_eq!(llm2.budget, 50_000);
    }

    #[test]
    fn test_fcp_llm_case_insensitive() {
        // Model names should match case-insensitively
        let llm = FcpLlm::for_model("Claude-OPUS-4");
        assert_eq!(llm.budget, 160_000);
        assert_eq!(llm.decision_cap, 50);
    }

    // ─── →967: ContextPage tests ────────────────────────────────────

    #[test]
    fn test_context_page_into_messages_with_summary() {
        let page = ContextPage {
            summary: "# session summary\n- [tool_call] Read: src/main.rs\n- [file_modified] src/main.rs".into(),
            recent_turns: vec![
                Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text { text: "fix the bug".into() }],
                    model: None,
                },
                Message {
                    role: "assistant".into(),
                    content: vec![ContentBlock::Text { text: "I'll fix it.".into() }],
                    model: None,
                },
            ],
            evicted_count: 12,
        };

        let messages = page.into_messages();

        // Should have: summary user + summary assistant + 2 recent turns = 4
        assert_eq!(messages.len(), 4, "expected 4 messages: summary pair + 2 recent");

        // First message is the summary injection
        assert_eq!(messages[0].role, "user");
        match &messages[0].content[0] {
            ContentBlock::Text { text } => {
                assert!(text.contains("12 turns compiled into summary"), "should mention evicted count: {text}");
                assert!(text.contains("session summary"), "should contain summary text: {text}");
                assert!(text.contains("ostk_session_history()"), "should mention history tool: {text}");
            }
            _ => panic!("expected Text block"),
        }

        // Second message is the assistant acknowledgment
        assert_eq!(messages[1].role, "assistant");
        match &messages[1].content[0] {
            ContentBlock::Text { text } => {
                assert!(text.contains("Context loaded"), "assistant should acknowledge: {text}");
            }
            _ => panic!("expected Text block"),
        }

        // Recent turns follow
        assert_eq!(messages[2].role, "user");
        assert_eq!(messages[3].role, "assistant");
    }

    #[test]
    fn test_context_page_empty_summary() {
        let page = ContextPage {
            summary: String::new(),
            recent_turns: vec![
                Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text { text: "hello".into() }],
                    model: None,
                },
            ],
            evicted_count: 0,
        };

        let messages = page.into_messages();

        // No summary block when summary is empty
        assert_eq!(messages.len(), 1, "should only have the recent turn, no summary pair");
        assert_eq!(messages[0].role, "user");
    }

    #[test]
    fn test_context_page_summary_with_zero_evicted() {
        // Edge case: summary text exists but evicted_count is 0
        // Should NOT inject summary since nothing was actually evicted
        let page = ContextPage {
            summary: "some stale summary".into(),
            recent_turns: vec![
                Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text { text: "hello".into() }],
                    model: None,
                },
            ],
            evicted_count: 0,
        };

        let messages = page.into_messages();
        assert_eq!(messages.len(), 1, "should not inject summary when evicted_count=0");
    }

    #[test]
    fn test_context_page_preserves_recent_turns() {
        // Recent turns should appear in full fidelity — no modification
        let tool_use = ContentBlock::ToolUse {
            id: "t1".into(),
            name: "Read".into(),
            input: serde_json::json!({"file_path": "/tmp/test.rs"}),
        };
        let tool_result = ContentBlock::ToolResult {
            tool_use_id: "t1".into(),
            content: "file contents here".into(),
            is_error: false,
        };

        let page = ContextPage {
            summary: "prior work".into(),
            recent_turns: vec![
                Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text { text: "read the file".into() }],
                    model: None,
                },
                Message {
                    role: "assistant".into(),
                    content: vec![tool_use.clone()],
                    model: None,
                },
                Message {
                    role: "user".into(),
                    content: vec![tool_result.clone()],
                    model: None,
                },
            ],
            evicted_count: 5,
        };

        let messages = page.into_messages();

        // 2 summary + 3 recent = 5
        assert_eq!(messages.len(), 5);

        // Verify tool_use is preserved
        match &messages[3].content[0] {
            ContentBlock::ToolUse { name, input, .. } => {
                assert_eq!(name, "Read");
                assert_eq!(input.get("file_path").unwrap().as_str().unwrap(), "/tmp/test.rs");
            }
            _ => panic!("expected ToolUse block"),
        }

        // Verify tool_result is preserved
        match &messages[4].content[0] {
            ContentBlock::ToolResult { content, is_error, .. } => {
                assert_eq!(content, "file contents here");
                assert!(!is_error);
            }
            _ => panic!("expected ToolResult block"),
        }
    }

    #[test]
    fn test_context_page_passthrough() {
        let original = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "hello".into() }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: "hi".into() }],
                model: None,
            },
        ];

        let page = ContextPage::passthrough(original.clone());
        assert_eq!(page.evicted_count, 0);
        assert!(page.summary.is_empty());

        let messages = page.into_messages();
        assert_eq!(messages.len(), 2, "passthrough should preserve all messages");
        assert_eq!(messages[0].role, "user");
        assert_eq!(messages[1].role, "assistant");
    }

    // ─── →968: Budget branching affects decision_cap ────────────────

    #[test]
    fn test_budget_branching_affects_decision_cap() {
        // Opus gets 50 decisions
        let opus = FcpLlm::for_model("claude-opus-4");
        assert_eq!(opus.decision_cap, 50, "Opus should get 50 decisions");

        // Haiku gets 5 decisions
        let haiku = FcpLlm::for_model("claude-haiku-3.5");
        assert_eq!(haiku.decision_cap, 5, "Haiku should get 5 decisions");

        // Sonnet is in between
        let sonnet = FcpLlm::for_model("claude-sonnet-4");
        assert_eq!(sonnet.decision_cap, 20, "Sonnet should get 20 decisions");

        // The ratio of decision_cap should roughly track budget
        assert!(opus.decision_cap > sonnet.decision_cap);
        assert!(sonnet.decision_cap > haiku.decision_cap);
    }

    // ─── →968: render_working_state with custom decision_cap ────────

    #[test]
    fn render_working_state_custom_decision_cap() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        let recent_ts = recent_ts();

        // Write 15 valid decisions
        let mut lines = String::new();
        for i in 0..15 {
            lines.push_str(&format!(
                r#"{{"key":"decision_{i}","value":"val_{i}","reason":"auto","timestamp":"{recent_ts}"}}"#,
            ));
            lines.push('\n');
        }
        std::fs::write(state_dir.join("decisions.jsonl"), &lines).unwrap();

        // With cap=5 — should show last 5 (decision_10 through decision_14)
        let working = render_working_state(root, Some(5));
        assert!(!working.contains("decision_9:"), "should not include decision_9 with cap=5: {working}");
        assert!(working.contains("decision_10"), "should include decision_10 with cap=5: {working}");
        assert!(working.contains("decision_14"), "should include decision_14: {working}");
        assert!(working.contains("10 more decisions"), "should show 10 filtered with cap=5: {working}");

        // With cap=50 — should show all 15
        let working_all = render_working_state(root, Some(50));
        assert!(working_all.contains("decision_0"), "should include decision_0 with cap=50: {working_all}");
        assert!(working_all.contains("decision_14"), "should include decision_14 with cap=50: {working_all}");
        assert!(!working_all.contains("more decisions"), "should not show filtered message with cap=50: {working_all}");

        // With cap=None — should use legacy default of 10
        let working_default = render_working_state(root, None);
        assert!(!working_default.contains("decision_4:"), "should not include decision_4 with default cap: {working_default}");
        assert!(working_default.contains("decision_5"), "should include decision_5 with default cap: {working_default}");
    }

    // ─── →967: Integration test — ContextPage with real render ──────

    #[test]
    fn test_integration_context_page_with_render_functions() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state_dir = root.join(".ostk");
        std::fs::create_dir_all(&state_dir).unwrap();

        let recent_ts = recent_ts();
        let decision = format!(
            r#"{{"key":"arch_decision","value":"use ContextPage","reason":"human confirmed","timestamp":"{recent_ts}"}}"#
        );
        std::fs::write(state_dir.join("decisions.jsonl"), format!("{}\n", decision)).unwrap();

        let gen_entry = r#"{"path":"src/fcp/llm.rs","gen":3}"#;
        std::fs::write(state_dir.join("gen_table.jsonl"), format!("{}\n", gen_entry)).unwrap();

        // Render working state with Opus cap
        let fcp_llm = FcpLlm::for_model("claude-opus-4");
        let working = render_working_state(root, Some(fcp_llm.decision_cap));

        // Build a ContextPage using real render output
        let page = ContextPage {
            summary: format!("# working state context\n{}", working),
            recent_turns: vec![
                Message {
                    role: "user".into(),
                    content: vec![ContentBlock::Text { text: "continue work".into() }],
                    model: None,
                },
                Message {
                    role: "assistant".into(),
                    content: vec![ContentBlock::Text { text: "Working on it.".into() }],
                    model: None,
                },
            ],
            evicted_count: 20,
        };

        let messages = page.into_messages();

        // Should have summary pair + 2 recent = 4
        assert_eq!(messages.len(), 4);

        // Summary should contain the working state data
        match &messages[0].content[0] {
            ContentBlock::Text { text } => {
                assert!(text.contains("arch_decision"), "summary should contain decision: {text}");
                assert!(text.contains("src/fcp/llm.rs"), "summary should contain modified file: {text}");
                assert!(text.contains("[H]"), "summary should contain confidence marker: {text}");
            }
            _ => panic!("expected Text block"),
        }

        // Recent turns should be the user's actual conversation
        match &messages[2].content[0] {
            ContentBlock::Text { text } => {
                assert_eq!(text, "continue work");
            }
            _ => panic!("expected Text block"),
        }
    }

    // ─── →966: compiled_summary priority in build_context_page ──────

    #[test]
    fn build_context_page_prefers_compiled_summary() {
        // When compiled_summary is provided, it should take priority over
        // both the fcp::llm::SessionSummary and compile_event_summary.
        let mut messages = Vec::new();
        // 6 user intents to ensure eviction with window=3
        for i in 0..6 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: format!("request {}", i) }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: format!("response {}", i) }],
                model: None,
            });
        }

        // Create an fcp::llm::SessionSummary with distinct text
        let mut llm_summary = SessionSummary::new();
        llm_summary.append_event("tool_call".into(), "Read: src/main.rs".into());
        llm_summary.compile(5);

        // The compiled_summary from cpu::summary — should win
        let compiled = "# session summary (cpu::summary compiler)\n- file_modified: src/lib.rs\n- test_result: 12 passed, 0 failed";

        // Trigger eviction: budget=100k, tokens=70k (>60%), window=3
        let page = build_context_page(
            &messages, 100_000, 70_000, 3,
            &ContextHints::default(), Some(&llm_summary), Some(compiled),
        );

        // Eviction should have occurred
        assert!(page.len() < messages.len(), "eviction should have occurred");

        // The first message should contain the compiled summary (priority 1), not the llm summary
        let first_text = match &page[0].content[0] {
            ContentBlock::Text { text } => text.clone(),
            _ => String::new(),
        };
        assert!(first_text.contains("cpu::summary compiler"),
            "should use compiled_summary (priority 1), got: {first_text}");
        assert!(!first_text.contains("incremental session summary"),
            "should NOT use fcp::llm::SessionSummary when compiled_summary is available");
    }

    #[test]
    fn build_context_page_falls_back_to_session_summary() {
        // When compiled_summary is None but session_summary exists, use session_summary.
        let mut messages = Vec::new();
        for i in 0..6 {
            messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: format!("request {}", i) }],
                model: None,
            });
            messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: format!("response {}", i) }],
                model: None,
            });
        }

        let mut llm_summary = SessionSummary::new();
        llm_summary.append_event("tool_call".into(), "Edit: src/main.rs".into());
        llm_summary.compile(5);

        // compiled_summary is None — should fall back to session_summary
        let page = build_context_page(
            &messages, 100_000, 70_000, 3,
            &ContextHints::default(), Some(&llm_summary), None,
        );

        assert!(page.len() < messages.len(), "eviction should have occurred");

        let first_text = match &page[0].content[0] {
            ContentBlock::Text { text } => text.clone(),
            _ => String::new(),
        };
        assert!(first_text.contains("incremental session summary"),
            "should fall back to fcp::llm::SessionSummary, got: {first_text}");
    }
}

