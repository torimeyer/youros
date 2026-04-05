//! fcp-screen event loop — TEA (The Elm Architecture) display driver for the kernel.
//!
//! All event sources feed a single `std::sync::mpsc` channel (`AppMessage`).
//! The `App` struct owns all mutable state; `update()` handles messages,
//! `render_if_dirty()` paints frames. No sleep in the main loop — a tick
//! timer thread handles frame pacing.

use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::time::Duration;
use crossterm::event::{Event, MouseButton, MouseEvent, MouseEventKind};

/// Re-entrancy guard for the panic hook — prevents infinite recursion if
/// crossterm::execute! panics during terminal restore.
static PANIC_RESTORING: AtomicBool = AtomicBool::new(false);
use crate::agentfile;
use crate::cpu::{config_from_agentfile, PermissionMode};
use crate::cpu::session::SessionManager;
use super::text_input::{InputAction, TextInput};
use super::protocol::{self, Color};
use super::renderer::{AppState, Renderer};
use super::selector::{Selector, SelectorAction, SelectorKind};

use super::action;
use super::clipboard_image;
use super::components::approval;
use crate::kernel::approval as approval_bus;
use super::components::context;
use super::components::dispatch::{self, DispatchResult, resolve_model_alias};
use super::components::overlays;
use super::components::render::render_events;
use super::components::selection;
use super::components::spinner::SpinnerState;
use super::components::sphere_nav;
use super::components::status::StatusCache;

// ─── AppMessage ────────────────────────────────────────────────────────

/// Unified message type — every event source sends through one channel.
pub(crate) enum AppMessage {
    Input(crossterm::event::Event),
    Daemon(super::daemon_poll::DaemonEvent),
    Broadcast(serde_json::Value),
    Tick,
}

// ─── Control ───────────────────────────────────────────────────────────

/// What the main loop does after `update()`.
enum Control {
    Continue,
    Exit,
}

// ─── TuiState ──────────────────────────────────────────────────────────

/// Stored state machine — transitions happen explicitly, never recomputed.
///
/// SpinnerState is folded into non-Idle variants so spinner and TuiState
/// can never drift (e.g. Idle but spinner active, or vice versa).
///
/// Daemon vs embedded is NOT stored here — SessionManager.is_daemon() is
/// the single source of truth. Duplicating it would create drift.
pub(crate) enum TuiState {
    /// No agent processing — accept user input.
    Idle,
    /// Agent is running (local SessionManager or daemon).
    AgentRunning { spinner: SpinnerState },
    /// Agent is blocked waiting for tool approval.
    AwaitingApproval {
        agent_alias: String,
        tool_name: String,
        #[allow(dead_code)]
        tool_input_preview: String,
        daemon_request_id: Option<String>,
        spinner: SpinnerState,
    },
}

impl TuiState {
    fn spinner_mut(&mut self) -> Option<&mut SpinnerState> {
        match self {
            TuiState::Idle => None,
            TuiState::AgentRunning { spinner, .. } => Some(spinner),
            TuiState::AwaitingApproval { spinner, .. } => Some(spinner),
        }
    }

    fn spinner(&self) -> Option<&SpinnerState> {
        match self {
            TuiState::Idle => None,
            TuiState::AgentRunning { spinner, .. } => Some(spinner),
            TuiState::AwaitingApproval { spinner, .. } => Some(spinner),
        }
    }

}

// ─── EventLoopContext ──────────────────────────────────────────────────

/// Persistent context — groups stable references for dispatch.rs compat.
/// Broadcasts go through the unified channel, not stored here.
pub struct EventLoopContext {
    pub root: PathBuf,
    pub scr: AppState,
    pub renderer: Renderer,
    pub inp: TextInput,
    pub mgr: SessionManager,
    pub status_cache: StatusCache,
    pub daemon_poller: Option<super::daemon_poll::DaemonPoller>,
}

// ─── App ───────────────────────────────────────────────────────────────

/// TEA-style application: owns all mutable state, updated via messages.
///
/// ## Dispatch generation protocol (→1134)
///
/// Daemon mode is asynchronous: events from the daemon may arrive after the
/// TUI has already moved on (cancel + re-submit). A generation counter
/// prevents stale events from corrupting the current turn:
///
/// - `dispatch_gen` increments on every dispatch (submit, auto-dispatch of staged_text).
/// - `active_gen` is set to `dispatch_gen` when entering AgentRunning.
/// - `handle_daemon_event` drops events when `active_gen != dispatch_gen` (stale turn).
/// - CancelAgent does NOT increment `dispatch_gen` — daemon's acknowledgment events
///   still match and trigger `enter_idle` correctly.
/// - `cancel_timeout` forces idle if daemon doesn't acknowledge cancel within 15s.
pub(crate) struct App {
    pub ctx: EventLoopContext,
    state: TuiState,
    streaming_buffer: String,
    got_text: bool,
    staged_text: Option<String>,
    selector: Option<Selector>,
    sel_state: selection::SelectionState,
    active_peek: Option<action::ActivePeek>,
    sphere_nav: Option<sphere_nav::SphereNavState>,
    /// Incremented on every dispatch. See generation protocol above.
    dispatch_gen: u64,
    /// Set to dispatch_gen when entering AgentRunning.
    active_gen: u64,
    /// When daemon cancel was requested (for timeout).
    cancel_timeout: Option<std::time::Instant>,
}

impl App {
    // ── Centralized state transition ───────────────────────────────

    fn transition(&mut self, new_state: TuiState) {
        // On-exit old state
        match &self.state {
            TuiState::AgentRunning { .. } | TuiState::AwaitingApproval { .. } => {
                if let Some(ref poller) = self.ctx.daemon_poller {
                    poller.stop_polling();
                }
            }
            _ => {}
        }
        // On-enter new state
        match &new_state {
            TuiState::Idle => {
                // Always drain residual events — fixes missing [done] line
                let mut scratch = SpinnerState::new();
                scratch.start();
                let mut buf = String::new();
                let _rr = render_events(
                    &mut self.ctx.mgr, &mut self.ctx.scr, &mut scratch,
                    &mut self.got_text, &mut self.ctx.status_cache, &mut buf,
                );
                self.refresh_status_bar();
                self.update_tab_bar();
            }
            TuiState::AgentRunning { .. } => {
                self.got_text = false;
                if let Some(ref poller) = self.ctx.daemon_poller {
                    poller.start_polling(&self.ctx.mgr.active);
                }
            }
            TuiState::AwaitingApproval { .. } => {}
        }
        self.state = new_state;
    }

    /// Transition to Idle, then auto-dispatch staged text if present.
    /// Returns true if the caller should exit (DispatchResult::Quit).
    fn enter_idle(&mut self) -> bool {
        self.cancel_timeout = None;
        self.transition(TuiState::Idle);

        // Auto-dispatch staged text AFTER transition returns (avoid re-entrancy)
        if let Some(text) = self.staged_text.take()
            && !text.trim().is_empty() {
                tracing::info!("enter_idle: auto-dispatching staged text ({} chars)", text.len());
                self.dispatch_gen += 1;
                self.cancel_timeout = None;
                self.got_text = false;
                if let Some(DispatchResult::Quit) = dispatch::dispatch_command(
                    &mut self.ctx, &mut self.got_text, &mut self.state, &text,
                ) {
                    approval_bus::deny_all();
                    return true; // caller must exit
                }
                self.enter_running_if_busy();
            }
        false
    }

    /// If session became busy but state is still Idle, transition to AgentRunning.
    fn enter_running_if_busy(&mut self) {
        if self.ctx.mgr.active_session().is_busy() && matches!(self.state, TuiState::Idle) {
            let mut spinner = SpinnerState::new();
            spinner.start();
            self.active_gen = self.dispatch_gen;
            self.transition(TuiState::AgentRunning { spinner });
        }
        // Ensure poller is started if we're already AgentRunning
        if matches!(self.state, TuiState::AgentRunning { .. })
            && let Some(ref poller) = self.ctx.daemon_poller {
                poller.start_polling(&self.ctx.mgr.active);
            }
    }

