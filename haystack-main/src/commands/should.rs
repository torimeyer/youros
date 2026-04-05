//! `ostk should` — intelligent issue filing with AI refinement.
//!
//! Flow: gather context -> refine via one-shot Haiku call -> present preview
//! -> prompt for confirmation -> submit via `gh issue create`.
//!
//! Falls back to dumb label-inference path when `--no-refine` is passed,
//! the API key is missing, or the refinement call fails.

use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::Command;

use serde::{Deserialize, Serialize};

const DEFAULT_REPO: &str = "os-tack/ostk.ai";

const BUG_VERBS: &[&str] = &["fix", "repair", "patch", "resolve", "handle"];
const ENHANCEMENT_VERBS: &[&str] = &["create", "add", "build", "implement", "support", "enable", "allow"];
const IMPROVEMENT_VERBS: &[&str] = &["improve", "update", "upgrade", "optimize", "refactor"];

// ─── Context gathering ──────────────────────────────────────────────

/// Contextual data gathered from the project before filing.
#[derive(Debug, Clone)]
pub struct IssueContext {
    pub version: String,
    pub recent_commits: Vec<String>,
    pub matching_needles: Vec<NeedleMatch>,
    pub boot_summary: String,
}

/// A needle that matched the description keywords.
#[derive(Debug, Clone)]
pub struct NeedleMatch {
    pub id: String,
    pub title: String,
    pub status: String,
}

/// Gather context from the project for issue refinement.
pub fn gather_context(ostk_dir: &Path, description: &str) -> IssueContext {
    let version = env!("CARGO_PKG_VERSION").to_string();

    // Recent git log (last 5 commits, oneline)
    let recent_commits = Command::new("git")
        .args(["log", "--oneline", "-5"])
        .output()
        .ok()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .map(|l| l.to_string())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    // Boot summary from live register dump (first line)
    let root = crate::find_project_root().unwrap_or_else(|_| ostk_dir.parent().unwrap_or(ostk_dir).to_path_buf());
    let boot_summary = crate::commands::boot::build_register_dump(&root, ostk_dir)
        .lines().next().unwrap_or("").to_string();

    // Search needles for keyword matches
    let matching_needles = search_needles(ostk_dir, description);

    IssueContext {
        version,
        recent_commits,
        matching_needles,
        boot_summary,
    }
}

/// Search issues.jsonl for needles whose title contains any word from the description.
fn search_needles(ostk_dir: &Path, description: &str) -> Vec<NeedleMatch> {
    let path = ostk_dir.join("needles/issues.jsonl");
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return vec![],
    };

    // Extract meaningful keywords (>3 chars, lowercased)
    let keywords: Vec<String> = description
        .split_whitespace()
        .map(|w| w.to_lowercase())
        .filter(|w| w.len() > 3)
        .collect();

    if keywords.is_empty() {
        return vec![];
    }

    let mut matches = Vec::new();
    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
            let title = v.get("title").and_then(|t| t.as_str()).unwrap_or("");
            let status = v.get("status").and_then(|s| s.as_str()).unwrap_or("");
            let id = v.get("id").and_then(|i| i.as_str()).unwrap_or("");

            // Only include open needles
            if status == "closed" {
                continue;
            }

            let title_lower = title.to_lowercase();
            if keywords.iter().any(|kw| title_lower.contains(kw.as_str())) {
                matches.push(NeedleMatch {
                    id: id.to_string(),
                    title: title.to_string(),
                    status: status.to_string(),
                });
            }
        }
    }
    // Cap at 5 matches
    matches.truncate(5);
    matches
}

// ─── AI refinement ──────────────────────────────────────────────────

/// The JSON structure returned by the refinement agent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RefinedIssue {
    pub title: String,
    pub description: String,
    #[serde(rename = "type")]
    pub issue_type: String,
    #[serde(default)]
    pub related: Vec<String>,
}

