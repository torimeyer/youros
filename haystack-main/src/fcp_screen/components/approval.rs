//! Tool approval modal — TUI overlay for governed permission mode (→751).
//!
//! When an agent requests a tool call in governed mode, the backend sends an
//! `ApprovalRequest` through an `mpsc` channel. This module builds the styled
//! overlay content. Canonical types live in `crate::cpu::approval`.

pub use crate::cpu::approval::{ApprovalRequest, ApprovalResponse};
use crate::fcp_screen::protocol::{self, Color};
use ratatui::text::Line;

// ---------------------------------------------------------------------------
// Overlay builder
// ---------------------------------------------------------------------------

/// Build the styled lines for the approval modal body.
///
/// The first line is the modal title (extracted by `ShowOverlay` dispatch).
/// Remaining lines are the body content rendered inside the modal box.
///
/// Layout:
/// ```text
///   Permission Required        <- title (extracted by ShowOverlay)
///   agent-3 requests permission
///
///   Tool: shell
///   Input: cargo test --lib...
///
///   [y] allow  [n] deny  [a] always allow
/// ```
pub fn build_approval_overlay(agent: &str, tool: &str, preview: &str) -> Vec<Line<'static>> {
    // →1014: Classify tool call to show capability class + scoped "A" prompt.
    let input_json: serde_json::Value = serde_json::from_str(preview)
        .unwrap_or(serde_json::Value::Null);
    let cap_class = crate::cpu::detect_capability_class(tool, &input_json);
    let cap_label = cap_class.label();

    // Build scoped "always allow" text from class + first arg pattern.
    let scope_hint = build_scope_hint(cap_label, tool, &input_json);

    let mut lines = vec![
        // Title line (ShowOverlay extracts this as the modal title)
        protocol::line_styled("  Permission Required  ", Color::Yellow),
        // Agent identity
        protocol::line_from_spans(vec![
            protocol::bold(agent),
            protocol::plain(" requests permission"),
        ]),
        // Blank separator
        protocol::line_plain(""),
        // Tool name + capability class
        protocol::line_from_spans(vec![
            protocol::dim("Tool: "),
            protocol::colored(tool, Color::Cyan),
            protocol::dim(format!(" ({})", cap_label)),
        ]),
    ];

    // Parse tool input as JSON → human-readable key: value pairs.
    // Falls back to truncated raw string if not valid JSON.
    lines.extend(format_tool_input(preview));

    // Blank separator + action keys with scoped "A" prompt
    lines.push(protocol::line_plain(""));
    lines.push(protocol::line_from_spans(vec![
        protocol::colored("[y]", Color::Green),
        protocol::plain(" allow  "),
        protocol::colored("[n]", Color::Red),
        protocol::plain(" deny  "),
        protocol::colored("[a]", Color::Yellow),
        protocol::dim(format!(" always allow {}", scope_hint)),
    ]));

    lines
}

/// →1014: Build a human-readable scope hint for the "always allow" action.
/// e.g. "shell:read(git *)" or "file:edit" or "shell:exec(cargo *)"
fn build_scope_hint(cap_label: &str, _tool: &str, input: &serde_json::Value) -> String {
    // Extract first meaningful arg for pattern display
    let first_arg = if let Some(cmd) = input.get("command").or_else(|| input.get("cmd")).and_then(|v| v.as_str()) {
        // Shell: first word of command
        cmd.split_whitespace().next().unwrap_or("")
    } else if let Some(path) = input.get("file_path").and_then(|v| v.as_str()) {
        // File tools: directory portion
        std::path::Path::new(path)
            .parent()
            .and_then(|p| p.file_name())
            .and_then(|n| n.to_str())
            .unwrap_or("")
    } else {
        ""
    };

    if first_arg.is_empty() {
        cap_label.to_string()
    } else {
        format!("{}({} *)", cap_label, first_arg)
    }
}

