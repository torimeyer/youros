use crate::find_project_root;
use serde_json::Value;
use std::fs;

pub fn run(filter: Option<&str>, last_n: Option<usize>) -> Result<(), String> {
    let root = find_project_root()?;
    let audit_path = root.join("audit.jsonl");

    if !audit_path.exists() {
        return Ok(());
    }

    let content =
        fs::read_to_string(&audit_path).map_err(|e| format!("failed to read audit.jsonl: {e}"))?;

    let mut events: Vec<Value> = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let v: Value =
            serde_json::from_str(line).map_err(|e| format!("malformed audit line: {e}"))?;
        events.push(v);
    }

    // Apply filter
    if let Some(f) = filter {
        events.retain(|v| {
            v.get("event")
                .and_then(|e| e.as_str())
                .map(|e| e.contains(f))
                .unwrap_or(false)
        });
    }

    // Apply last_n
    if let Some(n) = last_n
        && events.len() > n
    {
        events = events.into_iter().rev().take(n).rev().collect();
    }

    for event in &events {
        let ts = event
            .get("timestamp")
            .and_then(|v| v.as_str())
            .unwrap_or("?");
        let ev = event.get("event").and_then(|v| v.as_str()).unwrap_or("?");

        let details = format_details(ev, event);
        println!("[{ts}] {ev}: {details}");
    }

    Ok(())
}

fn format_details(event_type: &str, v: &Value) -> String {
    let s = |key: &str| {
        v.get(key)
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string()
    };

    match event_type {
        "draft.created" => s("path"),
        "spec.promoted" => {
            let from = s("from");
            let to = s("to");
            if !from.is_empty() && !to.is_empty() {
                format!("{from} → {to}")
            } else {
                s("path")
            }
        }
        "beads.created" => {
            let path = s("path");
            let ids = v
                .get("bead_ids")
                .and_then(|x| x.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|x| x.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default();
            if ids.is_empty() {
                path
            } else {
                format!("{path} ({ids})")
            }
        }
        "spec.amended" => {
            let path = s("path");
            let severity = s("severity");
            if severity.is_empty() {
                path
            } else {
                format!("{path} [{severity}]")
            }
        }
        "bead.shelved" => s("id"),
        "bead.unshelved" => s("id"),
        "task.closed" | "bead.closed" => {
            let id = s("id");
            let reason = s("reason");
            if reason.is_empty() {
                id
            } else {
                format!("{id} ({reason})")
            }
        }
        "project.initialized" => s("path"),
        _ => {
            // Generic: show path or id if present
            let path = s("path");
            if !path.is_empty() {
                return path;
            }
            let id = s("id");
            if !id.is_empty() {
                return id;
            }
            // Fallback: show all non-event, non-timestamp fields
            v.as_object()
                .map(|m| {
                    m.iter()
                        .filter(|(k, _)| k.as_str() != "event" && k.as_str() != "timestamp")
                        .map(|(k, val)| format!("{k}={val}"))
                        .collect::<Vec<_>>()
                        .join(" ")
                })
                .unwrap_or_default()
        }
    }
}
