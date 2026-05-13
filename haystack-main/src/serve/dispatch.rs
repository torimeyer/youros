//! MCP Dispatch — routes JSON-RPC requests to tool handlers.
//!
//! Implements the MCP protocol lifecycle: initialize, notifications/initialized,
//! tools/list, tools/call. Uses ostk kernel primitives for execution.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};

use serde_json::json;

/// Per-process connection counter for generating unique client IDs (→904).
static CLIENT_COUNTER: AtomicU64 = AtomicU64::new(0);

use crate::kernel::{digest, dying, heartbeat, identity, nudge, predispatch, recovery};
use crate::serve::state::ServerState;
use crate::serve::tools::{sh_interact, sh_lock, sh_run, sh_session, sh_spawn, fs_ops, fs_read, tack, pitchfork, context_search, context_release};
use crate::serve::types::{
    ERR_INTERNAL, ERR_INVALID_PARAMS, ERR_INVALID_REQUEST, ERR_METHOD_NOT_FOUND, InitializeResult,
    JsonRpcError, JsonRpcRequest, JsonRpcResponse, ServerCapabilities, ServerInfo,
    ShInteractParams, ShLockParams, ShRunParams, ShSessionParams, ShSpawnParams, FsOpsParams,
    FsReadParams, TackParams, ToolDefinition, ToolError, ToolsCapability,
};

/// The MCP server dispatcher.
pub struct McpDispatcher {
    state: Arc<ServerState>,
    initialized: std::sync::atomic::AtomicBool,
    /// →904: Unique identity for this daemon connection.
    /// Format: "client-{pid}-{counter}", stable for the connection lifetime.
    client_id: String,
    /// →904: Session name this client is bound to.
    /// None until the client calls `session/bind` or first `session/dispatch`.
    /// Protects against cross-contamination when two TUI instances share a daemon.
    bound_session: std::sync::Mutex<Option<String>>,
}

impl McpDispatcher {
    pub fn new(state: Arc<ServerState>) -> Self {
        let seq = CLIENT_COUNTER.fetch_add(1, AtomicOrdering::Relaxed);
        let client_id = format!("client-{}-{}", std::process::id(), seq);
        Self {
            state,
            initialized: std::sync::atomic::AtomicBool::new(false),
            client_id,
            bound_session: std::sync::Mutex::new(None),
        }
    }

    /// →904: Return the session name this client is bound to, or a default.
    ///
    /// Falls back to the requested `name` parameter so that existing clients
    /// which never call `session/bind` continue working without change.
    fn resolve_session(&self, requested: &str) -> String {
        self.bound_session
            .lock()
            .unwrap()
            .clone()
            .unwrap_or_else(|| requested.to_string())
    }

    /// →904: Bind this client to a session name and return the client_id.
    ///
    /// Idempotent: calling it multiple times replaces the binding.
    fn bind_session(&self, name: &str) {
        *self.bound_session.lock().unwrap() = Some(name.to_string());
    }

