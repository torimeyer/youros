//! Text selection component — mouse click-drag selection, clipboard copy.
//!
//! Extracted from app.rs to isolate selection state management and
//! rendering from the main event loop.

use std::io;

use ratatui::text::Line;
use crate::fcp_screen::protocol::display_width;
use crate::fcp_screen::renderer::{AppState as Screen, LEFT_MARGIN};

// ---------------------------------------------------------------------------
// SelectionState — owns the drag lifecycle
// ---------------------------------------------------------------------------

/// Encapsulates the mutable state for a mouse text selection.
///
/// Coordinates are stored in **buffer space**: columns are screen columns,
/// but rows are absolute buffer indices (0 = oldest line). This makes the
/// selection immune to scroll-offset changes that occur during edge-scroll
/// drag operations.
pub struct SelectionState {
    /// Start and end coordinates: ((start_col, start_buf_row), (end_col, end_buf_row)).
    pub range: Option<((u16, usize), (u16, usize))>,
    /// `true` while the mouse button is held (actively dragging).
    pub active: bool,
}

impl Default for SelectionState {
    fn default() -> Self {
        Self::new()
    }
}

impl SelectionState {
    pub fn new() -> Self {
        Self { range: None, active: false }
    }

    /// Begin a new selection at the given screen coordinate (converted to buffer space).
    pub fn start(&mut self, col: u16, row: u16, scr: &Screen) {
        let buf_row = screen_to_buffer(row, scr);
        self.range = Some(((col, buf_row), (col, buf_row)));
        self.active = true;
    }

    /// Extend the in-progress selection to a new endpoint (screen coords → buffer).
    pub fn extend(&mut self, col: u16, row: u16, scr: &Screen) {
        if let Some(ref mut sel) = self.range {
            let buf_row = screen_to_buffer(row, scr);
            sel.1 = (col, buf_row);
        }
    }

    /// Finalize the selection.  If start == end, treat as a click (clear).
    pub fn finish(&mut self) {
        self.active = false;
        if let Some(sel) = self.range
            && sel.0 == sel.1 {
                self.range = None;
            }
    }

    /// Discard the current selection entirely.
    pub fn clear(&mut self) {
        self.range = None;
        self.active = false;
    }

    /// Returns `true` when there is a finalized (non-empty) selection.
    pub fn has_selection(&self) -> bool {
        self.range.is_some()
    }
}

/// Convert a screen row (0-based, within the visible chat zone) to an
/// absolute buffer index. This accounts for the current scroll offset so
/// that selection coordinates remain stable when the viewport moves.
pub fn screen_to_buffer(row: u16, scr: &Screen) -> usize {
    let visible = scr.chat_bottom_row() as usize + 1;
    let total = scr.line_count();
    let end = total.saturating_sub(scr.scroll_offset());
    let start = end.saturating_sub(visible);
    start + row as usize
}

// buffer_to_screen and render() removed — selection highlighting is now
// handled inside ratatui's draw closure via renderer::apply_selection_highlight().

// ---------------------------------------------------------------------------
// Pure functions — normalize, extract, clipboard
// ---------------------------------------------------------------------------

/// Normalize a selection so that `start` is always before `end` (top-left to bottom-right).
/// Rows are buffer indices (usize), columns are screen columns (u16).
pub fn normalize(sel: ((u16, usize), (u16, usize))) -> ((u16, usize), (u16, usize)) {
    let ((sc, sr), (ec, er)) = sel;
    if sr < er || (sr == er && sc <= ec) {
        ((sc, sr), (ec, er))
    } else {
        ((ec, er), (sc, sr))
    }
}

