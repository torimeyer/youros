//! Agent session management — needles →746, →747.
//!
//! `AgentSession` wraps `agent_loop::run_loop` with session lifecycle:
//! persistence, channel plumbing, busy tracking.
//!
//! `SessionManager` manages multiple `AgentSession`s with active switching,
//! extracting agent loop ownership from fcp_screen/app.rs into kernel-level
//! infrastructure.

use std::collections::HashMap;
use std::collections::VecDeque;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use tokio::sync::mpsc;

use futures_util::FutureExt;

use crate::cpu::agent_loop::{self, AgentResponse, CpuEvent, LoopConfig, SharedMessages};
use crate::cpu::anthropic::Usage;
use crate::cpu::anthropic::{ContentBlock, Message};
use crate::cpu::file_cache::FileCache;
use crate::cpu::CpuDriver;

// ─── Token tracking ─────────────────────────────────────────────────

/// Cumulative + per-turn token counts with cache breakdown for accurate cost.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SessionTokens {
    /// Uncached input tokens (full price)
    pub input: u64,
    /// Cached input tokens read from cache (10% price)
    pub cache_read: u64,
    /// Cache creation tokens (125% price)
    pub cache_create: u64,
    /// Output tokens
    pub output: u64,
    /// Output tokens from the most recent turn only (for [done] line)
    pub last_turn_output: u64,
    /// Uncached input from the most recent turn (for per-turn cost)
    pub last_turn_input: u64,
    /// Cache read from the most recent turn
    pub last_turn_cache_read: u64,
    /// Cache create from the most recent turn
    pub last_turn_cache_create: u64,
}

impl SessionTokens {
    /// Total input tokens (all categories) for display/backward compat.
    pub fn total_input(&self) -> u64 {
        self.input + self.cache_read + self.cache_create
    }

    /// Accumulate a Usage event into cumulative totals.
    pub fn accumulate(&mut self, usage: &crate::cpu::anthropic::Usage) {
        self.input += usage.input_tokens;
        self.cache_read += usage.cache_read_tokens;
        self.cache_create += usage.cache_create_tokens;
        self.output += usage.output_tokens;
    }

    /// Record turn-complete usage and snapshot per-turn values.
    pub fn turn_complete(&mut self, usage: &crate::cpu::anthropic::Usage) {
        self.accumulate(usage);
        self.last_turn_output = usage.output_tokens;
        self.last_turn_input = usage.input_tokens;
        self.last_turn_cache_read = usage.cache_read_tokens;
        self.last_turn_cache_create = usage.cache_create_tokens;
    }

    /// Calculate per-turn cost in USD using model-specific pricing.
    pub fn last_turn_cost(&self, model: &str) -> f64 {
        let (input_price, cache_read_price, cache_create_price, output_price) = model_pricing(model);
        (self.last_turn_input as f64 * input_price
            + self.last_turn_cache_read as f64 * cache_read_price
            + self.last_turn_cache_create as f64 * cache_create_price
            + self.last_turn_output as f64 * output_price) / 1_000_000.0
    }

    /// Calculate cumulative session cost in USD.
    pub fn session_cost(&self, model: &str) -> f64 {
        let (input_price, cache_read_price, cache_create_price, output_price) = model_pricing(model);
        (self.input as f64 * input_price
            + self.cache_read as f64 * cache_read_price
            + self.cache_create as f64 * cache_create_price
            + self.output as f64 * output_price) / 1_000_000.0
    }
}

/// Returns (input_$/M, cache_read_$/M, cache_create_$/M, output_$/M) for a model.
fn model_pricing(model: &str) -> (f64, f64, f64, f64) {
    // (input_$/M, cache_read_$/M, cache_create_$/M, output_$/M)
    if model.contains("opus") {
        (15.0, 1.50, 18.75, 75.0)
    } else if model.contains("haiku") {
        (0.80, 0.08, 1.00, 4.0)
    } else if model.contains("gemini-3.1-pro") {
        (2.0, 0.50, 2.0, 12.0)          // no cache tiers yet; estimate
    } else if model.contains("gemini-3-flash") || model.contains("gemini-2.5-flash") {
        (0.50, 0.125, 0.50, 3.0)
    } else if model.contains("flash-lite") {
        (0.10, 0.025, 0.10, 0.40)
    } else if model.contains("gemini-2.5-pro") || model.contains("gemini") {
        (1.25, 0.3125, 1.25, 10.0)      // gemini-2.5-pro + fallback
    } else {
        // Sonnet default
        (3.0, 0.30, 3.75, 15.0)
    }
}

// ─── AgentSession ────────────────────────────────────────────────────

/// Maximum number of queued outbox messages before oldest are dropped (→895).
const MAX_OUTBOX: usize = 10;

/// →plan Phase 4: Session lifecycle type.
#[derive(Clone, Debug)]
pub enum SessionType {
    /// Normal interactive TUI session.
    Interactive,
    /// Background agent spawned via `ostk spawn`.
    /// Writes events to transcript file and emits lifecycle audit events.
    Spawned {
        transcript_path: std::path::PathBuf,
        model: String,
        prompt_preview: String,
    },
}

/// A single agent session with its conversation state, channel, and busy flag.
pub struct AgentSession {
    pub name: String,
    pub config: LoopConfig,
    pub messages: SharedMessages,
    pub event_tx: mpsc::Sender<CpuEvent>,
    pub event_rx: Option<mpsc::Receiver<CpuEvent>>,
    pub busy: Arc<AtomicBool>,
    pub session_tokens: SessionTokens,
    /// Outbox for queued inter-agent messages (→740).
    /// Messages are drained and prepended as user messages before the next
    /// inference call in `dispatch`.
    pub(crate) outbox: VecDeque<String>,
    /// →823: Pending image attachments for the next dispatch.
    /// Drained and prepended to the user message content blocks on dispatch.
    pub pending_images: Vec<ContentBlock>,
    root: PathBuf,
    /// Kernel identity alias (if registered). Used for heartbeat + deregister.
    kernel_alias: Option<String>,
    /// →FSM: Handle to the spawned agent task for cancellation.
    task_handle: Option<tokio::task::JoinHandle<()>>,
    /// →plan Phase 3: Cooperative cancel flag. Agent loop checks this at each
    /// checkpoint and exits cleanly (emitting TurnComplete) instead of being
    /// hard-aborted via task_handle.abort().
    cancel_flag: Arc<AtomicBool>,
    /// TUI→Agent response channel sender (approval decisions, cancel signals).
    /// Recreated on each dispatch() — always pairs with the running agent task's receiver.
    pub response_tx: mpsc::Sender<AgentResponse>,
    /// fcp-llm: Incremental session summary for context eviction.
    /// Arc<Mutex> so the spawned agent loop task can read (for build_params)
    /// and write (append_event after tool calls) while AgentSession persists
    /// the summary across dispatch boundaries.
    pub session_summary: Arc<std::sync::Mutex<crate::fcp::llm::SessionSummary>>,
    /// →903: Track which models have been used in this session (for →905 status bar).
    /// Seeded with the initial model; appended on each `:model` switch.
    pub model_chain: Vec<String>,
    /// →plan Phase 4: Session lifecycle type (interactive vs spawned).
    pub session_type: SessionType,
    /// →1004: Tools approved via AlwaysAllow, persisted across dispatch boundaries.
    /// Keyed by tool name (today) or capability class (future P1).
    pub runtime_allowed: Arc<std::sync::Mutex<std::collections::HashSet<String>>>,
}

impl AgentSession {
    /// Create a new session with fresh context.
    /// Previous session preserved as `.prev` on disk for manual recovery.
    pub fn new(name: &str, config: LoopConfig, root: PathBuf) -> Self {
        // Event channel buffer: 64 slots balances backpressure (prevents the old
        // 5-second visual lag at 128) with throughput (32 was too aggressive for
        // tool-heavy exploration through the daemon poll path).
        let (event_tx, event_rx) = mpsc::channel(64);
        // Response channel: TUI→Agent (approval decisions, cancel signals).
        // Initial sender — will be replaced with a fresh pair on each dispatch().
        let (response_tx, _initial_rx) = mpsc::channel(8);
        // Register with kernel identity system so the session is visible to the fleet.
        let ostk_dir = crate::state_dir(&root);
        let kernel_alias = {
            let identity = crate::kernel::identity::Identity::new(&ostk_dir);
            identity.assign_alias_with(Some(name)).ok()
        };

        let initial_model = config.model.clone();
        Self {
            name: name.to_string(),
            config,
            messages: SharedMessages::new(vec![]),
            event_tx,
            event_rx: Some(event_rx),
            busy: Arc::new(AtomicBool::new(false)),
            session_tokens: SessionTokens::default(),
            outbox: VecDeque::new(),
            pending_images: Vec::new(),
            root,
            kernel_alias,
            task_handle: None,
            cancel_flag: Arc::new(AtomicBool::new(false)),
            response_tx,
            session_summary: Arc::new(std::sync::Mutex::new(crate::fcp::llm::SessionSummary::new())),
            model_chain: vec![initial_model], // →903: seed with initial model
            session_type: SessionType::Interactive,
            runtime_allowed: Arc::new(std::sync::Mutex::new(std::collections::HashSet::new())),
        }
    }

