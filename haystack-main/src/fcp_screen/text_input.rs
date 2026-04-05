//! fcp-screen text input — tui-textarea wrapper with history, picker, verb highlighting.
//!
//! Wraps ratatui_textarea::TextArea with ostk-specific behavior:
//! command history (up/down), submit-on-Enter, inline picker (model selector),
//! and verb highlighting (:compile shows in cyan).

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui_textarea::{Input, Key, TextArea};

/// Actions produced by text input handling.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InputAction {
    /// User pressed Enter — submit this text.
    Submit(String),
    /// User pressed Escape — cancel.
    Cancel,
    /// User pressed Up on first line — focus staged buffer if present.
    FocusStaged,
    /// User pressed a peek key (Alt+f, Alt+w, etc.).
    Peek(char),
    /// Picker: user selected an item.
    PickerSelect(String),
    /// Picker: user cancelled.
    PickerCancel,
}

/// Inline single-line picker — rotates through items with Up/Down, Enter selects.
#[derive(Debug, Clone)]
pub struct InlinePicker {
    pub items: Vec<String>,
    pub index: usize,
    pub label: String,
}

impl InlinePicker {
    pub fn new(label: &str, items: Vec<String>, current: Option<&str>) -> Self {
        let index = current
            .and_then(|c| items.iter().position(|m| m == c))
            .unwrap_or(0);
        Self {
            items,
            index,
            label: label.to_string(),
        }
    }

    pub fn current(&self) -> Option<&str> {
        self.items.get(self.index).map(|s| s.as_str())
    }

    pub fn next(&mut self) {
        if !self.items.is_empty() {
            self.index = (self.index + 1) % self.items.len();
        }
    }

    pub fn prev(&mut self) {
        if !self.items.is_empty() {
            self.index = if self.index == 0 {
                self.items.len() - 1
            } else {
                self.index - 1
            };
        }
    }
}

/// Convert crossterm KeyEvent to ratatui-textarea Input.
/// Needed because ratatui-textarea uses ratatui-crossterm's re-exported types,
/// which may differ from our direct crossterm dependency.
fn key_to_input(key: KeyEvent) -> Input {
    let k = match key.code {
        KeyCode::Char(c) => Key::Char(c),
        KeyCode::Backspace => Key::Backspace,
        KeyCode::Enter => Key::Enter,
        KeyCode::Left => Key::Left,
        KeyCode::Right => Key::Right,
        KeyCode::Up => Key::Up,
        KeyCode::Down => Key::Down,
        KeyCode::Tab => Key::Tab,
        KeyCode::Delete => Key::Delete,
        KeyCode::Home => Key::Home,
        KeyCode::End => Key::End,
        KeyCode::PageUp => Key::PageUp,
        KeyCode::PageDown => Key::PageDown,
        KeyCode::Esc => Key::Esc,
        KeyCode::F(n) => Key::F(n),
        _ => Key::Null,
    };
    Input {
        key: k,
        ctrl: key.modifiers.contains(KeyModifiers::CONTROL),
        alt: key.modifiers.contains(KeyModifiers::ALT),
        shift: key.modifiers.contains(KeyModifiers::SHIFT),
    }
}

/// Text input wrapper with history, picker, and ostk keybindings.
pub struct TextInput {
    textarea: TextArea<'static>,
    history: Vec<String>,
    history_idx: Option<usize>,
    /// Saved buffer content when browsing history.
    history_stash: Option<String>,
    /// Active inline picker (model selector, etc.).
    pub picker: Option<InlinePicker>,
    /// When true, Up on first line returns FocusStaged instead of history.
    pub staged_active: bool,
}

/// Apply standard styling to a TextArea (block, cursor, placeholder).
fn style_textarea(ta: &mut TextArea<'static>) {
    use ratatui::style::{Color, Style};
    use ratatui::widgets::{Block, Borders, Padding};
    ta.set_cursor_line_style(Style::default());
    ta.set_placeholder_text("type here...");
    ta.set_placeholder_style(Style::default().fg(Color::DarkGray));
    ta.set_block(
        Block::default()
            .borders(Borders::TOP)
            .border_style(Style::default().fg(Color::DarkGray))
            .padding(Padding::new(2, 2, 1, 1))
    );
    ta.set_tab_length(4);
}

impl Default for TextInput {
    fn default() -> Self {
        Self::new()
    }
}

impl TextInput {
    pub fn new() -> Self {
        let mut ta = TextArea::default();
        style_textarea(&mut ta);
        Self {
            textarea: ta,
            history: Vec::new(),
            history_idx: None,
            history_stash: None,
            picker: None,
            staged_active: false,
        }
    }