/// Format tool input preview as human-readable key: value lines.
fn format_tool_input(preview: &str) -> Vec<Line<'static>> {
    // Try to parse as JSON object
    if let Ok(serde_json::Value::Object(map)) = serde_json::from_str::<serde_json::Value>(preview) {
        let mut lines = Vec::new();
        for (key, val) in &map {
            let display_val = match val {
                serde_json::Value::String(s) => shorten_value(key, s),
                other => {
                    let s = other.to_string();
                    truncate_str(&s, 60)
                }
            };
            lines.push(protocol::line_from_spans(vec![
                protocol::dim(format!("  {key}: ")),
                protocol::plain(display_val),
            ]));
        }
        if lines.is_empty() {
            lines.push(protocol::line_from_spans(vec![
                protocol::dim("Input: "),
                protocol::plain("(empty)"),
            ]));
        }
        lines
    } else {
        // Not JSON — show truncated raw string
        vec![protocol::line_from_spans(vec![
            protocol::dim("Input: "),
            protocol::plain(truncate_str(preview, 60)),
        ])]
    }
}

/// Shorten a string value for display. Path-like keys get project root stripped.
fn shorten_value(key: &str, value: &str) -> String {
    let shortened = if key.contains("path") || key.contains("file") {
        shorten_path(value)
    } else {
        value.to_string()
    };
    truncate_str(&shortened, 60)
}

/// Strip common project root prefixes from file paths.
fn shorten_path(path: &str) -> String {
    // Strip /Users/*/projects/*/ prefix
    if let Some(idx) = path.find("/projects/") {
        let after = &path[idx + "/projects/".len()..];
        // Keep "project/relative/path"
        after.to_string()
    } else if let Some(idx) = path.find("/home/") {
        let after = &path[idx..];
        format!("~{}", &after[after.find('/').map(|i| i + after[1..].find('/').unwrap_or(0) + 1).unwrap_or(0)..])
    } else {
        path.to_string()
    }
}