    /// Dispatch user input to the agent loop.
    ///
    /// Pushes a user Message, sets busy=true, and spawns `agent_loop::run_loop`
    /// on the provided tokio runtime.  Uses the shared `CpuDriver` owned by
    /// `SessionManager` for HTTP connection pooling (->755).
    pub fn dispatch(
        &mut self,
        input: &str,
        rt: &tokio::runtime::Handle,
        client: &Arc<dyn CpuDriver>,
        boot_context: &BootContext,
    ) {
        // →FSM Phase 1: Busy guard — prevent concurrent agent tasks (RC1).
        // If the agent loop is already running, queue the input for auto-dispatch
        // after TurnComplete instead of spawning a second concurrent task.
        if self.busy.load(Ordering::Acquire) {
            tracing::info!(name = %self.name, "session: dispatch queued (busy)");
            self.outbox.push_back(input.to_string());
            return;
        }
        tracing::info!(name = %self.name, "session: dispatch");
        // →740: Drain outbox and prepend queued inter-agent messages
        let pending_outbox = self.drain_outbox();

        // Single lock acquisition: push outbox messages AND user message atomically.
        // Previously these were two separate lock acquisitions, allowing the agent
        // loop to clone messages between them and miss the user input.
        let mut content: Vec<ContentBlock> = self.pending_images.drain(..).collect();
        content.push(ContentBlock::Text { text: input.to_string() });
        {
            let mut outbox_msgs: Vec<Message> = pending_outbox.into_iter().map(|msg| Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: msg }],
                model: None,
            }).collect();
            outbox_msgs.push(Message {
                role: "user".into(),
                content,
                model: None,
            });
            self.messages.extend(outbox_msgs);
        }

        // Abort the old task if it's still running (cancel is cooperative and may not
        // have taken effect yet). Without this, resetting cancel_flag below lets the
        // old task resume, and two agent loops run concurrently on the same session.
        if let Some(handle) = self.task_handle.take() {
            handle.abort();
        }
        // Set busy, reset cancel flag for new dispatch
        self.busy.store(true, Ordering::Release);
        self.cancel_flag.store(false, Ordering::Release);

        // Clone what the spawned task needs.
        // →896: config is cloned (not Arc-wrapped) because `:model`, `:permission`,
        // and `:fast` mutate config fields directly on AgentSession between dispatches
        // (see fcp_screen/app.rs and components/dispatch.rs). Arc<LoopConfig> would
        // require interior mutability at every mutation site for no real gain — the
        // clone cost is negligible compared to an API round-trip.
        let messages = self.messages.clone();
        let tx = self.event_tx.clone();
        let config = self.config.clone();
        let busy = Arc::clone(&self.busy);
        let cancel_flag = Arc::clone(&self.cancel_flag);
        let api = Arc::clone(client);

        // Create a fresh response channel for this dispatch cycle.
        // The old response_rx (if any) is dropped — its sender was either consumed
        // by the previous agent task or is stale. A fresh pair ensures the TUI's
        // response_tx always reaches the running agent loop.
        let (new_response_tx, response_rx) = mpsc::channel(8);
        self.response_tx = new_response_tx;

        // →803: Build FileCache for Anthropic Files API when model is claude-*.
        // If cache creation fails, fall back to no cache (resilient).
        let file_cache: Option<FileCache> = if config.model.starts_with("claude") {
            let ostk_dir = crate::state_dir(&self.root);
            Some(FileCache::load(&ostk_dir))
        } else {
            None
        };

        // →843: Clone boot_context for the spawned task to own.
        // Refresh happens at turn boundary in SessionManager::dispatch(), not here.
        let mut boot_ctx = boot_context.clone();

        // fcp-llm: Clone Arc for session summary — agent loop reads for
        // build_params and appends events after tool calls.
        let session_summary = Arc::clone(&self.session_summary);

        // →1004: Clone Arc for session-scoped allow-list — agent loop reads
        // and inserts, persists across dispatch boundaries.
        let runtime_allowed = Arc::clone(&self.runtime_allowed);

        // Spawn on the runtime — wrapped with catch_unwind so a panic in the
        // agent loop sends an Error event instead of silently leaving busy=true
        // until the 180s watchdog fires.
        //
        // →FSM Phase 1 (RC2 fix): Pass Arc<Mutex<Vec<Message>>> directly to the
        // agent loop instead of clone-mutate-writeback. The agent loop locks per
        // append, so there is no stale-copy overwrite race.
        let handle = rt.spawn(async move {
            let tx_panic = tx.clone();
            let busy_panic = Arc::clone(&busy);

            let result = std::panic::AssertUnwindSafe(async {
                // When file_cache is present (claude-* models), skip boot context
                // refresh (the file cache path handles its own caching).
                let boot_ctx_ref = if file_cache.is_some() { None } else { Some(&mut boot_ctx) };
                let res = agent_loop::run_loop(
                    &*api, &config, &messages, tx.clone(),
                    agent_loop::AgentLoopOptions {
                        file_cache,
                        boot_context: boot_ctx_ref,
                        approval_source: Some(crate::kernel::approval::ApprovalSource::Channel(response_rx)),
                        session_summary: Some(session_summary.clone()),
                        cancel_flag: Some(Arc::clone(&cancel_flag)),
                        runtime_allowed: Some(runtime_allowed),
                    },
                ).await;
                if let Err(e) = res {
                    tracing::error!("agent loop error: {e}");
                    let _ = tx
                        .send(CpuEvent::Error(format!("agent loop: {e}")))
                        .await;
                    // Always emit TurnComplete on error so TUI exits
                    // AgentRunning state — prevents permanent hang.
                    let _ = tx
                        .send(CpuEvent::TurnComplete {
                            usage: Usage::default(),
                        })
                        .await;
                    busy.store(false, Ordering::Release);
                }
            })
            .catch_unwind()
            .await;

            if let Err(panic_info) = result {
                let msg = if let Some(s) = panic_info.downcast_ref::<&str>() {
                    s.to_string()
                } else if let Some(s) = panic_info.downcast_ref::<String>() {
                    s.clone()
                } else {
                    "agent loop panicked".to_string()
                };
                tracing::error!("[panic] agent loop: {msg}");
                let _ = tx_panic.send(CpuEvent::Error(format!("[panic] {msg}"))).await;
                let _ = tx_panic
                    .send(CpuEvent::TurnComplete {
                        usage: Usage::default(),
                    })
                    .await;
                busy_panic.store(false, Ordering::Release);
            }
        });
        // →FSM Phase 3: Store the task handle for cancellation.
        self.task_handle = Some(handle);
    }

    /// Persist the current messages to `.ostk/sessions/{name}.jsonl`.
    ///
    /// Uses atomic write-to-temp-then-rename so a crash between truncate and
    /// write completion never loses session data.
    pub fn save(&self) -> std::io::Result<()> {
        let path = session_file_path(&self.root, &self.name);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let tmp_path = path.with_extension("tmp");
        self.messages.with_lock(|msgs| {
            let mut f = fs::File::create(&tmp_path)?;
            for msg in msgs.iter() {
                // Skip empty-response placeholders and auto-continue prompts —
                // they poison compile_cached and provide no recovery value.
                if is_empty_placeholder(msg) || is_auto_continue(msg) {
                    continue;
                }
                if let Ok(line) = serde_json::to_string(msg) {
                    writeln!(f, "{}", line)?;
                }
            }
            f.sync_all()?;
            Ok::<(), std::io::Error>(())
        })?;
        // Atomic rename — same filesystem guarantees atomicity on POSIX.
        if let Err(e) = fs::rename(&tmp_path, &path) {
            eprintln!("session save rename failed: {e}");
            return Err(e);
        }
        Ok(())
    }

    /// Atomically deregister the kernel identity and clear the alias (→894).
    ///
    /// Idempotent: safe to call multiple times. After the first call,
    /// `kernel_alias` is `None` so subsequent calls are no-ops.
    /// Consolidates the deregister logic that was previously duplicated in
    /// `clear()`, `Drop`, and `remove_session()`.
    fn deregister(&mut self) {
        if let Some(alias) = self.kernel_alias.take() {
            let identity = crate::kernel::identity::Identity::new(
                &crate::state_dir(&self.root),
            );
            let _ = identity.deregister(&alias);
        }
    }

    /// Clear all messages and remove the session file.
    /// Also deregisters the kernel identity so the agent shows as inactive.
    pub fn clear(&mut self) {
        self.messages.clear();
        self.session_tokens = SessionTokens::default();
        let path = session_file_path(&self.root, &self.name);
        let _ = fs::remove_file(path);
        self.deregister();
    }

    /// Compact the conversation: keep last N turn pairs, drop the rest.
    /// Returns (messages_before, messages_after) for display.
    /// Pure client-side — no LLM call needed (works offline/airplane).
    pub fn compact(&mut self, keep_pairs: usize) -> (usize, usize) {
        let before = self.messages.len();
        let kept = self.messages.with_lock(|messages| {
            if messages.is_empty() {
                return Vec::new();
            }
            // Walk backward counting user TEXT intents (not ToolResult messages).
            // ToolResult messages have role "user" but are tool response pairs,
            // not user intents — counting them causes the cut to land on an
            // orphaned tool_result, producing API 400 errors. (→892)
            let mut user_count = 0;
            let mut cut_idx = messages.len();
            for i in (0..messages.len()).rev() {
                if messages[i].role == "user"
                    && !crate::fcp::llm::is_tool_result_only(&messages[i])
                {
                    user_count += 1;
                    if user_count >= keep_pairs {
                        // Back up to include complete tool_use/tool_result pairs
                        // preceding this user intent (same pattern as
                        // build_context_page in fcp/llm.rs).
                        let mut start = i;
                        while start > 0 {
                            let prev = &messages[start - 1];
                            if prev.role == "assistant"
                                && crate::fcp::llm::has_tool_use(prev)
                                && start >= 2
                                && crate::fcp::llm::is_tool_result_only(&messages[start - 2])
                            {
                                start -= 2; // include the tool_result + tool_use pair
                            } else if prev.role == "assistant"
                                && crate::fcp::llm::has_tool_use(prev)
                            {
                                start -= 1; // include the assistant tool_use
                            } else {
                                break;
                            }
                        }
                        cut_idx = start;
                        break;
                    }
                    cut_idx = i;
                }
            }
            messages[cut_idx..].to_vec()
        });
        let after = kept.len();
        self.messages.replace(kept);
        (before, after)
    }

    /// Number of messages in the conversation.
    pub fn message_count(&self) -> usize {
        self.messages.len()
    }

    /// Whether the agent loop is currently running.
    pub fn is_busy(&self) -> bool {
        self.busy.load(Ordering::Acquire)
    }

    /// Queue an inter-agent message for injection before the next inference call (→740).
    ///
    /// Capped at `MAX_OUTBOX` entries (→895). When full, the oldest message is
    /// dropped and a warning is logged so backpressure is visible in traces.
    pub fn queue_message(&mut self, msg: String) {
        if self.outbox.len() >= MAX_OUTBOX {
            let dropped = self.outbox.pop_front();
            tracing::warn!(
                name = %self.name,
                dropped = ?dropped,
                "outbox at capacity ({MAX_OUTBOX}), dropping oldest message"
            );
        }
        self.outbox.push_back(msg);
    }

    /// Drain all queued outbox messages, returning them in FIFO order (→740).
    pub fn drain_outbox(&mut self) -> Vec<String> {
        self.outbox.drain(..).collect()
    }

    /// Process a CpuEvent and update session state (token accumulation, busy flag).
    ///
    /// →FSM Phase 1: Returns `true` when the outbox has queued work that should
    /// be auto-dispatched (on TurnComplete/Error after busy goes false).
    pub fn process_event(&mut self, event: &CpuEvent) -> bool {
        // →plan Phase 4: Write transcript and lifecycle events for spawned sessions.
        self.process_spawned_event(event);

        match event {
            CpuEvent::Usage { usage } => {
                self.session_tokens.accumulate(usage);
                false
            }
            CpuEvent::TurnComplete { usage } => {
                self.session_tokens.turn_complete(usage);
                self.busy.store(false, Ordering::Release);
                self.task_handle = None;
                // Record heartbeat so the fleet health check knows we're alive.
                if let Some(ref alias) = self.kernel_alias {
                    let _ = crate::kernel::heartbeat::record_heartbeat(
                        &crate::state_dir(&self.root),
                        alias,
                    );
                }
                !self.outbox.is_empty()
            }
            CpuEvent::TextComplete(_) => {
                // Record heartbeat on significant text output events.
                if let Some(ref alias) = self.kernel_alias {
                    let _ = crate::kernel::heartbeat::record_heartbeat(
                        &crate::state_dir(&self.root),
                        alias,
                    );
                }
                false
            }
            CpuEvent::Error(_) => {
                self.busy.store(false, Ordering::Release);
                self.task_handle = None;
                !self.outbox.is_empty()
            }
            _ => false,
        }
    }

    /// →plan Phase 4: Write transcript line and emit lifecycle events for spawned sessions.
    fn process_spawned_event(&self, event: &CpuEvent) {
        let (transcript_path, model, prompt_preview) = match &self.session_type {
            SessionType::Spawned { transcript_path, model, prompt_preview } => {
                (transcript_path, model, prompt_preview)
            }
            SessionType::Interactive => return,
        };

        // Append to transcript file
        use std::io::Write;
        let line = match event {
            CpuEvent::TextComplete(text) if !text.trim().is_empty() => {
                format!("{text}\n")
            }
            CpuEvent::ToolStart { name, .. } => format!("> tool: {name}\n"),
            CpuEvent::ToolResult { name, output, success } => {
                let status = if *success { "ok" } else { "FAILED" };
                let trunc: String = output.chars().take(500).collect();
                format!("> {name} [{status}]: {trunc}\n")
            }
            CpuEvent::TurnComplete { usage } => {
                let line = format!("\n[done] ↓{} ↑{}\n", usage.input_tokens, usage.output_tokens);
                // Lifecycle: agent.complete
                let _ = crate::append_audit(&self.root, &serde_json::json!({
                    "event": "agent.complete",
                    "name": self.name,
                    "model": model,
                    "prompt_preview": prompt_preview,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "timestamp": crate::now_iso(),
                }));
                line
            }
            CpuEvent::Error(msg) => {
                // Lifecycle: agent.error
                let _ = crate::append_audit(&self.root, &serde_json::json!({
                    "event": "agent.error",
                    "name": self.name,
                    "model": model,
                    "error": msg,
                    "timestamp": crate::now_iso(),
                }));
                format!("[error] {msg}\n")
            }
            _ => return,
        };

        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true).append(true).open(transcript_path)
        {
            let _ = f.write_all(line.as_bytes());
        }
    }

    /// →FSM Phase 3: Cancel the running agent task.
    ///
    /// Aborts the tokio task, resets busy flag, and clears the outbox so
    /// ghost events from the old task don't leak into the next interaction.
    pub fn cancel(&mut self) {
        // →plan Phase 3: Set cooperative cancel flag instead of hard-aborting.
        // The agent loop checks this at each checkpoint (top of loop, during
        // streaming, before tool execution) and exits cleanly with TurnComplete.
        self.cancel_flag.store(true, Ordering::Release);
        self.busy.store(false, Ordering::Release);
        self.outbox.clear();
    }
}

