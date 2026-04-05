//! →957: Pre-dispatch intent resolution for the ostk coordination kernel.
//!
//! On every tool call, before the response is returned, the kernel examines
//! the user's most recent input and pre-resolves obvious queries by injecting
//! kernel state into the tool response. This is the INPUT-side counterpart
//! to digest (which enriches OUTPUT).
//!
//! Intent patterns (checked in priority order):
//! 1. **Orientation** — "what are we working on?", "where did we leave off?"
//! 2. **Decision reference** — "didn't we decide...", "what was the decision about..."
//! 3. **Concept reference** — →NNN needle IDs, needle title keywords

use std::path::Path;

/// Result of a pre-dispatch intent resolution.
pub struct PreDispatchResult {
    /// Text to inject into the tool response.
    pub injection: String,
    /// Tag identifying which pattern matched.
    pub source: &'static str,
}

/// Analyze user input and pre-resolve kernel state.
/// Returns `None` if no pattern matches or input is empty.
///
/// Designed to be fast — runs on every tool call. Each check does
/// lightweight keyword matching and bounded file reads.
pub fn pre_resolve(ostk_dir: &Path, user_input: &str) -> Option<PreDispatchResult> {
    let input = user_input.trim();
    if input.is_empty() {
        return None;
    }
    let lower = input.to_lowercase();

    // Try each pattern in priority order
    if let Some(r) = check_orientation(ostk_dir, &lower) {
        return Some(r);
    }
    if let Some(r) = check_decision_ref(ostk_dir, &lower) {
        return Some(r);
    }
    if let Some(r) = check_concept_ref(ostk_dir, input) {
        return Some(r);
    }
    None
}

// ── Pattern 1: Orientation queries ──

/// Keywords that signal "orient me" — the user wants to know what's happening.
const ORIENT_KEYWORDS: &[&str] = &[
    "working on",
    "left off",
    "where were we",
    "where did we",
    "what's the status",
    "what is the status",
    "what's open",
    "what is open",
    "orient",
    "catch me up",
    "bring me up to speed",
    "what are we doing",
    "what were we doing",
    "what should i",
    "what should we",
    "what's next",
    "what is next",
    "what needs",
];

fn check_orientation(ostk_dir: &Path, lower: &str) -> Option<PreDispatchResult> {
    if !ORIENT_KEYWORDS.iter().any(|kw| lower.contains(kw)) {
        return None;
    }

    let mut parts: Vec<String> = Vec::new();

    // 1. Open P0/P1 needles (capped at 10)
    let needles_path = ostk_dir.join("needles/issues.jsonl");
    if let Ok(content) = std::fs::read_to_string(&needles_path) {
        let mut open_needles: Vec<(String, String, String)> = Vec::new(); // (id, priority, title)
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) {
                let status = entry["status"].as_str().unwrap_or("open");
                if status == "closed" {
                    continue;
                }
                let priority = entry["priority"].as_str().unwrap_or("P2");
                if priority != "P0" && priority != "P1" {
                    continue;
                }
                let id = entry["id"].as_str().unwrap_or("?").to_string();
                let title = entry["title"].as_str().unwrap_or("").to_string();
                open_needles.push((id, priority.to_string(), title));
            }
        }
        if !open_needles.is_empty() {
            // Sort P0 first, then P1
            open_needles.sort_by(|a, b| a.1.cmp(&b.1));
            let cap = open_needles.len().min(10);
            let lines: Vec<String> = open_needles[..cap]
                .iter()
                .map(|(id, pri, title)| format!("  {id} [{pri}] {title}"))
                .collect();
            parts.push(format!("open needles ({}):\n{}", open_needles.len(), lines.join("\n")));
        }
    }

    // 2. Last 3 decisions
    let decisions_path = ostk_dir.join("decisions.jsonl");
    if let Ok(content) = std::fs::read_to_string(&decisions_path) {
        let decision_lines: Vec<&str> = content.lines().filter(|l| !l.trim().is_empty()).collect();
        let recent = decision_lines.len().saturating_sub(3);
        let mut recent_decisions: Vec<String> = Vec::new();
        for line in &decision_lines[recent..] {
            if let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) {
                let key = entry["key"].as_str().unwrap_or("?");
                let value = entry["value"].as_str().unwrap_or("");
                let ts = entry["timestamp"].as_str().unwrap_or("");
                // Truncate value to keep injection compact
                let display_val = if value.len() > 80 {
                    format!("{}...", &value[..77])
                } else {
                    value.to_string()
                };
                recent_decisions.push(format!("  {key}: {display_val} ({ts})"));
            }
        }
        if !recent_decisions.is_empty() {
            parts.push(format!("recent decisions:\n{}", recent_decisions.join("\n")));
        }
    }

    if parts.is_empty() {
        return None;
    }

    // Cap total injection at ~800 chars (~200 tokens)
    let mut injection = parts.join("\n");
    if injection.len() > 800 {
        injection.truncate(800);
        injection.push_str("...");
    }

    Some(PreDispatchResult {
        injection,
        source: "orientation",
    })
}