    /// Handle a key event. Returns Some(action) if the key produced a result,
    /// None if it was consumed by the textarea.
    pub fn handle_key(&mut self, key: KeyEvent) -> Option<InputAction> {
        // Picker mode: intercept all keys
        if let Some(ref mut picker) = self.picker {
            return match key.code {
                KeyCode::Enter => {
                    let selected = picker.current().unwrap_or("").to_string();
                    self.picker = None;
                    Some(InputAction::PickerSelect(selected))
                }
                KeyCode::Esc => {
                    self.picker = None;
                    Some(InputAction::PickerCancel)
                }
                KeyCode::Up => {
                    picker.prev();
                    None
                }
                KeyCode::Down => {
                    picker.next();
                    None
                }
                _ => None, // swallow other keys during picker
            };
        }

        // Alt+key → peek shortcuts
        if key.modifiers.contains(KeyModifiers::ALT)
            && let KeyCode::Char(c) = key.code
        {
            return Some(InputAction::Peek(c));
        }

        // Escape → cancel
        if key.code == KeyCode::Esc {
            return Some(InputAction::Cancel);
        }

        // Shift+Enter or Alt+Enter → insert newline
        if key.code == KeyCode::Enter
            && (key.modifiers.contains(KeyModifiers::SHIFT)
                || key.modifiers.contains(KeyModifiers::ALT))
        {
            self.textarea.input(key_to_input(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)));
            return None;
        }

        // Enter (no modifiers) → submit
        if key.code == KeyCode::Enter {
            let text = self.text();
            if !text.is_empty() {
                self.history.push(text.clone());
                self.history_idx = None;
                self.history_stash = None;
            }
            self.textarea = TextArea::default();
            style_textarea(&mut self.textarea);
            return Some(InputAction::Submit(text));
        }

        // Up arrow on first line → focus staged buffer (if present) or history
        if key.code == KeyCode::Up && self.textarea.cursor().0 == 0 {
            if self.staged_active {
                return Some(InputAction::FocusStaged);
            }
            self.history_up();
            return None;
        }

        // Down arrow → history (when on last line)
        if key.code == KeyCode::Down {
            let last_line = self.textarea.lines().len().saturating_sub(1);
            if self.textarea.cursor().0 == last_line && self.history_idx.is_some() {
                self.history_down();
                return None;
            }
        }