/// Build the refinement prompt with gathered context.
fn build_refine_prompt(raw_input: &str, ctx: &IssueContext) -> String {
    let commits = if ctx.recent_commits.is_empty() {
        "(none)".to_string()
    } else {
        ctx.recent_commits.join("\n")
    };

    let needles = if ctx.matching_needles.is_empty() {
        "(none)".to_string()
    } else {
        ctx.matching_needles
            .iter()
            .map(|n| format!("\u{2192}{} [{}]: {}", n.id, n.status, n.title))
            .collect::<Vec<_>>()
            .join("\n")
    };

    format!(
        r#"You are filing a GitHub issue for the ostk project. The user said:
"{raw_input}"

Based on the context below, produce:
1. A concise title (under 70 chars, starts with "fix:" for bugs or "feat:" for features)
2. A clear description (2-3 sentences)
3. Type: bug, enhancement, or improvement
4. Related needles (if any match)

Context:
- Version: {version}
- Boot state: {boot}
- Recent commits:
{commits}
- Related open needles:
{needles}

Output ONLY valid JSON, no markdown fences: {{"title": "...", "description": "...", "type": "...", "related": ["->NNN", ...]}}"#,
        version = ctx.version,
        boot = ctx.boot_summary,
        commits = commits,
        needles = needles,
    )
}

/// Call Haiku for fast one-shot refinement. Returns RefinedIssue or error.
fn refine_with_agent(raw_input: &str, ctx: &IssueContext) -> Result<RefinedIssue, String> {
    use crate::cpu::anthropic::{ContentBlock, InferenceRequest, Message, StreamEvent};
    use futures_util::StreamExt;

    let driver = crate::cpu::create_driver("claude-haiku-4-5")
        .map_err(|e| format!("no API key for refinement: {e}"))?;

    let prompt = build_refine_prompt(raw_input, ctx);

    let params = InferenceRequest {
        model: "claude-haiku-4-5".to_string(),
        max_tokens: 1024,
        system: Some(
            "You produce structured JSON for GitHub issue filing. Be concise and precise. \
             Output only the JSON object, no explanation."
                .to_string(),
        ),
        messages: vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: prompt }],
            model: None,
        }],
        tools: vec![],
        tool_choice: None,
        stream: true,
        claude: Default::default(),
    };

    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| format!("runtime: {e}"))?;

    let result = rt.block_on(async {
        let mut stream = driver.create_stream(params).await?;
        let mut text = String::new();
        while let Some(event) = stream.next().await {
            match event? {
                StreamEvent::TextDelta(delta) => text.push_str(&delta),
                StreamEvent::MessageStop { .. } => break,
                _ => {}
            }
        }
        Ok::<String, String>(text)
    })?;

    // Parse JSON from the response — handle possible markdown fences
    let json_str = extract_json(&result);
    serde_json::from_str::<RefinedIssue>(json_str)
        .map_err(|e| format!("failed to parse refined issue JSON: {e}\nRaw: {result}"))
}

/// Extract JSON from a response that might contain markdown fences.
fn extract_json(s: &str) -> &str {
    let trimmed = s.trim();
    // Strip ```json ... ``` fences if present
    if let Some(start) = trimmed.find('{')
        && let Some(end) = trimmed.rfind('}') {
            return &trimmed[start..=end];
        }
    trimmed
}

// ─── Preview and submission ─────────────────────────────────────────

// ANSI escape codes for CLI color output.
const BOLD: &str = "\x1b[1m";
const DIM: &str = "\x1b[2m";
const GREEN: &str = "\x1b[32m";
const RED: &str = "\x1b[31m";
const YELLOW: &str = "\x1b[33m";
const RESET: &str = "\x1b[0m";

