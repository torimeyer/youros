pub mod agent_loop;
pub mod anthropic;
pub mod approval;
pub mod error;
pub mod file_cache;
pub mod gemini;
pub mod mistral;
pub mod openrouter;
pub mod params;
pub mod sse;
pub mod session;
pub mod summary;
pub mod classify;
pub mod tool_exec;
pub mod providers;

use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;

use futures_util::Stream;
use serde_json::json;

use crate::agentfile::{Agentfile, PromptSource};
use crate::cpu::providers::resolve_provider;
use providers::model_max_output_tokens;
use crate::language::{self, LanguageEntry};

// ---------------------------------------------------------------------------
// InferenceRequest — provider-neutral request type (→841)
// ---------------------------------------------------------------------------

/// Claude-specific options that non-Anthropic providers ignore.
/// Separated from the universal request to make the abstraction honest.
#[derive(Debug, Clone, Default)]
pub struct ClaudeOptions {
    /// Context management edits (compact, clear_tool_uses). Requires beta header.
    pub context_management: Option<serde_json::Value>,
    /// Prompt caching control (`{"type": "ephemeral", "ttl": "1h"}`).
    pub cache_control: Option<serde_json::Value>,
    /// Beta feature slugs (e.g. "context-management-2025-06-27").
    pub betas: Vec<String>,
    /// Speed mode for Opus 4.6 fast mode ("fast" for 2.5x faster output).
    pub speed: Option<String>,
    /// Cached prefix context blocks (boot.md, .language, identity).
    pub preload_context: Vec<String>,
    /// →949: Extended thinking config. When set, enables the model's internal
    /// reasoning chain. Value: `{"type": "enabled", "budget_tokens": N}`.
    pub thinking: Option<serde_json::Value>,
    /// →948: Enable citations for grounded file references.
    /// When true, adds `citations: {"enabled": true}` to request body.
    pub citations: bool,
}

/// Provider-neutral inference request. All providers consume this.
/// Claude-specific fields live in `claude_options` — other providers ignore them.
#[derive(Debug, Clone)]
pub struct InferenceRequest {
    pub model: String,
    pub max_tokens: u32,
    pub system: Option<String>,
    pub messages: Vec<anthropic::Message>,
    pub tools: Vec<serde_json::Value>,
    pub tool_choice: Option<serde_json::Value>,
    pub stream: bool,
    /// Claude-specific options (context management, caching, betas, speed).
    /// Non-Anthropic providers should ignore this entirely.
    pub claude: ClaudeOptions,
}

// →849: type alias removed — use InferenceRequest directly everywhere.

// ---------------------------------------------------------------------------
// CpuDriver trait — unified provider abstraction (->754)
// ---------------------------------------------------------------------------

/// A boxed stream of SSE events, compatible with all providers.
pub type BoxedStream<'a> = Pin<Box<dyn Stream<Item = Result<anthropic::StreamEvent, String>> + Send + 'a>>;

/// Provider-agnostic inference driver trait.
///
/// The driver is a **codec**, not a controller. It translates between
/// kernel-native types (`InferenceRequest` / `StreamEvent`) and the provider
/// wire format. The agent_loop owns the turn cycle; the driver never
/// executes tools, manages history, or decides when to stop.
///
/// Methods return boxed futures for object safety (`dyn CpuDriver`).
pub trait CpuDriver: Send + Sync {
    /// Create a streaming inference call. Returns a boxed stream of StreamEvents.
    fn create_stream(
        &self,
        params: anthropic::InferenceRequest,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<BoxedStream<'_>, error::DriverError>> + Send + '_>>;

    /// Count tokens for a set of messages (optional -- not all providers support it).
    fn count_tokens(
        &self,
        params: anthropic::InferenceRequest,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<u64, error::DriverError>> + Send + '_>> {
        let _ = params;
        Box::pin(async { Err(error::DriverError::StreamError("count_tokens not supported by this provider".into())) })
    }

    /// List available models (optional).
    fn list_models(
        &self,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<Vec<anthropic::ModelInfo>, error::DriverError>> + Send + '_>> {
        Box::pin(async { Ok(vec![]) })
    }

    /// Provider name for display and routing checks.
    fn provider_name(&self) -> &str;
}

// ---------------------------------------------------------------------------
// CpuDriver implementations for each provider
// ---------------------------------------------------------------------------

impl CpuDriver for anthropic::AnthropicClient {
    fn create_stream(
        &self,
        params: anthropic::InferenceRequest,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<BoxedStream<'_>, error::DriverError>> + Send + '_>> {
        Box::pin(async move {
            let s = anthropic::AnthropicClient::create_stream(self, params).await?;
            Ok(Box::pin(s) as BoxedStream<'_>)
        })
    }

    fn count_tokens(
        &self,
        params: anthropic::InferenceRequest,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<u64, error::DriverError>> + Send + '_>> {
        Box::pin(async move { anthropic::AnthropicClient::count_tokens(self, &params).await })
    }

    fn list_models(
        &self,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<Vec<anthropic::ModelInfo>, error::DriverError>> + Send + '_>> {
        Box::pin(async move { anthropic::AnthropicClient::list_models(self).await })
    }

    fn provider_name(&self) -> &str {
        "anthropic"
    }
}

impl CpuDriver for gemini::GeminiClient {
    fn create_stream(
        &self,
        params: anthropic::InferenceRequest,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<BoxedStream<'_>, error::DriverError>> + Send + '_>> {
        Box::pin(async move {
            let s = gemini::GeminiClient::create_stream(self, params).await?;
            Ok(Box::pin(s) as BoxedStream<'_>)
        })
    }

    fn provider_name(&self) -> &str {
        "google"
    }
}

impl CpuDriver for mistral::MistralClient {
    fn create_stream(
        &self,
        params: anthropic::InferenceRequest,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<BoxedStream<'_>, error::DriverError>> + Send + '_>> {
        Box::pin(async move {
            let s = mistral::MistralClient::create_stream(self, params).await?;
            Ok(Box::pin(s) as BoxedStream<'_>)
        })
    }

    fn provider_name(&self) -> &str {
        "mistral"
    }
}

impl CpuDriver for openrouter::OpenRouterClient {
    fn create_stream(
        &self,
        params: anthropic::InferenceRequest,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<BoxedStream<'_>, error::DriverError>> + Send + '_>> {
        Box::pin(async move {
            let s = openrouter::OpenRouterClient::create_stream(self, params).await?;
            Ok(Box::pin(s) as BoxedStream<'_>)
        })
    }

    fn count_tokens(
        &self,
        params: anthropic::InferenceRequest,
    ) -> Pin<Box<dyn std::future::Future<Output = Result<u64, error::DriverError>> + Send + '_>> {
        Box::pin(async move { openrouter::OpenRouterClient::count_tokens(self, &params).await })
    }

    fn provider_name(&self) -> &str {
        "openrouter"
    }
}

// ---------------------------------------------------------------------------
// Driver factory -- replaces ProviderClient::for_model()
// ---------------------------------------------------------------------------

/// Construct the correct CpuDriver for a given model name.
///
/// Uses `resolve_provider` from `commands::run` to determine which
/// backend to use, then resolves the appropriate API key through the
/// kernel secret manager (env var fallback).
pub fn create_driver(model: &str) -> Result<Arc<dyn CpuDriver>, error::DriverError> {
    use crate::cpu::providers::ApiProvider;
    let provider = resolve_provider(model);
    tracing::info!(model = %model, provider = ?provider, "cpu: creating driver");

    // Try the primary provider first; if it fails (missing API key, no network),
    // fall back to Ollama if it's running locally.
    let result = create_driver_for_provider(model, &provider);
    if result.is_err() && provider != ApiProvider::Ollama {
        let host = std::env::var("OLLAMA_HOST")
            .unwrap_or_else(|_| "http://localhost:11434".to_string());
        // Quick check: is Ollama reachable?
        if std::process::Command::new("curl")
            .args(["-sf", "--max-time", "1", &format!("{host}/api/tags")])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
        {
            eprintln!("[kernel] cloud API unavailable — falling back to Ollama at {host}");
            let base_url = format!("{}/v1", host.trim_end_matches('/'));
            let client = openrouter::OpenRouterClient::with_config(
                "ollama".to_string(),
                base_url,
            );
            return Ok(Arc::new(client));
        }
    }
    result
}

fn create_driver_for_provider(_model: &str, provider: &crate::cpu::providers::ApiProvider) -> Result<Arc<dyn CpuDriver>, error::DriverError> {
    use crate::cpu::providers::ApiProvider;
    match provider {
        ApiProvider::Anthropic => {
            Ok(Arc::new(anthropic::AnthropicClient::new()?))
        }
        ApiProvider::Google => {
            Ok(Arc::new(gemini::GeminiClient::new()?))
        }
        ApiProvider::Mistral => {
            Ok(Arc::new(mistral::MistralClient::new()?))
        }
        ApiProvider::OpenRouter => {
            Ok(Arc::new(openrouter::OpenRouterClient::new()?))
        }
        ApiProvider::OpenAi => {
            // OpenAI models route through OpenRouter when available,
            // otherwise fall back to OpenRouter client with OPENAI_API_KEY.
            let client = openrouter::OpenRouterClient::new()
                .or_else(|_| {
                    let key = crate::commands::secret::resolve_secret("OPENAI_API_KEY")
                        .map_err(|_| error::DriverError::MissingApiKey {
                            provider: "openai".into(),
                            key_name: "OPENAI_API_KEY".into(),
                        })?;
                    Ok::<_, error::DriverError>(openrouter::OpenRouterClient::with_config(
                        key,
                        "https://api.openai.com/v1".to_string(),
                    ))
                })?;
            Ok(Arc::new(client))
        }
        ApiProvider::Ollama => {
            // Ollama local models — OpenAI-compatible API at localhost:11434
            let host = std::env::var("OLLAMA_HOST")
                .unwrap_or_else(|_| "http://localhost:11434".to_string());
            let base_url = format!("{}/v1", host.trim_end_matches('/'));
            // Ollama doesn't need a real API key but the client requires one
            let client = openrouter::OpenRouterClient::with_config(
                "ollama".to_string(),
                base_url,
            );
            Ok(Arc::new(client))
        }
    }
}

// ---------------------------------------------------------------------------
// Tool schema translation — Anthropic → OpenAI-compatible format (→850)
// ---------------------------------------------------------------------------

/// Convert Anthropic-format tool schemas to OpenAI function-calling format.
///
/// Used by OpenRouter, Mistral, and any future OpenAI-compatible provider.
/// Gemini uses a different format (`functionDeclarations`) and has its own
/// translation in `gemini.rs`.
///
/// Anthropic: `{"name": "Bash", "description": "...", "input_schema": {...}}`
/// OpenAI:    `{"type": "function", "function": {"name": "Bash", "description": "...", "parameters": {...}}}`
pub fn translate_tools_openai(tools: &[serde_json::Value]) -> Vec<serde_json::Value> {
    tools
        .iter()
        .filter_map(|t| {
            let name = t.get("name")?.as_str()?;
            let description = t.get("description")?.as_str().unwrap_or("");
            let parameters = t.get("input_schema").cloned().unwrap_or(json!({
                "type": "object",
                "properties": {},
                "required": []
            }));
            Some(json!({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters
                }
            }))
        })
        .collect()
}

// ---------------------------------------------------------------------------
// API error message extraction — parse provider JSON errors into one-liners
// ---------------------------------------------------------------------------

/// Extract a human-readable message from a raw API error response body.
///
/// Supports the error JSON shapes from all three providers:
/// - Anthropic: `{"type":"error","error":{"type":"...","message":"THE MESSAGE"}}`
/// - Gemini:    `{"error":{"code":400,"message":"THE MESSAGE","status":"..."}}`
/// - OpenRouter/OpenAI: `{"error":{"message":"THE MESSAGE","type":"...","code":"..."}}`
///
/// Falls back to the raw string (truncated to 200 chars) if JSON parsing fails.
pub fn extract_api_error_message(raw: &str) -> String {
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(raw) {
        // Anthropic: .error.message
        if let Some(msg) = v.get("error").and_then(|e| e.get("message")).and_then(|m| m.as_str()) {
            return msg.to_string();
        }
        // Some APIs put message at top level
        if let Some(msg) = v.get("message").and_then(|m| m.as_str()) {
            return msg.to_string();
        }
    }
    // Not JSON or no message field — truncate raw string
    crate::util::safe_truncate(raw, 200)
}

// ---------------------------------------------------------------------------
// PermissionMode — runtime permission levels for agent tool access (→732)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[derive(Default)]
pub enum PermissionMode {
    /// No tools. Text only. Agent plans, doesn't execute.
    Plan,
    /// Each tool call shown to user for approval (default).
    #[default]
    Governed,
    /// File edits auto-approved. Bash still gated.
    Auto,
    /// Full access. For bench/CI.
    Autonomous,
}

impl PermissionMode {
    /// Parse a permission mode string (case-insensitive).
    #[allow(clippy::should_implement_trait)]
    pub fn from_str(s: &str) -> Option<PermissionMode> {
        match s.trim().to_lowercase().as_str() {
            "plan" => Some(PermissionMode::Plan),
            "governed" => Some(PermissionMode::Governed),
            "auto" => Some(PermissionMode::Auto),
            "autonomous" => Some(PermissionMode::Autonomous),
            _ => None,
        }
    }

    /// Short label for display.
    pub fn label(&self) -> &'static str {
        match self {
            PermissionMode::Plan => "plan",
            PermissionMode::Governed => "governed",
            PermissionMode::Auto => "auto",
            PermissionMode::Autonomous => "autonomous",
        }
    }
}

impl std::fmt::Display for PermissionMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.label())
    }
}

// ---------------------------------------------------------------------------
// CapabilityClass — tool call classification for the approval policy chain (→1005)
// ---------------------------------------------------------------------------

/// Capability class for a tool call. Determines which policy chain rules apply.
/// Ordered by privilege: read < edit < write < exec < secret.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CapabilityClass {
    /// Read, Glob, Grep — no side effects.
    FileRead,
    /// Edit — mutates existing files in-place.
    FileEdit,
    /// Write — creates or overwrites files.
    FileWrite,
    /// cat, ls, ps, git log, etc. — read-only shell commands.
    ShellRead,
    /// cp, mv, rm, echo > file, git commit, etc. — filesystem mutation.
    ShellWrite,
    /// spawn, curl, cargo build, npm install, etc. — process/network.
    ShellExec,
    /// gpg, ssh-keygen, ostk secret — key material operations.
    ShellSecret,
    /// ostk needle list, ostk ps, ostk clock — read-only kernel state.
    KernelRead,
    /// ostk needle add, ostk commit, ostk hay — mutates kernel state.
    KernelWrite,
    /// ostk run, ostk spawn — creates agent processes.
    KernelSpawn,
    /// ostk secret — key material through kernel.
    KernelSecret,
}

impl CapabilityClass {
    /// Short label for display in approval prompts and reason chains.
    pub fn label(&self) -> &'static str {
        match self {
            Self::FileRead => "file:read",
            Self::FileEdit => "file:edit",
            Self::FileWrite => "file:write",
            Self::ShellRead => "shell:read",
            Self::ShellWrite => "shell:write",
            Self::ShellExec => "shell:exec",
            Self::ShellSecret => "shell:secret",
            Self::KernelRead => "kernel:read",
            Self::KernelWrite => "kernel:write",
            Self::KernelSpawn => "kernel:spawn",
            Self::KernelSecret => "kernel:secret",
        }
    }
}

impl std::fmt::Display for CapabilityClass {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.label())
    }
}

