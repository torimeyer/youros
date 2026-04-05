//! Status bar — cached filesystem reads, formatted status line.
//!
//! StatusCache is the single authoritative in-process view of .ostk/ state.
//! Refreshes every 2 seconds. All counting uses canonical helpers from
//! `commands::helpers` to stay consistent with bootloader, context, and boot.

use std::path::Path;
use std::time::Instant;

use crate::commands::helpers;
use crate::fcp_screen::protocol::{self, Color};
use ratatui::text::{Line, Span};

/// Format token count for compact display (e.g. 1500 → "1k", 1500000 → "1.5M").
pub fn fmt_tokens(n: u64) -> String {
    if n < 1000 { format!("{n}") }
    else if n < 1_000_000 { format!("{}k", n / 1000) }
    else { format!("{:.1}M", n as f64 / 1_000_000.0) }
}

/// Format token count with one decimal for chat-zone display (e.g. 2100 → "2.1k").
pub fn fmt_tokens_f(n: u64) -> String {
    if n < 1000 { format!("{n}") }
    else if n < 10_000 { format!("{:.1}k", n as f64 / 1000.0) }
    else if n < 1_000_000 { format!("{}k", n / 1000) }
    else { format!("{:.1}M", n as f64 / 1_000_000.0) }
}

/// Cached filesystem state for status bar rendering.
/// Avoids reading 4+ files on every spinner frame (~12x/sec).
pub struct StatusCache {
    identity: u64,
    fleet_count: usize,
    open_needles: usize,
    last_refresh: Instant,
    pub context_pct: Option<u8>,  // 0-100, None if no budget set
    pub last_context_tokens: Option<u64>,  // actual context window size from last PreCallTokenCount
}

impl StatusCache {
    pub fn new(root: &Path) -> Self {
        let mut cache = Self {
            identity: 0,
            fleet_count: 0,
            open_needles: 0,
            last_refresh: Instant::now(),
            context_pct: None,
            last_context_tokens: None,
        };
        cache.refresh(root);
        cache
    }

    /// Refresh cached values if stale (>2 seconds since last refresh).
    pub fn maybe_refresh(&mut self, root: &Path) {
        if self.last_refresh.elapsed().as_secs() >= 2 {
            self.refresh(root);
        }
    }

    /// Reset context tracking after :clear or :reboot.
    /// Without this, stale token counts persist in the status bar and context
    /// overlay, making the model (and human) think context is still full.
    pub fn reset_context(&mut self) {
        self.context_pct = None;
        self.last_context_tokens = None;
    }

    fn refresh(&mut self, root: &Path) {
        let hs = crate::state_dir(root);
        self.identity = crate::kernel::identity::read_counter(&hs);
        let (fleet_active, _fleet_total) = helpers::count_fleet(&hs.join("agents.jsonl"));
        self.fleet_count = fleet_active;
        // Canonical definition: active needles = open + in_progress
        // (uses the single canonical count_active_needles() from helpers.rs)
        self.open_needles = helpers::count_active_needles(
            &hs.join("needles").join("issues.jsonl"),
        );
        self.last_refresh = Instant::now();
    }

    /// Build the right-aligned portion: model · living systems · context · cost.
    pub fn build_right_line(&self, session_tokens: &crate::cpu::session::SessionTokens, model: &str, fast_mode: bool) -> Line<'static> {
        let dot = || protocol::dim(" \u{00b7} ");
        let short = super::model_short_name(model);
        let model_color = super::model_badge_color(model);
        let cost = session_tokens.session_cost(model);

        let mut spans = vec![
            protocol::colored(
                if fast_mode { format!("{short}\u{26a1}") } else { short.to_string() },
                model_color,
            ),
        ];

        // Living systems: needles + fleet (always visible)
        spans.push(dot());
        spans.push(protocol::colored(format!("{}\u{2192}", self.open_needles), Color::Yellow));
        spans.push(protocol::dim(" "));
        spans.push(protocol::colored(format!("{}\u{25b2}", self.fleet_count), Color::Green));

        // Context health (only after first API call)
        if let Some(pct) = self.context_pct {
            let ctx_tokens = self.last_context_tokens.unwrap_or(0);
            spans.push(dot());
            spans.extend(build_context_gauge(ctx_tokens, pct));
        }

        // Session cost (hidden until nonzero)
        if cost > 0.001 {
            spans.push(dot());
            spans.push(protocol::dim(format!("${cost:.2}")));
        }

