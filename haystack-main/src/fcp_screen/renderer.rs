//! fcp-screen ratatui renderer — immediate-mode TUI backend.
//!
//! All display state lives in AppState; Renderer wraps ratatui's Terminal
//! for cell-diffing draws. No custom type conversions — Line/Span/Style
//! from ratatui are used directly throughout.

use std::io::{self, Stdout};

use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Layout, Rect},
    style::Modifier,
    text::{Line, Span, Text},
    widgets::{Block, Paragraph, Scrollbar, ScrollbarOrientation, ScrollbarState, Wrap},
    Frame, Terminal,
};

use super::widgets;

/// Left margin (columns) for chat zone text — visual breathing room.
pub const LEFT_MARGIN: u16 = 4;

/// Maximum lines retained in the chat buffer.
const MAX_LINES: usize = 10_000;

// ---------------------------------------------------------------------------
// Overlay state
// ---------------------------------------------------------------------------

/// Active overlay (approval modal or peek panel).
#[derive(Debug, Clone)]
pub struct Overlay {
    pub title: String,
    pub content: Vec<Line<'static>>,
    pub compact: bool,
}

// ---------------------------------------------------------------------------
// AppState — all mutable display state
// ---------------------------------------------------------------------------

/// Display state mutated by direct method calls, read by Renderer::draw().
pub struct AppState {
    /// Cached terminal dimensions (updated on resize).
    pub cols: u16,
    pub rows: u16,
    /// Actual chat zone height from last layout (updated by Renderer::draw).
    pub chat_area_height: u16,
    /// Chat conversation buffer (capped at MAX_LINES).
    pub chat_lines: Vec<Line<'static>>,
    /// Scroll offset (0 = pinned to bottom, >0 = scrolled up).
    pub scroll_offset: usize,
    /// Whether the user has manually scrolled (suppresses auto-scroll on new content).
    pub user_scrolled: bool,
    /// Left side of status bar (model, session, spinner).
    pub status_left: Line<'static>,
    /// Right side of status bar (tokens, cost).
    pub status_right: Line<'static>,
    /// Active overlay (modal or peek).
    pub overlay: Option<Overlay>,
    /// Dirty flag — set when state changes, cleared after draw.
    pub dirty: bool,
    /// Number of temporary streaming lines at the end of chat_lines.
    pub streaming_lines: usize,
    /// User scrolled down during active streaming.
    scroll_down_during_stream: bool,
    /// Debug mode enabled.
    debug_enabled: bool,
    /// Debug panel lines.
    debug_lines_buf: Vec<Line<'static>>,
    /// Debug panel scroll offset.
    debug_scroll_offset: usize,
    /// Which panel has keyboard focus.
    focused_panel: Panel,
    /// Pre-computed session tab bar line (None = single session, hidden).
    pub tab_bar: Option<Line<'static>>,
}

/// Which panel has keyboard focus.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Panel {
    Chat,
    Debug,
}

impl AppState {
    /// Create with explicit size.
    pub fn new(cols: u16, rows: u16) -> Self {
        Self::with_debug_sized(cols, rows, false)
    }

    pub fn with_debug(debug: bool) -> Self {
        let (c, r) = crossterm::terminal::size().unwrap_or((80, 24));
        Self::with_debug_sized(c, r, debug)
    }

    pub fn with_debug_sized(cols: u16, rows: u16, debug: bool) -> Self {
        Self {
            cols,
            rows,
            chat_area_height: rows.saturating_sub(4),
            chat_lines: Vec::new(),
            scroll_offset: 0,
            user_scrolled: false,
            status_left: Line::default(),
            status_right: Line::default(),
            overlay: None,
            dirty: true,
            streaming_lines: 0,
            scroll_down_during_stream: false,
            debug_enabled: debug,
            debug_lines_buf: Vec::new(),
            debug_scroll_offset: 0,
            focused_panel: Panel::Chat,
            tab_bar: None,
        }
    }

    // ── Direct mutation methods (state mutation API) ──────────

    /// Append a line to the chat zone.
    pub fn append_line(&mut self, line: Line<'static>) {
        if self.user_scrolled && self.scroll_offset > 0 {
            self.scroll_offset += 1;
        }
        self.chat_lines.push(line);
        if self.chat_lines.len() > MAX_LINES {
            let excess = self.chat_lines.len() - MAX_LINES;
            self.chat_lines.drain(..excess);
            self.scroll_offset = self.scroll_offset.saturating_sub(excess);
        }
        self.dirty = true;
    }