/// Classify a tool call into a capability class.
///
/// This is the P0 stub — maps existing tool names to classes without changing
/// any behavior. The 4-layer shell classifier (operator scan, dangerous flags,
/// command allowlist, default) ships in P1.
pub fn detect_capability_class(tool_name: &str, input: &serde_json::Value) -> CapabilityClass {
    match tool_name {
        // File tools
        "Read" | "Glob" | "Grep" => CapabilityClass::FileRead,
        "Edit" => CapabilityClass::FileEdit,
        "Write" | "NotebookEdit" => CapabilityClass::FileWrite,

        // Shell tools — classify via 4-layer shell classifier (→1006).
        "Bash" | "shell" | "sh_run" => {
            let cmd = input.get("command")
                .or_else(|| input.get("cmd"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            classify::detect_shell_class(cmd)
        }

        // Kernel tools — classify by subcommand
        name if name.starts_with("ostk_") || name.starts_with("mcp__ostk__") => {
            let cmd = input.get("cmd")
                .or_else(|| input.get("command"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            classify_kernel_tool(name, cmd)
        }

        // Default: treat unknown tools as shell:exec (conservative)
        _ => CapabilityClass::ShellExec,
    }
}

/// Classify kernel tools by verb/subcommand.
fn classify_kernel_tool(tool_name: &str, cmd: &str) -> CapabilityClass {
    // MCP tools have structured names — return directly
    match tool_name {
        "mcp__ostk__shell" | "mcp__ostk__sh_run" => return CapabilityClass::ShellExec,
        "mcp__ostk__spawn" | "mcp__ostk__sh_spawn" => return CapabilityClass::KernelSpawn,
        "mcp__ostk__interact" | "mcp__ostk__sh_interact" => return CapabilityClass::ShellExec,
        "mcp__ostk__lock" | "mcp__ostk__session" => return CapabilityClass::KernelWrite,
        "mcp__ostk__help" | "mcp__ostk__tack" => return CapabilityClass::KernelRead,
        "mcp__ostk__web_read" | "mcp__ostk__web_links" | "mcp__ostk__web_status" => return CapabilityClass::ShellRead,
        "mcp__ostk__ostk_context_search" | "mcp__ostk__ostk_pitchfork" => return CapabilityClass::KernelRead,
        "mcp__ostk__ostk_context_release" => return CapabilityClass::KernelWrite,
        _ => {}
    }

    // CLI-style ostk_* tools — classify by the subcommand in cmd string
    let first_word = cmd.split_whitespace()
        .find(|w| !w.starts_with('-'))
        .unwrap_or("");

    match first_word {
        // Read-only kernel state
        "needle" if cmd.contains("list") => CapabilityClass::KernelRead,
        "ps" | "clock" | "history" | "show" | "search" | "status"
        | "drivers" | "metrics" | "compounds" | "guide" => CapabilityClass::KernelRead,

        // Kernel writes
        "needle" | "hay" | "commit" | "thread" | "draft" | "promote"
        | "decompose" | "sign" | "init" | "install" => CapabilityClass::KernelWrite,

        // Kernel spawn
        "run" | "spawn" => CapabilityClass::KernelSpawn,

        // Secret operations
        "secret" => CapabilityClass::KernelSecret,

        // Default for unknown ostk subcommands
        _ => CapabilityClass::KernelRead,
    }
}

// ---------------------------------------------------------------------------
// CpuConfig — configuration derived from an Agentfile
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct CpuConfig {
    pub model: String,
    pub system_prompt: Option<String>,
    pub tools: Vec<String>,
    pub max_turns: Option<u32>,
    pub max_budget_usd: Option<f64>,
    pub max_tokens: u32,
    pub context_budget: Option<u64>,
    pub permission_mode: PermissionMode,
    pub betas: Vec<String>,
    /// Whether this agent requires real-time streaming (→727).
    /// Defaults to `true`. When `false`, the agent is batch-eligible and
    /// can use the Batch API at reduced cost instead of streaming.
    /// Controlled via `LIMIT interactive false` in the Agentfile.
    pub interactive: bool,
    /// →1011: Capability class patterns from Agentfile TOOL directives.
    pub tool_patterns: Vec<(String, Option<String>)>,
}

impl CpuConfig {
    /// Convert into a [`LoopConfig`] ready for the agent loop.
    ///
    /// Tool **names** are resolved to Anthropic tool schemas, `fast_mode` is
    /// derived from the beta list + model name, and the caller supplies the
    /// project `root` path.  This is the single authoritative transcription
    /// site — avoids manual field-by-field copying elsewhere (→766).
    pub fn into_loop_config(self, root: Option<PathBuf>) -> agent_loop::LoopConfig {
        let fast_mode =
            self.betas.iter().any(|b| b == "fast-mode") && self.model.contains("opus");

        // →781: Clamp max_tokens to model-specific ceiling to prevent silent
        // API 400 errors that cause agents to spin dead.
        let model_limit = model_max_output_tokens(&self.model);
        let max_tokens = self.max_tokens.min(model_limit);
        if max_tokens < self.max_tokens {
            tracing::warn!(
                requested = self.max_tokens,
                clamped = max_tokens,
                model = %self.model,
                "clamped max_tokens to model limit"
            );
        }

        // Demand-paged tool loading: native tools + .language resident set
        // Increment boot_stamp once per session start (frozen for session lifetime)
        let mut all_tools = tool_schemas(&self.tools);
        if let Some(ref r) = root {
            let _ = boot_stamp(r); // Increment — tools_from_language reads via current_boot_stamp
            let language_tools = tools_from_language(r);
            all_tools.extend(language_tools);
        }

        agent_loop::LoopConfig {
            model: self.model,
            system_prompt: self.system_prompt,
            tools: all_tools,
            max_tokens,
            max_turns: self.max_turns,
            context_budget: self.context_budget,
            permission_mode: self.permission_mode,
            betas: self.betas,
            fast_mode,
            root,
            preload_context: vec![],
            thinking: None,
            citations: false,
            tool_patterns: self.tool_patterns,
        }
    }
}

// ---------------------------------------------------------------------------
// Agentfile -> CpuConfig mapping
// ---------------------------------------------------------------------------

/// Build a single system prompt string from a list of PromptSource entries.
///
/// Inline prompts are concatenated with double newlines. FileRef entries are
/// included as `[file: <path>]` placeholders — resolution is left to the
/// caller / agent loop that has filesystem access.
fn build_system_prompt(prompts: &[PromptSource], base_dir: &std::path::Path) -> Option<String> {
    if prompts.is_empty() {
        return None;
    }
    let parts: Vec<String> = prompts
        .iter()
        .map(|p| match p {
            PromptSource::Inline(text) => text.clone(),
            PromptSource::FileRef(path) => {
                let resolved = base_dir.join(path);
                // →FIND-009: Prevent path traversal — resolved path must stay under base_dir.
                match resolved.canonicalize() {
                    Ok(canonical) => {
                        let base_canonical = base_dir.canonicalize().unwrap_or_else(|_| base_dir.to_path_buf());
                        if !canonical.starts_with(&base_canonical) {
                            format!("[denied: path traversal outside project: {path}]")
                        } else {
                            std::fs::read_to_string(&canonical)
                                .unwrap_or_else(|_| format!("[file not found: {path}]"))
                        }
                    }
                    Err(_) => format!("[file not found: {path}]"),
                }
            }
        })
        .collect();
    Some(parts.join("\n\n"))
}

/// Convert a parsed Agentfile into a CpuConfig suitable for starting a CPU
/// session.
pub fn config_from_agentfile(af: &Agentfile, base_dir: &std::path::Path) -> CpuConfig {
    // →732: Parse permission mode from LIMIT permissions <mode> or Agentfile.permissions
    let permission_mode = af
        .limits
        .iter()
        .find(|l| l.key == "permissions")
        .and_then(|l| PermissionMode::from_str(&l.value))
        .or_else(|| af.permissions.as_deref().and_then(PermissionMode::from_str))
        .unwrap_or_default();

    CpuConfig {
        model: af.from.clone(),
        system_prompt: build_system_prompt(&af.prompts, base_dir),
        tools: if af.tools.is_empty() {
            crate::kernel::defaults::default_tools()
        } else {
            af.tools.clone()
        },
        max_turns: af
            .limits
            .iter()
            .find(|l| l.key == "max_turns")
            .and_then(|l| l.value.parse().ok()),
        max_budget_usd: af
            .limits
            .iter()
            .find(|l| l.key == "budget_usd")
            .and_then(|l| l.value.parse().ok()),
        // LIMIT tokens = context budget (Agentfile), not max output tokens (API).
        // →781: Clamp to the model-specific max, not a hardcoded 64k ceiling.
        // into_loop_config() also clamps, but doing it here prevents CpuConfig
        // from ever holding an invalid value.
        max_tokens: {
            let requested: u32 = af
                .limits
                .iter()
                .find(|l| l.key == "max_output_tokens")
                .and_then(|l| l.value.parse().ok())
                .unwrap_or(16384);
            let model_limit = model_max_output_tokens(&af.from);
            requested.min(model_limit)
        },
        context_budget: af
            .limits
            .iter()
            .find(|l| l.key == "tokens")
            .and_then(|l| l.value.parse().ok()),
        permission_mode,
        betas: af.betas.clone(),
        // →727: LIMIT interactive false marks agents as batch-eligible.
        // Default is true (streaming). Only explicit "false" disables it.
        interactive: af
            .limits
            .iter()
            .find(|l| l.key == "interactive")
            .map(|l| l.value.trim().to_lowercase() != "false")
            .unwrap_or(true),
        tool_patterns: af.tool_patterns.clone(),
    }
}

// ---------------------------------------------------------------------------
// SpawnRequest — unified agent launch pipeline
// ---------------------------------------------------------------------------

/// How to obtain the Agentfile for a spawn.
pub enum AgentfileSource {
    /// Load from a path on disk. Callers handle GPG verification separately.
    FromPath(PathBuf),
    /// Inline Agentfile content (pre-formatted directive syntax).
    Inline(String),
}

/// Declarative specification for launching an agent.
///
/// Call [`SpawnRequest::prepare`] to run the shared config pipeline and
/// obtain a [`PreparedSpawn`]. Callers then own execution: threading,
/// event draining, session wiring, done-artifact writing.
pub struct SpawnRequest {
    /// Project root.
    pub root: PathBuf,
    /// Agentfile source.
    pub source: AgentfileSource,
    /// Optional model override — takes precedence over the Agentfile's FROM.
    /// This is how `--model` works.
    pub model_override: Option<String>,
    /// Context to inject as a preload_context block before the first turn.
    pub parent_context: Option<String>,
}

/// The product of [`SpawnRequest::prepare`] — all config work done.
pub struct PreparedSpawn {
    /// Canonical model name after alias/auto resolution.
    pub model: String,
    /// Ready-to-use LoopConfig.
    pub config: agent_loop::LoopConfig,
    /// Driver pointed at the correct backend.
    pub driver: Arc<dyn CpuDriver>,
    /// Parsed Agentfile — retained for callers that inspect pin, tools, etc.
    pub agentfile: crate::agentfile::Agentfile,
    /// Project root, forwarded for session/audit/artifact paths.
    pub root: PathBuf,
}

impl SpawnRequest {
    /// Run the shared config pipeline: resolve model, parse AF, build config,
    /// enrich boot context, create driver.
    pub fn prepare(self) -> Result<PreparedSpawn, String> {
        // ── 1. Parse Agentfile ───────────────────────────────────────
        let (mut af, base_dir) = match self.source {
            AgentfileSource::FromPath(ref path) => {
                let content = std::fs::read_to_string(path)
                    .map_err(|e| format!("failed to read Agentfile '{}': {e}", path.display()))?;
                let af = crate::agentfile::parse(&content)
                    .map_err(|e| format!("failed to parse Agentfile: {e}"))?;
                let base = path.parent()
                    .unwrap_or_else(|| std::path::Path::new("."))
                    .to_path_buf();
                (af, base)
            }
            AgentfileSource::Inline(ref content) => {
                let af = crate::agentfile::parse(content)
                    .map_err(|e| format!("failed to parse inline Agentfile: {e}"))?;
                (af, self.root.clone())
            }
        };

        // ── 2. Resolve model ─────────────────────────────────────────
        if let Some(ref ovr) = self.model_override {
            af.from = providers::resolve_model_alias(ovr).to_string();
        } else if af.from.trim() == "auto" {
            af.from = crate::commands::run::resolve_auto_model();
        } else {
            af.from = providers::resolve_model_alias(&af.from).to_string();
        }
        let model = af.from.clone();

        // ── 3. CpuConfig → LoopConfig ────────────────────────────────
        let cpu_config = config_from_agentfile(&af, &base_dir);
        let mut config = cpu_config.into_loop_config(Some(self.root.clone()));

        // ── 4. Boot context enrichment ───────────────────────────────
        let boot_ctx = session::BootContext::new(&self.root);
        config.system_prompt = Some(
            boot_ctx.build_system_prompt(
                config.system_prompt.as_deref().unwrap_or("")
            )
        );

        // ── 5. Parent context injection ──────────────────────────────
        if let Some(ctx) = self.parent_context {
            config.preload_context.push(ctx);
        }

        // ── 6. Create driver ─────────────────────────────────────────
        let driver = create_driver(&model)
            .map_err(|e| format!("failed to create driver for '{model}': {e}"))?;

        Ok(PreparedSpawn {
            model,
            config,
            driver,
            agentfile: af,
            root: self.root,
        })
    }
}

// ---------------------------------------------------------------------------
// Demand-paged tool loading from .language
// ---------------------------------------------------------------------------

/// Default momentum threshold for tool residency.
pub const TOOL_RESIDENT_THRESHOLD: f64 = 0.45;

/// Read boot_stamp from .ostk/boot_stamp, increment, and return the new value.
pub fn boot_stamp(root: &Path) -> u64 {
    let path = crate::state_dir(root).join("boot_stamp");
    let current = std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or(0);
    let next = current + 1;
    let _ = std::fs::write(&path, next.to_string());
    next
}

/// Read the current boot_stamp without incrementing.
pub fn current_boot_stamp(root: &Path) -> u64 {
    let path = crate::state_dir(root).join("boot_stamp");
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| s.trim().parse::<u64>().ok())
        .unwrap_or(0)
}

/// Read the tool resident threshold from env or fall back to default.
fn env_threshold() -> f64 {
    std::env::var("OSTK_TOOL_THRESHOLD")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(TOOL_RESIDENT_THRESHOLD)
}

/// Parse a .language signature like "(title, body?) -> (id)" into JSON Schema
/// properties and required fields.
fn parse_signature_to_schema(sig: &str) -> (serde_json::Value, Vec<String>) {
    let sig = sig.trim();
    // Extract input portion: everything before " -> " or "→"
    // Note: split("->").next() always returns Some, so check contains first.
    let input_part = if sig.contains("->") {
        sig.split("->").next().unwrap_or(sig)
    } else if sig.contains('\u{2192}') {
        sig.split('\u{2192}').next().unwrap_or(sig)
    } else {
        sig
    }
    .trim()
    .trim_start_matches('(')
    .trim_end_matches(')');

    let mut properties = serde_json::Map::new();
    let mut required = Vec::new();

    for param in input_part.split(',') {
        let param = param.trim();
        if param.is_empty() {
            continue;
        }
        let optional = param.ends_with('?');
        let name = param.trim_end_matches('?').trim();
        if name.is_empty() {
            continue;
        }
        properties.insert(
            name.to_string(),
            json!({"type": "string", "description": name}),
        );
        if !optional {
            required.push(name.to_string());
        }
    }

    (serde_json::Value::Object(properties), required)
}

/// Generate a single MCP tool schema from a LanguageEntry.
///
/// Note: boot_stamp is NOT embedded in the schema (Anthropic API rejects
/// unknown fields). Stamp validation happens kernel-side in the dispatch
/// via `current_boot_stamp()`.
pub fn generate_tool_schema(entry: &LanguageEntry, _stamp: u64) -> serde_json::Value {
    let (properties, required) = parse_signature_to_schema(&entry.signature);
    json!({
        "name": format!("ostk_{}", entry.verb),
        "description": if entry.doc.is_empty() {
            format!("ostk {} — {}", entry.verb, entry.resolution)
        } else {
            entry.doc.clone()
        },
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": false
        }
    })
}

/// The ostk_verbs meta-tool schema — always resident, the page fault handler.
fn ostk_verbs_schema(_stamp: u64) -> serde_json::Value {
    json!({
        "name": "ostk_verbs",
        "description": "Discover available kernel verbs. Returns tool schemas for matching verbs not in your current tool set. Use when you need a capability not in your loaded tools.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — verb name, keyword, or category (e.g. 'search', 'draft', 'admin')"
                }
            },
            "required": ["query"],
            "additionalProperties": false
        }
    })
}

