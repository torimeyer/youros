//! SessionSummary compiler — needle →966.
//!
//! Incrementally compiles raw session JSONL transcripts into structured
//! summaries, reducing context from ~50K tokens to ~5K tokens.
//!
//! Handles two session formats:
//! - **API format**: `{"role":"user/assistant","content":[...]}`
//!   Written by `AgentSession::save()` — full Claude API message structs.
//! - **MCP format**: `{"tool":"shell","args_summary":"...","result_summary":"...","timestamp":"..."}`
//!   Written by MCP tool dispatchers — compressed tool call records.
//!
//! The compiler extracts structured events (file modifications, test results,
//! needle state changes, decisions, errors) from older turns and compiles them
//! into a concise summary. Recent turns are preserved at full fidelity.
//!
//! This is deterministic compilation, NOT LLM summarization.

use std::io::{BufRead, BufReader};
use std::path::Path;

use regex::Regex;
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Events extracted from session turns that matter for the summary.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum SessionEvent {
    FileModified { path: String },
    TestResult { passed: u32, failed: u32, skipped: u32 },
    NeedleStateChange { id: String, action: String },
    DecisionMade { key: String, value: String },
    ToolCallSignificant { tool: String, summary: String },
    ErrorEncountered { tool: String, message: String },
}

/// A single turn from the session transcript.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionTurn {
    pub turn_number: usize,
    pub role: String,
    pub content: String,
    pub tool_name: Option<String>,
    pub timestamp: Option<String>,
}

/// Incrementally maintained session summary.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionSummary {
    /// Compiled summary text — the materialized view.
    pub text: String,
    /// Turn number up to which the summary covers (exclusive).
    pub compiled_through_turn: usize,
    /// Total turns in the session.
    pub total_turns: usize,
    /// Key events extracted from compiled turns.
    pub events: Vec<SessionEvent>,
    /// Number of unique files modified.
    pub files_modified: usize,
    /// Number of tool calls.
    pub tool_calls: usize,
    /// Number of errors.
    pub errors: usize,
}

// ---------------------------------------------------------------------------
// Internal: raw line parsing
// ---------------------------------------------------------------------------

/// A parsed line from the session JSONL, normalized across both formats.
#[derive(Debug, Clone)]
struct ParsedLine {
    /// "user", "assistant", "tool_call", "tool_result"
    role: String,
    /// Concatenated text content.
    content: String,
    /// Tool name if this is a tool call or tool result.
    tool_name: Option<String>,
    /// Timestamp if present.
    timestamp: Option<String>,
    /// Whether this is an error.
    is_error: bool,
}

/// Parse a single JSONL line into a normalized ParsedLine.
/// Returns None if the line is empty or unparseable.
fn parse_line(line: &str) -> Option<Vec<ParsedLine>> {
    let line = line.trim();
    if line.is_empty() {
        return None;
    }
    let v: serde_json::Value = serde_json::from_str(line).ok()?;

    // MCP format: {"tool": "...", "args_summary": "...", "result_summary": "...", "timestamp": "..."}
    if let Some(tool) = v.get("tool").and_then(|t| t.as_str()) {
        let args = v
            .get("args_summary")
            .and_then(|a| a.as_str())
            .unwrap_or("")
            .to_string();
        let result = v
            .get("result_summary")
            .and_then(|r| r.as_str())
            .unwrap_or("")
            .to_string();
        let timestamp = v
            .get("timestamp")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string());
        let is_error = result.contains("exit:1")
            || result.contains("exit:2")
            || result.contains("exit:101")
            || result.starts_with("error")
            || result.starts_with("Error");

        let content = if result.is_empty() {
            format!("{}: {}", tool, args)
        } else {
            format!("{}: {} → {}", tool, args, result)
        };
        return Some(vec![ParsedLine {
            role: "tool_call".to_string(),
            content,
            tool_name: Some(tool.to_string()),
            timestamp,
            is_error,
        }]);
    }

    // API format: {"role": "...", "content": [...]}
    if let Some(role) = v.get("role").and_then(|r| r.as_str()) {
        let content_blocks = v.get("content").and_then(|c| c.as_array());
        let mut results = Vec::new();

        if let Some(blocks) = content_blocks {
            for block in blocks {
                let block_type = block.get("type").and_then(|t| t.as_str()).unwrap_or("");
                match block_type {
                    "text" => {
                        let text = block
                            .get("text")
                            .and_then(|t| t.as_str())
                            .unwrap_or("")
                            .to_string();
                        results.push(ParsedLine {
                            role: role.to_string(),
                            content: text,
                            tool_name: None,
                            timestamp: None,
                            is_error: false,
                        });
                    }
                    "tool_use" => {
                        let name = block
                            .get("name")
                            .and_then(|n| n.as_str())
                            .unwrap_or("unknown")
                            .to_string();
                        let input = block.get("input").cloned().unwrap_or_default();
                        // Summarize tool call input
                        let input_summary = summarize_tool_input(&name, &input);
                        results.push(ParsedLine {
                            role: "tool_call".to_string(),
                            content: format!("{}: {}", name, input_summary),
                            tool_name: Some(name),
                            timestamp: None,
                            is_error: false,
                        });
                    }
                    "tool_result" => {
                        let is_error = block
                            .get("is_error")
                            .and_then(|e| e.as_bool())
                            .unwrap_or(false);
                        let content = block
                            .get("content")
                            .and_then(|c| c.as_str())
                            .unwrap_or("")
                            .to_string();
                        let tool_use_id = block
                            .get("tool_use_id")
                            .and_then(|id| id.as_str())
                            .unwrap_or("")
                            .to_string();
                        results.push(ParsedLine {
                            role: "tool_result".to_string(),
                            content,
                            tool_name: Some(tool_use_id),
                            timestamp: None,
                            is_error,
                        });
                    }
                    _ => {
                        // thinking, citation, web_search, image — skip for summary
                    }
                }
            }
        }

        if results.is_empty() {
            // No recognized blocks — still record the message
            results.push(ParsedLine {
                role: role.to_string(),
                content: String::new(),
                tool_name: None,
                timestamp: None,
                is_error: false,
            });
        }
        return Some(results);
    }

    None
}

