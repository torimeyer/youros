use crate::find_project_root;
use serde_json::Value;
use std::fs;

/// Run the history command: chronological audit trail for a needle or topic.
pub fn run(target: Option<&str>, last_n: Option<usize>) -> Result<(), String> {
    let root = find_project_root()?;

    // Collect events from audit.jsonl
    let audit_path = crate::state_dir(&root).join("audit.jsonl");
    let mut events: Vec<Value> = Vec::new();
    if audit_path.exists() {
        let content = fs::read_to_string(&audit_path)
            .map_err(|e| format!("failed to read audit.jsonl: {e}"))?;
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                events.push(v);
            }
        }
    }

    // Collect events from needles/issues.jsonl (needle lifecycle data)
    let needles_path = crate::state_dir(&root).join("needles/issues.jsonl");
    if needles_path.exists() {
        let content = fs::read_to_string(&needles_path)
            .map_err(|e| format!("failed to read issues.jsonl: {e}"))?;
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                // Synthesize events from needle records that aren't in audit
                let mut synthesized = synthesize_needle_events(&v);
                events.append(&mut synthesized);
            }
        }
    }

    // Sort by timestamp
    events.sort_by(|a, b| {
        let ts_a = extract_sort_ts(a);
        let ts_b = extract_sort_ts(b);
        ts_a.cmp(&ts_b)
    });

    // Deduplicate: audit.jsonl has task.added/task.closed events AND issues.jsonl
    // has the same data as synthesized events. Remove synthesized duplicates
    // when the audit already has the real event.
    dedup_events(&mut events);

    // Filter
    match (target, last_n) {
        (Some(t), _) => {
            let needle_id = if is_needle_id(t) {
                Some(normalize_needle_id(t))
            } else {
                None
            };

            events.retain(|v| {
                if let Some(ref nid) = needle_id {
                    event_matches_needle(v, nid)
                } else {
                    event_matches_text(v, t)
                }
            });
        }
        (None, Some(n)) => {
            if events.len() > n {
                events = events.into_iter().rev().take(n).rev().collect();
            }
        }
        (None, None) => {
            // No filter, no limit — show everything (could be large)
            // Default to last 20 if unfiltered
            if events.len() > 20 {
                events = events.into_iter().rev().take(20).rev().collect();
            }
        }
    }

    if events.is_empty() {
        println!("no events found");
        return Ok(());
    }

    for event in &events {
        println!("{}", format_event(event));
    }

    Ok(())
}

/// Check if a target looks like a needle ID.
fn is_needle_id(target: &str) -> bool {
    target.starts_with('→')
        || target.starts_with("bd-")
        || target.starts_with("nd-")
}

/// Normalize needle ID to canonical form (→NNN) plus keep original for matching.
fn normalize_needle_id(id: &str) -> String {
    if let Some(rest) = id.strip_prefix("bd-")
        && let Ok(n) = rest.parse::<u64>() {
            return format!("→{n:03}");
        }
    if let Some(rest) = id.strip_prefix("nd-")
        && let Ok(n) = rest.parse::<u64>() {
            return format!("→{n:03}");
        }
    id.to_string()
}

/// Check if an event references a specific needle ID.
fn event_matches_needle(v: &Value, needle_id: &str) -> bool {
    // Also generate the bd- form for matching legacy data
    let bd_form = if let Some(rest) = needle_id.strip_prefix('→') {
        if let Ok(n) = rest.parse::<u64>() {
            Some(format!("bd-{n:03}"))
        } else {
            None
        }
    } else {
        None
    };

    let forms: Vec<&str> = {
        let mut v = vec![needle_id];
        if let Some(ref bd) = bd_form {
            v.push(bd.as_str());
        }
        v
    };

    // Check known ID fields
    for field in &["id", "bead", "bead_id"] {
        if let Some(val) = v.get(field).and_then(|x| x.as_str()) {
            for form in &forms {
                if val == *form {
                    return true;
                }
            }
        }
    }

    // Check needles array (thread events)
    if let Some(arr) = v.get("needles").and_then(|x| x.as_array()) {
        for item in arr {
            if let Some(s) = item.as_str() {
                for form in &forms {
                    if s == *form {
                        return true;
                    }
                }
            }
        }
    }

    // Check text fields for needle ID mention
    for field in &["title", "reason", "close_reason", "straw", "message", "desc"] {
        if let Some(val) = v.get(field).and_then(|x| x.as_str()) {
            for form in &forms {
                if val.contains(*form) {
                    return true;
                }
            }
        }
    }

    false
}

