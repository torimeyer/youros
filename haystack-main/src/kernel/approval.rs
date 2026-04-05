//! Kernel-mediated tool approval (→824, →852).
//!
//! Used by daemon wire protocol only. The TUI path uses session-scoped
//! response channels (→bidirectional) — see `AgentResponse` in `cpu::agent_loop`
//! and `response_tx`/`response_rx` on `cpu::session::AgentSession`.
//!
//! Approval requests flow through a module-level queue (OnceLock<Mutex>):
//!   1. Agent calls `request()` → stored in pending queue, broadcast to displays
//!   2. Any display driver calls `next_pending()` → gets oldest request
//!   3. Display driver calls `respond(id, decision)` → agent unblocks
//!   4. Timeout (300s) → auto-deny
//!
//! →852: Replaced lazy_static with std::sync::OnceLock (no external dep,
//! same thread-safety, no risk of deadlock from async-in-lock).
//!
//! Benefits:
//! - No dangling oneshot (bus owns the lifecycle)
//! - Works across daemon socket (broadcast is already wired)
//! - Any display driver can handle approval (TUI, web, CLI)
//! - Auditable (decision logged via broadcast)

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use tokio::sync::oneshot;

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

/// A request for human approval of a tool call.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ApprovalRequest {
    pub id: String,
    pub agent_alias: String,
    pub tool_name: String,
    pub tool_input_preview: String,
}

/// The human operator's decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ApprovalDecision {
    Allow,
    Deny,
    AlwaysAllow,
}

struct PendingApproval {
    request: ApprovalRequest,
    response_tx: oneshot::Sender<ApprovalDecision>,
}

/// Pending approval queue. Initialized on first access via `OnceLock`
/// (replaces lazy_static — no external dependency, same thread-safety).
static PENDING: OnceLock<Mutex<VecDeque<PendingApproval>>> = OnceLock::new();

fn pending() -> &'static Mutex<VecDeque<PendingApproval>> {
    PENDING.get_or_init(|| Mutex::new(VecDeque::new()))
}

// ── Agent side ──────────────────────────────────────────────────────

/// Request approval for a tool call. Blocks until the display driver responds
/// or the timeout expires (300s → auto-deny). Broadcasts `approval.requested`
/// for remote display drivers.
pub async fn request(agent: &str, tool: &str, preview: &str) -> ApprovalDecision {
    let id = format!("approval-{}", NEXT_ID.fetch_add(1, Ordering::Relaxed));
    tracing::info!(id = %id, tool = %tool, "approval: request pending");
    let (tx, rx) = oneshot::channel();

    let req = ApprovalRequest {
        id: id.clone(),
        agent_alias: agent.to_string(),
        tool_name: tool.to_string(),
        tool_input_preview: preview.to_string(),
    };

    // Store in pending queue
    pending()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .push_back(PendingApproval {
            request: req,
            response_tx: tx,
        });

    // Broadcast for any connected display drivers (fcp-screen, future web UI)
    // Fire-and-forget: broadcast is async but we don't need to wait for delivery.
    // Spawn a task so the request() caller isn't blocked by subscriber I/O.
    let broadcast_id = id.clone();
    let broadcast_agent = agent.to_string();
    let broadcast_tool = tool.to_string();
    let broadcast_preview = preview.to_string();
    tokio::spawn(async move {
        crate::serve::socket::BROADCAST.broadcast(serde_json::json!({
            "event": "approval.requested",
            "id": broadcast_id,
            "agent": broadcast_agent,
            "tool": broadcast_tool,
            "preview": broadcast_preview,
        })).await;
    });

    // Await response with timeout
    match tokio::time::timeout(Duration::from_secs(300), rx).await {
        Ok(Ok(decision)) => {
            tracing::info!(id = %id, decision = ?decision, "approval: responded");
            decision
        }
        Ok(Err(_)) => {
            // Channel dropped (display driver exited) → deny
            tracing::warn!(id = %id, "approval: channel dropped — auto-deny");
            cleanup(&id);
            ApprovalDecision::Deny
        }
        Err(_) => {
            // Timeout → deny
            tracing::warn!(id = %id, "approval: timeout — auto-deny");
            // →838: Surface timeout to user via stderr so it's visible in logs
            eprintln!("[approval] tool approval timed out after 300s — auto-denied (id={id})");
            cleanup(&id);
            ApprovalDecision::Deny
        }
    }
}

