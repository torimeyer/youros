//! MCP tool handler for `ostk_context_search` — search within the current agent's session transcript.
//!
//! →964: Searches across all string values in session JSONL entries.

use serde_json::json;
use std::fs;
use std::path::Path;

use crate::serve::types::ToolError;

/// Handle a `context_search` tool call.
pub fn handle(query: &str, ostk_dir: &Path, agent_alias: Option<&str>) -> Result<serde_json::Value, ToolError> {
    if query.is_empty() {
        return Err(ToolError::invalid_params("query is required"));
    }

    let alias = match agent_alias {
        Some(a) => a,
        None => {
            return Ok(json!({ "text": "no session transcript available — kernel is running in MCP mode without session recording" }));
        }
    };

    let session_path = ostk_dir.join("sessions").join(format!("{}.jsonl", alias));
    if !session_path.exists() {
        return Ok(json!({ "text": "no session transcript available — kernel is running in MCP mode without session recording" }));
    }

    let content = fs::read_to_string(&session_path)
        .map_err(|e| ToolError::internal(format!("failed to read session file: {e}")))?;

    let query_lower = query.to_lowercase();
    let all_lines: Vec<&str> = content.lines().collect();

    // Find matching turns
    let mut matches: Vec<MatchEntry> = Vec::new();
    let max_matches = 20;

    for (idx, line_str) in all_lines.iter().enumerate() {
        if matches.len() >= max_matches {
            break;
        }
        let trimmed = line_str.trim();
        if trimmed.is_empty() {
            continue;
        }

        // Search across the entire JSON line (all string values)
        if trimmed.to_lowercase().contains(&query_lower) {
            let role = extract_role(trimmed);
            let snippet = extract_snippet(trimmed, &query_lower);
            let turn = idx + 1; // 1-based turn numbers

            // Gather ±1 context
            let prev = if idx > 0 {
                let prev_str = all_lines[idx - 1].trim();
                if !prev_str.is_empty() {
                    Some(ContextLine {
                        turn: idx,
                        role: extract_role(prev_str),
                        snippet: extract_first_text(prev_str),
                    })
                } else {
                    None
                }
            } else {
                None
            };

            let next = if idx + 1 < all_lines.len() {
                let next_str = all_lines[idx + 1].trim();
                if !next_str.is_empty() {
                    Some(ContextLine {
                        turn: idx + 2,
                        role: extract_role(next_str),
                        snippet: extract_first_text(next_str),
                    })
                } else {
                    None
                }
            } else {
                None
            };

            matches.push(MatchEntry {
                turn,
                role,
                snippet,
                prev,
                next,
            });
        }
    }

    let total_turns = all_lines.iter().filter(|l| !l.trim().is_empty()).count();

    let output = if matches.is_empty() {
        format!("context_search \"{query}\"\n\nno matches in session ({total_turns} turns)")
    } else {
        let mut parts = vec![format!(
            "context_search \"{query}\"\n\nsession matches ({} of {total_turns} turns):",
            matches.len()
        )];
        for (i, m) in matches.iter().enumerate() {
            if let Some(ref prev) = m.prev {
                parts.push(format!(
                    "  turn {}: [{}] \"{}\"",
                    prev.turn, prev.role, prev.snippet
                ));
            }
            parts.push(format!(
                "  turn {}: [{}] \"{}\"",
                m.turn, m.role, m.snippet
            ));
            if let Some(ref next) = m.next {
                parts.push(format!(
                    "  turn {}: [{}] \"{}\"",
                    next.turn, next.role, next.snippet
                ));
            }
            if i < matches.len() - 1 {
                parts.push("  ---".to_string());
            }
        }
        parts.join("\n")
    };

    Ok(json!({ "text": output }))
}

struct MatchEntry {
    turn: usize,
    role: String,
    snippet: String,
    prev: Option<ContextLine>,
    next: Option<ContextLine>,
}

struct ContextLine {
    turn: usize,
    role: String,
    snippet: String,
}

/// Extract the role field from a JSON line.
fn extract_role(line: &str) -> String {
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
        v["role"]
            .as_str()
            .unwrap_or("?")
            .to_string()
    } else {
        "?".to_string()
    }
}