        // Everything else → pass to textarea
        self.textarea.input(key_to_input(key));
        // Reset history index when user types
        if self.history_idx.is_some() && matches!(key.code, KeyCode::Char(_) | KeyCode::Backspace | KeyCode::Delete) {
            self.history_idx = None;
            self.history_stash = None;
        }
        None
    }

    /// Get the current text content.
    pub fn text(&self) -> String {
        self.textarea.lines().join("\n")
    }

    /// Set the text content (replaces everything).
    pub fn set_text(&mut self, text: &str) {
        self.textarea = TextArea::new(text.lines().map(String::from).collect());
        style_textarea(&mut self.textarea);
        self.textarea.move_cursor(ratatui_textarea::CursorMove::End);
    }

    /// Whether the input is empty.
    pub fn is_empty(&self) -> bool {
        self.textarea.lines().iter().all(|l| l.is_empty())
    }

    /// Get the textarea widget reference for rendering.
    pub fn widget(&self) -> &TextArea<'static> {
        &self.textarea
    }

    /// Desired height in rows: content lines + 1 (top border), clamped to [MIN, MAX].
    /// The layout uses this to grow the input area dynamically.
    pub fn desired_height(&self) -> u16 {
        const MIN_HEIGHT: u16 = 4;  // border + top pad + 1 line + bottom pad
        const MAX_HEIGHT: u16 = 12;
        let content_lines = self.textarea.lines().len().max(1) as u16;
        (content_lines + 3).clamp(MIN_HEIGHT, MAX_HEIGHT) // +1 border +1 top pad +1 bottom pad
    }

    /// Insert text at the cursor (paste support).
    pub fn handle_paste(&mut self, text: &str) {
        self.textarea.insert_str(text);
    }

    /// Show an inline picker.
    pub fn show_picker(&mut self, picker: InlinePicker) {
        self.picker = Some(picker);
    }

    /// Start an inline picker (convenience — matches InputBar API).
    pub fn start_picker(&mut self, label: &str, items: Vec<String>, current: Option<&str>) {
        self.picker = Some(InlinePicker::new(label, items, current));
    }

    // -- History navigation ---------------------------------------------------

    fn history_up(&mut self) {
        if self.history.is_empty() {
            return;
        }
        match self.history_idx {
            None => {
                // Save current buffer, jump to last history entry
                self.history_stash = Some(self.text());
                self.history_idx = Some(self.history.len() - 1);
            }
            Some(0) => return, // already at oldest
            Some(idx) => {
                self.history_idx = Some(idx - 1);
            }
        }
        if let Some(idx) = self.history_idx {
            let entry = self.history[idx].clone();
            self.set_text(&entry);
        }
    }

    fn history_down(&mut self) {
        match self.history_idx {
            None => {},
            Some(idx) if idx + 1 >= self.history.len() => {
                // Restore stashed buffer
                self.history_idx = None;
                let stash = self.history_stash.take().unwrap_or_default();
                self.set_text(&stash);
            }
            Some(idx) => {
                self.history_idx = Some(idx + 1);
                let entry = self.history[idx + 1].clone();
                self.set_text(&entry);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn key_mod(code: KeyCode, mods: KeyModifiers) -> KeyEvent {
        KeyEvent::new(code, mods)
    }

    #[test]
    fn submit_returns_text_and_clears() {
        let mut input = TextInput::new();
        input.handle_key(key(KeyCode::Char('h')));
        input.handle_key(key(KeyCode::Char('i')));
        let action = input.handle_key(key(KeyCode::Enter));
        assert_eq!(action, Some(InputAction::Submit("hi".to_string())));
        assert!(input.is_empty());
    }

    #[test]
    fn submit_adds_to_history() {
        let mut input = TextInput::new();
        input.handle_key(key(KeyCode::Char('a')));
        input.handle_key(key(KeyCode::Enter));
        input.handle_key(key(KeyCode::Char('b')));
        input.handle_key(key(KeyCode::Enter));
        assert_eq!(input.history.len(), 2);
        assert_eq!(input.history[0], "a");
        assert_eq!(input.history[1], "b");
    }

    #[test]
    fn history_up_down() {
        let mut input = TextInput::new();
        // Submit three entries
        for text in ["first", "second", "third"] {
            for c in text.chars() {
                input.handle_key(key(KeyCode::Char(c)));
            }
            input.handle_key(key(KeyCode::Enter));
        }

        // Type something new
        input.handle_key(key(KeyCode::Char('x')));

        // Up → third
        input.handle_key(key(KeyCode::Up));
        assert_eq!(input.text(), "third");

        // Up → second
        input.handle_key(key(KeyCode::Up));
        assert_eq!(input.text(), "second");

        // Down → third
        input.handle_key(key(KeyCode::Down));
        assert_eq!(input.text(), "third");

        // Down → restore "x"
        input.handle_key(key(KeyCode::Down));
        assert_eq!(input.text(), "x");
    }

    #[test]
    fn empty_submit_does_not_add_to_history() {
        let mut input = TextInput::new();
        input.handle_key(key(KeyCode::Enter));
        assert!(input.history.is_empty());
    }

    #[test]
    fn escape_returns_cancel() {
        let mut input = TextInput::new();
        let action = input.handle_key(key(KeyCode::Esc));
        assert_eq!(action, Some(InputAction::Cancel));
    }

    #[test]
    fn alt_key_returns_peek() {
        let mut input = TextInput::new();
        let action = input.handle_key(key_mod(KeyCode::Char('f'), KeyModifiers::ALT));
        assert_eq!(action, Some(InputAction::Peek('f')));
    }

    #[test]
    fn picker_enter_selects() {
        let mut input = TextInput::new();
        let picker = InlinePicker::new("Model", vec!["a".into(), "b".into(), "c".into()], Some("b"));
        input.show_picker(picker);
        assert!(input.picker.is_some());

        // Down → c
        input.handle_key(key(KeyCode::Down));
        let action = input.handle_key(key(KeyCode::Enter));
        assert_eq!(action, Some(InputAction::PickerSelect("c".to_string())));
        assert!(input.picker.is_none());
    }

    #[test]
    fn picker_esc_cancels() {
        let mut input = TextInput::new();
        input.show_picker(InlinePicker::new("X", vec!["a".into()], None));
        let action = input.handle_key(key(KeyCode::Esc));
        assert_eq!(action, Some(InputAction::PickerCancel));
        assert!(input.picker.is_none());
    }

    #[test]
    fn set_text_replaces_content() {
        let mut input = TextInput::new();
        input.set_text("hello world");
        assert_eq!(input.text(), "hello world");
    }

    #[test]
    fn picker_wraps_around() {
        let mut picker = InlinePicker::new("X", vec!["a".into(), "b".into(), "c".into()], None);
        assert_eq!(picker.current(), Some("a"));
        picker.prev(); // wrap to end
        assert_eq!(picker.current(), Some("c"));
        picker.next(); // wrap to start
        assert_eq!(picker.current(), Some("a"));
    }
}