    fn refresh_status_bar(&mut self) {
        self.ctx.status_cache.maybe_refresh(&self.ctx.root);
        let session = self.ctx.mgr.active_session();
        self.ctx.scr.set_status(
            self.ctx.status_cache.build_left_line(&self.ctx.mgr.active),
            self.ctx.status_cache.build_right_line(
                &session.session_tokens, &session.config.model, session.config.fast_mode,
            ),
        );
    }

    fn update_tab_bar(&mut self) {
        let sessions = self.ctx.mgr.list_sessions();
        self.ctx.scr.tab_bar = super::components::tabs::build_tab_bar(&sessions, &self.ctx.mgr.active);
    }

    // ── update() — TEA message handler ─────────────────────────────

    fn update(&mut self, msg: AppMessage) -> Control {
        match msg {
            AppMessage::Input(event) => self.handle_input(event),
            AppMessage::Daemon(event) => self.handle_daemon_event(event),
            AppMessage::Broadcast(val) => { self.handle_broadcast(val); Control::Continue }
            AppMessage::Tick => self.handle_tick(),
        }
    }

    // ── handle_tick ────────────────────────────────────────────────

    fn handle_tick(&mut self) -> Control {
        let busy_session = self.ctx.mgr.active.clone();

        // Detect idle transition (embedded mode only).
        // Daemon mode: is_busy() is always false locally — rely on handle_daemon_event.
        if matches!(self.state, TuiState::AgentRunning { .. } | TuiState::AwaitingApproval { .. })
            && !self.ctx.mgr.is_daemon()
            && !self.ctx.mgr.active_session().is_busy()
            && self.enter_idle() { return Control::Exit; }

        // Drain embedded events
        match &self.state {
            TuiState::AgentRunning { .. } => {
                let spinner = self.state.spinner_mut().expect("AgentRunning has spinner");
                let rr = render_events(
                    &mut self.ctx.mgr, &mut self.ctx.scr, spinner,
                    &mut self.got_text, &mut self.ctx.status_cache,
                    &mut self.streaming_buffer,
                );
                // Redirect: if staged text waiting and a tool just completed, cancel + redirect
                if rr.tool_completed && self.staged_text.is_some() {
                    let _ = self.ctx.mgr.cancel_active();
                    self.ctx.mgr.active_session_mut().cancel();
                    self.ctx.scr.append_line(
                        protocol::line_styled("[redirecting...]", Color::Yellow));
                    if self.enter_idle() { return Control::Exit; }
                    return Control::Continue;
                }
                if let Some((tool_name, tool_input_preview)) = rr.approval_needed {
                    let session_name = self.ctx.mgr.active.clone();
                    let lines = approval::build_approval_overlay(
                        &session_name, &tool_name, &tool_input_preview,
                    );
                    // Dismiss any peek/navigator — approval must own the overlay
                    self.active_peek = None;
                    self.sphere_nav = None;
                    self.selector = None;
                    self.ctx.scr.show_overlay(lines);
                    let spinner = match std::mem::replace(&mut self.state, TuiState::Idle) {
                        TuiState::AgentRunning { spinner, .. } => spinner,
                        _ => unreachable!(),
                    };
                    self.state = TuiState::AwaitingApproval {
                        agent_alias: session_name,
                        tool_name, tool_input_preview,
                        daemon_request_id: None,
                        spinner,
                    };
                }
            }
            TuiState::AwaitingApproval { .. } => {
                // Drain events while waiting for approval (ignore additional ApprovalNeeded)
                let spinner = self.state.spinner_mut().expect("AwaitingApproval has spinner");
                let _rr = render_events(
                    &mut self.ctx.mgr, &mut self.ctx.scr, spinner,
                    &mut self.got_text, &mut self.ctx.status_cache,
                    &mut self.streaming_buffer,
                );
            }
            TuiState::Idle => {}
        }

        // Watchdog: stall detection
        if !matches!(self.state, TuiState::Idle) {
            if self.state.spinner().is_some_and(|s| s.secs_since_last_event() > 300)
                && self.ctx.mgr.active == busy_session
            {
                let secs = self.state.spinner().map(|s| s.secs_since_last_event()).unwrap_or(0);
                let reason = format!("[stall] no events for {secs}s \u{2014} resetting");
                tracing::warn!("tui: watchdog: {reason}");
                self.ctx.scr.append_line(protocol::line_styled(&reason, Color::Red));
                let _ = crate::append_audit(&self.ctx.root, &serde_json::json!({
                    "event": "watchdog.timeout", "reason": reason,
                    "session": self.ctx.mgr.active, "ts": crate::now_iso(),
                }));
                self.ctx.mgr.active_session_mut().cancel();
                if self.enter_idle() { return Control::Exit; }
            }

            // Animate spinner (skip while approval modal is showing)
            if let TuiState::AgentRunning { ref mut spinner, .. } = self.state {
                let mut busy_left = self.ctx.status_cache.build_left_line(&self.ctx.mgr.active);
                spinner.prepend_to(&mut busy_left);
                let right = self.ctx.scr.status_right.clone();
                self.ctx.scr.set_status(busy_left, right);
            }
        }

        // Auto-deny safety net: overlay dismissed but AwaitingApproval still live
        if !self.ctx.scr.overlay_active()
            && matches!(self.state, TuiState::AwaitingApproval { .. })
        {
            let (agent_alias, tool_name, spinner) = match std::mem::replace(&mut self.state, TuiState::Idle) {
                TuiState::AwaitingApproval { agent_alias, tool_name, spinner, .. } => {
                    (agent_alias, tool_name, spinner)
                }
                _ => unreachable!(),
            };
            self.ctx.mgr.auto_deny_active();
            self.ctx.scr.append_line(protocol::line_from_spans(vec![
                protocol::colored("[denied] ", Color::Red),
                protocol::dim(format!("{}: {} (modal dismissed)", agent_alias, tool_name)),
            ]));
            self.state = TuiState::AgentRunning { spinner };
            if let Some(ref poller) = self.ctx.daemon_poller {
                poller.start_polling(&self.ctx.mgr.active);
            }
        }

        // →1134: Daemon cancel timeout — force idle if no acknowledgment within 15s.
        if let Some(cancel_at) = self.cancel_timeout
            && matches!(self.state, TuiState::AgentRunning { .. })
                && cancel_at.elapsed() > Duration::from_secs(15)
            {
                tracing::warn!("daemon cancel timeout: forcing idle after 15s");
                self.cancel_timeout = None;
                let _ = self.enter_idle();
                self.ctx.scr.append_line(
                    protocol::line_styled("[cancel timeout — forced idle]", Color::Yellow));
                return Control::Continue;
            }

        // →1127: Show inline spinner next to model tag while waiting for inference.
        // Only when no streaming text has arrived yet — once TextDelta flows,
        // it takes over via replace_streaming.
        if matches!(self.state, TuiState::AgentRunning { .. })
            && !self.got_text
            && self.ctx.scr.streaming_line_count() == 0
            && let Some(spinner) = self.state.spinner()
                && let Some(frame) = spinner.current_frame() {
                    let model_id = &self.ctx.mgr.active_session().config.model;
                    let model_tag = super::components::model_short_name(model_id);
                    let badge_color = super::components::model_badge_color(model_id);
                    let elapsed = spinner.elapsed_secs();
                    let tools = spinner.tool_count();
                    let suffix = if tools > 0 {
                        format!(" {elapsed}s {tools} tools")
                    } else {
                        format!(" {elapsed}s")
                    };
                    self.ctx.scr.replace_streaming(vec![
                        protocol::line_plain(""),
                        protocol::line_from_spans(vec![
                            protocol::colored(format!("[{}] ", model_tag), badge_color),
                            protocol::colored(format!("{frame} "), Color::Cyan),
                            protocol::dim(suffix),
                        ]),
                    ]);
                }

        Control::Continue
    }