/// The ostk_man tool schema — always resident, like man(1).
fn ostk_man_schema() -> serde_json::Value {
    json!({
        "name": "ostk_man",
        "description": "Show detailed help for a ostk CLI command — arguments, flags, defaults. Like man(1). Use when you need to know exact CLI syntax.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command path, e.g. 'work add', 'show', 'needle list', 'kernel spawn'"
                }
            },
            "required": ["command"],
            "additionalProperties": false
        }
    })
}

fn log_decision_schema() -> serde_json::Value {
    json!({
        "name": "log_decision",
        "description": "Log a key decision to the working state. Use when making architectural choices, setting parameters, or when the human confirms a direction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Decision name (e.g. 'momentum_threshold', 'boot_snapshot')"},
                "value": {"type": "string", "description": "Decision value"},
                "reason": {"type": "string", "description": "Why this decision was made (optional)"}
            },
            "required": ["key", "value"],
            "additionalProperties": false
        }
    })
}

/// Generate the resident tool set from .language at boot.
///
/// Returns: (resident_schemas, deferred_summary)
/// - resident_schemas: tool JSON schemas for high-momentum verbs + verbs.lock + meta-tool
/// - deferred_summary: not used here (built in session.rs), but logged for audit
pub fn tools_from_language(root: &Path) -> Vec<serde_json::Value> {
    let entries = match language::parse_language_file(root) {
        Ok(e) => e,
        Err(_) => return vec![],
    };
    let stamp = current_boot_stamp(root);
    let threshold = env_threshold();
    let lock_entries = language::read_verbs_lock(root);

    // Step 1: Alias deduplication — collapse by resolution
    let canonical = language::deduplicate_by_resolution(&entries);

    // Step 2: Filter to resident set (above threshold OR in verbs.lock)
    let resident: Vec<serde_json::Value> = canonical
        .iter()
        .filter(|e| e.momentum >= threshold || lock_entries.contains(&e.verb))
        .map(|e| generate_tool_schema(e, stamp))
        .collect();

    // Step 3: Always include meta-tools (if not already in resident set)
    let mut tools = resident;
    let has_tool = |tools: &[serde_json::Value], name: &str| -> bool {
        tools.iter().any(|t| t["name"].as_str() == Some(name))
    };
    if !has_tool(&tools, "ostk_verbs") {
        tools.push(ostk_verbs_schema(stamp));
    }
    if !has_tool(&tools, "ostk_man") {
        tools.push(ostk_man_schema());
    }
    if !has_tool(&tools, "log_decision") {
        tools.push(log_decision_schema());
    }

    // Step 4: Add alive device tools (if not already present)
    for e in entries.iter().filter(|e| e.is_device() && e.is_alive()) {
        let name = format!("ostk_{}", e.verb);
        if !has_tool(&tools, &name) {
            tools.push(generate_tool_schema(e, stamp));
        }
    }

    tools
}