        protocol::line_from_spans(spans)
    }

    /// Build the left-aligned portion: model + session identity.
    /// Format: `opus · scheduler`
    pub fn build_left_line(&self, session_name: &str) -> Line<'static> {
        // Left side is intentionally sparse — spinner prepends when active.
        let spans = vec![
            protocol::dim(format!("@p+{} ", self.identity)),
            protocol::colored(session_name, Color::Cyan),
        ];
        protocol::line_from_spans(spans)
    }

}

/// Build a visual gauge for context window usage with block characters.
fn build_context_gauge(tokens: u64, pct: u8) -> Vec<Span<'static>> {
    let color = if pct >= 90 {
        Color::Red
    } else if pct >= 60 {
        Color::Yellow
    } else {
        Color::Green
    };

    let bar_width = 8; // 8 chars for the gauge
    let filled = ((pct as usize) * bar_width / 100).min(bar_width);
    let empty = bar_width - filled;

    vec![
        protocol::colored(format!("ctx:{}", fmt_tokens(tokens)), color),
        protocol::colored("\u{2588}".repeat(filled), color),
        protocol::dim("\u{2591}".repeat(empty)),
        protocol::colored(format!("{pct}%"), color),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cpu::session::SessionTokens;

    /// Helper: create SessionTokens with uncached input + output only (for legacy tests).
    fn st(input: u64, output: u64) -> SessionTokens {
        SessionTokens { input, output, ..Default::default() }
    }

    #[test]
    fn fmt_tokens_zero() {
        assert_eq!(fmt_tokens(0), "0");
    }

    #[test]
    fn fmt_tokens_hundreds() {
        assert_eq!(fmt_tokens(500), "500");
        assert_eq!(fmt_tokens(999), "999");
    }

    #[test]
    fn fmt_tokens_thousands() {
        assert_eq!(fmt_tokens(1000), "1k");
        assert_eq!(fmt_tokens(1500), "1k");
        assert_eq!(fmt_tokens(42000), "42k");
        assert_eq!(fmt_tokens(999999), "999k");
    }

    #[test]
    fn fmt_tokens_millions() {
        assert_eq!(fmt_tokens(1_000_000), "1.0M");
        assert_eq!(fmt_tokens(1_500_000), "1.5M");
        assert_eq!(fmt_tokens(42_000_000), "42.0M");
    }

    #[test]
    fn fmt_tokens_f_sub_thousand() {
        assert_eq!(fmt_tokens_f(0), "0");
        assert_eq!(fmt_tokens_f(500), "500");
        assert_eq!(fmt_tokens_f(999), "999");
    }

    #[test]
    fn fmt_tokens_f_low_thousands() {
        assert_eq!(fmt_tokens_f(1000), "1.0k");
        assert_eq!(fmt_tokens_f(1500), "1.5k");
        assert_eq!(fmt_tokens_f(2100), "2.1k");
        assert_eq!(fmt_tokens_f(9999), "10.0k");
    }

    #[test]
    fn fmt_tokens_f_high_thousands() {
        assert_eq!(fmt_tokens_f(10000), "10k");
        assert_eq!(fmt_tokens_f(42000), "42k");
        assert_eq!(fmt_tokens_f(999999), "999k");
    }

    #[test]
    fn fmt_tokens_f_millions() {
        assert_eq!(fmt_tokens_f(1_000_000), "1.0M");
        assert_eq!(fmt_tokens_f(1_500_000), "1.5M");
    }

    #[test]
    fn build_right_line_contains_model_and_cost() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join(".ostk")).unwrap();
        let cache = StatusCache::new(tmp.path());
        let line = cache.build_right_line(&st(100_000, 50_000), "claude-opus-4-6", false);
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("opus"), "right line should contain model name, got: {text}");
        assert!(text.contains("$"), "right line should contain cost, got: {text}");
        assert!(text.contains("\u{2192}"), "right line should contain needles arrow, got: {text}");
    }

    #[test]
    fn build_right_line_shows_context_when_set() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join(".ostk")).unwrap();
        let mut cache = StatusCache::new(tmp.path());
        cache.context_pct = Some(42);
        cache.last_context_tokens = Some(85_000);
        let line = cache.build_right_line(&st(0, 0), "claude-sonnet-4-6", false);
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("ctx:85k"), "should show context tokens, got: {text}");
        assert!(text.contains("42%"), "should show context percentage, got: {text}");
        // Should contain gauge characters
        assert!(text.contains('\u{2588}') || text.contains('\u{2591}'),
            "should contain gauge block chars, got: {text}");
    }

    #[test]
    fn build_left_line_contains_session_name() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join(".ostk")).unwrap();
        let cache = StatusCache::new(tmp.path());
        let line = cache.build_left_line("my-thread");
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("my-thread"), "left line should contain session name, got: {text}");
        assert!(text.contains("@p+"), "left line should contain identity, got: {text}");
    }

    #[test]
    fn build_left_line_does_not_contain_model() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join(".ostk")).unwrap();
        let cache = StatusCache::new(tmp.path());
        let line = cache.build_left_line("scheduler");
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(!text.contains("opus"), "left line should not contain model name, got: {text}");
        assert!(!text.contains("sonnet"), "left line should not contain model name, got: {text}");
        assert!(!text.contains("$"), "left line should not contain cost, got: {text}");
    }

    #[test]
    fn build_right_line_fast_mode() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join(".ostk")).unwrap();
        let cache = StatusCache::new(tmp.path());
        let line = cache.build_right_line(&st(0, 0), "claude-opus-4-6", true);
        let text: String = line.spans.iter().map(|s| s.content.to_string()).collect();
        assert!(text.contains("\u{26a1}"), "fast mode should show lightning bolt, got: {text}");
    }

    // -- Context gauge tests --

    #[test]
    fn context_gauge_at_zero() {
        let spans = build_context_gauge(0, 0);
        assert_eq!(spans.len(), 4, "gauge should have 4 spans");
        // Token label
        assert!(spans[0].content.contains("ctx:0"), "should show ctx:0, got: {}", spans[0].content);
        // All empty bars (8 ░)
        assert_eq!(spans[1].content.as_ref(), "", "filled should be empty at 0%");
        assert_eq!(spans[2].content.len(), "\u{2591}".len() * 8, "should have 8 empty blocks");
        // Percentage
        assert!(spans[3].content.contains("0%"), "should show 0%");
        // Color should be green (< 60%)
        assert_eq!(spans[0].style.fg, Some(Color::Green));
    }

    #[test]
    fn context_gauge_at_50() {
        let spans = build_context_gauge(50_000, 50);
        assert_eq!(spans.len(), 4);
        // 50% of 8 = 4 filled, 4 empty
        let filled_count = spans[1].content.chars().filter(|&c| c == '\u{2588}').count();
        let empty_count = spans[2].content.chars().filter(|&c| c == '\u{2591}').count();
        assert_eq!(filled_count, 4, "should have 4 filled blocks at 50%");
        assert_eq!(empty_count, 4, "should have 4 empty blocks at 50%");
        assert!(spans[0].content.contains("ctx:50k"), "should show 50k tokens, got: {}", spans[0].content);
        assert!(spans[3].content.contains("50%"), "should show 50%");
        // Color should be green (< 60%)
        assert_eq!(spans[0].style.fg, Some(Color::Green));
    }

    #[test]
    fn context_gauge_at_100() {
        let spans = build_context_gauge(200_000, 100);
        assert_eq!(spans.len(), 4);
        // All filled (8 █), no empty
        let filled_count = spans[1].content.chars().filter(|&c| c == '\u{2588}').count();
        let empty_count = spans[2].content.chars().filter(|&c| c == '\u{2591}').count();
        assert_eq!(filled_count, 8, "should have 8 filled blocks at 100%");
        assert_eq!(empty_count, 0, "should have 0 empty blocks at 100%");
        assert!(spans[3].content.contains("100%"), "should show 100%");
        // Color should be red (>= 90%)
        assert_eq!(spans[0].style.fg, Some(Color::Red));
        assert_eq!(spans[1].style.fg, Some(Color::Red));
        assert_eq!(spans[3].style.fg, Some(Color::Red));
    }

    #[test]
    fn context_gauge_at_60_is_yellow() {
        let spans = build_context_gauge(120_000, 60);
        assert_eq!(spans[0].style.fg, Some(Color::Yellow), "60% should be yellow");
    }

    #[test]
    fn context_gauge_at_90_is_red() {
        let spans = build_context_gauge(180_000, 90);
        assert_eq!(spans[0].style.fg, Some(Color::Red), "90% should be red");
    }
}