    /// Handle a single JSON-RPC request and return a response.
    /// Returns `None` for notifications (no response expected).
    pub async fn dispatch(&self, request: JsonRpcRequest) -> Option<JsonRpcResponse> {
        match request.method.as_str() {
            "initialize" => Some(self.handle_initialize(request).await),
            "tools/list" => Some(self.handle_tools_list(request)),
            "tools/call" => {
                if !self.initialized.load(std::sync::atomic::Ordering::Relaxed) {
                    return Some(JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: request.id,
                        result: None,
                        error: Some(JsonRpcError {
                            code: ERR_INVALID_REQUEST,
                            message: "Server not initialized. Send 'initialize' and 'notifications/initialized' before calling tools.".to_string(),
                            data: None,
                        }),
                    });
                }
                Some(self.handle_tools_call(request).await)
            }
            m if m.starts_with("session/") => {
                if !self.initialized.load(std::sync::atomic::Ordering::Relaxed) {
                    return Some(JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: request.id,
                        result: None,
                        error: Some(JsonRpcError {
                            code: ERR_INVALID_REQUEST,
                            message: "Server not initialized".to_string(),
                            data: None,
                        }),
                    });
                }
                Some(self.handle_session(request).await)
            }
            m if m.starts_with("notifications/") => {
                if m == "notifications/initialized" {
                    self.initialized
                        .store(true, std::sync::atomic::Ordering::Relaxed);
                }
                None // Notifications get no response
            }
            _ => Some(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: request.id,
                result: None,
                error: Some(JsonRpcError {
                    code: ERR_METHOD_NOT_FOUND,
                    message: format!("Method not found: {}", request.method),
                    data: None,
                }),
            }),
        }
    }

    async fn handle_initialize(&self, request: JsonRpcRequest) -> JsonRpcResponse {
        // Assign a kernel identity for this agent connection.
        let id_mgr = identity::Identity::new(&self.state.ostk_dir);
        match id_mgr.assign_alias() {
            Ok(alias) => {
                // Store the alias in server state. Use blocking write to guarantee
                // alias assignment — try_write() would silently drop it if contended
                // (PR-C-004: alias must never be lost silently).
                {
                    let mut guard = self.state.agent_alias.write().await;
                    *guard = Some(alias.clone());
                }
                // Record initial heartbeat so agent appears in digest immediately.
                let _ = heartbeat::record_heartbeat(&self.state.ostk_dir, &alias);

                // Check for previous session and generate recovery digest (Task 4: bd-203)
                let recovery_mgr = recovery::Recovery::new(&self.state.ostk_dir);
                if let Ok(Some(summary)) = recovery_mgr.generate_recovery(&alias)
                    && !summary.formatted.is_empty()
                {
                    eprintln!("[ostk] recovery for {alias}: {}", summary.formatted);
                    // Store recovery text in state for injection on first tool response.
                    // Blocking write guarantees it is stored (PR-C-004).
                    {
                        let mut guard = self.state.recovery_text.write().await;
                        *guard = Some(summary.formatted);
                    }
                    // Clear the old session log so it doesn't replay again
                    let _ = recovery_mgr.clear_session(&alias);
                }

                eprintln!("[ostk] initialized as {alias}");
            }
            Err(e) => {
                eprintln!("[ostk] identity assignment failed: {e}");
                return self.error_response(
                    request.id,
                    ERR_INTERNAL,
                    format!("identity assignment failed: {e}"),
                );
            }
        }

        let result = InitializeResult {
            protocol_version: "2024-11-05".to_string(),
            capabilities: ServerCapabilities {
                tools: ToolsCapability {
                    list_changed: false,
                },
            },
            server_info: ServerInfo {
                name: "ostk".to_string(),
                version: env!("CARGO_PKG_VERSION").to_string(),
            },
            instructions: Some(OSTK_INSTRUCTIONS.to_string()),
        };

        JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: request.id,
            result: Some(serde_json::to_value(result).unwrap()),
            error: None,
        }
    }

    fn handle_tools_list(&self, request: JsonRpcRequest) -> JsonRpcResponse {
        let tools = tool_definitions();
        JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: request.id,
            result: Some(json!({ "tools": tools })),
            error: None,
        }
    }

    async fn handle_tools_call(&self, request: JsonRpcRequest) -> JsonRpcResponse {
        let id = request.id.clone();

        // Record heartbeat before dispatching (makes agent visible in process digest).
        let agent_alias = self.get_agent_alias().await;
        if let Some(ref alias) = agent_alias {
            let _ = heartbeat::record_heartbeat(&self.state.ostk_dir, alias);
        }

        // →620: update context_pct from env var OSTK_CONTEXT_PCT (set by run.rs or CI).
        // This allows the caller to signal context pressure to the kernel each turn.
        if let Ok(pct_str) = std::env::var("OSTK_CONTEXT_PCT")
            && let Ok(pct) = pct_str.parse::<f32>() {
                self.state.set_context_pct(pct);
            }

        let params = match request.params {
            Some(p) => p,
            None => {
                return self.error_response(id, ERR_INVALID_PARAMS, "Missing params");
            }
        };

        let tool_name = match params.get("name").and_then(|n| n.as_str()) {
            Some(name) => name.to_string(),
            None => {
                return self.error_response(id, ERR_INVALID_PARAMS, "Missing 'name' in params");
            }
        };

        let arguments = params
            .get("arguments")
            .cloned()
            .unwrap_or_else(|| json!({}));

        // Extract args summary before arguments is moved (Task 3: bd-202)
        let args_summary = match tool_name.as_str() {
            "shell" | "sh_run" | "bash" | "Bash" => arguments
                .get("cmd")
                .or_else(|| arguments.get("command"))
                .and_then(|c| c.as_str())
                .unwrap_or("")
                .to_string(),
            "fs_ops" | "fs_read" | "file:read" | "read" | "file:edit" | "file:write" => arguments
                .get("path")
                .or_else(|| arguments.get("action"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            _ => serde_json::to_string(&arguments).unwrap_or_default(),
        };

        let tool_result = match tool_name.as_str() {
            "shell" | "sh_run" | "bash" | "Bash" => self.dispatch_sh_run(arguments).await,
            "spawn" | "sh_spawn" => self.dispatch_sh_spawn(arguments).await,
            "interact" | "sh_interact" => self.dispatch_sh_interact(arguments).await,
            "session" | "sh_session" => self.dispatch_sh_session(arguments).await,
            "lock" | "sh_lock" => self.dispatch_sh_lock(arguments).await,
            "help" | "sh_help" => Ok(sh_run::handle_sh_help()),
            "fs_ops" | "file:edit" | "file:write" | "Edit" | "Write" => self.dispatch_fs_ops(arguments).await,
            "fs_read" | "file:read" | "Read" => self.dispatch_fs_read(arguments).await,
            "read" => self.dispatch_read(arguments).await,
            "search" => self.dispatch_search(arguments),
            "tack" => self.dispatch_tack(arguments),
            // fcp-web: built-in web reading tools (→842)
            "WebRead" | "web_read" | "ostk_fcp-web" | "ostk_fcp_web"
                => self.dispatch_web_read(arguments).await,
            "WebLinks" | "web_links" => self.dispatch_web_links(arguments).await,
            "WebStatus" | "web_status" => self.dispatch_web_status(arguments).await,
            "ostk_pitchfork" | "pitchfork" => self.dispatch_pitchfork(arguments),
            "ostk_context_search" | "context_search" => self.dispatch_context_search(arguments).await,
            "ostk_context_release" | "context_release" => self.dispatch_context_release(arguments).await,
            _ => {
                return self.error_response(
                    id,
                    ERR_METHOD_NOT_FOUND,
                    format!("Unknown tool: {tool_name}"),
                );
            }
        };

        // Log tool call for recovery (Task 3: bd-202)
        let result_summary = match &tool_result {
            Ok(v) => {
                if let Some(exit) = v.get("exit_code") {
                    format!("exit:{exit}")
                } else {
                    "ok".to_string()
                }
            }
            Err(e) => format!("error: {}", e.message),
        };
        if let Some(ref alias) = agent_alias {
            let recovery = recovery::Recovery::new(&self.state.ostk_dir);
            let _ = recovery.log_tool_call(alias, &tool_name, &args_summary, &result_summary);
        }

        match tool_result {
            Ok(result_value) => {
                // Format as MCP content wrapper
                let mut text = format_tool_text(&tool_name, &result_value);

                // Append digest to every tool response (ambient awareness).
                if let Some(ref alias) = agent_alias
                    && let Ok(d) = digest::generate_digest(&self.state.ostk_dir, alias)
                {
                    let digest_text = d.format();
                    if !digest_text.is_empty() {
                        text.push('\n');
                        text.push_str(&digest_text);
                    }
                }

                // →1199: [loadavg] line — live read from disk, replaces the per-session
                // cached register that init_full.rs initialised to 0 and never refreshed.
                // generate_loadavg_line() reads needles/issues.jsonl, agents.jsonl, nudges/
                // on every tool call, so the counts always reflect the current state.
                {
                    let la = crate::commands::helpers::generate_loadavg_line(&self.state.ostk_dir);
                    text.push('\n');
                    text.push_str(&la);
                }

                // →928: One-shot context note. Fires once per session to tell the model
                // that system_warning from clear_tool_uses is routine, not context death.
                if !self.state.context_note_sent.swap(true, std::sync::atomic::Ordering::Relaxed) {
                    let call_count = self.state.tool_call_counter.load(std::sync::atomic::Ordering::Relaxed);
                    // Fire after 6+ tool calls (when clear_tool_uses likely first triggers)
                    if call_count >= 6 {
                        text.push_str("\n[ctx] system_warning is routine clear_tool_uses cleanup — not context pressure. Use :status for real utilization.");
                    } else {
                        // Reset — haven't hit the threshold yet
                        self.state.context_note_sent.store(false, std::sync::atomic::Ordering::Relaxed);
                    }
                }

                // →957: Pre-dispatch intent resolution — inject kernel state for user queries.
                // Reads last_user_input (set by session/dispatch) and pre-resolves
                // orientation queries, decision references, and concept references.
                {
                    let user_input = self.state.last_user_input.read().await;
                    if let Some(ref input) = *user_input
                        && let Some(pre) = predispatch::pre_resolve(&self.state.ostk_dir, input) {
                            text.push_str("\n[predispatch:");
                            text.push_str(pre.source);
                            text.push_str("] ");
                            text.push_str(&pre.injection);
                        }
                }

                // fcp-llm: Append kernel context block (registers + working state).
                // Injected alongside digest so harness agents (Claude Code) see
                // the same structured state that kernel agents get in preload_context.
                {
                    let root = self.state.ostk_dir.parent()
                        .unwrap_or(&self.state.ostk_dir);
                    // Throttle: only include full context block every 10 tool calls
                    // to avoid bloating every response. The digest (procs/files) is
                    // always present; the context block adds registers + decisions.
                    let call_count = self.state.tool_call_counter.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    if call_count.is_multiple_of(10) {
                        let boot_ctx = crate::cpu::session::BootContext::new(root);
                        let registers = crate::fcp::llm::render_registers(&boot_ctx, root);
                        let working = crate::fcp::llm::render_working_state(root, None);
                        text.push_str("\n[ctx] ");
                        text.push_str(&registers);
                        if working.len() > 20 {
                            text.push_str(&working);
                        }
                    }
                }

                // Inject recovery digest on first tool response (Task 4: bd-203).
                // Blocking write guarantees the recovery text is not silently lost
                // if the lock is transiently contended (PR-C-004).
                {
                    let mut guard = self.state.recovery_text.write().await;
                    if let Some(recovery_text) = guard.take() {
                        text.push('\n');
                        text.push_str("[recovery] ");
                        text.push_str(&recovery_text);
                    }
                }

                // Pop pending nudges for this agent (Task 2: bd-201)
                if let Some(ref alias) = agent_alias
                    && let Ok(msgs) = nudge::pop_nudges(&self.state.ostk_dir, alias)
                    && !msgs.is_empty()
                {
                    let nudge_text = nudge::format_nudges(&msgs);
                    text.push('\n');
                    text.push_str(&nudge_text);
                }

                // →620: dying notification — fire when context hits 90%
                // context_pct is set by OSTK_CONTEXT_PCT env var (set by run.rs).
                if let Some(ref alias) = agent_alias {
                    let ctx_pct = self.state.get_context_pct();
                    if dying::should_notify(ctx_pct, &self.state.ostk_dir, alias) {
                        let _ = dying::notify_dying(
                            &self.state.ostk_dir,
                            alias,
                            ctx_pct,
                            None, // active needle unknown at dispatch level
                            "context pressure reached — see recovery log for work state",
                            "scheduler will re-dispatch with this file as boot context",
                        );
                        text.push_str("\n\n[dying] context at ");
                        text.push_str(&format!("{ctx_pct:.0}%"));
                        text.push_str(" — dying.md written, scheduler notified");
                    }
                }

                // →608: Context heartbeat — inject kernel state delta every 8 turns
                // or on tool failure. Per-agent temporal tracking.
                let hb_alias = agent_alias.as_deref().unwrap_or("unknown");
                if let Some(hb) = crate::commands::context::tick_and_maybe_inject(
                    &self.state.ostk_dir,
                    hb_alias,
                    false, // success path — not a tool failure
                ) {
                    text.push('\n');
                    text.push_str(&hb);
                }

                JsonRpcResponse {
                    jsonrpc: "2.0".to_string(),
                    id,
                    result: Some(json!({
                        "content": [
                            { "type": "text", "text": text }
                        ]
                    })),
                    error: None,
                }
            }
            Err(e) => {
                // →608: Inject heartbeat on tool failure (immediate trigger).
                let err_alias = agent_alias.as_deref().unwrap_or("unknown");
                let _ = crate::commands::context::tick_and_maybe_inject(
                    &self.state.ostk_dir,
                    err_alias,
                    true, // tool failed — force heartbeat
                );
                self.error_response(id, e.code, e.message)
            }
        }
    }

    /// Get the current agent alias from state.
    async fn get_agent_alias(&self) -> Option<String> {
        self.state.agent_alias.read().await.clone()
    }

    fn error_response(
        &self,
        id: serde_json::Value,
        code: i32,
        message: impl Into<String>,
    ) -> JsonRpcResponse {
        JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id,
            result: None,
            error: Some(JsonRpcError {
                code,
                message: message.into(),
                data: None,
            }),
        }
    }

    // ── Tool dispatch helpers ──

    async fn dispatch_sh_run(
        &self,
        mut arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        // Normalize Anthropic "command" → ostk "cmd" so both API schemas work.
        if arguments.get("cmd").is_none()
            && let Some(command) = arguments.get("command").cloned() {
                arguments["cmd"] = command;
            }
        let params: ShRunParams = serde_json::from_value(arguments)
            .map_err(|e| ToolError::invalid_params(format!("Invalid shell params: {e}")))?;
        // →879: Resolve project root from kernel state (ostk_dir.parent())
        let project_root = self.state.ostk_dir.parent()
            .unwrap_or(&self.state.ostk_dir)
            .to_path_buf();
        // →1153: Fleet-active gate — block git state mutations while agents are writing.
        // count_fleet() applies a 90s staleness window, so stale rows never falsely block.
        // OSTK_SKIP_GIT_GUARD=1 bypasses the gate (operator override).
        if crate::commands::helpers::is_git_state_mutation(&params.cmd)
            && std::env::var("OSTK_SKIP_GIT_GUARD").map_or(true, |v| v.is_empty())
        {
            let agents_path = self.state.ostk_dir.join("agents.jsonl");
            let (active, _) = crate::commands::helpers::count_fleet(&agents_path);
            if active > 0 {
                return Err(ToolError::new(
                    crate::serve::types::ERR_SHELL_ERROR,
                    format!(
                        "error: git-state-mutation blocked by fleet-active gate (→1522): \
                         {} agent{} active — git stash/reset/clean while peers are writing \
                         risks data loss. Wait until fleet is idle, or set \
                         OSTK_SKIP_GIT_GUARD=1 to override (operator use only).",
                        active, if active == 1 { "" } else { "s" }
                    ),
                ));
            }
        }
        sh_run::handle(params, &project_root).await
    }

    async fn dispatch_sh_spawn(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let params: ShSpawnParams = serde_json::from_value(arguments)
            .map_err(|e| ToolError::invalid_params(format!("Invalid spawn params: {e}")))?;
        sh_spawn::handle(params, &self.state).await
    }

    async fn dispatch_sh_interact(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let params: ShInteractParams = serde_json::from_value(arguments)
            .map_err(|e| ToolError::invalid_params(format!("Invalid interact params: {e}")))?;
        sh_interact::handle(params, &self.state).await
    }

    async fn dispatch_sh_session(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let params: ShSessionParams = serde_json::from_value(arguments)
            .map_err(|e| ToolError::invalid_params(format!("Invalid session params: {e}")))?;
        sh_session::handle(params, &self.state).await
    }

    async fn dispatch_sh_lock(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let params: ShLockParams = serde_json::from_value(arguments)
            .map_err(|e| ToolError::invalid_params(format!("Invalid lock params: {e}")))?;
        sh_lock::handle(params, &self.state).await
    }

    async fn dispatch_fs_ops(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let mut params: FsOpsParams = serde_json::from_value(arguments)
            .map_err(|e| ToolError::invalid_params(format!("Invalid file:edit params: {e}")))?;
        let agent = self.get_agent_alias().await;
        // →1225: Remap absolute main-checkout paths to the agent worktree.
        // Agents in isolation:worktree sessions may receive absolute paths from
        // the parent session context. Those paths point to the main checkout
        // instead of the worktree, causing writes to land on main. If this
        // process runs in a git worktree (project_root/.git is a file not a
        // directory), detect the main checkout and transparently remap.
        let project_root = self.state.ostk_dir.parent().unwrap_or(&self.state.ostk_dir);
        if let Some(main_root) = find_main_checkout_from_worktree(project_root) {
            params = remap_fs_ops_paths(params, project_root, &main_root);
        }
        fs_ops::handle(params, &self.state.ostk_dir, agent.as_deref()).await
    }

    async fn dispatch_fs_read(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let params: FsReadParams = serde_json::from_value(arguments)
            .map_err(|e| ToolError::invalid_params(format!("Invalid file:read params: {e}")))?;
        let agent = self.get_agent_alias().await;
        fs_read::handle(params, &self.state.ostk_dir, agent.as_deref()).await
    }

    /// `read` tool: accepts path/offset/limit params (documented API) and
    /// translates to the internal action-string format used by fs_read::handle.
    async fn dispatch_read(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let action = if let Some(path) = arguments.get("path").and_then(|v| v.as_str()) {
            if let Some(offset) = arguments.get("offset").and_then(|v| v.as_i64()) {
                let limit = arguments.get("limit").and_then(|v| v.as_i64()).unwrap_or(100);
                let end = offset + limit;
                format!("read {} start:{} end:{}", path, offset + 1, end)
            } else {
                format!("read {}", path)
            }
        } else if let Some(action_str) = arguments.get("action").and_then(|v| v.as_str()) {
            action_str.to_string()
        } else {
            return Err(ToolError::invalid_params("'path' is required for read tool"));
        };
        let params = FsReadParams { action };
        let agent = self.get_agent_alias().await;
        fs_read::handle(params, &self.state.ostk_dir, agent.as_deref()).await
    }

    /// `search` tool: routes to pitchfork for kernel-state keyword search.
    fn dispatch_search(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let query = arguments.get("query").and_then(|v| v.as_str()).unwrap_or("");
        pitchfork::handle(query, &self.state.ostk_dir)
    }

    fn dispatch_tack(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let params: TackParams = serde_json::from_value(arguments)
            .map_err(|e| ToolError::invalid_params(format!("Invalid tack params: {e}")))?;
        tack::handle(params, &self.state.ostk_dir)
    }

    fn dispatch_pitchfork(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let query = arguments
            .get("query")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        pitchfork::handle(query, &self.state.ostk_dir)
    }

    async fn dispatch_context_search(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let query = arguments
            .get("query")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let agent = self.get_agent_alias().await;
        context_search::handle(query, &self.state.ostk_dir, agent.as_deref())
    }

    async fn dispatch_context_release(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let before_turn = arguments
            .get("before_turn")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let reason = arguments
            .get("reason")
            .and_then(|v| v.as_str());
        let agent = self.get_agent_alias().await;
        context_release::handle(before_turn, reason, &self.state.ostk_dir, agent.as_deref())
    }

    // fcp-web dispatch helpers — route to built-in Rust handlers
    async fn dispatch_web_read(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let url = arguments.get("url").and_then(|v| v.as_str()).unwrap_or("");
        if url.is_empty() {
            return Err(ToolError::invalid_params("url is required"));
        }
        match crate::fcp::web::web_read(url).await {
            Ok(text) => Ok(json!({ "content": text })),
            Err(e) => Err(ToolError::new(-32000, e)),
        }
    }

    async fn dispatch_web_links(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let url = arguments.get("url").and_then(|v| v.as_str()).unwrap_or("");
        if url.is_empty() {
            return Err(ToolError::invalid_params("url is required"));
        }
        match crate::fcp::web::web_links(url).await {
            Ok(text) => Ok(json!({ "content": text })),
            Err(e) => Err(ToolError::new(-32000, e)),
        }
    }

    async fn dispatch_web_status(
        &self,
        arguments: serde_json::Value,
    ) -> Result<serde_json::Value, ToolError> {
        let url = arguments.get("url").and_then(|v| v.as_str()).unwrap_or("");
        if url.is_empty() {
            return Err(ToolError::invalid_params("url is required"));
        }
        match crate::fcp::web::web_status(url).await {
            Ok(text) => Ok(json!({ "content": text })),
            Err(e) => Err(ToolError::new(-32000, e)),
        }
    }

    // →840/→842: Session wire protocol — routes session/* methods to daemon SessionManager.
    async fn handle_session(&self, request: JsonRpcRequest) -> JsonRpcResponse {
        let method = request.method.clone();
        let params = request.params.clone().unwrap_or(serde_json::json!({}));

        // session/approve: Send approval decision through the session's response channel.
        // Falls back to the global approval bus for backward compat.
        if method == "session/approve" {
            let decision_str = params["decision"].as_str().unwrap_or("deny");
            let decision = match decision_str {
                "allow" => crate::kernel::approval::ApprovalDecision::Allow,
                "always_allow" => crate::kernel::approval::ApprovalDecision::AlwaysAllow,
                _ => crate::kernel::approval::ApprovalDecision::Deny,
            };
            // →904: Primary path: send through the bound session's response_tx.
            // Use the client's bound session (not mgr.active) to avoid cross-contamination.
            let sent_via_channel = if let Some(ref mgr_lock) = self.state.session_manager {
                let mgr = mgr_lock.lock().await;
                let session_name = self.resolve_session("scheduler");
                let response = crate::cpu::agent_loop::AgentResponse::Approval(decision);
                mgr.sessions()
                    .get(&session_name)
                    .map(|s| s.response_tx.try_send(response).is_ok())
                    .unwrap_or(false)
            } else {
                false
            };
            // Fallback: global approval bus (legacy)
            if !sent_via_channel {
                let id = params["id"].as_str().unwrap_or("");
                crate::kernel::approval::respond(id, decision);
            }
            return JsonRpcResponse {
                jsonrpc: "2.0".into(), id: request.id,
                result: Some(json!({"ok": true, "decision": decision_str})),
                error: None,
            };
        }

        // Check if daemon has a SessionManager
        let mgr_lock = match &self.state.session_manager {
            Some(m) => m,
            None => {
                let code = if matches!(method.as_str(),
                    "session/bind" | "session/dispatch" | "session/events" | "session/list"
                    | "session/switch" | "session/clear" | "session/reboot"
                    | "session/status" | "session/cancel" | "session/redirect"
                    | "session/spawn_agent"
                ) { ERR_INTERNAL } else { ERR_METHOD_NOT_FOUND };
                return JsonRpcResponse {
                    jsonrpc: "2.0".into(), id: request.id, result: None,
                    error: Some(JsonRpcError {
                        code,
                        message: "session manager not available — start daemon with `ostk listen`".into(),
                        data: None,
                    }),
                };
            }
        };

        let result: Result<serde_json::Value, String> = match method.as_str() {
            // →904: Bind this client connection to a specific session.
            // Must be called before session/dispatch for clean per-client isolation.
            // Backward compatible: clients that never call session/bind get auto-bound
            // on the first session/dispatch call.
            "session/bind" => {
                let name = params["name"].as_str().unwrap_or("scheduler");
                self.bind_session(name);
                let mut mgr = mgr_lock.lock().await;
                mgr.ensure_session(name);
                Ok(json!({"bound": name, "client_id": self.client_id}))
            }
            "session/dispatch" => {
                let requested = params["session_name"].as_str().unwrap_or("scheduler");
                let input = params["input"].as_str().unwrap_or("");
                // →957: Store last user input for pre-dispatch intent resolution.
                if !input.is_empty() {
                    let mut guard = self.state.last_user_input.write().await;
                    *guard = Some(input.to_string());
                }
                // →904: Auto-bind on first dispatch if no explicit session/bind was called.
                // This preserves backward compatibility — clients that never call session/bind
                // get the same behaviour as before (bound to the requested session).
                {
                    let mut guard = self.bound_session.lock().unwrap();
                    if guard.is_none() {
                        *guard = Some(requested.to_string());
                    }
                }
                let name = self.resolve_session(requested);
                let mut mgr = mgr_lock.lock().await;
                // →904: Ensure the bound session exists without changing mgr.active.
                mgr.ensure_session(&name);
                // →823: Inject any images sent over the wire into pending_images
                if let Some(images) = params.get("images").and_then(|v| v.as_array())
                    && let Some(session) = mgr.sessions_mut().get_mut(&name) {
                        for img_val in images {
                            if let Ok(block) = serde_json::from_value::<crate::cpu::anthropic::ContentBlock>(img_val.clone()) {
                                session.pending_images.push(block);
                            }
                        }
                    }
                // →827: Approval forwarding now works over the wire protocol.
                // session/events includes pending approvals, session/approve responds.
                // No need to force Auto mode — the TUI shows the modal and responds.
                if mgr.client.is_none() {
                    Err("no API client available (check API key)".into())
                } else {
                    mgr.dispatch_to(&name, input);
                    Ok(json!({"ok": true, "session": name, "client_id": self.client_id}))
                }
            }
            "session/events" => {
                let requested = params["session_name"].as_str().unwrap_or("scheduler");
                let name = self.resolve_session(requested);
                let mut mgr = mgr_lock.lock().await;
                let events = mgr.drain_events_from(&name);
                // Extract ApprovalNeeded from events — populate "approval" field
                // so the TUI can show the overlay. The event itself is filtered out
                // of the events array (TUI handles it via the approval field).
                let mut approval: Option<serde_json::Value> = None;
                let mut serialized: Vec<serde_json::Value> = Vec::new();
                for ev in &events {
                    if let crate::cpu::agent_loop::CpuEvent::ApprovalNeeded { tool_name, tool_input_preview } = ev {
                        approval = Some(json!({
                            "id": format!("daemon-{}", crate::now_iso()),
                            "agent_alias": name,
                            "tool_name": tool_name,
                            "tool_input_preview": tool_input_preview,
                        }));
                    } else if let Ok(v) = serde_json::to_value(ev) {
                        serialized.push(v);
                    }
                }
                // Fallback: also check global approval bus (legacy daemon path)
                if approval.is_none() {
                    approval = crate::kernel::approval::next_pending()
                        .map(|req| json!({
                            "id": req.id,
                            "agent_alias": req.agent_alias,
                            "tool_name": req.tool_name,
                            "tool_input_preview": req.tool_input_preview,
                        }));
                }
                let mut result = json!({"events": serialized});
                if let Some(a) = approval {
                    result["approval"] = a;
                }
                Ok(result)
            }
            // session/approve handled above (before session manager check)
            "session/list" => {
                let mgr = mgr_lock.lock().await;
                let sessions: Vec<serde_json::Value> = mgr.list_sessions().iter()
                    .map(|(name, count, busy)| json!({
                        "name": name, "message_count": count, "is_busy": busy,
                    }))
                    .collect();
                Ok(json!({"sessions": sessions}))
            }
            "session/switch" => {
                let name = params["session_name"].as_str().unwrap_or("scheduler");
                let mut mgr = mgr_lock.lock().await;
                mgr.switch(name);
                Ok(json!({"ok": true, "active": name}))
            }
            "session/status" => {
                // →904: Report status of the bound session, not mgr.active.
                let name = self.resolve_session("scheduler");
                let mut mgr = mgr_lock.lock().await;
                mgr.ensure_session(&name);
                let session = mgr.sessions_mut().get_mut(&name).expect("ensured above");
                let busy = session.is_busy();
                // Clear stale outbox on status query — prevents auto-dispatch of
                // messages queued by a previous TUI session.
                if !busy {
                    session.outbox.clear();
                }
                Ok(json!({"busy": busy, "session": name}))
            }
            "session/clear" => {
                let requested = params["session_name"].as_str().unwrap_or("scheduler");
                let name = self.resolve_session(requested);
                let mut mgr = mgr_lock.lock().await;
                // →904: Clear the bound session directly without changing mgr.active.
                mgr.ensure_session(&name);
                if let Some(session) = mgr.sessions_mut().get_mut(&name) {
                    session.clear();
                }
                Ok(json!({"ok": true}))
            }
            "session/reboot" => {
                // →904: Reboot the bound session. session/reboot is only meaningful
                // for the caller's own session, not the shared active.
                let name = self.resolve_session("scheduler");
                let mut mgr = mgr_lock.lock().await;
                // Temporarily switch to the bound session to reuse reboot_active(),
                // then restore the original active session.
                let prev_active = mgr.active.clone();
                if mgr.active != name {
                    mgr.switch(&name);
                }
                mgr.reboot_active();
                if prev_active != name {
                    mgr.switch(&prev_active);
                }
                Ok(json!({"ok": true}))
            }
            "session/cancel" => {
                // Cancel the running agent in the bound session.
                // Aborts the tokio task and clears busy flag so the TUI
                // can transition to Idle and accept new input.
                let name = self.resolve_session("scheduler");
                let mut mgr = mgr_lock.lock().await;
                mgr.ensure_session(&name);
                if let Some(session) = mgr.sessions_mut().get_mut(&name) {
                    session.cancel();
                }
                Ok(json!({"ok": true, "session": name}))
            }
            "session/redirect" => {
                // →plan Phase 3: Cooperative cancel + re-dispatch.
                // cancel() sets the cooperative flag (no hard abort). The agent
                // loop notices the flag at its next checkpoint and exits cleanly
                // with TurnComplete. A short delay ensures the old task has
                // exited before the new dispatch starts.
                let text = params["input"].as_str().unwrap_or("");
                let name = self.resolve_session("scheduler");
                let mut mgr = mgr_lock.lock().await;
                mgr.ensure_session(&name);
                if let Some(session) = mgr.sessions_mut().get_mut(&name) {
                    session.cancel(); // sets cancel_flag, not abort
                }
                if mgr.client.is_some() && !text.is_empty() {
                    // Brief yield for cooperative shutdown — agent loop checks
                    // cancel_flag every ~10ms during streaming and at turn start.
                    drop(mgr);
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                    let mut mgr = mgr_lock.lock().await;
                    mgr.dispatch_to(&name, text);
                    Ok(json!({"ok": true, "session": name, "redirected": true}))
                } else if text.is_empty() {
                    Ok(json!({"ok": true, "session": name, "redirected": false}))
                } else {
                    Err("no API client available (check API key)".into())
                }
            }
            "session/spawn_agent" => {
                // →plan Phase 4: Spawn a background agent as a managed session.
                let name = params["name"].as_str().unwrap_or("").to_string();
                let model = params["model"].as_str().unwrap_or("sonnet").to_string();
                let budget = params["budget"].as_str().unwrap_or("2").to_string();
                let prompt = params["prompt"].as_str().unwrap_or("").to_string();
                if name.is_empty() || prompt.is_empty() {
                    return JsonRpcResponse {
                        jsonrpc: "2.0".into(), id: request.id, result: None,
                        error: Some(JsonRpcError {
                            code: ERR_INVALID_PARAMS,
                            message: "name and prompt are required".into(),
                            data: None,
                        }),
                    };
                }
                let mut mgr = mgr_lock.lock().await;
                match mgr.spawn_agent(&name, &model, &budget, &prompt) {
                    Ok(()) => Ok(json!({"ok": true, "name": name, "model": model})),
                    Err(e) => Err(e),
                }
            }
            _ => Err(format!("unknown session method: {method}")),
        };

        match result {
            Ok(value) => JsonRpcResponse {
                jsonrpc: "2.0".into(), id: request.id, result: Some(value), error: None,
            },
            Err(msg) => JsonRpcResponse {
                jsonrpc: "2.0".into(), id: request.id, result: None,
                error: Some(JsonRpcError { code: ERR_INTERNAL, message: msg, data: None }),
            },
        }
    }
}

