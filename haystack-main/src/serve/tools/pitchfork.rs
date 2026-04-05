//! MCP tool handler for `ostk_pitchfork` — search across all kernel state by keyword.
//!
//! →963: The model's demand-paging tool. Searches decisions, needles, docs, and audit.
//!
//! The search logic is exposed via `pitchfork_search()` as a reusable function
//! so that both the MCP tool dispatch AND pre-dispatch intent resolution (→957)
//! can call it.

use serde_json::json;
use std::collections::HashMap;
use std::fs;
use std::path::Path;

use crate::serve::types::ToolError;

// ── Structured result types (reusable by →957 pre-dispatch) ──

/// A single decision match.
#[derive(Debug, Clone)]
pub struct DecisionMatch {
    pub key: String,
    pub value: String,
    pub reason: String,
    /// "H" if human-confirmed, "A" if agent-generated.
    pub marker: &'static str,
}

/// A single needle match.
#[derive(Debug, Clone)]
pub struct NeedleMatch {
    pub id: String,
    pub title: String,
    pub status: String,
}

/// A single doc match.
#[derive(Debug, Clone)]
pub struct DocMatch {
    /// Relative path from project root (e.g. "docs/draft/heartbeat-primitive.md").
    pub path: String,
}

/// Aggregated audit match — event type and count.
#[derive(Debug, Clone)]
pub struct AuditMatch {
    pub event_type: String,
    pub count: usize,
}

/// Complete pitchfork search results across all kernel state.
#[derive(Debug, Clone)]
pub struct PitchforkResults {
    pub query: String,
    pub decisions: Vec<DecisionMatch>,
    pub needles: Vec<NeedleMatch>,
    pub docs: Vec<DocMatch>,
    pub audit: Vec<AuditMatch>,
    /// Total audit match count (sum of individual event counts).
    pub audit_total: usize,
}

impl PitchforkResults {
    pub fn is_empty(&self) -> bool {
        self.decisions.is_empty()
            && self.needles.is_empty()
            && self.docs.is_empty()
            && self.audit.is_empty()
    }

    /// Format results for human/LLM display, capped at ~200 lines.
    pub fn format(&self, line_cap: usize) -> String {
        let mut sections: Vec<String> = Vec::new();
        let mut total_lines = 0;

        // 1. Decisions
        if !self.decisions.is_empty() {
            sections.push(format!("decisions ({} matches):", self.decisions.len()));
            for d in &self.decisions {
                if total_lines >= line_cap {
                    break;
                }
                let display_reason = if d.reason.len() > 80 {
                    format!("{}...", &d.reason[..77])
                } else {
                    d.reason.clone()
                };
                sections.push(format!(
                    "  - [{}] {}: {}\n    reason: \"{}\"",
                    d.marker, d.key, d.value, display_reason
                ));
                total_lines += 2;
            }
            total_lines += 1;
        }

        // 2. Needles
        if !self.needles.is_empty() && total_lines < line_cap {
            sections.push(format!("\nneedles ({} matches):", self.needles.len()));
            for n in &self.needles {
                if total_lines >= line_cap {
                    break;
                }
                sections.push(format!("  - {} [{}] {}", n.id, n.status, n.title));
                total_lines += 1;
            }
            total_lines += 2;
        }

        // 3. Docs
        if !self.docs.is_empty() && total_lines < line_cap {
            sections.push(format!("\ndocs ({} matches):", self.docs.len()));
            for d in &self.docs {
                if total_lines >= line_cap {
                    break;
                }
                sections.push(format!("  - {}", d.path));
                total_lines += 1;
            }
            total_lines += 2;
        }

        // 4. Audit
        if !self.audit.is_empty() && total_lines < line_cap {
            sections.push(format!(
                "\naudit ({} matches, last 1000 events):",
                self.audit_total
            ));
            for a in &self.audit {
                if total_lines >= line_cap {
                    break;
                }
                let label = if a.event_type.is_empty() {
                    "(unknown)"
                } else {
                    &a.event_type
                };
                sections.push(format!("  - {} (\u{00d7}{})", label, a.count));
                total_lines += 1;
            }
        }

        if sections.is_empty() {
            format!(":pitchfork \"{}\"\n\nno matches found", self.query)
        } else {
            format!(
                ":pitchfork \"{}\"\n\n{}",
                self.query,
                sections.join("\n")
            )
        }
    }
}

// ── Reusable search function ──

/// Search across all kernel state by keyword. Returns structured results.
///
/// Called by:
/// - MCP tool dispatch (`ostk_pitchfork`)
/// - Future: pre-dispatch intent resolution (→957)
///
/// `ostk_dir` is the `.ostk/` directory. `query` is case-insensitive.
pub fn pitchfork_search(ostk_dir: &Path, query: &str) -> PitchforkResults {
    let root = ostk_dir.parent().unwrap_or(ostk_dir);
    let query_lower = query.to_lowercase();

    let (audit, audit_total) = search_audit(ostk_dir, &query_lower);

    PitchforkResults {
        query: query.to_string(),
        decisions: search_decisions(ostk_dir, &query_lower),
        needles: search_needles(ostk_dir, &query_lower),
        docs: search_docs(root, &query_lower),
        audit,
        audit_total,
    }
}

