//! Model switch handoff — build a lookup table for the new model (→1157 Phase 6).
//!
//! When `:model` crosses a provider boundary (e.g., opus → haiku, claude → gemini),
//! the kernel builds a thin handoff table (~200 tokens) from ISA-neutral state:
//!
//! - Hot files (gen_table: recently modified by this agent)
//! - Recent decisions (last N from decisions.jsonl)
//! - Intent thread (last user messages from the session)
//! - Demand-page pointers (where to find more context)
//!
//! The new model boots with this INDEX, not the data. Tools (Read, pitchfork,
//! context_search) are the page fault handler — the model pages in what it needs.
//!
//! This is novel: no existing system builds a page table for model switching.
//! It works because ostk has ISA-neutral state (.ostk/) + demand-paging tools.

use std::fmt::Write;
use std::path::Path;

use crate::cpu::anthropic::Message;

/// Build the handoff lookup table for a model switch.
///
/// Returns a markdown string (~200 tokens) that serves as the new model's
/// boot orientation. The new model reads this and pages in data via tools.
pub fn build_handoff_table(
    root: &Path,
    old_model: &str,
    new_model: &str,
    messages: &[Message],
) -> String {
    let ostk_dir = crate::state_dir(root);
    let mut out = String::new();

    // Header
    writeln!(out, "# Handoff — {old_model} → {new_model}").unwrap();
    writeln!(out).unwrap();

    // Focus: current work from recent decisions
    write_focus(&mut out, &ostk_dir);

    // Hot files: recently modified, worth re-reading
    write_hot_files(&mut out, &ostk_dir);

    // Recent decisions: what was decided this session
    write_recent_decisions(&mut out, &ostk_dir);

    // Intent thread: last user messages
    write_intent_thread(&mut out, messages);

    // Demand-page pointers
    write_page_pointers(&mut out, &ostk_dir);

    out
}

/// Extract focus from the most recent decision key.
fn write_focus(out: &mut String, ostk_dir: &Path) {
    let decisions_path = ostk_dir.join("decisions.jsonl");
    let content = std::fs::read_to_string(&decisions_path).unwrap_or_default();

    // Last decision's key is usually the current focus
    if let Some(last_line) = content.lines().rev().find(|l| !l.trim().is_empty()) {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(last_line) {
            if let Some(key) = v["key"].as_str() {
                let value = v["value"].as_str().unwrap_or("");
                // Truncate value to first sentence for the summary
                let summary: String = value.chars().take(120).collect();
                writeln!(out, "## Focus").unwrap();
                writeln!(out, "{key}: {summary}...").unwrap();
                writeln!(out).unwrap();
            }
        }
    }
}

/// List hot files from gen_table (recently modified).
fn write_hot_files(out: &mut String, ostk_dir: &Path) {
    let gen_path = ostk_dir.join("gen_table.jsonl");
    let content = std::fs::read_to_string(&gen_path).unwrap_or_default();

    // Collect all entries, keep latest gen per path
    let mut latest: std::collections::HashMap<String, (u64, String)> = std::collections::HashMap::new();
    for line in content.lines() {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
            let path = v["path"].as_str().unwrap_or("").to_string();
            let generation = v["generation"].as_u64().unwrap_or(0);
            let ts = v["timestamp"].as_str().unwrap_or("").to_string();
            let entry = latest.entry(path).or_insert((0, String::new()));
            if generation > entry.0 {
                *entry = (generation, ts);
            }
        }
    }

    // Sort by generation descending, take top 10
    let mut files: Vec<(String, u64, String)> = latest
        .into_iter()
        .map(|(path, (generation, ts))| (path, generation, ts))
        .collect();
    files.sort_by(|a, b| b.1.cmp(&a.1));
    files.truncate(10);

    if !files.is_empty() {
        writeln!(out, "## Hot files (Read to page in)").unwrap();
        for (path, generation, _ts) in &files {
            writeln!(out, "- {path} (gen {generation})").unwrap();
        }
        writeln!(out).unwrap();
    }
}

/// Include last N decisions (condensed).
fn write_recent_decisions(out: &mut String, ostk_dir: &Path) {
    let decisions_path = ostk_dir.join("decisions.jsonl");
    let content = std::fs::read_to_string(&decisions_path).unwrap_or_default();

    let lines: Vec<&str> = content.lines().filter(|l| !l.trim().is_empty()).collect();
    let recent = &lines[lines.len().saturating_sub(5)..];

    if !recent.is_empty() {
        writeln!(out, "## Recent decisions").unwrap();
        for line in recent {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                let key = v["key"].as_str().unwrap_or("?");
                let value = v["value"].as_str().unwrap_or("");
                let summary: String = value.chars().take(80).collect();
                let marker = if v["reason"].as_str().unwrap_or("").contains("uman") {
                    "[H]"
                } else {
                    "[A]"
                };
                writeln!(out, "- {marker} {key}: {summary}...").unwrap();
            }
        }
        writeln!(out).unwrap();
    }
}