// ── Worktree path remapping (→1225) ──────────────────────────────────────────

/// If `project_root` is a git worktree, return the main checkout path.
/// Returns None when running in the main checkout (no remapping needed).
/// Git worktrees have .git as a FILE containing "gitdir: /path/.git/worktrees/name".
fn find_main_checkout_from_worktree(project_root: &std::path::Path) -> Option<std::path::PathBuf> {
    let git_path = project_root.join(".git");
    if !git_path.is_file() {
        return None;
    }
    let content = std::fs::read_to_string(&git_path).ok()?;
    let gitdir = content.trim().strip_prefix("gitdir:")?.trim();
    // gitdir = /path/to/main/.git/worktrees/name
    // main checkout = gitdir.parent().parent().parent()
    let gitdir_path = std::path::PathBuf::from(gitdir);
    let main_checkout = gitdir_path.parent()?.parent()?.parent()?;
    Some(main_checkout.to_path_buf())
}

/// Remap absolute main-checkout paths in fs_ops params to the worktree equivalent.
fn remap_fs_ops_paths(
    mut params: FsOpsParams,
    project_root: &std::path::Path,
    main_root: &std::path::Path,
) -> FsOpsParams {
    params.path = params.path.take().map(|p| remap_path_str(&p, project_root, main_root));
    if let Some(ref mut ops) = params.ops {
        for op in ops.iter_mut() {
            let path_str = op.get("path").and_then(|p| p.as_str()).map(str::to_string);
            if let Some(path_str) = path_str {
                let remapped = remap_path_str(&path_str, project_root, main_root);
                if remapped != path_str {
                    if let Some(obj) = op.as_object_mut() {
                        obj.insert("path".to_string(), serde_json::Value::String(remapped));
                    }
                }
            }
        }
    }
    params
}