/// Extract a snippet around the query match from a JSON line.
fn extract_snippet(line: &str, query: &str) -> String {
    // Try to get a meaningful text snippet from the JSON
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
        // Check common text fields
        for field in &["content", "text", "input", "message", "cmd"] {
            if let Some(text) = v[field].as_str()
                && text.to_lowercase().contains(query) {
                    return truncate_around(text, query, 80);
                }
        }
    }
    // Fallback: search the raw JSON
    truncate_around(line, query, 80)
}

/// Extract first meaningful text from a JSON line for context display.
fn extract_first_text(line: &str) -> String {
    if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
        for field in &["content", "text", "input", "message", "cmd"] {
            if let Some(text) = v[field].as_str() {
                let first_line = text.lines().next().unwrap_or(text);
                if first_line.len() > 60 {
                    return format!("{}...", &first_line[..57]);
                }
                return first_line.to_string();
            }
        }
    }
    if line.len() > 60 {
        let end = line.floor_char_boundary(57);
        format!("{}...", &line[..end])
    } else {
        line.to_string()
    }
}

/// Truncate text around a query match, showing context.
fn truncate_around(text: &str, query: &str, max_len: usize) -> String {
    let lower = text.to_lowercase();
    if let Some(pos) = lower.find(query) {
        let start = pos.saturating_sub(max_len / 4);
        let end = (pos + query.len() + max_len * 3 / 4).min(text.len());
        // Ensure we don't split in the middle of a multi-byte char
        let start = text.floor_char_boundary(start);
        let end = text.ceil_char_boundary(end);
        let slice = &text[start..end];
        let mut result = String::new();
        if start > 0 {
            result.push_str("...");
        }
        result.push_str(slice.lines().next().unwrap_or(slice));
        if end < text.len() {
            result.push_str("...");
        }
        result
    } else {
        let first_line = text.lines().next().unwrap_or(text);
        if first_line.len() > max_len {
            format!("{}...", &first_line[..max_len - 3])
        } else {
            first_line.to_string()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn tmpdir(suffix: &str) -> std::path::PathBuf {
        use std::sync::atomic::{AtomicU32, Ordering};
        static CTR: AtomicU32 = AtomicU32::new(0);
        let n = CTR.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("hs_ctx_search_{suffix}_{pid}_{n}"))
    }

    #[test]
    fn context_search_empty_query_returns_error() {
        let tmp = tmpdir("empty");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();
        let result = handle("", &ostk, Some("worker-1"));
        assert!(result.is_err());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn context_search_no_alias_returns_message() {
        let tmp = tmpdir("noalias");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();
        let result = handle("test", &ostk, None).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no session transcript"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn context_search_no_session_file() {
        let tmp = tmpdir("nofile");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(ostk.join("sessions")).unwrap();
        let result = handle("test", &ostk, Some("worker-1")).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no session transcript"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn context_search_finds_matches() {
        let tmp = tmpdir("matches");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(ostk.join("sessions")).unwrap();

        let session = r#"{"role":"user","content":"hello world"}
{"role":"assistant","content":"I will help you with that"}
{"role":"user","content":"let's refactor the dispatch path"}
{"role":"assistant","content":"I'll start by reading dispatch.rs..."}
{"role":"user","content":"the refactor broke the tests"}
"#;
        fs::write(ostk.join("sessions/worker-1.jsonl"), session).unwrap();

        let result = handle("refactor", &ostk, Some("worker-1")).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("session matches (2 of 5 turns)"));
        assert!(text.contains("turn 3"));
        assert!(text.contains("[user]"));
        assert!(text.contains("refactor"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn context_search_no_matches() {
        let tmp = tmpdir("nomatch");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(ostk.join("sessions")).unwrap();

        let session = r#"{"role":"user","content":"hello"}
{"role":"assistant","content":"hi there"}
"#;
        fs::write(ostk.join("sessions/worker-1.jsonl"), session).unwrap();

        let result = handle("nonexistent", &ostk, Some("worker-1")).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no matches"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn context_search_caps_at_20() {
        let tmp = tmpdir("cap");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(ostk.join("sessions")).unwrap();

        let mut session = String::new();
        for i in 0..30 {
            session.push_str(&format!(
                "{{\"role\":\"user\",\"content\":\"test message {}\"}}\n",
                i
            ));
        }
        fs::write(ostk.join("sessions/worker-1.jsonl"), &session).unwrap();

        let result = handle("test", &ostk, Some("worker-1")).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("20 of 30 turns"));
        let _ = fs::remove_dir_all(&tmp);
    }
}