// ── Pattern 2: Decision references ──

const DECISION_KEYWORDS: &[&str] = &[
    "decided",
    "decision",
    "we agreed",
    "we chose",
    "didn't we decide",
    "did we decide",
    "what was the decision",
    "the decision about",
    "the decision on",
    "agreed on",
    "agreed to",
];

fn check_decision_ref(ostk_dir: &Path, lower: &str) -> Option<PreDispatchResult> {
    if !DECISION_KEYWORDS.iter().any(|kw| lower.contains(kw)) {
        return None;
    }

    // Extract search terms: remove the decision keywords themselves and stop words,
    // then use remaining words as search terms.
    let search_terms = extract_search_terms(lower);
    if search_terms.is_empty() {
        return None;
    }

    let decisions_path = ostk_dir.join("decisions.jsonl");
    let content = std::fs::read_to_string(&decisions_path).ok()?;

    let mut matches: Vec<String> = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) {
            let key = entry["key"].as_str().unwrap_or("");
            let value = entry["value"].as_str().unwrap_or("");
            let reason = entry["reason"].as_str().unwrap_or("");
            let ts = entry["timestamp"].as_str().unwrap_or("");

            let searchable = format!("{} {} {}", key, value, reason).to_lowercase();
            // Require at least one search term to match
            let matched = search_terms.iter().any(|term| searchable.contains(term));
            if matched {
                let display_val = if value.len() > 100 {
                    format!("{}...", &value[..97])
                } else {
                    value.to_string()
                };
                matches.push(format!("  {key}: {display_val} ({ts})"));
            }
        }
    }

    if matches.is_empty() {
        return None;
    }

    // Cap at 5 matches
    let cap = matches.len().min(5);
    let mut injection = format!("matching decisions ({} total):\n{}", matches.len(), matches[..cap].join("\n"));
    if injection.len() > 800 {
        injection.truncate(800);
        injection.push_str("...");
    }

    Some(PreDispatchResult {
        injection,
        source: "decision_ref",
    })
}

/// Extract meaningful search terms from user input after removing
/// decision keywords and common stop words.
fn extract_search_terms(lower: &str) -> Vec<String> {
    // Remove decision trigger phrases
    let mut cleaned = lower.to_string();
    for kw in DECISION_KEYWORDS {
        cleaned = cleaned.replace(kw, " ");
    }

    let stop_words: &[&str] = &[
        "the", "a", "an", "is", "was", "were", "are", "been", "be",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "that", "this", "it", "its", "we", "our", "about", "what",
        "how", "when", "where", "which", "who", "why", "did", "do",
        "does", "don't", "didn't", "not", "no", "and", "or", "but",
        "if", "then", "than", "so", "just", "also", "like", "would",
        "could", "should", "will", "can", "may", "might", "has",
        "have", "had",
    ];

    cleaned
        .split_whitespace()
        .filter(|w| {
            let w = w.trim_matches(|c: char| !c.is_alphanumeric());
            w.len() >= 3 && !stop_words.contains(&w)
        })
        .map(|w| w.trim_matches(|c: char| !c.is_alphanumeric()).to_string())
        .filter(|w| !w.is_empty())
        .collect()
}

// ── Pattern 3: Concept references (needle IDs, needle title keywords) ──

fn check_concept_ref(ostk_dir: &Path, input: &str) -> Option<PreDispatchResult> {
    // Check for →NNN needle references
    let needle_ids = extract_needle_ids(input);
    if needle_ids.is_empty() {
        return None;
    }

    let needles_path = ostk_dir.join("needles/issues.jsonl");
    let content = std::fs::read_to_string(&needles_path).ok()?;

    let mut matches: Vec<String> = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) {
            let id = entry["id"].as_str().unwrap_or("");
            // Check if this needle's ID matches any referenced ID
            if needle_ids.iter().any(|ref_id| id.contains(ref_id)) {
                let title = entry["title"].as_str().unwrap_or("");
                let status = entry["status"].as_str().unwrap_or("open");
                let priority = entry["priority"].as_str().unwrap_or("?");
                matches.push(format!("  {id} [{status}/{priority}] {title}"));
            }
        }
    }

    if matches.is_empty() {
        return None;
    }

    let injection = format!("referenced needles:\n{}", matches.join("\n"));

    Some(PreDispatchResult {
        injection,
        source: "concept",
    })
}

