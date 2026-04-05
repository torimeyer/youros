//! Session tab bar — shows active sessions when >1 exists.
//!
//! Returns `None` when only one session is present (tab bar hidden).

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};

/// Build the tab bar line from session list.
/// Returns None if only 1 session (hide tabs).
pub fn build_tab_bar(sessions: &[(&str, usize, bool)], active: &str) -> Option<Line<'static>> {
    if sessions.len() <= 1 {
        return None;
    }
    let mut spans = Vec::new();
    for (i, (name, _msg_count, busy)) in sessions.iter().enumerate() {
        if i > 0 {
            spans.push(Span::styled(
                " \u{2502} ".to_string(),
                Style::default().fg(Color::DarkGray),
            ));
        }
        let style = if *name == active {
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(Color::DarkGray)
        };
        let indicator = if *busy { "\u{25cf}" } else { "" };
        spans.push(Span::styled(format!("{indicator}{name}"), style));
    }
    Some(Line::from(spans))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tab_bar_hidden_single_session() {
        let sessions = vec![("scheduler", 5, false)];
        assert!(build_tab_bar(&sessions, "scheduler").is_none());
    }

    #[test]
    fn tab_bar_shows_multiple() {
        let sessions = vec![("scheduler", 5, false), ("worker-1", 3, true)];
        let line = build_tab_bar(&sessions, "scheduler").unwrap();
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("scheduler"));
        assert!(text.contains("worker-1"));
    }

    #[test]
    fn tab_bar_highlights_active() {
        let sessions = vec![("a", 0, false), ("b", 0, false)];
        let line = build_tab_bar(&sessions, "b").unwrap();
        // "b" span should be Yellow+Bold
        let b_span = line.spans.iter().find(|s| s.content.contains("b")).unwrap();
        assert_eq!(b_span.style.fg, Some(Color::Yellow));
    }

    #[test]
    fn tab_bar_busy_indicator() {
        let sessions = vec![("a", 0, false), ("b", 0, true)];
        let line = build_tab_bar(&sessions, "a").unwrap();
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("\u{25cf}b"), "Busy session should have bullet indicator");
        // Non-busy session should not have indicator
        let a_span = line.spans.iter().find(|s| s.content.contains("a")).unwrap();
        assert!(!a_span.content.contains('\u{25cf}'));
    }

    #[test]
    fn tab_bar_non_active_is_dark_gray() {
        let sessions = vec![("a", 0, false), ("b", 0, false)];
        let line = build_tab_bar(&sessions, "a").unwrap();
        let b_span = line.spans.iter().find(|s| s.content.contains("b")).unwrap();
        assert_eq!(b_span.style.fg, Some(Color::DarkGray));
    }

    #[test]
    fn tab_bar_separator_between_tabs() {
        let sessions = vec![("x", 0, false), ("y", 0, false), ("z", 0, false)];
        let line = build_tab_bar(&sessions, "x").unwrap();
        let sep_count = line
            .spans
            .iter()
            .filter(|s| s.content.contains('\u{2502}'))
            .count();
        assert_eq!(sep_count, 2, "Should have 2 separators for 3 tabs");
    }
}