/// Remap a single path string if it's absolute and points to the main checkout.
fn remap_path_str(
    path: &str,
    project_root: &std::path::Path,
    main_root: &std::path::Path,
) -> String {
    let p = std::path::Path::new(path);
    if !p.is_absolute() || p.starts_with(project_root) {
        return path.to_string();
    }
    if let Ok(rel) = p.strip_prefix(main_root) {
        let remapped = project_root.join(rel);
        eprintln!(
            "[ostk] fs_ops path remapped (→1225): {} -> {}",
            path,
            remapped.display()
        );
        return remapped.to_string_lossy().into_owned();
    }
    path.to_string()
}

/// Format tool result as text for the MCP content response.
fn format_tool_text(tool_name: &str, result: &serde_json::Value) -> String {
    match tool_name {
        "shell" | "sh_run" | "bash" => {
            let exit_code = result["exit_code"].as_i64().unwrap_or(0);
            let duration_ms = result["duration_ms"].as_u64().unwrap_or(0);
            let output = result["output"].as_str().unwrap_or("");
            let symbol = if exit_code == 0 { "+" } else { "!" };
            let elapsed = format_elapsed(duration_ms);
            format!("{} exit:{} {}\n{}", symbol, exit_code, elapsed, output)
        }
        "spawn" | "sh_spawn" => {
            let alias = result["alias"].as_str().unwrap_or("?");
            let pid = result["pid"].as_u64().unwrap_or(0);
            let state = result["state"].as_str().unwrap_or("?");
            let wait_matched = result["wait_matched"].as_bool().unwrap_or(false);
            let symbol = if wait_matched { "+" } else { "~" };
            let mut text = format!("{} spawned {} pid:{} {}", symbol, alias, pid, state);
            if let Some(line) = result["match_line"].as_str() {
                text.push('\n');
                text.push_str(line);
            }
            if let Some(tail) = result["output_tail"].as_str()
                && !tail.is_empty()
            {
                text.push('\n');
                text.push_str(tail);
            }
            if let Some(reason) = result["reason"].as_str() {
                text.push_str(&format!("\n~ {}", reason));
            }
            text
        }
        "interact" | "sh_interact" => {
            let alias = result["alias"].as_str().unwrap_or("?");
            let action = result["action"].as_str().unwrap_or("?");
            let state = result["state"].as_str().unwrap_or("?");
            match action {
                "read_tail" => {
                    let lines_returned = result["lines_returned"].as_u64().unwrap_or(0);
                    let output = result["output"].as_str().unwrap_or("");
                    let mut text =
                        format!("+ {} {} {} lines {}", alias, action, lines_returned, state);
                    if !output.is_empty() {
                        text.push('\n');
                        text.push_str(output);
                    }
                    text
                }
                "send_input" => {
                    let bytes = result["bytes_written"].as_u64().unwrap_or(0);
                    format!("+ {} send_input {}B {}", alias, bytes, state)
                }
                "kill" => format!("- {} killed {}", alias, state),
                "status" => {
                    let pid = result["pid"].as_u64().unwrap_or(0);
                    let elapsed_ms = result["elapsed_ms"].as_u64().unwrap_or(0);
                    let elapsed = format_elapsed(elapsed_ms);
                    format!("+ {} status {} pid:{} {}", alias, state, pid, elapsed)
                }
                _ => serde_json::to_string_pretty(result).unwrap_or_default(),
            }
        }
        "session" | "sh_session" => {
            if let Some(sessions) = result.get("sessions") {
                let mut lines = vec!["+ session list".to_string()];
                if let Some(arr) = sessions.as_array() {
                    for s in arr {
                        let name = s["session"].as_str().unwrap_or("?");
                        let cwd = s["cwd"].as_str().unwrap_or("?");
                        lines.push(format!("  {} {}", name, cwd));
                    }
                }
                lines.join("\n")
            } else {
                serde_json::to_string_pretty(result).unwrap_or_default()
            }
        }
        "help" | "sh_help" => result["text"]
            .as_str()
            .unwrap_or("ostk help")
            .to_string(),
        "fs_ops" | "fs_read" | "read" | "file:edit" | "file:read" | "file:write" => result["text"].as_str().unwrap_or("").to_string(),
        "ostk_pitchfork" | "pitchfork" | "search" | "ostk_context_search" | "context_search"
        | "ostk_context_release" | "context_release" => result["text"].as_str().unwrap_or("").to_string(),
        "tack" => {
            let resolved = result["resolved"].as_bool().unwrap_or(false);
            let verb = result["verb"].as_str().unwrap_or("?");
            let intent = result["intent"].as_str().unwrap_or("?");
            if resolved {
                let cmd = result["command"].as_str().unwrap_or("?");
                let args = result["args"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|v| v.as_str())
                            .collect::<Vec<_>>()
                            .join(" ")
                    })
                    .unwrap_or_default();
                let source = result["source"].as_str().unwrap_or("?");
                if args.is_empty() {
                    format!("+ tack {verb} → {cmd} [{intent}] ({source})")
                } else {
                    format!("+ tack {verb} → {cmd} {args} [{intent}] ({source})")
                }
            } else {
                let suggestions = result["suggestions"]
                    .as_array()
                    .map(|a| {
                        a.iter()
                            .filter_map(|v| v.as_str())
                            .collect::<Vec<_>>()
                            .join(", ")
                    })
                    .unwrap_or_default();
                if suggestions.is_empty() {
                    format!("? tack {verb} — unrecognized [{intent}]")
                } else {
                    format!("? tack {verb} — unrecognized [{intent}] did you mean: {suggestions}")
                }
            }
        }
        _ => serde_json::to_string_pretty(result).unwrap_or_default(),
    }
}

