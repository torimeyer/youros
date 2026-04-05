//! fcp-screen protocol — ratatui-native styled text helpers and display utilities.
//!
//! Thin convenience layer over ratatui types. No custom type system — just
//! ergonomic constructors that return `Span<'static>` and `Line<'static>`.

pub use ratatui::style::Color;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

// ---------------------------------------------------------------------------
// Custom colors (aliases for our palette beyond ratatui's named colors)
// ---------------------------------------------------------------------------

pub const DARK_GREEN: Color = Color::Rgb(80, 140, 80);
pub const DARK_RED: Color = Color::Rgb(180, 80, 80);
/// Map old "Gray" to ratatui's DarkGray (the terminal dim gray).
pub const GRAY: Color = Color::DarkGray;

// ---------------------------------------------------------------------------
// Display width utilities
// ---------------------------------------------------------------------------

/// Terminal display width of a string — accounts for CJK (2 columns),
/// zero-width combiners, etc.
pub fn display_width(s: &str) -> usize {
    UnicodeWidthStr::width(s)
}

/// Terminal display width of a single character.
pub fn char_width(c: char) -> usize {
    UnicodeWidthChar::width(c).unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Span constructors — return Span<'static> (owned String content)
// ---------------------------------------------------------------------------

/// Plain unstyled text span.
pub fn plain(text: impl Into<String>) -> Span<'static> {
    Span::raw(text.into())
}

/// Colored text span.
pub fn colored(text: impl Into<String>, color: Color) -> Span<'static> {
    Span::styled(text.into(), Style::default().fg(color))
}

/// Bold text span.
pub fn bold(text: impl Into<String>) -> Span<'static> {
    Span::styled(text.into(), Style::default().add_modifier(Modifier::BOLD))
}

/// Dim text span.
pub fn dim(text: impl Into<String>) -> Span<'static> {
    Span::styled(text.into(), Style::default().add_modifier(Modifier::DIM))
}

/// Dim + colored text span.
pub fn dim_colored(text: impl Into<String>, color: Color) -> Span<'static> {
    Span::styled(
        text.into(),
        Style::default().fg(color).add_modifier(Modifier::DIM),
    )
}

// ---------------------------------------------------------------------------
// Line constructors — return Line<'static>
// ---------------------------------------------------------------------------

/// A line with a single plain span.
pub fn line_plain(text: impl Into<String>) -> Line<'static> {
    Line::from(plain(text))
}

/// A line with a single colored span.
pub fn line_styled(text: impl Into<String>, color: Color) -> Line<'static> {
    Line::from(colored(text, color))
}

/// An empty line.
pub fn line_empty() -> Line<'static> {
    Line::default()
}

/// Build a line from multiple spans.
pub fn line_from_spans(spans: Vec<Span<'static>>) -> Line<'static> {
    Line::from(spans)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_span() {
        let s = plain("hello");
        assert_eq!(s.content, "hello");
        assert_eq!(s.style, Style::default());
    }

    #[test]
    fn colored_span() {
        let s = colored("warning", Color::Yellow);
        assert_eq!(s.content, "warning");
        assert_eq!(s.style.fg, Some(Color::Yellow));
    }

    #[test]
    fn bold_span() {
        let s = bold("title");
        assert!(s.style.add_modifier.contains(Modifier::BOLD));
    }

    #[test]
    fn dim_span() {
        let s = dim("muted");
        assert!(s.style.add_modifier.contains(Modifier::DIM));
    }

    #[test]
    fn line_constructors() {
        let p = line_plain("hello");
        assert_eq!(p.spans.len(), 1);

        let s = line_styled("red", Color::Red);
        assert_eq!(s.spans.len(), 1);

        let e = line_empty();
        assert!(e.spans.is_empty());

        let multi = line_from_spans(vec![plain("a"), bold("b"), dim("c")]);
        assert_eq!(multi.spans.len(), 3);
    }

    #[test]
    fn display_width_ascii() {
        assert_eq!(display_width("hello"), 5);
    }

    #[test]
    fn display_width_cjk() {
        // CJK characters are 2 columns wide
        assert_eq!(char_width('中'), 2);
    }
}
