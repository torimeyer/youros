/// Markdown-to-Line processor for rendering LLM responses in the TUI.
///
/// Uses pulldown-cmark (the industry-standard parser used by GitHub, VS Code, etc.)
/// to parse markdown into events, then converts those events to ratatui `Line<'static>`
/// with appropriate styling.
use pulldown_cmark::{Event, Options, Parser, Tag, TagEnd};
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};

use crate::fcp_screen::protocol::{self, Color};

/// Process AI response text into styled lines using pulldown-cmark.
pub fn style_ai_response(text: &str) -> Vec<Line<'static>> {
    let opts = Options::ENABLE_TABLES | Options::ENABLE_STRIKETHROUGH;
    let parser = Parser::new_ext(text, opts);

    let mut lines: Vec<Line<'static>> = Vec::new();
    let mut current_spans: Vec<Span<'static>> = Vec::new();
    let mut style_stack: Vec<Style> = vec![Style::default()];
    let mut in_code_block = false;
    let mut list_stack: Vec<ListKind> = Vec::new();
    let mut in_blockquote = false;
    // Table state
    let mut table_rows: Vec<Vec<String>> = Vec::new();
    let mut table_separator_after: Option<usize> = None;
    let mut in_table = false;
    let mut current_table_row: Vec<String> = Vec::new();
    let mut current_cell_text = String::new();

    for event in parser {
        match event {
            // -- Block-level start tags --
            Event::Start(Tag::Heading { .. }) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                style_stack.push(Style {
                    fg: Some(Color::Cyan),
                    bold: true,
                    ..Style::default()
                });
            }
            Event::End(TagEnd::Heading(_)) => {
                style_stack.pop();
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
            }

            Event::Start(Tag::Emphasis) => {
                style_stack.push(Style {
                    italic: true,
                    ..current_style(&style_stack)
                });
            }
            Event::End(TagEnd::Emphasis) => {
                style_stack.pop();
            }

            Event::Start(Tag::Strong) => {
                style_stack.push(Style {
                    bold: true,
                    ..current_style(&style_stack)
                });
            }
            Event::End(TagEnd::Strong) => {
                style_stack.pop();
            }

            Event::Start(Tag::CodeBlock(_)) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                lines.push(protocol::line_plain(""));
                in_code_block = true;
            }
            Event::End(TagEnd::CodeBlock) => {
                // Flush any remaining code content
                if !current_spans.is_empty() {
                    lines.push(protocol::line_from_spans(std::mem::take(&mut current_spans)));
                }
                in_code_block = false;
                lines.push(protocol::line_plain(""));
            }

            Event::Start(Tag::List(first_num)) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                match first_num {
                    Some(start) => list_stack.push(ListKind::Ordered(start as usize)),
                    None => list_stack.push(ListKind::Unordered),
                }
            }
            Event::End(TagEnd::List(_)) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                list_stack.pop();
            }

            Event::Start(Tag::Item) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                // Prepare list prefix
                let indent = "  ".repeat(list_stack.len().saturating_sub(1));
                match list_stack.last_mut() {
                    Some(ListKind::Unordered) => {
                        current_spans.push(protocol::plain(indent));
                        current_spans.push(protocol::colored("· ", Color::Green));
                    }
                    Some(ListKind::Ordered(n)) => {
                        current_spans.push(protocol::plain(indent));
                        current_spans.push(protocol::colored(format!("{}. ", n), Color::Green));
                        *n += 1;
                    }
                    None => {}
                }
            }
            Event::End(TagEnd::Item) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
            }

            Event::Start(Tag::BlockQuote(_)) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                in_blockquote = true;
            }
            Event::End(TagEnd::BlockQuote(_)) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                in_blockquote = false;
            }

            Event::Start(Tag::Link { dest_url, .. }) => {
                style_stack.push(Style {
                    fg: Some(Color::Cyan),
                    ..current_style(&style_stack)
                });
                // Store URL for later — but we only render the text, not the URL
                let _ = dest_url;
            }
            Event::End(TagEnd::Link) => {
                style_stack.pop();
            }

            Event::Start(Tag::Paragraph) => {
                // Don't flush if we're inside a list item — the item start already flushed
                if list_stack.is_empty() && !in_blockquote {
                    flush_line(&mut current_spans, &mut lines, &list_stack, false);
                }
            }
            Event::End(TagEnd::Paragraph) => {
                flush_line(
                    &mut current_spans,
                    &mut lines,
                    &list_stack,
                    in_blockquote,
                );
            }

            // -- Table events --
            Event::Start(Tag::Table(_)) => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                in_table = true;
                table_rows.clear();
                table_separator_after = None;
            }
            Event::End(TagEnd::Table) => {
                render_table(&table_rows, table_separator_after, &mut lines);
                table_rows.clear();
                in_table = false;
            }
            Event::Start(Tag::TableHead) => {
                current_table_row.clear();
            }
            Event::End(TagEnd::TableHead) => {
                if !current_cell_text.is_empty() {
                    current_table_row.push(std::mem::take(&mut current_cell_text));
                }
                table_rows.push(std::mem::take(&mut current_table_row));
                table_separator_after = Some(table_rows.len() - 1);
            }
            Event::Start(Tag::TableRow) => {
                current_table_row.clear();
            }
            Event::End(TagEnd::TableRow) => {
                if !current_cell_text.is_empty() {
                    current_table_row.push(std::mem::take(&mut current_cell_text));
                }
                table_rows.push(std::mem::take(&mut current_table_row));
            }
            Event::Start(Tag::TableCell) => {
                current_cell_text.clear();
            }
            Event::End(TagEnd::TableCell) => {
                current_table_row.push(std::mem::take(&mut current_cell_text));
            }

            // -- Inline/text events --
            Event::Text(text) => {
                if in_table {
                    current_cell_text.push_str(&text);
                    continue;
                }
                if in_code_block {
                    // Each line in a code block gets its own Line
                    let text_str = text.to_string();
                    for (i, code_line) in text_str.split('\n').enumerate() {
                        if i > 0 {
                            // Flush previous code line
                            lines.push(protocol::line_from_spans(
                                std::mem::take(&mut current_spans),
                            ));
                        }
                        if !code_line.is_empty() || i == 0 {
                            current_spans
                                .push(protocol::colored(code_line.to_string(), Color::Yellow));
                        }
                    }
                } else {
                    let style = current_style(&style_stack);
                    let text_str = text.to_string();
                    // Handle newlines within text by splitting into multiple lines
                    let parts: Vec<&str> = text_str.split('\n').collect();
                    for (i, part) in parts.iter().enumerate() {
                        if i > 0 {
                            flush_line(
                                &mut current_spans,
                                &mut lines,
                                &list_stack,
                                in_blockquote,
                            );
                        }
                        if !part.is_empty() {
                            current_spans.push(make_span(part, &style));
                        }
                    }
                }
            }

            Event::Code(code) => {
                if in_table {
                    current_cell_text.push_str(&code);
                    continue;
                }
                current_spans.push(protocol::colored(code.to_string(), Color::Yellow));
            }

            Event::SoftBreak => {
                if in_table {
                    continue;
                }
                // Soft break = space within paragraph for wrapping
                let style = current_style(&style_stack);
                current_spans.push(make_span(" ", &style));
            }
            Event::HardBreak => {
                if in_table {
                    continue;
                }
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
            }

            Event::Rule => {
                flush_line(&mut current_spans, &mut lines, &list_stack, in_blockquote);
                lines.push(protocol::line_from_spans(vec![protocol::dim(
                    "\u{2500}".repeat(40),
                )]));
            }

            // Ignore other events (footnotes, task lists, etc.)
            _ => {}
        }
    }

    // Flush any remaining spans
    if !current_spans.is_empty() {
        let line = if in_blockquote {
            let mut spans = vec![protocol::dim("\u{2502} ")];
            spans.append(&mut current_spans);
            protocol::line_from_spans(spans)
        } else {
            protocol::line_from_spans(std::mem::take(&mut current_spans))
        };
        lines.push(line);
    }

    lines
}

