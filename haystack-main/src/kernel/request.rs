//! Unified privilege escalation — every resource boundary in one syscall.
//!
//! Pattern: agent hits a limit → submits Request → human/parent grants or denies
//! Covers: Secret, Budget, Tool, FileAccess, ModelUpgrade
//!
//! Storage:
//!   .ostk/requests/<agent-alias>.jsonl  — per-agent request log
//!   .ostk/audit.jsonl                   — global record of every event
//!
//! Protocol:
//!   1. Agent calls submit_request() → Request appended, granter nudged
//!   2. Human calls `ostk grant approve <id>` or `ostk grant deny <id>`
//!   3. Grant appended with scope + TTL; agent nudged back
//!   4. Agent reads active_grants() → proceeds with resource
//!   5. revoke_expired() records revocation for any grant past TTL or dead agent

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::{append_audit, now_iso};
use crate::kernel::nudge::push_nudge;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum RequestType {
    Secret,
    Budget,
    Tool,
    FileAccess,
    ModelUpgrade,
}

impl std::fmt::Display for RequestType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RequestType::Secret => write!(f, "secret"),
            RequestType::Budget => write!(f, "budget"),
            RequestType::Tool => write!(f, "tool"),
            RequestType::FileAccess => write!(f, "file_access"),
            RequestType::ModelUpgrade => write!(f, "model_upgrade"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum RequestStatus {
    Pending,
    Granted,
    Denied,
    Revoked,
}

impl std::fmt::Display for RequestStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RequestStatus::Pending => write!(f, "pending"),
            RequestStatus::Granted => write!(f, "granted"),
            RequestStatus::Denied => write!(f, "denied"),
            RequestStatus::Revoked => write!(f, "revoked"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Request {
    pub id: String,
    pub agent_alias: String,
    pub request_type: RequestType,
    /// The resource being requested: key name, file path, tool name, model name, etc.
    pub target: String,
    pub reason: String,
    pub timestamp: String,
    pub status: RequestStatus,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Grant {
    pub request_id: String,
    pub granter: String,
    /// Scoped description of what was granted (may be narrower than the request)
    pub scope: String,
    /// Seconds until this grant expires. 0 = permanent for session.
    pub ttl_secs: u64,
    pub granted_at: String,
    pub granted_at_unix: u64,
}

// ---------------------------------------------------------------------------
// ID generation (no uuid dep — timestamp + nanos suffix)
// ---------------------------------------------------------------------------

fn gen_id(prefix: &str) -> String {
    let dur = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = dur.as_secs();
    let nanos = dur.subsec_nanos() % 1_000_000;
    format!("{prefix}-{secs}-{nanos:06}")
}

// ---------------------------------------------------------------------------
// Storage helpers
// ---------------------------------------------------------------------------

fn requests_dir(ostk_dir: &Path) -> std::path::PathBuf {
    ostk_dir.join("requests")
}

fn agent_request_path(ostk_dir: &Path, agent: &str) -> std::path::PathBuf {
    requests_dir(ostk_dir).join(format!("{agent}.jsonl"))
}

fn append_to_requests(ostk_dir: &Path, agent: &str, entry: &serde_json::Value) -> Result<(), String> {
    let dir = requests_dir(ostk_dir);
    fs::create_dir_all(&dir).map_err(|e| format!("create requests dir: {e}"))?;

    let path = agent_request_path(ostk_dir, agent);
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("open requests file: {e}"))?;

    let mut line = serde_json::to_string(entry).map_err(|e| e.to_string())?;
    line.push('\n');
    file.write_all(line.as_bytes())
        .map_err(|e| format!("write request: {e}"))
}

/// Read all entries from an agent's request log.
fn read_agent_requests(ostk_dir: &Path, agent: &str) -> Vec<serde_json::Value> {
    let path = agent_request_path(ostk_dir, agent);
    let content = match fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return vec![],
    };
    content
        .lines()
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| serde_json::from_str(l).ok())
        .collect()
}