/// Check if an event matches a text search (case-insensitive).
fn event_matches_text(v: &Value, query: &str) -> bool {
    let lower_query = query.to_lowercase();

    // Search across all string values in the event
    if let Some(obj) = v.as_object() {
        for (_key, val) in obj {
            match val {
                Value::String(s) => {
                    if s.to_lowercase().contains(&lower_query) {
                        return true;
                    }
                }
                Value::Array(arr) => {
                    for item in arr {
                        if let Some(s) = item.as_str()
                            && s.to_lowercase().contains(&lower_query) {
                                return true;
                            }
                    }
                }
                _ => {}
            }
        }
    }
    false
}

/// Synthesize audit-style events from a needle record in issues.jsonl.
/// Only synthesizes if the needle has fields that wouldn't be in audit.jsonl.
fn synthesize_needle_events(needle: &Value) -> Vec<Value> {
    let mut events = Vec::new();
    let id = needle.get("id").and_then(|x| x.as_str()).unwrap_or("");
    if id.is_empty() {
        return events;
    }

    // Mark synthesized events so we can dedup against real audit events
    let title = needle.get("title").and_then(|x| x.as_str()).unwrap_or("");

    // Created event
    if let Some(created) = needle.get("created_at").and_then(|x| x.as_str()) {
        events.push(serde_json::json!({
            "event": "task.added",
            "id": id,
            "title": title,
            "timestamp": created,
            "_synthesized": true,
        }));
    }

    // Closed event
    if needle.get("status").and_then(|x| x.as_str()) == Some("closed")
        && let Some(closed) = needle.get("closed_at").and_then(|x| x.as_str()) {
            let reason = needle.get("close_reason").and_then(|x| x.as_str()).unwrap_or("");
            events.push(serde_json::json!({
                "event": "task.closed",
                "id": id,
                "reason": reason,
                "timestamp": closed,
                "_synthesized": true,
            }));
        }

    events
}

/// Remove synthesized events when a real audit event already covers it.
fn dedup_events(events: &mut Vec<Value>) {
    // Build a set of (event_type, id, timestamp) from non-synthesized events
    let mut seen: std::collections::HashSet<(String, String, String)> =
        std::collections::HashSet::new();

    for ev in events.iter() {
        if ev.get("_synthesized").is_some() {
            continue;
        }
        let event_type = ev.get("event").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let id = ev.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let ts = extract_sort_ts(ev);
        if !id.is_empty() {
            seen.insert((event_type, id, ts));
        }
    }

    // Remove synthesized events that duplicate real ones
    events.retain(|ev| {
        if ev.get("_synthesized").is_none() {
            return true;
        }
        let event_type = ev.get("event").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let id = ev.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let ts = extract_sort_ts(ev);
        !seen.contains(&(event_type, id, ts))
    });
}

/// Extract a timestamp string for sorting. Handles both Z and offset formats.
fn extract_sort_ts(v: &Value) -> String {
    v.get("timestamp")
        .and_then(|x| x.as_str())
        .unwrap_or("0000-00-00T00:00:00Z")
        .to_string()
}

/// Format a single event as an fcp-style one-liner.
fn format_event(v: &Value) -> String {
    let ts = v.get("timestamp").and_then(|x| x.as_str()).unwrap_or("?");
    let event_type = v.get("event").and_then(|x| x.as_str()).unwrap_or("?");

    // Pad event type to fixed width for alignment
    let padded_event = format!("{:<18}", event_type);

    let detail = format_detail(event_type, v);

    format!("[{ts}] {padded_event}{detail}")
}