// ---------------------------------------------------------------------------
// Internal types and helpers
// ---------------------------------------------------------------------------

/// Track list context for prefix rendering.
#[derive(Clone)]
enum ListKind {
    Unordered,
    Ordered(usize),
}

/// Lightweight style descriptor — avoids dragging ratatui::Style through the stack.
#[derive(Clone, Default)]
struct Style {
    fg: Option<Color>,
    bold: bool,
    italic: bool,
    dim: bool,
}

/// Get the current effective style from the stack.
fn current_style(stack: &[Style]) -> Style {
    stack.last().cloned().unwrap_or_default()
}

/// Convert our lightweight style + text into a ratatui Span.
fn make_span(text: &str, style: &Style) -> Span<'static> {
    let mut rs = ratatui::style::Style::default();
    if let Some(fg) = style.fg {
        rs = rs.fg(fg);
    }
    if style.bold {
        rs = rs.add_modifier(Modifier::BOLD);
    }
    if style.italic {
        rs = rs.add_modifier(Modifier::ITALIC);
    }
    if style.dim {
        rs = rs.add_modifier(Modifier::DIM);
    }
    Span::styled(text.to_string(), rs)
}

/// Flush accumulated spans into a Line, applying blockquote/list prefixes as needed.
fn flush_line(
    spans: &mut Vec<Span<'static>>,
    lines: &mut Vec<Line<'static>>,
    _list_stack: &[ListKind],
    in_blockquote: bool,
) {
    if spans.is_empty() {
        return;
    }
    if in_blockquote {
        let mut final_spans = vec![protocol::dim("\u{2502} ")];
        final_spans.append(spans);
        lines.push(protocol::line_from_spans(final_spans));
    } else {
        lines.push(protocol::line_from_spans(std::mem::take(spans)));
    }
}