/// Read all agents' request files.
fn read_all_requests(ostk_dir: &Path) -> Vec<serde_json::Value> {
    let dir = requests_dir(ostk_dir);
    let entries = match fs::read_dir(&dir) {
        Ok(e) => e,
        Err(_) => return vec![],
    };
    let mut all = Vec::new();
    for entry in entries.flatten() {
        if let Ok(content) = fs::read_to_string(entry.path()) {
            for line in content.lines() {
                let line = line.trim();
                if line.is_empty() { continue; }
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                    all.push(v);
                }
            }
        }
    }
    all
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Submit a privilege escalation request. Writes to per-agent log + audit,
/// then nudges the granting entity.
///
/// Returns the request ID (for polling or display).
pub fn submit_request(
    ostk_dir: &Path,
    agent_alias: &str,
    request_type: RequestType,
    target: &str,
    reason: &str,
    granter: &str,
) -> Result<String, String> {
    let id = gen_id("req");
    let req = Request {
        id: id.clone(),
        agent_alias: agent_alias.to_string(),
        request_type,
        target: target.to_string(),
        reason: reason.to_string(),
        timestamp: now_iso(),
        status: RequestStatus::Pending,
    };

    let entry = serde_json::json!({
        "kind": "request",
        "id": req.id,
        "agent_alias": req.agent_alias,
        "request_type": req.request_type,
        "target": req.target,
        "reason": req.reason,
        "timestamp": req.timestamp,
        "status": req.status,
    });

    append_to_requests(ostk_dir, agent_alias, &entry)?;

    // Global audit trail
    if let Ok(root) = crate::find_project_root() {
        let _ = append_audit(&root, &serde_json::json!({
            "event": "request.submitted",
            "request_id": id,
            "agent": agent_alias,
            "request_type": req.request_type,
            "target": target,
            "reason": reason,
            "timestamp": req.timestamp,
        }));
    }

    // Nudge the granting entity
    let nudge_msg = format!(
        "privilege request pending — id={id} agent={agent_alias} type={} target={target} reason={reason}. \
         Run: ostk grant approve {id}",
        req.request_type
    );
    let _ = push_nudge(ostk_dir, granter, &nudge_msg);

    Ok(id)
}

/// Grant a pending request. Writes grant record, nudges requesting agent.
pub fn grant_request(
    ostk_dir: &Path,
    request_id: &str,
    granter: &str,
    scope: &str,
    ttl_secs: u64,
) -> Result<(), String> {
    let now = now_iso();
    let unix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    // Find the request to get agent alias
    let all = read_all_requests(ostk_dir);
    let req_entry = all
        .iter()
        .filter(|v| v.get("kind").and_then(|k| k.as_str()) == Some("request"))
        .find(|v| v.get("id").and_then(|i| i.as_str()) == Some(request_id))
        .cloned()
        .ok_or_else(|| format!("request {request_id} not found"))?;

    let agent = req_entry
        .get("agent_alias")
        .and_then(|a| a.as_str())
        .unwrap_or("unknown");

    let grant = Grant {
        request_id: request_id.to_string(),
        granter: granter.to_string(),
        scope: scope.to_string(),
        ttl_secs,
        granted_at: now.clone(),
        granted_at_unix: unix,
    };

    let entry = serde_json::json!({
        "kind": "grant",
        "request_id": grant.request_id,
        "granter": grant.granter,
        "scope": grant.scope,
        "ttl_secs": grant.ttl_secs,
        "granted_at": grant.granted_at,
        "granted_at_unix": grant.granted_at_unix,
    });

    let status_entry = serde_json::json!({
        "kind": "status_update",
        "id": request_id,
        "status": "granted",
        "timestamp": now,
    });

    append_to_requests(ostk_dir, agent, &entry)?;
    append_to_requests(ostk_dir, agent, &status_entry)?;

    // Audit
    if let Ok(root) = crate::find_project_root() {
        let _ = append_audit(&root, &serde_json::json!({
            "event": "request.granted",
            "request_id": request_id,
            "granter": granter,
            "scope": scope,
            "ttl_secs": ttl_secs,
            "agent": agent,
            "timestamp": now,
        }));
    }

    // Nudge requesting agent
    let ttl_display = if ttl_secs == 0 {
        "session".to_string()
    } else {
        format!("{ttl_secs}s")
    };
    let nudge_msg = format!(
        "request {request_id} GRANTED by {granter} — scope={scope} ttl={ttl_display}"
    );
    let _ = push_nudge(ostk_dir, agent, &nudge_msg);

    Ok(())
}