/// Extract selected text from the screen buffer as plain text.
/// Selection coordinates are in buffer space (rows are absolute buffer indices).
pub fn extract_text(scr: &Screen, sel: ((u16, usize), (u16, usize))) -> String {
    let ((sc, sr), (ec, er)) = normalize(sel);
    let line_count = scr.line_count();
    let last = er.min(line_count.saturating_sub(1));
    let mut result = String::new();
    for buf_idx in sr..=last {
        let line_text = match scr.line_at(buf_idx) {
            Some(line) => plain_text(line),
            None => String::new(),
        };
        let col_start = if buf_idx == sr { sc } else { LEFT_MARGIN };
        let col_end = if buf_idx == er { ec } else {
            // →923: Use display_width, not byte length
            (LEFT_MARGIN as usize + display_width(&line_text)) as u16
        };
        // →923: Convert screen columns to character indices via display width walk.
        // Each character may occupy 1 or 2 columns; we walk until we reach the target column.
        let target_start = (col_start as usize).saturating_sub(LEFT_MARGIN as usize);
        let target_end = (col_end as usize).saturating_sub(LEFT_MARGIN as usize);
        let chars: Vec<char> = line_text.chars().collect();
        let mut col = 0;
        let mut start = 0;
        for (i, &c) in chars.iter().enumerate() {
            if col >= target_start { start = i; break; }
            col += crate::fcp_screen::protocol::char_width(c);
            start = i + 1;
        }
        col = 0;
        let mut end = 0;
        for (i, &c) in chars.iter().enumerate() {
            col += crate::fcp_screen::protocol::char_width(c);
            end = i + 1;
            if col > target_end { break; }
        }
        let start = start.min(chars.len());
        let end = end.min(chars.len());
        if start < end {
            let slice: String = chars[start..end].iter().collect();
            result.push_str(&slice);
        }
        // Newline between every line except the last in the selection
        if buf_idx < last {
            result.push('\n');
        }
    }
    result
}

/// Copy text to clipboard — OSC 52 (works over SSH) with pbcopy fallback.
pub fn copy_to_clipboard(text: &str) {
    use std::io::Write;
    // OSC 52: terminal-native clipboard — works over SSH, tmux, etc.
    let b64 = base64_encode(text.as_bytes());
    let _ = io::stdout().write_all(format!("\x1b]52;c;{b64}\x07").as_bytes());
    let _ = io::stdout().flush();
    // Also try pbcopy as fallback (local macOS)
    if let Ok(mut child) = std::process::Command::new("pbcopy")
        .stdin(std::process::Stdio::piped())
        .spawn()
    {
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(text.as_bytes());
        }
        let _ = child.wait();
    }
}

/// Minimal base64 encoder (no dependency needed for this one use).
fn base64_encode(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let triple = match chunk.len() {
            3 => (chunk[0] as u32) << 16 | (chunk[1] as u32) << 8 | chunk[2] as u32,
            2 => (chunk[0] as u32) << 16 | (chunk[1] as u32) << 8,
            1 => (chunk[0] as u32) << 16,
            _ => unreachable!(),
        };
        out.push(ALPHABET[((triple >> 18) & 0x3F) as usize] as char);
        out.push(ALPHABET[((triple >> 12) & 0x3F) as usize] as char);
        if chunk.len() > 1 {
            out.push(ALPHABET[((triple >> 6) & 0x3F) as usize] as char);
        } else {
            out.push('=');
        }
        if chunk.len() > 2 {
            out.push(ALPHABET[(triple & 0x3F) as usize] as char);
        } else {
            out.push('=');
        }
    }
    out
}

