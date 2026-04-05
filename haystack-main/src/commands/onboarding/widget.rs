//! Interactive terminal widgets for onboarding — multi-line selector + toggle list.
//!
//! Uses crossterm directly (already a dep via ratatui). No new crates needed.
//! Falls back to numbered prompts if terminal is too small or not a TTY.

use crossterm::event::{self, Event, KeyCode};
use crossterm::terminal;
use std::io::{self, Write};

/// Ensure raw mode is disabled on drop (panic safety).
struct RawModeGuard;

impl RawModeGuard {
    fn new() -> Result<Self, String> {
        terminal::enable_raw_mode().map_err(|e| format!("enable raw mode: {e}"))?;
        Ok(Self)
    }
}

impl Drop for RawModeGuard {
    fn drop(&mut self) {
        let _ = terminal::disable_raw_mode();
    }
}

// ─── MenuSelector ────────────────────────────────────────────

/// A single item in the selector.
pub struct MenuItem {
    pub label: String,
    pub value: String,
    pub description: String,
}

/// Result of a menu selection.
pub enum MenuResult {
    Selected(String),
    Skipped,
}

/// Multi-line selector — all options visible, arrow keys move `▸` marker.
pub struct MenuSelector {
    title: String,
    step: String,
    items: Vec<MenuItem>,
    selected: usize,
    allow_skip: bool,
}

impl MenuSelector {
    pub fn new(step: &str, title: &str, items: Vec<MenuItem>, allow_skip: bool) -> Self {
        Self {
            title: title.to_string(),
            step: step.to_string(),
            items,
            selected: 0,
            allow_skip,
        }
    }

    /// Pre-select an item by value.
    pub fn preselect(&mut self, value: &str) {
        if let Some(idx) = self.items.iter().position(|i| i.value == value) {
            self.selected = idx;
        }
    }

    /// Run the selector. Blocks until user picks or skips.
    pub fn run(&mut self) -> Result<MenuResult, String> {
        if self.items.is_empty() {
            return Ok(MenuResult::Skipped);
        }

        // Check if we can use raw mode (TTY + enough terminal height)
        let can_raw = std::io::stdin().is_terminal()
            && terminal::size().map(|(_, h)| h as usize >= self.items.len() + 6).unwrap_or(false);

        if can_raw {
            self.run_interactive()
        } else {
            self.run_fallback()
        }
    }

    /// Interactive mode with arrow keys.
    fn run_interactive(&mut self) -> Result<MenuResult, String> {
        let mut out = io::stdout();
        let total_lines = self.render_all(&mut out)?;
        let _guard = RawModeGuard::new()?;

        loop {
            if let Ok(Event::Key(key)) = event::read() {
                match key.code {
                    KeyCode::Up => {
                        if self.selected == 0 {
                            self.selected = self.items.len() - 1;
                        } else {
                            self.selected -= 1;
                        }
                        self.rerender_items(&mut out, total_lines)?;
                    }
                    KeyCode::Down => {
                        self.selected = (self.selected + 1) % self.items.len();
                        self.rerender_items(&mut out, total_lines)?;
                    }
                    KeyCode::Enter => {
                        drop(_guard);
                        self.clear_and_confirm(&mut out, total_lines)?;
                        let val = self.items[self.selected].value.clone();
                        return Ok(MenuResult::Selected(val));
                    }
                    KeyCode::Esc | KeyCode::Char('s') => {
                        if self.allow_skip {
                            drop(_guard);
                            self.clear_and_skip(&mut out, total_lines)?;
                            return Ok(MenuResult::Skipped);
                        }
                    }
                    KeyCode::Char('c') if key.modifiers.contains(crossterm::event::KeyModifiers::CONTROL) => {
                        drop(_guard);
                        // Clear our output
                        for _ in 0..total_lines {
                            write!(out, "\x1b[A\x1b[2K").ok();
                        }
                        write!(out, "\r").ok();
                        out.flush().ok();
                        std::process::exit(130);
                    }
                    _ => {}
                }
            }
        }
    }