/// Truncate a string to max chars, appending "..." if truncated.
fn truncate_str(s: &str, max: usize) -> String {
    if s.chars().count() > max {
        let truncated: String = s.chars().take(max).collect();
        format!("{truncated}...")
    } else {
        s.to_string()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_approval_overlay_basic_structure() {
        let lines = build_approval_overlay("agent-3", "shell", "cargo test --lib");
        // At minimum: title, agent, blank, tool, input, blank, actions
        assert!(lines.len() >= 7, "expected at least 7 lines, got {}", lines.len());
    }

    #[test]
    fn build_approval_overlay_title_line() {
        let lines = build_approval_overlay("agent-1", "Bash", "ls -la");
        let title_text: String = lines[0].spans.iter().map(|s| s.content.to_string()).collect();
        assert!(
            title_text.contains("Permission Required"),
            "title should contain 'Permission Required', got: {title_text}"
        );
    }

    #[test]
    fn build_approval_overlay_contains_agent_name() {
        let lines = build_approval_overlay("worker-5", "Read", "/etc/hosts");
        let agent_text: String = lines[1].spans.iter().map(|s| s.content.to_string()).collect();
        assert!(
            agent_text.contains("worker-5"),
            "should contain agent name: {agent_text}"
        );
        assert!(
            agent_text.contains("requests permission"),
            "should contain 'requests permission': {agent_text}"
        );
    }

    #[test]
    fn build_approval_overlay_contains_tool_name() {
        let lines = build_approval_overlay("agent-1", "shell", "echo hi");
        let tool_text: String = lines[3].spans.iter().map(|s| s.content.to_string()).collect();
        assert!(
            tool_text.contains("Tool:"),
            "should contain 'Tool:': {tool_text}"
        );
        assert!(
            tool_text.contains("shell"),
            "should contain tool name: {tool_text}"
        );
    }

    #[test]
    fn build_approval_overlay_contains_input_preview() {
        let lines = build_approval_overlay("agent-1", "Bash", "cargo test --lib");
        let all_text: String = lines.iter().flat_map(|l| l.spans.iter().map(|s| s.content.to_string())).collect();
        assert!(
            all_text.contains("Input:") || all_text.contains("cargo test"),
            "should contain input preview: {all_text}"
        );
    }

    #[test]
    fn build_approval_overlay_contains_action_keys() {
        let lines = build_approval_overlay("agent-1", "shell", "ls");
        let actions_text: String = lines.last().unwrap().spans.iter().map(|s| s.content.to_string()).collect();
        assert!(actions_text.contains("[y]"), "should contain [y]: {actions_text}");
        assert!(actions_text.contains("[n]"), "should contain [n]: {actions_text}");
        assert!(actions_text.contains("[a]"), "should contain [a]: {actions_text}");
        assert!(actions_text.contains("allow"), "should contain 'allow': {actions_text}");
        assert!(actions_text.contains("deny"), "should contain 'deny': {actions_text}");
        assert!(actions_text.contains("always"), "should contain 'always': {actions_text}");
    }

    #[test]
    fn build_approval_overlay_action_colors() {
        let lines = build_approval_overlay("agent-1", "shell", "ls");
        let action_spans = &lines.last().unwrap().spans;
        // [y] should be green
        assert_eq!(action_spans[0].style.fg, Some(Color::Green), "[y] should be green");
        // [n] should be red
        assert_eq!(action_spans[2].style.fg, Some(Color::Red), "[n] should be red");
        // [a] should be yellow
        assert_eq!(action_spans[4].style.fg, Some(Color::Yellow), "[a] should be yellow");
    }

    #[test]
    fn build_approval_overlay_long_preview_truncated() {
        let long_input = "a".repeat(100);
        let lines = build_approval_overlay("agent-1", "Bash", &long_input);
        let all_text: String = lines.iter().flat_map(|l| l.spans.iter().map(|s| s.content.to_string())).collect();
        assert!(all_text.contains("..."), "long preview should be truncated: {all_text}");
    }

    #[test]
    fn build_approval_overlay_short_preview_not_truncated() {
        let lines = build_approval_overlay("agent-1", "Read", "/tmp/file.txt");
        let all_text: String = lines.iter().flat_map(|l| l.spans.iter().map(|s| s.content.to_string())).collect();
        assert!(all_text.contains("/tmp/file.txt"), "short preview should be present: {all_text}");
    }

    #[test]
    fn build_approval_overlay_empty_preview() {
        let lines = build_approval_overlay("agent-1", "Bash", "");
        let all_text: String = lines.iter().flat_map(|l| l.spans.iter().map(|s| s.content.to_string())).collect();
        assert!(all_text.contains("Input:"), "should show Input label: {all_text}");
    }

    #[test]
    fn build_approval_overlay_json_input_parsed() {
        let json = r#"{"file_path":"/Users/scott/projects/ostk/src/main.rs","old_string":"foo","new_string":"bar"}"#;
        let lines = build_approval_overlay("agent-1", "Edit", json);
        let all_text: String = lines.iter().flat_map(|l| l.spans.iter().map(|s| s.content.to_string())).collect();
        // Should show parsed key-value, not raw JSON braces
        assert!(!all_text.contains("{\"file_path\""), "should NOT show raw JSON: {all_text}");
        assert!(all_text.contains("file_path:"), "should show key: {all_text}");
        // Path should be shortened
        assert!(all_text.contains("ostk/src/main.rs"), "should shorten path: {all_text}");
    }

    #[test]
    fn shorten_path_strips_projects_prefix() {
        assert_eq!(shorten_path("/Users/scott/projects/ostk/src/main.rs"), "ostk/src/main.rs");
    }

    #[test]
    fn shorten_path_no_prefix_unchanged() {
        assert_eq!(shorten_path("src/main.rs"), "src/main.rs");
    }

    #[test]
    fn truncate_str_short_unchanged() {
        assert_eq!(truncate_str("hello", 10), "hello");
    }

    #[test]
    fn truncate_str_long_truncated() {
        let long = "a".repeat(100);
        let result = truncate_str(&long, 10);
        assert_eq!(result.chars().count(), 13); // 10 + "..."
        assert!(result.ends_with("..."));
    }

    #[test]
    fn approval_response_variants() {
        // Verify all variants construct and compare correctly.
        assert_eq!(ApprovalResponse::Allow, ApprovalResponse::Allow);
        assert_eq!(ApprovalResponse::Deny, ApprovalResponse::Deny);
        assert_eq!(ApprovalResponse::AlwaysAllow, ApprovalResponse::AlwaysAllow);
        assert_ne!(ApprovalResponse::Allow, ApprovalResponse::Deny);
        assert_ne!(ApprovalResponse::Allow, ApprovalResponse::AlwaysAllow);
        assert_ne!(ApprovalResponse::Deny, ApprovalResponse::AlwaysAllow);
    }

    #[test]
    fn approval_response_debug_format() {
        let _ = format!("{:?}", ApprovalResponse::Allow);
        let _ = format!("{:?}", ApprovalResponse::Deny);
        let _ = format!("{:?}", ApprovalResponse::AlwaysAllow);
    }

    #[test]
    fn test_edit_approval_shows_parsed_json() {
        // Simulate Edit tool input with long paths — must NOT show raw JSON
        let json = r#"{"file_path":"/Users/scott/projects/ostk/tests/tui_pty_integration.rs","old_string":"some old text that is fairly long to test truncation behavior","new_string":"some new text that replaces the old one"}"#;
        let lines = build_approval_overlay("agent-1", "Edit", json);
        let all_text: String = lines.iter()
            .flat_map(|l| l.spans.iter().map(|s| s.content.to_string()))
            .collect();
        // Must show parsed key: value, not raw JSON braces
        assert!(
            !all_text.contains("{\"file_path\""),
            "should NOT show raw JSON, got: {all_text}"
        );
        assert!(all_text.contains("file_path:"), "should show parsed key 'file_path:': {all_text}");
        assert!(all_text.contains("old_string:"), "should show parsed key 'old_string:': {all_text}");
        assert!(all_text.contains("new_string:"), "should show parsed key 'new_string:': {all_text}");
    }

    // →1014: Scoped "A" prompt tests

    #[test]
    fn approval_shows_capability_class() {
        let json = r#"{"command":"git status"}"#;
        let lines = build_approval_overlay("agent-1", "Bash", json);
        let all_text: String = lines.iter().flat_map(|l| l.spans.iter().map(|s| s.content.to_string())).collect();
        assert!(all_text.contains("shell:read"), "should show capability class: {all_text}");
    }

    #[test]
    fn approval_scoped_always_allow_shell() {
        let json = r#"{"command":"cargo test --lib"}"#;
        let lines = build_approval_overlay("agent-1", "Bash", json);
        let action_text: String = lines.last().unwrap().spans.iter().map(|s| s.content.to_string()).collect();
        assert!(action_text.contains("always allow"), "should contain 'always allow': {action_text}");
        assert!(action_text.contains("cargo"), "should show first arg in scope: {action_text}");
    }

    #[test]
    fn approval_scoped_always_allow_file() {
        let json = r#"{"file_path":"/Users/scott/projects/ostk/src/main.rs"}"#;
        let lines = build_approval_overlay("agent-1", "Edit", json);
        let action_text: String = lines.last().unwrap().spans.iter().map(|s| s.content.to_string()).collect();
        assert!(action_text.contains("file:edit"), "should show file:edit class: {action_text}");
        assert!(action_text.contains("src"), "should show directory in scope: {action_text}");
    }

    #[test]
    fn scope_hint_empty_input() {
        let hint = build_scope_hint("shell:exec", "Bash", &serde_json::Value::Null);
        assert_eq!(hint, "shell:exec");
    }

    #[test]
    fn scope_hint_with_command() {
        let input = serde_json::json!({"command": "git log --oneline"});
        let hint = build_scope_hint("shell:read", "Bash", &input);
        assert_eq!(hint, "shell:read(git *)");
    }
}