    /// Update the status bar.
    pub fn set_status(&mut self, left: Line<'static>, right: Line<'static>) {
        self.status_left = left;
        self.status_right = right;
        self.dirty = true;
    }

    /// Show an overlay. First line is the title, rest is body.
    pub fn show_overlay(&mut self, mut content: Vec<Line<'static>>) {
        let title = if content.is_empty() {
            String::new()
        } else {
            let first = content.remove(0);
            first.spans.iter().map(|s| s.content.as_ref()).collect()
        };
        self.overlay = Some(Overlay { title, content, compact: false });
        self.dirty = true;
    }

    /// Show a compact (narrower) overlay.
    pub fn show_overlay_compact(&mut self, mut content: Vec<Line<'static>>) {
        let title = if content.is_empty() {
            String::new()
        } else {
            let first = content.remove(0);
            first.spans.iter().map(|s| s.content.as_ref()).collect()
        };
        self.overlay = Some(Overlay { title, content, compact: true });
        self.dirty = true;
    }

    /// Dismiss the active overlay.
    pub fn dismiss_overlay(&mut self) {
        self.overlay = None;
        self.dirty = true;
    }

    /// Clear the chat zone.
    pub fn clear(&mut self) {
        self.chat_lines.clear();
        self.scroll_offset = 0;
        self.user_scrolled = false;
        self.streaming_lines = 0;
        self.dirty = true;
    }

    /// Append to the debug panel (only stored when debug mode is active).
    pub fn debug_log(&mut self, line: Line<'static>) {
        if self.debug_enabled {
            self.debug_lines_buf.push(line);
            if self.debug_lines_buf.len() > 5000 {
                self.debug_lines_buf.drain(..1000);
            }
            self.dirty = true;
        }
    }

    // ── Streaming ────────────────────────────────────────────────────────

    /// Replace temporary streaming lines with new content.
    pub fn replace_streaming(&mut self, lines: Vec<Line<'static>>) {
        let len = self.chat_lines.len();
        self.chat_lines.truncate(len.saturating_sub(self.streaming_lines));

        if self.user_scrolled && self.scroll_offset > 0 && !self.scroll_down_during_stream {
            let old_count = self.streaming_lines;
            let new_count = lines.len();
            if new_count > old_count {
                self.scroll_offset += new_count - old_count;
            } else {
                self.scroll_offset = self.scroll_offset.saturating_sub(old_count - new_count);
            }
        }

        self.streaming_lines = lines.len();
        self.chat_lines.extend(lines);
        self.dirty = true;
    }

    /// Finalize streaming — remove temporary lines.
    pub fn finalize_streaming(&mut self) {
        let len = self.chat_lines.len();
        self.chat_lines.truncate(len.saturating_sub(self.streaming_lines));
        self.streaming_lines = 0;
        self.dirty = true;
    }

    /// Promote streaming lines to permanent (don't remove them).
    pub fn promote_streaming(&mut self) {
        self.streaming_lines = 0;
    }

    // ── Scroll ───────────────────────────────────────────────────────────

    pub fn scroll_up(&mut self, n: usize) {
        self.scroll_offset = self.scroll_offset.saturating_add(n);
        let max_offset = self.chat_lines.len().saturating_sub(1);
        self.scroll_offset = self.scroll_offset.min(max_offset);
        self.user_scrolled = true;
        self.scroll_down_during_stream = false;
        self.dirty = true;
    }

    pub fn scroll_down(&mut self, n: usize) {
        self.scroll_offset = self.scroll_offset.saturating_sub(n);
        if self.scroll_offset == 0 {
            self.user_scrolled = false;
        }
        if self.streaming_lines > 0 {
            self.scroll_down_during_stream = true;
        }
        self.dirty = true;
    }

    pub fn scroll_to_bottom(&mut self) {
        self.scroll_offset = 0;
        self.user_scrolled = false;
        self.dirty = true;
    }

    // ── Accessors ────────────────────────────────────────────────────────

    pub fn overlay_active(&self) -> bool {
        self.overlay.is_some()
    }