/// Word-wrap text at `max_width` characters, breaking at word boundaries.
fn wrap_text(text: &str, max_width: usize) -> Vec<String> {
    if max_width == 0 {
        return vec![text.to_string()];
    }
    let mut lines = Vec::new();
    for input_line in text.lines() {
        let mut current = String::new();
        for word in input_line.split_whitespace() {
            if current.is_empty() {
                if word.len() > max_width {
                    let mut remaining = word;
                    while remaining.len() > max_width {
                        lines.push(remaining[..max_width].to_string());
                        remaining = &remaining[max_width..];
                    }
                    current = remaining.to_string();
                } else {
                    current = word.to_string();
                }
            } else if current.len() + 1 + word.len() > max_width {
                lines.push(current);
                if word.len() > max_width {
                    let mut remaining = word;
                    while remaining.len() > max_width {
                        lines.push(remaining[..max_width].to_string());
                        remaining = &remaining[max_width..];
                    }
                    current = remaining.to_string();
                } else {
                    current = word.to_string();
                }
            } else {
                current.push(' ');
                current.push_str(word);
            }
        }
        // Push current line (even if empty — preserves blank lines from input)
        lines.push(current);
    }
    if lines.is_empty() {
        lines.push(String::new());
    }
    lines
}

/// Color string for the issue type label.
fn type_color(issue_type: &str) -> &'static str {
    match issue_type {
        "bug" => RED,
        "enhancement" => GREEN,
        "improvement" => YELLOW,
        _ => RESET,
    }
}

/// Print the refined issue in a boxed preview with ANSI colors.
fn present_preview(refined: &RefinedIssue, repo: &str) {
    // Inner content width (excluding border + padding)
    let inner_w: usize = 50;
    // Total box width = "│  " (3) + inner_w + "  │" (3) = inner_w + 6
    // But we measure content area as inner_w, padded by 2 spaces on each side.

    let pad = |s: &str| -> String {
        // Visible length (strip ANSI escapes for padding calculation)
        let visible_len = strip_ansi_len(s);
        let trail = inner_w.saturating_sub(visible_len);
        format!(
            "{DIM}\u{2502}{RESET}  {s}{}{DIM}  \u{2502}{RESET}",
            " ".repeat(trail),
        )
    };

    let blank = pad("");

    // Top border
    let top = format!(
        "{DIM}\u{250c}\u{2500} {RESET}{BOLD}Issue Preview{RESET} {DIM}{}\u{2510}{RESET}",
        "\u{2500}".repeat(inner_w + 4 - " Issue Preview ".len()),
    );
    // Bottom border
    let bot = format!(
        "{DIM}\u{2514}{}\u{2518}{RESET}",
        "\u{2500}".repeat(inner_w + 4),
    );

    eprintln!();
    eprintln!("{top}");
    eprintln!("{blank}");

    // Title (bold)
    eprintln!("{}", pad(&format!("{BOLD}Title:{RESET} {}", refined.title)));

    // Type (colored)
    let tc = type_color(&refined.issue_type);
    eprintln!(
        "{}",
        pad(&format!("{BOLD}Type:{RESET}  {tc}{}{RESET}", refined.issue_type))
    );

    // Repo
    eprintln!("{}", pad(&format!("{BOLD}Repo:{RESET}  {repo}")));

    eprintln!("{blank}");

    // Description (word-wrapped)
    eprintln!("{}", pad(&format!("{BOLD}Description:{RESET}")));
    let desc_width = inner_w - 2; // extra 2-char indent inside box
    let wrapped = wrap_text(&refined.description, desc_width);
    for line in &wrapped {
        eprintln!("{}", pad(&format!("  {line}")));
    }

    // Related needles
    if !refined.related.is_empty() {
        eprintln!("{blank}");
        let related_str = refined.related.join(", ");
        eprintln!(
            "{}",
            pad(&format!("{DIM}Related: {related_str}{RESET}"))
        );
    }

    eprintln!("{blank}");
    eprintln!("{bot}");
}