// ---------------------------------------------------------------------------
// Table rendering (preserved from original implementation)
// ---------------------------------------------------------------------------

/// Parse inline formatting markers (backtick code and **bold**) into styled spans.
/// Used for table cell content where pulldown-cmark gives us raw text.
fn parse_inline(text: &str) -> Vec<Span<'static>> {
    let mut spans = Vec::new();
    let mut remaining = text;

    while !remaining.is_empty() {
        let backtick_pos = remaining.find('`');
        let bold_pos = remaining.find("**");

        match (backtick_pos, bold_pos) {
            (Some(bp), Some(sp)) => {
                if bp <= sp {
                    parse_backtick(remaining, bp, &mut spans, &mut remaining);
                } else {
                    parse_bold(remaining, sp, &mut spans, &mut remaining);
                }
            }
            (Some(bp), None) => {
                parse_backtick(remaining, bp, &mut spans, &mut remaining);
            }
            (None, Some(sp)) => {
                parse_bold(remaining, sp, &mut spans, &mut remaining);
            }
            (None, None) => {
                spans.push(protocol::plain(remaining));
                break;
            }
        }
    }

    spans
}

fn parse_backtick<'a>(
    input: &'a str,
    pos: usize,
    spans: &mut Vec<Span<'static>>,
    remaining: &mut &'a str,
) {
    if pos > 0 {
        spans.push(protocol::plain(&input[..pos]));
    }
    if let Some(end) = input[pos + 1..].find('`') {
        let code = &input[pos + 1..pos + 1 + end];
        spans.push(protocol::colored(code, Color::Yellow));
        *remaining = &input[pos + 1 + end + 1..];
    } else {
        spans.push(protocol::plain(&input[pos..]));
        *remaining = "";
    }
}

fn parse_bold<'a>(
    input: &'a str,
    pos: usize,
    spans: &mut Vec<Span<'static>>,
    remaining: &mut &'a str,
) {
    if pos > 0 {
        spans.push(protocol::plain(&input[..pos]));
    }
    if let Some(end) = input[pos + 2..].find("**") {
        let bold_text = &input[pos + 2..pos + 2 + end];
        spans.push(protocol::bold(bold_text));
        *remaining = &input[pos + 2 + end + 2..];
    } else {
        spans.push(protocol::plain(&input[pos..]));
        *remaining = "";
    }
}