fn format_elapsed(ms: u64) -> String {
    if ms < 1000 {
        format!("{}ms", ms)
    } else if ms < 60_000 {
        format!("{:.1}s", ms as f64 / 1000.0)
    } else {
        format!("{}m{}s", ms / 60_000, (ms % 60_000) / 1000)
    }
}

// ----- Tool Definitions -----

const OSTK_INSTRUCTIONS: &str = r#"ostk — coordination kernel. Use bash/spawn/interact instead of Bash. Output is compressed, file state is tracked, conflicts resolve automatically.

## File I/O (replaces Read / Edit / Write)
  read(path="src/main.rs")                       — read file (gen_table tracked, elision-aware)
  read(path="src/main.rs", offset=10, limit=20)  — read lines 10-30
  fs_ops(path="src/main.rs", new_str="...")       — create / overwrite a file
  fs_ops(path="src/main.rs", old_str="...", new_str="...") — CAS str_replace edit
  fs_ops(ops=[{method: "file.str_replace" | "file.read" | "file.write", ...}]) — batch
  fs_ops(path="src/new_dir", op="mkdir")          — mkdir, mv, cp, rm, chmod

## Shell (replaces Bash)
  bash(cmd="cargo test")                          — run command, compressed output
  bash(cmd="git diff HEAD~1")                     — any shell command
  bash(cmd="cargo test", raw=true)                — skip compression (use for test output)
  bash(cmd="...", cwd="subdir")                   — set working directory

## Background processes (spawn + interact)
  spawn(alias="server", cmd="npm run dev", wait_for="ready on port 3000")
  interact(alias="server", action="read_tail", lines=20)
  interact(alias="server", action="kill")