    /// Fallback: numbered selection for small terminals or non-TTY.
    fn run_fallback(&mut self) -> Result<MenuResult, String> {
        println!();
        println!("  \x1b[1m{} — {}\x1b[0m", self.step, self.title);
        println!();
        for (i, item) in self.items.iter().enumerate() {
            println!("    \x1b[1m{}\x1b[0m) {:<14} {}", i + 1, item.label, item.description);
        }
        if self.allow_skip {
            println!("    \x1b[1ms\x1b[0m) skip");
        }
        println!();
        print!("  Select: ");
        io::stdout().flush().ok();

        let mut line = String::new();
        io::stdin().read_line(&mut line).ok();
        let choice = line.trim();

        if choice == "s" && self.allow_skip {
            return Ok(MenuResult::Skipped);
        }
        if let Ok(n) = choice.parse::<usize>()
            && n >= 1 && n <= self.items.len()
        {
            let val = self.items[n - 1].value.clone();
            println!("  \x1b[32m✓\x1b[0m {}: {}", self.title, self.items[n - 1].label);
            return Ok(MenuResult::Selected(val));
        }
        // Default to first item
        let val = self.items[0].value.clone();
        println!("  \x1b[32m✓\x1b[0m {}: {}", self.title, self.items[0].label);
        Ok(MenuResult::Selected(val))
    }

    /// Render the full selector block. Returns total lines printed.
    fn render_all(&self, out: &mut impl Write) -> Result<usize, String> {
        let mut lines = 0;
        write!(out, "\r\n").ok(); lines += 1;
        write!(out, "  \x1b[1m{} — {}\x1b[0m\r\n", self.step, self.title).ok(); lines += 1;
        write!(out, "\r\n").ok(); lines += 1;
        for (i, item) in self.items.iter().enumerate() {
            if i == self.selected {
                write!(out, "  \x1b[36m▸ {:<14}\x1b[0m {}\r\n", item.label, item.description).ok();
            } else {
                write!(out, "    {:<14} \x1b[2m{}\x1b[0m\r\n", item.label, item.description).ok();
            }
            lines += 1;
        }
        write!(out, "\r\n").ok(); lines += 1;
        let skip_hint = if self.allow_skip { "  s skip" } else { "" };
        write!(out, "  \x1b[2m↑↓ navigate  Enter select{skip_hint}\x1b[0m\r\n").ok(); lines += 1;
        out.flush().ok();
        Ok(lines)
    }

    /// Re-render just the item lines (move cursor up, overwrite).
    fn rerender_items(&self, out: &mut impl Write, _total_lines: usize) -> Result<(), String> {
        // Move cursor to start of items (total_lines - 2 for footer, then items.len())
        let up = self.items.len() + 2; // items + blank + footer
        for _ in 0..up {
            write!(out, "\x1b[A").ok();
        }
        write!(out, "\r").ok();

        for (i, item) in self.items.iter().enumerate() {
            write!(out, "\x1b[2K").ok(); // clear line
            if i == self.selected {
                write!(out, "  \x1b[36m▸ {:<14}\x1b[0m {}\r\n", item.label, item.description).ok();
            } else {
                write!(out, "    {:<14} \x1b[2m{}\x1b[0m\r\n", item.label, item.description).ok();
            }
        }
        // Re-print blank + footer
        write!(out, "\x1b[2K\r\n").ok();
        let skip_hint = if self.allow_skip { "  s skip" } else { "" };
        write!(out, "\x1b[2K  \x1b[2m↑↓ navigate  Enter select{skip_hint}\x1b[0m\r\n").ok();
        out.flush().ok();
        Ok(())
    }

    /// Clear the selector block and print confirmation line.
    fn clear_and_confirm(&self, out: &mut impl Write, total_lines: usize) -> Result<(), String> {
        for _ in 0..total_lines {
            write!(out, "\x1b[A\x1b[2K").ok();
        }
        write!(out, "\r  \x1b[32m✓\x1b[0m {}: {}\n", self.title, self.items[self.selected].label).ok();
        out.flush().ok();
        Ok(())
    }

    /// Clear and print skip message.
    fn clear_and_skip(&self, out: &mut impl Write, total_lines: usize) -> Result<(), String> {
        for _ in 0..total_lines {
            write!(out, "\x1b[A\x1b[2K").ok();
        }
        write!(out, "\r  \x1b[2m– {}: skipped\x1b[0m\n", self.title).ok();
        out.flush().ok();
        Ok(())
    }
}

// ─── ToggleList ──────────────────────────────────────────────

/// A single toggle item (approved / ask first).
pub struct ToggleItem {
    pub label: String,
    pub value: bool, // true = approved, false = ask first
}

/// Result of the toggle list.
pub struct ToggleResult {
    pub values: Vec<(String, bool)>,
}

/// Toggle list — Up/Down moves between rows, Left/Right toggles.
pub struct ToggleList {
    title: String,
    step: String,
    items: Vec<ToggleItem>,
    selected: usize,
    allow_skip: bool,
}