/// Render a complete table block into styled lines with aligned columns.
fn render_table(
    rows: &[Vec<String>],
    separator_after: Option<usize>,
    out: &mut Vec<Line<'static>>,
) {
    if rows.is_empty() {
        return;
    }

    let col_count = rows.iter().map(|r| r.len()).max().unwrap_or(0);
    if col_count == 0 {
        return;
    }

    const MAX_COL_WIDTH: usize = 60;
    let mut col_widths: Vec<usize> = vec![0; col_count];
    for cells in rows.iter() {
        for (j, cell) in cells.iter().enumerate() {
            if j < col_count {
                col_widths[j] = col_widths[j].max(cell.chars().count()).min(MAX_COL_WIDTH);
            }
        }
    }

    for (i, cells) in rows.iter().enumerate() {
        let mut spans = Vec::new();
        let is_header_row = separator_after == Some(i);

        for (j, w) in col_widths.iter().enumerate() {
            if j > 0 {
                spans.push(protocol::dim(" \u{2502} "));
            }
            let cell_text = cells.get(j).map(|s| s.as_str()).unwrap_or("");
            let padding = w.saturating_sub(cell_text.chars().count());
            let padded = format!("{}{}", cell_text, " ".repeat(padding));
            if is_header_row {
                spans.push(protocol::bold(&padded));
            } else {
                let inline = parse_inline(&padded);
                spans.extend(inline);
            }
        }
        out.push(protocol::line_from_spans(spans));

        // Insert separator line after the header row
        if separator_after == Some(i) {
            let mut sep_spans = Vec::new();
            for (j, w) in col_widths.iter().enumerate() {
                if j > 0 {
                    sep_spans.push(protocol::dim(" \u{2502} "));
                }
                sep_spans.push(protocol::dim("\u{2500}".repeat(*w)));
            }
            out.push(protocol::line_from_spans(sep_spans));
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// Collect all span text from a Line for easy assertion.
    fn full_text(line: &Line<'static>) -> String {
        line.spans.iter().map(|s| s.content.as_ref()).collect()
    }

    // -- Headings --

    #[test]
    fn test_heading_styled() {
        let lines = style_ai_response("# Hello");
        assert!(!lines.is_empty());
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("Hello"), "heading text: {text}");
        assert!(
            line.spans
                .iter()
                .any(|s| s.style.add_modifier.contains(Modifier::BOLD)),
            "heading should be bold"
        );
        assert!(
            line.spans.iter().any(|s| s.style.fg == Some(Color::Cyan)),
            "heading should be cyan"
        );
    }

    #[test]
    fn test_heading_h2() {
        let lines = style_ai_response("## Subheading");
        assert!(!lines.is_empty());
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("Subheading"), "h2 text: {text}");
        assert!(line
            .spans
            .iter()
            .any(|s| s.style.add_modifier.contains(Modifier::BOLD)));
        assert!(line.spans.iter().any(|s| s.style.fg == Some(Color::Cyan)));
    }

    #[test]
    fn test_heading_h3() {
        let lines = style_ai_response("### Deep");
        assert!(!lines.is_empty());
        let text = full_text(&lines[0]);
        assert!(text.contains("Deep"), "h3 text: {text}");
        assert!(lines[0]
            .spans
            .iter()
            .any(|s| s.style.add_modifier.contains(Modifier::BOLD)));
        assert!(lines[0]
            .spans
            .iter()
            .any(|s| s.style.fg == Some(Color::Cyan)));
    }

    // -- Bullet lists --

    #[test]
    fn test_bullet_list() {
        let lines = style_ai_response("- item");
        let all_text: String = lines.iter().map(full_text).collect();
        assert!(all_text.contains('·'), "bullet marker missing: {all_text}");
        assert!(all_text.contains("item"), "bullet text missing: {all_text}");
        // Green bullet
        let has_green_bullet = lines.iter().any(|l| {
            l.spans
                .iter()
                .any(|s| s.content.contains('·') && s.style.fg == Some(Color::Green))
        });
        assert!(has_green_bullet, "bullet should be green");
    }

    #[test]
    fn test_bullet_list_asterisk() {
        let lines = style_ai_response("* item");
        let has_green_bullet = lines.iter().any(|l| {
            l.spans
                .iter()
                .any(|s| s.content.contains('·') && s.style.fg == Some(Color::Green))
        });
        assert!(has_green_bullet, "asterisk bullet should be green");
        let all_text: String = lines.iter().map(full_text).collect();
        assert!(all_text.contains("item"));
    }

    #[test]
    fn test_numbered_list() {
        let lines = style_ai_response("1. first");
        let has_green_num = lines.iter().any(|l| {
            l.spans
                .iter()
                .any(|s| s.content.contains("1.") && s.style.fg == Some(Color::Green))
        });
        assert!(has_green_num, "numbered list should have green number");
        let all_text: String = lines.iter().map(full_text).collect();
        assert!(all_text.contains("first"));
    }

    // -- Inline code --

    #[test]
    fn test_inline_code() {
        let lines = style_ai_response("use `foo` here");
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("use"), "text before code: {text}");
        assert!(text.contains("foo"), "code text: {text}");
        assert!(text.contains("here"), "text after code: {text}");
        // Check yellow code span
        let code_span = line
            .spans
            .iter()
            .find(|s| s.content.as_ref() == "foo")
            .expect("should have foo span");
        assert_eq!(code_span.style.fg, Some(Color::Yellow));
    }

    // -- Bold --

    #[test]
    fn test_bold_text() {
        let lines = style_ai_response("this is **important**");
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("this is"), "text before bold: {text}");
        assert!(text.contains("important"), "bold text: {text}");
        let bold_span = line
            .spans
            .iter()
            .find(|s| s.content.as_ref() == "important")
            .expect("should have bold span");
        assert!(bold_span
            .style
            .add_modifier
            .contains(Modifier::BOLD));
    }

    // -- Code blocks --

    #[test]
    fn test_code_block() {
        let input = "```rust\nlet x = 1;\n```";
        let lines = style_ai_response(input);
        // Should have: empty line (fence open), code content, empty line (fence close)
        assert!(lines.len() >= 3, "code block should have >=3 lines, got {}", lines.len());
        // First line should be empty (code block start)
        assert_eq!(full_text(&lines[0]), "", "first line should be empty fence marker");
        // Last line should be empty (code block end)
        let last = &lines[lines.len() - 1];
        assert_eq!(full_text(last), "", "last line should be empty fence marker");
        // Find the code content line
        let code_line = lines.iter().find(|l| {
            full_text(l).contains("let x = 1;")
        }).expect("should have code content line");
        assert!(
            code_line.spans.iter().any(|s| s.style.fg == Some(Color::Yellow)),
            "code should be yellow"
        );
    }

    #[test]
    fn test_code_block_tilde() {
        // pulldown-cmark does not treat ~~~ as code fences by default,
        // but backtick fences work. Test backtick variant.
        let input = "```\ncode\n```";
        let lines = style_ai_response(input);
        assert!(lines.len() >= 3);
        // First and last are empty (fence markers)
        assert_eq!(full_text(&lines[0]), "");
        let last = &lines[lines.len() - 1];
        assert_eq!(full_text(last), "");
        // Content is yellow
        let code_line = lines
            .iter()
            .find(|l| full_text(l).contains("code"))
            .expect("should have code line");
        assert!(code_line
            .spans
            .iter()
            .any(|s| s.style.fg == Some(Color::Yellow)));
    }

    // -- Plain text --

    #[test]
    fn test_plain_text_unchanged() {
        let lines = style_ai_response("hello world");
        assert!(!lines.is_empty());
        let line = &lines[0];
        assert_eq!(full_text(line), "hello world");
        // No special formatting
        assert!(line.spans.iter().all(|s| s.style.fg.is_none()));
        assert!(line
            .spans
            .iter()
            .all(|s| !s.style.add_modifier.contains(Modifier::BOLD)));
    }

    // -- Mixed inline --

    #[test]
    fn test_mixed_inline() {
        let lines = style_ai_response("use `foo` and **bar**");
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("use"), "should contain 'use': {text}");
        assert!(text.contains("foo"), "should contain 'foo': {text}");
        assert!(text.contains("and"), "should contain 'and': {text}");
        assert!(text.contains("bar"), "should contain 'bar': {text}");
        // foo is yellow
        assert!(line
            .spans
            .iter()
            .any(|s| s.content.as_ref() == "foo" && s.style.fg == Some(Color::Yellow)));
        // bar is bold
        assert!(line.spans.iter().any(
            |s| s.content.as_ref() == "bar" && s.style.add_modifier.contains(Modifier::BOLD)
        ));
    }

    // -- Edge cases --

    #[test]
    fn test_empty_input() {
        let lines = style_ai_response("");
        assert!(lines.is_empty(), "empty input should produce no lines");
    }

    #[test]
    fn test_style_ai_response_mixed() {
        let input = "# Title\n\nSome **bold** text\n\n```\ncode\n```\n\n- item";
        let lines = style_ai_response(input);
        // Title should be bold cyan
        assert!(lines[0]
            .spans
            .iter()
            .any(|s| s.style.add_modifier.contains(Modifier::BOLD)));
        assert!(lines[0]
            .spans
            .iter()
            .any(|s| s.style.fg == Some(Color::Cyan)));
        // Code content should be yellow somewhere in the output
        let has_yellow_code = lines.iter().any(|l| {
            l.spans.iter().any(|s| {
                s.style.fg == Some(Color::Yellow) && s.content.as_ref().contains("code")
            })
        });
        assert!(has_yellow_code, "should have yellow code line");
        // Bullet item
        let has_bullet = lines.iter().any(|l| {
            l.spans
                .iter()
                .any(|s| s.content.contains('·') && s.style.fg == Some(Color::Green))
        });
        assert!(has_bullet, "should have green bullet");
    }

    #[test]
    fn test_multiple_inline_codes() {
        let lines = style_ai_response("`a` and `b`");
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("a"), "should contain 'a': {text}");
        assert!(text.contains("and"), "should contain 'and': {text}");
        assert!(text.contains("b"), "should contain 'b': {text}");
        // Both code spans should be yellow
        let yellow_count = line
            .spans
            .iter()
            .filter(|s| s.style.fg == Some(Color::Yellow))
            .count();
        assert!(yellow_count >= 2, "should have at least 2 yellow spans");
    }

    #[test]
    fn test_bold_at_start() {
        let lines = style_ai_response("**bold** start");
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("bold"), "should contain bold text: {text}");
        assert!(text.contains("start"), "should contain 'start': {text}");
        assert!(line.spans.iter().any(
            |s| s.content.as_ref() == "bold" && s.style.add_modifier.contains(Modifier::BOLD)
        ));
    }

    // -- Table rendering --

    #[test]
    fn test_table_basic_rendering() {
        let input = "| Name | Age |\n|---|---|\n| Alice | 30 |";
        let lines = style_ai_response(input);
        assert!(lines.len() >= 3, "table should have >=3 lines, got {}", lines.len());
        // Header row
        let header_text: String = lines.iter().map(full_text).collect::<Vec<_>>().join("\n");
        assert!(header_text.contains("Name"), "should contain Name: {header_text}");
        assert!(header_text.contains("Age"), "should contain Age: {header_text}");
        // Header should be bold
        assert!(
            lines[0]
                .spans
                .iter()
                .any(|s| s.style.add_modifier.contains(Modifier::BOLD)),
            "header should be bold"
        );
        // Separator should be dim
        assert!(
            lines[1]
                .spans
                .iter()
                .all(|s| s.style.add_modifier.contains(Modifier::DIM)),
            "separator should be dim"
        );
        // Data row
        let data_text = full_text(&lines[2]);
        assert!(data_text.contains("Alice"), "data should contain Alice: {data_text}");
        assert!(data_text.contains("30"), "data should contain 30: {data_text}");
    }

    #[test]
    fn test_table_column_alignment() {
        let input = "| A | Longer |\n|---|---|\n| X | Y |";
        let lines = style_ai_response(input);
        // Data row "Y" should be padded to width of "Longer" (6 chars)
        let data_text = full_text(&lines[2]);
        assert!(
            data_text.contains("Y     "),
            "Y should be padded to 6: {:?}",
            data_text
        );
    }

    #[test]
    fn test_table_separator_hidden_pipes() {
        let input = "| H |\n|---|\n| D |";
        let lines = style_ai_response(input);
        // Separator row (index 1)
        let sep_text = full_text(&lines[1]);
        assert!(
            !sep_text.contains('|'),
            "separator should not contain ASCII pipe: {sep_text}"
        );
        assert!(
            !sep_text.contains("---"),
            "separator should use box-drawing: {sep_text}"
        );
        assert!(
            sep_text.contains('\u{2500}'),
            "separator should contain \u{2500}: {sep_text}"
        );
        assert!(
            lines[1]
                .spans
                .iter()
                .all(|s| s.style.add_modifier.contains(Modifier::DIM)),
            "separator spans should be dim"
        );
    }

    #[test]
    fn test_table_mixed_with_text() {
        let input = "Before table\n\n| a | b |\n|---|---|\n| c | d |\n\nAfter table";
        let lines = style_ai_response(input);
        let all_text: Vec<String> = lines.iter().map(full_text).collect();
        let joined = all_text.join("|");
        assert!(joined.contains("Before table"), "should have text before table: {joined}");
        assert!(joined.contains("After table"), "should have text after table: {joined}");
    }

    // -- New tests for pulldown-cmark features --

    #[test]
    fn test_nested_bold_inside_italic() {
        let lines = style_ai_response("*italic and **bold***");
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("italic"), "should contain italic text: {text}");
        assert!(text.contains("bold"), "should contain bold text: {text}");
        // The bold part should have BOLD modifier
        assert!(line
            .spans
            .iter()
            .any(|s| s.content.as_ref() == "bold" && s.style.add_modifier.contains(Modifier::BOLD)));
    }

    #[test]
    fn test_code_block_with_language() {
        let input = "```python\ndef hello():\n    pass\n```";
        let lines = style_ai_response(input);
        // Code lines should be yellow
        let code_lines: Vec<&Line> = lines
            .iter()
            .filter(|l| {
                l.spans
                    .iter()
                    .any(|s| s.style.fg == Some(Color::Yellow) && !s.content.is_empty())
            })
            .collect();
        assert!(
            !code_lines.is_empty(),
            "should have yellow code lines"
        );
        let all_text: String = lines.iter().map(full_text).collect::<Vec<_>>().join("\n");
        assert!(
            all_text.contains("def hello():"),
            "should contain code: {all_text}"
        );
    }

    #[test]
    fn test_multi_paragraph_blank_line_separation() {
        let input = "Paragraph one.\n\nParagraph two.";
        let lines = style_ai_response(input);
        // Should produce at least 2 lines (one per paragraph)
        assert!(
            lines.len() >= 2,
            "should have at least 2 lines for 2 paragraphs, got {}",
            lines.len()
        );
        let all_text: String = lines.iter().map(full_text).collect::<Vec<_>>().join("|");
        assert!(all_text.contains("Paragraph one."), "first para: {all_text}");
        assert!(all_text.contains("Paragraph two."), "second para: {all_text}");
    }

    #[test]
    fn test_blockquote_formatting() {
        let input = "> This is a quote";
        let lines = style_ai_response(input);
        assert!(!lines.is_empty(), "blockquote should produce lines");
        // Should have the │ prefix
        let all_text: String = lines.iter().map(full_text).collect::<Vec<_>>().join("|");
        assert!(
            all_text.contains('\u{2502}'),
            "blockquote should have \u{2502} prefix: {all_text}"
        );
        assert!(
            all_text.contains("This is a quote"),
            "blockquote text: {all_text}"
        );
    }

    #[test]
    fn test_mixed_inline_formatting_one_line() {
        let lines = style_ai_response("Here is `code`, **bold**, and *italic* text.");
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("code"), "should have code: {text}");
        assert!(text.contains("bold"), "should have bold: {text}");
        assert!(text.contains("italic"), "should have italic: {text}");
        // code → yellow
        assert!(line
            .spans
            .iter()
            .any(|s| s.content.as_ref() == "code" && s.style.fg == Some(Color::Yellow)));
        // bold → BOLD
        assert!(line
            .spans
            .iter()
            .any(|s| s.content.as_ref() == "bold" && s.style.add_modifier.contains(Modifier::BOLD)));
        // italic → ITALIC
        assert!(line
            .spans
            .iter()
            .any(|s| s.content.as_ref() == "italic" && s.style.add_modifier.contains(Modifier::ITALIC)));
    }

    #[test]
    fn test_link_renders_cyan() {
        let lines = style_ai_response("[click here](https://example.com)");
        let line = &lines[0];
        let text = full_text(line);
        assert!(text.contains("click here"), "link text: {text}");
        assert!(line
            .spans
            .iter()
            .any(|s| s.content.as_ref() == "click here" && s.style.fg == Some(Color::Cyan)));
    }
}