/// Count visible characters in a string, ignoring ANSI escape sequences.
fn strip_ansi_len(s: &str) -> usize {
    let mut len = 0;
    let mut in_esc = false;
    for ch in s.chars() {
        if in_esc {
            if ch == 'm' {
                in_esc = false;
            }
        } else if ch == '\x1b' {
            in_esc = true;
        } else {
            len += 1;
        }
    }
    len
}

/// Prompt the user for confirmation: y/e/n.
fn prompt_confirm() -> char {
    eprint!(
        "\n  {GREEN}[y]{RESET} submit  {YELLOW}[e]{RESET} edit  {RED}[n]{RESET} cancel  ",
    );
    let _ = io::stderr().flush();

    let stdin = io::stdin();
    let mut line = String::new();
    if stdin.lock().read_line(&mut line).is_err() {
        return 'n';
    }
    line.trim()
        .chars()
        .next()
        .unwrap_or('n')
        .to_ascii_lowercase()
}

/// Open $EDITOR on the description, returning the edited text.
fn edit_description(description: &str) -> Result<String, String> {
    let editor = std::env::var("EDITOR").unwrap_or_else(|_| "vi".to_string());
    let tmp = std::env::temp_dir().join("ostk-should-edit.md");
    std::fs::write(&tmp, description).map_err(|e| format!("write tmp: {e}"))?;

    let status = Command::new(&editor)
        .arg(&tmp)
        .status()
        .map_err(|e| format!("failed to launch {editor}: {e}"))?;

    if !status.success() {
        return Err(format!("{editor} exited with non-zero status"));
    }

    std::fs::read_to_string(&tmp).map_err(|e| format!("read tmp: {e}"))
}

/// Submit the issue via `gh issue create`.
fn submit_issue(title: &str, body: &str, label: &str, repo: &str) -> Result<(), String> {
    let output = Command::new("gh")
        .args([
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            title,
            "--body",
            body,
            "--label",
            label,
        ])
        .output()
        .map_err(|e| format!("failed to run gh: {}", e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("gh issue create failed: {}", stderr.trim()));
    }

    let url = String::from_utf8_lossy(&output.stdout);
    println!("{}", url.trim());
    Ok(())
}

// ─── Label inference (fallback) ─────────────────────────────────────

fn infer_label(description: &str) -> &'static str {
    let first_word = description
        .split_whitespace()
        .next()
        .unwrap_or("")
        .to_lowercase();

    if BUG_VERBS.contains(&first_word.as_str()) {
        "bug"
    } else if ENHANCEMENT_VERBS.contains(&first_word.as_str()) {
        "enhancement"
    } else if IMPROVEMENT_VERBS.contains(&first_word.as_str()) {
        "improvement"
    } else {
        "enhancement"
    }
}

/// Resolve the target repo from ostk.toml, falling back to the default.
fn resolve_repo() -> String {
    let root = std::env::current_dir().unwrap_or_default();
    let cfg = crate::config::load_config(&root);
    cfg.should
        .and_then(|s| s.repo)
        .unwrap_or_else(|| DEFAULT_REPO.to_string())
}

// ─── Dumb path (original behavior) ─────────────────────────────────

fn run_dumb(description: &str) -> Result<(), String> {
    let label = infer_label(description);
    let version = env!("CARGO_PKG_VERSION");
    let repo = resolve_repo();

    let title = description.to_string();
    let body = format!(
        "## Description\n{}\n\n## Type\n{}\n\n## Filed via\n`ostk should` v{}",
        description, label, version
    );

    submit_issue(&title, &body, label, &repo)
}

// ─── Main entry point ───────────────────────────────────────────────

