//! Busy spinner / working indicator — extracted from app.rs main loop.
//!
//! Encapsulates the braille animation frames, tool-call counter, elapsed-time
//! tracking, and styled-line generation that previously lived inline in the
//! `run_tui` busy-loop (app.rs ~lines 132-190).
//!
//! ## Integration point (app.rs)
//!
//! In `app.rs`, replace the inline spinner state (`spinners`, `spin_idx`,
//! `tool_count`, `work_start`) with a single `SpinnerState` instance:
//!
//! ```rust,ignore
//! // Before the main loop:
//! let mut spinner = SpinnerState::new();
//!
//! // Where work_start = Some(Instant::now()):
//! spinner.start();
//!
//! // Where tool_count += 1:
//! spinner.increment_tools();
//!
//! // In the animate-spinner block (lines ~176-190):
//! if spinner.is_active() {
//!     let mut busy_left = status_cache.build_left_line(&mgr.active);
//!     spinner.prepend_to(&mut busy_left);
//!     // ... dispatch SetStatus with busy_left ...
//! }
//!
//! // Where work_start = None; tool_count = 0:
//! spinner.reset();
//!
//! // In the watchdog timeout block:
//! spinner.reset();
//!
//! // To read elapsed seconds (e.g. turn-complete summary):
//! spinner.elapsed_secs()
//! ```

use std::time::Instant;

use ratatui::text::Line;
use crate::fcp_screen::protocol::{self, Color};

/// Braille dot-spinner frames — 10 positions, one full rotation.
const FRAMES: &[char] = &[
    '\u{280b}', '\u{2819}', '\u{2839}', '\u{2838}',
    '\u{283c}', '\u{2834}', '\u{2826}', '\u{2827}',
    '\u{2807}', '\u{280f}',
];

/// Busy indicator state for the TUI status bar.
///
/// Tracks spinner animation frame, tool-call count, and wall-clock elapsed
/// time since the agent started working.
pub struct SpinnerState {
    frames: &'static [char],
    index: usize,
    tool_count: u32,
    start: Option<Instant>,
    /// Last time an event was received — for stall detection.
    last_event: Option<Instant>,
    /// Last time the animation frame advanced — rate-limits to ~8fps.
    last_tick: Option<Instant>,
}

impl SpinnerState {
    /// Create a new idle spinner (not yet active).
    pub fn new() -> Self {
        Self {
            frames: FRAMES,
            index: 0,
            tool_count: 0,
            start: None,
            last_event: None,
            last_tick: None,
        }
    }

    /// Begin a new busy period — resets counters and records the start time.
    pub fn start(&mut self) {
        let now = Instant::now();
        self.start = Some(now);
        self.last_event = Some(now);
        self.tool_count = 0;
        self.index = 0;
    }

    /// Record that one more tool call was dispatched during this busy period.
    pub fn increment_tools(&mut self) {
        self.tool_count += 1;
    }

    /// End the busy period — clears all state back to idle.
    pub fn reset(&mut self) {
        self.start = None;
        self.last_event = None;
        self.last_tick = None;
        self.tool_count = 0;
        self.index = 0;
    }

    /// Record that an event was received (resets the stall timer).
    pub fn touch(&mut self) {
        self.last_event = Some(Instant::now());
    }

    /// Seconds since the last event was received, or 0 if idle.
    pub fn secs_since_last_event(&self) -> u64 {
        self.last_event.map(|t| t.elapsed().as_secs()).unwrap_or(0)
    }

    /// Advance the animation frame if ≥125ms since last advance (~8fps).
    /// Called by tick() and prepend_to() — keeps the spinner smooth without blur.
    fn maybe_advance_frame(&mut self) {
        let now = Instant::now();
        let should_advance = self.last_tick
            .map(|t| now.duration_since(t).as_millis() >= 125)
            .unwrap_or(true);
        if should_advance {
            self.index = (self.index + 1) % self.frames.len();
            self.last_tick = Some(now);
        }
    }

    /// Whether the spinner is currently active (agent is working).
    pub fn is_active(&self) -> bool {
        self.start.is_some()
    }

    /// Elapsed seconds since the busy period started, or 0 if idle.
    pub fn elapsed_secs(&self) -> u64 {
        self.start.map(|s| s.elapsed().as_secs()).unwrap_or(0)
    }

    /// Current tool count for external reads (e.g. turn-complete summary).
    pub fn tool_count(&self) -> u32 {
        self.tool_count
    }

    /// Current spinner character without advancing the frame.
    pub fn current_frame(&self) -> Option<char> {
        if self.start.is_some() {
            Some(self.frames[self.index])
        } else {
            None
        }
    }