    // ── handle_daemon_event ────────────────────────────────────────

    fn handle_daemon_event(&mut self, event: super::daemon_poll::DaemonEvent) -> Control {
        // Generation guard: drop stale events from a previous dispatch turn.
        if self.active_gen != self.dispatch_gen {
            tracing::debug!(
                "daemon event dropped: stale generation (active={}, dispatch={})",
                self.active_gen, self.dispatch_gen
            );
            return Control::Continue;
        }
        if matches!(self.state, TuiState::Idle) {
            tracing::debug!("daemon event dropped: TUI already idle");
            return Control::Continue;
        }

        match event {
            super::daemon_poll::DaemonEvent::Events(val) => {
                let mut should_idle = false;
                let mut tool_completed = false;
                if let Some(events) = val.get("events").and_then(|e| e.as_array()) {
                    for ev_val in events {
                        if let Ok(ev) = serde_json::from_value::<crate::cpu::agent_loop::CpuEvent>(ev_val.clone()) {
                            if matches!(ev, crate::cpu::agent_loop::CpuEvent::TurnComplete { .. }
                                | crate::cpu::agent_loop::CpuEvent::Error(_))
                            {
                                should_idle = true;
                            }
                            if matches!(ev, crate::cpu::agent_loop::CpuEvent::ToolResult { .. }) {
                                tool_completed = true;
                            }
                            // Guard: if already Idle (stale cancel from daemon), skip rendering
                            // to avoid spinner panic and stale error messages leaking into new turn
                            if let Some(spinner) = self.state.spinner_mut() {
                                super::components::render::render_single_event(
                                    &ev, &mut self.ctx.mgr, &mut self.ctx.scr, spinner,
                                    &mut self.got_text, &mut self.ctx.status_cache,
                                    &mut self.streaming_buffer,
                                );
                            }
                        }
                    }
                }
                // Redirect: staged text + tool just completed → cancel + redirect
                if tool_completed && !should_idle && self.staged_text.is_some() {
                    // Send cancel to daemon
                    let _ = self.ctx.mgr.cancel_active();
                    self.ctx.scr.append_line(
                        protocol::line_styled("[redirecting...]", Color::Yellow));
                    if self.enter_idle() { return Control::Exit; }
                    return Control::Continue;
                }
                if should_idle {
                    // Guard: if we're already Idle (e.g., user cancelled and
                    // re-submitted before daemon processed the cancel), don't
                    // enter_idle again — it would kill the new submission.
                    if !matches!(self.state, TuiState::Idle)
                        && self.enter_idle() { return Control::Exit; }
                    return Control::Continue;
                }
                // Check for approval requests forwarded from daemon
                if !matches!(self.state, TuiState::AwaitingApproval { .. })
                    && let Some(approval_val) = val.get("approval")
                {
                    let agent_alias = approval_val["agent_alias"].as_str().unwrap_or("").to_string();
                    let tool_name = approval_val["tool_name"].as_str().unwrap_or("").to_string();
                    let tool_input_preview = approval_val["tool_input_preview"].as_str().unwrap_or("").to_string();
                    let daemon_id = approval_val["id"].as_str().unwrap_or("").to_string();
                    let lines = approval::build_approval_overlay(
                        &agent_alias, &tool_name, &tool_input_preview,
                    );
                    // Dismiss any peek/navigator — approval must own the overlay
                    self.active_peek = None;
                    self.sphere_nav = None;
                    self.selector = None;
                    self.ctx.scr.show_overlay(lines);
                    let spinner = match std::mem::replace(&mut self.state, TuiState::Idle) {
                        TuiState::AgentRunning { spinner, .. } => spinner,
                        TuiState::AwaitingApproval { spinner, .. } => spinner,
                        _ => SpinnerState::new(),
                    };
                    self.state = TuiState::AwaitingApproval {
                        agent_alias, tool_name, tool_input_preview,
                        daemon_request_id: Some(daemon_id),
                        spinner,
                    };
                }
                Control::Continue
            }
            super::daemon_poll::DaemonEvent::Lost => {
                self.ctx.daemon_poller = None;
                self.ctx.mgr.drop_daemon();
                self.ctx.scr.append_line(
                    protocol::line_styled("[daemon lost \u{2014} switched to embedded mode]", Color::Yellow));
                if self.enter_idle() { return Control::Exit; }
                Control::Continue
            }
        }
    }

    // ── handle_broadcast ───────────────────────────────────────────

    fn handle_broadcast(&mut self, msg: serde_json::Value) {
        let summary = if let Some(event) = msg.get("event").and_then(|v| v.as_str()) {
            if let Some(detail) = msg.get("detail").and_then(|v| v.as_str()) {
                format!("[broadcast] {event}: {detail}")
            } else {
                format!("[broadcast] {event}")
            }
        } else {
            let s = msg.to_string();
            let truncated: String = s.chars().take(80).collect();
            format!("[broadcast] {truncated}")
        };
        self.ctx.scr.debug_log(protocol::line_styled(summary, Color::Cyan));
    }

    // ── handle_input ───────────────────────────────────────────────

    fn handle_input(&mut self, event: Event) -> Control {
        let key = match event {
            Event::Key(k) => k,
            Event::Mouse(MouseEvent { kind: MouseEventKind::ScrollUp, .. }) => {
                if matches!(self.state, TuiState::AwaitingApproval { .. }) { return Control::Continue; }
                self.sel_state.clear();
                self.ctx.scr.scroll_up(1);
                return Control::Continue;
            }
            Event::Mouse(MouseEvent { kind: MouseEventKind::ScrollDown, .. }) => {
                if matches!(self.state, TuiState::AwaitingApproval { .. }) { return Control::Continue; }
                self.sel_state.clear();
                self.ctx.scr.scroll_down(1);
                return Control::Continue;
            }
            Event::Mouse(MouseEvent { kind: MouseEventKind::Down(MouseButton::Left), column, row, .. }) => {
                if matches!(self.state, TuiState::AwaitingApproval { .. }) { return Control::Continue; }
                if row <= self.ctx.scr.chat_bottom_row() {
                    self.sel_state.start(column, row, &self.ctx.scr);
                    self.ctx.scr.mark_dirty();
                }
                return Control::Continue;
            }
            Event::Mouse(MouseEvent { kind: MouseEventKind::Drag(MouseButton::Left), column, row, .. }) => {
                if matches!(self.state, TuiState::AwaitingApproval { .. }) { return Control::Continue; }
                if self.sel_state.active {
                    if row == 0 {
                        self.ctx.scr.scroll_up(1);
                    } else if row >= self.ctx.scr.chat_bottom_row() {
                        self.ctx.scr.scroll_down(1);
                    }
                    self.sel_state.extend(column, row.min(self.ctx.scr.chat_bottom_row()), &self.ctx.scr);
                    self.ctx.scr.mark_dirty();
                }
                return Control::Continue;
            }
            Event::Mouse(MouseEvent { kind: MouseEventKind::Up(MouseButton::Left), column, row, .. }) => {
                if matches!(self.state, TuiState::AwaitingApproval { .. }) { return Control::Continue; }
                if self.sel_state.active {
                    self.sel_state.extend(column, row.min(self.ctx.scr.chat_bottom_row()), &self.ctx.scr);
                    self.sel_state.finish();
                    self.ctx.scr.mark_dirty();
                }
                return Control::Continue;
            }
            Event::Mouse(_) => return Control::Continue,
            Event::Paste(text) => {
                self.ctx.inp.handle_paste(&text);
                self.render_frame();
                return Control::Continue;
            }
            Event::Resize(c, r) => {
                self.ctx.scr.resize(c, r);
                self.refresh_status_bar();
                self.ctx.scr.mark_dirty();
                return Control::Continue;
            }
            Event::FocusLost => {
                self.ctx.scr.mark_dirty();
                return Control::Continue;
            }
            Event::FocusGained => {
                self.ctx.scr.mark_dirty();
                return Control::Continue;
            }
        };

        self.handle_key(key)
    }