impl ToggleList {
    pub fn new(step: &str, title: &str, items: Vec<ToggleItem>, allow_skip: bool) -> Self {
        Self {
            title: title.to_string(),
            step: step.to_string(),
            items,
            selected: 0,
            allow_skip,
        }
    }

    pub fn run(&mut self) -> Result<Option<ToggleResult>, String> {
        if self.items.is_empty() {
            return Ok(None);
        }

        let can_raw = std::io::stdin().is_terminal()
            && terminal::size().map(|(_, h)| h as usize >= self.items.len() + 6).unwrap_or(false);

        if can_raw {
            self.run_interactive()
        } else {
            self.run_fallback()
        }
    }

    fn run_interactive(&mut self) -> Result<Option<ToggleResult>, String> {
        let mut out = io::stdout();
        let total_lines = self.render_all(&mut out)?;
        let _guard = RawModeGuard::new()?;

        loop {
            if let Ok(Event::Key(key)) = event::read() {
                match key.code {
                    KeyCode::Up => {
                        if self.selected == 0 {
                            self.selected = self.items.len() - 1;
                        } else {
                            self.selected -= 1;
                        }
                        self.rerender_items(&mut out)?;
                    }
                    KeyCode::Down => {
                        self.selected = (self.selected + 1) % self.items.len();
                        self.rerender_items(&mut out)?;
                    }
                    KeyCode::Left | KeyCode::Right => {
                        self.items[self.selected].value = !self.items[self.selected].value;
                        self.rerender_items(&mut out)?;
                    }
                    KeyCode::Enter => {
                        drop(_guard);
                        self.clear_and_confirm(&mut out, total_lines)?;
                        return Ok(Some(self.result()));
                    }
                    KeyCode::Esc | KeyCode::Char('s') if self.allow_skip => {
                        drop(_guard);
                        self.clear_and_skip(&mut out, total_lines)?;
                        return Ok(None);
                    }
                    KeyCode::Char('c') if key.modifiers.contains(crossterm::event::KeyModifiers::CONTROL) => {
                        drop(_guard);
                        for _ in 0..total_lines {
                            write!(out, "\x1b[A\x1b[2K").ok();
                        }
                        write!(out, "\r").ok();
                        out.flush().ok();
                        std::process::exit(130);
                    }
                    _ => {}
                }
            }
        }
    }

    fn run_fallback(&mut self) -> Result<Option<ToggleResult>, String> {
        println!();
        println!("  \x1b[1m{} — {}\x1b[0m", self.step, self.title);
        println!();
        println!("  Using defaults. Edit HUMANFILE to customize.");
        println!();
        self.print_summary();
        Ok(Some(self.result()))
    }

    fn render_all(&self, out: &mut impl Write) -> Result<usize, String> {
        let mut lines = 0;
        write!(out, "\r\n").ok(); lines += 1;
        write!(out, "  \x1b[1m{} — {}\x1b[0m\r\n", self.step, self.title).ok(); lines += 1;
        write!(out, "\r\n").ok(); lines += 1;
        write!(out, "  \x1b[2mWhat can agents do without asking?\x1b[0m\r\n").ok(); lines += 1;
        write!(out, "\r\n").ok(); lines += 1;
        for (i, item) in self.items.iter().enumerate() {
            let marker = if i == self.selected { "▸" } else { " " };
            let (left, right) = if item.value {
                ("\x1b[32mapproved\x1b[0m", "\x1b[2mask first\x1b[0m")
            } else {
                ("\x1b[2mapproved\x1b[0m", "\x1b[33mask first\x1b[0m")
            };
            write!(out, "  {marker} {:<18} {left}  /  {right}\r\n", item.label).ok();
            lines += 1;
        }
        write!(out, "\r\n").ok(); lines += 1;
        let skip_hint = if self.allow_skip { "  s skip" } else { "" };
        write!(out, "  \x1b[2m↑↓ navigate  ←→ toggle  Enter confirm{skip_hint}\x1b[0m\r\n").ok(); lines += 1;
        out.flush().ok();
        Ok(lines)
    }

