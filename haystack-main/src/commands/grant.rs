//! `ostk grant` — human-side privilege escalation interface.
//!
//! `ostk grant list`                       — show all pending requests
//! `ostk grant approve <id>`               — grant (session TTL)
//! `ostk grant approve <id> --ttl 3600`    — grant with 1h TTL
//! `ostk grant deny <id> --reason "..."`   — deny a request

use crate::kernel::request::{self, RequestType};
use crate::find_project_root;

pub fn run_list(status: Option<&str>, json: bool) -> Result<(), String> {
    let root = find_project_root()?;
    let hs = crate::state_dir(&root);
    let filter = status.unwrap_or("pending");
    let requests = request::list_requests(&hs, Some(filter));

    if requests.is_empty() {
        println!("no {filter} requests");
        return Ok(());
    }

    if json {
        println!("{}", serde_json::to_string_pretty(&requests).unwrap_or_default());
        return Ok(());
    }

    println!("{filter} requests ({}):", requests.len());
    println!();
    for req in &requests {
        let id     = req.get("id").and_then(|v| v.as_str()).unwrap_or("?");
        let agent  = req.get("agent_alias").and_then(|v| v.as_str()).unwrap_or("?");
        let rtype  = req.get("request_type").and_then(|v| v.as_str()).unwrap_or("?");
        let target = req.get("target").and_then(|v| v.as_str()).unwrap_or("?");
        let reason = req.get("reason").and_then(|v| v.as_str()).unwrap_or("?");
        let ts     = req.get("timestamp").and_then(|v| v.as_str()).unwrap_or("?");
        println!("  {id}");
        println!("    agent:   {agent}");
        println!("    type:    {rtype}");
        println!("    target:  {target}");
        println!("    reason:  {reason}");
        println!("    time:    {ts}");
        println!("    approve: ostk grant approve {id}");
        println!("    deny:    ostk grant deny {id} --reason \"...\"");
        println!();
    }

    Ok(())
}

pub fn run_approve(request_id: &str, ttl_secs: u64, scope: Option<&str>) -> Result<(), String> {
    let root = find_project_root()?;
    let hs = crate::state_dir(&root);

    // Default scope: the requested target
    let resolved_scope = if let Some(s) = scope {
        s.to_string()
    } else {
        request::list_requests(&hs, None)
            .iter()
            .find(|r| r.get("id").and_then(|i| i.as_str()) == Some(request_id))
            .and_then(|r| r.get("target").and_then(|t| t.as_str()))
            .unwrap_or(request_id)
            .to_string()
    };

    request::grant_request(&hs, request_id, "operator", &resolved_scope, ttl_secs)?;

    let ttl_display = if ttl_secs == 0 { "session".to_string() } else { format!("{ttl_secs}s") };
    println!("granted {request_id} — scope={resolved_scope} ttl={ttl_display}");
    println!("agent nudged");
    Ok(())
}

pub fn run_deny(request_id: &str, reason: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let hs = crate::state_dir(&root);
    request::deny_request(&hs, request_id, "operator", reason)?;
    println!("denied {request_id}");
    Ok(())
}

pub fn run_show(request_id: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let hs = crate::state_dir(&root);
    let all = request::list_requests(&hs, None);
    let req = all
        .iter()
        .find(|r| r.get("id").and_then(|i| i.as_str()) == Some(request_id))
        .ok_or_else(|| format!("request {request_id} not found"))?;
    println!("{}", serde_json::to_string_pretty(req).unwrap_or_default());
    Ok(())
}

pub fn run_types() -> Result<(), String> {
    println!("request types:");
    for rt in [
        RequestType::Secret,
        RequestType::Budget,
        RequestType::Tool,
        RequestType::FileAccess,
        RequestType::ModelUpgrade,
    ] {
        println!("  {rt}");
    }
    Ok(())
}