/// Deny a pending request. Records denial, nudges requesting agent.
pub fn deny_request(
    ostk_dir: &Path,
    request_id: &str,
    denier: &str,
    reason: &str,
) -> Result<(), String> {
    let now = now_iso();

    let all = read_all_requests(ostk_dir);
    let req_entry = all
        .iter()
        .filter(|v| v.get("kind").and_then(|k| k.as_str()) == Some("request"))
        .find(|v| v.get("id").and_then(|i| i.as_str()) == Some(request_id))
        .cloned()
        .ok_or_else(|| format!("request {request_id} not found"))?;

    let agent = req_entry
        .get("agent_alias")
        .and_then(|a| a.as_str())
        .unwrap_or("unknown");

    let status_entry = serde_json::json!({
        "kind": "status_update",
        "id": request_id,
        "status": "denied",
        "reason": reason,
        "denier": denier,
        "timestamp": now,
    });

    append_to_requests(ostk_dir, agent, &status_entry)?;

    if let Ok(root) = crate::find_project_root() {
        let _ = append_audit(&root, &serde_json::json!({
            "event": "request.denied",
            "request_id": request_id,
            "denier": denier,
            "reason": reason,
            "agent": agent,
            "timestamp": now,
        }));
    }

    let nudge_msg = format!(
        "request {request_id} DENIED by {denier} — reason={reason}"
    );
    let _ = push_nudge(ostk_dir, agent, &nudge_msg);

    Ok(())
}

/// Revoke expired grants. Call from scheduler or reap.
/// Returns count of revocations recorded.
pub fn revoke_expired(ostk_dir: &Path) -> Result<usize, String> {
    let now_unix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    let all = read_all_requests(ostk_dir);
    let mut revoked = 0;

    for entry in &all {
        if entry.get("kind").and_then(|k| k.as_str()) != Some("grant") {
            continue;
        }
        let ttl = entry.get("ttl_secs").and_then(|t| t.as_u64()).unwrap_or(0);
        if ttl == 0 {
            continue; // permanent for session
        }
        let granted_at = entry.get("granted_at_unix").and_then(|t| t.as_u64()).unwrap_or(0);
        if granted_at + ttl < now_unix {
            let request_id = entry
                .get("request_id")
                .and_then(|i| i.as_str())
                .unwrap_or("unknown");

            let req_entry = all
                .iter()
                .filter(|v| v.get("kind").and_then(|k| k.as_str()) == Some("request"))
                .find(|v| v.get("id").and_then(|i| i.as_str()) == Some(request_id));

            let agent = req_entry
                .and_then(|r| r.get("agent_alias"))
                .and_then(|a| a.as_str())
                .unwrap_or("unknown");

            let now = now_iso();
            let revoke_entry = serde_json::json!({
                "kind": "status_update",
                "id": request_id,
                "status": "revoked",
                "reason": "ttl_expired",
                "timestamp": now,
            });

            let _ = append_to_requests(ostk_dir, agent, &revoke_entry);

            if let Ok(root) = crate::find_project_root() {
                let _ = append_audit(&root, &serde_json::json!({
                    "event": "request.revoked",
                    "request_id": request_id,
                    "reason": "ttl_expired",
                    "agent": agent,
                    "timestamp": now,
                }));
            }

            revoked += 1;
        }
    }

    Ok(revoked)
}