    /// Replace the last line in the chat buffer (used for spinner animation).
    pub fn replace_last_line(&mut self, line: Line<'static>) {
        if let Some(last) = self.chat_lines.last_mut() {
            *last = line;
        } else {
            self.chat_lines.push(line);
        }
        self.dirty = true;
    }

    pub fn chat_bottom_row(&self) -> u16 {
        self.chat_area_height.saturating_sub(1)
    }

    pub fn debug_mode(&self) -> bool {
        self.debug_enabled
    }

    pub fn debug_lines(&self) -> &[Line<'static>] {
        &self.debug_lines_buf
    }

    pub fn scroll_debug_up(&mut self, n: usize) {
        self.debug_scroll_offset = self.debug_scroll_offset.saturating_add(n);
        let max = self.debug_lines_buf.len().saturating_sub(1);
        self.debug_scroll_offset = self.debug_scroll_offset.min(max);
        self.dirty = true;
    }

    pub fn scroll_debug_down(&mut self, n: usize) {
        self.debug_scroll_offset = self.debug_scroll_offset.saturating_sub(n);
        self.dirty = true;
    }

    pub fn scroll_debug_to_bottom(&mut self) {
        self.debug_scroll_offset = 0;
        self.dirty = true;
    }

    pub fn focused_panel(&self) -> Panel {
        self.focused_panel
    }

    pub fn set_focused_panel(&mut self, panel: Panel) {
        self.focused_panel = panel;
    }

    pub fn resize(&mut self, cols: u16, rows: u16) {
        self.cols = cols;
        self.rows = rows;
        self.chat_area_height = rows.saturating_sub(4);
        self.dirty = true;
    }

    pub fn streaming_line_count(&self) -> usize {
        self.streaming_lines
    }

    pub fn mark_dirty(&mut self) {
        self.dirty = true;
    }

    pub fn is_at_bottom(&self) -> bool {
        self.scroll_offset == 0
    }

    pub fn line_count(&self) -> usize {
        self.chat_lines.len()
    }

    pub fn line_at(&self, idx: usize) -> Option<&Line<'static>> {
        self.chat_lines.get(idx)
    }

    pub fn scroll_offset(&self) -> usize {
        self.scroll_offset
    }

    pub fn visible_line_at(&self, row: u16) -> Option<&Line<'static>> {
        let visible = self.chat_bottom_row() as usize + 1;
        let total = self.chat_lines.len();
        let end = total.saturating_sub(self.scroll_offset);
        let start = end.saturating_sub(visible);
        self.chat_lines.get(start + row as usize)
    }
}

// ---------------------------------------------------------------------------
// Renderer — ratatui Terminal wrapper
// ---------------------------------------------------------------------------

pub struct Renderer {
    terminal: Terminal<CrosstermBackend<Stdout>>,
}

impl Renderer {
    pub fn new() -> io::Result<Self> {
        let mut stdout = io::stdout();
        crossterm::terminal::enable_raw_mode()?;
        // Required: alt screen, mouse, paste
        crossterm::execute!(
            stdout,
            crossterm::terminal::EnterAlternateScreen,
            crossterm::event::EnableMouseCapture,
            crossterm::event::EnableBracketedPaste,
        )?;
        // Optional enhancements — fail silently on unsupported terminals
        let _ = crossterm::execute!(stdout, crossterm::terminal::SetTitle("ostk"));
        let _ = crossterm::execute!(stdout, crossterm::event::EnableFocusChange);
        let _ = crossterm::execute!(stdout, crossterm::cursor::SetCursorStyle::BlinkingBar);
        // Kitty keyboard protocol: enables Shift+Enter distinction.
        // Only supported by kitty, foot, WezTerm, ghostty, etc.
        let _ = crossterm::execute!(
            stdout,
            crossterm::event::PushKeyboardEnhancementFlags(
                crossterm::event::KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES
            )
        );
        let backend = CrosstermBackend::new(stdout);
        let terminal = Terminal::new(backend)?;
        Ok(Self { terminal })
    }