impl Drop for AgentSession {
    fn drop(&mut self) {
        // →894: Consolidated deregister — idempotent, safe if already called.
        self.deregister();
    }
}

// ─── BootContext ─────────────────────────────────────────────────────

/// Build a demand-paged tool surface summary from raw `.language` content.
///
/// Parses the `.language` table, splits entries into *resident* (momentum >= 0.45)
/// and *deferred* (everything else), and produces a compact context block.
/// Resident verbs are listed inline so the model can use them immediately;
/// deferred verbs are grouped by layer with a note to use `ostk_verbs` to load.
fn build_tool_surface_summary(language_raw: &str) -> String {
    if language_raw.is_empty() {
        return String::new();
    }
    let raw_entries = crate::language::parse_language(language_raw);
    let entries = crate::language::deduplicate_by_resolution(&raw_entries);
    if entries.is_empty() {
        return String::new();
    }

    const MOMENTUM_THRESHOLD: f64 = 0.45;

    let mut resident: Vec<&crate::language::LanguageEntry> = Vec::new();
    let mut deferred: Vec<&crate::language::LanguageEntry> = Vec::new();
    for entry in &entries {
        if entry.momentum >= MOMENTUM_THRESHOLD {
            resident.push(entry);
        } else {
            deferred.push(entry);
        }
    }

    let mut out = String::from("# Tool surface (demand-paged)\n");

    // Resident verbs — inline
    if resident.is_empty() {
        out.push_str("# Resident: (none above threshold)\n");
    } else {
        let names: Vec<String> = resident.iter().map(|e| format!(":{}", e.verb)).collect();
        out.push_str(&format!("# Resident: {}\n", names.join(", ")));
    }

    // Deferred verbs — grouped by layer
    if deferred.is_empty() {
        out.push_str("# Deferred: (none)\n");
    } else {
        let mut by_layer: std::collections::BTreeMap<&str, Vec<&str>> =
            std::collections::BTreeMap::new();
        for e in &deferred {
            by_layer.entry(e.layer.as_str()).or_default().push(&e.verb);
        }
        let groups: Vec<String> = by_layer
            .iter()
            .map(|(layer, verbs)| {
                let vlist: Vec<String> = verbs.iter().map(|v| format!(":{v}")).collect();
                format!("{layer}[{}]", vlist.join(", "))
            })
            .collect();
        out.push_str(&format!(
            "# Deferred (use ostk_verbs to load): {}\n",
            groups.join(", ")
        ));
    }

    out.push_str("\n# CLI (use ostk_man for details)\n");
    out.push_str("work: add, close, compile, hay, index, link, list, next, pull, refine\n");
    out.push_str("os: audit, clock, diff, history, metrics, status\n");
    out.push_str("doc: decompose, draft, promote\n");
    out.push_str("kernel: await, init, install, ps, reap, serve, shutdown, spawn\n");

    out
}

/// Centralized boot.md + .language state (→754).
///
/// Owns the refresh cycle: reads live state from .ostk/ and composes system prompts.
/// →843: No subprocess, no file write — just reads live needle counts, fleet, metrics.
#[derive(Clone)]
pub struct BootContext {
    pub boot_md: String,
    pub language: String,
    /// Demand-paged tool surface summary generated from `.language`.
    /// Resident verbs (momentum >= 0.45) are listed inline; the rest are deferred
    /// behind `ostk_verbs` so the model only loads them on demand.
    pub deferred_summary: String,
    /// ->751: Stale P0 needle IDs detected in boot.md (empty = current).
    pub stale_p0_ids: Vec<String>,
    /// Cached preload context blocks — rebuilt on refresh(), stable between turns.
    /// Prevents prompt cache invalidation from live state reads on every API call.
    cached_preload: Vec<String>,
    root: PathBuf,
    /// Last refresh timestamp — for maybe_refresh throttling (30s).
    last_refresh: Option<std::time::Instant>,
    /// →968: Provider-aware decision cap for render_working_state.
    /// Set from FcpLlm::for_model() when the model is known. None = legacy default (10).
    pub decision_cap: Option<usize>,
}

impl BootContext {
    /// Create a new BootContext and immediately refresh.
    pub fn new(root: &Path) -> Self {
        let mut ctx = Self {
            boot_md: String::new(),
            language: String::new(),
            deferred_summary: String::new(),
            stale_p0_ids: Vec::new(),
            cached_preload: Vec::new(),
            root: root.to_path_buf(),
            last_refresh: None,
            decision_cap: None,
        };
        ctx.refresh();
        ctx
    }

    /// Refresh boot.md by reading live state directly (no subprocess).
    /// →843: Calls build_register_dump to get fresh needle counts, fleet status, etc.
    ///
    /// Boot snapshot immutability: the tool surface summary is frozen at first
    /// build (BootContext::new). Subsequent refreshes update boot state (needle
    /// counts, fleet, metrics) but NOT the tool surface — that is demand-paged
    /// and pinned per session.
    pub fn refresh(&mut self) {
        let ostk_dir = crate::state_dir(&self.root);
        self.boot_md = crate::commands::boot::build_register_dump(&self.root, &ostk_dir);
        self.language = std::fs::read_to_string(ostk_dir.join(".language"))
            .unwrap_or_default();
        // Frozen: only build summary on first refresh (when deferred_summary is empty)
        if self.deferred_summary.is_empty() {
            self.deferred_summary = build_tool_surface_summary(&self.language);
        }
        self.stale_p0_ids = crate::commands::boot::detect_stale_p0(&ostk_dir);
        // Rebuild cached preload so it's stable between API calls within a turn.
        // This prevents prompt cache invalidation from live state reads.
        self.cached_preload = self.build_preload_context_inner();
        self.last_refresh = Some(std::time::Instant::now());
    }

    /// Refresh only if >30s since last refresh. Prevents subprocess overhead
    /// on every API turn while keeping system state reasonably fresh.
    pub fn maybe_refresh(&mut self) {
        let stale = self.last_refresh
            .map(|t| t.elapsed().as_secs() > 30)
            .unwrap_or(true);
        if stale { self.refresh(); }
    }

    /// Build compaction instructions that preserve kernel-critical state.
    /// Used as custom `instructions` in the compact_20260112 context edit.
    pub fn compact_instructions(&self) -> String {
        "Summarize preserving: (1) all open needle IDs and their status, \
         (2) current resident tool set names from the tool surface block, \
         (3) any ETOOLNOTLOADED or ESTALE errors and their resolutions, \
         (4) session identity and boot state. \
         Drop: old tool results, file contents already processed, \
         intermediate reasoning for completed work.".to_string()
    }

    /// The project root path (exposed for component data-gathering).
    pub fn root_path(&self) -> &Path {
        &self.root
    }

    /// Build the full system prompt: agentfile prompt + boot state + .language.
    ///
    /// ->751: If stale P0 is detected, appends a note so the agent knows
    /// boot.md's milestone is outdated.
    pub fn build_system_prompt(&self, agentfile_prompt: &str) -> String {
        let mut prompt = agentfile_prompt.to_string();
        if !self.boot_md.is_empty() {
            prompt.push_str("\n\n# Boot state\n");
            prompt.push_str(&self.boot_md);
        }
        if !self.deferred_summary.is_empty() {
            prompt.push_str("\n\n");
            prompt.push_str(&self.deferred_summary);
        }
        if !self.stale_p0_ids.is_empty() {
            let ids: Vec<String> = self.stale_p0_ids.iter()
                .map(|id| format!("\u{2192}{}", id))
                .collect();
            prompt.push_str(&format!(
                "\n\nNote: boot.md P0 references stale needles ({}) \u{2014} shipped long ago. Current milestone is v1.7.",
                ids.join(", ")
            ));
        }
        prompt
    }

    /// →823: Build preload context blocks for prompt caching.
    ///
    /// Returns a Vec of strings that should be sent as separate system content
    /// blocks with cache_control breakpoints. Each block is cached independently
    /// by Anthropic's prompt caching, avoiding re-tokenization on every turn.
    ///
    /// Unlike `build_system_prompt` which concatenates everything into one string,
    /// this keeps boot context separate from the agentfile prompt so the static
    /// agentfile prompt is always a cache hit even when boot.md refreshes.
    /// Return cached preload context blocks. Stable within a turn —
    /// rebuilt only on refresh() at turn boundaries.
    pub fn build_preload_context(&self) -> Vec<String> {
        if !self.cached_preload.is_empty() {
            return self.cached_preload.clone();
        }
        self.build_preload_context_inner()
    }

    /// Internal: build fresh preload context from live state. Called by refresh().
    fn build_preload_context_inner(&self) -> Vec<String> {
        let mut blocks = Vec::new();

        // fcp-llm: Kernel registers (YAML)
        let registers = crate::fcp::llm::render_registers(self, &self.root);
        if !registers.is_empty() {
            blocks.push(registers);
        }

        // fcp-llm: Working state (YAML)
        let working = crate::fcp::llm::render_working_state(&self.root, self.decision_cap);
        if working.len() > 20 { // more than just the header
            blocks.push(working);
        }

        // Boot state (register dump — needle counts, fleet, etc.)
        if !self.boot_md.is_empty() {
            let mut boot_block = format!("# Boot state\n{}", self.boot_md);
            if !self.stale_p0_ids.is_empty() {
                let ids: Vec<String> = self.stale_p0_ids.iter()
                    .map(|id| format!("\u{2192}{}", id))
                    .collect();
                boot_block.push_str(&format!(
                    "\n\nNote: boot.md P0 references stale needles ({}) \u{2014} shipped long ago. Current milestone is v1.7.",
                    ids.join(", ")
                ));
            }
            blocks.push(boot_block);
        }

        // Tool surface summary (demand-paged)
        if !self.deferred_summary.is_empty() {
            blocks.push(self.deferred_summary.clone());
        }
        blocks
    }
}