## Search (replaces Grep / Glob)
  search(query="fn main")                         — grep content (default)
  search(query="*.rs", mode="files")              — find files by glob pattern

## Sessions and locks
  session(action="list")
  lock(action="create", name="task-123")
  lock(action="release", name="task-123")
  lock(action="watch", name="task-123")

## Intent and help
  tack(input=":compile")                         — resolve tack grammar to ostk commands
  help()                                         — list available tools

## Boot protocol (do this first, every session)
  bash(cmd="ostk boot")                          — read .ostk/ state, run POST checks

Do not fall back to Bash if a command fails — check syntax and retry with ostk tools."#;

/// →1157 Phase 5: Generate tool definitions from .language.
///
/// Replaces 200 lines of hardcoded JSON schemas with a single .language read.
/// Process primitives (interact, session, lock) get enriched schemas via
/// kernel::schema::enriched_schema(). Everything else uses signature parsing.
///
/// Falls back to a minimal hardcoded set if .language is unavailable (first boot).
fn tool_definitions() -> Vec<ToolDefinition> {
    if let Ok(root) = crate::find_project_root() {
        let entries = crate::language::parse_language_file(&root).unwrap_or_default();
        if !entries.is_empty() {
            let mut tools = crate::kernel::schema::tool_definitions_from_language(&entries, 0.45);
            // Ensure core MCP primitives are always present even if
            // .language doesn't have them yet (pre-boot state).
            let names: Vec<String> = tools.iter().map(|t| t.name.clone()).collect();
            for fallback in tool_definitions_fallback() {
                if !names.iter().any(|n| n == &fallback.name) {
                    tools.push(fallback);
                }
            }
            return tools;
        }
    }

    tool_definitions_fallback()
}

/// Hardcoded fallback for first boot / pre-.language state.
fn tool_definitions_fallback() -> Vec<ToolDefinition> {
    vec![
        ToolDefinition {
            name: "shell".to_string(),
            description: "Execute a command and return output (replaces Bash). Alias: sh_run. CWD defaults to project root.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "cmd": { "type": "string", "description": "Command to execute" },
                    "timeout": { "type": "integer", "description": "Seconds before kill", "default": 300 },
                    "raw": { "type": "boolean", "description": "Skip compression", "default": false },
                    "cwd": { "type": "string", "description": "Working directory override. Defaults to project root." }
                },
                "required": ["cmd"]
            }),
        },
        ToolDefinition {
            name: "spawn".to_string(),
            description: "Start a background process. Alias: sh_spawn. Optionally wait for a regex match before returning.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "alias": { "type": "string", "description": "Name for the process" },
                    "cmd": { "type": "string", "description": "Command to run" },
                    "wait_for": { "type": "string", "description": "Regex to wait for in output" },
                    "timeout": { "type": "integer", "description": "Seconds to wait for match", "default": 30 }
                },
                "required": ["alias", "cmd"]
            }),
        },
        ToolDefinition {
            name: "interact".to_string(),
            description: "Interact with a background process: read output, send input, signal, kill, status. Alias: sh_interact.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "alias": { "type": "string", "description": "Process alias" },
                    "action": { "type": "string", "enum": ["read_tail", "send_input", "send_signal", "kill", "status"], "description": "Action to perform" },
                    "input": { "type": "string", "description": "Input to send (for send_input/send_signal)" },
                    "lines": { "type": "integer", "description": "Number of lines to read (for read_tail)", "default": 50 },
                    "timeout": { "type": "integer", "description": "Timeout in seconds" }
                },
                "required": ["alias", "action"]
            }),
        },
        ToolDefinition {
            name: "session".to_string(),
            description: "Session management: list or close sessions. Alias: sh_session.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "action": { "type": "string", "enum": ["list", "create", "close"], "description": "Action" },
                    "name": { "type": "string", "description": "Session name (for create/close)" }
                },
                "required": ["action"]
            }),
        },
        ToolDefinition {
            name: "lock".to_string(),
            description: "Coordination locks for agent orchestration. Alias: sh_lock.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "action": { "type": "string", "enum": ["create", "release", "watch", "status"], "description": "Lock action" },
                    "name": { "type": "string", "description": "Lock name" },
                    "timeout": { "type": "integer", "description": "Watch timeout in seconds", "default": 300 }
                },
                "required": ["action", "name"]
            }),
        },
        ToolDefinition {
            name: "help".to_string(),
            description: "Show available tools and usage. Alias: sh_help.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {}
            }),
        },
        ToolDefinition {
            name: "tack".to_string(),
            description: "Resolve tack grammar to ostk commands. fcp-ostk device driver for human intent. Input: ':verb', '.? query', '→NNN', ':: target'.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": "Tack expression. Examples: ':compile', ':show needles', '.? status', '→437', ':: agent1 fix build'"
                    }
                },
                "required": ["input"]
            }),
        },
        // fcp-web: built-in web tools
        ToolDefinition {
            name: "web_read".to_string(),
            description: "Read a web page and extract its text content. Returns cleaned text with metadata.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "url": { "type": "string", "description": "URL to read" }
                },
                "required": ["url"]
            }),
        },
        ToolDefinition {
            name: "web_links".to_string(),
            description: "Extract all links from a web page.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "url": { "type": "string", "description": "URL to extract links from" }
                },
                "required": ["url"]
            }),
        },
        ToolDefinition {
            name: "web_status".to_string(),
            description: "Check HTTP status and headers for a URL.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "url": { "type": "string", "description": "URL to check" }
                },
                "required": ["url"]
            }),
        },
        // →963: pitchfork — search across all kernel state
        ToolDefinition {
            name: "ostk_pitchfork".to_string(),
            description: "Search across ALL kernel state by keyword — decisions, needles, docs, audit. The demand-paging tool: 'find everything the kernel knows about X.'".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "Keyword to search for across kernel state" }
                },
                "required": ["query"]
            }),
        },
        // →964: context_search — search within the current agent's session transcript
        ToolDefinition {
            name: "ostk_context_search".to_string(),
            description: "Search within the current agent's session transcript. Find previous turns by keyword.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "Keyword to search for in session history" }
                },
                "required": ["query"]
            }),
        },
        // →965: context_release — signal that the model has processed turns before a given number
        ToolDefinition {
            name: "ostk_context_release".to_string(),
            description: "Signal that turns before a given number have been processed and can be released. Bookkeeping for context management.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "before_turn": { "type": "integer", "description": "Release context before this turn number" },
                    "reason": { "type": "string", "description": "Why this context is being released (optional)" }
                },
                "required": ["before_turn"]
            }),
        },
        // →1152: kernel tools missing from fallback — agents call these by name per CLAUDE.md routing table.
        // bash/read/fs_ops/search were advertised in OSTK_INSTRUCTIONS but absent from tools/list,
        // causing mcp__ostk__bash/read/fs_ops/search calls to return unknown-tool errors.
        ToolDefinition {
            name: "bash".to_string(),
            description: "Execute a shell command (replaces Bash). Alias: shell. CWD defaults to project root.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "cmd": { "type": "string", "description": "Command to execute" },
                    "timeout": { "type": "integer", "description": "Seconds before kill", "default": 300 },
                    "raw": { "type": "boolean", "description": "Skip compression (use for test output)", "default": false },
                    "cwd": { "type": "string", "description": "Working directory override. Defaults to project root." }
                },
                "required": ["cmd"]
            }),
        },
        ToolDefinition {
            name: "read".to_string(),
            description: "Read a file (replaces Read). gen_table tracked, elision-aware. Returns [304] on redundant reads.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "File path to read" },
                    "offset": { "type": "integer", "description": "Start line (0-based). Lines offset..(offset+limit) are returned." },
                    "limit": { "type": "integer", "description": "Number of lines to read", "default": 100 }
                },
                "required": ["path"]
            }),
        },
        ToolDefinition {
            name: "fs_ops".to_string(),
            description: "File operations: create, edit, or batch-mutate files (replaces Edit/Write). CAS str_replace with OCC conflict detection.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Target file path (quick mode)" },
                    "old_str": { "type": "string", "description": "Text to find and replace (omit for create/overwrite)" },
                    "new_str": { "type": "string", "description": "Replacement text or full file content" },
                    "replace_all": { "type": "boolean", "description": "Replace every occurrence", "default": false },
                    "ops": { "type": "array", "description": "Batch mode: array of {method, path, ...} operations", "items": { "type": "object" } },
                    "op": { "type": "string", "description": "File system op: mkdir, mv, cp, rm, chmod" }
                }
            }),
        },
        ToolDefinition {
            name: "search".to_string(),
            description: "Search across kernel state by keyword: decisions, needles, docs, audit (replaces Grep/Glob). Use mode='files' for glob, mode='content' for grep.".to_string(),
            input_schema: json!({
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "Search keyword or glob pattern" },
                    "mode": { "type": "string", "description": "Search mode: content (default), files, semantic", "default": "content" },
                    "scope": { "type": "string", "description": "Search scope: code, files, work, history, decisions, transcripts, all" }
                },
                "required": ["query"]
            }),
        },
    ]
}

// Legacy hardcoded tool_definitions above serves as first-boot fallback.
// After first boot, .language is populated and tool_definitions() returns
// generated schemas from .language entries.