/// Extract the plain text content of a Line.
fn plain_text(line: &Line) -> String {
    line.spans.iter().map(|s| s.content.as_ref()).collect()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fcp_screen::protocol::{self, Color};
    use crate::fcp_screen::renderer::AppState as Screen;

    // -- normalize ----------------------------------------------------------

    #[test]
    fn normalize_already_ordered() {
        let sel = ((4, 2usize), (10, 5usize));
        assert_eq!(normalize(sel), ((4, 2), (10, 5)));
    }

    #[test]
    fn normalize_reversed_rows() {
        let sel = ((10, 5usize), (4, 2usize));
        assert_eq!(normalize(sel), ((4, 2), (10, 5)));
    }

    #[test]
    fn normalize_same_row_reversed_cols() {
        let sel = ((15, 3usize), (4, 3usize));
        assert_eq!(normalize(sel), ((4, 3), (15, 3)));
    }

    #[test]
    fn normalize_same_point() {
        let sel = ((5, 5usize), (5, 5usize));
        assert_eq!(normalize(sel), ((5, 5), (5, 5)));
    }

    // -- plain_text ---------------------------------------------------------

    #[test]
    fn plain_text_multi_span() {
        let line = protocol::line_from_spans(vec![
            protocol::colored("[ai] ", Color::Green),
            protocol::plain("hello world"),
        ]);
        assert_eq!(plain_text(&line), "[ai] hello world");
    }

    #[test]
    fn plain_text_empty() {
        assert_eq!(plain_text(&protocol::line_empty()), "");
    }

    // -- extract_text -------------------------------------------------------

    /// Helper: fill screen with enough lines to reach the bottom, then
    /// convert a screen row to a buffer index for use in selection coords.
    fn fill_and_buf_idx(scr: &Screen, screen_row: u16) -> usize {
        screen_to_buffer(screen_row, scr)
    }

    #[test]
    fn extract_text_single_line() {
        let mut scr = Screen::new(80, 24);
        for _ in 0..scr.chat_bottom_row() + 1 {
            scr.append_line(protocol::line_plain(""));
        }
        scr.append_line(protocol::line_plain("hello world"));
        // The last line is at screen row chat_bottom_row — convert to buffer index
        let row = scr.chat_bottom_row();
        let buf_row = fill_and_buf_idx(&scr, row);
        // cols 4..=8 → char indices 0..=4 → "hello" (inclusive end)
        let sel = ((4, buf_row), (8, buf_row));
        let text = extract_text(&scr, sel);
        assert_eq!(text, "hello");
    }

    #[test]
    fn extract_text_multi_line() {
        let mut scr = Screen::new(80, 24);
        let visible = scr.chat_bottom_row() as usize + 1;
        for i in 0..visible {
            scr.append_line(
                protocol::line_plain(format!("line {i}")));
        }
        let row0 = (visible - 2) as u16;
        let row1 = (visible - 1) as u16;
        let buf_row0 = fill_and_buf_idx(&scr, row0);
        let buf_row1 = fill_and_buf_idx(&scr, row1);
        let sel = ((4, buf_row0), (80, buf_row1));
        let text = extract_text(&scr, sel);
        assert!(text.contains("line"));
        assert!(text.contains('\n'));
    }

    #[test]
    fn extract_text_before_margin_clamps() {
        let mut scr = Screen::new(80, 24);
        for _ in 0..scr.chat_bottom_row() + 1 {
            scr.append_line(protocol::line_plain("test"));
        }
        let row = scr.chat_bottom_row();
        let buf_row = fill_and_buf_idx(&scr, row);
        let sel = ((0, buf_row), (8, buf_row));
        let text = extract_text(&scr, sel);
        assert_eq!(text, "test");
    }

    // -- SelectionState lifecycle -------------------------------------------

    #[test]
    fn state_new_is_empty() {
        let st = SelectionState::new();
        assert!(!st.has_selection());
        assert!(!st.active);
    }

    #[test]
    fn state_start_makes_active() {
        let scr = Screen::new(80, 24);
        let mut st = SelectionState::new();
        st.start(10, 5, &scr);
        assert!(st.active);
        assert!(st.range.is_some());
    }

    #[test]
    fn state_extend_updates_endpoint() {
        let scr = Screen::new(80, 24);
        let mut st = SelectionState::new();
        st.start(4, 2, &scr);
        st.extend(20, 7, &scr);
        let r = st.range.unwrap();
        assert_ne!(r.0, r.1); // endpoints differ
    }

    #[test]
    fn state_finish_single_click_clears() {
        let scr = Screen::new(80, 24);
        let mut st = SelectionState::new();
        st.start(5, 5, &scr);
        // No extend — start == end
        st.finish();
        assert!(!st.active);
        assert!(!st.has_selection());
    }

    #[test]
    fn state_finish_real_selection_kept() {
        let scr = Screen::new(80, 24);
        let mut st = SelectionState::new();
        st.start(4, 2, &scr);
        st.extend(20, 7, &scr);
        st.finish();
        assert!(!st.active);
        assert!(st.has_selection());
    }

    #[test]
    fn state_clear_removes_everything() {
        let scr = Screen::new(80, 24);
        let mut st = SelectionState::new();
        st.start(4, 2, &scr);
        st.extend(20, 7, &scr);
        st.finish();
        st.clear();
        assert!(!st.has_selection());
        assert!(!st.active);
    }

    // -- Bug fix tests ------------------------------------------------------

    #[test]
    fn test_extract_text_preserves_newlines() {
        // Select 3 lines, verify extracted text contains 2 newlines.
        let mut scr = Screen::new(80, 24);
        let visible = scr.chat_bottom_row() as usize + 1;
        // Fill enough lines to reach the visible area
        for _ in 0..visible.saturating_sub(3) {
            scr.append_line(protocol::line_plain(""));
        }
        scr.append_line(protocol::line_plain("alpha"));
        scr.append_line(protocol::line_plain("beta"));
        scr.append_line(protocol::line_plain("gamma"));

        // Select all 3 lines (last 3 visible rows)
        let row_top = (visible - 3) as u16;
        let row_bot = (visible - 1) as u16;
        let buf_top = screen_to_buffer(row_top, &scr);
        let buf_bot = screen_to_buffer(row_bot, &scr);
        // Select from LEFT_MARGIN to end-of-line on each
        let sel = ((LEFT_MARGIN, buf_top), (80, buf_bot));
        let text = extract_text(&scr, sel);
        // Should contain exactly 2 newlines (between 3 lines)
        let newline_count = text.chars().filter(|&c| c == '\n').count();
        assert_eq!(newline_count, 2, "expected 2 newlines in 3-line selection, got: {:?}", text);
        assert!(text.contains("alpha"), "missing 'alpha' in: {:?}", text);
        assert!(text.contains("beta"), "missing 'beta' in: {:?}", text);
        assert!(text.contains("gamma"), "missing 'gamma' in: {:?}", text);
    }

    #[test]
    fn test_selection_with_scroll_offset() {
        // Create a screen with 50 lines, scroll up 10, select visible rows,
        // verify correct buffer lines are extracted.
        let mut scr = Screen::new(80, 24);
        for i in 0..50 {
            scr.append_line(
                protocol::line_plain(format!("LINE-{i:02}")));
        }
        // Scroll up by 10
        scr.scroll_up(10);
        assert_eq!(scr.scroll_offset(), 10);

        let visible = scr.chat_bottom_row() as usize + 1;
        // The bottom visible line should be line (50 - 10 - 1) = line 39
        // The top visible line should be line (50 - 10 - visible)
        let bottom_screen_row = scr.chat_bottom_row();
        let buf_bot = screen_to_buffer(bottom_screen_row, &scr);
        let buf_top = screen_to_buffer(0, &scr);

        // Select the top-most visible row
        let sel_top = ((LEFT_MARGIN, buf_top), (80, buf_top));
        let text_top = extract_text(&scr, sel_top);
        let expected_top_idx = 50usize.saturating_sub(10).saturating_sub(visible);
        let expected_top = format!("LINE-{expected_top_idx:02}");
        assert!(text_top.contains(&expected_top),
            "top visible line should be '{}', got: {:?}", expected_top, text_top);

        // Select the bottom-most visible row
        let sel_bot = ((LEFT_MARGIN, buf_bot), (80, buf_bot));
        let text_bot = extract_text(&scr, sel_bot);
        // Bottom visible = total - scroll_offset - 1 = 50 - 10 - 1 = 39
        assert!(text_bot.contains("LINE-39"),
            "bottom visible line should be 'LINE-39', got: {:?}", text_bot);

        // Verify that scrolling further doesn't break existing selection coords.
        // The buf_bot index points to LINE-39 regardless of future scroll changes.
        scr.scroll_up(5); // now scroll_offset = 15
        let text_after_scroll = extract_text(&scr, sel_bot);
        assert!(text_after_scroll.contains("LINE-39"),
            "buffer-space selection should be stable after scroll, got: {:?}", text_after_scroll);
    }
}