    /// Advance the animation frame (rate-limited to ~8fps) and return the
    /// styled spinner + elapsed text as a Line.
    ///
    /// This is the "left prefix" that gets prepended to the status bar's left
    /// side during busy periods.
    pub fn tick(&mut self) -> Line<'static> {
        self.maybe_advance_frame();
        let elapsed = self.elapsed_secs();
        let tools = if self.tool_count > 0 {
            format!(" {} tools", self.tool_count)
        } else {
            String::new()
        };
        protocol::line_from_spans(vec![
            protocol::colored(format!("{} ", self.frames[self.index]), Color::Yellow),
            protocol::dim(format!("{:>3}s{} ", elapsed, tools)),
        ])
    }

    /// Advance the frame and prepend the spinner + elapsed spans to an
    /// existing Line (typically the status-cache left line).
    ///
    /// This mirrors the original app.rs pattern:
    /// ```rust,ignore
    /// busy_left.spans.insert(0, protocol::dim(format!("{:>3}s{} ", elapsed, tools)));
    /// busy_left.spans.insert(0, protocol::colored(format!("{} ", spinners[spin_idx]), Color::Yellow));
    /// ```
    pub fn prepend_to(&mut self, line: &mut Line<'static>) {
        self.maybe_advance_frame();
        let elapsed = self.elapsed_secs();
        let tools = if self.tool_count > 0 {
            format!(" {} tools", self.tool_count)
        } else {
            String::new()
        };
        line.spans.insert(0, protocol::dim(format!("{:>3}s{} ", elapsed, tools)));
        line.spans.insert(0, protocol::colored(format!("{} ", self.frames[self.index]), Color::Yellow));
    }
}

impl Default for SpinnerState {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_spinner_is_idle() {
        let s = SpinnerState::new();
        assert!(!s.is_active());
        assert_eq!(s.elapsed_secs(), 0);
        assert_eq!(s.tool_count(), 0);
    }

    #[test]
    fn start_activates() {
        let mut s = SpinnerState::new();
        s.start();
        assert!(s.is_active());
    }

    #[test]
    fn reset_returns_to_idle() {
        let mut s = SpinnerState::new();
        s.start();
        s.increment_tools();
        s.increment_tools();
        s.reset();
        assert!(!s.is_active());
        assert_eq!(s.tool_count(), 0);
    }

    #[test]
    fn increment_tools_counts() {
        let mut s = SpinnerState::new();
        s.start();
        s.increment_tools();
        s.increment_tools();
        s.increment_tools();
        assert_eq!(s.tool_count(), 3);
    }

    #[test]
    fn tick_cycles_through_frames() {
        let mut s = SpinnerState::new();
        s.start();
        // Tick 10 times to go through all frames, then one more to wrap
        for _ in 0..FRAMES.len() {
            let _ = s.tick();
        }
        // Index should have wrapped back to 0 after 10 ticks (each tick advances by 1)
        // Frame 0 is at index 0, after 10 ticks: (0+10) % 10 = 0
        let line = s.tick();
        // 11th tick → index 1
        assert_eq!(line.spans.len(), 2);
        // First span is the spinner char (yellow)
        assert!(line.spans[0].style.fg == Some(Color::Yellow));
        // Second span is dim elapsed
        assert!(line.spans[1].style.add_modifier.contains(ratatui::style::Modifier::DIM));
    }

    #[test]
    fn tick_shows_tool_count_when_nonzero() {
        let mut s = SpinnerState::new();
        s.start();
        s.increment_tools();
        s.increment_tools();
        let line = s.tick();
        let dim_text = &line.spans[1].content;
        assert!(dim_text.contains("2 tools"), "expected '2 tools' in {:?}", dim_text);
    }

    #[test]
    fn tick_hides_tool_count_when_zero() {
        let mut s = SpinnerState::new();
        s.start();
        let line = s.tick();
        let dim_text = &line.spans[1].content;
        assert!(!dim_text.contains("tools"), "should not contain 'tools' in {:?}", dim_text);
    }

    #[test]
    fn prepend_to_inserts_at_front() {
        let mut s = SpinnerState::new();
        s.start();
        let mut line = protocol::line_from_spans(vec![
            protocol::plain("existing"),
        ]);
        s.prepend_to(&mut line);
        // Should now have 3 spans: spinner char, elapsed, original
        assert_eq!(line.spans.len(), 3);
        assert!(line.spans[0].style.fg == Some(Color::Yellow));
        assert!(line.spans[1].style.add_modifier.contains(ratatui::style::Modifier::DIM));
        assert_eq!(line.spans[2].content, "existing");
    }

    #[test]
    fn default_matches_new() {
        let a = SpinnerState::new();
        let b = SpinnerState::default();
        assert_eq!(a.is_active(), b.is_active());
        assert_eq!(a.tool_count(), b.tool_count());
        assert_eq!(a.elapsed_secs(), b.elapsed_secs());
    }

    #[test]
    fn start_resets_tool_count_from_previous_session() {
        let mut s = SpinnerState::new();
        s.start();
        s.increment_tools();
        s.increment_tools();
        assert_eq!(s.tool_count(), 2);
        // Starting a new busy period should reset tool count
        s.start();
        assert_eq!(s.tool_count(), 0);
        assert!(s.is_active());
    }
}
