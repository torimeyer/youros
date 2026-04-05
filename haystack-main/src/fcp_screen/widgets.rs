//! fcp-screen ratatui widgets — overlay rendering for modals and peek panels.

use ratatui::{
    layout::Rect,
    style::{Modifier, Style},
    text::Line,
    widgets::{Block, Clear, Paragraph, Wrap},
    Frame,
};

use super::renderer::Overlay;

/// Render an overlay (approval modal or peek panel) on top of the chat area.
///
/// Dims the background, draws a centered bordered box, renders content inside.
pub fn render_overlay(frame: &mut Frame, area: Rect, overlay: &Overlay) {
    // Dim the entire background
    let dim = Block::default().style(Style::default().add_modifier(Modifier::DIM));
    frame.render_widget(dim, area);

    // Size popup to fit content, accounting for line wrapping.
    let width_pct = if overlay.compact { 45u16 } else { 60u16 };
    let popup_width = (area.width as u32 * width_pct as u32 / 100) as u16;
    // Inner width = popup - 2 (left/right border)
    let inner_width = popup_width.saturating_sub(2) as usize;
    let visual_lines: u16 = overlay.content.iter().map(|line| {
        let w = line.width();
        if inner_width == 0 || w == 0 { 1 } else { w.div_ceil(inner_width) as u16 }
    }).sum();
    let max_height = (area.height as u32 * 80 / 100) as u16;
    let desired_height = visual_lines.saturating_add(2); // +2 for border
    let popup_height = desired_height.min(max_height).max(4); // at least 4 rows
    let popup = content_fit_rect(width_pct, popup_height, area);
    frame.render_widget(Clear, popup);

    // Bordered box with title
    let block = Block::bordered()
        .title(Line::from(overlay.title.as_str()).centered())
        .border_style(Style::default().fg(ratatui::style::Color::DarkGray));
    let inner = block.inner(popup);
    frame.render_widget(block, popup);

    // Content is already Vec<Line<'static>> — render directly.
    let content = Paragraph::new(overlay.content.clone()).wrap(Wrap { trim: false });
    frame.render_widget(content, inner);
}

/// Compute a centered rectangle within an area, sized by percentage.
pub fn centered_rect(width_pct: u16, height_pct: u16, area: Rect) -> Rect {
    let popup_width = (area.width as u32 * width_pct as u32 / 100) as u16;
    let popup_height = (area.height as u32 * height_pct as u32 / 100) as u16;
    let x = area.x + (area.width.saturating_sub(popup_width)) / 2;
    let y = area.y + (area.height.saturating_sub(popup_height)) / 2;
    Rect::new(x, y, popup_width.min(area.width), popup_height.min(area.height))
}

/// Compute a centered rectangle with fixed height, width by percentage.
/// Used for overlays that should fit their content rather than fill a percentage.
fn content_fit_rect(width_pct: u16, height: u16, area: Rect) -> Rect {
    let popup_width = (area.width as u32 * width_pct as u32 / 100) as u16;
    let popup_height = height.min(area.height);
    let x = area.x + (area.width.saturating_sub(popup_width)) / 2;
    let y = area.y + (area.height.saturating_sub(popup_height)) / 2;
    Rect::new(x, y, popup_width.min(area.width), popup_height)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn centered_rect_50pct() {
        let area = Rect::new(0, 0, 100, 40);
        let r = centered_rect(50, 50, area);
        assert_eq!(r.width, 50);
        assert_eq!(r.height, 20);
        assert_eq!(r.x, 25);
        assert_eq!(r.y, 10);
    }

    #[test]
    fn centered_rect_100pct() {
        let area = Rect::new(0, 0, 80, 24);
        let r = centered_rect(100, 100, area);
        assert_eq!(r.width, 80);
        assert_eq!(r.height, 24);
        assert_eq!(r.x, 0);
        assert_eq!(r.y, 0);
    }

    #[test]
    fn centered_rect_small_area() {
        let area = Rect::new(5, 3, 10, 6);
        let r = centered_rect(60, 80, area);
        assert_eq!(r.width, 6);
        assert_eq!(r.height, 4); // 6 * 80 / 100 = 4.8 → 4
        assert_eq!(r.x, 7); // 5 + (10-6)/2
        assert_eq!(r.y, 4); // 3 + (6-4)/2
    }

    #[test]
    fn centered_rect_compact_vs_normal() {
        let area = Rect::new(0, 0, 100, 40);
        let normal = centered_rect(60, 80, area);
        let compact = centered_rect(45, 80, area);
        assert!(compact.width < normal.width);
        assert_eq!(compact.height, normal.height); // same height pct
    }
}