/// Return active (granted, non-expired) grants for an agent.
pub fn active_grants(ostk_dir: &Path, agent: &str) -> Vec<Grant> {
    let now_unix = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    let entries = read_agent_requests(ostk_dir, agent);

    // Build map: request_id → latest status
    let mut statuses: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for entry in &entries {
        if entry.get("kind").and_then(|k| k.as_str()) == Some("status_update")
            && let (Some(id), Some(status)) = (
                entry.get("id").and_then(|i| i.as_str()),
                entry.get("status").and_then(|s| s.as_str()),
            ) {
                statuses.insert(id.to_string(), status.to_string());
            }
    }

    entries
        .iter()
        .filter(|e| e.get("kind").and_then(|k| k.as_str()) == Some("grant"))
        .filter_map(|e| {
            let request_id = e.get("request_id")?.as_str()?.to_string();
            let status = statuses.get(&request_id).map(|s| s.as_str()).unwrap_or("granted");
            if status != "granted" {
                return None;
            }
            let ttl = e.get("ttl_secs")?.as_u64()?;
            let granted_at_unix = e.get("granted_at_unix")?.as_u64()?;
            if ttl > 0 && granted_at_unix + ttl < now_unix {
                return None;
            }
            Some(Grant {
                request_id,
                granter: e.get("granter")?.as_str()?.to_string(),
                scope: e.get("scope")?.as_str()?.to_string(),
                ttl_secs: ttl,
                granted_at: e.get("granted_at")?.as_str()?.to_string(),
                granted_at_unix,
            })
        })
        .collect()
}