    // ── handle_key ─────────────────────────────────────────────────

    fn handle_key(&mut self, key: crossterm::event::KeyEvent) -> Control {
        let sphere_nav_active = self.sphere_nav.is_some();
        let mode = action::compute_input_mode(&self.state, self.ctx.scr.overlay_active(), self.selector.is_some(), sphere_nav_active);
        let classify_ctx = action::ClassifyCtx {
            has_selection: self.sel_state.has_selection(),
            input_buffer_empty: self.ctx.inp.is_empty(),
            picker_active: self.ctx.inp.picker.is_some(),
            debug_mode: self.ctx.scr.debug_mode(),
            focused_panel: self.ctx.scr.focused_panel(),
            half_page: (self.ctx.scr.chat_bottom_row() as usize).div_ceil(2),
            sphere_nav_active,
        };
        if let Some(act) = action::classify_input(key, mode, &classify_ctx) {
            match self.dispatch_action(act) {
                action::LoopControl::Exit => return Control::Exit,
                action::LoopControl::Continue => return Control::Continue,
                action::LoopControl::Render => {
                    self.ctx.scr.mark_dirty();
                }
            }
        }
        Control::Continue
    }

    // ── toggle_peek ────────────────────────────────────────────────

    fn toggle_peek(
        &mut self,
        peek_kind: action::ActivePeek,
        show: impl FnOnce(&mut AppState),
    ) -> action::LoopControl {
        if self.active_peek == Some(peek_kind) {
            self.ctx.scr.dismiss_overlay();
            self.active_peek = None;
        } else {
            if self.ctx.scr.overlay_active() {
                self.ctx.scr.dismiss_overlay();
            }
            show(&mut self.ctx.scr);
            self.active_peek = Some(peek_kind);
            self.selector = None;
            self.sphere_nav = None;
        }
        self.ctx.scr.mark_dirty();
        action::LoopControl::Render
    }

    // ── dispatch_action ────────────────────────────────────────────