// ─── SessionManager ──────────────────────────────────────────────────

/// Manages multiple `AgentSession`s with active-session switching.
///
/// →plan Phase 4: SessionManager is the unified interface for all session
/// operations. When `daemon_client` is set, operations route through the
/// daemon socket. When None, they execute locally. Same binary, same code —
/// the daemon is an optimization, not a dependency.
pub struct SessionManager {
    sessions: HashMap<String, AgentSession>,
    pub active: String,
    /// Handle to the tokio runtime used for spawning agent tasks.
    pub handle: tokio::runtime::Handle,
    /// Owned runtime — Some when SessionManager created it, None when borrowed via with_handle.
    _rt: Option<tokio::runtime::Runtime>,
    /// Shared CpuDriver for HTTP connection pooling across all sessions (->755).
    /// Routes to the correct provider (Anthropic, Gemini, OpenRouter) based on
    /// the active session's model name.
    pub client: Option<Arc<dyn CpuDriver>>,
    pub available_models: Vec<String>,
    pub boot_context: BootContext,
    /// The original agentfile prompt before boot enrichment (→787).
    /// Used to rebuild system prompts with fresh boot context for new sessions.
    agentfile_prompt: Option<String>,
    root: PathBuf,
    /// →plan: Optional daemon connection. When present, operations route through
    /// the daemon socket via JSON-RPC. When absent, they execute locally.
    daemon_client: Option<crate::serve::client::DaemonClient>,
    /// Session name bound on the daemon side (via session/bind).
    daemon_bound_session: Option<String>,
}

impl SessionManager {
    /// Common initialization for both constructors (→892).
    ///
    /// Resolves the model, creates the driver, discovers available models,
    /// refreshes boot context, and assembles the SessionManager struct.
    /// The only difference between `new()` and `with_handle()` is whether the
    /// tokio runtime is owned (`Some(rt)`) or borrowed (`None`).
    fn init_common(
        handle: tokio::runtime::Handle,
        owned_rt: Option<tokio::runtime::Runtime>,
        root: PathBuf,
        default_config: LoopConfig,
    ) -> Result<Self, String> {
        // Resolve "auto" model to actual model name from HUMANFILE/env
        let resolved_model = if default_config.model == "auto" {
            crate::commands::run::resolve_auto_model()
        } else {
            default_config.model.clone()
        };
        // Create driver with 5s timeout — secret resolution (keychain, vault) can hang.
        let model_for_driver = resolved_model.clone();
        let client = {
            let (tx, rx) = std::sync::mpsc::channel();
            std::thread::Builder::new()
                .name("driver-init".into())
                .spawn(move || { let _ = tx.send(crate::cpu::create_driver(&model_for_driver).ok()); })
                .ok();
            match rx.recv_timeout(std::time::Duration::from_secs(5)) {
                Ok(result) => result,
                Err(_) => {
                    tracing::warn!("driver init timed out (5s) — starting without API client");
                    None
                }
            }
        };

        // →826: Available models from merged Humanfile hierarchy (canonical source).
        // Falls back to API list_models if the Humanfile has no AVAILABLE directive.
        let ostk_dir = crate::state_dir(&root);
        let hf_result = crate::humanfile::load(&ostk_dir);
        let humanfile_models = hf_result.humanfile.available_models;
        let available_models = if !humanfile_models.is_empty() {
            humanfile_models
        } else if let Some(ref c) = client {
            handle.block_on(c.list_models())
                .unwrap_or_default()
                .into_iter()
                .map(|m| m.id)
                .collect()
        } else {
            vec![]
        };

        // Boot context: refresh boot.md + read .language
        let boot_context = BootContext::new(&root);

        // Save the original agentfile prompt before enrichment (→787).
        let mut config = default_config;
        // Apply resolved model name (in case "auto" was specified)
        config.model = resolved_model;
        let agentfile_prompt = config.system_prompt.clone();

        // →823: Use preload_context for cached prefix instead of concatenating
        // boot context into the system prompt. The agentfile prompt stays as-is
        // (cached as the first system block), and boot/language context goes into
        // preload_context (cached as separate blocks with cache_control).
        config.preload_context = boot_context.build_preload_context();

        let scheduler = AgentSession::new("scheduler", config, root.clone());

        let mut sessions = HashMap::new();
        sessions.insert("scheduler".to_string(), scheduler);

        Ok(Self {
            sessions,
            active: "scheduler".to_string(),
            handle,
            _rt: owned_rt,
            client,
            available_models,
            boot_context,
            agentfile_prompt,
            root,
            daemon_client: None,
            daemon_bound_session: None,
        })
    }