// ── MCP tool handler (thin wrapper) ──

/// Handle a `pitchfork` MCP tool call.
pub fn handle(query: &str, ostk_dir: &Path) -> Result<serde_json::Value, ToolError> {
    if query.is_empty() {
        return Err(ToolError::invalid_params("query is required"));
    }

    let results = pitchfork_search(ostk_dir, query);
    let output = results.format(200);

    Ok(json!({ "text": output }))
}

// ── Search implementations ──

fn search_decisions(ostk_dir: &Path, query: &str) -> Vec<DecisionMatch> {
    let path = ostk_dir.join("decisions.jsonl");
    let mut matches = Vec::new();

    if let Ok(content) = fs::read_to_string(&path) {
        for line_str in content.lines() {
            let trimmed = line_str.trim();
            if trimmed.is_empty() {
                continue;
            }
            if let Ok(entry) = serde_json::from_str::<serde_json::Value>(trimmed) {
                let key = entry["key"].as_str().unwrap_or("");
                let value = entry["value"].as_str().unwrap_or("");
                let reason = entry["reason"].as_str().unwrap_or("");

                let searchable = format!("{} {} {}", key, value, reason).to_lowercase();
                if searchable.contains(query) {
                    let marker = if reason.to_lowercase().contains("human")
                        || reason.to_lowercase().contains("confirmed")
                        || reason.to_lowercase().contains("operator")
                    {
                        "H"
                    } else {
                        "A"
                    };
                    matches.push(DecisionMatch {
                        key: key.to_string(),
                        value: value.to_string(),
                        reason: reason.to_string(),
                        marker,
                    });
                }
            }
        }
    }

    matches
}

fn search_needles(ostk_dir: &Path, query: &str) -> Vec<NeedleMatch> {
    let path = ostk_dir.join("needles").join("issues.jsonl");
    let mut matches = Vec::new();

    if let Ok(content) = fs::read_to_string(&path) {
        for line_str in content.lines() {
            let trimmed = line_str.trim();
            if trimmed.is_empty() {
                continue;
            }
            if let Ok(entry) = serde_json::from_str::<serde_json::Value>(trimmed) {
                let title = entry["title"].as_str().unwrap_or("");
                let id = entry["id"].as_str().unwrap_or("");
                let status = entry["status"].as_str().unwrap_or("open");

                let searchable = format!("{} {}", title, id).to_lowercase();
                if searchable.contains(query) {
                    matches.push(NeedleMatch {
                        id: id.to_string(),
                        title: title.to_string(),
                        status: status.to_string(),
                    });
                }
            }
        }
    }

    matches
}

fn search_docs(root: &Path, query: &str) -> Vec<DocMatch> {
    let mut matches = Vec::new();

    let dirs = [root.join("docs/draft"), root.join("docs/spec")];

    for dir in &dirs {
        if !dir.is_dir() {
            continue;
        }
        if let Ok(entries) = fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) != Some("md") {
                    continue;
                }
                let filename = path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string();

                // Check filename match
                let mut matched = filename.to_lowercase().contains(query);

                // Check first 5 lines of content
                if !matched
                    && let Ok(content) = fs::read_to_string(&path) {
                        for line in content.lines().take(5) {
                            if line.to_lowercase().contains(query) {
                                matched = true;
                                break;
                            }
                        }
                    }

                if matched {
                    let rel = path
                        .strip_prefix(root)
                        .unwrap_or(&path)
                        .to_string_lossy()
                        .to_string();
                    matches.push(DocMatch { path: rel });
                }
            }
        }
    }

    matches
}

