//! Interactive selector overlay — navigable selection list for the fcp-screen TUI.
//!
//! Reusable primitive: mode selection first, then model, thread, needle pickers later.
//! Internally uses ratatui's `ListState` for selection tracking.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::text::Line;
use ratatui::widgets::ListState;
use super::protocol::{self, Color};

/// What this selector controls.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SelectorKind {
    Mode,
    Model,
}

/// A single item in the selector list.
#[derive(Debug, Clone)]
pub struct SelectorItem {
    pub label: String,
    pub value: String,
    pub hint: String,
}

/// Result of handling a key event in the selector.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SelectorAction {
    /// User pressed Enter — value is the selected item's value.
    Select(String),
    /// User pressed Esc or any unrecognized key — dismiss.
    Cancel,
    /// Arrow key handled internally — caller should re-render.
    None,
}

/// Interactive selector with keyboard navigation.
/// Uses ratatui's `ListState` for internal selection management.
pub struct Selector {
    pub on_select: SelectorKind,
    items: Vec<SelectorItem>,
    state: ListState,
    title: String,
}

impl Selector {
    pub fn new(title: &str, items: Vec<SelectorItem>, kind: SelectorKind) -> Self {
        let mut state = ListState::default();
        if !items.is_empty() {
            state.select(Some(0));
        }
        Self {
            title: title.to_string(),
            items,
            state,
            on_select: kind,
        }
    }

    /// Currently selected index (for test compatibility).
    #[cfg(test)]
    fn selected(&self) -> usize {
        self.state.selected().unwrap_or(0)
    }

    /// Handle a key event. Returns the resulting action.
    pub fn handle_key(&mut self, key: KeyEvent) -> SelectorAction {
        match key.code {
            KeyCode::Up => {
                if self.items.is_empty() {
                    return SelectorAction::None;
                }
                let current = self.state.selected().unwrap_or(0);
                let new_idx = if current == 0 {
                    self.items.len() - 1
                } else {
                    current - 1
                };
                self.state.select(Some(new_idx));
                SelectorAction::None
            }
            KeyCode::Down => {
                if self.items.is_empty() {
                    return SelectorAction::None;
                }
                let current = self.state.selected().unwrap_or(0);
                let new_idx = (current + 1) % self.items.len();
                self.state.select(Some(new_idx));
                SelectorAction::None
            }
            KeyCode::Enter => {
                let selected = self.state.selected().unwrap_or(0);
                if let Some(item) = self.items.get(selected) {
                    SelectorAction::Select(item.value.clone())
                } else {
                    SelectorAction::Cancel
                }
            }
            KeyCode::Esc => SelectorAction::Cancel,
            _ => SelectorAction::Cancel,
        }
    }