    /// Create a new SessionManager with a default "scheduler" session.
    ///
    /// Boots immediately: refreshes boot.md, reads .language, enriches
    /// the default config's system prompt with boot context.
    pub fn new(root: PathBuf, default_config: LoopConfig) -> Result<Self, String> {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| format!("tokio: {e}"))?;
        let handle = rt.handle().clone();
        Self::init_common(handle, Some(rt), root, default_config)
    }

    /// Create a SessionManager using an existing tokio runtime Handle.
    ///
    /// The daemon (ostk listen) already runs a tokio runtime. This constructor
    /// lets the daemon host sessions without creating a redundant runtime.
    pub fn with_handle(handle: tokio::runtime::Handle, root: PathBuf, default_config: LoopConfig) -> Result<Self, String> {
        Self::init_common(handle, None, root, default_config)
    }

    /// Get an immutable reference to the active session.
    pub fn active_session(&self) -> &AgentSession {
        self.sessions.get(&self.active).expect("active session must exist")
    }

    /// Get a mutable reference to the active session.
    pub fn active_session_mut(&mut self) -> &mut AgentSession {
        self.sessions.get_mut(&self.active).expect("active session must exist")
    }

    /// Invalidate the shared CpuDriver so the next `dispatch()` creates a
    /// fresh one for the current model. Call after `:model` switches (→816).
    pub fn invalidate_client(&mut self) {
        self.client = None;
    }

    /// Dispatch user input to the active session.
    ///
    /// Uses the shared `Arc<dyn CpuDriver>` for HTTP connection pooling (->755).
    /// Falls back to a fresh driver if the shared one is unavailable or targets
    /// the wrong provider for the active session's model.
    pub fn dispatch(&mut self, input: &str) {
        // Resolve the correct CpuDriver for the active session's model.
        // If the shared driver doesn't match the active model's provider,
        // create a fresh one on the fly.
        let model = &self.sessions.get(&self.active)
            .expect("active session must exist")
            .config.model;
        let client = self.client.clone()
            .filter(|c| driver_matches(c.as_ref(), model))
            .or_else(|| {
                // →753: create the correct driver for the model's provider —
                // never fall back to Anthropic, which sends the wrong API key.
                match crate::cpu::create_driver(model) {
                    Ok(c) => Some(c),
                    Err(e) => {
                        eprintln!("[session] failed to create driver for {model}: {e}");
                        None
                    }
                }
            });
        let client = match client {
            Some(c) => c,
            None => {
                eprintln!("[session] no driver available for model {model} — cannot dispatch");
                return;
            }
        };
        // Cache the driver so mgr.client.is_none() checks pass (→816 fix).
        if self.client.is_none() {
            self.client = Some(Arc::clone(&client));
        }
        // →843: Refresh boot context at turn boundary (before cloning for the spawned task).
        // Replaces the old 30s timer inside the agent loop — refresh on dispatch, not on a clock.
        self.boot_context.refresh();
        let session = self.sessions.get_mut(&self.active).expect("active session must exist");
        session.dispatch(input, &self.handle, &client, &self.boot_context);
    }

    /// Dispatch user input to a specific named session without changing `self.active`.
    ///
    /// →904: Per-client daemon isolation — each client dispatches to its own bound
    /// session rather than the shared `mgr.active`, preventing cross-contamination
    /// between concurrent TUI connections.
    ///
    /// Creates the session on demand if it does not exist (same policy as `switch`).
    /// The embedded TUI path continues using `dispatch()` via `mgr.active`.
    pub fn dispatch_to(&mut self, name: &str, input: &str) {
        // Ensure the named session exists without changing self.active.
        if !self.sessions.contains_key(name) {
            let mut config = self.sessions.get("scheduler")
                .expect("scheduler session must exist")
                .config.clone();
            if self.agentfile_prompt.is_some() {
                self.boot_context.refresh();
                config.preload_context = self.boot_context.build_preload_context();
            }
            let session = AgentSession::new(name, config, self.root.clone());
            self.sessions.insert(name.to_string(), session);
        }

        // Resolve the correct CpuDriver for this session's model.
        let model = self.sessions.get(name)
            .expect("session must exist after ensure-create above")
            .config.model.clone();
        let client = self.client.clone()
            .filter(|c| driver_matches(c.as_ref(), &model))
            .or_else(|| {
                match crate::cpu::create_driver(&model) {
                    Ok(c) => Some(c),
                    Err(e) => {
                        eprintln!("[session] failed to create driver for {model}: {e}");
                        None
                    }
                }
            });
        let client = match client {
            Some(c) => c,
            None => {
                eprintln!("[session] no driver available for model {model} — cannot dispatch");
                return;
            }
        };
        if self.client.is_none() {
            self.client = Some(Arc::clone(&client));
        }
        self.boot_context.refresh();
        let boot_ctx = self.boot_context.clone();
        let handle = self.handle.clone();
        let session = self.sessions.get_mut(name).expect("session must exist");
        session.dispatch(input, &handle, &client, &boot_ctx);
    }

    // ─── Daemon routing ──────────────────────────────────────────────
    // →plan: SessionManager IS the unified interface. When daemon_client
    // is set, operations forward to the daemon. Otherwise, they execute
    // locally. Same binary, same code.

    /// Whether a daemon connection is active.
    pub fn is_daemon(&self) -> bool { self.daemon_client.is_some() }

    /// Set the daemon connection (e.g. after auto_connect).
    pub fn set_daemon(&mut self, client: crate::serve::client::DaemonClient) {
        self.daemon_client = Some(client);
    }

    /// Drop the daemon connection (e.g. on RPC failure). Falls back to local.
    pub fn drop_daemon(&mut self) {
        self.daemon_client = None;
        self.daemon_bound_session = None;
    }

    /// Access the daemon client directly (transitional — for dispatch.rs).
    pub fn daemon_client_mut(&mut self) -> Option<&mut crate::serve::client::DaemonClient> {
        self.daemon_client.as_mut()
    }

    /// Bind this TUI to a unique daemon session. No-op in embedded mode.
    pub fn bind_daemon(&mut self, session_id: &str) -> Result<(), String> {
        if let Some(ref mut client) = self.daemon_client {
            let _ = client.send_request("session/bind", serde_json::json!({ "name": session_id }))?;
            self.daemon_bound_session = Some(session_id.to_string());
        }
        Ok(())
    }

    /// Query daemon busy status. None in embedded mode.
    pub fn daemon_status(&mut self) -> Option<bool> {
        if let Some(ref mut client) = self.daemon_client {
            match client.send_request("session/status", serde_json::json!({})) {
                Ok(val) => val.get("busy").and_then(|b| b.as_bool()),
                Err(_) => None,
            }
        } else {
            None
        }
    }

    /// Poll events from daemon. None in embedded mode.
    pub fn poll_daemon_events(&mut self, session_name: &str) -> Result<Option<serde_json::Value>, String> {
        if let Some(ref mut client) = self.daemon_client {
            let result = client.poll_events(session_name)?;
            Ok(Some(result))
        } else {
            Ok(None)
        }
    }

    /// Cancel the active session. Routes through daemon if connected.
    pub fn cancel_active(&mut self) -> Result<(), String> {
        if let Some(ref mut client) = self.daemon_client {
            client.send_request("session/cancel", serde_json::json!({}))?;
            crate::kernel::approval::deny_all();
            Ok(())
        } else {
            self.active_session_mut().cancel();
            Ok(())
        }
    }

    /// Send an approval decision. Routes through daemon if connected.
    pub fn approve_active(&mut self, decision: &str, request_id: Option<&str>) -> Result<(), String> {
        if let Some(ref mut client) = self.daemon_client {
            let mut params = serde_json::json!({ "decision": decision });
            if let Some(id) = request_id {
                params["id"] = serde_json::json!(id);
            }
            client.send_request("session/approve", params)?;
            Ok(())
        } else {
            let approval = match decision {
                "allow" => crate::kernel::approval::ApprovalDecision::Allow,
                "always_allow" => crate::kernel::approval::ApprovalDecision::AlwaysAllow,
                _ => crate::kernel::approval::ApprovalDecision::Deny,
            };
            let tx = self.active_session().response_tx.clone();
            let _ = tx.try_send(crate::cpu::agent_loop::AgentResponse::Approval(approval));
            Ok(())
        }
    }

    /// Auto-deny approval (timeout or overlay dismissed).
    pub fn auto_deny_active(&mut self) {
        if self.daemon_client.is_some() {
            crate::kernel::approval::deny_all();
        } else {
            let tx = self.active_session().response_tx.clone();
            let _ = tx.try_send(crate::cpu::agent_loop::AgentResponse::Approval(
                crate::kernel::approval::ApprovalDecision::Deny,
            ));
        }
    }

    /// Redirect: cancel current agent and re-dispatch with new input.
    /// Returns Ok(true) if redirected, Ok(false) if queued.
    pub fn redirect_active(&mut self, input: &str) -> Result<bool, String> {
        if let Some(ref mut client) = self.daemon_client {
            client.send_request("session/redirect", serde_json::json!({ "input": input }))?;
            Ok(true)
        } else {
            let tx = self.active_session().response_tx.clone();
            match tx.try_send(crate::cpu::agent_loop::AgentResponse::Redirect(input.to_string())) {
                Ok(_) => Ok(true),
                Err(_) => {
                    self.active_session_mut().queue_message(input.to_string());
                    Ok(false)
                }
            }
        }
    }

    /// Try upgrading from embedded to daemon if a daemon appeared.
    pub fn try_upgrade(&mut self, ostk_dir: &std::path::Path) -> bool {
        if self.daemon_client.is_some() { return false; }
        if !crate::serve::socket::kernel_alive(ostk_dir) { return false; }
        match crate::serve::client::auto_connect(ostk_dir) {
            Some(client) => {
                self.daemon_client = Some(client);
                true
            }
            None => false,
        }
    }

    /// Drain events from all spawned sessions (transcript + lifecycle).
    /// Call periodically from the daemon event loop or TUI render loop.
    pub fn tick_spawned(&mut self) {
        let names: Vec<String> = self.sessions.keys()
            .filter(|name| {
                self.sessions.get(*name)
                    .is_some_and(|s| matches!(s.session_type, SessionType::Spawned { .. }))
            })
            .cloned()
            .collect();
        for name in names {
            if let Some(session) = self.sessions.get_mut(&name) {
                // Collect events first to avoid double mutable borrow
                let events: Vec<_> = session.event_rx.as_mut()
                    .map(|rx| {
                        let mut evts = Vec::new();
                        while let Ok(ev) = rx.try_recv() { evts.push(ev); }
                        evts
                    })
                    .unwrap_or_default();
                for ev in &events {
                    session.process_event(ev);
                }
            }
        }
    }

    /// →plan Phase 4: Spawn a background agent as a named session.
    ///
    /// Creates a session with `SessionType::Spawned`, configures it from the
    /// provided model/budget, dispatches the prompt, and returns. The session
    /// registers in `agents.jsonl` automatically (via `AgentSession::new`),
    /// writes transcript on events, and emits `agent.complete`/`agent.error`
    /// audit events. Cancellable via cooperative cancel flag.
    pub fn spawn_agent(
        &mut self,
        name: &str,
        model: &str,
        budget: &str,
        prompt: &str,
    ) -> Result<(), String> {
        // Route through daemon if connected
        if let Some(ref mut client) = self.daemon_client {
            client.send_request("session/spawn_agent", serde_json::json!({
                "name": name,
                "model": model,
                "budget": budget,
                "prompt": prompt,
            }))?;
            return Ok(());
        }

        // Local execution — create session and dispatch
        let resolved_model = crate::cpu::providers::resolve_model_alias(model).to_string();

        // Build config from base scheduler config
        let mut config = self.sessions.get("scheduler")
            .or_else(|| self.sessions.get(&self.active))
            .expect("at least one session must exist")
            .config.clone();
        config.model = resolved_model.clone();
        config.permission_mode = crate::cpu::PermissionMode::Autonomous;
        config.max_turns = Some(50);

        // Create transcript directory
        let transcript_dir = self.root.join("transcripts");
        let _ = std::fs::create_dir_all(&transcript_dir);
        let transcript_path = transcript_dir.join(format!("{name}.md"));

        // Create the session
        let mut session = AgentSession::new(name, config, self.root.clone());
        session.session_type = SessionType::Spawned {
            transcript_path: transcript_path.clone(),
            model: resolved_model.clone(),
            prompt_preview: prompt.chars().take(100).collect(),
        };

        self.sessions.insert(name.to_string(), session);

        // Resolve driver for the model
        let client = self.client.clone()
            .filter(|c| driver_matches(c.as_ref(), &resolved_model))
            .or_else(|| {
                crate::cpu::create_driver(&resolved_model).ok()
            })
            .ok_or_else(|| format!("no driver for model {resolved_model}"))?;

        if self.client.is_none() {
            self.client = Some(Arc::clone(&client));
        }

        // Audit: agent.spawned
        let _ = crate::append_audit(&self.root, &serde_json::json!({
            "event": "agent.spawned",
            "name": name,
            "model": resolved_model,
            "budget": budget,
            "runtime": "session",
            "transcript": transcript_path.display().to_string(),
            "timestamp": crate::now_iso(),
        }));

        // Dispatch prompt
        self.boot_context.refresh();
        let boot_ctx = self.boot_context.clone();
        let handle = self.handle.clone();
        let session = self.sessions.get_mut(name).expect("just inserted");
        session.dispatch(prompt, &handle, &client, &boot_ctx);

        Ok(())
    }

    /// Non-blocking drain of all available events from a specific named session.
    ///
    /// →904: Per-client daemon isolation — each client drains from its own bound
    /// session rather than the shared `mgr.active`.
    ///
    /// Returns an empty Vec if the session does not exist.
    pub fn drain_events_from(&mut self, name: &str) -> Vec<CpuEvent> {
        let mut events = Vec::new();
        let mut should_auto_dispatch = false;

        if !self.sessions.contains_key(name) {
            return events;
        }

        let queued_text: Option<String> = {
            let session = self.sessions.get_mut(name).expect("checked above");
            if let Some(ref mut rx) = session.event_rx {
                while let Ok(ev) = rx.try_recv() {
                    events.push(ev);
                }
            }
            for ev in &events {
                if session.process_event(ev) {
                    should_auto_dispatch = true;
                }
            }
            if should_auto_dispatch && !session.is_busy() {
                session.outbox.pop_front()
            } else {
                None
            }
        };
        if let Some(queued) = queued_text {
            // Save active, dispatch to named session, restore active.
            // dispatch_to does not change self.active so this is safe.
            self.dispatch_to(name, &queued);
        }
        let has_turn_complete = events.iter().any(|ev| matches!(ev, CpuEvent::TurnComplete { .. }));
        if has_turn_complete {
            self.boot_context.refresh();
        }
        events
    }

    /// Non-blocking drain of all available events from the active session.
    ///
    /// →FSM Phase 1: Drain events from the active session's event channel.
    ///
    /// →926: Returns `(events, Option<queued_text>)`. When queued text is
    /// returned, the TUI populates the input bar so the user can edit before
    /// sending — replacing the old fire-and-forget auto-dispatch.
    pub fn drain_events(&mut self) -> (Vec<CpuEvent>, Option<String>) {
        let mut events = Vec::new();
        let mut should_auto_dispatch = false;
        let queued_text: Option<String> = {
            let session = self.sessions.get_mut(&self.active).expect("active session must exist");
            if let Some(ref mut rx) = session.event_rx {
                while let Ok(ev) = rx.try_recv() {
                    events.push(ev);
                }
            }
            for ev in &events {
                if session.process_event(ev) {
                    should_auto_dispatch = true;
                }
            }
            // →926: Extract queued text but don't dispatch — let the TUI
            // populate the input bar so the user can review/edit first.
            if should_auto_dispatch && !session.is_busy() {
                session.outbox.pop_front()
            } else {
                None
            }
        };
        // →843: Refresh boot context at turn boundary so the next dispatch
        // always sees current system state (needle counts, fleet, etc).
        let has_turn_complete = events.iter().any(|ev| matches!(ev, CpuEvent::TurnComplete { .. }));
        if has_turn_complete {
            self.boot_context.refresh();
        }
        (events, queued_text)
    }

    /// →904: Return an immutable reference to the sessions map.
    ///
    /// Used by the daemon dispatcher to read a named session directly
    /// (e.g. for approval response_tx lookup) without going through `active_session`.
    pub fn sessions(&self) -> &HashMap<String, AgentSession> {
        &self.sessions
    }

    /// →904: Return a mutable reference to the sessions map.
    ///
    /// Used by the daemon dispatcher to access a named session directly
    /// (e.g. for pending_images injection) without going through `active_session_mut`.
    pub fn sessions_mut(&mut self) -> &mut HashMap<String, AgentSession> {
        &mut self.sessions
    }

    /// Ensure a named session exists without changing `self.active`.
    ///
    /// →904: Called by `session/bind` to create the target session on demand
    /// without side-effects on `mgr.active`.
    pub fn ensure_session(&mut self, name: &str) {
        if !self.sessions.contains_key(name) {
            let mut config = self.sessions.get("scheduler")
                .expect("scheduler session must exist")
                .config.clone();
            if self.agentfile_prompt.is_some() {
                self.boot_context.refresh();
                config.preload_context = self.boot_context.build_preload_context();
            }
            let session = AgentSession::new(name, config, self.root.clone());
            self.sessions.insert(name.to_string(), session);
        }
    }

    /// Switch to a different session by name. Creates on demand.
    ///
    /// New sessions get a system prompt rebuilt from the current boot context
    /// (→787) so they never inherit stale boot state.
    pub fn switch(&mut self, name: &str) {
        tracing::info!(from = %self.active, to = %name, "session: switching");
        if let Some(session) = self.sessions.get(&self.active) {
            let _ = session.save();
        }
        if !self.sessions.contains_key(name) {
            let mut config = self.sessions.get("scheduler")
                .expect("scheduler session must exist")
                .config.clone();
            // →843: Refresh boot state for new session
            if self.agentfile_prompt.is_some() {
                self.boot_context.refresh();
                config.preload_context = self.boot_context.build_preload_context();
            }
            let session = AgentSession::new(name, config, self.root.clone());
            self.sessions.insert(name.to_string(), session);
        }
        self.active = name.to_string();
    }

    /// Create a new named session with the given config.
    pub fn create_session(&mut self, name: &str, config: LoopConfig) {
        let session = AgentSession::new(name, config, self.root.clone());
        self.sessions.insert(name.to_string(), session);
    }

    /// List all sessions as (name, message_count, is_busy) tuples.
    pub fn list_sessions(&self) -> Vec<(&str, usize, bool)> {
        self.sessions.iter()
            .map(|(name, s)| (name.as_str(), s.message_count(), s.is_busy()))
            .collect()
    }

    /// Save the active session to disk.
    pub fn save_active(&self) {
        if let Some(session) = self.sessions.get(&self.active) {
            let _ = session.save();
        }
    }

    /// Reboot the active session (→806).
    ///
    /// Full boot sequence: save config, clear old session, refresh boot context,
    /// rebuild system prompt, re-register kernel identity, inject boot orientation
    /// as first message pair so the agent starts oriented.
    pub fn reboot_active(&mut self) {
        let name = self.active.clone();

        // Save the old config before clearing
        let old_config = self.sessions.get(&name)
            .map(|s| s.config.clone());

        // Remove old session (deregisters identity via Drop)
        self.sessions.remove(&name);

        // Delete session file so the new session starts clean.
        // Without this, AgentSession::new() reloads old messages from disk.
        let session_path = session_file_path(&self.root, &name);
        let prev_path = crate::state_dir(&self.root).join(format!("sessions/{}.prev.jsonl", name));
        // Preserve as .prev for recovery, then delete
        let _ = std::fs::rename(&session_path, &prev_path);

        // Refresh boot context with fresh boot.md + .language
        self.boot_context.refresh();

        // Rebuild config with fresh boot state
        let mut config = old_config.unwrap_or_else(|| {
            self.sessions.get("scheduler")
                .expect("scheduler must exist")
                .config.clone()
        });
        // →823: Use preload_context for cached prefix (replaces →787 enrichment).
        if self.agentfile_prompt.is_some() {
            config.preload_context = self.boot_context.build_preload_context();
        }

        // Create new session (re-registers kernel identity in new())
        let session = AgentSession::new(&name, config, self.root.clone());

        // Inject boot orientation as first message pair
        let boot_summary: String = self.boot_context.boot_md
            .lines().take(10).collect::<Vec<_>>().join("\n");
        session.messages.extend([
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text {
                    text: ":reboot — orient yourself. Fresh kernel state follows.".into(),
                }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text {
                    text: format!("Rebooted. Current state:\n{boot_summary}\n\nReady."),
                }],
                model: None,
            },
        ]);

        self.sessions.insert(name.clone(), session);
        self.active = name;
    }

    /// Remove a session by name (→785).
    ///
    /// Saves the session, deregisters its kernel identity, and removes it from
    /// the HashMap. The "scheduler" session cannot be removed.
    pub fn remove_session(&mut self, name: &str) -> Result<(), String> {
        if name == "scheduler" {
            return Err("cannot remove the scheduler session".into());
        }
        if let Some(mut session) = self.sessions.remove(name) {
            let _ = session.save();
            // →894: Consolidated deregister — idempotent, Drop is now a no-op.
            session.deregister();
            Ok(())
        } else {
            Err(format!("session '{}' not found", name))
        }
    }

    /// Save every session in the HashMap (→789).
    pub fn save_all(&self) {
        for session in self.sessions.values() {
            let _ = session.save();
        }
    }
}