#[cfg(test)]
mod tests {
    use super::*;

    // ── format_tool_text: sh_run ──

    #[test]
    fn format_tool_text_sh_run_success() {
        let result = serde_json::json!({
            "exit_code": 0,
            "duration_ms": 150,
            "output": "hello",
        });
        let text = format_tool_text("sh_run", &result);
        assert!(text.starts_with("+"));
        assert!(text.contains("exit:0"));
        assert!(text.contains("150ms"));
        assert!(text.contains("hello"));
    }

    #[test]
    fn format_tool_text_sh_run_failure() {
        let result = serde_json::json!({
            "exit_code": 1,
            "duration_ms": 2500,
            "output": "error",
        });
        let text = format_tool_text("sh_run", &result);
        assert!(text.starts_with("!"));
        assert!(text.contains("exit:1"));
        assert!(text.contains("2.5s"));
    }

    // ── format_tool_text: sh_spawn ──

    #[test]
    fn format_tool_text_sh_spawn_matched() {
        let result = serde_json::json!({
            "alias": "server",
            "pid": 1234,
            "state": "running",
            "wait_matched": true,
            "match_line": "Server ready on port 3000",
        });
        let text = format_tool_text("sh_spawn", &result);
        assert!(text.starts_with("+"));
        assert!(text.contains("server"));
        assert!(text.contains("pid:1234"));
        assert!(text.contains("Server ready"));
    }

    #[test]
    fn format_tool_text_sh_spawn_no_match() {
        let result = serde_json::json!({
            "alias": "bg",
            "pid": 555,
            "state": "running",
            "wait_matched": false,
            "reason": "timeout after 30s",
            "output_tail": "still starting...",
        });
        let text = format_tool_text("sh_spawn", &result);
        assert!(text.starts_with("~"));
        assert!(text.contains("timeout"));
        assert!(text.contains("still starting"));
    }

    // ── format_tool_text: sh_interact ──

    #[test]
    fn format_tool_text_sh_interact_read_tail() {
        let result = serde_json::json!({
            "alias": "worker",
            "action": "read_tail",
            "lines_returned": 10,
            "output": "line1\nline2",
            "state": "running",
        });
        let text = format_tool_text("sh_interact", &result);
        assert!(text.contains("worker"));
        assert!(text.contains("10 lines"));
        assert!(text.contains("line1"));
    }

    #[test]
    fn format_tool_text_sh_interact_send_input() {
        let result = serde_json::json!({
            "alias": "repl",
            "action": "send_input",
            "bytes_written": 5,
            "state": "running",
        });
        let text = format_tool_text("sh_interact", &result);
        assert!(text.contains("send_input"));
        assert!(text.contains("5B"));
    }

    #[test]
    fn format_tool_text_sh_interact_kill() {
        let result = serde_json::json!({
            "alias": "proc",
            "action": "kill",
            "state": "killed",
        });
        let text = format_tool_text("sh_interact", &result);
        assert!(text.contains("killed"));
    }

    #[test]
    fn format_tool_text_sh_interact_status() {
        let result = serde_json::json!({
            "alias": "bg",
            "action": "status",
            "state": "running",
            "pid": 999,
            "elapsed_ms": 65000,
        });
        let text = format_tool_text("sh_interact", &result);
        assert!(text.contains("status"));
        assert!(text.contains("pid:999"));
        assert!(text.contains("1m5s"));
    }

    // ── format_tool_text: sh_session ──

    #[test]
    fn format_tool_text_sh_session_list() {
        let result = serde_json::json!({
            "sessions": [{"session": "main", "cwd": "/home"}]
        });
        let text = format_tool_text("sh_session", &result);
        assert!(text.contains("session list"));
        assert!(text.contains("main"));
    }

    // ── format_tool_text: fs_ops / fs_read ──

    #[test]
    fn format_tool_text_fs_ops() {
        let result = serde_json::json!({"text": "+ file:edit created file.rs gen=1"});
        let text = format_tool_text("fs_ops", &result);
        assert_eq!(text, "+ file:edit created file.rs gen=1");
    }

    #[test]
    fn format_tool_text_fs_read() {
        let result = serde_json::json!({"text": "[200] main.rs:gen=1\nhello"});
        let text = format_tool_text("fs_read", &result);
        assert!(text.contains("[200]"));
    }

    // ── format_tool_text: sh_help ──

    #[test]
    fn format_tool_text_sh_help() {
        let result = serde_json::json!({"text": "help text"});
        let text = format_tool_text("sh_help", &result);
        assert_eq!(text, "help text");
    }

    // ── format_tool_text: unknown tool ──

    #[test]
    fn format_tool_text_unknown() {
        let result = serde_json::json!({"foo": "bar"});
        let text = format_tool_text("unknown_tool", &result);
        // Falls back to pretty-printed JSON
        assert!(text.contains("foo"));
        assert!(text.contains("bar"));
    }

    // ── format_elapsed ──

    #[test]
    fn format_elapsed_ms() {
        assert_eq!(format_elapsed(50), "50ms");
        assert_eq!(format_elapsed(999), "999ms");
    }

    #[test]
    fn format_elapsed_seconds() {
        assert_eq!(format_elapsed(1000), "1.0s");
        assert_eq!(format_elapsed(2500), "2.5s");
        assert_eq!(format_elapsed(59999), "60.0s");
    }

    #[test]
    fn format_elapsed_minutes() {
        assert_eq!(format_elapsed(60000), "1m0s");
        assert_eq!(format_elapsed(90000), "1m30s");
        assert_eq!(format_elapsed(125000), "2m5s");
    }

    // ── tool_definitions ──

    #[test]
    fn tool_definitions_count() {
        let tools = tool_definitions();
        // →1157: tools now generated from .language (dynamic count).
        // Must have at least the core primitives.
        assert!(tools.len() >= 13, "should have at least 13 tools, got {}", tools.len());
    }

    #[test]
    fn tool_definitions_names() {
        let tools = tool_definitions();
        let names: Vec<&str> = tools.iter().map(|t| t.name.as_str()).collect();
        // Core primitives must always be present (registered at boot)
        for expected in ["shell", "spawn", "interact", "session", "lock", "tack", "help"] {
            assert!(names.contains(&expected), "missing core tool: {expected}");
        }
    }

    #[test]
    fn tool_definitions_all_have_input_schema() {
        let tools = tool_definitions();
        for tool in &tools {
            assert!(tool.input_schema.is_object(), "tool {} missing input_schema", tool.name);
        }
    }

    // ── McpDispatcher: method not found ──