pub fn run(description: &str, no_refine: bool) -> Result<(), String> {
    if description.trim().is_empty() {
        return Err("usage: ostk should <description>".to_string());
    }

    // Fast path: skip refinement if requested
    if no_refine {
        return run_dumb(description);
    }

    let repo = resolve_repo();

    // Step 1: Gather context
    let ostk_dir = find_ostk_dir();
    let ctx = match ostk_dir {
        Some(ref dir) => gather_context(dir, description),
        None => IssueContext {
            version: env!("CARGO_PKG_VERSION").to_string(),
            recent_commits: vec![],
            matching_needles: vec![],
            boot_summary: String::new(),
        },
    };

    // Step 2: Refine via one-shot agent
    let refined = match refine_with_agent(description, &ctx) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("[should] refinement unavailable ({e}), using dumb path");
            return run_dumb(description);
        }
    };

    // Step 3: Present preview
    present_preview(&refined, &repo);

    // Step 4: Prompt and submit
    loop {
        match prompt_confirm() {
            'y' => {
                let version = env!("CARGO_PKG_VERSION");
                let related_str = if refined.related.is_empty() {
                    String::new()
                } else {
                    format!("\n\n## Related\n{}", refined.related.join(", "))
                };
                let body = format!(
                    "## Description\n{}\n\n## Type\n{}{}\n\n## Filed via\n`ostk should` v{} (AI-refined)",
                    refined.description, refined.issue_type, related_str, version
                );
                return submit_issue(&refined.title, &body, &refined.issue_type, &repo);
            }
            'e' => {
                match edit_description(&refined.description) {
                    Ok(edited) => {
                        let version = env!("CARGO_PKG_VERSION");
                        let related_str = if refined.related.is_empty() {
                            String::new()
                        } else {
                            format!("\n\n## Related\n{}", refined.related.join(", "))
                        };
                        let body = format!(
                            "## Description\n{}\n\n## Type\n{}{}\n\n## Filed via\n`ostk should` v{} (AI-refined, edited)",
                            edited.trim(), refined.issue_type, related_str, version
                        );
                        return submit_issue(&refined.title, &body, &refined.issue_type, &repo);
                    }
                    Err(e) => {
                        eprintln!("[should] edit failed: {e}");
                        continue;
                    }
                }
            }
            'n' => {
                eprintln!("Aborted.");
                return Ok(());
            }
            _ => {
                eprintln!("Please enter y, e, or n.");
                continue;
            }
        }
    }
}

/// Find the state directory by walking up from cwd.
fn find_ostk_dir() -> Option<PathBuf> {
    crate::find_project_root().ok().map(|r| crate::state_dir(&r))
}