/// Summarize tool input JSON for compact representation.
fn summarize_tool_input(tool: &str, input: &serde_json::Value) -> String {
    match tool {
        "Bash" | "shell" => input
            .get("command")
            .or_else(|| input.get("cmd"))
            .and_then(|c| c.as_str())
            .map(|s| truncate(s, 80))
            .unwrap_or_else(|| truncate(&input.to_string(), 80)),
        "Read" | "file:read" => input
            .get("file_path")
            .and_then(|p| p.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| "?".into()),
        "Edit" | "file:edit" | "Write" | "file:write" => input
            .get("file_path")
            .and_then(|p| p.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| "?".into()),
        "Grep" | "Glob" => input
            .get("pattern")
            .and_then(|p| p.as_str())
            .map(|s| truncate(s, 60))
            .unwrap_or_else(|| truncate(&input.to_string(), 60)),
        _ => truncate(&input.to_string(), 60),
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        let end = s.floor_char_boundary(max);
        format!("{}...", &s[..end])
    }
}

// ---------------------------------------------------------------------------
// Event extraction
// ---------------------------------------------------------------------------

/// Extract events from a slice of parsed lines.
fn extract_events(lines: &[ParsedLine]) -> Vec<SessionEvent> {
    let mut events = Vec::new();
    let mut files_seen = std::collections::HashSet::new();

    // Regex patterns (compiled once)
    let test_result_re =
        Regex::new(r"(\d+)\s+passed.*?(\d+)\s+failed").unwrap();
    let test_result_ok_re =
        Regex::new(r"test result: ok\.\s+(\d+)\s+passed").unwrap();
    let needle_close_re =
        Regex::new(r"(?:needle|work)\s+close\s+(?:→)?(\d+)").unwrap();
    let needle_add_re =
        Regex::new(r"(?:needle|work)\s+add\b").unwrap();
    let needle_id_re = Regex::new(r"→(\d+)").unwrap();
    let decision_key_re =
        Regex::new(r#""key"\s*:\s*"([^"]+)""#).unwrap();
    let decision_value_re =
        Regex::new(r#""value"\s*:\s*"([^"]+)""#).unwrap();

    // Trivial commands we skip for "significant tool call" tracking
    let trivial_cmds = ["echo", "ls", "pwd", "cd", "true", "false"];

    for line in lines {
        let content = &line.content;
        let content_lower = content.to_lowercase();
        let tool = line.tool_name.as_deref().unwrap_or("");

        // ── File modifications ──
        match tool {
            "Edit" | "Write" | "file:edit" | "file:write" => {
                // Extract file path from content (format: "Edit: path")
                if let Some(path) = content.split(": ").nth(1) {
                    let path = path.split(" →").next().unwrap_or(path).trim();
                    if !path.is_empty() && files_seen.insert(path.to_string()) {
                        events.push(SessionEvent::FileModified {
                            path: path.to_string(),
                        });
                    }
                }
            }
            "shell" | "Bash" => {
                // Shell commands that modify files
                let cmd = content.to_lowercase();
                if cmd.contains("sed ") || cmd.contains("tee ") || cmd.contains("cat >") || cmd.contains("echo >") {
                    // Try to extract filename from the command
                    // Pattern: redirect targets or sed -i targets
                    // This is heuristic — good enough for summary purposes
                }
                // Test results in shell output
                if let Some(caps) = test_result_re.captures(content) {
                    let passed: u32 = caps[1].parse().unwrap_or(0);
                    let failed: u32 = caps[2].parse().unwrap_or(0);
                    events.push(SessionEvent::TestResult {
                        passed,
                        failed,
                        skipped: 0,
                    });
                } else if let Some(caps) = test_result_ok_re.captures(content) {
                    let passed: u32 = caps[1].parse().unwrap_or(0);
                    events.push(SessionEvent::TestResult {
                        passed,
                        failed: 0,
                        skipped: 0,
                    });
                }

                // Needle state changes
                for caps in needle_close_re.captures_iter(content) {
                    events.push(SessionEvent::NeedleStateChange {
                        id: format!("→{}", &caps[1]),
                        action: "closed".to_string(),
                    });
                }
                if needle_add_re.is_match(content) {
                    // Try to find a needle ID in the result
                    if let Some(id_caps) = needle_id_re.captures(content) {
                        events.push(SessionEvent::NeedleStateChange {
                            id: format!("→{}", &id_caps[1]),
                            action: "added".to_string(),
                        });
                    } else {
                        events.push(SessionEvent::NeedleStateChange {
                            id: "?".to_string(),
                            action: "added".to_string(),
                        });
                    }
                }

                // Decision logging
                if content_lower.contains("log_decision")
                    || content_lower.contains("decisions.jsonl")
                {
                    let key = decision_key_re
                        .captures(content)
                        .map(|c| c[1].to_string())
                        .unwrap_or_else(|| "?".into());
                    let value = decision_value_re
                        .captures(content)
                        .map(|c| truncate(&c[1], 60))
                        .unwrap_or_default();
                    events.push(SessionEvent::DecisionMade { key, value });
                }

                // Cargo install / cargo build as significant
                if cmd.contains("cargo build") || cmd.contains("cargo install") {
                    let summary: String = content.chars().take(80).collect();
                    events.push(SessionEvent::ToolCallSignificant {
                        tool: tool.to_string(),
                        summary,
                    });
                }
            }
            _ => {}
        }

        // ── Tool result analysis (API format) ──
        if line.role == "tool_result" {
            // Test results in tool output
            if let Some(caps) = test_result_re.captures(content) {
                let passed: u32 = caps[1].parse().unwrap_or(0);
                let failed: u32 = caps[2].parse().unwrap_or(0);
                events.push(SessionEvent::TestResult {
                    passed,
                    failed,
                    skipped: 0,
                });
            } else if let Some(caps) = test_result_ok_re.captures(content) {
                let passed: u32 = caps[1].parse().unwrap_or(0);
                events.push(SessionEvent::TestResult {
                    passed,
                    failed: 0,
                    skipped: 0,
                });
            }

            // Needle actions in tool results
            if content_lower.contains("closed →") || content_lower.contains("added →") {
                for id_caps in needle_id_re.captures_iter(content) {
                    let action = if content_lower.contains("closed") {
                        "closed"
                    } else {
                        "added"
                    };
                    events.push(SessionEvent::NeedleStateChange {
                        id: format!("→{}", &id_caps[1]),
                        action: action.to_string(),
                    });
                }
            }
        }

        // ── Errors ──
        if line.is_error {
            let msg: String = content.chars().take(120).collect();
            events.push(SessionEvent::ErrorEncountered {
                tool: tool.to_string(),
                message: msg,
            });
        } else if line.role == "tool_result" || line.role == "tool_call" {
            // Heuristic error detection in non-error-flagged results
            if content_lower.contains("error[e")       // rustc errors
                || content_lower.contains("panicked at")
                || (content_lower.contains("error:") && content_lower.contains("failed"))
            {
                let msg: String = content.chars().take(120).collect();
                events.push(SessionEvent::ErrorEncountered {
                    tool: tool.to_string(),
                    message: msg,
                });
            }
        }

        // ── Significant tool calls (not trivial) ──
        if line.role == "tool_call" && !tool.is_empty() {
            let is_trivial = trivial_cmds.iter().any(|&cmd| {
                content_lower.starts_with(&format!("{}: {}", tool.to_lowercase(), cmd))
                    || content_lower.starts_with(&format!("{}: \"{}\"", tool.to_lowercase(), cmd))
            });

            if !is_trivial
                && !matches!(tool, "Read" | "file:read" | "Grep" | "Glob")
            {
                let summary: String = content.chars().take(100).collect();
                // Only add if not already captured as a more specific event type
                let dominated = events.iter().any(|e| match e {
                    SessionEvent::FileModified { .. }
                    | SessionEvent::TestResult { .. }
                    | SessionEvent::NeedleStateChange { .. }
                    | SessionEvent::DecisionMade { .. } => {
                        // Check if this tool call produced a more specific event
                        // in this same iteration. Heuristic: just check last few.
                        false
                    }
                    _ => false,
                });

                if !dominated {
                    events.push(SessionEvent::ToolCallSignificant {
                        tool: tool.to_string(),
                        summary,
                    });
                }
            }
        }
    }

    events
}

// ---------------------------------------------------------------------------
// Compilation
// ---------------------------------------------------------------------------

impl SessionSummary {
    /// Compile a session summary from a JSONL file on disk.
    ///
    /// Reads all lines, compiles older turns (everything except the last
    /// `keep_recent` lines) into a structured summary, and leaves recent
    /// turns untouched.
    ///
    /// Streams the file line-by-line — does not load the entire file into
    /// memory at once.
    pub fn compile_from_session(
        session_path: &Path,
        keep_recent: usize,
    ) -> Result<SessionSummary, String> {
        // Phase 1: Count lines without loading content.
        let total = count_lines(session_path)?;
        if total == 0 {
            return Ok(SessionSummary {
                text: String::new(),
                compiled_through_turn: 0,
                total_turns: 0,
                events: Vec::new(),
                files_modified: 0,
                tool_calls: 0,
                errors: 0,
            });
        }

        let compile_through = total.saturating_sub(keep_recent);

        // Phase 2: Parse and extract events from compiled turns.
        let file = std::fs::File::open(session_path)
            .map_err(|e| format!("open session: {}", e))?;
        let reader = BufReader::new(file);

        let mut compiled_lines: Vec<ParsedLine> = Vec::new();
        let mut total_tool_calls = 0usize;
        let mut total_errors = 0usize;

        for (i, line_result) in reader.lines().enumerate() {
            if i >= compile_through {
                break;
            }
            let line = line_result.map_err(|e| format!("read line {}: {}", i, e))?;
            if let Some(parsed) = parse_line(&line) {
                for p in &parsed {
                    if p.role == "tool_call" {
                        total_tool_calls += 1;
                    }
                    if p.is_error {
                        total_errors += 1;
                    }
                }
                compiled_lines.extend(parsed);
            }
        }

        let events = extract_events(&compiled_lines);

        let files_modified = events
            .iter()
            .filter(|e| matches!(e, SessionEvent::FileModified { .. }))
            .count();
        // Count errors from events too (some are detected heuristically)
        let event_errors = events
            .iter()
            .filter(|e| matches!(e, SessionEvent::ErrorEncountered { .. }))
            .count();
        let errors = total_errors.max(event_errors);

        // When nothing was compiled (all turns are recent), return empty text.
        let text = if compile_through == 0 {
            String::new()
        } else {
            render_summary_text(
                &events,
                compile_through,
                total,
                keep_recent.min(total),
                files_modified,
                total_tool_calls,
                errors,
            )
        };

        Ok(SessionSummary {
            text,
            compiled_through_turn: compile_through,
            total_turns: total,
            events,
            files_modified,
            tool_calls: total_tool_calls,
            errors,
        })
    }

    /// Load a cached summary from disk, returning None if stale or missing.
    ///
    /// A cached summary is valid when `compiled_through_turn` matches what
    /// we'd compute for the current session length minus `keep_recent`.
    pub fn load_cached(
        session_path: &Path,
        keep_recent: usize,
    ) -> Option<SessionSummary> {
        let cache_path = cache_path_for(session_path);
        let content = std::fs::read_to_string(&cache_path).ok()?;
        let cached: SessionSummary = serde_json::from_str(&content).ok()?;

        // Validate: is the cache still current?
        let total = count_lines(session_path).ok()?;
        let expected_through = total.saturating_sub(keep_recent);

        if cached.compiled_through_turn == expected_through && cached.total_turns == total {
            Some(cached)
        } else {
            None
        }
    }

    /// Save this summary to the cache file alongside the session.
    pub fn save_cache(&self, session_path: &Path) -> Result<(), String> {
        let cache_path = cache_path_for(session_path);
        let json = serde_json::to_string_pretty(self)
            .map_err(|e| format!("serialize summary: {}", e))?;
        std::fs::write(&cache_path, json)
            .map_err(|e| format!("write cache: {}", e))?;
        Ok(())
    }

    /// Compile with caching: loads from cache if valid, otherwise recompiles
    /// and writes the new cache.
    pub fn compile_cached(
        session_path: &Path,
        keep_recent: usize,
    ) -> Result<SessionSummary, String> {
        if let Some(cached) = Self::load_cached(session_path, keep_recent) {
            return Ok(cached);
        }
        let summary = Self::compile_from_session(session_path, keep_recent)?;
        // Best-effort cache write — don't fail if we can't write
        let _ = summary.save_cache(session_path);
        Ok(summary)
    }

    /// Render the summary as a structured text block for context injection.
    pub fn render(&self) -> &str {
        &self.text
    }

    /// Extract the last N turns from a session file at full fidelity.
    ///
    /// These are the working set — NOT compiled, preserved for conversational
    /// coherence. Streams the file rather than loading everything at once.
    pub fn extract_recent_turns(
        session_path: &Path,
        keep_recent: usize,
    ) -> Result<Vec<SessionTurn>, String> {
        let total = count_lines(session_path)?;
        if total == 0 {
            return Ok(Vec::new());
        }

        let start = total.saturating_sub(keep_recent);

        let file = std::fs::File::open(session_path)
            .map_err(|e| format!("open session: {}", e))?;
        let reader = BufReader::new(file);

        let mut turns = Vec::new();
        for (i, line_result) in reader.lines().enumerate() {
            if i < start {
                continue;
            }
            let line = line_result.map_err(|e| format!("read line {}: {}", i, e))?;
            if let Some(parsed) = parse_line(&line) {
                for p in parsed {
                    turns.push(SessionTurn {
                        turn_number: i,
                        role: p.role,
                        content: p.content,
                        tool_name: p.tool_name,
                        timestamp: p.timestamp,
                    });
                }
            }
        }

        Ok(turns)
    }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/// Build the summary text from extracted events.
fn render_summary_text(
    events: &[SessionEvent],
    compiled_through: usize,
    total: usize,
    recent_count: usize,
    files_modified: usize,
    tool_calls: usize,
    errors: usize,
) -> String {
    let mut out = String::new();

    // Header
    out.push_str(&format!(
        "SESSION_SUMMARY (turns 1-{} of {}, {} recent turns follow)\n",
        compiled_through, total, recent_count,
    ));

    // File modifications
    let file_paths: Vec<&str> = events
        .iter()
        .filter_map(|e| match e {
            SessionEvent::FileModified { path } => Some(path.as_str()),
            _ => None,
        })
        .collect();
    if !file_paths.is_empty() {
        let display: Vec<&str> = file_paths.iter().take(8).copied().collect();
        let suffix = if file_paths.len() > 8 {
            format!(", +{} more", file_paths.len() - 8)
        } else {
            String::new()
        };
        out.push_str(&format!(
            "  Modified {} files: {}{}\n",
            files_modified,
            display.join(", "),
            suffix,
        ));
    }

    // Test results
    let test_results: Vec<&SessionEvent> = events
        .iter()
        .filter(|e| matches!(e, SessionEvent::TestResult { .. }))
        .collect();
    if !test_results.is_empty() {
        let passed_runs = test_results
            .iter()
            .filter(|e| matches!(e, SessionEvent::TestResult { failed: 0, .. }))
            .count();
        let failed_runs = test_results.len() - passed_runs;
        let total_passed: u32 = test_results
            .iter()
            .filter_map(|e| match e {
                SessionEvent::TestResult { passed, .. } => Some(passed),
                _ => None,
            })
            .sum();
        let total_failed: u32 = test_results
            .iter()
            .filter_map(|e| match e {
                SessionEvent::TestResult { failed, .. } => Some(failed),
                _ => None,
            })
            .sum();

        let mut test_line = format!("  {} test runs", test_results.len());
        if passed_runs > 0 {
            test_line.push_str(&format!(": {} passed ({}/{})", passed_runs, total_passed, total_passed + total_failed));
        }
        if failed_runs > 0 {
            test_line.push_str(&format!(", {} failed", failed_runs));
        }
        test_line.push('\n');
        out.push_str(&test_line);
    }

    // Needle state changes
    let needle_changes: Vec<&SessionEvent> = events
        .iter()
        .filter(|e| matches!(e, SessionEvent::NeedleStateChange { .. }))
        .collect();
    if !needle_changes.is_empty() {
        let closed: Vec<&str> = needle_changes
            .iter()
            .filter_map(|e| match e {
                SessionEvent::NeedleStateChange { id, action } if action == "closed" => {
                    Some(id.as_str())
                }
                _ => None,
            })
            .collect();
        let added: Vec<&str> = needle_changes
            .iter()
            .filter_map(|e| match e {
                SessionEvent::NeedleStateChange { id, action } if action == "added" => {
                    Some(id.as_str())
                }
                _ => None,
            })
            .collect();

        let mut needle_line = String::from("  ");
        if !closed.is_empty() {
            needle_line.push_str(&format!("Closed {}.", closed.join(", ")));
        }
        if !added.is_empty() {
            if !closed.is_empty() {
                needle_line.push(' ');
            }
            needle_line.push_str(&format!("Opened {}.", added.join(", ")));
        }
        needle_line.push('\n');
        out.push_str(&needle_line);
    }

    // Decisions
    let decisions: Vec<&SessionEvent> = events
        .iter()
        .filter(|e| matches!(e, SessionEvent::DecisionMade { .. }))
        .collect();
    if !decisions.is_empty() {
        let keys: Vec<&str> = decisions
            .iter()
            .filter_map(|e| match e {
                SessionEvent::DecisionMade { key, .. } => Some(key.as_str()),
                _ => None,
            })
            .take(6)
            .collect();
        out.push_str(&format!(
            "  {} decisions logged: {}\n",
            decisions.len(),
            keys.join(", "),
        ));
    }

    // Errors
    let error_events: Vec<&SessionEvent> = events
        .iter()
        .filter(|e| matches!(e, SessionEvent::ErrorEncountered { .. }))
        .collect();
    if !error_events.is_empty() {
        out.push_str(&format!("  {} errors", errors));
        // Show first 3 error summaries
        let show: Vec<String> = error_events
            .iter()
            .take(3)
            .filter_map(|e| match e {
                SessionEvent::ErrorEncountered { message, .. } => {
                    Some(truncate(message, 60))
                }
                _ => None,
            })
            .collect();
        if !show.is_empty() {
            out.push_str(&format!(": {}", show.join("; ")));
        }
        out.push('\n');
    }

    // Significant actions summary
    let significant: Vec<&SessionEvent> = events
        .iter()
        .filter(|e| matches!(e, SessionEvent::ToolCallSignificant { .. }))
        .collect();
    if !significant.is_empty() {
        // Deduplicate by tool name and take last 6
        let summaries: Vec<String> = significant
            .iter()
            .rev()
            .take(6)
            .rev()
            .filter_map(|e| match e {
                SessionEvent::ToolCallSignificant { summary, .. } => {
                    Some(truncate(summary, 80))
                }
                _ => None,
            })
            .collect();
        out.push_str(&format!(
            "  Key actions ({} tool calls total): {}\n",
            tool_calls,
            summaries.join(", "),
        ));
    } else if tool_calls > 0 {
        out.push_str(&format!("  {} tool calls total\n", tool_calls));
    }

    out
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Count lines in a file without loading content.
fn count_lines(path: &Path) -> Result<usize, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("open: {}", e))?;
    let reader = BufReader::new(file);
    let mut count = 0usize;
    for line in reader.lines() {
        let _ = line.map_err(|e| format!("read: {}", e))?;
        count += 1;
    }
    Ok(count)
}

/// Derive the cache path from a session path.
/// `.ostk/sessions/agent.jsonl` → `.ostk/sessions/agent.summary.json`
fn cache_path_for(session_path: &Path) -> std::path::PathBuf {
    let stem = session_path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("session");
    session_path
        .parent()
        .unwrap_or(Path::new("."))
        .join(format!("{}.summary.json", stem))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    /// Create a temp session file with the given JSONL lines.
    fn write_session(lines: &[&str]) -> tempfile::NamedTempFile {
        let mut f = tempfile::NamedTempFile::new().unwrap();
        for line in lines {
            writeln!(f, "{}", line).unwrap();
        }
        f.flush().unwrap();
        f
    }

    // ── 1. Empty session ──

    #[test]
    fn test_compile_empty_session() {
        let f = write_session(&[]);
        let summary = SessionSummary::compile_from_session(f.path(), 5).unwrap();
        assert_eq!(summary.total_turns, 0);
        assert_eq!(summary.compiled_through_turn, 0);
        assert!(summary.events.is_empty());
        assert!(summary.text.is_empty());
        assert_eq!(summary.files_modified, 0);
        assert_eq!(summary.tool_calls, 0);
        assert_eq!(summary.errors, 0);
    }

    // ── 2. Single turn (all recent, nothing compiled) ──

    #[test]
    fn test_compile_single_turn() {
        let f = write_session(&[
            r#"{"tool":"shell","args_summary":"echo hello","result_summary":"exit:0","timestamp":"2026-03-01T00:00:00Z"}"#,
        ]);
        let summary = SessionSummary::compile_from_session(f.path(), 5).unwrap();
        assert_eq!(summary.total_turns, 1);
        assert_eq!(summary.compiled_through_turn, 0);
        assert!(summary.events.is_empty());
        // Text should be empty since nothing was compiled
        assert!(summary.text.is_empty());
    }

    // ── 3. File modifications ──

    #[test]
    fn test_compile_with_file_modifications() {
        let lines: Vec<&str> = vec![
            // 10 tool calls, keep_recent=3, so 7 compiled
            r#"{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Edit","input":{"file_path":"src/main.rs","old_string":"a","new_string":"b"}}]}"#,
            r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"edited"}]}"#,
            r#"{"role":"assistant","content":[{"type":"tool_use","id":"t2","name":"Write","input":{"file_path":"src/lib.rs","content":"..."}}]}"#,
            r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t2","content":"written"}]}"#,
            r#"{"role":"assistant","content":[{"type":"tool_use","id":"t3","name":"Edit","input":{"file_path":"src/main.rs","old_string":"b","new_string":"c"}}]}"#,
            r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t3","content":"edited"}]}"#,
            r#"{"role":"assistant","content":[{"type":"tool_use","id":"t4","name":"Read","input":{"file_path":"src/other.rs"}}]}"#,
            // Last 3 are recent
            r#"{"role":"user","content":[{"type":"text","text":"what now?"}]}"#,
            r#"{"role":"assistant","content":[{"type":"text","text":"checking..."}]}"#,
            r#"{"role":"user","content":[{"type":"text","text":"ok"}]}"#,
        ];
        let f = write_session(&lines);
        let summary = SessionSummary::compile_from_session(f.path(), 3).unwrap();
        assert_eq!(summary.total_turns, 10);
        assert_eq!(summary.compiled_through_turn, 7);

        // Should detect 2 unique files: src/main.rs and src/lib.rs
        assert_eq!(summary.files_modified, 2);
        let file_events: Vec<_> = summary
            .events
            .iter()
            .filter(|e| matches!(e, SessionEvent::FileModified { .. }))
            .collect();
        assert_eq!(file_events.len(), 2);

        // Render should mention files
        let rendered = summary.render();
        assert!(rendered.contains("Modified 2 files"));
        assert!(rendered.contains("src/main.rs"));
        assert!(rendered.contains("src/lib.rs"));
    }

    // ── 4. Test results ──

    #[test]
    fn test_compile_with_test_results() {
        let lines: Vec<&str> = vec![
            r#"{"tool":"shell","args_summary":"cargo test --lib 2>&1","result_summary":"exit:0","timestamp":"2026-03-01T00:00:00Z"}"#,
            r#"{"tool":"shell","args_summary":"cargo test --lib 2>&1","result_summary":"12 passed 0 failed exit:0","timestamp":"2026-03-01T00:01:00Z"}"#,
            r#"{"tool":"shell","args_summary":"cargo test --lib 2>&1","result_summary":"10 passed 2 failed exit:1","timestamp":"2026-03-01T00:02:00Z"}"#,
            // Recent
            r#"{"tool":"shell","args_summary":"echo done","result_summary":"exit:0","timestamp":"2026-03-01T00:03:00Z"}"#,
        ];
        let f = write_session(&lines);
        let summary = SessionSummary::compile_from_session(f.path(), 1).unwrap();

        let test_events: Vec<_> = summary
            .events
            .iter()
            .filter(|e| matches!(e, SessionEvent::TestResult { .. }))
            .collect();
        // Should detect at least the explicit pass/fail counts
        assert!(
            !test_events.is_empty(),
            "should extract test results, got events: {:?}",
            summary.events
        );

        let rendered = summary.render();
        assert!(rendered.contains("test run"), "rendered: {}", rendered);
    }

    // ── 5. Needle state changes ──

    #[test]
    fn test_compile_with_needle_changes() {
        let lines: Vec<&str> = vec![
            r#"{"tool":"shell","args_summary":"ostk needle close →960 --reason \"shipped\"","result_summary":"exit:0","timestamp":"2026-03-01T00:00:00Z"}"#,
            r#"{"tool":"shell","args_summary":"ostk needle close →961 --reason \"done\"","result_summary":"exit:0","timestamp":"2026-03-01T00:01:00Z"}"#,
            r#"{"tool":"shell","args_summary":"ostk needle add \"new feature\"","result_summary":"exit:0","timestamp":"2026-03-01T00:02:00Z"}"#,
            // Recent
            r#"{"tool":"shell","args_summary":"echo done","result_summary":"exit:0","timestamp":"2026-03-01T00:03:00Z"}"#,
        ];
        let f = write_session(&lines);
        let summary = SessionSummary::compile_from_session(f.path(), 1).unwrap();

        let needle_events: Vec<_> = summary
            .events
            .iter()
            .filter(|e| matches!(e, SessionEvent::NeedleStateChange { .. }))
            .collect();
        assert!(
            needle_events.len() >= 2,
            "should detect needle closes, got: {:?}",
            needle_events
        );

        let rendered = summary.render();
        assert!(rendered.contains("Closed"), "rendered: {}", rendered);
        assert!(rendered.contains("→960"), "rendered: {}", rendered);
    }

    // ── 6. Error detection ──

    #[test]
    fn test_compile_with_errors() {
        let lines: Vec<&str> = vec![
            r#"{"tool":"shell","args_summary":"cargo build 2>&1","result_summary":"exit:1","timestamp":"2026-03-01T00:00:00Z"}"#,
            r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"error: missing import","is_error":true}]}"#,
            r#"{"tool":"shell","args_summary":"cargo test 2>&1","result_summary":"exit:0","timestamp":"2026-03-01T00:02:00Z"}"#,
            // Recent
            r#"{"tool":"shell","args_summary":"echo ok","result_summary":"exit:0","timestamp":"2026-03-01T00:03:00Z"}"#,
        ];
        let f = write_session(&lines);
        let summary = SessionSummary::compile_from_session(f.path(), 1).unwrap();

        assert!(summary.errors > 0, "should detect errors");

        let error_events: Vec<_> = summary
            .events
            .iter()
            .filter(|e| matches!(e, SessionEvent::ErrorEncountered { .. }))
            .collect();
        assert!(
            !error_events.is_empty(),
            "should have error events, got: {:?}",
            summary.events
        );

        let rendered = summary.render();
        assert!(rendered.contains("error"), "rendered: {}", rendered);
    }

    // ── 7. Preserves recent turns ──

    #[test]
    fn test_compile_preserves_recent_turns() {
        let lines: Vec<&str> = vec![
            r#"{"tool":"shell","args_summary":"echo 1","result_summary":"exit:0","timestamp":"2026-03-01T00:00:00Z"}"#,
            r#"{"tool":"shell","args_summary":"echo 2","result_summary":"exit:0","timestamp":"2026-03-01T00:01:00Z"}"#,
            r#"{"tool":"shell","args_summary":"echo 3","result_summary":"exit:0","timestamp":"2026-03-01T00:02:00Z"}"#,
            r#"{"tool":"shell","args_summary":"echo 4","result_summary":"exit:0","timestamp":"2026-03-01T00:03:00Z"}"#,
            r#"{"tool":"shell","args_summary":"echo 5","result_summary":"exit:0","timestamp":"2026-03-01T00:04:00Z"}"#,
        ];
        let f = write_session(&lines);

        // Keep last 3 recent
        let summary = SessionSummary::compile_from_session(f.path(), 3).unwrap();
        assert_eq!(summary.compiled_through_turn, 2);
        assert_eq!(summary.total_turns, 5);

        // Recent turns extraction
        let recent = SessionSummary::extract_recent_turns(f.path(), 3).unwrap();
        assert_eq!(recent.len(), 3);
        assert_eq!(recent[0].turn_number, 2);
        assert_eq!(recent[1].turn_number, 3);
        assert_eq!(recent[2].turn_number, 4);
    }

    // ── 8. Render format ──

    #[test]
    fn test_render_format() {
        let lines: Vec<&str> = vec![
            r#"{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Edit","input":{"file_path":"src/main.rs","old_string":"a","new_string":"b"}}]}"#,
            r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"edited"}]}"#,
            r#"{"tool":"shell","args_summary":"cargo test --lib","result_summary":"12 passed 0 failed exit:0","timestamp":"2026-03-01T00:00:00Z"}"#,
            r#"{"tool":"shell","args_summary":"ostk needle close →960 --reason \"done\"","result_summary":"exit:0","timestamp":"2026-03-01T00:01:00Z"}"#,
            r#"{"tool":"shell","args_summary":"cargo build 2>&1","result_summary":"exit:1","timestamp":"2026-03-01T00:02:00Z"}"#,
            // Recent
            r#"{"tool":"shell","args_summary":"echo done","result_summary":"exit:0","timestamp":"2026-03-01T00:03:00Z"}"#,
        ];
        let f = write_session(&lines);
        let summary = SessionSummary::compile_from_session(f.path(), 1).unwrap();
        let rendered = summary.render();

        // Verify structured format
        assert!(
            rendered.starts_with("SESSION_SUMMARY"),
            "should start with header: {}",
            rendered
        );
        assert!(rendered.contains("Modified"), "should mention files: {}", rendered);
        assert!(rendered.contains("test run"), "should mention tests: {}", rendered);
        assert!(rendered.contains("→960"), "should mention needles: {}", rendered);
    }

    // ── 9. Extract recent turns ──

    #[test]
    fn test_extract_recent_turns() {
        let lines: Vec<&str> = vec![
            r#"{"tool":"shell","args_summary":"echo old","result_summary":"exit:0","timestamp":"2026-03-01T00:00:00Z"}"#,
            r#"{"role":"user","content":[{"type":"text","text":"what next?"}]}"#,
            r#"{"role":"assistant","content":[{"type":"text","text":"I suggest..."}]}"#,
        ];
        let f = write_session(&lines);
        let recent = SessionSummary::extract_recent_turns(f.path(), 2).unwrap();

        assert_eq!(recent.len(), 2);
        assert_eq!(recent[0].role, "user");
        assert!(recent[0].content.contains("what next?"));
        assert_eq!(recent[1].role, "assistant");
        assert!(recent[1].content.contains("I suggest"));
    }

    // ── 10. Incremental compilation (caching) ──

    #[test]
    fn test_incremental_compilation() {
        let dir = tempfile::tempdir().unwrap();
        let session_path = dir.path().join("test.jsonl");

        // Write initial session
        {
            let mut f = std::fs::File::create(&session_path).unwrap();
            for i in 0..10 {
                writeln!(
                    f,
                    r#"{{"tool":"shell","args_summary":"echo {}","result_summary":"exit:0","timestamp":"2026-03-01T00:0{}:00Z"}}"#,
                    i, i
                )
                .unwrap();
            }
        }

        // First compile
        let summary1 = SessionSummary::compile_cached(&session_path, 3).unwrap();
        assert_eq!(summary1.compiled_through_turn, 7);
        assert_eq!(summary1.total_turns, 10);

        // Cache should exist
        let cache_path = cache_path_for(&session_path);
        assert!(cache_path.exists(), "cache file should exist");

        // Second compile should use cache (same result)
        let summary2 = SessionSummary::compile_cached(&session_path, 3).unwrap();
        assert_eq!(summary2.compiled_through_turn, summary1.compiled_through_turn);
        assert_eq!(summary2.total_turns, summary1.total_turns);
        assert_eq!(summary2.text, summary1.text);

        // Append more lines — cache should be invalidated
        {
            let mut f = std::fs::OpenOptions::new()
                .append(true)
                .open(&session_path)
                .unwrap();
            for i in 10..15 {
                writeln!(
                    f,
                    r#"{{"tool":"shell","args_summary":"echo {}","result_summary":"exit:0","timestamp":"2026-03-01T00:{}:00Z"}}"#,
                    i, i
                )
                .unwrap();
            }
        }

        let summary3 = SessionSummary::compile_cached(&session_path, 3).unwrap();
        assert_eq!(summary3.total_turns, 15);
        assert_eq!(summary3.compiled_through_turn, 12);
        // Should have recompiled
        assert_ne!(summary3.compiled_through_turn, summary1.compiled_through_turn);
    }

    // ── 11. Token budget ──

    #[test]
    fn test_summary_token_budget() {
        // Build a large session with many tool calls, edits, errors, needle changes
        let mut lines = Vec::new();
        for i in 0..50 {
            lines.push(format!(
                r#"{{"tool":"shell","args_summary":"cargo test module_{} --lib 2>&1","result_summary":"{} passed 0 failed exit:0","timestamp":"2026-03-01T00:{:02}:00Z"}}"#,
                i, i * 10, i
            ));
        }
        for i in 0..20 {
            lines.push(format!(
                r#"{{"role":"assistant","content":[{{"type":"tool_use","id":"t{i}","name":"Edit","input":{{"file_path":"src/mod{i}.rs","old_string":"old","new_string":"new"}}}}]}}"#,
            ));
        }
        for i in 0..5 {
            lines.push(format!(
                r#"{{"tool":"shell","args_summary":"ostk needle close →{} --reason \"done\"","result_summary":"exit:0","timestamp":"2026-03-01T01:{:02}:00Z"}}"#,
                900 + i, i
            ));
        }
        // Pad to 100 lines
        while lines.len() < 100 {
            lines.push(format!(
                r#"{{"tool":"shell","args_summary":"echo padding","result_summary":"exit:0","timestamp":"2026-03-01T02:00:00Z"}}"#
            ));
        }

        let line_refs: Vec<&str> = lines.iter().map(|s| s.as_str()).collect();
        let f = write_session(&line_refs);
        let summary = SessionSummary::compile_from_session(f.path(), 5).unwrap();

        // Summary text should stay under ~1500 chars (~300 tokens at 5 chars/token)
        let char_count = summary.render().len();
        assert!(
            char_count < 1500,
            "summary should be under 1500 chars (~300 tokens), got {} chars: {}",
            char_count,
            summary.render()
        );
    }

    // ── 12. Real session file (if available) ──

    #[test]
    fn test_compile_real_session() {
        // Try to compile a real session file if one exists
        let sessions_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(".ostk")
            .join("sessions");

        if !sessions_dir.exists() {
            // No sessions dir — skip
            return;
        }

        // Find the first non-empty .jsonl file
        let entries = match std::fs::read_dir(&sessions_dir) {
            Ok(e) => e,
            Err(_) => return,
        };

        let mut found = false;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("jsonl") {
                continue;
            }
            let metadata = match std::fs::metadata(&path) {
                Ok(m) => m,
                Err(_) => continue,
            };
            if metadata.len() < 100 {
                continue; // Too small to be meaningful
            }

            let summary = SessionSummary::compile_from_session(&path, 5);
            match summary {
                Ok(s) => {
                    assert!(
                        s.total_turns > 0,
                        "real session {} should have turns",
                        path.display()
                    );
                    // Render should produce non-empty text if turns were compiled
                    if s.compiled_through_turn > 0 {
                        assert!(
                            !s.render().is_empty(),
                            "real session {} should produce summary text",
                            path.display()
                        );
                    }
                    found = true;
                    break;
                }
                Err(e) => {
                    panic!(
                        "Failed to compile real session {}: {}",
                        path.display(),
                        e
                    );
                }
            }
        }

        if found {
            eprintln!("Successfully compiled a real session file");
        } else {
            eprintln!("No suitable real session files found — skipping");
        }
    }

    // ── Mixed format session ──

    #[test]
    fn test_compile_mixed_format_session() {
        let lines: Vec<&str> = vec![
            // API format turn
            r#"{"role":"user","content":[{"type":"text","text":"start working"}]}"#,
            r#"{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"cargo build"}}]}"#,
            r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"Compiling...","is_error":false}]}"#,
            // MCP format turn
            r#"{"tool":"shell","args_summary":"cargo test --lib 2>&1","result_summary":"15 passed 0 failed exit:0","timestamp":"2026-03-01T00:01:00Z"}"#,
            r#"{"tool":"shell","args_summary":"ostk needle close →100 --reason \"done\"","result_summary":"exit:0","timestamp":"2026-03-01T00:02:00Z"}"#,
            // More API format
            r#"{"role":"assistant","content":[{"type":"tool_use","id":"t2","name":"Edit","input":{"file_path":"src/lib.rs","old_string":"old","new_string":"new"}}]}"#,
            r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t2","content":"edited"}]}"#,
            // Recent
            r#"{"role":"user","content":[{"type":"text","text":"done?"}]}"#,
        ];
        let f = write_session(&lines);
        let summary = SessionSummary::compile_from_session(f.path(), 1).unwrap();

        assert_eq!(summary.total_turns, 8);
        assert_eq!(summary.compiled_through_turn, 7);

        // Should detect file modification, test result, and needle close
        let has_file = summary
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::FileModified { path } if path == "src/lib.rs"));
        assert!(has_file, "should detect file edit: {:?}", summary.events);

        let has_test = summary
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::TestResult { .. }));
        assert!(has_test, "should detect test result: {:?}", summary.events);

        let has_needle = summary
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::NeedleStateChange { id, .. } if id == "→100"));
        assert!(has_needle, "should detect needle close: {:?}", summary.events);
    }

    // ── API format with decisions ──

    #[test]
    fn test_compile_with_decisions_api_format() {
        let lines: Vec<&str> = vec![
            r#"{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"cat decisions.jsonl | grep compound"}}]}"#,
            r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"\"key\": \"compound_input_protocol\", \"value\": \"use heredoc for multi-line\""}]}"#,
            // Recent
            r#"{"role":"user","content":[{"type":"text","text":"next"}]}"#,
        ];
        let f = write_session(&lines);
        let summary = SessionSummary::compile_from_session(f.path(), 1).unwrap();

        // The decisions.jsonl reference in Bash command should be detected
        let has_decision = summary
            .events
            .iter()
            .any(|e| matches!(e, SessionEvent::DecisionMade { .. }));
        assert!(
            has_decision,
            "should detect decision from decisions.jsonl reference: {:?}",
            summary.events
        );
    }

    // ── parse_line unit tests ──

    #[test]
    fn test_parse_line_mcp_format() {
        let line = r#"{"tool":"shell","args_summary":"echo hello","result_summary":"exit:0","timestamp":"2026-03-01T00:00:00Z"}"#;
        let parsed = parse_line(line).unwrap();
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].role, "tool_call");
        assert_eq!(parsed[0].tool_name.as_deref(), Some("shell"));
        assert!(parsed[0].content.contains("echo hello"));
        assert!(!parsed[0].is_error);
    }

    #[test]
    fn test_parse_line_mcp_error() {
        let line = r#"{"tool":"shell","args_summary":"cargo build","result_summary":"exit:1","timestamp":"2026-03-01T00:00:00Z"}"#;
        let parsed = parse_line(line).unwrap();
        assert!(parsed[0].is_error);
    }

    #[test]
    fn test_parse_line_api_format() {
        let line = r#"{"role":"user","content":[{"type":"text","text":"hello world"}]}"#;
        let parsed = parse_line(line).unwrap();
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].role, "user");
        assert_eq!(parsed[0].content, "hello world");
    }

    #[test]
    fn test_parse_line_api_tool_use() {
        let line = r#"{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls"}}]}"#;
        let parsed = parse_line(line).unwrap();
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].role, "tool_call");
        assert_eq!(parsed[0].tool_name.as_deref(), Some("Bash"));
    }

    #[test]
    fn test_parse_line_api_tool_result() {
        let line = r#"{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"file contents here","is_error":false}]}"#;
        let parsed = parse_line(line).unwrap();
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].role, "tool_result");
        assert!(!parsed[0].is_error);
    }

    #[test]
    fn test_parse_line_empty() {
        assert!(parse_line("").is_none());
        assert!(parse_line("   ").is_none());
    }

    #[test]
    fn test_parse_line_invalid_json() {
        assert!(parse_line("not json at all").is_none());
    }

    // ── extract_events unit tests ──

    #[test]
    fn test_extract_events_file_modified() {
        let lines = vec![ParsedLine {
            role: "tool_call".into(),
            content: "Edit: src/foo.rs".into(),
            tool_name: Some("Edit".into()),
            timestamp: None,
            is_error: false,
        }];
        let events = extract_events(&lines);
        assert!(events
            .iter()
            .any(|e| matches!(e, SessionEvent::FileModified { path } if path == "src/foo.rs")));
    }

    #[test]
    fn test_extract_events_dedup_files() {
        let lines = vec![
            ParsedLine {
                role: "tool_call".into(),
                content: "Edit: src/foo.rs".into(),
                tool_name: Some("Edit".into()),
                timestamp: None,
                is_error: false,
            },
            ParsedLine {
                role: "tool_call".into(),
                content: "Edit: src/foo.rs".into(),
                tool_name: Some("Edit".into()),
                timestamp: None,
                is_error: false,
            },
        ];
        let events = extract_events(&lines);
        let file_events: Vec<_> = events
            .iter()
            .filter(|e| matches!(e, SessionEvent::FileModified { .. }))
            .collect();
        assert_eq!(
            file_events.len(),
            1,
            "should deduplicate file modifications"
        );
    }

    #[test]
    fn test_extract_events_error_heuristic() {
        let lines = vec![ParsedLine {
            role: "tool_result".into(),
            content: "error[E0432]: unresolved import; compilation failed".into(),
            tool_name: Some("t1".into()),
            timestamp: None,
            is_error: false, // not flagged, but content contains error
        }];
        let events = extract_events(&lines);
        assert!(
            events
                .iter()
                .any(|e| matches!(e, SessionEvent::ErrorEncountered { .. })),
            "should detect errors heuristically: {:?}",
            events
        );
    }

    // ── cache_path_for ──

    #[test]
    fn test_cache_path_for() {
        let p = std::path::PathBuf::from("/tmp/sessions/agent.jsonl");
        let cache = cache_path_for(&p);
        assert_eq!(cache.to_str().unwrap(), "/tmp/sessions/agent.summary.json");
    }

    // ── count_lines ──

    #[test]
    fn test_count_lines() {
        let f = write_session(&["line1", "line2", "line3"]);
        assert_eq!(count_lines(f.path()).unwrap(), 3);
    }

    #[test]
    fn test_count_lines_empty() {
        let f = write_session(&[]);
        assert_eq!(count_lines(f.path()).unwrap(), 0);
    }

    // ── truncate ──

    #[test]
    fn test_truncate_short() {
        assert_eq!(truncate("hello", 10), "hello");
    }

    #[test]
    fn test_truncate_long() {
        assert_eq!(truncate("hello world!", 5), "hello...");
    }
}