/// Extract last 3 user messages as the intent thread.
fn write_intent_thread(out: &mut String, messages: &[Message]) {
    let user_messages: Vec<&Message> = messages
        .iter()
        .filter(|m| m.role == "user")
        .collect();

    let recent = &user_messages[user_messages.len().saturating_sub(3)..];

    if !recent.is_empty() {
        writeln!(out, "## Intent thread (last user messages)").unwrap();
        for msg in recent {
            // Extract text content, truncate
            for block in &msg.content {
                if let crate::cpu::anthropic::ContentBlock::Text { text } = block {
                    let truncated: String = text.chars().take(100).collect();
                    let ellipsis = if text.len() > 100 { "..." } else { "" };
                    writeln!(out, "- \"{truncated}{ellipsis}\"").unwrap();
                }
            }
        }
        writeln!(out).unwrap();
    }
}

/// Demand-page pointers — where to find more context.
fn write_page_pointers(out: &mut String, ostk_dir: &Path) {
    writeln!(out, "## Demand page (use tools when needed)").unwrap();

    // Decisions count
    let decisions_count = std::fs::read_to_string(ostk_dir.join("decisions.jsonl"))
        .map(|s| s.lines().filter(|l| !l.trim().is_empty()).count())
        .unwrap_or(0);
    writeln!(out, "- decisions.jsonl: {decisions_count} entries (pitchfork to search)").unwrap();

    // Needles count
    let needles_count = std::fs::read_to_string(ostk_dir.join("needles/issues.jsonl"))
        .map(|s| s.lines().filter(|l| !l.trim().is_empty()).count())
        .unwrap_or(0);
    writeln!(out, "- needles: {needles_count} total (pitchfork to search)").unwrap();

    // Audit depth
    let audit_count = std::fs::read_to_string(ostk_dir.join("audit.jsonl"))
        .map(|s| s.lines().count())
        .unwrap_or(0);
    writeln!(out, "- audit.jsonl: {audit_count} events").unwrap();

    writeln!(out, "- Use Read for hot files, pitchfork for keyword search").unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn setup(name: &str) -> tempfile::TempDir {
        let tmp = tempfile::Builder::new()
            .prefix(&format!("ostk_handoff_{name}_"))
            .tempdir()
            .unwrap();
        let ostk = tmp.path().join(".ostk");
        fs::create_dir_all(ostk.join("needles")).unwrap();
        fs::write(ostk.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk.join("decisions.jsonl"), "").unwrap();
        fs::write(ostk.join("gen_table.jsonl"), "").unwrap();
        fs::write(ostk.join("audit.jsonl"), "").unwrap();
        tmp
    }

    #[test]
    fn test_handoff_table_empty_state() {
        let tmp = setup("empty");
        let table = build_handoff_table(tmp.path(), "opus", "haiku", &[]);
        assert!(table.contains("Handoff"));
        assert!(table.contains("opus → haiku"));
        assert!(table.contains("Demand page"));
    }

    #[test]
    fn test_handoff_table_with_decisions() {
        let tmp = setup("decisions");
        let ostk = tmp.path().join(".ostk");
        fs::write(
            ostk.join("decisions.jsonl"),
            "{\"key\":\"TEST_DECISION\",\"value\":\"we decided to do X\",\"reason\":\"because Y\",\"timestamp\":\"2026-04-04\"}\n",
        ).unwrap();

        let table = build_handoff_table(tmp.path(), "opus", "haiku", &[]);
        assert!(table.contains("TEST_DECISION"));
        assert!(table.contains("Recent decisions"));
    }

    #[test]
    fn test_handoff_table_with_hot_files() {
        let tmp = setup("hot_files");
        let ostk = tmp.path().join(".ostk");
        fs::write(
            ostk.join("gen_table.jsonl"),
            "{\"path\":\"src/main.rs\",\"generation\":5,\"writer\":\"agent-1\",\"timestamp\":\"2026-04-04\"}\n\
             {\"path\":\"src/lib.rs\",\"generation\":3,\"writer\":\"agent-1\",\"timestamp\":\"2026-04-04\"}\n",
        ).unwrap();

        let table = build_handoff_table(tmp.path(), "opus", "haiku", &[]);
        assert!(table.contains("src/main.rs"));
        assert!(table.contains("gen 5"));
        assert!(table.contains("Hot files"));
    }

    #[test]
    fn test_handoff_table_with_user_messages() {
        use crate::cpu::anthropic::{ContentBlock, Message};

        let messages = vec![
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "fix the bug in auth".into() }],
                model: None,
            },
            Message {
                role: "assistant".into(),
                content: vec![ContentBlock::Text { text: "I'll look at auth.rs".into() }],
                model: None,
            },
            Message {
                role: "user".into(),
                content: vec![ContentBlock::Text { text: "also check the session handling".into() }],
                model: None,
            },
        ];

        let tmp = setup("messages");
        let table = build_handoff_table(tmp.path(), "opus", "haiku", &messages);
        assert!(table.contains("fix the bug"));
        assert!(table.contains("session handling"));
        assert!(table.contains("Intent thread"));
        // Should NOT contain assistant messages
        assert!(!table.contains("I'll look at"));
    }

    #[test]
    fn test_handoff_table_is_compact() {
        let tmp = setup("compact");
        let table = build_handoff_table(tmp.path(), "opus", "haiku", &[]);
        // Should be under 1000 chars (~250 tokens) even with empty state
        assert!(table.len() < 1000, "handoff table too large: {} chars", table.len());
    }
}