    fn rerender_items(&self, out: &mut impl Write) -> Result<(), String> {
        let up = self.items.len() + 2;
        for _ in 0..up {
            write!(out, "\x1b[A").ok();
        }
        write!(out, "\r").ok();

        for (i, item) in self.items.iter().enumerate() {
            let marker = if i == self.selected { "▸" } else { " " };
            let (left, right) = if item.value {
                ("\x1b[32mapproved\x1b[0m", "\x1b[2mask first\x1b[0m")
            } else {
                ("\x1b[2mapproved\x1b[0m", "\x1b[33mask first\x1b[0m")
            };
            write!(out, "\x1b[2K  {marker} {:<18} {left}  /  {right}\r\n", item.label).ok();
        }
        write!(out, "\x1b[2K\r\n").ok();
        let skip_hint = if self.allow_skip { "  s skip" } else { "" };
        write!(out, "\x1b[2K  \x1b[2m↑↓ navigate  ←→ toggle  Enter confirm{skip_hint}\x1b[0m\r\n").ok();
        out.flush().ok();
        Ok(())
    }

    fn clear_and_confirm(&self, out: &mut impl Write, total_lines: usize) -> Result<(), String> {
        for _ in 0..total_lines {
            write!(out, "\x1b[A\x1b[2K").ok();
        }
        write!(out, "\r").ok();
        self.print_summary_to(out);
        out.flush().ok();
        Ok(())
    }

    fn clear_and_skip(&self, out: &mut impl Write, total_lines: usize) -> Result<(), String> {
        for _ in 0..total_lines {
            write!(out, "\x1b[A\x1b[2K").ok();
        }
        write!(out, "\r  \x1b[2m– {}: skipped (using defaults)\x1b[0m\n", self.title).ok();
        out.flush().ok();
        Ok(())
    }

    fn print_summary(&self) {
        let mut out = io::stdout();
        self.print_summary_to(&mut out);
        out.flush().ok();
    }

    fn print_summary_to(&self, out: &mut impl Write) {
        write!(out, "  \x1b[32m✓\x1b[0m Autonomy: ").ok();
        for (i, item) in self.items.iter().enumerate() {
            if i > 0 { write!(out, "  ").ok(); }
            let symbol = if item.value { "✓" } else { "?" };
            // Short label: take first word
            let short = item.label.split_whitespace().next().unwrap_or(&item.label);
            write!(out, "{short} {symbol}").ok();
        }
        writeln!(out).ok();
    }

    fn result(&self) -> ToggleResult {
        ToggleResult {
            values: self.items.iter().map(|i| (i.label.clone(), i.value)).collect(),
        }
    }
}

// ─── Simple yes/no confirm ──────────────────────────────────

/// Simple yes/no confirmation. Returns true for yes, false for no.
pub fn confirm(prompt: &str, default_yes: bool) -> bool {
    let hint = if default_yes { "[Y/n]" } else { "[y/N]" };
    print!("  {} {}: ", prompt, hint);
    io::stdout().flush().ok();
    let mut line = String::new();
    io::stdin().read_line(&mut line).ok();
    let trimmed = line.trim().to_lowercase();
    match trimmed.as_str() {
        "y" | "yes" => true,
        "n" | "no" => false,
        "" => default_yes,
        _ => default_yes,
    }
}

use std::io::IsTerminal;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn menu_selector_preselect() {
        let items = vec![
            MenuItem { label: "A".into(), value: "a".into(), description: "first".into() },
            MenuItem { label: "B".into(), value: "b".into(), description: "second".into() },
        ];
        let mut sel = MenuSelector::new("1/2", "Test", items, true);
        assert_eq!(sel.selected, 0);
        sel.preselect("b");
        assert_eq!(sel.selected, 1);
    }

    #[test]
    fn menu_selector_preselect_unknown() {
        let items = vec![
            MenuItem { label: "A".into(), value: "a".into(), description: "first".into() },
        ];
        let mut sel = MenuSelector::new("1/2", "Test", items, true);
        sel.preselect("z");
        assert_eq!(sel.selected, 0); // unchanged
    }

    #[test]
    fn toggle_result_captures_values() {
        let items = vec![
            ToggleItem { label: "Writes".into(), value: true },
            ToggleItem { label: "Commits".into(), value: false },
        ];
        let list = ToggleList::new("5/7", "Autonomy", items, true);
        let result = list.result();
        assert_eq!(result.values.len(), 2);
        assert_eq!(result.values[0], ("Writes".to_string(), true));
        assert_eq!(result.values[1], ("Commits".to_string(), false));
    }

    #[test]
    fn menu_empty_items_returns_skipped() {
        let mut sel = MenuSelector::new("1/1", "Empty", vec![], true);
        let result = sel.run().unwrap();
        assert!(matches!(result, MenuResult::Skipped));
    }
}