    pub fn restore(&mut self) -> io::Result<()> {
        crossterm::terminal::disable_raw_mode()?;
        // Best-effort cleanup — ignore errors from features that weren't enabled
        let _ = crossterm::execute!(self.terminal.backend_mut(), crossterm::terminal::EndSynchronizedUpdate);
        let _ = crossterm::execute!(self.terminal.backend_mut(), crossterm::cursor::SetCursorStyle::DefaultUserShape);
        let _ = crossterm::execute!(self.terminal.backend_mut(), crossterm::event::PopKeyboardEnhancementFlags);
        let _ = crossterm::execute!(self.terminal.backend_mut(), crossterm::event::DisableFocusChange);
        // Required cleanup
        crossterm::execute!(
            self.terminal.backend_mut(),
            crossterm::event::DisableBracketedPaste,
            crossterm::event::DisableMouseCapture,
            crossterm::terminal::LeaveAlternateScreen,
        )?;
        self.terminal.show_cursor()?;
        Ok(())
    }

    /// Update the terminal window/tab title.
    pub fn set_title(&mut self, title: &str) {
        let _ = crossterm::execute!(
            self.terminal.backend_mut(),
            crossterm::terminal::SetTitle(title)
        );
    }

    pub fn size(&self) -> io::Result<Rect> {
        let size = self.terminal.size()?;
        Ok(Rect::new(0, 0, size.width, size.height))
    }

    pub fn draw<F>(
        &mut self,
        state: &mut AppState,
        selection: Option<((u16, usize), (u16, usize))>,
        input_height: u16,
        staged: Option<&str>,
        render_input: F,
    ) -> io::Result<()>
    where
        F: FnOnce(&mut Frame, Rect),
    {
        let _ = crossterm::execute!(self.terminal.backend_mut(), crossterm::terminal::BeginSynchronizedUpdate);
        self.terminal.draw(|frame| {
            let area = frame.area();

            // Tab bar: visible when >1 session exists
            let tab_height = if state.tab_bar.is_some() { 1u16 } else { 0u16 };

            // Staged buffer: visible between chat and input when non-empty
            let staged_height = match staged {
                Some(s) if !s.is_empty() => (s.lines().count() as u16 + 2).min(6), // +2 for border+pad, cap at 6
                _ => 0,
            };

            let layout = Layout::vertical([
                Constraint::Length(tab_height),     // tabs (0 = hidden)
                Constraint::Min(1),                 // chat
                Constraint::Length(staged_height),   // staged (0 = hidden)
                Constraint::Length(input_height),    // input
                Constraint::Length(1),               // status
            ])
            .split(area);

            let tab_area = layout[0];
            let chat_area = layout[1];
            let staged_area = layout[2];
            let input_area = layout[3];
            let status_area = layout[4];

            // Render tab bar if present
            if let Some(ref tabs) = state.tab_bar {
                frame.render_widget(Paragraph::new(tabs.clone()), tab_area);
            }

            state.chat_area_height = chat_area.height;

            render_chat(frame, chat_area, state, selection);

            // Render staged buffer if present
            if let Some(text) = staged
                && !text.is_empty()
            {
                render_staged(frame, staged_area, text);
            }

            render_input(frame, input_area);
            render_status(frame, status_area, state);

            if let Some(ref overlay) = state.overlay {
                widgets::render_overlay(frame, area, overlay);
            }
        })?;
        let _ = crossterm::execute!(self.terminal.backend_mut(), crossterm::terminal::EndSynchronizedUpdate);
        Ok(())
    }
}

impl Drop for Renderer {
    fn drop(&mut self) {
        let _ = self.restore();
    }
}

// ---------------------------------------------------------------------------
// Render functions
// ---------------------------------------------------------------------------

/// Render the staged buffer — submitted text waiting for the next turn.
fn render_staged(frame: &mut Frame, area: Rect, text: &str) {
    use ratatui::style::{Color, Modifier, Style};
    use ratatui::widgets::{Borders, Padding};
    let para = Paragraph::new(text.to_string())
        .style(Style::default().fg(Color::Yellow).add_modifier(Modifier::DIM))
        .block(
            Block::default()
                .borders(Borders::TOP)
                .border_style(Style::default().fg(Color::Yellow))
                .title(" ▸ staged ")
                .title_style(Style::default().fg(Color::Yellow))
                .padding(Padding::horizontal(2))
        );
    frame.render_widget(para, area);
}