/// Extract needle ID numbers from text. Matches →NNN patterns.
/// Returns the numeric portion (e.g., "957" from "→957").
fn extract_needle_ids(text: &str) -> Vec<String> {
    let mut ids = Vec::new();
    // Look for → followed by digits
    let arrow = '\u{2192}'; // →
    for (i, c) in text.char_indices() {
        if c == arrow {
            let after = &text[i + c.len_utf8()..];
            let digits: String = after.chars().take_while(|c| c.is_ascii_digit()).collect();
            if !digits.is_empty() && !ids.contains(&digits) {
                ids.push(digits);
            }
        }
    }
    // Also check for "nd-NNN" and "bd-NNN" patterns
    for prefix in &["nd-", "bd-"] {
        let mut remaining = text;
        while let Some(pos) = remaining.find(prefix) {
            let after = &remaining[pos + prefix.len()..];
            let digits: String = after.chars().take_while(|c| c.is_ascii_digit()).collect();
            if !digits.is_empty() && !ids.contains(&digits) {
                ids.push(digits);
            }
            remaining = &remaining[pos + prefix.len()..];
        }
    }
    ids
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn temp_ostk_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join("ostk_test_predispatch")
            .join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("needles")).unwrap();
        dir
    }

    fn write_needles(dir: &Path, needles: &[serde_json::Value]) {
        let path = dir.join("needles/issues.jsonl");
        let mut content = String::new();
        for n in needles {
            content.push_str(&serde_json::to_string(n).unwrap());
            content.push('\n');
        }
        fs::write(path, content).unwrap();
    }

    fn write_decisions(dir: &Path, decisions: &[serde_json::Value]) {
        let path = dir.join("decisions.jsonl");
        let mut content = String::new();
        for d in decisions {
            content.push_str(&serde_json::to_string(d).unwrap());
            content.push('\n');
        }
        fs::write(path, content).unwrap();
    }

    // ── extract_needle_ids ──

    #[test]
    fn extract_needle_ids_arrow() {
        let ids = extract_needle_ids("check \u{2192}957 and \u{2192}123");
        assert_eq!(ids, vec!["957", "123"]);
    }

    #[test]
    fn extract_needle_ids_nd_prefix() {
        let ids = extract_needle_ids("see nd-042 for details");
        assert_eq!(ids, vec!["042"]);
    }

    #[test]
    fn extract_needle_ids_empty() {
        let ids = extract_needle_ids("no references here");
        assert!(ids.is_empty());
    }

    // ── extract_search_terms ──

    #[test]
    fn extract_search_terms_filters_stop_words() {
        let terms = extract_search_terms("what was the decision about embeddings");
        assert!(terms.contains(&"embeddings".to_string()));
        assert!(!terms.contains(&"the".to_string()));
        assert!(!terms.contains(&"was".to_string()));
    }

    // ── Orientation ──

    #[test]
    fn orientation_returns_open_needles() {
        let dir = temp_ostk_dir("orient_needles");
        write_needles(
            &dir,
            &[
                serde_json::json!({
                    "id": "\u{2192}100", "title": "fix the TUI", "status": "open",
                    "priority": "P0", "issue_type": "task"
                }),
                serde_json::json!({
                    "id": "\u{2192}101", "title": "add embeddings", "status": "open",
                    "priority": "P1", "issue_type": "task"
                }),
                serde_json::json!({
                    "id": "\u{2192}102", "title": "docs cleanup", "status": "closed",
                    "priority": "P0", "issue_type": "task"
                }),
                serde_json::json!({
                    "id": "\u{2192}103", "title": "low prio thing", "status": "open",
                    "priority": "P2", "issue_type": "task"
                }),
            ],
        );

        let result = pre_resolve(&dir, "what are we working on?").unwrap();
        assert_eq!(result.source, "orientation");
        assert!(result.injection.contains("\u{2192}100"));
        assert!(result.injection.contains("fix the TUI"));
        assert!(result.injection.contains("\u{2192}101"));
        // Closed and P2 should NOT appear
        assert!(!result.injection.contains("\u{2192}102"));
        assert!(!result.injection.contains("\u{2192}103"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn orientation_includes_recent_decisions() {
        let dir = temp_ostk_dir("orient_decisions");
        // Create needles dir but leave it empty so we test decisions alone
        write_decisions(
            &dir,
            &[
                serde_json::json!({
                    "key": "old_decision", "value": "old value",
                    "reason": "old reason", "timestamp": "2026-03-01T00:00:00Z"
                }),
                serde_json::json!({
                    "key": "recent_one", "value": "chose option A",
                    "reason": "because fast", "timestamp": "2026-03-29T10:00:00Z"
                }),
                serde_json::json!({
                    "key": "latest_one", "value": "use Rust",
                    "reason": "performance", "timestamp": "2026-03-30T08:00:00Z"
                }),
            ],
        );

        let result = pre_resolve(&dir, "where did we leave off?").unwrap();
        assert_eq!(result.source, "orientation");
        assert!(result.injection.contains("recent_one"));
        assert!(result.injection.contains("latest_one"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn orientation_no_match_for_unrelated_input() {
        let dir = temp_ostk_dir("orient_nomatch");
        let result = pre_resolve(&dir, "please compile the project");
        assert!(result.is_none());

        let _ = fs::remove_dir_all(&dir);
    }

    // ── Decision references ──

    #[test]
    fn decision_ref_finds_matching_decisions() {
        let dir = temp_ostk_dir("decision_ref");
        write_decisions(
            &dir,
            &[
                serde_json::json!({
                    "key": "embeddings_model", "value": "use Model2Vec",
                    "reason": "fast and local", "timestamp": "2026-03-28T12:00:00Z"
                }),
                serde_json::json!({
                    "key": "deploy_strategy", "value": "blue-green",
                    "reason": "zero downtime", "timestamp": "2026-03-29T12:00:00Z"
                }),
            ],
        );

        let result = pre_resolve(&dir, "what was the decision about embeddings?").unwrap();
        assert_eq!(result.source, "decision_ref");
        assert!(result.injection.contains("embeddings_model"));
        assert!(!result.injection.contains("deploy_strategy"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn decision_ref_no_match_when_no_terms() {
        let dir = temp_ostk_dir("decision_ref_noterms");
        // "didn't we decide" alone with no meaningful terms after removing stop words
        let result = pre_resolve(&dir, "didn't we decide?");
        assert!(result.is_none());

        let _ = fs::remove_dir_all(&dir);
    }

    // ── Concept references ──

    #[test]
    fn concept_ref_resolves_needle_id() {
        let dir = temp_ostk_dir("concept_ref");
        write_needles(
            &dir,
            &[
                serde_json::json!({
                    "id": "\u{2192}957", "title": "kernel pre-dispatch intent resolution",
                    "status": "open", "priority": "P0", "issue_type": "task"
                }),
                serde_json::json!({
                    "id": "\u{2192}958", "title": "orient command",
                    "status": "open", "priority": "P1", "issue_type": "task"
                }),
            ],
        );

        let result = pre_resolve(&dir, "let me check \u{2192}957 status").unwrap();
        assert_eq!(result.source, "concept");
        assert!(result.injection.contains("\u{2192}957"));
        assert!(result.injection.contains("kernel pre-dispatch"));
        assert!(!result.injection.contains("\u{2192}958"));

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn concept_ref_no_match_for_unknown_id() {
        let dir = temp_ostk_dir("concept_ref_unknown");
        write_needles(
            &dir,
            &[serde_json::json!({
                "id": "\u{2192}100", "title": "known needle",
                "status": "open", "priority": "P0", "issue_type": "task"
            })],
        );

        // →999 doesn't exist
        let result = pre_resolve(&dir, "what about \u{2192}999?");
        assert!(result.is_none());

        let _ = fs::remove_dir_all(&dir);
    }

    // ── Empty/None cases ──

    #[test]
    fn pre_resolve_returns_none_for_empty_input() {
        let dir = temp_ostk_dir("empty_input");
        let result = pre_resolve(&dir, "");
        assert!(result.is_none());
        let result = pre_resolve(&dir, "   ");
        assert!(result.is_none());

        let _ = fs::remove_dir_all(&dir);
    }

    // ── Injection length cap ──

    #[test]
    fn orientation_caps_injection_length() {
        let dir = temp_ostk_dir("orient_cap");
        // Create many open P0 needles with long titles
        let mut needles = Vec::new();
        for i in 0..50 {
            needles.push(serde_json::json!({
                "id": format!("\u{2192}{:03}", i),
                "title": format!("This is a very long needle title number {} that should contribute to exceeding the cap", i),
                "status": "open",
                "priority": "P0",
                "issue_type": "task"
            }));
        }
        write_needles(&dir, &needles);

        let result = pre_resolve(&dir, "what's the status?").unwrap();
        // Should be capped at ~800 chars + "..."
        assert!(result.injection.len() <= 810, "injection too long: {} chars", result.injection.len());
        assert!(result.injection.ends_with("..."));

        let _ = fs::remove_dir_all(&dir);
    }
}