/// Format the detail portion of an event line.
fn format_detail(event_type: &str, v: &Value) -> String {
    let s = |key: &str| -> String {
        v.get(key)
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string()
    };

    match event_type {
        "task.added" => {
            let id = s("id");
            let title = s("title");
            let title = truncate(&title, 120);
            format!("{id} {title}")
        }
        "task.closed" => {
            let id = s("id");
            let reason = s("reason");
            let reason = truncate(&reason, 100);
            if reason.is_empty() {
                id
            } else {
                format!("{id} reason: {reason}")
            }
        }
        "task.claimed" => {
            let id = s("id");
            let assignee = s("assignee");
            if assignee.is_empty() {
                id
            } else {
                format!("{id} assignee: {assignee}")
            }
        }
        "bead.committed" => {
            let bead = s("bead");
            let commit = s("commit");
            let short_commit = if commit.len() > 8 {
                &commit[..8]
            } else {
                &commit
            };
            let spec = s("spec_ref");
            let agent = s("agent");
            let msg = s("message");
            let msg = truncate(&msg, 80);
            let mut parts = Vec::new();
            if !bead.is_empty() {
                parts.push(bead);
            }
            if !short_commit.is_empty() {
                parts.push(short_commit.to_string());
            }
            if !spec.is_empty() {
                parts.push(format!("spec:{spec}"));
            }
            if !agent.is_empty() {
                parts.push(format!("agent:{agent}"));
            }
            if !msg.is_empty() {
                parts.push(msg);
            }
            parts.join(" ")
        }
        "hay.filed" => {
            let straw = s("straw");
            let source = s("source");
            let straw = truncate(&straw, 100);
            if source.is_empty() {
                format!("~ {straw}")
            } else {
                format!("({source}) ~ {straw}")
            }
        }
        "hay.compiled" => {
            let straw = s("straw");
            let result = s("result");
            let straw = truncate(&straw, 80);
            format!("→{result} ~ {straw}")
        }
        "thread.created" => {
            let name = s("name");
            let needles = v
                .get("needles")
                .and_then(|x| x.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default();
            format!("{name} [{needles}]")
        }
        "draft.created" => {
            let path = s("path");
            let title = s("title");
            if title.is_empty() {
                path
            } else {
                format!("{title} ({path})")
            }
        }
        "spec.promoted" => {
            let from = s("from");
            let to = s("to");
            if !from.is_empty() && !to.is_empty() {
                format!("{from} → {to}")
            } else {
                s("path")
            }
        }
        "project.installed" => {
            let symlinks = v
                .get("symlinks_created")
                .and_then(|x| x.as_u64())
                .unwrap_or(0);
            format!("symlinks:{symlinks}")
        }
        _ => {
            // Generic: show id, path, or all non-meta fields
            let id = s("id");
            if !id.is_empty() {
                return id;
            }
            let path = s("path");
            if !path.is_empty() {
                return path;
            }
            v.as_object()
                .map(|m| {
                    m.iter()
                        .filter(|(k, _)| {
                            !matches!(
                                k.as_str(),
                                "event" | "timestamp" | "_synthesized"
                            )
                        })
                        .map(|(k, val)| format!("{k}={val}"))
                        .collect::<Vec<_>>()
                        .join(" ")
                })
                .unwrap_or_default()
        }
    }
}

/// Truncate a string to max length, appending "..." if truncated.
fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        let end = s.floor_char_boundary(max);
        format!("{}...", &s[..end])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_needle_id() {
        assert!(is_needle_id("→428"));
        assert!(is_needle_id("bd-001"));
        assert!(is_needle_id("nd-42"));
        assert!(!is_needle_id("Tack"));
        assert!(!is_needle_id("some text"));
    }

    #[test]
    fn test_normalize_needle_id() {
        assert_eq!(normalize_needle_id("→428"), "→428");
        assert_eq!(normalize_needle_id("bd-1"), "→001");
        assert_eq!(normalize_needle_id("nd-42"), "→042");
        assert_eq!(normalize_needle_id("bd-428"), "→428");
    }

    #[test]
    fn test_event_matches_needle_by_id() {
        let event = serde_json::json!({
            "event": "task.added",
            "id": "→428",
            "title": "fix cat shim",
            "timestamp": "2026-03-08T18:38:54Z"
        });
        assert!(event_matches_needle(&event, "→428"));
        assert!(!event_matches_needle(&event, "→999"));
    }

    #[test]
    fn test_event_matches_needle_in_text() {
        let event = serde_json::json!({
            "event": "hay.filed",
            "straw": "related to →428 shim work",
            "timestamp": "2026-03-08T19:00:00Z"
        });
        assert!(event_matches_needle(&event, "→428"));
    }

    #[test]
    fn test_event_matches_needle_in_thread() {
        let event = serde_json::json!({
            "event": "thread.created",
            "name": "test-thread",
            "needles": ["→428", "→429"],
            "timestamp": "2026-03-08T19:00:00Z"
        });
        assert!(event_matches_needle(&event, "→428"));
        assert!(!event_matches_needle(&event, "→999"));
    }

    #[test]
    fn test_event_matches_text_case_insensitive() {
        let event = serde_json::json!({
            "event": "task.added",
            "id": "→433",
            "title": "milestone: Tack — the intent language",
            "timestamp": "2026-03-08T19:09:37Z"
        });
        assert!(event_matches_text(&event, "tack"));
        assert!(event_matches_text(&event, "Tack"));
        assert!(event_matches_text(&event, "TACK"));
        assert!(!event_matches_text(&event, "foobar"));
    }

    #[test]
    fn test_truncate() {
        assert_eq!(truncate("short", 10), "short");
        assert_eq!(truncate("this is a long string", 10), "this is a ...");
    }

    #[test]
    fn test_format_event_task_added() {
        let event = serde_json::json!({
            "event": "task.added",
            "id": "→428",
            "title": "fix cat/sed/head shim arg passthrough",
            "timestamp": "2026-03-08T18:38:54Z"
        });
        let line = format_event(&event);
        assert!(line.contains("[2026-03-08T18:38:54Z]"));
        assert!(line.contains("task.added"));
        assert!(line.contains("→428"));
        assert!(line.contains("fix cat/sed/head shim arg passthrough"));
    }

    #[test]
    fn test_format_event_hay_filed() {
        let event = serde_json::json!({
            "event": "hay.filed",
            "source": "scottmeyer",
            "straw": "test straw content",
            "timestamp": "2026-03-08T19:00:00Z"
        });
        let line = format_event(&event);
        assert!(line.contains("hay.filed"));
        assert!(line.contains("(scottmeyer)"));
        assert!(line.contains("~ test straw content"));
    }

    #[test]
    fn test_dedup_removes_synthesized() {
        let mut events = vec![
            serde_json::json!({
                "event": "task.added",
                "id": "→428",
                "title": "real event",
                "timestamp": "2026-03-08T18:38:54Z"
            }),
            serde_json::json!({
                "event": "task.added",
                "id": "→428",
                "title": "synthesized",
                "timestamp": "2026-03-08T18:38:54Z",
                "_synthesized": true
            }),
        ];
        dedup_events(&mut events);
        assert_eq!(events.len(), 1);
        assert!(events[0].get("_synthesized").is_none());
    }

    #[test]
    fn test_dedup_keeps_unique_synthesized() {
        let mut events = vec![
            serde_json::json!({
                "event": "task.added",
                "id": "→428",
                "title": "real event",
                "timestamp": "2026-03-08T18:38:54Z"
            }),
            serde_json::json!({
                "event": "task.closed",
                "id": "→428",
                "reason": "fixed",
                "timestamp": "2026-03-08T19:00:00Z",
                "_synthesized": true
            }),
        ];
        dedup_events(&mut events);
        // task.closed at 19:00 has no matching non-synthesized event, so it stays
        assert_eq!(events.len(), 2);
    }

    #[test]
    fn test_event_matches_needle_bd_form() {
        // Audit has bd-011, user searches →011
        let event = serde_json::json!({
            "event": "bead.committed",
            "bead": "bd-011",
            "timestamp": "2026-03-08T00:52:06Z"
        });
        assert!(event_matches_needle(&event, "→011"));
    }
}