// ─── Tests ──────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_infer_label_bug() {
        assert_eq!(infer_label("fix colors in TUI"), "bug");
        assert_eq!(infer_label("repair broken pipeline"), "bug");
    }

    #[test]
    fn test_infer_label_enhancement() {
        assert_eq!(infer_label("add dark mode support"), "enhancement");
        assert_eq!(infer_label("implement caching layer"), "enhancement");
    }

    #[test]
    fn test_infer_label_improvement() {
        assert_eq!(infer_label("improve error messages"), "improvement");
        assert_eq!(infer_label("optimize query performance"), "improvement");
    }

    #[test]
    fn test_infer_label_default() {
        assert_eq!(infer_label("something vague"), "enhancement");
        assert_eq!(infer_label(""), "enhancement");
    }

    #[test]
    fn test_gather_context_empty_ostk() {
        let tmp = tempfile::tempdir().unwrap();
        let ostk = tmp.path().join(".ostk");
        fs::create_dir_all(&ostk).unwrap();

        let ctx = gather_context(&ostk, "fix colors");
        assert!(!ctx.version.is_empty());
        assert!(ctx.matching_needles.is_empty());
    }

    #[test]
    fn test_gather_context_with_needles() {
        let tmp = tempfile::tempdir().unwrap();
        let ostk = tmp.path().join(".ostk");
        let needles = ostk.join("needles");
        fs::create_dir_all(&needles).unwrap();

        // Write some test needles
        let issues = r#"{"id":"bd-100","title":"fix colors in fcp_screen","status":"open","priority":"P1","issue_type":"bug"}
{"id":"bd-101","title":"add dark mode theme","status":"open","priority":"P2","issue_type":"enhancement"}
{"id":"bd-102","title":"optimize token counting","status":"closed","priority":"P1","issue_type":"improvement"}"#;
        fs::write(needles.join("issues.jsonl"), issues).unwrap();

        let ctx = gather_context(&ostk, "fix colors");
        assert_eq!(ctx.matching_needles.len(), 1);
        assert_eq!(ctx.matching_needles[0].id, "bd-100");
        assert_eq!(ctx.matching_needles[0].status, "open");
    }

    #[test]
    fn test_gather_context_needles_skip_closed() {
        let tmp = tempfile::tempdir().unwrap();
        let ostk = tmp.path().join(".ostk");
        let needles = ostk.join("needles");
        fs::create_dir_all(&needles).unwrap();

        let issues = r#"{"id":"bd-200","title":"optimize token counting","status":"closed","priority":"P1","issue_type":"improvement"}"#;
        fs::write(needles.join("issues.jsonl"), issues).unwrap();

        let ctx = gather_context(&ostk, "optimize token counting");
        assert!(ctx.matching_needles.is_empty(), "closed needles should be skipped");
    }

    #[test]
    fn test_search_needles_short_keywords_ignored() {
        let tmp = tempfile::tempdir().unwrap();
        let ostk = tmp.path().join(".ostk");
        let needles = ostk.join("needles");
        fs::create_dir_all(&needles).unwrap();

        let issues = r#"{"id":"bd-300","title":"fix the bug","status":"open","priority":"P1","issue_type":"bug"}"#;
        fs::write(needles.join("issues.jsonl"), issues).unwrap();

        // "fix" and "the" and "bug" are all <= 3 chars, so no matches
        let matches = search_needles(&ostk, "fix the bug");
        assert!(matches.is_empty(), "short keywords should not match");
    }

    #[test]
    fn test_extract_json_plain() {
        let input = r#"{"title": "test", "description": "desc", "type": "bug", "related": []}"#;
        assert_eq!(extract_json(input), input);
    }

    #[test]
    fn test_extract_json_with_fences() {
        let input = "```json\n{\"title\": \"test\"}\n```";
        assert_eq!(extract_json(input), "{\"title\": \"test\"}");
    }

    #[test]
    fn test_extract_json_with_leading_text() {
        let input = "Here is the JSON:\n{\"title\": \"test\"}";
        assert_eq!(extract_json(input), "{\"title\": \"test\"}");
    }

    #[test]
    fn test_refined_issue_parse() {
        let json = r#"{"title": "fix: TUI colors apply to full line", "description": "Tool result lines use dim_colored on entire prefix.", "type": "bug", "related": ["\u2192755"]}"#;
        let refined: RefinedIssue = serde_json::from_str(json).unwrap();
        assert_eq!(refined.title, "fix: TUI colors apply to full line");
        assert_eq!(refined.issue_type, "bug");
        assert_eq!(refined.related.len(), 1);
    }

    #[test]
    fn test_refined_issue_parse_no_related() {
        let json = r#"{"title": "feat: add dark mode", "description": "Support dark mode theme.", "type": "enhancement"}"#;
        let refined: RefinedIssue = serde_json::from_str(json).unwrap();
        assert!(refined.related.is_empty());
    }

    #[test]
    fn test_build_refine_prompt_contains_input() {
        let ctx = IssueContext {
            version: "1.7.0".to_string(),
            recent_commits: vec!["abc123 fix: something".to_string()],
            matching_needles: vec![],
            boot_summary: "ostk v1.7".to_string(),
        };
        let prompt = build_refine_prompt("fix colors", &ctx);
        assert!(prompt.contains("fix colors"));
        assert!(prompt.contains("1.7.0"));
        assert!(prompt.contains("abc123"));
        assert!(prompt.contains("ostk v1.7"));
    }

    #[test]
    fn test_build_refine_prompt_with_needles() {
        let ctx = IssueContext {
            version: "1.7.0".to_string(),
            recent_commits: vec![],
            matching_needles: vec![NeedleMatch {
                id: "bd-100".to_string(),
                title: "fix colors in screen".to_string(),
                status: "open".to_string(),
            }],
            boot_summary: String::new(),
        };
        let prompt = build_refine_prompt("fix colors", &ctx);
        assert!(prompt.contains("bd-100"));
        assert!(prompt.contains("fix colors in screen"));
    }

    #[test]
    fn test_run_empty_description() {
        let result = run("", false);
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("usage:"));
    }

    #[test]
    fn test_run_whitespace_description() {
        let result = run("   ", false);
        assert!(result.is_err());
    }

    // ─── Preview formatting tests ───────────────────────────────────

    #[test]
    fn test_wrap_text_short_fits_one_line() {
        let lines = wrap_text("hello world", 40);
        assert_eq!(lines, vec!["hello world"]);
    }

    #[test]
    fn test_wrap_text_breaks_at_word_boundary() {
        let lines = wrap_text("one two three four five", 10);
        assert_eq!(lines, vec!["one two", "three four", "five"]);
    }

    #[test]
    fn test_wrap_text_long_word_hard_breaks() {
        let lines = wrap_text("abcdefghijklmnop", 5);
        assert_eq!(lines, vec!["abcde", "fghij", "klmno", "p"]);
    }

    #[test]
    fn test_wrap_text_empty_string() {
        let lines = wrap_text("", 40);
        assert_eq!(lines, vec![""]);
    }

    #[test]
    fn test_wrap_text_preserves_input_newlines() {
        let lines = wrap_text("line one\nline two", 40);
        assert_eq!(lines, vec!["line one", "line two"]);
    }

    #[test]
    fn test_wrap_text_zero_width() {
        let lines = wrap_text("hello world", 0);
        assert_eq!(lines, vec!["hello world"]);
    }

    #[test]
    fn test_strip_ansi_len_plain() {
        assert_eq!(strip_ansi_len("hello"), 5);
    }

    #[test]
    fn test_strip_ansi_len_with_escapes() {
        assert_eq!(strip_ansi_len("\x1b[1mhello\x1b[0m"), 5);
        assert_eq!(strip_ansi_len("\x1b[32mgreen\x1b[0m"), 5);
    }

    #[test]
    fn test_strip_ansi_len_empty() {
        assert_eq!(strip_ansi_len(""), 0);
        assert_eq!(strip_ansi_len("\x1b[0m"), 0);
    }

    #[test]
    fn test_type_color_bug_is_red() {
        assert_eq!(type_color("bug"), RED);
    }

    #[test]
    fn test_type_color_enhancement_is_green() {
        assert_eq!(type_color("enhancement"), GREEN);
    }

    #[test]
    fn test_type_color_improvement_is_yellow() {
        assert_eq!(type_color("improvement"), YELLOW);
    }

    #[test]
    fn test_type_color_unknown_is_reset() {
        assert_eq!(type_color("unknown"), RESET);
    }

    #[test]
    fn test_present_preview_does_not_panic() {
        // Smoke test: just make sure it doesn't panic with various inputs
        let refined = RefinedIssue {
            title: "feat: add diagram support".to_string(),
            description: "Implement diagram rendering and visualization capabilities in ostk. This feature should enable users to generate and display diagrams for documentation, architecture visualization, and issue context.".to_string(),
            issue_type: "enhancement".to_string(),
            related: vec!["\u{2192}753".to_string()],
        };
        present_preview(&refined, "os-tack/ostk.ai");
    }

    #[test]
    fn test_present_preview_no_related() {
        let refined = RefinedIssue {
            title: "fix: crash on empty input".to_string(),
            description: "Short desc.".to_string(),
            issue_type: "bug".to_string(),
            related: vec![],
        };
        present_preview(&refined, "os-tack/ostk.ai");
    }
}