    #[tokio::test]
    async fn dispatch_method_not_found() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "unknown/method".to_string(),
            params: None,
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_some());
        assert_eq!(response.error.unwrap().code, ERR_METHOD_NOT_FOUND);
    }

    // ── McpDispatcher: notification returns None ──

    #[tokio::test]
    async fn dispatch_notification_returns_none() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::Value::Null,
            method: "notifications/initialized".to_string(),
            params: None,
        };
        let response = dispatcher.dispatch(request).await;
        assert!(response.is_none());
    }

    // ── McpDispatcher: tools/call before initialize ──

    #[tokio::test]
    async fn dispatch_tools_call_before_init() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/call".to_string(),
            params: Some(serde_json::json!({"name": "sh_run", "arguments": {"cmd": "echo hi"}})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_some());
        assert_eq!(response.error.unwrap().code, ERR_INVALID_REQUEST);
    }

    // ── McpDispatcher: tools/list ──

    #[tokio::test]
    async fn dispatch_tools_list() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/list".to_string(),
            params: None,
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_none());
        let tools = response.result.unwrap()["tools"].as_array().unwrap().len();
        assert!(tools >= 13, "should have at least 13 tools, got {tools}");
    }

    // ── McpDispatcher: tools/call missing params ──

    #[tokio::test]
    async fn dispatch_tools_call_missing_params() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        // Initialize first
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/call".to_string(),
            params: None,
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_some());
        assert_eq!(response.error.unwrap().code, ERR_INVALID_PARAMS);
    }

    // ── McpDispatcher: tools/call missing name ──

    #[tokio::test]
    async fn dispatch_tools_call_missing_name() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/call".to_string(),
            params: Some(serde_json::json!({"arguments": {}})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_some());
        assert_eq!(response.error.unwrap().code, ERR_INVALID_PARAMS);
    }

    // ── McpDispatcher: tools/call unknown tool ──

    #[tokio::test]
    async fn dispatch_tools_call_unknown_tool() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/call".to_string(),
            params: Some(serde_json::json!({"name": "nonexistent_tool", "arguments": {}})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_some());
        assert_eq!(response.error.unwrap().code, ERR_METHOD_NOT_FOUND);
    }

    // →840: Session wire protocol dispatch tests

    #[tokio::test]
    async fn dispatch_session_before_init_returns_error() {
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ServerState::with_ostk_dir(dir.path().to_path_buf()));
        let dispatcher = McpDispatcher::new(state);
        // Don't initialize — session/ methods should fail

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "session/dispatch".to_string(),
            params: Some(serde_json::json!({"session_name": "test", "input": "hi"})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_some());
        assert_eq!(response.error.as_ref().unwrap().code, ERR_INVALID_REQUEST,
            "session/ before init should return ERR_INVALID_REQUEST");
    }

    #[tokio::test]
    async fn dispatch_session_known_method_returns_internal() {
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ServerState::with_ostk_dir(dir.path().to_path_buf()));
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        for method in ["session/dispatch", "session/events", "session/list",
                       "session/switch", "session/clear", "session/reboot"] {
            let request = JsonRpcRequest {
                jsonrpc: "2.0".to_string(),
                id: serde_json::json!(1),
                method: method.to_string(),
                params: Some(serde_json::json!({})),
            };
            let response = dispatcher.dispatch(request).await.unwrap();
            assert!(response.error.is_some(), "{method} should return error (stub)");
            assert_eq!(response.error.as_ref().unwrap().code, ERR_INTERNAL,
                "{method} should return ERR_INTERNAL (session manager not available)");
        }
    }

    #[tokio::test]
    async fn dispatch_unknown_session_method_returns_not_found() {
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ServerState::with_ostk_dir(dir.path().to_path_buf()));
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "session/nonexistent".to_string(),
            params: Some(serde_json::json!({})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_some());
        assert_eq!(response.error.unwrap().code, ERR_METHOD_NOT_FOUND,
            "unknown session/ method should return ERR_METHOD_NOT_FOUND");
    }

    // →823: Test image injection + →827 Auto mode via integration tests
    // (dispatch.rs tests can't create SessionManager inside #[tokio::test]
    // because with_handle calls block_on internally — use the integration
    // tests in client.rs instead which run on separate runtime threads)

    #[tokio::test]
    async fn dispatch_session_parses_image_params() {
        // Verify the image JSON structure is valid for ContentBlock deserialization
        let image_json = serde_json::json!({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "iVBORw0KGgo="
            }
        });
        let block: Result<crate::cpu::anthropic::ContentBlock, _> =
            serde_json::from_value(image_json);
        assert!(block.is_ok(), "image JSON should deserialize to ContentBlock: {:?}", block);
        match block.unwrap() {
            crate::cpu::anthropic::ContentBlock::Image { source } => {
                assert_eq!(source.media_type, "image/png");
                assert_eq!(source.data, "iVBORw0KGgo=");
                assert_eq!(source.source_type, "base64");
            }
            other => panic!("expected Image, got {:?}", other),
        }
    }

    // →827: Approval forwarding tests

    #[tokio::test]
    async fn dispatch_session_approve_responds() {
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ServerState::with_ostk_dir(dir.path().to_path_buf()));
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        // session/approve on a non-existent ID should return ok: false
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "session/approve".to_string(),
            params: Some(serde_json::json!({"id": "nonexistent", "decision": "allow"})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_none(), "approve should not error");
        // Approve always returns ok: true (best-effort delivery)
        assert_eq!(response.result.as_ref().unwrap()["ok"], true);
    }

    #[tokio::test]
    async fn dispatch_session_approve_accepts_all_decisions() {
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ServerState::with_ostk_dir(dir.path().to_path_buf()));
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        for decision in ["allow", "deny", "always_allow"] {
            let request = JsonRpcRequest {
                jsonrpc: "2.0".to_string(),
                id: serde_json::json!(1),
                method: "session/approve".to_string(),
                params: Some(serde_json::json!({"id": "test", "decision": decision})),
            };
            let response = dispatcher.dispatch(request).await.unwrap();
            assert!(response.error.is_none(), "approve({decision}) should not error");
            assert_eq!(response.result.as_ref().unwrap()["decision"], decision);
        }
    }

    #[tokio::test]
    async fn dispatch_session_approve_works_without_session_manager() {
        // session/approve falls back to global approval bus when no SessionManager.
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ServerState::with_ostk_dir(dir.path().to_path_buf()));
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "session/approve".to_string(),
            params: Some(serde_json::json!({"id": "x", "decision": "deny"})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        // Should succeed (not error) — falls back to approval bus
        assert!(response.error.is_none(), "session/approve should work without session manager");
        assert_eq!(response.result.as_ref().unwrap()["ok"], true);
    }

    #[tokio::test]
    async fn dispatch_no_auto_mode_override() {
        // →827: Verify daemon dispatch does NOT force Auto mode anymore
        // (approval forwarding replaces the workaround)
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().to_path_buf();
        std::fs::create_dir_all(crate::state_dir(&root)).unwrap();

        let _rt_handle = tokio::runtime::Handle::current();
        let _config = crate::cpu::agent_loop::LoopConfig {
            model: "test-model".into(),
            system_prompt: Some("test".into()),
            tools: vec![],
            max_tokens: 1024,
            max_turns: None,
            context_budget: None,
            permission_mode: crate::cpu::PermissionMode::Governed,
            fast_mode: false,
            root: Some(root.clone()),
            betas: vec![],
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: vec![],
        };

        // Can't create SessionManager inside tokio::test (block_on conflict)
        // but we can verify the dispatch code no longer references Auto mode
        let source = include_str!("dispatch.rs");
        let session_dispatch_section: String = source.lines()
            .skip_while(|l| !l.contains("\"session/dispatch\""))
            .take_while(|l| !l.contains("\"session/events\""))
            .collect::<Vec<_>>().join("\n");
        assert!(!session_dispatch_section.contains("PermissionMode::Auto"),
            "session/dispatch should NOT force Auto mode (→827 approval forwarding)");
    }

    // ── OSTK_INSTRUCTIONS: registered tool names ──

    #[test]
    fn ostk_instructions_advertises_registered_tools() {
        assert!(OSTK_INSTRUCTIONS.contains("read(path="),
            "instructions must advertise the registered 'read' tool");
        assert!(OSTK_INSTRUCTIONS.contains("fs_ops("),
            "instructions must advertise the registered 'fs_ops' tool");
        assert!(OSTK_INSTRUCTIONS.contains("bash(cmd="),
            "instructions must advertise the registered 'bash' tool");
        assert!(OSTK_INSTRUCTIONS.contains("search(query="),
            "instructions must advertise the registered 'search' tool");
    }

    #[test]
    fn ostk_instructions_no_unregistered_verbs() {
        assert!(!OSTK_INSTRUCTIONS.contains("fs_read("),
            "fs_read is not a registered MCP tool — use read(path=...)");
        assert!(!OSTK_INSTRUCTIONS.contains("fs_write("),
            "fs_write is not a registered MCP tool — use fs_ops(path=..., new_str=...)");
        assert!(!OSTK_INSTRUCTIONS.contains("edit(path="),
            "edit(path=...) is not a registered MCP tool — use fs_ops(path=..., old_str=..., new_str=...)");
    }

    // →1152: kernel tools must appear in tools/list so agents can call mcp__ostk__bash/read/fs_ops/search

    #[tokio::test]
    async fn kernel_tools_registered_in_tools_list() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/list".to_string(),
            params: None,
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(response.error.is_none());
        let tools = response.result.unwrap()["tools"].as_array().unwrap().clone();
        let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
        for expected in ["bash", "read", "fs_ops", "search"] {
            assert!(
                names.contains(&expected),
                "kernel tool missing from tools/list: {expected} — agents cannot call mcp__ostk__{expected}",
            );
        }
    }

    #[tokio::test]
    async fn dispatch_bash_tool_is_callable() {
        let state = Arc::new(ServerState::new());
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/call".to_string(),
            params: Some(serde_json::json!({"name": "bash", "arguments": {"cmd": "echo hi"}})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(
            response.error.as_ref().map(|e| e.code).unwrap_or(0) != ERR_METHOD_NOT_FOUND,
            "bash must not return unknown-tool error: {:?}", response.error,
        );
    }

    #[tokio::test]
    async fn dispatch_search_tool_is_callable() {
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ServerState::with_ostk_dir(dir.path().to_path_buf()));
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/call".to_string(),
            params: Some(serde_json::json!({"name": "search", "arguments": {"query": "test"}})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(
            response.error.as_ref().map(|e| e.code).unwrap_or(0) != ERR_METHOD_NOT_FOUND,
            "search must not return unknown-tool error: {:?}", response.error,
        );
    }

    #[tokio::test]
    async fn dispatch_read_tool_is_callable() {
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ServerState::with_ostk_dir(dir.path().to_path_buf()));
        let dispatcher = McpDispatcher::new(state);
        dispatcher.initialized.store(true, std::sync::atomic::Ordering::Relaxed);

        // Write a test file the handler can actually read
        let test_file = dir.path().parent().unwrap_or(dir.path()).join("test_read_dispatch.txt");
        std::fs::write(&test_file, "line1\nline2\n").unwrap();

        let request = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            method: "tools/call".to_string(),
            params: Some(serde_json::json!({"name": "read", "arguments": {"path": test_file.to_string_lossy()}})),
        };
        let response = dispatcher.dispatch(request).await.unwrap();
        assert!(
            response.error.as_ref().map(|e| e.code).unwrap_or(0) != ERR_METHOD_NOT_FOUND,
            "read must not return unknown-tool error: {:?}", response.error,
        );
    }
}