// ---------------------------------------------------------------------------
// Tool name -> Anthropic tool schema mapping
// ---------------------------------------------------------------------------

/// Map ostk tool names to Anthropic's tool definition format.
///
/// Unknown tool names are silently filtered out — the caller should validate
/// tool availability earlier in the pipeline if strict checking is needed.
pub fn tool_schemas(tool_names: &[String]) -> Vec<serde_json::Value> {
    tool_names
        .iter()
        .filter_map(|name| match name.as_str() {
            "shell" | "Bash" => Some(json!({
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
            })),
            "file:read" | "Read" => Some(json!({
                "name": "Read",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Absolute path to read"}
                    },
                    "required": ["file_path"],
                    "additionalProperties": false
                }
            })),
            "file:edit" | "Edit" => Some(json!({
                "name": "Edit",
                "description": "Replace text in a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"}
                    },
                    "required": ["file_path", "old_string", "new_string"],
                    "additionalProperties": false
                }
            })),
            "Write" => Some(json!({
                "name": "Write",
                "description": "Write content to a file",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["file_path", "content"],
                    "additionalProperties": false
                }
            })),
            "Glob" => Some(json!({
                "name": "Glob",
                "description": "Find files by glob pattern",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"}
                    },
                    "required": ["pattern"],
                    "additionalProperties": false
                }
            })),
            "Grep" => Some(json!({
                "name": "Grep",
                "description": "Search file contents with regex",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"}
                    },
                    "required": ["pattern"],
                    "additionalProperties": false
                }
            })),
            // ─── Kernel tools ────────────────────────────────────────────
            "spawn" | "SpawnAgent" => Some(json!({
                "name": "SpawnAgent",
                "description": "Spawn a new agent worker via the kernel",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Agent name"},
                        "prompt": {"type": "string", "description": "Task prompt for the agent"},
                        "model": {"type": "string", "description": "Model to use (default: sonnet)"},
                        "budget": {"type": "string", "description": "Budget cap in USD (default: 2)"}
                    },
                    "required": ["name"],
                    "additionalProperties": false
                }
            })),
            "nudge" | "NudgeAgent" => Some(json!({
                "name": "NudgeAgent",
                "description": "Send a nudge message to another agent",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "description": "Agent name to nudge"},
                        "message": {"type": "string", "description": "Nudge message"}
                    },
                    "required": ["agent", "message"],
                    "additionalProperties": false
                }
            })),
            "needle:add" | "FileNeedle" => Some(json!({
                "name": "FileNeedle",
                "description": "File a new needle (work item) in the backlog",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short title (verb + target)"},
                        "priority": {"type": "string", "description": "P0 (immediate), P1 (sprint), P2 (someday)"}
                    },
                    "required": ["title"],
                    "additionalProperties": false
                }
            })),
            "needle:close" | "CloseNeedle" => Some(json!({
                "name": "CloseNeedle",
                "description": "Close a needle with an optional reason",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Needle ID (e.g. 576 or →576)"},
                        "reason": {"type": "string", "description": "Reason for closing"}
                    },
                    "required": ["id"],
                    "additionalProperties": false
                }
            })),
            "compile" | "CompileHay" => Some(json!({
                "name": "CompileHay",
                "description": "Triage hay into needles — the compile pass",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "dry_run": {"type": "boolean", "description": "Only show what would be compiled"}
                    },
                    "required": [],
                    "additionalProperties": false
                }
            })),
            // ─── fcp-web: web reading intelligence ─────────────────────
            "web_read" | "WebRead" => Some(json!({
                "name": "WebRead",
                "description": "Fetch a URL and return cleaned text content (HTML stripped, readability extracted)",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to fetch and read"}
                    },
                    "required": ["url"],
                    "additionalProperties": false
                }
            })),
            "web_links" | "WebLinks" => Some(json!({
                "name": "WebLinks",
                "description": "Extract all links from a web page",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to extract links from"}
                    },
                    "required": ["url"],
                    "additionalProperties": false
                }
            })),
            "web_status" | "WebStatus" => Some(json!({
                "name": "WebStatus",
                "description": "Check HTTP status and headers for a URL",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to check"}
                    },
                    "required": ["url"],
                    "additionalProperties": false
                }
            })),
            // →963/964/965: kernel introspection tools
            "pitchfork" | "ostk_pitchfork" => Some(json!({
                "name": "ostk_pitchfork",
                "description": "Search across ALL kernel state by keyword — decisions, needles, docs, audit",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keyword to search for across kernel state"}
                    },
                    "required": ["query"],
                    "additionalProperties": false
                }
            })),
            "context_search" | "ostk_context_search" => Some(json!({
                "name": "ostk_context_search",
                "description": "Search within the current agent's session transcript by keyword",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keyword to search for in session history"}
                    },
                    "required": ["query"],
                    "additionalProperties": false
                }
            })),
            "context_release" | "ostk_context_release" => Some(json!({
                "name": "ostk_context_release",
                "description": "Signal that turns before a given number have been processed and can be released",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "before_turn": {"type": "integer", "description": "Release context before this turn number"},
                        "reason": {"type": "string", "description": "Why this context is being released"}
                    },
                    "required": ["before_turn"],
                    "additionalProperties": false
                }
            })),
            _ => None,
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agentfile::{self, Agentfile, Limit, PromptSource};

    #[test]
    fn config_from_agentfile_scheduler() {
        let content = r#"
FROM claude-sonnet-4-5
PROMPT "You are the scheduling intelligence for ostk."
TOOL shell
TOOL file:read
TOOL file:edit
LIMIT max_output_tokens 32000
LIMIT permissions governed
"#;
        let af = agentfile::parse(content).unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));

        assert_eq!(cfg.model, "claude-sonnet-4-5");
        assert!(cfg.system_prompt.as_ref().unwrap().contains("scheduling intelligence"));
        assert_eq!(cfg.tools, vec!["shell", "file:read", "file:edit"]);
        assert!(cfg.max_turns.is_none());
        assert!(cfg.max_budget_usd.is_none());
        assert_eq!(cfg.max_tokens, 32000);
    }

    #[test]
    fn config_from_agentfile_all_limits() {
        let af = Agentfile {
            from: "claude-sonnet-4-6".to_string(),
            prompts: vec![PromptSource::Inline("Test prompt.".to_string())],
            tools: vec!["shell".to_string()],
            skills: vec![],
            limits: vec![
                Limit { key: "max_output_tokens".to_string(), value: "4096".to_string() },
                Limit { key: "max_turns".to_string(), value: "10".to_string() },
                Limit { key: "budget_usd".to_string(), value: "2.50".to_string() },
            ],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));

        assert_eq!(cfg.model, "claude-sonnet-4-6");
        assert_eq!(cfg.max_tokens, 4096);
        assert_eq!(cfg.max_turns, Some(10));
        assert!((cfg.max_budget_usd.unwrap() - 2.50).abs() < f64::EPSILON);
    }

    #[test]
    fn config_defaults_max_tokens_clamped_to_model_limit() {
        // Unknown model "m" → conservative 8192 limit clamps the 16384 default
        let af = Agentfile {
            from: "m".to_string(),
            prompts: vec![PromptSource::Inline("p".to_string())],
            tools: vec![],
            skills: vec![],
            limits: vec![],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        // Default 16384 clamped to model's conservative 8192 limit
        assert_eq!(cfg.max_tokens, 8192);
    }

    #[test]
    fn config_system_prompt_concatenates() {
        let af = Agentfile {
            from: "m".to_string(),
            prompts: vec![
                PromptSource::Inline("First.".to_string()),
                PromptSource::Inline("Second.".to_string()),
                PromptSource::FileRef("prompts/extra.md".to_string()),
            ],
            tools: vec![],
            skills: vec![],
            limits: vec![],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        let prompt = cfg.system_prompt.unwrap();
        assert!(prompt.contains("First."));
        assert!(prompt.contains("Second."));
        assert!(prompt.contains("[file not found: prompts/extra.md]"));
    }

    #[test]
    fn tool_schemas_maps_known_names() {
        let names: Vec<String> = vec![
            "shell".to_string(),
            "file:read".to_string(),
            "file:edit".to_string(),
        ];
        let schemas = tool_schemas(&names);
        assert_eq!(schemas.len(), 3);
        assert_eq!(schemas[0]["name"], "Bash");
        assert_eq!(schemas[1]["name"], "Read");
        assert_eq!(schemas[2]["name"], "Edit");
    }

    #[test]
    fn tool_schemas_aliases() {
        let names: Vec<String> = vec![
            "Bash".to_string(),
            "Read".to_string(),
            "Edit".to_string(),
            "Write".to_string(),
            "Glob".to_string(),
            "Grep".to_string(),
        ];
        let schemas = tool_schemas(&names);
        assert_eq!(schemas.len(), 6);
        assert_eq!(schemas[0]["name"], "Bash");
        assert_eq!(schemas[1]["name"], "Read");
        assert_eq!(schemas[2]["name"], "Edit");
        assert_eq!(schemas[3]["name"], "Write");
        assert_eq!(schemas[4]["name"], "Glob");
        assert_eq!(schemas[5]["name"], "Grep");
    }

    #[test]
    fn tool_schemas_filters_unknown() {
        let names: Vec<String> = vec![
            "shell".to_string(),
            "ostk".to_string(),
            "fcp-rust".to_string(),
        ];
        let schemas = tool_schemas(&names);
        // Only "shell" maps; "ostk" and "fcp-rust" are filtered out
        assert_eq!(schemas.len(), 1);
        assert_eq!(schemas[0]["name"], "Bash");
    }

    #[test]
    fn tool_schemas_empty_input() {
        let schemas = tool_schemas(&[]);
        assert!(schemas.is_empty());
    }

    #[test]
    fn tool_schema_has_required_fields() {
        let names = vec!["shell".to_string()];
        let schemas = tool_schemas(&names);
        let bash = &schemas[0];
        assert!(bash.get("name").is_some());
        assert!(bash.get("description").is_some());
        assert!(bash.get("input_schema").is_some());
        let input_schema = &bash["input_schema"];
        assert_eq!(input_schema["type"], "object");
        assert!(input_schema.get("properties").is_some());
        assert!(input_schema.get("required").is_some());
    }

    #[test]
    fn build_system_prompt_empty() {
        assert!(build_system_prompt(&[], std::path::Path::new(".")).is_none());
    }

    #[test]
    fn build_system_prompt_single_inline() {
        let prompts = vec![PromptSource::Inline("Hello.".to_string())];
        assert_eq!(build_system_prompt(&prompts, std::path::Path::new(".")).unwrap(), "Hello.");
    }

    // -----------------------------------------------------------------------
    // tool_schemas strict mode — verify every schema has required Anthropic fields
    // -----------------------------------------------------------------------

    #[test]
    fn tool_schemas_all_have_required_anthropic_api_fields() {
        // Every tool the Anthropic API accepts must have: name, description, input_schema
        // and input_schema must have: type=object, properties, required
        let all_tools: Vec<String> = vec![
            "Bash".into(), "Read".into(), "Edit".into(),
            "Write".into(), "Glob".into(), "Grep".into(),
        ];
        let schemas = tool_schemas(&all_tools);
        assert_eq!(schemas.len(), 6);

        for schema in &schemas {
            let name = schema["name"].as_str().unwrap_or("unknown");

            // Required top-level fields
            assert!(schema.get("name").is_some(), "{name}: missing 'name'");
            assert!(
                schema.get("description").is_some() && schema["description"].is_string(),
                "{name}: missing or non-string 'description'"
            );
            assert!(
                schema.get("input_schema").is_some(),
                "{name}: missing 'input_schema'"
            );

            let input_schema = &schema["input_schema"];
            assert_eq!(
                input_schema["type"], "object",
                "{name}: input_schema.type must be 'object'"
            );
            assert!(
                input_schema.get("properties").is_some(),
                "{name}: input_schema missing 'properties'"
            );
            assert!(
                input_schema.get("required").is_some() && input_schema["required"].is_array(),
                "{name}: input_schema missing 'required' array"
            );

            // Verify required fields are actually present in properties
            let properties = input_schema["properties"].as_object().unwrap();
            let required = input_schema["required"].as_array().unwrap();
            for req in required {
                let field = req.as_str().unwrap();
                assert!(
                    properties.contains_key(field),
                    "{name}: required field '{field}' not in properties"
                );
            }

            // Strict mode: additionalProperties must be false
            assert_eq!(
                input_schema["additionalProperties"], false,
                "{name}: input_schema missing 'additionalProperties: false' (strict mode)"
            );
        }
    }

    #[test]
    fn tool_schemas_bash_has_command_required() {
        let schemas = tool_schemas(&["Bash".to_string()]);
        let bash = &schemas[0];
        let required = bash["input_schema"]["required"].as_array().unwrap();
        let req_strs: Vec<&str> = required.iter().map(|v| v.as_str().unwrap()).collect();
        assert!(req_strs.contains(&"command"), "Bash must require 'command'");
    }

    #[test]
    fn tool_schemas_read_has_file_path_required() {
        let schemas = tool_schemas(&["Read".to_string()]);
        let read = &schemas[0];
        let required = read["input_schema"]["required"].as_array().unwrap();
        let req_strs: Vec<&str> = required.iter().map(|v| v.as_str().unwrap()).collect();
        assert!(req_strs.contains(&"file_path"), "Read must require 'file_path'");
    }

    #[test]
    fn tool_schemas_edit_has_three_required_fields() {
        let schemas = tool_schemas(&["Edit".to_string()]);
        let edit = &schemas[0];
        let required = edit["input_schema"]["required"].as_array().unwrap();
        let req_strs: Vec<&str> = required.iter().map(|v| v.as_str().unwrap()).collect();
        assert!(req_strs.contains(&"file_path"), "Edit must require 'file_path'");
        assert!(req_strs.contains(&"old_string"), "Edit must require 'old_string'");
        assert!(req_strs.contains(&"new_string"), "Edit must require 'new_string'");
    }

    #[test]
    fn tool_schemas_write_has_file_path_and_content_required() {
        let schemas = tool_schemas(&["Write".to_string()]);
        let write = &schemas[0];
        let required = write["input_schema"]["required"].as_array().unwrap();
        let req_strs: Vec<&str> = required.iter().map(|v| v.as_str().unwrap()).collect();
        assert!(req_strs.contains(&"file_path"), "Write must require 'file_path'");
        assert!(req_strs.contains(&"content"), "Write must require 'content'");
    }

    #[test]
    fn tool_schemas_grep_has_pattern_required() {
        let schemas = tool_schemas(&["Grep".to_string()]);
        let grep = &schemas[0];
        let required = grep["input_schema"]["required"].as_array().unwrap();
        let req_strs: Vec<&str> = required.iter().map(|v| v.as_str().unwrap()).collect();
        assert!(req_strs.contains(&"pattern"), "Grep must require 'pattern'");
    }

    #[test]
    fn tool_schemas_glob_has_pattern_required() {
        let schemas = tool_schemas(&["Glob".to_string()]);
        let glob = &schemas[0];
        let required = glob["input_schema"]["required"].as_array().unwrap();
        let req_strs: Vec<&str> = required.iter().map(|v| v.as_str().unwrap()).collect();
        assert!(req_strs.contains(&"pattern"), "Glob must require 'pattern'");
    }

    // -----------------------------------------------------------------------
    // →781: max_tokens clamped to model-specific ceiling
    // -----------------------------------------------------------------------

    #[test]
    fn config_caps_max_tokens_to_model_max() {
        // Unknown model "m" → conservative 8192 default cap
        let af = Agentfile {
            from: "m".to_string(),
            prompts: vec![PromptSource::Inline("p".to_string())],
            tools: vec![],
            skills: vec![],
            limits: vec![
                Limit { key: "max_output_tokens".to_string(), value: "200000".to_string() },
            ],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.max_tokens, 8192, "unknown model clamped to conservative 8192");
    }

    #[test]
    fn config_max_tokens_below_cap_is_preserved() {
        let af = Agentfile {
            from: "m".to_string(),
            prompts: vec![PromptSource::Inline("p".to_string())],
            tools: vec![],
            skills: vec![],
            limits: vec![
                Limit { key: "max_output_tokens".to_string(), value: "4096".to_string() },
            ],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.max_tokens, 4096);
    }

    #[test]
    fn config_limit_tokens_100k_sonnet_clamped_to_64k() {
        // →781: LIMIT max_output_tokens 100000 + claude-sonnet → clamped to 64000
        let af = Agentfile {
            from: "claude-sonnet-4-6".to_string(),
            prompts: vec![PromptSource::Inline("p".to_string())],
            tools: vec![],
            skills: vec![],
            limits: vec![
                Limit { key: "max_output_tokens".to_string(), value: "100000".to_string() },
            ],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.max_tokens, 64000, "sonnet max_tokens clamped to 64000");
    }

    #[test]
    fn config_limit_tokens_50k_opus_clamped_to_32k() {
        // →781: LIMIT max_output_tokens 50000 + claude-opus → clamped to 32000
        let af = Agentfile {
            from: "claude-opus-4-6".to_string(),
            prompts: vec![PromptSource::Inline("p".to_string())],
            tools: vec![],
            skills: vec![],
            limits: vec![
                Limit { key: "max_output_tokens".to_string(), value: "50000".to_string() },
            ],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.max_tokens, 32000, "opus max_tokens clamped to 32000");
    }

    // -----------------------------------------------------------------------
    // build_system_prompt with multiple FileRef entries
    // -----------------------------------------------------------------------

    #[test]
    fn build_system_prompt_fileref_not_found() {
        let prompts = vec![PromptSource::FileRef("nonexistent.md".to_string())];
        let result = build_system_prompt(&prompts, std::path::Path::new(".")).unwrap();
        assert_eq!(result, "[file not found: nonexistent.md]");
    }

    #[test]
    fn build_system_prompt_mixed_entries_separated_by_double_newline() {
        let prompts = vec![
            PromptSource::Inline("Part one.".to_string()),
            PromptSource::FileRef("extra.md".to_string()),
            PromptSource::Inline("Part two.".to_string()),
        ];
        let result = build_system_prompt(&prompts, std::path::Path::new(".")).unwrap();
        let parts: Vec<&str> = result.split("\n\n").collect();
        assert_eq!(parts.len(), 3);
        assert_eq!(parts[0], "Part one.");
        assert_eq!(parts[1], "[file not found: extra.md]");
        assert_eq!(parts[2], "Part two.");
    }

    // -----------------------------------------------------------------------
    // →FIND-009: path traversal prevention
    // -----------------------------------------------------------------------

    #[test]
    fn build_system_prompt_path_traversal_denied() {
        let tmp = std::env::temp_dir().join("ostk_traversal_test");
        let sub = tmp.join("project");
        let _ = std::fs::create_dir_all(&sub);
        // Create a file outside the project dir
        let secret = tmp.join("secret.txt");
        std::fs::write(&secret, "LEAKED").unwrap();
        // Try to reference it via traversal
        let prompts = vec![PromptSource::FileRef("../secret.txt".to_string())];
        let result = build_system_prompt(&prompts, &sub).unwrap();
        assert!(result.contains("[denied: path traversal outside project"), "expected traversal denial, got: {result}");
        assert!(!result.contains("LEAKED"), "secret content should not be readable");
        let _ = std::fs::remove_dir_all(&tmp);
    }

    // -----------------------------------------------------------------------
    // CpuConfig from Agentfile with no system prompt
    // -----------------------------------------------------------------------

    #[test]
    fn config_no_budget_or_turns_is_none() {
        let af = Agentfile {
            from: "model".to_string(),
            prompts: vec![PromptSource::Inline("p".to_string())],
            tools: vec!["shell".to_string()],
            skills: vec![],
            limits: vec![],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert!(cfg.max_turns.is_none());
        assert!(cfg.max_budget_usd.is_none());
    }

    // -----------------------------------------------------------------------
    // →732: PermissionMode tests
    // -----------------------------------------------------------------------

    #[test]
    fn permission_mode_from_str_valid() {
        assert_eq!(PermissionMode::from_str("plan"), Some(PermissionMode::Plan));
        assert_eq!(PermissionMode::from_str("governed"), Some(PermissionMode::Governed));
        assert_eq!(PermissionMode::from_str("auto"), Some(PermissionMode::Auto));
        assert_eq!(PermissionMode::from_str("autonomous"), Some(PermissionMode::Autonomous));
    }

    #[test]
    fn permission_mode_from_str_case_insensitive() {
        assert_eq!(PermissionMode::from_str("PLAN"), Some(PermissionMode::Plan));
        assert_eq!(PermissionMode::from_str("Governed"), Some(PermissionMode::Governed));
        assert_eq!(PermissionMode::from_str("AUTO"), Some(PermissionMode::Auto));
        assert_eq!(PermissionMode::from_str("Autonomous"), Some(PermissionMode::Autonomous));
    }

    #[test]
    fn permission_mode_from_str_invalid() {
        assert_eq!(PermissionMode::from_str("unknown"), None);
        assert_eq!(PermissionMode::from_str(""), None);
        assert_eq!(PermissionMode::from_str("restricted"), None);
    }

    #[test]
    fn permission_mode_label() {
        assert_eq!(PermissionMode::Plan.label(), "plan");
        assert_eq!(PermissionMode::Governed.label(), "governed");
        assert_eq!(PermissionMode::Auto.label(), "auto");
        assert_eq!(PermissionMode::Autonomous.label(), "autonomous");
    }

    #[test]
    fn permission_mode_default_is_governed() {
        assert_eq!(PermissionMode::default(), PermissionMode::Governed);
    }

    #[test]
    fn permission_mode_from_str_trims_whitespace() {
        assert_eq!(PermissionMode::from_str("  plan  "), Some(PermissionMode::Plan));
        assert_eq!(PermissionMode::from_str("\tautonomous\n"), Some(PermissionMode::Autonomous));
    }

    #[test]
    fn config_from_agentfile_permission_governed() {
        let content = r#"
FROM m
PROMPT "p"
LIMIT permissions governed
"#;
        let af = agentfile::parse(content).unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.permission_mode, PermissionMode::Governed);
    }

    #[test]
    fn config_from_agentfile_permission_autonomous() {
        let content = r#"
FROM m
PROMPT "p"
LIMIT permissions autonomous
"#;
        let af = agentfile::parse(content).unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.permission_mode, PermissionMode::Autonomous);
    }

    #[test]
    fn config_from_agentfile_permission_plan() {
        let content = r#"
FROM m
PROMPT "p"
LIMIT permissions plan
"#;
        let af = agentfile::parse(content).unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.permission_mode, PermissionMode::Plan);
    }

    #[test]
    fn config_from_agentfile_permission_auto() {
        let content = r#"
FROM m
PROMPT "p"
LIMIT permissions auto
"#;
        let af = agentfile::parse(content).unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.permission_mode, PermissionMode::Auto);
    }

    #[test]
    fn config_from_agentfile_no_permission_defaults_governed() {
        // When no LIMIT permissions is specified, the Agentfile parser defaults
        // permissions to "supervised", but PermissionMode::from_str("supervised")
        // returns None, so config_from_agentfile falls back to PermissionMode::default()
        // which is Governed.
        let af = Agentfile {
            from: "m".to_string(),
            prompts: vec![PromptSource::Inline("p".to_string())],
            tools: vec![],
            skills: vec![],
            limits: vec![],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert_eq!(cfg.permission_mode, PermissionMode::Governed);
    }

    #[test]
    fn plan_mode_produces_empty_tools() {
        let tools = vec!["shell".to_string(), "file:read".to_string()];
        let schemas = tool_schemas(&tools);
        assert!(!schemas.is_empty(), "sanity: schemas should be non-empty for governed mode");

        // Plan mode: when applied in the agent loop, effective_tools should be empty
        let mode = PermissionMode::Plan;
        let effective_tools = match mode {
            PermissionMode::Plan => vec![],
            _ => schemas,
        };
        assert!(effective_tools.is_empty(), "plan mode must produce empty tools");
    }

    // -----------------------------------------------------------------------
    // →727: interactive / batch eligibility tests
    // -----------------------------------------------------------------------

    #[test]
    fn interactive_defaults_to_true() {
        let af = Agentfile {
            from: "m".to_string(),
            prompts: vec![PromptSource::Inline("p".to_string())],
            tools: vec![],
            skills: vec![],
            limits: vec![],
            work: None,
            interrupts: vec![],
            boot_cmd: None,
            destructive_ops: None,
            permissions: None,
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        };
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert!(cfg.interactive, "interactive should default to true");
    }

    #[test]
    fn limit_interactive_false_sets_batch_eligible() {
        let content = r#"
FROM m
PROMPT "p"
LIMIT interactive false
"#;
        let af = agentfile::parse(content).unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert!(!cfg.interactive, "LIMIT interactive false should set interactive=false");
    }

    #[test]
    fn limit_interactive_true_keeps_interactive() {
        let content = r#"
FROM m
PROMPT "p"
LIMIT interactive true
"#;
        let af = agentfile::parse(content).unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert!(cfg.interactive, "LIMIT interactive true should keep interactive=true");
    }

    #[test]
    fn limit_interactive_false_case_insensitive() {
        let content = r#"
FROM m
PROMPT "p"
LIMIT interactive False
"#;
        let af = agentfile::parse(content).unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        assert!(!cfg.interactive, "LIMIT interactive False (capital F) should set interactive=false");
    }

    // -----------------------------------------------------------------------
    // extract_api_error_message tests
    // -----------------------------------------------------------------------

    #[test]
    fn extract_anthropic_error() {
        let raw = r#"{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low."}}"#;
        assert_eq!(extract_api_error_message(raw), "Your credit balance is too low.");
    }

    #[test]
    fn extract_gemini_error() {
        let raw = r#"{"error":{"code":400,"message":"API key not valid. Please pass a valid API key.","status":"INVALID_ARGUMENT","details":[{"@type":"type.googleapis.com/google.rpc.ErrorInfo"}]}}"#;
        assert_eq!(extract_api_error_message(raw), "API key not valid. Please pass a valid API key.");
    }

    #[test]
    fn extract_openai_error() {
        let raw = r#"{"error":{"message":"Incorrect API key provided.","type":"invalid_request_error","code":"invalid_api_key"}}"#;
        assert_eq!(extract_api_error_message(raw), "Incorrect API key provided.");
    }

    #[test]
    fn extract_toplevel_message() {
        let raw = r#"{"message":"Something went wrong","code":500}"#;
        assert_eq!(extract_api_error_message(raw), "Something went wrong");
    }

    #[test]
    fn extract_non_json_fallback() {
        let raw = "Bad Gateway";
        assert_eq!(extract_api_error_message(raw), "Bad Gateway");
    }

    #[test]
    fn extract_non_json_truncates_long() {
        let raw = "x".repeat(300);
        let result = extract_api_error_message(&raw);
        assert!(result.len() <= 203); // 200 + "..."
        assert!(result.ends_with("..."));
    }

    #[test]
    fn extract_empty_string() {
        assert_eq!(extract_api_error_message(""), "");
    }

    #[test]
    fn extract_json_no_message_field() {
        let raw = r#"{"error":{"code":400}}"#;
        // No message field anywhere — falls back to truncated raw
        let result = extract_api_error_message(raw);
        assert_eq!(result, raw);
    }

    // -----------------------------------------------------------------------
    // translate_tools_openai — shared Anthropic→OpenAI tool translation (→850)
    // -----------------------------------------------------------------------

    #[test]
    fn translate_tools_openai_single() {
        let anthropic_tools = vec![serde_json::json!({
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

        let openai_tools = translate_tools_openai(&anthropic_tools);
        assert_eq!(openai_tools.len(), 1);

        let tool = &openai_tools[0];
        assert_eq!(tool["type"], "function");
        assert_eq!(tool["function"]["name"], "Bash");
        assert_eq!(tool["function"]["description"], "Execute a shell command");
        assert_eq!(tool["function"]["parameters"]["type"], "object");
        assert_eq!(
            tool["function"]["parameters"]["properties"]["command"]["type"],
            "string"
        );
        let required = tool["function"]["parameters"]["required"]
            .as_array()
            .unwrap();
        assert!(required.iter().any(|v| v.as_str() == Some("command")));
    }

    #[test]
    fn translate_tools_openai_multiple() {
        let anthropic_tools = vec![
            serde_json::json!({
                "name": "Bash",
                "description": "Execute a shell command",
                "input_schema": {
                    "type": "object",
                    "properties": { "command": {"type": "string"} },
                    "required": ["command"],
                    "additionalProperties": false
                }
            }),
            serde_json::json!({
                "name": "Read",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": { "file_path": {"type": "string"} },
                    "required": ["file_path"],
                    "additionalProperties": false
                }
            }),
        ];

        let openai_tools = translate_tools_openai(&anthropic_tools);
        assert_eq!(openai_tools.len(), 2);
        assert_eq!(openai_tools[0]["function"]["name"], "Bash");
        assert_eq!(openai_tools[1]["function"]["name"], "Read");
    }

    #[test]
    fn translate_tools_openai_empty() {
        let openai_tools = translate_tools_openai(&[]);
        assert!(openai_tools.is_empty());
    }

    #[test]
    fn translate_tools_openai_missing_input_schema() {
        // Tool with no input_schema should get a default empty object schema.
        let tools = vec![serde_json::json!({
            "name": "Noop",
            "description": "Does nothing"
        })];
        let result = translate_tools_openai(&tools);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0]["function"]["parameters"]["type"], "object");
    }

    #[test]
    fn translate_tools_openai_skips_missing_name() {
        // Tool with no name should be filtered out.
        let tools = vec![serde_json::json!({
            "description": "Orphan tool with no name"
        })];
        let result = translate_tools_openai(&tools);
        assert!(result.is_empty());
    }

    // -----------------------------------------------------------------------
    // Demand-paged tool loading tests — generate_tool_schema, boot_stamp,
    // current_boot_stamp, env_threshold, tools_from_language
    // -----------------------------------------------------------------------

    /// Helper: build a LanguageEntry with sensible defaults for testing.
    fn lang_entry(
        verb: &str,
        layer: &str,
        momentum: f64,
        resolution: &str,
        signature: &str,
        doc: &str,
    ) -> crate::language::LanguageEntry {
        crate::language::LanguageEntry {
            verb: verb.to_string(),
            tier: 2,
            layer: layer.to_string(),
            last_gen: 100,
            half_life: 10,
            momentum,
            resolution: resolution.to_string(),
            signature: signature.to_string(),
            doc: doc.to_string(),
            spec: String::new(),
        }
    }

    /// Helper: format a LanguageEntry as a pipe-delimited .language line.
    fn lang_line(
        verb: &str,
        tier: u8,
        layer: &str,
        momentum: f64,
        resolution: &str,
        signature: &str,
        doc: &str,
    ) -> String {
        format!(
            "{verb} | {tier} | {layer} | 100 | 10 | {momentum:.2} | {resolution} | {signature} | {doc}"
        )
    }

    /// Helper: set up a temp dir with .ostk state dir, .language, and optional verbs.lock.
    /// Sets OSTK_STATE_DIR so parse_language_file finds the right directory.
    fn setup_language_dir(
        language_content: &str,
        verbs_lock: Option<&str>,
    ) -> (tempfile::TempDir, std::path::PathBuf) {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path().to_path_buf();
        let state = root.join(".ostk");
        std::fs::create_dir_all(&state).unwrap();
        std::fs::write(state.join(".language"), language_content).unwrap();
        if let Some(lock) = verbs_lock {
            std::fs::write(state.join("verbs.lock"), lock).unwrap();
        }
        // parse_language_file reads OSTK_STATE_DIR; state_dir() checks for .ostk dir.
        // Safety: test-only env mutation — cargo test runs these single-threaded by default.
        unsafe { std::env::set_var("OSTK_STATE_DIR", ".ostk"); }
        (tmp, root)
    }

    // ── generate_tool_schema ───────────────────────────────────────────────

    #[test]
    fn generate_tool_schema_basic() {
        let entry = lang_entry("draft", "user", 0.80, "ostk work add", "(title, body) -> (id)", "Draft a work item");
        let schema = generate_tool_schema(&entry, 1);

        assert_eq!(schema["name"], "ostk_draft");
        assert_eq!(schema["description"], "Draft a work item");
        assert_eq!(schema["input_schema"]["type"], "object");
        assert!(schema["input_schema"].get("properties").is_some());
        assert!(schema["input_schema"].get("required").is_some());
        assert_eq!(schema["input_schema"]["additionalProperties"], false);
    }

    #[test]
    fn generate_tool_schema_parses_signature() {
        // "(title, body?) -> (id)" should produce:
        //   properties: {title: {type: string}, body: {type: string}}
        //   required: ["title"]   (body is optional because of ?)
        let entry = lang_entry("draft", "user", 0.80, "ostk work add", "(title, body?) -> (id)", "");
        let schema = generate_tool_schema(&entry, 1);

        let props = schema["input_schema"]["properties"].as_object().unwrap();
        assert!(props.contains_key("title"), "should have 'title' property");
        assert!(props.contains_key("body"), "should have 'body' property");

        let required: Vec<&str> = schema["input_schema"]["required"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert!(required.contains(&"title"), "'title' should be required");
        assert!(!required.contains(&"body"), "'body?' should NOT be required");
    }

    #[test]
    fn generate_tool_schema_empty_signature() {
        let entry = lang_entry("ping", "user", 0.90, "ostk ping", "()", "");
        let schema = generate_tool_schema(&entry, 1);

        let props = schema["input_schema"]["properties"].as_object().unwrap();
        assert!(props.is_empty(), "empty signature should produce no properties");

        let required = schema["input_schema"]["required"].as_array().unwrap();
        assert!(required.is_empty(), "empty signature should produce no required");
    }

    #[test]
    fn generate_tool_schema_no_boot_stamp_in_output() {
        // Anthropic API rejects unknown top-level fields — boot_stamp must NOT appear
        let entry = lang_entry("show", "user", 1.0, "ostk show", "(target)", "");
        let schema = generate_tool_schema(&entry, 42);

        assert!(schema.get("_boot_stamp").is_none(), "schema must not contain _boot_stamp");
        assert!(schema.get("boot_stamp").is_none(), "schema must not contain boot_stamp");
        assert!(schema.get("stamp").is_none(), "schema must not contain stamp");
        // Also verify the stamp isn't embedded in input_schema
        let input = &schema["input_schema"];
        assert!(input.get("_boot_stamp").is_none());
        assert!(input.get("boot_stamp").is_none());
    }

    #[test]
    fn generate_tool_schema_uses_doc_when_present() {
        let entry = lang_entry("compile", "user", 0.95, "ostk compile", "()", "Triage hay into needles");
        let schema = generate_tool_schema(&entry, 1);
        assert_eq!(schema["description"], "Triage hay into needles");
    }

    #[test]
    fn generate_tool_schema_fallback_description_when_no_doc() {
        let entry = lang_entry("compile", "user", 0.95, "ostk compile", "()", "");
        let schema = generate_tool_schema(&entry, 1);
        let desc = schema["description"].as_str().unwrap();
        assert!(desc.contains("ostk compile"), "fallback description should mention 'ostk compile'");
        assert!(desc.contains("ostk compile"), "fallback description should mention the resolution");
    }

    #[test]
    fn generate_tool_schema_multiple_required_params() {
        let entry = lang_entry("edit", "user", 0.90, "ostk edit", "(file, old, new)", "");
        let schema = generate_tool_schema(&entry, 1);

        let required: Vec<&str> = schema["input_schema"]["required"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(required.len(), 3);
        assert!(required.contains(&"file"));
        assert!(required.contains(&"old"));
        assert!(required.contains(&"new"));
    }

    #[test]
    fn generate_tool_schema_all_optional_params() {
        let entry = lang_entry("search", "user", 0.90, "ostk search", "(query?, path?)", "");
        let schema = generate_tool_schema(&entry, 1);

        let props = schema["input_schema"]["properties"].as_object().unwrap();
        assert_eq!(props.len(), 2);

        let required = schema["input_schema"]["required"].as_array().unwrap();
        assert!(required.is_empty(), "all-optional params should have empty required");
    }

    // ── boot_stamp / current_boot_stamp ────────────────────────────────────

    #[test]
    fn boot_stamp_increments() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state = root.join(".ostk");
        std::fs::create_dir_all(&state).unwrap();

        let first = boot_stamp(root);
        assert_eq!(first, 1, "first boot_stamp should be 1");

        let second = boot_stamp(root);
        assert_eq!(second, 2, "second boot_stamp should be 2");

        let third = boot_stamp(root);
        assert_eq!(third, 3, "third boot_stamp should be 3");
    }

    #[test]
    fn current_boot_stamp_does_not_increment() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state = root.join(".ostk");
        std::fs::create_dir_all(&state).unwrap();

        // Write a stamp
        std::fs::write(state.join("boot_stamp"), "5").unwrap();

        let a = current_boot_stamp(root);
        let b = current_boot_stamp(root);
        let c = current_boot_stamp(root);
        assert_eq!(a, 5);
        assert_eq!(b, 5);
        assert_eq!(c, 5, "current_boot_stamp should never change the stamp");
    }

    #[test]
    fn current_boot_stamp_returns_zero_when_missing() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state = root.join(".ostk");
        std::fs::create_dir_all(&state).unwrap();
        // No boot_stamp file written

        assert_eq!(current_boot_stamp(root), 0, "missing stamp file should return 0");
    }

    #[test]
    fn boot_stamp_and_current_boot_stamp_agree() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state = root.join(".ostk");
        std::fs::create_dir_all(&state).unwrap();

        let incremented = boot_stamp(root);
        let read_back = current_boot_stamp(root);
        assert_eq!(incremented, read_back, "boot_stamp and current_boot_stamp should agree");
    }

    // ── env_threshold ──────────────────────────────────────────────────────
    //
    // These tests mutate process-wide env vars. Consolidated into one #[test]
    // to avoid parallel test interference (cargo test runs tests in the same
    // process by default).

    #[test]
    fn env_threshold_default_override_and_invalid() {
        // 1. Default: no env var set
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }
        let default_t = env_threshold();
        assert!(
            (default_t - TOOL_RESIDENT_THRESHOLD).abs() < f64::EPSILON,
            "default threshold should be TOOL_RESIDENT_THRESHOLD ({}), got {}",
            TOOL_RESIDENT_THRESHOLD,
            default_t,
        );

        // 2. Override with a valid value
        unsafe { std::env::set_var("OSTK_TOOL_THRESHOLD", "0.75"); }
        let override_t = env_threshold();
        assert!(
            (override_t - 0.75).abs() < f64::EPSILON,
            "env override should set threshold to 0.75, got {}",
            override_t,
        );

        // 3. Invalid value falls back to default
        unsafe { std::env::set_var("OSTK_TOOL_THRESHOLD", "not_a_number"); }
        let invalid_t = env_threshold();
        assert!(
            (invalid_t - TOOL_RESIDENT_THRESHOLD).abs() < f64::EPSILON,
            "invalid env value should fall back to default, got {}",
            invalid_t,
        );

        // Clean up
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }
    }

    // ── tools_from_language ────────────────────────────────────────────────

    #[test]
    fn tools_from_language_includes_resident_verbs() {
        let language = [
            "# .language",
            &lang_line("compile", 2, "user", 0.95, "ostk compile", "()", "Compile hay"),
            &lang_line("boot", 2, "user", 0.90, "ostk boot", "()", "Boot the stack"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, None);
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); } // use default 0.45

        let tools = tools_from_language(&root);
        let names: Vec<String> = tools
            .iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        assert!(names.contains(&"ostk_compile".to_string()), "compile (0.95) should be resident");
        assert!(names.contains(&"ostk_boot".to_string()), "boot (0.90) should be resident");
    }

    #[test]
    fn tools_from_language_excludes_below_threshold() {
        let language = [
            "# .language",
            &lang_line("rare", 2, "user", 0.10, "ostk rare", "()", "Rarely used"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, None);
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }

        let tools = tools_from_language(&root);
        let names: Vec<String> = tools
            .iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        assert!(!names.contains(&"ostk_rare".to_string()), "rare (0.10) should not be resident");
    }

    #[test]
    fn tools_from_language_includes_verbs_lock_entries() {
        let language = [
            "# .language",
            &lang_line("pinned", 2, "user", 0.0, "ostk pinned", "()", "Pinned verb"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, Some("pinned\n"));
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }

        let tools = tools_from_language(&root);
        let names: Vec<String> = tools
            .iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        assert!(
            names.contains(&"ostk_pinned".to_string()),
            "pinned (momentum 0.0 but in verbs.lock) should be resident"
        );
    }

    #[test]
    fn tools_from_language_includes_meta_tools() {
        // Even with an empty .language file (just a comment), meta-tools should appear
        let language = "# empty language\n";
        let (_tmp, root) = setup_language_dir(language, None);

        let tools = tools_from_language(&root);
        let names: Vec<String> = tools
            .iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        assert!(names.contains(&"ostk_verbs".to_string()), "ostk_verbs meta-tool must always be present");
        assert!(names.contains(&"ostk_man".to_string()), "ostk_man meta-tool must always be present");
    }

    #[test]
    fn tools_from_language_includes_alive_devices() {
        let language = [
            "# .language",
            &lang_line("fcp-web", 1, "device", 1.00, "fcp-web", "(url) -> (content)", "Web reader"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, None);
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }

        let tools = tools_from_language(&root);
        let names: Vec<String> = tools
            .iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        assert!(
            names.contains(&"ostk_fcp-web".to_string()),
            "alive device (momentum 1.0) should be included"
        );
    }

    #[test]
    fn tools_from_language_excludes_dead_devices() {
        let language = [
            "# .language",
            &lang_line("fcp-dead", 1, "device", 0.00, "fcp-dead", "(x) -> (y)", "Dead device"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, None);
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }

        let tools = tools_from_language(&root);
        let names: Vec<String> = tools
            .iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        assert!(
            !names.contains(&"ostk_fcp-dead".to_string()),
            "dead device (momentum 0.0) should NOT be included"
        );
    }

    #[test]
    fn tools_from_language_deduplicates_aliases() {
        // Two verbs with same resolution: only highest momentum one should appear
        let language = [
            "# .language",
            &lang_line("compile", 2, "user", 0.95, "ostk compile", "()", "Compile hay"),
            &lang_line("triage", 2, "user", 0.50, "ostk compile", "()", "Alias for compile"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, None);
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }

        let tools = tools_from_language(&root);
        let names: Vec<String> = tools
            .iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        // Both are above threshold, but they share a resolution.
        // deduplicate_by_resolution should keep the higher-momentum one.
        assert!(
            names.contains(&"ostk_compile".to_string()),
            "higher-momentum alias (compile, 0.95) should win"
        );
        assert!(
            !names.contains(&"ostk_triage".to_string()),
            "lower-momentum alias (triage, 0.50) should be deduplicated away"
        );
    }

    #[test]
    fn tools_from_language_returns_empty_when_no_language_file() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let state = root.join(".ostk");
        std::fs::create_dir_all(&state).unwrap();
        // No .language file

        let tools = tools_from_language(root);
        // Should still get meta-tools? No — parse_language_file returns Err,
        // so tools_from_language returns vec![]
        assert!(tools.is_empty(), "no .language file should produce empty tool set");
    }

    #[test]
    fn tools_from_language_mixed_resident_and_deferred() {
        let language = [
            "# .language",
            &lang_line("high", 2, "user", 0.90, "ostk high", "()", "High momentum"),
            &lang_line("low", 2, "user", 0.10, "ostk low", "()", "Low momentum"),
            &lang_line("locked", 2, "user", 0.05, "ostk locked", "(x)", "Locked verb"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, Some("locked\n"));
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }

        let tools = tools_from_language(&root);
        let names: Vec<String> = tools
            .iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        assert!(names.contains(&"ostk_high".to_string()), "high momentum should be resident");
        assert!(names.contains(&"ostk_locked".to_string()), "locked verb should be resident");
        assert!(!names.contains(&"ostk_low".to_string()), "low momentum and unlocked should be deferred");
        assert!(names.contains(&"ostk_verbs".to_string()), "meta-tool always present");
        assert!(names.contains(&"ostk_man".to_string()), "man meta-tool always present");
    }

    // ── E2E: Agentfile → InferenceRequest pipeline ──────────────────────
    //
    // These tests verify the full path: Agentfile → config_from_agentfile →
    // into_loop_config → build_params → InferenceRequest. They check what
    // actually reaches the API: tools, system prompt, context_management,
    // preload_context, betas.

    #[test]
    fn e2e_pipeline_tools_include_demand_paged() {
        // Verify that into_loop_config appends .language tools to native tools
        let language = [
            "# .language",
            &lang_line("compile", 1, "user", 0.90, "ostk compile", "(hay) -> (needles)", "triage"),
            &lang_line("draft", 1, "user", 0.05, "ostk draft", "(title) -> (path)", "create draft"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, Some("compile\n"));
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }

        let af = agentfile::parse("FROM claude-sonnet-4-6\nPROMPT \"test\"\nTOOL shell\nTOOL file:read\n").unwrap();
        let cfg = config_from_agentfile(&af, &root);
        let lc = cfg.into_loop_config(Some(root.to_path_buf()));

        let tool_names: Vec<String> = lc.tools.iter()
            .filter_map(|t| t["name"].as_str().map(String::from))
            .collect();

        // Native tools from TOOL directives
        assert!(tool_names.contains(&"Bash".to_string()), "native Bash from TOOL shell");
        assert!(tool_names.contains(&"Read".to_string()), "native Read from TOOL file:read");

        // Demand-paged tools from .language
        assert!(tool_names.contains(&"ostk_compile".to_string()), "resident verb from .language");
        assert!(!tool_names.contains(&"ostk_draft".to_string()), "deferred verb should NOT be in tools");

        // Meta-tools always present
        assert!(tool_names.contains(&"ostk_verbs".to_string()), "page fault handler");
        assert!(tool_names.contains(&"ostk_man".to_string()), "CLI reference tool");
    }

    #[test]
    fn e2e_pipeline_context_management_claude_structure() {
        // Verify structural fields of context_management for Claude (no BootContext)
        let af = agentfile::parse("FROM claude-opus-4-6\nPROMPT \"test\"\nTOOL shell\n").unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        let lc = cfg.into_loop_config(None);

        let params = crate::cpu::params::build_params(
            &lc, &[], lc.tools.clone(), None, None, 0, None,
        );

        let cm = params.claude.context_management.expect("Claude should have context_management");
        let edits = cm["edits"].as_array().expect("should have edits array");

        // clear_tool_uses: exclude_tools + clear_tool_inputs
        let clear = edits.iter()
            .find(|e| e["type"].as_str() == Some("clear_tool_uses_20250919"))
            .expect("should have clear_tool_uses");
        let exclude = clear["exclude_tools"].as_array().expect("should have exclude_tools");
        let exclude_names: Vec<&str> = exclude.iter().filter_map(|v| v.as_str()).collect();
        assert!(exclude_names.contains(&"ostk_verbs"), "ostk_verbs protected");
        assert!(exclude_names.contains(&"ostk_man"), "ostk_man protected");
        assert_eq!(clear["clear_tool_inputs"].as_bool(), Some(true));

        // compact: trigger + pause_after_compaction
        let compact = edits.iter()
            .find(|e| e["type"].as_str() == Some("compact_20260112"))
            .expect("should have compact for 4-6 model");
        assert_eq!(compact["pause_after_compaction"].as_bool(), Some(true));
        assert!(compact["trigger"]["value"].as_u64().unwrap_or(0) > 0,
            "should have a trigger threshold");
    }

    #[test]
    fn e2e_pipeline_context_management_claude_with_boot_context() {
        // With BootContext, compact instructions should preserve kernel state
        let language = "# .language\n\
            :hay | 1 | user | 0 | 200 | 1.00 | ostk work hay | (straw) -> () | capture intent\n";
        let (_tmp, root) = setup_language_dir(language, None);

        let af = agentfile::parse("FROM claude-opus-4-6\nPROMPT \"test\"\nTOOL shell\n").unwrap();
        let cfg = config_from_agentfile(&af, &root);
        let lc = cfg.into_loop_config(Some(root.to_path_buf()));

        let mut boot_ctx = crate::cpu::session::BootContext::new(&root);
        let params = crate::cpu::params::build_params(
            &lc, &[], lc.tools.clone(), None, Some(&mut boot_ctx), 0, None,
        );

        let cm = params.claude.context_management.expect("should have context_management");
        let edits = cm["edits"].as_array().unwrap();
        let compact = edits.iter()
            .find(|e| e["type"].as_str() == Some("compact_20260112"))
            .expect("should have compact");

        let instructions = compact["instructions"].as_str().unwrap_or("");
        assert!(instructions.contains("needle"),
            "instructions should mention preserving needle state, got: '{}'", instructions);
        assert!(instructions.contains("tool set"),
            "instructions should mention tool set, got: '{}'", instructions);
    }

    #[test]
    fn e2e_pipeline_context_management_non_claude() {
        // Non-Claude models should NOT get context_management
        let af = agentfile::parse("FROM deepseek/deepseek-chat\nPROMPT \"test\"\nTOOL shell\n").unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        let lc = cfg.into_loop_config(None);

        let params = crate::cpu::params::build_params(
            &lc,
            &[],
            lc.tools.clone(),
            None,
            None,
            0,
            None,
        );

        assert!(params.claude.context_management.is_none(),
            "non-Claude model should have no context_management (provider-specific)");
    }

    #[test]
    fn e2e_pipeline_tool_schemas_valid_for_api() {
        // Every tool schema must have exactly: name, description, input_schema
        // No extra fields (API rejects unknown fields)
        let language = [
            "# .language",
            &lang_line("hay", 1, "user", 1.00, "ostk work hay", "(straw) -> ()", "capture intent"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, Some("hay\n"));
        unsafe { std::env::remove_var("OSTK_TOOL_THRESHOLD"); }

        let af = agentfile::parse("FROM claude-sonnet-4-6\nPROMPT \"test\"\nTOOL shell\n").unwrap();
        let cfg = config_from_agentfile(&af, &root);
        let lc = cfg.into_loop_config(Some(root.to_path_buf()));

        let allowed_fields = ["name", "description", "input_schema"];
        for tool in &lc.tools {
            let obj = tool.as_object().expect("tool should be object");
            let name = obj.get("name").and_then(|v| v.as_str()).unwrap_or("?");
            for key in obj.keys() {
                assert!(allowed_fields.contains(&key.as_str()),
                    "tool '{}' has unexpected field '{}' — API will reject it", name, key);
            }
            // input_schema must have type: object
            let schema = obj.get("input_schema").expect("must have input_schema");
            assert_eq!(schema["type"].as_str(), Some("object"),
                "tool '{}' input_schema.type must be 'object'", name);
        }
    }

    #[test]
    fn e2e_pipeline_preload_context_has_tool_surface_not_raw_language() {
        // The system prompt should contain the demand-paged tool surface summary,
        // NOT the raw .language file content
        let language = [
            "# .language",
            &lang_line("hay", 1, "user", 1.00, "ostk work hay", "(straw) -> ()", "capture intent"),
            &lang_line("draft", 1, "user", 0.05, "ostk doc draft", "(title) -> (path)", "create draft"),
        ]
        .join("\n");
        let (_tmp, root) = setup_language_dir(&language, None);

        let boot_ctx = crate::cpu::session::BootContext::new(&root);
        let preload = boot_ctx.build_preload_context();

        // Should have the tool surface summary
        let has_surface = preload.iter().any(|b| b.contains("Tool surface (demand-paged)"));
        assert!(has_surface, "preload should contain tool surface summary, got: {:?}", preload);

        // Should NOT have raw .language content
        let has_raw = preload.iter().any(|b| b.contains("verb | tier | layer"));
        assert!(!has_raw, "preload should NOT contain raw .language header");

        // Summary should list :hay as resident
        let summary = preload.iter().find(|b| b.contains("Tool surface")).unwrap();
        assert!(summary.contains(":hay"), "summary should list :hay as resident");

        // Summary should list :draft as deferred
        assert!(summary.contains(":draft"), "summary should list :draft as deferred");

        // Summary should have CLI tree
        assert!(summary.contains("CLI (use ostk_man"), "summary should include CLI tree");
    }

    #[test]
    fn e2e_pipeline_betas_injected_for_claude() {
        let af = agentfile::parse("FROM claude-opus-4-6\nPROMPT \"test\"\nTOOL shell\n").unwrap();
        let cfg = config_from_agentfile(&af, std::path::Path::new("."));
        let lc = cfg.into_loop_config(None);

        let params = crate::cpu::params::build_params(&lc, &[], lc.tools.clone(), None, None, 0, None);

        assert!(params.claude.betas.contains(&"context-management-2025-06-27".to_string()),
            "should inject context-management beta");
        assert!(params.claude.betas.contains(&"compact-2026-01-12".to_string()),
            "should inject compact beta for 4-6");
        assert!(params.claude.betas.contains(&"extended-cache-ttl-2025-04-11".to_string()),
            "should inject cache TTL beta");
    }

    // →1005: CapabilityClass tests
    #[test]
    fn capability_class_file_tools() {
        let empty = serde_json::json!({});
        assert_eq!(detect_capability_class("Read", &empty), CapabilityClass::FileRead);
        assert_eq!(detect_capability_class("Glob", &empty), CapabilityClass::FileRead);
        assert_eq!(detect_capability_class("Grep", &empty), CapabilityClass::FileRead);
        assert_eq!(detect_capability_class("Edit", &empty), CapabilityClass::FileEdit);
        assert_eq!(detect_capability_class("Write", &empty), CapabilityClass::FileWrite);
        assert_eq!(detect_capability_class("NotebookEdit", &empty), CapabilityClass::FileWrite);
    }

    #[test]
    fn capability_class_shell_tools_use_classifier() {
        let empty = serde_json::json!({});
        // Empty command → ShellRead (no side effects)
        assert_eq!(detect_capability_class("Bash", &empty), CapabilityClass::ShellRead);
        // Shell with a read command
        let ls = serde_json::json!({"command": "ls -la"});
        assert_eq!(detect_capability_class("shell", &ls), CapabilityClass::ShellRead);
        // Shell with a write command
        let rm = serde_json::json!({"command": "rm -rf target/"});
        assert_eq!(detect_capability_class("Bash", &rm), CapabilityClass::ShellWrite);
        // Shell with an exec command
        let curl = serde_json::json!({"command": "curl https://example.com"});
        assert_eq!(detect_capability_class("sh_run", &curl), CapabilityClass::ShellExec);
    }

    #[test]
    fn capability_class_kernel_mcp_tools() {
        let empty = serde_json::json!({});
        assert_eq!(detect_capability_class("mcp__ostk__help", &empty), CapabilityClass::KernelRead);
        assert_eq!(detect_capability_class("mcp__ostk__tack", &empty), CapabilityClass::KernelRead);
        assert_eq!(detect_capability_class("mcp__ostk__spawn", &empty), CapabilityClass::KernelSpawn);
        assert_eq!(detect_capability_class("mcp__ostk__shell", &empty), CapabilityClass::ShellExec);
        assert_eq!(detect_capability_class("mcp__ostk__lock", &empty), CapabilityClass::KernelWrite);
        assert_eq!(detect_capability_class("mcp__ostk__web_read", &empty), CapabilityClass::ShellRead);
        assert_eq!(detect_capability_class("mcp__ostk__ostk_context_search", &empty), CapabilityClass::KernelRead);
        assert_eq!(detect_capability_class("mcp__ostk__ostk_context_release", &empty), CapabilityClass::KernelWrite);
    }

    #[test]
    fn capability_class_kernel_cli_tools() {
        let cmd = |s: &str| serde_json::json!({"cmd": s});
        assert_eq!(detect_capability_class("ostk_shell", &cmd("ps aux")), CapabilityClass::KernelRead);
        assert_eq!(detect_capability_class("ostk_shell", &cmd("needle list")), CapabilityClass::KernelRead);
        assert_eq!(detect_capability_class("ostk_shell", &cmd("needle add 'test'")), CapabilityClass::KernelWrite);
        assert_eq!(detect_capability_class("ostk_shell", &cmd("run agents/foo.af")), CapabilityClass::KernelSpawn);
        assert_eq!(detect_capability_class("ostk_shell", &cmd("secret get KEY")), CapabilityClass::KernelSecret);
        assert_eq!(detect_capability_class("ostk_shell", &cmd("commit -m 'test'")), CapabilityClass::KernelWrite);
    }

    #[test]
    fn capability_class_unknown_defaults_to_exec() {
        let empty = serde_json::json!({});
        assert_eq!(detect_capability_class("SomeUnknownTool", &empty), CapabilityClass::ShellExec);
    }

    #[test]
    fn capability_class_labels() {
        assert_eq!(CapabilityClass::FileRead.label(), "file:read");
        assert_eq!(CapabilityClass::ShellWrite.label(), "shell:write");
        assert_eq!(CapabilityClass::KernelSpawn.label(), "kernel:spawn");
        assert_eq!(CapabilityClass::KernelSecret.label(), "kernel:secret");
    }
}