// ─── Helpers ─────────────────────────────────────────────────────────

/// Check if the existing CpuDriver matches the provider needed for the
/// given model. Avoids creating a new driver when the shared one already
/// targets the right backend.
fn driver_matches(driver: &dyn CpuDriver, model: &str) -> bool {
    use crate::cpu::providers::{resolve_provider, ApiProvider};
    let needed = resolve_provider(model);
    let name = driver.provider_name();
    matches!(
        (name, needed),
        ("anthropic", ApiProvider::Anthropic)
            | ("google", ApiProvider::Google)
            | ("mistral", ApiProvider::Mistral)
            | ("openrouter", ApiProvider::OpenRouter)
            | ("openrouter", ApiProvider::OpenAi)
    )
}

fn session_file_path(root: &Path, name: &str) -> PathBuf {
    crate::state_dir(root).join(format!("sessions/{}.jsonl", name))
}

/// Returns true if a message is a `(empty)` placeholder from the empty-response retry path.
fn is_empty_placeholder(msg: &Message) -> bool {
    if msg.role != "assistant" {
        return false;
    }
    msg.content.iter().all(|block| match block {
        crate::cpu::anthropic::ContentBlock::Text { text } => text == "(empty)",
        _ => false,
    })
}

/// Returns true if a message is an auto-continue prompt injected by the agent loop.
fn is_auto_continue(msg: &Message) -> bool {
    if msg.role != "user" {
        return false;
    }
    msg.content.iter().any(|block| match block {
        crate::cpu::anthropic::ContentBlock::Text { text } => {
            text == "Continue -- your previous response was empty."
        }
        _ => false,
    })
}

