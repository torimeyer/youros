//! Typed errors for the cpu/ module (→851 Phase 1).
//!
//! Replaces `Result<_, String>` throughout the driver, agent loop, and session
//! layers with structured error types. Callers outside cpu/ can convert to
//! String via `.to_string()` at the boundary until Phase 2.

use thiserror::Error;

// ---------------------------------------------------------------------------
// DriverError — provider construction + API call errors
// ---------------------------------------------------------------------------

#[derive(Debug, Error)]
pub enum DriverError {
    #[error("no API key for {provider} (set {key_name} via `ostk secret set`)")]
    MissingApiKey { provider: String, key_name: String },

    #[error("failed to create {provider} driver: {reason}")]
    CreationFailed { provider: String, reason: String },

    #[error("API error {status}: {body}")]
    ApiError { status: u16, body: String },

    #[error("streaming error: {0}")]
    StreamError(String),

    #[error("model {model} not supported by any available provider")]
    UnsupportedModel { model: String },
}

impl DriverError {
    /// Backward-compat helper: check if the Display string contains a substring.
    /// Bridges the gap between the old `Result<_, String>` pattern and typed errors.
    pub fn contains(&self, needle: &str) -> bool {
        self.to_string().contains(needle)
    }
}

impl From<String> for DriverError {
    fn from(s: String) -> Self {
        DriverError::StreamError(s)
    }
}

impl serde::Serialize for DriverError {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.to_string())
    }
}

// ---------------------------------------------------------------------------
// AgentLoopError — errors during the inference loop
// ---------------------------------------------------------------------------

#[derive(Debug, Error)]
pub enum AgentLoopError {
    #[error("API call failed: {0}")]
    ApiCall(#[from] DriverError),

    #[error("model refused: {0}")]
    Refusal(String),

    #[error("context window exceeded")]
    ContextExceeded,

    #[error("rate limited after {attempts} retries")]
    RateLimited { attempts: u32 },

    #[error("tool execution failed: {tool}: {reason}")]
    ToolExecution { tool: String, reason: String },

    #[error("stream processing error: {0}")]
    StreamProcessing(String),
}

// ---------------------------------------------------------------------------
// SessionError — session lifecycle errors
// ---------------------------------------------------------------------------

#[derive(Debug, Error)]
pub enum SessionError {
    #[error("session '{name}' not found")]
    NotFound { name: String },

    #[error("no driver available for model {model}")]
    NoDriver { model: String },

    #[error("agent loop error: {0}")]
    AgentLoop(#[from] AgentLoopError),
}

// ---------------------------------------------------------------------------
// Conversions — backward compat at module boundaries
// ---------------------------------------------------------------------------

impl From<DriverError> for String {
    fn from(e: DriverError) -> Self {
        e.to_string()
    }
}

impl From<AgentLoopError> for String {
    fn from(e: AgentLoopError) -> Self {
        e.to_string()
    }
}

impl From<SessionError> for String {
    fn from(e: SessionError) -> Self {
        e.to_string()
    }
}