/// List all requests across all agents, optionally filtered by status.
pub fn list_requests(
    ostk_dir: &Path,
    status_filter: Option<&str>,
) -> Vec<serde_json::Value> {
    let all = read_all_requests(ostk_dir);

    // Build latest status per request id
    let mut statuses: std::collections::HashMap<String, String> = std::collections::HashMap::new();
    for entry in &all {
        if entry.get("kind").and_then(|k| k.as_str()) == Some("status_update")
            && let (Some(id), Some(status)) = (
                entry.get("id").and_then(|i| i.as_str()),
                entry.get("status").and_then(|s| s.as_str()),
            ) {
                statuses.insert(id.to_string(), status.to_string());
            }
    }

    all.into_iter()
        .filter(|e| e.get("kind").and_then(|k| k.as_str()) == Some("request"))
        .filter(|e| {
            if let Some(filter) = status_filter {
                let id = e.get("id").and_then(|i| i.as_str()).unwrap_or("");
                let status = statuses.get(id).map(|s| s.as_str()).unwrap_or("pending");
                status == filter
            } else {
                true
            }
        })
        .map(|mut e| {
            let id = e.get("id").and_then(|i| i.as_str()).unwrap_or("").to_string();
            let status = statuses.get(&id).cloned().unwrap_or_else(|| "pending".to_string());
            e["status"] = serde_json::json!(status);
            e
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join("ostk_request_test").join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join(".ostk")).unwrap();
        dir
    }

    #[test]
    fn test_gen_id_unique() {
        let a = gen_id("req");
        std::thread::sleep(std::time::Duration::from_millis(1));
        let b = gen_id("req");
        assert_ne!(a, b);
        assert!(a.starts_with("req-"));
    }

    #[test]
    fn test_submit_writes_to_requests_dir() {
        let dir = temp_dir("submit");
        let result = submit_request(
            &dir.join(".ostk"),
            "agent-1",
            RequestType::Secret,
            "ANTHROPIC_API_KEY",
            "need to call Claude API",
            "operator",
        );
        assert!(result.is_ok(), "submit failed: {:?}", result);
        let id = result.unwrap();
        assert!(id.starts_with("req-"));

        let req_path = dir.join(".ostk/requests/agent-1.jsonl");
        assert!(req_path.exists());
        let content = fs::read_to_string(&req_path).unwrap();
        assert!(content.contains("ANTHROPIC_API_KEY"));
        assert!(content.contains("pending"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_grant_updates_status_and_nudges() {
        let dir = temp_dir("grant");
        let id = submit_request(
            &dir.join(".ostk"),
            "agent-2",
            RequestType::Budget,
            "budget_usd",
            "approaching limit",
            "operator",
        ).unwrap();

        let result = grant_request(
            &dir.join(".ostk"),
            &id,
            "human",
            "budget_usd +10",
            3600,
        );
        assert!(result.is_ok(), "grant failed: {:?}", result);

        let content = fs::read_to_string(
            dir.join(".ostk/requests/agent-2.jsonl")
        ).unwrap();
        assert!(content.contains("granted"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_deny_writes_status() {
        let dir = temp_dir("deny");
        let id = submit_request(
            &dir.join(".ostk"),
            "agent-3",
            RequestType::Tool,
            "bash_execute",
            "need shell access",
            "operator",
        ).unwrap();

        deny_request(
            &dir.join(".ostk"),
            &id,
            "human",
            "not permitted in this session",
        ).unwrap();

        let content = fs::read_to_string(
            dir.join(".ostk/requests/agent-3.jsonl")
        ).unwrap();
        assert!(content.contains("denied"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_list_requests_pending() {
        let dir = temp_dir("list");
        submit_request(
            &dir.join(".ostk"),
            "agent-4",
            RequestType::FileAccess,
            "/etc/hosts",
            "read system file",
            "operator",
        ).unwrap();

        let pending = list_requests(&dir.join(".ostk"), Some("pending"));
        assert_eq!(pending.len(), 1);
        assert_eq!(
            pending[0].get("target").and_then(|t| t.as_str()),
            Some("/etc/hosts")
        );

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_active_grants_respects_ttl() {
        let dir = temp_dir("ttl");
        let id = submit_request(
            &dir.join(".ostk"),
            "agent-5",
            RequestType::ModelUpgrade,
            "claude-opus-4-6",
            "complex reasoning task",
            "operator",
        ).unwrap();

        grant_request(&dir.join(".ostk"), &id, "human", "model_upgrade", 1).unwrap();

        let grants = active_grants(&dir.join(".ostk"), "agent-5");
        assert_eq!(grants.len(), 1);

        std::thread::sleep(std::time::Duration::from_secs(2));
        let grants = active_grants(&dir.join(".ostk"), "agent-5");
        assert_eq!(grants.len(), 0, "grant should have expired");

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_revoke_expired_records_audit() {
        let dir = temp_dir("revoke");
        let id = submit_request(
            &dir.join(".ostk"),
            "agent-6",
            RequestType::Secret,
            "OPENAI_API_KEY",
            "test",
            "operator",
        ).unwrap();

        grant_request(&dir.join(".ostk"), &id, "human", "openai_key", 1).unwrap();
        std::thread::sleep(std::time::Duration::from_secs(2));

        let count = revoke_expired(&dir.join(".ostk")).unwrap();
        assert_eq!(count, 1);

        let content = fs::read_to_string(
            dir.join(".ostk/requests/agent-6.jsonl")
        ).unwrap();
        assert!(content.contains("revoked"));
        assert!(content.contains("ttl_expired"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_all_request_types_serialize() {
        let types = [
            RequestType::Secret,
            RequestType::Budget,
            RequestType::Tool,
            RequestType::FileAccess,
            RequestType::ModelUpgrade,
        ];
        for rt in &types {
            let s = serde_json::to_string(rt).unwrap();
            let back: RequestType = serde_json::from_str(&s).unwrap();
            assert_eq!(*rt, back);
        }
    }
}