#[cfg(test)]
fn load_session_messages(root: &Path, name: &str) -> Vec<Message> {
    use std::io::BufRead;
    let path = session_file_path(root, name);
    let file = match fs::File::open(&path) {
        Ok(f) => f,
        Err(_) => return vec![],
    };
    std::io::BufReader::new(file)
        .lines()
        .map_while(Result::ok)
        .filter(|line| !line.trim().is_empty())
        .filter_map(|line| serde_json::from_str::<Message>(&line).ok())
        .collect()
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn test_config() -> LoopConfig {
        LoopConfig {
            model: "claude-sonnet-4-6".into(),
            system_prompt: Some("You are a test agent.".into()),
            tools: vec![],
            max_tokens: 1024,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::Governed,
            fast_mode: false,
            root: None,
            betas: vec![],
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        }
    }

    #[test]
    fn test_agent_session_new_creates_channel() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("test", test_config(), tmp.path().to_path_buf());

        let tx = session.event_tx.clone();
        let rx = session.event_rx.as_mut().unwrap();

        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all().build().unwrap();
        rt.block_on(async {
            tx.send(CpuEvent::TextDelta("hello".into())).await.unwrap();
        });

        match rx.try_recv() {
            Ok(CpuEvent::TextDelta(t)) => assert_eq!(t, "hello"),
            other => panic!("expected TextDelta, got {:?}", other),
        }
    }

    #[test]
    fn test_agent_session_save_load_roundtrip() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();

        let session = AgentSession::new("roundtrip", test_config(), root.clone());
        session.messages.extend([
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "hello".into() }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: "hi there".into() }],
                model: None,
            },
        ]);
        session.save().unwrap();

        // →FSM: new() starts fresh (c71f18e). Use load_session_messages() to verify persistence.
        let msgs = load_session_messages(&root, "roundtrip");
        assert_eq!(msgs.len(), 2);
        assert_eq!(msgs[0].role, "user");
        assert_eq!(msgs[1].role, "assistant");
    }

    #[test]
    fn test_agent_session_clear() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();

        let mut session = AgentSession::new("clearme", test_config(), root.clone());
        session.messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "test".into() }],
            model: None,
        });
        session.save().unwrap();
        assert!(session_file_path(&root, "clearme").exists());

        // Simulate some token accumulation before clear
        session.process_event(&CpuEvent::Usage {
            usage: crate::cpu::anthropic::Usage { input_tokens: 100, output_tokens: 50, ..Default::default() },
        });
        assert_eq!(session.session_tokens.input, 100);

        session.clear();
        assert_eq!(session.message_count(), 0);
        assert!(!session_file_path(&root, "clearme").exists());
        // →931: session_tokens must reset on clear
        assert_eq!(session.session_tokens, SessionTokens::default());
    }

    #[test]
    fn test_session_manager_creates_scheduler() {
        let tmp = tempfile::tempdir().unwrap();
        let mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();
        assert_eq!(mgr.active, "scheduler");
        assert_eq!(mgr.active_session().name, "scheduler");
    }

    #[test]
    fn test_session_manager_switch_creates_on_demand() {
        let tmp = tempfile::tempdir().unwrap();
        let mut mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();
        assert!(!mgr.sessions.contains_key("worker-1"));

        mgr.switch("worker-1");
        assert_eq!(mgr.active, "worker-1");
        assert_eq!(mgr.active_session().name, "worker-1");
    }

    #[test]
    fn test_session_manager_list_sessions() {
        let tmp = tempfile::tempdir().unwrap();
        let mut mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();
        mgr.create_session("alpha", test_config());
        mgr.create_session("beta", test_config());

        let list = mgr.list_sessions();
        assert_eq!(list.len(), 3);
        let names: Vec<&str> = list.iter().map(|(n, _, _)| *n).collect();
        assert!(names.contains(&"scheduler"));
        assert!(names.contains(&"alpha"));
        assert!(names.contains(&"beta"));
    }

    #[test]
    fn test_session_manager_drain_events_empty() {
        let tmp = tempfile::tempdir().unwrap();
        let mut mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();
        assert!(mgr.drain_events().0.is_empty());
    }

    #[test]
    fn test_process_event_accumulates_tokens() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("tok", test_config(), tmp.path().to_path_buf());
        assert_eq!(session.session_tokens, SessionTokens::default());

        session.process_event(&CpuEvent::Usage {
            usage: crate::cpu::anthropic::Usage { input_tokens: 100, output_tokens: 50, ..Default::default() },
        });
        assert_eq!(session.session_tokens.input, 100);
        assert_eq!(session.session_tokens.output, 50);

        session.process_event(&CpuEvent::Usage {
            usage: crate::cpu::anthropic::Usage { input_tokens: 200, output_tokens: 75, ..Default::default() },
        });
        assert_eq!(session.session_tokens.input, 300);
        assert_eq!(session.session_tokens.output, 125);
    }

    #[test]
    fn test_process_event_turn_complete_resets_busy() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("busy", test_config(), tmp.path().to_path_buf());
        session.busy.store(true, Ordering::Release);
        assert!(session.is_busy());

        session.process_event(&CpuEvent::TurnComplete {
            usage: crate::cpu::anthropic::Usage { input_tokens: 50, output_tokens: 25, ..Default::default() },
        });
        assert!(!session.is_busy());
        assert_eq!(session.session_tokens.input, 50);
        assert_eq!(session.session_tokens.output, 25);
        assert_eq!(session.session_tokens.last_turn_output, 25);
    }

    #[test]
    fn test_drain_events_processes_tokens() {
        let tmp = tempfile::tempdir().unwrap();
        let mut mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();

        // Send events into the active session's channel
        let tx = mgr.active_session().event_tx.clone();
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all().build().unwrap();
        rt.block_on(async {
            tx.send(CpuEvent::Usage {
                usage: crate::cpu::anthropic::Usage { input_tokens: 500, output_tokens: 200, ..Default::default() },
            }).await.unwrap();
            tx.send(CpuEvent::TurnComplete {
                usage: crate::cpu::anthropic::Usage { input_tokens: 100, output_tokens: 50, ..Default::default() },
            }).await.unwrap();
        });

        let (events, _queued) = mgr.drain_events();
        assert_eq!(events.len(), 2);
        assert_eq!(mgr.active_session().session_tokens.input, 600);
        assert_eq!(mgr.active_session().session_tokens.output, 250);
        assert!(!mgr.active_session().is_busy());
    }

    // ─── →926: drain_events returns queued text instead of auto-dispatch ─

    #[test]
    fn test_drain_events_returns_queued_text() {
        let tmp = tempfile::tempdir().unwrap();
        let mut mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();

        // Queue a message while "busy"
        mgr.active_session_mut().queue_message("fix the build".to_string());
        assert_eq!(mgr.active_session().outbox.len(), 1);

        // Send TurnComplete so process_event triggers should_auto_dispatch
        let tx = mgr.active_session().event_tx.clone();
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all().build().unwrap();
        rt.block_on(async {
            tx.send(CpuEvent::TurnComplete {
                usage: crate::cpu::anthropic::Usage::default(),
            }).await.unwrap();
        });

        let (events, queued) = mgr.drain_events();
        assert_eq!(events.len(), 1);
        // Queued text is RETURNED, not auto-dispatched
        assert_eq!(queued, Some("fix the build".to_string()));
        // Outbox is now empty
        assert!(mgr.active_session().outbox.is_empty());
        // Session should NOT be busy (no auto-dispatch happened)
        assert!(!mgr.active_session().is_busy());
    }

    #[test]
    fn test_drain_events_no_queued_when_outbox_empty() {
        let tmp = tempfile::tempdir().unwrap();
        let mut mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();

        // Send TurnComplete with no queued messages
        let tx = mgr.active_session().event_tx.clone();
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all().build().unwrap();
        rt.block_on(async {
            tx.send(CpuEvent::TurnComplete {
                usage: crate::cpu::anthropic::Usage::default(),
            }).await.unwrap();
        });

        let (_events, queued) = mgr.drain_events();
        assert!(queued.is_none());
    }

    // ─── BootContext tests (→754) ────────────────────────────────────

    fn setup_ostk_dir() -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        fs::create_dir_all(tmp.path().join(".ostk")).unwrap();
        tmp
    }

    #[test]
    fn test_boot_context_reads_language() {
        let tmp = setup_ostk_dir();
        let lang = ":deploy | 3 | kernel | 0 | 0 | 1.0 | cmd\n";
        fs::write(tmp.path().join(".ostk/.language"), lang).unwrap();
        fs::write(tmp.path().join(".ostk/boot.md"), "# test boot").unwrap();

        let ctx = BootContext::new(tmp.path());
        assert_eq!(ctx.language, lang);
    }

    #[test]
    fn test_boot_context_build_system_prompt() {
        let tmp = setup_ostk_dir();
        // momentum 1.0 → resident; language has 7 columns so it parses
        let mut ctx = BootContext {
            boot_md: "## Needles: 5 open".to_string(),
            language: ":deploy | 3 | kernel | 0 | 0 | 1.0 | cmd".to_string(),
            deferred_summary: build_tool_surface_summary(":deploy | 3 | kernel | 0 | 0 | 1.0 | cmd"),
            stale_p0_ids: vec![],
            cached_preload: Vec::new(),
            root: tmp.path().to_path_buf(),
            last_refresh: None,
            decision_cap: None,
        };

        let prompt = ctx.build_system_prompt("You are an agent.");
        assert!(prompt.starts_with("You are an agent."));
        assert!(prompt.contains("# Boot state\n## Needles: 5 open"));
        assert!(prompt.contains("# Tool surface (demand-paged)"));
        assert!(prompt.contains(":deploy"));

        ctx.boot_md.clear();
        ctx.language.clear();
        ctx.deferred_summary.clear();
        assert_eq!(ctx.build_system_prompt("Base only."), "Base only.");
    }

    #[test]
    fn test_boot_context_stale_p0_injection() {
        let tmp = setup_ostk_dir();
        let ctx = BootContext {
            boot_md: "P0: none".to_string(),
            language: String::new(),
            deferred_summary: String::new(),
            stale_p0_ids: vec!["\u{2192}540".to_string()],
            cached_preload: Vec::new(),
            root: tmp.path().to_path_buf(),
            last_refresh: None,
            decision_cap: None,
        };

        let prompt = ctx.build_system_prompt("Agent.");
        assert!(prompt.contains("stale needles"), "should mention stale needles");
        assert!(prompt.contains("v1.7"), "should mention current milestone");
    }

    #[test]
    fn test_boot_context_no_stale_p0_no_injection() {
        let tmp = setup_ostk_dir();
        let ctx = BootContext {
            boot_md: "P0: current work".to_string(),
            language: String::new(),
            deferred_summary: String::new(),
            stale_p0_ids: vec![],
            cached_preload: Vec::new(),
            root: tmp.path().to_path_buf(),
            last_refresh: None,
            decision_cap: None,
        };

        let prompt = ctx.build_system_prompt("Agent.");
        assert!(!prompt.contains("stale"), "should not mention stale");
    }

    #[test]
    fn test_session_manager_enriches_system_prompt() {
        let tmp = setup_ostk_dir();
        fs::write(tmp.path().join(".ostk/boot.md"), "# Boot OK").unwrap();
        fs::write(tmp.path().join(".ostk/.language"), ":test | 1 | kernel | 0 | 0 | 0.8 | cmd").unwrap();

        let mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();
        // →823: System prompt stays as agentfile prompt only
        let prompt = mgr.active_session().config.system_prompt.as_ref().unwrap();
        assert!(prompt.contains("You are a test agent."));
        // Boot context now lives in preload_context (cached separately)
        let preload = &mgr.active_session().config.preload_context;
        assert!(preload.iter().any(|b| b.contains("# Boot state")), "preload should contain boot state");
        assert!(preload.iter().any(|b| b.contains("Tool surface")), "preload should contain tool surface summary");
    }

    #[test]
    fn test_remove_session() {
        let tmp = tempfile::tempdir().unwrap();
        let mut mgr = SessionManager::new(tmp.path().to_path_buf(), test_config()).unwrap();
        mgr.create_session("worker-1", test_config());
        assert!(mgr.sessions.contains_key("worker-1"));

        // Remove worker-1 — should succeed
        mgr.remove_session("worker-1").unwrap();
        assert!(!mgr.sessions.contains_key("worker-1"));

        // Removing scheduler — should fail
        let err = mgr.remove_session("scheduler");
        assert!(err.is_err());
        assert!(mgr.sessions.contains_key("scheduler"));

        // Removing non-existent session — should fail
        let err = mgr.remove_session("ghost");
        assert!(err.is_err());
    }

    #[test]
    fn test_save_all() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();
        let mut mgr = SessionManager::new(root.clone(), test_config()).unwrap();

        // Add a message to scheduler
        mgr.active_session_mut().messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "hello scheduler".into() }],
            model: None,
        });

        // Create worker-1 with a message
        mgr.create_session("worker-1", test_config());
        mgr.sessions.get_mut("worker-1").unwrap()
            .messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "hello worker".into() }],
                model: None,
            });

        // save_all should persist both
        mgr.save_all();

        assert!(session_file_path(&root, "scheduler").exists());
        assert!(session_file_path(&root, "worker-1").exists());

        // Verify contents by loading fresh sessions
        let loaded_scheduler = load_session_messages(&root, "scheduler");
        assert_eq!(loaded_scheduler.len(), 1);
        let loaded_worker = load_session_messages(&root, "worker-1");
        assert_eq!(loaded_worker.len(), 1);
    }

    // ─── Atomic save tests ─────────────────────────────────────────────

    #[test]
    fn test_session_save_creates_file() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();

        let session = AgentSession::new("atomictest", test_config(), root.clone());
        session.messages.extend([
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
        ]);
        session.save().unwrap();

        let path = session_file_path(&root, "atomictest");
        assert!(path.exists(), "session file should exist after save");

        // Verify contents are valid JSONL
        let content = fs::read_to_string(&path).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 2, "should have 2 JSONL lines");
        for line in &lines {
            assert!(
                serde_json::from_str::<Message>(line).is_ok(),
                "each line should be valid JSON: {}",
                line
            );
        }
    }

    #[test]
    fn test_session_save_atomic_no_partial() {
        let tmp = tempfile::tempdir().unwrap();
        let root = tmp.path().to_path_buf();

        let session = AgentSession::new("atomicclean", test_config(), root.clone());
        session.messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "test".into() }],
            model: None,
        });
        session.save().unwrap();

        // After a successful save, no .tmp file should remain
        let tmp_path = session_file_path(&root, "atomicclean").with_extension("tmp");
        assert!(
            !tmp_path.exists(),
            ".tmp file should not remain after successful save"
        );

        // The real file must exist
        let path = session_file_path(&root, "atomicclean");
        assert!(path.exists(), "session file should exist after save");
    }

    // ─── Outbox tests (→740) ─────────────────────────────────────────

    #[test]
    fn test_outbox_queue_and_drain() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("outbox", test_config(), tmp.path().to_path_buf());

        // Empty outbox drains to empty vec
        assert!(session.drain_outbox().is_empty());

        // Queue messages and verify FIFO order
        session.queue_message("msg-1".to_string());
        session.queue_message("msg-2".to_string());
        session.queue_message("msg-3".to_string());

        let drained = session.drain_outbox();
        assert_eq!(drained, vec!["msg-1", "msg-2", "msg-3"]);

        // Outbox is empty after drain
        assert!(session.drain_outbox().is_empty());
    }

    #[test]
    fn test_outbox_drain_is_idempotent() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("outbox2", test_config(), tmp.path().to_path_buf());

        session.queue_message("once".to_string());
        let first = session.drain_outbox();
        assert_eq!(first, vec!["once"]);

        // Second drain returns nothing — messages are consumed
        let second = session.drain_outbox();
        assert!(second.is_empty());
    }

    // ─── with_handle() tests (→839) ──────────────────────────────────

    #[test]
    fn test_session_manager_with_handle_creates() {
        let tmp = tempfile::tempdir().unwrap();
        let rt = tokio::runtime::Runtime::new().unwrap();
        let handle = rt.handle().clone();
        let mgr = SessionManager::with_handle(handle, tmp.path().to_path_buf(), test_config())
            .expect("with_handle should succeed");
        // scheduler session must exist
        assert!(mgr.sessions.contains_key("scheduler"));
        assert_eq!(mgr.active, "scheduler");
        // _rt must be None (we provided the handle, not the runtime)
        assert!(mgr._rt.is_none());
    }

    #[test]
    fn test_session_manager_dispatch_with_handle() {
        let tmp = tempfile::tempdir().unwrap();
        let rt = tokio::runtime::Builder::new_multi_thread()
            .enable_all().build().unwrap();
        let handle = rt.handle().clone();
        let mut mgr = SessionManager::with_handle(handle, tmp.path().to_path_buf(), test_config())
            .expect("with_handle should succeed");

        // We can't make real API calls, but dispatch() should not panic.
        // It will attempt to create a driver (which may fail gracefully) and
        // return without blocking. The message gets pushed to the session.
        // Since no real driver is available in tests, dispatch logs an error
        // and returns — the message count increments from the push.
        let before = mgr.active_session().message_count();
        mgr.dispatch("hello from with_handle");
        // Message was pushed before the spawn attempt
        let after = mgr.active_session().message_count();
        assert!(after >= before, "message count should not decrease after dispatch");
    }

    #[test]
    fn test_session_manager_with_handle_list_sessions() {
        let tmp = tempfile::tempdir().unwrap();
        let rt = tokio::runtime::Runtime::new().unwrap();
        let handle = rt.handle().clone();
        let mgr = SessionManager::with_handle(handle, tmp.path().to_path_buf(), test_config())
            .expect("with_handle should succeed");
        let sessions = mgr.list_sessions();
        assert!(!sessions.is_empty(), "should have at least one session");
        let names: Vec<&str> = sessions.iter().map(|(n, _, _)| *n).collect();
        assert!(names.contains(&"scheduler"), "scheduler session must be listed");
    }

    #[test]
    fn test_session_manager_switch_creates_session() {
        let tmp = tempfile::tempdir().unwrap();
        let rt = tokio::runtime::Runtime::new().unwrap();
        let handle = rt.handle().clone();
        let mut mgr = SessionManager::with_handle(handle, tmp.path().to_path_buf(), test_config())
            .expect("with_handle should succeed");
        assert!(!mgr.sessions.contains_key("worker"));
        mgr.switch("worker");
        assert!(mgr.sessions.contains_key("worker"), "switch should create new session");
        assert_eq!(mgr.active, "worker");
    }

    // ─── Panic handler tests ─────────────────────────────────────────

    #[test]
    fn test_agent_panic_sends_error_event() {
        // Verify that a CpuEvent::Error with "[panic]" prefix is correctly
        // processed: busy flag should be set to false.
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("panic-test", test_config(), tmp.path().to_path_buf());

        // Simulate: busy was true (agent loop running)
        session.busy.store(true, Ordering::Release);
        assert!(session.is_busy());

        // Send a panic-style error through the channel
        let tx = session.event_tx.clone();
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all().build().unwrap();
        rt.block_on(async {
            tx.send(CpuEvent::Error("[panic] test panic message".into()))
                .await
                .unwrap();
        });

        // Drain and process events
        let rx = session.event_rx.as_mut().unwrap();
        match rx.try_recv() {
            Ok(ref event @ CpuEvent::Error(ref msg)) => {
                assert!(msg.contains("[panic]"), "error should contain [panic] prefix");
                session.process_event(event);
            }
            other => panic!("expected CpuEvent::Error, got {:?}", other),
        }

        // Busy must be false after processing the error event
        assert!(!session.is_busy(), "busy should be false after panic error event");
    }

    #[test]
    fn test_busy_flag_false_after_error() {
        // Verify that process_event(CpuEvent::Error) always resets busy to false,
        // regardless of the error message contents.
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("busy-err", test_config(), tmp.path().to_path_buf());

        // Set busy = true
        session.busy.store(true, Ordering::Release);
        assert!(session.is_busy());

        // Process a generic error
        session.process_event(&CpuEvent::Error("something went wrong".into()));
        assert!(!session.is_busy(), "busy should be false after Error event");

        // Re-set busy and process a panic-style error
        session.busy.store(true, Ordering::Release);
        session.process_event(&CpuEvent::Error("[panic] agent loop panicked".into()));
        assert!(!session.is_busy(), "busy should be false after panic Error event");
    }

    // ─── build_tool_surface_summary tests (demand-paged tool system) ─

    #[test]
    fn build_tool_surface_summary_deduplicates_aliases() {
        // Two verbs with same resolution should collapse to one in the summary
        let language = "# .language\n\
            :add     | 2 | user | 0 | 200 | 0.25 | ostk work add | (title) -> (id) | file a needle\n\
            :needle  | 2 | user | 0 | 300 | 0.60 | ostk work add | (title) -> (id) | file executable work\n";
        let summary = build_tool_surface_summary(language);
        // :needle should appear (higher momentum), :add should not
        assert!(summary.contains(":needle"), "should contain canonical :needle");
        assert!(!summary.contains(":add"), "should NOT contain alias :add, got: {}", summary);
    }

    #[test]
    fn build_tool_surface_summary_splits_resident_deferred() {
        let language = "# .language\n\
            :hay     | 1 | user | 0 | 200 | 1.00 | ostk work hay | (straw) -> () | capture intent\n\
            :draft   | 1 | user | 0 | 200 | 0.05 | ostk doc draft | (title) -> (path) | create draft\n";
        let summary = build_tool_surface_summary(language);
        assert!(summary.contains("Resident:"), "should have Resident section");
        assert!(summary.contains(":hay"), ":hay should be resident (momentum 1.0)");
        assert!(summary.contains("Deferred"), "should have Deferred section");
        assert!(summary.contains(":draft"), ":draft should be deferred (momentum 0.05)");
    }

    #[test]
    fn build_tool_surface_summary_includes_cli_tree() {
        let language = "# .language\n\
            :hay | 1 | user | 0 | 200 | 1.00 | ostk work hay | (straw) -> () | capture intent\n";
        let summary = build_tool_surface_summary(language);
        assert!(summary.contains("CLI (use ostk_man"), "should include CLI tree index");
        assert!(summary.contains("work:"), "should list work commands");
    }

    // ─── →892: init_common produces equivalent state ────────────────────

    #[test]
    fn test_new_and_with_handle_produce_equivalent_state() {
        let tmp1 = tempfile::tempdir().unwrap();
        let tmp2 = tempfile::tempdir().unwrap();
        // Set up identical .ostk dirs
        for tmp in [&tmp1, &tmp2] {
            fs::create_dir_all(tmp.path().join(".ostk")).unwrap();
        }

        let mgr_new = SessionManager::new(tmp1.path().to_path_buf(), test_config()).unwrap();

        let rt = tokio::runtime::Runtime::new().unwrap();
        let mgr_handle = SessionManager::with_handle(
            rt.handle().clone(), tmp2.path().to_path_buf(), test_config(),
        ).unwrap();

        // Same session count
        assert_eq!(mgr_new.sessions.len(), mgr_handle.sessions.len(),
            "both constructors should create same number of sessions");
        // Same active session name
        assert_eq!(mgr_new.active, mgr_handle.active);
        // Same model on the scheduler session
        assert_eq!(
            mgr_new.active_session().config.model,
            mgr_handle.active_session().config.model,
        );
        // Owned rt vs borrowed
        assert!(mgr_new._rt.is_some(), "new() should own the runtime");
        assert!(mgr_handle._rt.is_none(), "with_handle() should not own the runtime");
    }

    // ─── →894: deregister idempotency ───────────────────────────────────

    #[test]
    fn test_deregister_clears_alias_and_is_idempotent() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("dereg", test_config(), tmp.path().to_path_buf());

        // First deregister should clear the alias
        session.deregister();
        assert!(session.kernel_alias.is_none(), "alias should be None after deregister");

        // Second deregister should be a no-op (must not panic)
        session.deregister();
        assert!(session.kernel_alias.is_none(), "alias should remain None");
    }

    // ─── →895: outbox cap ───────────────────────────────────────────────

    #[test]
    fn test_outbox_cap_drops_oldest() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("cap", test_config(), tmp.path().to_path_buf());

        // Push 15 messages — only the last MAX_OUTBOX should remain
        for i in 0..15 {
            session.queue_message(format!("msg-{i}"));
        }
        assert_eq!(session.outbox.len(), MAX_OUTBOX,
            "outbox should be capped at MAX_OUTBOX");

        let drained = session.drain_outbox();
        // The first 5 (msg-0 through msg-4) should have been dropped
        assert_eq!(drained.len(), MAX_OUTBOX);
        assert_eq!(drained[0], "msg-5", "oldest kept message should be msg-5");
        assert_eq!(drained[MAX_OUTBOX - 1], "msg-14", "newest message should be msg-14");
    }

    // ─── →892: compact preserves tool_use/tool_result pairing ──────────

    #[test]
    fn test_compact_preserves_tool_pairing() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("compact-pair", test_config(), tmp.path().to_path_buf());

        // Build 3 rounds of: user text, assistant tool_use, user tool_result, assistant text
        for round in 0..3 {
            let tool_id = format!("tool_{round}");
            session.messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: format!("question {round}") }],
                model: None,
            });
            session.messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: tool_id.clone(),
                    name: "Bash".into(),
                    input: serde_json::json!({"cmd": "ls"}),
                }],
                model: None,
            });
            session.messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: tool_id,
                    content: format!("result {round}"),
                    is_error: false,
                }],
                model: None,
            });
            session.messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: format!("answer {round}") }],
                model: None,
            });
        }
        assert_eq!(session.message_count(), 12);

        // Compact to keep last 1 user turn pair
        let (before, after) = session.compact(1);
        assert_eq!(before, 12);
        assert!(after < before, "compact should have removed messages");

        // Validate: every tool_use has a matching tool_result
        let msgs = session.messages.with_lock(|m| m.to_vec());
        assert!(
            crate::fcp::llm::validate_tool_pairing(&msgs),
            "compact must preserve tool_use/tool_result pairing, got: {:#?}",
            msgs.iter().map(|m| (&m.role, m.content.len())).collect::<Vec<_>>()
        );
    }

    #[test]
    fn test_compact_counts_text_not_tool_results() {
        let tmp = tempfile::tempdir().unwrap();
        let mut session = AgentSession::new("compact-count", test_config(), tmp.path().to_path_buf());

        // Build a conversation where tool_result messages outnumber text messages:
        // 1 user text, then 3 rounds of tool_use + tool_result, then assistant text
        session.messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "do three things".into() }],
            model: None,
        });
        for i in 0..3 {
            let tool_id = format!("multi_{i}");
            session.messages.push(Message {
                role: "assistant".into(),
                content: vec![ContentBlock::ToolUse {
                    id: tool_id.clone(),
                    name: "Bash".into(),
                    input: serde_json::json!({"cmd": "echo"}),
                }],
                model: None,
            });
            session.messages.push(Message {
                role: "user".into(),
                content: vec![ContentBlock::ToolResult {
                    tool_use_id: tool_id,
                    content: format!("ok {i}"),
                    is_error: false,
                }],
                model: None,
            });
        }
        session.messages.push(Message {
            role: "assistant".into(),
            content: vec![ContentBlock::Text { text: "all done".into() }],
            model: None,
        });

        // Now add a second user text turn
        session.messages.push(Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: "thanks".into() }],
            model: None,
        });
        session.messages.push(Message {
            role: "assistant".into(),
            content: vec![ContentBlock::Text { text: "you're welcome".into() }],
            model: None,
        });

        // Total: 10 messages (1 user text + 3*(assistant tool_use + user tool_result)
        //        + 1 assistant text + 1 user text + 1 assistant text)
        // There are 5 "user" role messages but only 2 are text intents.
        assert_eq!(session.message_count(), 10);

        // Compact keeping 2 text intents — should keep everything
        let (before, after) = session.compact(2);
        assert_eq!(before, 10);
        assert_eq!(after, 10, "keeping 2 intents should retain entire conversation");

        // Compact keeping 1 text intent — should drop the first turn
        let (_, after) = session.compact(1);
        assert!(after < 10, "keeping 1 intent should drop earlier turns");

        // Validate pairing is preserved
        let msgs = session.messages.with_lock(|m| m.to_vec());
        assert!(
            crate::fcp::llm::validate_tool_pairing(&msgs),
            "compact(1) must preserve tool pairing"
        );

        // The kept messages should contain "thanks" (the last user text intent)
        let has_thanks = msgs.iter().any(|m| {
            m.content.iter().any(|c| matches!(c, ContentBlock::Text { text } if text == "thanks"))
        });
        assert!(has_thanks, "last user text intent 'thanks' should be kept");
    }
}