// ── Display side ────────────────────────────────────────────────────

/// Get the oldest pending approval request (FIFO). Non-blocking.
/// Returns None if no requests are pending.
pub fn next_pending() -> Option<ApprovalRequest> {
    pending()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .front()
        .map(|p| p.request.clone())
}

/// Respond to a pending approval request by ID.
/// Returns true if the request was found and responded to.
pub fn respond(id: &str, decision: ApprovalDecision) -> bool {
    tracing::info!(id = %id, decision = ?decision, "approval: responded");
    let mut lock = pending().lock().unwrap_or_else(|e| e.into_inner());
    if let Some(idx) = lock.iter().position(|p| p.request.id == id) {
        let pending = lock.remove(idx).unwrap();
        let _ = pending.response_tx.send(decision);
        true
    } else {
        false
    }
}

/// Deny all pending requests. Called on TUI exit / panic for clean shutdown.
pub fn deny_all() {
    let mut lock = pending().lock().unwrap_or_else(|e| e.into_inner());
    while let Some(pending) = lock.pop_front() {
        let _ = pending.response_tx.send(ApprovalDecision::Deny);
    }
}

/// Number of pending approval requests.
pub fn pending_count() -> usize {
    pending()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .len()
}

// ── Unified approval source ────────────────────────────────────────
//
// →903: Unifies TUI (session-scoped channel) and daemon (global bus)
// approval paths behind a single enum + method. Callers use
// `approval_source.request()` instead of branching on Option<Receiver>.

use tokio::sync::mpsc;

/// The two ways approval decisions can arrive.
pub enum ApprovalSource {
    /// TUI path: session-scoped channel pair (event_tx for requests, rx for responses).
    Channel(mpsc::Receiver<super::super::cpu::agent_loop::AgentResponse>),
    /// Daemon path: global approval_bus module.
    Bus,
}

impl ApprovalSource {
    /// Request approval for a tool call. Sends `ApprovalNeeded` through the
    /// event channel (Channel path) or the global bus (Bus path), then awaits
    /// the operator's decision with a 300s timeout.
    pub async fn request(
        &mut self,
        tool_name: &str,
        preview: &str,
        event_tx: &mpsc::Sender<super::super::cpu::agent_loop::CpuEvent>,
    ) -> ApprovalDecision {
        match self {
            Self::Channel(rx) => {
                use super::super::cpu::agent_loop::{AgentResponse, CpuEvent};
                let _ = event_tx.send(CpuEvent::ApprovalNeeded {
                    tool_name: tool_name.to_string(),
                    tool_input_preview: preview.to_string(),
                }).await;

                match tokio::time::timeout(
                    Duration::from_secs(300),
                    rx.recv(),
                ).await {
                    Ok(Some(AgentResponse::Approval(d))) => d,
                    Ok(Some(AgentResponse::Cancel)) => {
                        // Caller must handle Cancel → we map to Deny and let the
                        // caller check for cancel separately.
                        ApprovalDecision::Deny
                    }
                    Ok(Some(AgentResponse::Redirect(_))) => {
                        // Redirect during approval → map to Deny; the redirect
                        // will be picked up by try_recv in the tool loop.
                        ApprovalDecision::Deny
                    }
                    Ok(None) => ApprovalDecision::Deny,  // channel dropped
                    Err(_) => ApprovalDecision::Deny,     // 300s timeout
                }
            }
            Self::Bus => {
                request("agent", tool_name, preview).await
            }
        }
    }

