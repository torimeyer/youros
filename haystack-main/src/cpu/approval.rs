//! Tool approval types for governed permission mode (->751).
//!
//! When an agent calls a tool that isn't pre-approved and the permission mode
//! is `Governed`, the loop pauses and sends an `ApprovalRequest` through a
//! channel so the human operator can allow or deny the call.

use tokio::sync::oneshot;

/// A request for human approval of a tool call.
#[derive(Debug)]
pub struct ApprovalRequest {
    pub agent_alias: String,
    pub tool_name: String,
    pub tool_input_preview: String, // full JSON tool input for approval overlay parsing
    pub response_tx: oneshot::Sender<ApprovalResponse>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ApprovalResponse {
    Allow,
    Deny,
    AlwaysAllow, // allow this tool for rest of session
}