    fn dispatch_action(&mut self, act: action::Action) -> action::LoopControl {
        use action::{Action, LoopControl, ActivePeek};

        match act {
            // ── Global Layer 1 ──

            Action::CopySelection => {
                if let Some(sel) = self.sel_state.range {
                    let text = selection::extract_text(&self.ctx.scr, sel);
                    if !text.is_empty() {
                        selection::copy_to_clipboard(&text);
                        let char_count = text.len();
                        let session = self.ctx.mgr.active_session();
                        self.ctx.status_cache.maybe_refresh(&self.ctx.root);
                        self.ctx.scr.set_status(
                            protocol::line_from_spans(vec![protocol::colored(
                                format!("[copied {char_count} chars]"), Color::Green)]),
                            self.ctx.status_cache.build_right_line(&session.session_tokens, &session.config.model, session.config.fast_mode),
                        );
                    }
                    self.sel_state.clear();
                    self.ctx.scr.mark_dirty();
                }
                LoopControl::Continue
            }

            Action::Quit => {
                tracing::info!("exit: Quit action");
                if !matches!(self.state, TuiState::Idle) {
                    let _ = self.ctx.mgr.cancel_active();
                }
                approval_bus::deny_all();
                LoopControl::Exit
            }

            Action::CopySelectionLegacy => {
                if let Some(sel) = self.sel_state.range {
                    let text = selection::extract_text(&self.ctx.scr, sel);
                    if !text.is_empty() {
                        selection::copy_to_clipboard(&text);
                        let char_count = text.len();
                        let session = self.ctx.mgr.active_session();
                        self.ctx.status_cache.maybe_refresh(&self.ctx.root);
                        self.ctx.scr.set_status(
                            protocol::line_from_spans(vec![protocol::colored(
                                format!("[copied {char_count} chars]"), Color::Green)]),
                            self.ctx.status_cache.build_right_line(&session.session_tokens, &session.config.model, session.config.fast_mode),
                        );
                    }
                    self.sel_state.clear();
                    self.ctx.scr.mark_dirty();
                }
                LoopControl::Continue
            }

            // ── Global Layer 2 — Overlay toggles ──

            Action::ShowContextOverlay => {
                let mgr = &self.ctx.mgr;
                let status_cache = &self.ctx.status_cache;
                let lines = context::build_context_overlay(mgr, status_cache);
                self.toggle_peek(ActivePeek::Context, |scr| {
                    scr.show_overlay_compact(lines);
                })
            }

            Action::ShowFleetOverlay => {
                let root = self.ctx.root.clone();
                self.toggle_peek(ActivePeek::Fleet, |scr| {
                    overlays::show_fleet(&root, scr);
                })
            }

            Action::ShowWorkOverlay => {
                let root = self.ctx.root.clone();
                self.toggle_peek(ActivePeek::Work, |scr| {
                    overlays::show_work(&root, scr);
                })
            }

            Action::ShowHelpOverlay => {
                let model = self.ctx.mgr.active_session().config.model.clone();
                let perm_label = self.ctx.mgr.active_session().config.permission_mode.label().to_string();
                self.toggle_peek(ActivePeek::Help, |scr| {
                    overlays::show_help(scr, &model, &perm_label);
                })
            }

            Action::ShowModelPicker => {
                if self.active_peek == Some(ActivePeek::Model) {
                    self.ctx.scr.dismiss_overlay();
                    self.active_peek = None;
                    self.selector = None;
                } else {
                    if self.ctx.scr.overlay_active() {
                        self.ctx.scr.dismiss_overlay();
                    }
                    if self.ctx.mgr.available_models.is_empty() {
                        self.ctx.scr.append_line(
                            protocol::line_styled("No models available (API key missing or fetch failed).", Color::Red));
                        self.active_peek = None;
                        self.selector = None;
                    } else {
                        let sel = Selector::model_selector(&self.ctx.mgr.available_models);
                        self.ctx.scr.show_overlay(sel.render());
                        self.selector = Some(sel);
                        self.active_peek = Some(ActivePeek::Model);
                    }
                }
                self.ctx.scr.mark_dirty();
                LoopControl::Render
            }

            Action::ShowModePicker => {
                if self.active_peek == Some(ActivePeek::Mode) {
                    self.ctx.scr.dismiss_overlay();
                    self.active_peek = None;
                    self.selector = None;
                } else {
                    if self.ctx.scr.overlay_active() {
                        self.ctx.scr.dismiss_overlay();
                    }
                    let sel = Selector::mode_selector();
                    self.ctx.scr.show_overlay(sel.render());
                    self.selector = Some(sel);
                    self.active_peek = Some(ActivePeek::Mode);
                }
                self.ctx.scr.mark_dirty();
                LoopControl::Render
            }

            Action::ShowDebugOverlay => {
                self.toggle_peek(ActivePeek::Debug, |scr| {
                    if scr.debug_mode() {
                        let debug_lines = scr.debug_lines();
                        let start = debug_lines.len().saturating_sub(20);
                        let mut overlay = vec![protocol::line_styled("  Debug Log  ", Color::Yellow)];
                        for line in &debug_lines[start..] {
                            overlay.push(line.clone());
                        }
                        overlay.push(protocol::line_from_spans(vec![protocol::dim("  press any key to dismiss")]));
                        scr.show_overlay(overlay);
                    }
                })
            }

            Action::ShowSphereNavigator => {
                if self.sphere_nav.is_some() {
                    // Toggle off
                    self.sphere_nav = None;
                    self.ctx.scr.dismiss_overlay();
                    self.active_peek = None;
                } else {
                    // Build state and show
                    if self.ctx.scr.overlay_active() {
                        self.ctx.scr.dismiss_overlay();
                    }
                    let state = sphere_nav::build_state(&self.ctx.root);
                    let lines = sphere_nav::render(&state, self.ctx.scr.cols);
                    self.sphere_nav = Some(state);
                    self.ctx.scr.show_overlay(lines);
                    self.active_peek = Some(ActivePeek::SphereNavigator);
                    self.selector = None;
                }
                self.ctx.scr.mark_dirty();
                LoopControl::Render
            }

            Action::SphereNavKey(key) => {
                use crossterm::event::KeyCode;
                if let Some(ref mut nav) = self.sphere_nav {
                    // Search mode: keys go to search input
                    if sphere_nav::is_searching(nav) {
                        match key.code {
                            KeyCode::Esc => sphere_nav::search_cancel(nav),
                            KeyCode::Enter => sphere_nav::search_confirm(nav),
                            KeyCode::Backspace => sphere_nav::search_pop(nav),
                            KeyCode::Tab | KeyCode::Down => sphere_nav::search_next(nav),
                            KeyCode::Char(ch) => sphere_nav::search_push(nav, ch),
                            _ => {}
                        }
                        // Re-render
                        if let Some(ref nav) = self.sphere_nav {
                            let lines = sphere_nav::render(nav, self.ctx.scr.cols);
                            self.ctx.scr.show_overlay(lines);
                        }
                        self.ctx.scr.mark_dirty();
                        return LoopControl::Render;
                    }

                    match key.code {
                        KeyCode::Up | KeyCode::Char('k') => {
                            sphere_nav::cursor_up(nav);
                        }
                        KeyCode::Down | KeyCode::Char('j') if nav.pending_joint.is_none() => {
                            sphere_nav::cursor_down(nav);
                        }
                        KeyCode::Enter => {
                            if nav.level == sphere_nav::ViewLevel::Galaxy {
                                if nav.cursor < nav.spheres.len() {
                                    let idx = nav.cursor;
                                    let root = self.ctx.root.clone();
                                    sphere_nav::load_sphere_detail(nav, &root, idx);
                                } else if nav.isolated_count > 0 {
                                    nav.level = sphere_nav::ViewLevel::IsolatedList;
                                    nav.cursor = 0;
                                }
                            } else if matches!(nav.level, sphere_nav::ViewLevel::SphereDetail | sphere_nav::ViewLevel::IsolatedList) {
                                if let Some(nid) = sphere_nav::cursor_needle_id(nav) {
                                    let root = self.ctx.root.clone();
                                    sphere_nav::load_needle_detail(nav, &root, &nid);
                                }
                            }
                        }
                        KeyCode::Char('j') => {
                            // j-key: start or complete joint
                            if let Some(cursor_id) = sphere_nav::cursor_needle_id(nav) {
                                if let Some(pending) = nav.pending_joint.take() {
                                    // Complete joint
                                    if pending.source_id != cursor_id {
                                        let root = self.ctx.root.clone();
                                        let result = std::process::Command::new("ostk")
                                            .args(["work", "link", &pending.source_id, "depends-on", &cursor_id])
                                            .current_dir(&root)
                                            .output();
                                        let msg = match result {
                                            Ok(o) if o.status.success() => {
                                                format!("linked {} → {}", pending.source_id, cursor_id)
                                            }
                                            Ok(o) => {
                                                let err = String::from_utf8_lossy(&o.stderr);
                                                format!("link failed: {}", err.trim())
                                            }
                                            Err(e) => format!("link error: {e}"),
                                        };
                                        // Rebuild state to reflect new joint
                                        *nav = sphere_nav::build_state(&root);
                                        // Show feedback in status bar
                                        self.ctx.scr.set_status(
                                            protocol::line_from_spans(vec![
                                                protocol::colored(msg, Color::Green),
                                            ]),
                                            protocol::line_plain(""),
                                        );
                                    } else {
                                        // Source == target, cancel
                                        nav.pending_joint = None;
                                    }
                                } else {
                                    // Start joint
                                    let title = match nav.level {
                                        sphere_nav::ViewLevel::Galaxy => {
                                            nav.spheres.get(nav.cursor)
                                                .map(|s| s.point_title.clone())
                                                .unwrap_or_default()
                                        }
                                        sphere_nav::ViewLevel::SphereDetail => {
                                            nav.detail_needles.get(nav.cursor)
                                                .map(|n| n.title.clone())
                                                .unwrap_or_default()
                                        }
                                        sphere_nav::ViewLevel::IsolatedList => {
                                            nav.isolated_needles.get(nav.cursor)
                                                .map(|n| n.title.clone())
                                                .unwrap_or_default()
                                        }
                                        sphere_nav::ViewLevel::NeedleDetail => {
                                            nav.focused_needle.as_ref()
                                                .map(|n| n.title.clone())
                                                .unwrap_or_default()
                                        }
                                    };
                                    nav.pending_joint = Some(sphere_nav::PendingJoint {
                                        source_id: cursor_id,
                                        source_title: title,
                                    });
                                }
                            }
                        }
                        KeyCode::Esc => {
                            if nav.pending_joint.is_some() {
                                nav.pending_joint = None;
                            } else if !matches!(nav.level, sphere_nav::ViewLevel::Galaxy) {
                                sphere_nav::back(nav);
                            } else {
                                self.sphere_nav = None;
                                self.ctx.scr.dismiss_overlay();
                                self.active_peek = None;
                                self.ctx.scr.mark_dirty();
                                return LoopControl::Render;
                            }
                        }
                        KeyCode::Char('p') if nav.level == sphere_nav::ViewLevel::NeedleDetail => {
                            // Cycle priority: P0 → P1 → P2 → P0
                            if let Some(ref mut nd) = nav.focused_needle {
                                let new_pri = match nd.priority.as_str() {
                                    "P0" => "P1", "P1" => "P2", _ => "P0",
                                };
                                let root = self.ctx.root.clone();
                                sphere_nav::set_needle_priority(&root, &nd.id, new_pri);
                                nd.priority = new_pri.to_string();
                            }
                        }
                        KeyCode::Char('x') if nav.level == sphere_nav::ViewLevel::NeedleDetail => {
                            // Close the needle
                            if let Some(ref nd) = nav.focused_needle {
                                let root = self.ctx.root.clone();
                                sphere_nav::close_needle(&root, &nd.id);
                                // Back out and rebuild state
                                sphere_nav::back(nav);
                                *nav = sphere_nav::build_state(&root);
                                self.ctx.scr.set_status(
                                    protocol::line_from_spans(vec![
                                        protocol::colored("[closed]", Color::Yellow),
                                    ]),
                                    protocol::line_plain(""),
                                );
                            }
                        }
                        KeyCode::Char('/') if nav.level != sphere_nav::ViewLevel::NeedleDetail => {
                            sphere_nav::search_start(nav);
                        }
                        KeyCode::Char('<') | KeyCode::Char(',') => {
                            sphere_nav::time_window_narrower(nav);
                        }
                        KeyCode::Char('>') | KeyCode::Char('.') => {
                            sphere_nav::time_window_wider(nav);
                        }
                        KeyCode::Char('q') => {
                            self.sphere_nav = None;
                            self.ctx.scr.dismiss_overlay();
                            self.active_peek = None;
                            self.ctx.scr.mark_dirty();
                            return LoopControl::Render;
                        }
                        _ => {}
                    }
                    // Re-render the navigator overlay
                    if let Some(ref nav) = self.sphere_nav {
                        let lines = sphere_nav::render(nav, self.ctx.scr.cols);
                        self.ctx.scr.show_overlay(lines);
                    }
                }
                self.ctx.scr.mark_dirty();
                LoopControl::Render
            }

            Action::SwitchTabPrev | Action::SwitchTabNext => {
                let sessions = self.ctx.mgr.list_sessions();
                if sessions.len() > 1 {
                    let names: Vec<String> = sessions.iter().map(|(n, _, _)| n.to_string()).collect();
                    let current_idx = names.iter().position(|n| n == &self.ctx.mgr.active).unwrap_or(0);
                    let new_idx = if matches!(act, Action::SwitchTabPrev) {
                        if current_idx == 0 { names.len() - 1 } else { current_idx - 1 }
                    } else {
                        (current_idx + 1) % names.len()
                    };
                    self.ctx.mgr.switch(&names[new_idx]);
                    self.refresh_status_bar();
                    self.update_tab_bar();
                }
                LoopControl::Render
            }

            // ── Global Layer 3 ──

            Action::CancelAgent => {
                tracing::info!("cancel: Esc pressed during AgentRunning");
                if self.staged_text.is_some() {
                    self.staged_text.take();
                    return LoopControl::Render;
                }
                let _ = self.ctx.mgr.cancel_active();
                if self.ctx.scr.overlay_active() {
                    self.ctx.scr.dismiss_overlay();
                }
                self.active_peek = None;
                self.sphere_nav = None;

                if self.ctx.mgr.is_daemon() {
                    // Daemon mode: cancel is async. Stay in AgentRunning — daemon will
                    // send Error("cancelled by operator") + TurnComplete, which triggers
                    // enter_idle() via handle_daemon_event. If the user submits a new
                    // prompt before then, it goes to staged_text and auto-dispatches
                    // on idle (dispatch_gen increment makes old events stale).
                    self.cancel_timeout = Some(std::time::Instant::now());
                    self.ctx.scr.append_line(
                        protocol::line_styled("[cancelling...]", Color::Yellow));
                    self.ctx.scr.scroll_to_bottom();
                } else {
                    // Embedded mode: cancel is synchronous (flag + abort).
                    let _ = self.enter_idle();
                    self.ctx.scr.append_line(
                        protocol::line_styled("[cancelled]", Color::Yellow));
                    self.ctx.scr.scroll_to_bottom();
                }
                LoopControl::Continue
            }

            Action::PasteClipboard => {
                match clipboard_image::grab_clipboard_image() {
                    clipboard_image::ClipboardContent::Image(path) => {
                        match dispatch::load_image_file(path.to_str().unwrap_or("")) {
                            Ok((media_type, data, size, dims)) => {
                                let size_str = if size > 1_000_000 {
                                    format!("{:.1}MB", size as f64 / 1_000_000.0)
                                } else {
                                    format!("{:.0}KB", size as f64 / 1_000.0)
                                };
                                let dim_str = dims.map(|(w, h)| format!(" {w}\u{00d7}{h}")).unwrap_or_default();
                                let block = crate::cpu::anthropic::ContentBlock::Image {
                                    source: crate::cpu::anthropic::ImageSource {
                                        source_type: "base64".into(),
                                        media_type,
                                        data,
                                    },
                                };
                                self.ctx.mgr.active_session_mut().pending_images.push(block);
                                let count = self.ctx.mgr.active_session().pending_images.len();
                                self.ctx.scr.append_line(protocol::line_from_spans(vec![
                                    protocol::colored(format!("[image #{count}] "), Color::Cyan),
                                    protocol::plain(format!("clipboard \u{00b7} {size_str}{dim_str}")),
                                ]));
                            }
                            Err(e) => {
                                self.ctx.scr.append_line(
                                    protocol::line_styled(format!("[image] clipboard error: {e}"), Color::Red));
                            }
                        }
                        let _ = std::fs::remove_file(&path);
                    }
                    clipboard_image::ClipboardContent::Text(text) => {
                        self.ctx.inp.handle_paste(&text);
                    }
                    clipboard_image::ClipboardContent::Empty => {}
                }
                self.ctx.scr.mark_dirty();
                LoopControl::Continue
            }

            // ── Mode-specific ──

            Action::ApprovalDecision { decision, label, color } => {
                let (agent_alias, tool_name, daemon_request_id, spinner) = match std::mem::replace(&mut self.state, TuiState::Idle) {
                    TuiState::AwaitingApproval { agent_alias, tool_name, daemon_request_id, spinner, .. } => {
                        (agent_alias, tool_name, daemon_request_id, spinner)
                    }
                    other => {
                        self.state = other;
                        return LoopControl::Continue;
                    }
                };
                self.state = TuiState::AgentRunning { spinner };
                if let Some(ref poller) = self.ctx.daemon_poller {
                    poller.start_polling(&self.ctx.mgr.active);
                }

                let _ = self.ctx.mgr.approve_active(decision, daemon_request_id.as_deref());
                self.ctx.scr.dismiss_overlay();
                self.ctx.scr.scroll_to_bottom();
                let suffix = format!("{}: {}", agent_alias, tool_name);
                self.ctx.scr.append_line(protocol::line_from_spans(vec![
                    protocol::colored(label, color),
                    protocol::dim(suffix),
                ]));
                self.active_peek = None;
                self.ctx.scr.mark_dirty();
                LoopControl::Continue
            }

            Action::SelectorKey(key) => {
                if let Some(sel) = &mut self.selector {
                    match sel.handle_key(key) {
                        SelectorAction::Select(value) => {
                            let kind = sel.on_select;
                            self.selector = None;
                            self.ctx.scr.dismiss_overlay();
                            match kind {
                                SelectorKind::Mode => {
                                    if let Some(mode) = PermissionMode::from_str(&value) {
                                        self.ctx.mgr.active_session_mut().config.permission_mode = mode;
                                        self.ctx.scr.append_line(
                                            protocol::line_styled(format!("Mode: {}", mode.label()), Color::Green));
                                    }
                                }
                                SelectorKind::Model => {
                                    let new_model = resolve_model_alias(&value);
                                    self.ctx.mgr.active_session_mut().config.model = new_model.to_string();
                                    self.ctx.mgr.active_session_mut().model_chain.push(new_model.to_string());
                                    self.ctx.mgr.invalidate_client();
                                    self.ctx.scr.append_line(
                                        protocol::line_styled(format!("Switched to {new_model}"), Color::Green));
                                }
                            }
                            self.refresh_status_bar();
                            self.active_peek = None;
                        }
                        SelectorAction::Cancel => {
                            self.selector = None;
                            self.ctx.scr.dismiss_overlay();
                            self.active_peek = None;
                        }
                        SelectorAction::None => {
                            if let Some(sel) = &self.selector {
                                self.ctx.scr.dismiss_overlay();
                                self.ctx.scr.show_overlay(sel.render());
                            }
                        }
                    }
                }
                self.ctx.scr.mark_dirty();
                LoopControl::Continue
            }

            Action::DismissPeekOverlay => {
                self.ctx.scr.dismiss_overlay();
                self.active_peek = None;
                self.sphere_nav = None;
                self.ctx.scr.mark_dirty();
                LoopControl::Render
            }

            // ── Scroll ──

            Action::ScrollUp(n) => { self.ctx.scr.scroll_up(n); LoopControl::Continue }
            Action::ScrollDown(n) => { self.ctx.scr.scroll_down(n); LoopControl::Continue }
            Action::ScrollToBottom => { self.ctx.scr.scroll_to_bottom(); LoopControl::Continue }
            Action::ScrollDebugUp(n) => { self.ctx.scr.scroll_debug_up(n); LoopControl::Continue }
            Action::ScrollDebugDown(n) => { self.ctx.scr.scroll_debug_down(n); LoopControl::Continue }
            Action::ScrollDebugToBottom => { self.ctx.scr.scroll_debug_to_bottom(); LoopControl::Continue }

            // ── Input passthrough ──

            Action::ClearSelection => {
                self.sel_state.clear();
                self.ctx.scr.mark_dirty();
                LoopControl::Render
            }

            Action::InputKey(key) => {
                if let Some(input_act) = self.ctx.inp.handle_key(key) {
                    match input_act {
                        InputAction::Submit(t) => {
                            if !matches!(self.state, TuiState::Idle) {
                                if !t.trim().is_empty() {
                                    self.staged_text = Some(match self.staged_text.take() {
                                        Some(existing) => format!("{existing}\n{t}"),
                                        None => t,
                                    });
                                }
                                return LoopControl::Render;
                            }
                            self.dispatch_gen += 1;
                            self.cancel_timeout = None;
                            match dispatch::dispatch_command(&mut self.ctx, &mut self.got_text, &mut self.state, &t) {
                                Some(DispatchResult::Quit) => {
                                    if !matches!(self.state, TuiState::Idle) {
                                        let _ = self.ctx.mgr.cancel_active();
                                    }
                                    approval_bus::deny_all();
                                    return LoopControl::Exit;
                                }
                                Some(DispatchResult::Handled) => {
                                    self.enter_running_if_busy();
                                }
                                None => { return LoopControl::Continue; }
                            }
                        }
                        InputAction::PickerSelect(value) => {
                            let new_model = dispatch::resolve_model_alias(&value);
                            self.ctx.mgr.active_session_mut().config.model = new_model.to_string();
                            self.ctx.mgr.active_session_mut().model_chain.push(new_model.to_string());
                            self.ctx.mgr.invalidate_client();
                            self.ctx.scr.append_line(
                                protocol::line_styled(format!("Switched to {new_model}"), Color::Green));
                        }
                        InputAction::PickerCancel => {}
                        InputAction::Cancel => {}
                        InputAction::FocusStaged => {
                            // Move staged text back into the input bar for editing.
                            // Current input text becomes the new staged (swap).
                            if let Some(staged) = self.staged_text.take() {
                                let current = self.ctx.inp.text();
                                if !current.trim().is_empty() {
                                    self.staged_text = Some(current);
                                }
                                self.ctx.inp.set_text(&staged);
                            }
                        }
                        InputAction::Peek('f') => overlays::show_fleet(&self.ctx.root, &mut self.ctx.scr),
                        InputAction::Peek('w') => overlays::show_work(&self.ctx.root, &mut self.ctx.scr),
                        InputAction::Peek('?') => {
                            let session = self.ctx.mgr.active_session();
                            overlays::show_help(&mut self.ctx.scr, &session.config.model, session.config.permission_mode.label());
                        }
                        InputAction::Peek('p') => {
                            let sel = Selector::mode_selector();
                            self.ctx.scr.show_overlay(sel.render());
                            self.selector = Some(sel);
                        }
                        InputAction::Peek('m') => {
                            if self.ctx.mgr.available_models.is_empty() {
                                self.ctx.scr.append_line(
                                    protocol::line_styled("No models available (API key missing or fetch failed).", Color::Red));
                            } else {
                                let sel = Selector::model_selector(&self.ctx.mgr.available_models);
                                self.ctx.scr.show_overlay(sel.render());
                                self.selector = Some(sel);
                            }
                        }
                        InputAction::Peek('c') => {
                            let overlay_lines = context::build_context_overlay(&self.ctx.mgr, &self.ctx.status_cache);
                            self.ctx.scr.show_overlay_compact(overlay_lines);
                        }
                        InputAction::Peek('d') => {
                            if self.ctx.scr.debug_mode() {
                                let debug_lines = self.ctx.scr.debug_lines();
                                let start = debug_lines.len().saturating_sub(20);
                                let mut overlay = vec![protocol::line_styled("  Debug Log  ", Color::Yellow)];
                                for line in &debug_lines[start..] {
                                    overlay.push(line.clone());
                                }
                                overlay.push(protocol::line_from_spans(vec![protocol::dim("  press any key to dismiss")]));
                                self.ctx.scr.show_overlay(overlay);
                            }
                        }
                        InputAction::Peek(_) => {}
                    }
                }
                LoopControl::Render
            }
        }
    }