fn render_chat(
    frame: &mut Frame,
    area: Rect,
    state: &AppState,
    selection: Option<((u16, usize), (u16, usize))>,
) {
    let total = state.chat_lines.len();
    let visible = area.height as usize;

    if total == 0 {
        frame.render_widget(Paragraph::new(""), area);
        return;
    }

    let end = total.saturating_sub(state.scroll_offset);
    let margin = visible.saturating_mul(3);
    let start = end.saturating_sub(margin);

    // Fast path: no selection → clone the slice without highlight processing.
    // Selection path: clone then apply REVERSED modifier to selected spans.
    // NOTE: both paths clone because ratatui's Text requires owned Line values;
    // full zero-copy rendering requires ratatui to support Text from borrowed slices
    // (tracked via unstable-widget-ref feature, enabled for future use).
    let text = if let Some(sel) = selection {
        let mut lines: Vec<Line> = state.chat_lines[start..end].to_vec();
        apply_selection_highlight(&mut lines, sel, start, state.cols);
        Text::from(lines)
    } else {
        Text::from(state.chat_lines[start..end].to_vec())
    };

    let para = Paragraph::new(text)
        .wrap(Wrap { trim: false })
        .block(Block::default().padding(ratatui::widgets::Padding::horizontal(LEFT_MARGIN)));

    // Use ratatui's own line_count — accounts for wrapping, padding, styled spans.
    // This is the single source of truth — no manual div_ceil that can diverge.
    let inner_width = area.width.saturating_sub(LEFT_MARGIN * 2);
    let visual_lines = para.line_count(inner_width);
    let scroll_from_top = visual_lines.saturating_sub(visible);

    let para = para.scroll((scroll_from_top as u16, 0));
    frame.render_widget(para, area);

    // Only show scrollbar when content exceeds viewport
    if visual_lines > visible {
        let mut scrollbar_state = ScrollbarState::new(visual_lines)
            .position(scroll_from_top);
        frame.render_stateful_widget(
            Scrollbar::new(ScrollbarOrientation::VerticalRight)
                .begin_symbol(None)
                .end_symbol(None),
            area,
            &mut scrollbar_state,
        );
    }
}

fn apply_selection_highlight(
    lines: &mut [Line],
    sel: ((u16, usize), (u16, usize)),
    slice_start: usize,
    _cols: u16,
) {
    use super::components::selection::normalize;
    let ((sc, sr), (ec, er)) = normalize(sel);
    for (i, line) in lines.iter_mut().enumerate() {
        let buf_idx = slice_start + i;
        if buf_idx < sr || buf_idx > er {
            continue;
        }
        let col_start = if buf_idx == sr {
            sc.saturating_sub(LEFT_MARGIN) as usize
        } else {
            0
        };
        let col_end = if buf_idx == er {
            ec.saturating_sub(LEFT_MARGIN) as usize
        } else {
            usize::MAX
        };
        let mut col = 0usize;
        let mut new_spans: Vec<Span> = Vec::new();
        for span in line.spans.iter() {
            let span_width = span.width();
            let span_start = col;
            let span_end = col + span_width;
            col += span_width;

            if span_end <= col_start || span_start > col_end {
                new_spans.push(span.clone());
            } else if span_start >= col_start && span_end <= col_end {
                let mut s = span.clone();
                s.style = s.style.add_modifier(Modifier::REVERSED);
                new_spans.push(s);
            } else {
                let text = &span.content;
                let chars: Vec<char> = text.chars().collect();
                let mut char_col = span_start;
                let mut before = String::new();
                let mut selected = String::new();
                let mut after = String::new();
                for &ch in &chars {
                    let cw = unicode_width::UnicodeWidthChar::width(ch).unwrap_or(1);
                    if char_col < col_start {
                        before.push(ch);
                    } else if char_col <= col_end {
                        selected.push(ch);
                    } else {
                        after.push(ch);
                    }
                    char_col += cw;
                }
                if !before.is_empty() {
                    new_spans.push(Span::styled(before, span.style));
                }
                if !selected.is_empty() {
                    new_spans.push(Span::styled(selected, span.style.add_modifier(Modifier::REVERSED)));
                }
                if !after.is_empty() {
                    new_spans.push(Span::styled(after, span.style));
                }
            }
        }
        *line = Line::from(new_spans);
    }
}