    /// Non-blocking check for a Cancel or Redirect message on the underlying channel.
    /// Returns the AgentResponse if one was received. Bus variant always returns None.
    pub fn try_recv(&mut self) -> Option<super::super::cpu::agent_loop::AgentResponse> {
        match self {
            Self::Channel(rx) => rx.try_recv().ok(),
            Self::Bus => None,
        }
    }

    /// Convenience: returns true if Cancel was received. Discards Redirect.
    /// Kept for backward compatibility with callers that only care about cancel.
    pub fn try_recv_cancel(&mut self) -> bool {
        matches!(self.try_recv(), Some(super::super::cpu::agent_loop::AgentResponse::Cancel))
    }
}

fn cleanup(id: &str) {
    let mut lock = pending().lock().unwrap_or_else(|e| e.into_inner());
    lock.retain(|p| p.request.id != id);
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: inject a pending approval directly (bypasses broadcast).
    fn inject_pending() -> (ApprovalRequest, oneshot::Receiver<ApprovalDecision>) {
        let id = format!("test-{}", NEXT_ID.fetch_add(1, Ordering::Relaxed));
        let req = ApprovalRequest {
            id: id.clone(),
            agent_alias: "test-agent".into(),
            tool_name: "Bash".into(),
            tool_input_preview: "echo hello".into(),
        };
        let (tx, rx) = oneshot::channel();
        let mut lock = pending().lock().unwrap();
        lock.push_back(PendingApproval {
            request: req.clone(),
            response_tx: tx,
        });
        (req, rx)
    }

    #[test]
    fn next_pending_returns_fifo_order() {
        // Clean state
        deny_all();

        let (req1, _rx1) = inject_pending();
        let (req2, _rx2) = inject_pending();

        let first = next_pending().expect("should have a pending request");
        assert_eq!(first.id, req1.id, "should return oldest first (FIFO)");

        // Still returns the same one (peek, not pop)
        let again = next_pending().expect("should still be pending");
        assert_eq!(again.id, req1.id);

        // Respond to first, then next should be second
        respond(&req1.id, ApprovalDecision::Allow);
        let second = next_pending().expect("second should now be front");
        assert_eq!(second.id, req2.id);

        deny_all();
    }

    #[test]
    fn respond_sends_decision_to_channel() {
        deny_all();

        let (req, rx) = inject_pending();
        let found = respond(&req.id, ApprovalDecision::AlwaysAllow);
        assert!(found, "should find and respond to request");

        let decision = rx.blocking_recv().expect("channel should deliver");
        assert_eq!(decision, ApprovalDecision::AlwaysAllow);
    }

    #[test]
    fn respond_unknown_id_returns_false() {
        deny_all();
        assert!(!respond("nonexistent-id", ApprovalDecision::Deny));
    }

    #[test]
    fn deny_all_drains_queue() {
        inject_pending();
        inject_pending();
        inject_pending();

        assert!(pending_count() >= 3);
        deny_all();
        assert_eq!(pending_count(), 0, "deny_all should drain all pending");
    }

    #[test]
    fn deny_all_sends_deny_to_all_channels() {
        deny_all();

        let (_req1, rx1) = inject_pending();
        let (_req2, rx2) = inject_pending();

        deny_all();

        assert_eq!(rx1.blocking_recv().unwrap(), ApprovalDecision::Deny);
        assert_eq!(rx2.blocking_recv().unwrap(), ApprovalDecision::Deny);
    }

    #[tokio::test]
    async fn channel_drop_returns_deny() {
        // If the response channel is dropped (display driver exited),
        // the agent side should get an error, simulating auto-deny.
        let (tx, rx) = oneshot::channel::<ApprovalDecision>();
        drop(tx); // Simulate display driver crash
        assert!(rx.await.is_err(), "dropped sender should error on recv");
    }
}