    // ── render_if_dirty / render_frame ─────────────────────────────

    fn render_if_dirty(&mut self) {
        // Sync staged flag so TextInput knows whether Up should focus staged
        self.ctx.inp.staged_active = self.staged_text.is_some();
        if self.ctx.scr.dirty {
            self.render_frame();
            self.ctx.scr.dirty = false;
        }
    }

    fn render_frame(&mut self) {
        render_complete_frame(
            &mut self.ctx.scr, &mut self.ctx.renderer, &self.ctx.inp,
            self.sel_state.range, self.staged_text.as_deref(),
        );
    }
}

// ─── render_complete_frame (free function) ─────────────────────────────

/// Draws the full frame via ratatui. Still used during the run() init phase
/// before App exists, and internally by App::render_frame.
pub(crate) fn render_complete_frame(
    scr: &mut AppState,
    renderer: &mut Renderer,
    inp: &TextInput,
    sel_range: Option<((u16, usize), (u16, usize))>,
    staged: Option<&str>,
) {
    let _ = renderer.draw(scr, sel_range, inp.desired_height(), staged, |frame, input_area| {
        frame.render_widget(inp.widget(), input_area);
    });
}

// ─── event_loop (TEA main loop) ────────────────────────────────────────

fn event_loop(app: &mut App, rx: Receiver<AppMessage>) -> Result<(), String> {
    while let Ok(msg) = rx.recv() {
        match app.update(msg) {
            Control::Continue => {}
            Control::Exit => break,
        }
        app.render_if_dirty();
    }
    Ok(())
}

