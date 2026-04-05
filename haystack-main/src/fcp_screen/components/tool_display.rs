//! Tool result display — formats tool output into styled lines for the chat zone.

use crate::fcp_screen::protocol::{self, Color};
use ratatui::text::Line;

/// Formatted tool result ready for display.
pub struct ToolDisplay {
    /// Icon + tool name + first line of output.
    pub prefix_line: Line<'static>,
    /// Remaining output lines (indented continuation).
    pub detail_lines: Vec<Line<'static>>,
}

/// Format a tool result into styled display lines.
///
/// Handles shell compression (via `compress_with_cmd`), truncation for
/// non-shell tools, icon selection (check/cross/denied), and color coding.
pub fn format_tool_result(name: &str, output: &str, success: bool) -> ToolDisplay {
    // Strip internal metadata tags before display
    let clean_output = output.replace("[passthrough]", "").trim().to_string();

    // Build display text: shell tools get compressed, others get truncated
    let display = if name == "shell" || name == "sh_run" || name == "Bash" || name == "bash" {
        let (compressed, _stats) = crate::squasher::compress_with_cmd(
            &clean_output, None, if success { 0 } else { 1 }, "", 0,
        );
        if compressed.lines().count() > 15 {
            let preview: String = compressed.lines().take(10)
                .collect::<Vec<_>>().join("\n");
            let remaining = compressed.lines().count() - 10;
            format!("{}\n  [+{} more lines]", preview, remaining)
        } else {
            compressed
        }
    } else {
        // Non-shell tools: truncate long lines and cap total line count
        let max_chars: usize = 120; // reasonable default, avoids mismatch with Screen cache
        if clean_output.len() > max_chars {
            let truncated: String = clean_output.chars().take(max_chars.saturating_sub(3)).collect();
            format!("{truncated}...")
        } else {
            clean_output.clone()
        }
    };

    // For empty output, show "ok" or "failed" instead of blank
    let first_line = if display.trim().is_empty() {
        if success { "ok".to_string() } else { "failed".to_string() }
    } else {
        display.lines().next().unwrap_or("").to_string()
    };

    let is_denied = !success && display.contains("denied");
    let (tool_color, icon) = if is_denied {
        (Color::Yellow, "[\u{2298}]") // ⊘ denied
    } else if success {
        (protocol::DARK_GREEN, "[\u{2713}]") // ✓ success
    } else {
        (protocol::DARK_RED, "[\u{2717}]") // ✗ error
    };

    let prefix_line = protocol::line_from_spans(vec![
        protocol::dim("  "),
        protocol::dim_colored(icon, tool_color),
        protocol::dim(format!(" {} {}", name, first_line)),
    ]);

    // Indent detail lines to align with the text after "  [✓] {name} "
    let indent_width = 2 + 3 + 1 + name.len() + 1; // "  " + icon + " " + name + " "
    let indent: String = " ".repeat(indent_width);
    let mut detail_lines: Vec<Line<'static>> = display.lines().skip(1)
        .map(|line| protocol::line_from_spans(vec![
            protocol::dim(format!("{indent}{line}")),
        ]))
        .collect();

    // Truncate detail lines if too many (applies to ALL tool types)
    const MAX_TOOL_OUTPUT_LINES: usize = 10;
    if detail_lines.len() > MAX_TOOL_OUTPUT_LINES {
        let omitted = detail_lines.len() - MAX_TOOL_OUTPUT_LINES;
        detail_lines.truncate(MAX_TOOL_OUTPUT_LINES);
        detail_lines.push(protocol::line_from_spans(vec![
            protocol::dim(format!("{indent}[+{} more lines]", omitted)),
        ]));
    }

    ToolDisplay { prefix_line, detail_lines }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn success_shows_check_icon_and_green() {
        let td = format_tool_result("Read", "file contents here", true);
        // prefix_line should have 3 spans: indent, icon, name+text
        assert_eq!(td.prefix_line.spans.len(), 3);
        let icon_span = &td.prefix_line.spans[1];
        assert!(icon_span.content.contains('\u{2713}'), "expected check icon");
        assert_eq!(icon_span.style.fg, Some(protocol::DARK_GREEN));
        assert!(td.detail_lines.is_empty());
    }

    #[test]
    fn error_shows_cross_icon_and_red() {
        let td = format_tool_result("Bash", "command not found", false);
        let icon_span = &td.prefix_line.spans[1];
        assert!(icon_span.content.contains('\u{2717}'), "expected cross icon");
        assert_eq!(icon_span.style.fg, Some(protocol::DARK_RED));
    }

    #[test]
    fn denied_shows_prohibited_icon_and_yellow() {
        let td = format_tool_result("Write", "permission denied by user", false);
        let icon_span = &td.prefix_line.spans[1];
        assert!(icon_span.content.contains('\u{2298}'), "expected denied icon");
        assert_eq!(icon_span.style.fg, Some(Color::Yellow));
    }

    #[test]
    fn empty_success_output_shows_ok() {
        let td = format_tool_result("Read", "", true);
        let text_span = &td.prefix_line.spans[2];
        assert!(text_span.content.contains("ok"), "expected 'ok' for empty success");
    }

    #[test]
    fn empty_error_output_shows_failed() {
        let td = format_tool_result("Write", "  ", false);
        let text_span = &td.prefix_line.spans[2];
        assert!(text_span.content.contains("failed"), "expected 'failed' for empty error");
    }

    #[test]
    fn multiline_output_produces_detail_lines() {
        let td = format_tool_result("Read", "line1\nline2\nline3", true);
        assert_eq!(td.detail_lines.len(), 2); // skip(1) → 2 remaining
        assert!(td.detail_lines[0].spans[0].content.contains("line2"));
        assert!(td.detail_lines[1].spans[0].content.contains("line3"));
    }

    #[test]
    fn passthrough_tag_stripped() {
        let td = format_tool_result("Read", "[passthrough] actual content", true);
        let text_span = &td.prefix_line.spans[2];
        assert!(!text_span.content.contains("[passthrough]"));
        assert!(text_span.content.contains("actual content"));
    }
}