fn render_status(frame: &mut Frame, area: Rect, state: &AppState) {
    let left_line = state.status_left.clone();
    let right_line = state.status_right.clone();

    let right_width = right_line.width() as u16;

    let status_layout = Layout::horizontal([
        Constraint::Min(1),
        Constraint::Length(right_width.saturating_add(1)),
    ])
    .split(area);

    let left_para = Paragraph::new(left_line);
    let right_para = Paragraph::new(right_line)
        .alignment(ratatui::layout::Alignment::Right);

    frame.render_widget(left_para, status_layout[0]);
    frame.render_widget(right_para, status_layout[1]);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fcp_screen::protocol::{self, Color};

    fn line(text: &str) -> Line<'static> {
        protocol::line_plain(text)
    }

    #[test]
    fn append_line_adds_to_buffer() {
        let mut state = AppState::new(80, 24);
        state.append_line(line("hello"));
        assert_eq!(state.chat_lines.len(), 1);
        assert!(state.dirty);
    }

    #[test]
    fn clear_resets_state() {
        let mut state = AppState::new(80, 24);
        state.append_line(line("a"));
        state.append_line(line("b"));
        state.clear();
        assert!(state.chat_lines.is_empty());
        assert_eq!(state.scroll_offset, 0);
        assert_eq!(state.streaming_lines, 0);
    }

    #[test]
    fn set_status_updates_lines() {
        let mut state = AppState::new(80, 24);
        state.set_status(
            protocol::line_styled("model", Color::Cyan),
            protocol::line_plain("tokens"),
        );
        // Verify spans exist
        assert!(!state.status_left.spans.is_empty());
        assert!(!state.status_right.spans.is_empty());
    }

    #[test]
    fn show_overlay_extracts_title() {
        let mut state = AppState::new(80, 24);
        state.show_overlay(vec![
            protocol::line_styled("Title", Color::Yellow),
            protocol::line_plain("body line 1"),
            protocol::line_plain("body line 2"),
        ]);
        assert!(state.overlay.is_some());
        let ov = state.overlay.as_ref().unwrap();
        assert_eq!(ov.title, "Title");
        assert_eq!(ov.content.len(), 2);
        assert!(!ov.compact);
    }

    #[test]
    fn show_overlay_compact() {
        let mut state = AppState::new(80, 24);
        state.show_overlay_compact(vec![
            protocol::line_plain("Title"),
            protocol::line_plain("body"),
        ]);
        assert!(state.overlay.as_ref().unwrap().compact);
    }

    #[test]
    fn dismiss_overlay() {
        let mut state = AppState::new(80, 24);
        state.show_overlay(vec![protocol::line_plain("T")]);
        assert!(state.overlay_active());
        state.dismiss_overlay();
        assert!(!state.overlay_active());
    }

    #[test]
    fn buffer_cap() {
        let mut state = AppState::new(80, 24);
        for i in 0..MAX_LINES + 100 {
            state.append_line(line(&format!("line {i}")));
        }
        assert_eq!(state.chat_lines.len(), MAX_LINES);
    }

    #[test]
    fn scroll_up_down() {
        let mut state = AppState::new(80, 24);
        for i in 0..50 {
            state.append_line(line(&format!("line {i}")));
        }
        state.scroll_up(5);
        assert_eq!(state.scroll_offset, 5);
        assert!(state.user_scrolled);

        state.scroll_down(3);
        assert_eq!(state.scroll_offset, 2);
        assert!(state.user_scrolled);

        state.scroll_down(10);
        assert_eq!(state.scroll_offset, 0);
        assert!(!state.user_scrolled);
    }

    #[test]
    fn scroll_to_bottom() {
        let mut state = AppState::new(80, 24);
        state.scroll_up(10);
        state.scroll_to_bottom();
        assert_eq!(state.scroll_offset, 0);
        assert!(!state.user_scrolled);
    }

    #[test]
    fn streaming_replace_finalize() {
        let mut state = AppState::new(80, 24);
        state.append_line(line("permanent"));

        state.replace_streaming(vec![line("stream 1"), line("stream 2")]);
        assert_eq!(state.chat_lines.len(), 3);
        assert_eq!(state.streaming_lines, 2);

        state.replace_streaming(vec![line("stream a"), line("stream b"), line("stream c")]);
        assert_eq!(state.chat_lines.len(), 4);
        assert_eq!(state.streaming_lines, 3);

        state.finalize_streaming();
        assert_eq!(state.chat_lines.len(), 1);
        assert_eq!(state.streaming_lines, 0);
    }

    #[test]
    fn streaming_promote() {
        let mut state = AppState::new(80, 24);
        state.replace_streaming(vec![line("kept")]);
        assert_eq!(state.streaming_lines, 1);

        state.promote_streaming();
        assert_eq!(state.streaming_lines, 0);
        assert_eq!(state.chat_lines.len(), 1);
    }

    #[test]
    fn scroll_compensates_append_when_scrolled() {
        let mut state = AppState::new(80, 24);
        for _ in 0..20 {
            state.append_line(line("x"));
        }
        state.scroll_up(5);
        assert_eq!(state.scroll_offset, 5);

        state.append_line(line("new"));
        assert_eq!(state.scroll_offset, 6);
    }

    #[test]
    fn dirty_flag_cleared_concept() {
        let mut state = AppState::new(80, 24);
        assert!(state.dirty);
        state.dirty = false;
        state.append_line(line("x"));
        assert!(state.dirty);
    }

    #[test]
    fn chat_bottom_row_uses_cached_height() {
        let mut state = AppState::new(80, 24);
        assert_eq!(state.chat_area_height, 20);
        assert_eq!(state.chat_bottom_row(), 19);

        state.chat_area_height = 15;
        assert_eq!(state.chat_bottom_row(), 14);
    }

    #[test]
    fn resize_updates_chat_area_height_estimate() {
        let mut state = AppState::new(80, 24);
        assert_eq!(state.chat_area_height, 20);

        state.resize(100, 40);
        assert_eq!(state.chat_area_height, 36);
        assert_eq!(state.chat_bottom_row(), 35);
    }

    #[test]
    fn visible_line_at_consistent_with_large_buffer() {
        let mut state = AppState::new(80, 24);
        for i in 0..500 {
            state.append_line(line(&format!("line-{i:03}")));
        }
        let bottom = state.chat_bottom_row();
        let last = state.visible_line_at(bottom);
        assert!(last.is_some());

        state.scroll_up(10);
        let last_scrolled = state.visible_line_at(bottom);
        assert!(last_scrolled.is_some());
    }

    #[test]
    fn render_chat_visual_line_count_via_paragraph() {
        // Ratatui's Paragraph::line_count() is now the source of truth.
        // Verify it agrees with expectations for simple ASCII content.
        let width: u16 = 40;
        let x40 = "x".repeat(40);
        let x41 = "x".repeat(41);
        let x80 = "x".repeat(80);
        let x81 = "x".repeat(81);
        let cases: Vec<(&str, usize)> = vec![
            ("", 1),           // empty line = 1 visual line
            ("short", 1),      // fits in 40 cols
            (&x40, 1),         // exactly 40 cols
            (&x41, 2),         // wraps to 2
            (&x80, 2),         // exactly 2
            (&x81, 3),         // wraps to 3
        ];
        for (content, expected) in cases {
            let para = Paragraph::new(content.to_string())
                .wrap(Wrap { trim: false });
            let actual = para.line_count(width);
            assert_eq!(actual, expected, "content len={} should produce {expected} visual lines", content.len());
        }
    }

    #[test]
    fn selection_highlight_applies_reversed() {
        let lines_data = vec![
            protocol::line_plain("first"),
            protocol::line_plain("second"),
            protocol::line_plain("third"),
        ];
        let mut lines: Vec<Line> = lines_data;
        let sel = ((LEFT_MARGIN, 1usize), (80, 1usize));
        apply_selection_highlight(&mut lines, sel, 0, 80);

        assert!(lines[1].spans.iter().any(|s| s.style.add_modifier.contains(Modifier::REVERSED)));
    }

    #[test]
    fn selection_highlight_partial_line() {
        let mut lines: Vec<Line> = vec![protocol::line_plain("hello world")];
        let sel = ((LEFT_MARGIN + 6, 0usize), (LEFT_MARGIN + 10, 0usize));
        apply_selection_highlight(&mut lines, sel, 0, 80);

        assert!(lines[0].spans.len() >= 2);
        let reversed_text: String = lines[0].spans.iter()
            .filter(|s| s.style.add_modifier.contains(Modifier::REVERSED))
            .map(|s| s.content.as_ref())
            .collect();
        assert!(reversed_text.contains("world"));
    }
}