// ─── run() ─────────────────────────────────────────────────────────────

pub fn run(root: PathBuf, debug: bool) -> Result<(), String> {
    let mut scr = AppState::with_debug(debug);
    let mut renderer = Renderer::new().map_err(|e| e.to_string())?;

    // Panic hook: restore terminal before printing panic info (C5: re-entrancy guard).
    // Uses color-eyre for richer formatted output with backtraces.
    let original_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if PANIC_RESTORING.swap(true, Ordering::SeqCst) {
            eprintln!("nested panic during terminal restore");
            std::process::abort();
        }
        let _ = crossterm::terminal::disable_raw_mode();
        let _ = io::stdout().write_all(b"\x1b[?1006l\x1b[?1002l\x1b[?1000l");
        let _ = crossterm::execute!(
            io::stdout(),
            crossterm::terminal::LeaveAlternateScreen,
            crossterm::event::DisableBracketedPaste
        );
        approval_bus::deny_all();
        // Rich panic output: formatted info + backtrace when RUST_BACKTRACE is set
        eprintln!("\nostk panicked: {info}");
        if std::env::var("RUST_BACKTRACE").is_ok() {
            let bt = std::backtrace::Backtrace::force_capture();
            eprintln!("\n{bt}");
        }
        // Fall through to original hook for any chained handlers
        original_hook(info);
    }));

    let inp = TextInput::new();

    // Paint the frame so the screen isn't blank, then stream POST checks.
    render_complete_frame(&mut scr, &mut renderer, &inp, None, None);
    super::components::bootloader::render_boot_sequence(&root, &mut scr);

    // ── Slow initialization ──

    // Parse Agentfile
    let state_dir = crate::state_dir(&root);
    let scheduler_af_path = crate::agentfile::resolve_default_agentfile(&root);
    let (af, _has_scheduler) = if let Some(ref af_path) = scheduler_af_path {
        let content = std::fs::read_to_string(af_path)
            .map_err(|e| format!("{}: {e}", af_path.display()))?;
        let parsed = agentfile::parse(&content)
            .map_err(|e| format!("{} parse: {e}", af_path.display()))?;
        (parsed, true)
    } else {
        (agentfile::Agentfile {
            from: "auto".to_string(),
            prompts: vec![],
            tools: crate::kernel::defaults::default_tools(),
            skills: vec![],
            limits: vec![],
            work: None,
            interrupts: vec![],
            boot_cmd: Some("ostk boot".into()),
            destructive_ops: Some("confirm".into()),
            permissions: Some(crate::kernel::defaults::DEFAULT_PERMISSIONS.into()),
            betas: vec![],
            pin: None,
            tool_patterns: vec![],
        }, false)
    };
    let mut cpu = config_from_agentfile(&af, &state_dir);
    if cpu.max_turns.is_none() { cpu.max_turns = Some(25); }
    let _cpu_model = cpu.model.clone();
    let lc = cpu.into_loop_config(Some(root.clone()));

    // Verify Agentfile signature
    let trust_unsigned = crate::humanfile::load(&crate::state_dir(&root))
        .humanfile.trust.as_deref() == Some("unsigned");
    let _agentfile_trust = if let Some(ref af_path) = scheduler_af_path {
        crate::commands::sign::verify_and_log_agentfile(af_path, trust_unsigned)
    } else {
        crate::commands::sign::AgentfileTrust::Unsigned
    };

    // Activate pin.caps
    if let Some(ref pin) = af.pin {
        unsafe { std::env::set_var("OSTK_PIN", pin) };
    }

    // ── Session init with animated spinner ──

    let spin_frames: &[char] = &[
        '\u{280b}', '\u{2819}', '\u{2839}', '\u{2838}',
        '\u{283c}', '\u{2834}', '\u{2826}', '\u{2827}',
    ];

    scr.append_line(protocol::line_from_spans(vec![
        protocol::colored(format!("  {} ", spin_frames[0]), Color::Cyan),
        protocol::dim("starting session...".to_string()),
    ]));
    render_complete_frame(&mut scr, &mut renderer, &inp, None, None);

    let init_root = root.clone();
    let init_state_dir = state_dir.clone();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::Builder::new()
        .name("session-init".into())
        .spawn(move || {
            let daemon = if crate::serve::socket::kernel_alive(&init_state_dir) {
                crate::serve::client::auto_connect(&init_state_dir)
            } else {
                None
            };
            let mgr = SessionManager::new(init_root, lc);
            let _ = tx.send((daemon, mgr));
        })
        .map_err(|e| format!("spawn session-init: {e}"))?;

    let mut spin_idx = 1usize;
    let (daemon_client, mgr) = loop {
        match rx.try_recv() {
            Ok(result) => break result,
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                let frame = spin_frames[spin_idx % spin_frames.len()];
                spin_idx += 1;
                scr.replace_last_line(protocol::line_from_spans(vec![
                    protocol::colored(format!("  {frame} "), Color::Cyan),
                    protocol::dim("starting session...".to_string()),
                ]));
                render_complete_frame(&mut scr, &mut renderer, &inp, None, None);
                std::thread::sleep(std::time::Duration::from_millis(80));
            }
            Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                return Err("session init thread panicked".into());
            }
        }
    };
    let mut mgr = mgr.map_err(|e| format!("SessionManager: {e}"))?;

    let resolved_model = &mgr.active_session().config.model;
    let short_model = super::components::model_short_name(resolved_model);
    scr.replace_last_line(protocol::line_from_spans(vec![
        protocol::colored("  \u{2713} ", Color::Green),
        protocol::plain(format!("{resolved_model} ready")),
    ]));
    scr.append_line(protocol::line_plain(""));
    renderer.set_title(&format!("ostk \u{00b7} {short_model}"));

    let mut _driver_registry = crate::kernel::drivers::DriverRegistry::new();
    _driver_registry.register_internal_screen();

    let mut status_cache = StatusCache::new(&root);
    {
        let session = mgr.active_session();
        status_cache.maybe_refresh(&root);
        scr.set_status(
            status_cache.build_left_line(&mgr.active),
            status_cache.build_right_line(&session.session_tokens, &session.config.model, session.config.fast_mode),
        );
    }
    render_complete_frame(&mut scr, &mut renderer, &inp, None, None);

    if mgr.client.is_none() {
        scr.append_line(
            protocol::line_styled("No API key found. Set ANTHROPIC_API_KEY (or another provider key) to connect.", Color::Red));
    }

    // T3 anonymous banner
    {
        let state_dir = crate::state_dir(&root);
        let (tier, _) = crate::kernel::identity::determine_trust_tier(&state_dir);
        if matches!(tier, crate::kernel::identity::TrustTier::T3) {
            scr.append_line(protocol::line_plain(""));
            scr.append_line(protocol::line_from_spans(vec![
                protocol::colored("  \u{26a0} ", Color::Yellow),
                protocol::plain("Read-only mode \u{2014} no GPG key found (T3 trust)"),
            ]));
            scr.append_line(protocol::line_from_spans(vec![
                protocol::dim("    To unlock writes: gpg --full-generate-key  then  ostk boot".to_string()),
            ]));
            scr.append_line(protocol::line_plain(""));
        }
    }
    if debug {
        scr.debug_log(
            protocol::line_styled("debug panel active", Color::Green));
    }
    render_complete_frame(&mut scr, &mut renderer, &inp, None, None);

    // Connect to daemon broadcast socket
    let broadcast_rx: Option<std::sync::mpsc::Receiver<serde_json::Value>> = {
        let ostk_dir = crate::state_dir(&root);
        if crate::serve::socket::kernel_alive(&ostk_dir) {
            let sock = crate::serve::socket::socket_path(&ostk_dir);
            match std::os::unix::net::UnixStream::connect(&sock) {
                Ok(stream) => {
                    let (tx, rx) = std::sync::mpsc::channel::<serde_json::Value>();
                    std::thread::Builder::new()
                        .name("broadcast-reader".into())
                        .spawn(move || {
                            let reader = io::BufReader::new(stream);
                            for line in reader.lines() {
                                match line {
                                    Ok(l) if l.trim().is_empty() => continue,
                                    Ok(l) => {
                                        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&l)
                                            && tx.send(val).is_err() { break; }
                                    }
                                    Err(_) => break,
                                }
                            }
                        })
                        .ok();
                    if debug {
                        scr.debug_log(
                            protocol::line_styled("[broadcast] connected to daemon socket", Color::Green));
                    }
                    Some(rx)
                }
                Err(_) => None,
            }
        } else {
            None
        }
    };

    // Move daemon client into SessionManager
    if let Some(client) = daemon_client {
        mgr.set_daemon(client);
        let model_short = mgr.active_session().config.model
            .split('-').next().unwrap_or("tui").to_string();
        let session_id = format!("{}-{}", model_short, std::process::id());
        let _ = mgr.bind_daemon(&session_id);
    }

    // ── Create unified message channel ──
    let (app_tx, app_rx) = mpsc::channel::<AppMessage>();

    // Input thread → unified channel
    super::input_thread::spawn_into(app_tx.clone());

    // Tick timer → unified channel
    {
        let tx = app_tx.clone();
        std::thread::Builder::new()
            .name("tick".into())
            .spawn(move || {
                loop {
                    std::thread::sleep(Duration::from_millis(16));
                    if tx.send(AppMessage::Tick).is_err() { break; }
                }
            })
            .ok();
    }

    // Daemon poller → unified channel
    let daemon_poller = if mgr.is_daemon() {
        super::daemon_poll::DaemonPoller::spawn(&state_dir, app_tx.clone())
    } else {
        None
    };

    // Bridge broadcast reader → unified channel
    if let Some(bcast_rx) = broadcast_rx {
        let tx = app_tx.clone();
        std::thread::Builder::new()
            .name("broadcast-bridge".into())
            .spawn(move || {
                while let Ok(val) = bcast_rx.recv() {
                    if tx.send(AppMessage::Broadcast(val)).is_err() { break; }
                }
            })
            .ok();
    }

    let ctx = EventLoopContext {
        root,
        scr,
        renderer,
        inp,
        mgr,
        status_cache,
        daemon_poller,
    };

    let mut app = App {
        ctx,
        state: TuiState::Idle,
        streaming_buffer: String::new(),
        got_text: false,
        staged_text: None,
        selector: None,
        sel_state: selection::SelectionState::new(),
        active_peek: None,
        sphere_nav: None,
        dispatch_gen: 0,
        active_gen: 0,
        cancel_timeout: None,
    };

    // Query daemon for current session state
    if let Some(busy) = app.ctx.mgr.daemon_status()
        && busy
    {
        app.ctx.scr.append_line(protocol::line_from_spans(vec![
            protocol::colored("  \u{26a0} ", Color::Yellow),
            protocol::dim("daemon reports busy session \u{2014} submit a prompt to continue".to_string()),
        ]));
    }

    let res = event_loop(&mut app, app_rx);
    app.ctx.mgr.save_all();
    res
}

// Tests for component functions live in their component modules:
// - components::dispatch::tests
// - components::overlays::tests
// - components::render::tests (wrap_text)
// - components::session::tests
// - cpu::session::tests (SessionManager, AgentSession, BootContext)
// - input::tests (draw_input)