fn search_audit(ostk_dir: &Path, query: &str) -> (Vec<AuditMatch>, usize) {
    let path = ostk_dir.join("audit.jsonl");
    let mut total_count = 0;
    let mut event_counts: HashMap<String, usize> = HashMap::new();

    if let Ok(content) = fs::read_to_string(&path) {
        // Take last 1000 lines for performance
        let all_lines: Vec<&str> = content.lines().collect();
        let start = if all_lines.len() > 1000 {
            all_lines.len() - 1000
        } else {
            0
        };

        for line_str in &all_lines[start..] {
            let trimmed = line_str.trim();
            if trimmed.is_empty() {
                continue;
            }
            if let Ok(entry) = serde_json::from_str::<serde_json::Value>(trimmed) {
                let event_type = entry["event"].as_str().unwrap_or("");

                // Search event type and all string values
                let serialized = trimmed.to_lowercase();
                if serialized.contains(query) {
                    total_count += 1;
                    let key = event_type.to_string();
                    *event_counts.entry(key).or_insert(0) += 1;
                }
            }
        }
    }

    let mut sorted: Vec<_> = event_counts
        .into_iter()
        .map(|(event_type, count)| AuditMatch { event_type, count })
        .collect();
    sorted.sort_by(|a, b| b.count.cmp(&a.count));

    (sorted, total_count)
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
        std::env::temp_dir().join(format!("hs_pitchfork_{suffix}_{pid}_{n}"))
    }

    #[test]
    fn pitchfork_empty_query_returns_error() {
        let tmp = tmpdir("empty");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();
        let result = handle("", &ostk);
        assert!(result.is_err());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn pitchfork_no_state_returns_no_matches() {
        let tmp = tmpdir("nostate");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();
        let result = handle("temporal", &ostk).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("no matches found"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn pitchfork_searches_decisions() {
        let tmp = tmpdir("decisions");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();

        let decisions = r#"{"key":"input_protocol","value":"images-are-context","reason":"Screenshots arrive as separate turns"}
{"key":"kernel_channel","value":"pre-dispatch","reason":"Confirmed by human operator"}
"#;
        fs::write(ostk.join("decisions.jsonl"), decisions).unwrap();

        let result = handle("screenshot", &ostk).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("decisions (1 matches)"));
        assert!(text.contains("[A]"));
        assert!(text.contains("input_protocol"));

        // Test human marker
        let result2 = handle("dispatch", &ostk).unwrap();
        let text2 = result2["text"].as_str().unwrap();
        assert!(text2.contains("[H]"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn pitchfork_searches_needles() {
        let tmp = tmpdir("needles");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(ostk.join("needles")).unwrap();

        let needles = r#"{"id":"→958","title":":orient verb — single tool call","status":"open"}
{"id":"→961","title":"context_pct in heartbeat line","status":"closed"}
"#;
        fs::write(ostk.join("needles/issues.jsonl"), needles).unwrap();

        let result = handle("orient", &ostk).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("needles (1 matches)"));
        assert!(text.contains("\u{2192}958"));
        assert!(text.contains("[open]"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn pitchfork_searches_docs() {
        let tmp = tmpdir("docs");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();
        fs::create_dir_all(tmp.join("docs/draft")).unwrap();
        fs::write(
            tmp.join("docs/draft/heartbeat-primitive.md"),
            "# Heartbeat Primitive\nThis doc covers temporal signals.",
        )
        .unwrap();
        fs::write(
            tmp.join("docs/draft/other.md"),
            "# Other\nNothing relevant here.",
        )
        .unwrap();

        let result = handle("heartbeat", &ostk).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("docs (1 matches)"));
        assert!(text.contains("heartbeat-primitive.md"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn pitchfork_searches_audit() {
        let tmp = tmpdir("audit");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(&ostk).unwrap();

        let mut audit_lines = String::new();
        for _ in 0..5 {
            audit_lines.push_str(r#"{"event":"heartbeat_injected","agent":"worker-1"}"#);
            audit_lines.push('\n');
        }
        audit_lines.push_str(r#"{"event":"tack.resolved","input":":status"}"#);
        audit_lines.push('\n');
        fs::write(ostk.join("audit.jsonl"), &audit_lines).unwrap();

        let result = handle("heartbeat", &ostk).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("audit (5 matches"));
        assert!(text.contains("heartbeat_injected"));
        assert!(text.contains("\u{00d7}5"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn pitchfork_case_insensitive() {
        let tmp = tmpdir("case");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(ostk.join("needles")).unwrap();

        let needles = r#"{"id":"→100","title":"Context Manager","status":"open"}"#;
        fs::write(ostk.join("needles/issues.jsonl"), needles).unwrap();

        let result = handle("context", &ostk).unwrap();
        let text = result["text"].as_str().unwrap();
        assert!(text.contains("\u{2192}100"));
        let _ = fs::remove_dir_all(&tmp);
    }

    // ── Structured search tests ──

    #[test]
    fn pitchfork_search_returns_structured_results() {
        let tmp = tmpdir("structured");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(ostk.join("needles")).unwrap();

        let needles = r#"{"id":"→200","title":"build pipeline","status":"open"}"#;
        fs::write(ostk.join("needles/issues.jsonl"), needles).unwrap();

        let results = pitchfork_search(&ostk, "pipeline");
        assert_eq!(results.needles.len(), 1);
        assert_eq!(results.needles[0].id, "\u{2192}200");
        assert_eq!(results.needles[0].status, "open");
        assert!(results.decisions.is_empty());
        assert!(results.docs.is_empty());
        assert!(results.audit.is_empty());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn pitchfork_results_format_respects_line_cap() {
        let tmp = tmpdir("linecap");
        let ostk = tmp.join(".ostk");
        fs::create_dir_all(ostk.join("needles")).unwrap();

        let results = PitchforkResults {
            query: "test".to_string(),
            decisions: vec![],
            needles: vec![],
            docs: vec![],
            audit: vec![],
            audit_total: 0,
        };
        let formatted = results.format(200);
        assert!(formatted.contains("no matches found"));
        let _ = fs::remove_dir_all(&tmp);
    }
}