    /// Render the overlay content as styled lines.
    pub fn render(&self) -> Vec<Line<'static>> {
        let selected = self.state.selected().unwrap_or(0);
        let mut lines = Vec::new();
        lines.push(protocol::line_from_spans(vec![
            protocol::plain("  "),
            protocol::bold(&self.title),
        ]));
        lines.push(protocol::line_from_spans(vec![
            protocol::dim("  \u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}\u{2500}"),
        ]));
        for (i, item) in self.items.iter().enumerate() {
            let is_selected = i == selected;
            let prefix = if is_selected { "\u{25b8} " } else { "  " };
            let label_span = if is_selected {
                protocol::colored(format!("{prefix}{:<14}", item.label), Color::Cyan)
            } else {
                protocol::plain(format!("{prefix}{:<14}", item.label))
            };
            lines.push(protocol::line_from_spans(vec![
                protocol::plain("  "),
                label_span,
                protocol::dim(&item.hint),
            ]));
        }
        lines.push(protocol::line_from_spans(vec![
            protocol::dim("  Enter select  Esc cancel  \u{2191}\u{2193} navigate"),
        ]));
        lines
    }

    /// Build a mode selector with the standard permission modes.
    pub fn mode_selector() -> Self {
        let items = vec![
            SelectorItem { label: "plan".into(), value: "plan".into(), hint: "no tools, text only".into() },
            SelectorItem { label: "governed".into(), value: "governed".into(), hint: "kernel approves tools".into() },
            SelectorItem { label: "auto".into(), value: "auto".into(), hint: "file edits auto-approved".into() },
            SelectorItem { label: "autonomous".into(), value: "autonomous".into(), hint: "full access".into() },
        ];
        Self::new("Mode", items, SelectorKind::Mode)
    }

    /// Build a model selector from a list of available model IDs.
    pub fn model_selector(models: &[String]) -> Self {
        let items: Vec<SelectorItem> = models.iter().map(|m| {
            let hint = if m.contains("opus") { "most capable".into() }
                else if m.contains("sonnet") { "balanced".into() }
                else if m.contains("haiku") { "fastest".into() }
                else { String::new() };
            SelectorItem { label: m.clone(), value: m.clone(), hint }
        }).collect();
        Self::new("Model", items, SelectorKind::Model)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    fn key(code: KeyCode) -> KeyEvent { KeyEvent::new(code, KeyModifiers::NONE) }

    fn test_items() -> Vec<SelectorItem> {
        vec![
            SelectorItem { label: "alpha".into(), value: "a".into(), hint: "first".into() },
            SelectorItem { label: "beta".into(), value: "b".into(), hint: "second".into() },
            SelectorItem { label: "gamma".into(), value: "c".into(), hint: "third".into() },
        ]
    }

    #[test]
    fn creation_and_initial_state() {
        let sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        assert_eq!(sel.selected(), 0);
        assert_eq!(sel.title, "Test");
        assert_eq!(sel.items.len(), 3);
        assert_eq!(sel.on_select, SelectorKind::Mode);
    }

    #[test]
    fn down_moves_selection() {
        let mut sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        assert_eq!(sel.selected(), 0);
        assert_eq!(sel.handle_key(key(KeyCode::Down)), SelectorAction::None);
        assert_eq!(sel.selected(), 1);
        assert_eq!(sel.handle_key(key(KeyCode::Down)), SelectorAction::None);
        assert_eq!(sel.selected(), 2);
    }

    #[test]
    fn down_wraps_to_top() {
        let mut sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        sel.handle_key(key(KeyCode::Down)); // 1
        sel.handle_key(key(KeyCode::Down)); // 2
        sel.handle_key(key(KeyCode::Down)); // wraps to 0
        assert_eq!(sel.selected(), 0);
    }

    #[test]
    fn up_wraps_to_bottom() {
        let mut sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        assert_eq!(sel.selected(), 0);
        assert_eq!(sel.handle_key(key(KeyCode::Up)), SelectorAction::None);
        assert_eq!(sel.selected(), 2); // wraps to last item
    }

    #[test]
    fn up_moves_selection() {
        let mut sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        sel.handle_key(key(KeyCode::Down)); // 1
        sel.handle_key(key(KeyCode::Down)); // 2
        sel.handle_key(key(KeyCode::Up));   // 1
        assert_eq!(sel.selected(), 1);
    }

    #[test]
    fn enter_returns_select_with_correct_value() {
        let mut sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        // Select first item
        assert_eq!(sel.handle_key(key(KeyCode::Enter)), SelectorAction::Select("a".into()));
        // Move to second and select
        sel.handle_key(key(KeyCode::Down));
        assert_eq!(sel.handle_key(key(KeyCode::Enter)), SelectorAction::Select("b".into()));
        // Move to third and select
        sel.handle_key(key(KeyCode::Down));
        assert_eq!(sel.handle_key(key(KeyCode::Enter)), SelectorAction::Select("c".into()));
    }

    #[test]
    fn esc_returns_cancel() {
        let mut sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        assert_eq!(sel.handle_key(key(KeyCode::Esc)), SelectorAction::Cancel);
    }

    #[test]
    fn other_key_returns_cancel() {
        let mut sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        assert_eq!(sel.handle_key(key(KeyCode::Char('x'))), SelectorAction::Cancel);
    }

    #[test]
    fn render_produces_correct_lines() {
        let sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        let lines = sel.render();
        // title + separator + 3 items + footer = 6 lines
        assert_eq!(lines.len(), 6);
        // Title line
        let title_text: String = lines[0].spans.iter().map(|s| s.content.to_string()).collect();
        assert!(title_text.contains("Test"));
        // Separator line
        let sep_text: String = lines[1].spans.iter().map(|s| s.content.to_string()).collect();
        assert!(sep_text.contains("\u{2500}"));
    }

    #[test]
    fn render_highlights_selected_item() {
        let sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        let lines = sel.render();
        // Item 0 (index 2 in lines) should be selected (Cyan + arrow prefix)
        let selected_line = &lines[2];
        let has_cyan = selected_line.spans.iter().any(|s| s.style.fg == Some(Color::Cyan));
        assert!(has_cyan, "Selected item should have Cyan color");
        let text: String = selected_line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("\u{25b8}"), "Selected item should have arrow prefix");

        // Item 1 (index 3) should NOT be Cyan
        let non_selected = &lines[3];
        let non_selected_has_cyan = non_selected.spans.iter().any(|s| s.style.fg == Some(Color::Cyan));
        assert!(!non_selected_has_cyan, "Non-selected item should not have Cyan color");
    }

    #[test]
    fn render_after_navigation_updates_highlight() {
        let mut sel = Selector::new("Test", test_items(), SelectorKind::Mode);
        sel.handle_key(key(KeyCode::Down)); // move to item 1
        let lines = sel.render();
        // Item 1 (index 3 in lines) should now be selected
        let selected_line = &lines[3];
        let has_cyan = selected_line.spans.iter().any(|s| s.style.fg == Some(Color::Cyan));
        assert!(has_cyan, "Item 1 should be highlighted after Down");
        // Item 0 (index 2) should no longer be Cyan
        let prev_line = &lines[2];
        let prev_has_cyan = prev_line.spans.iter().any(|s| s.style.fg == Some(Color::Cyan));
        assert!(!prev_has_cyan, "Item 0 should not be highlighted after moving down");
    }

    #[test]
    fn mode_selector_has_four_items() {
        let sel = Selector::mode_selector();
        assert_eq!(sel.items.len(), 4);
        assert_eq!(sel.on_select, SelectorKind::Mode);
        assert_eq!(sel.items[0].value, "plan");
        assert_eq!(sel.items[1].value, "governed");
        assert_eq!(sel.items[2].value, "auto");
        assert_eq!(sel.items[3].value, "autonomous");
    }

    #[test]
    fn model_selector_from_list() {
        let models = vec!["claude-opus-4-6".into(), "claude-sonnet-4-5-20250929".into()];
        let sel = Selector::model_selector(&models);
        assert_eq!(sel.items.len(), 2);
        assert_eq!(sel.on_select, SelectorKind::Model);
        assert_eq!(sel.items[0].value, "claude-opus-4-6");
        assert!(sel.items[0].hint.contains("capable"));
        assert!(sel.items[1].hint.contains("balanced"));
    }

    #[test]
    fn empty_items_no_crash() {
        let mut sel = Selector::new("Empty", vec![], SelectorKind::Mode);
        assert_eq!(sel.handle_key(key(KeyCode::Up)), SelectorAction::None);
        assert_eq!(sel.handle_key(key(KeyCode::Down)), SelectorAction::None);
        assert_eq!(sel.handle_key(key(KeyCode::Enter)), SelectorAction::Cancel);
        let lines = sel.render();
        // title + separator + footer = 3 lines (no items)
        assert_eq!(lines.len(), 3);
    }

    #[test]
    fn selector_kind_equality() {
        assert_eq!(SelectorKind::Mode, SelectorKind::Mode);
        assert_eq!(SelectorKind::Model, SelectorKind::Model);
        assert_ne!(SelectorKind::Mode, SelectorKind::Model);
    }
}
