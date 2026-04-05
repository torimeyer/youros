//! TUI FSM Phase 2 — Classify-Then-Dispatch input handling.
//!
//! Separates "what happened" (classify_input) from "what to do" (dispatch_action).
//! Global keybindings fire before modal gating. Two overlay types (approval vs peek)
//! get distinct InputMode variants with different key semantics.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use super::app::TuiState;
use super::protocol::Color;
use super::renderer::Panel;

// ─── InputMode ──────────────────────────────────────────────────────────

/// Collapsed input context — replaces 5 boolean dimensions with one enum.
/// Computed once per key event from (TuiState, overlay_active, selector).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum InputMode {
    /// Approval modal showing — captures y/n/a/Enter, blocks most input.
    ApprovalModal,
    /// Selector overlay (model/mode picker) — navigation keys only.
    SelectorOverlay,
    /// Peek overlay (fleet/context/work/help/debug) — any key dismisses,
    /// but global bindings (Alt+key toggles) still work.
    PeekOverlay,
    /// Agent running, no overlay — input redirects or queues.
    AgentRunning,
    /// Sphere navigator overlay — captures navigation + linking keys.
    SphereNavigator,
    /// Idle, input has focus — normal editing + dispatch.
    Editing,
}

/// Compute InputMode from TUI state.
pub(crate) fn compute_input_mode(
    tui_state: &TuiState,
    overlay_active: bool,
    has_selector: bool,
    sphere_nav_active: bool,
) -> InputMode {
    // ApprovalModal always wins — must capture y/n/a before anything else.
    if matches!(tui_state, TuiState::AwaitingApproval { .. }) && overlay_active {
        return InputMode::ApprovalModal;
    }
    if overlay_active && has_selector {
        return InputMode::SelectorOverlay;
    }
    if sphere_nav_active && overlay_active {
        return InputMode::SphereNavigator;
    }
    if overlay_active {
        return InputMode::PeekOverlay;
    }
    if !matches!(tui_state, TuiState::Idle) {
        return InputMode::AgentRunning;
    }
    InputMode::Editing
}

// ─── Action ─────────────────────────────────────────────────────────────

/// Every distinct thing the TUI can do in response to a key event.
#[derive(Debug, Clone)]
pub(crate) enum Action {
    // ── Global (Layer 1) ──
    Quit,
    CopySelection,
    CopySelectionLegacy,

    // ── Global (Layer 2) — overlay toggles ──
    ShowContextOverlay,
    ShowFleetOverlay,
    ShowWorkOverlay,
    ShowHelpOverlay,
    ShowModelPicker,
    ShowModePicker,
    ShowDebugOverlay,
    ShowSphereNavigator,
    SwitchTabPrev,
    SwitchTabNext,

    // ── Global (Layer 3) ──
    CancelAgent,
    PasteClipboard,

    // ── Mode-specific ──
    ApprovalDecision {
        decision: &'static str,
        label: &'static str,
        color: Color,
    },
    SelectorKey(KeyEvent),
    DismissPeekOverlay,
    SphereNavKey(KeyEvent),

    // ── Scroll ──
    ScrollUp(usize),
    ScrollDown(usize),
    ScrollToBottom,
    ScrollDebugUp(usize),
    ScrollDebugDown(usize),
    ScrollDebugToBottom,

    // ── Input passthrough ──
    ClearSelection,
    InputKey(KeyEvent),
}

// ─── LoopControl ────────────────────────────────────────────────────────

/// What the event loop should do after dispatching an action.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum LoopControl {
    /// Skip render, go to next iteration.
    Continue,
    /// Return Ok(()) from event_loop.
    Exit,
    /// Fall through to render_complete_frame.
    Render,
}

// ─── ActivePeek ─────────────────────────────────────────────────────────

/// Which peek overlay is currently showing (for toggle semantics).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ActivePeek {
    Context,
    Fleet,
    Work,
    Help,
    Mode,
    Model,
    Debug,
    SphereNavigator,
}

// ─── ClassifyCtx ────────────────────────────────────────────────────────

/// Read-only context for classify_input (avoids borrowing the full app state).
pub(crate) struct ClassifyCtx {
    pub has_selection: bool,
    pub input_buffer_empty: bool,
    pub picker_active: bool,
    pub debug_mode: bool,
    pub focused_panel: Panel,
    pub half_page: usize,
    #[allow(dead_code)]
    pub sphere_nav_active: bool,
}

// ─── classify_input ─────────────────────────────────────────────────────

/// Pure function: map (key, mode, context) → Option<Action>.
///
/// 4-layer precedence:
///   Layer 1: Global bindings (all modes)
///   Layer 2: Overlay toggles (except ApprovalModal/SelectorOverlay)
///   Layer 3: Non-idle globals (Esc cancel, Ctrl+V paste)
///   Layer 4: Mode-specific dispatch
pub(crate) fn classify_input(
    key: KeyEvent,
    mode: InputMode,
    ctx: &ClassifyCtx,
) -> Option<Action> {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    let shift = key.modifiers.contains(KeyModifiers::SHIFT);

    // ── Layer 1: Global bindings (ALL modes) ──

    // Ctrl+C with selection → copy (takes priority over exit)
    if ctrl && key.code == KeyCode::Char('c') && ctx.has_selection {
        return Some(Action::CopySelection);
    }
    // Ctrl+C/D/Q → exit
    if ctrl && matches!(key.code, KeyCode::Char('c') | KeyCode::Char('d') | KeyCode::Char('q'))
        && !ctx.has_selection
    {
        return Some(Action::Quit);
    }
    // Ctrl+Y → legacy copy
    if ctrl && key.code == KeyCode::Char('y') {
        return Some(Action::CopySelectionLegacy);
    }

    // ── Layer 2: Overlay toggles (except ApprovalModal/SelectorOverlay) ──
    // macOS terminals without Kitty protocol send Option+key as composed chars
    // (e.g. Option+C = ç, Option+F = ƒ). Match both the Alt-modifier path
    // AND the composed-character path so peek overlays work everywhere.
    if !matches!(mode, InputMode::ApprovalModal | InputMode::SelectorOverlay) {
        if alt {
            match key.code {
                KeyCode::Char('c') => return Some(Action::ShowContextOverlay),
                KeyCode::Char('f') => return Some(Action::ShowFleetOverlay),
                KeyCode::Char('w') => return Some(Action::ShowWorkOverlay),
                KeyCode::Char('?') => return Some(Action::ShowHelpOverlay),
                KeyCode::Char('m') => return Some(Action::ShowModelPicker),
                KeyCode::Char('p') => return Some(Action::ShowModePicker),
                KeyCode::Char('d') if ctx.debug_mode => return Some(Action::ShowDebugOverlay),
                KeyCode::Char('g') => return Some(Action::ShowSphereNavigator),
                KeyCode::Left => return Some(Action::SwitchTabPrev),
                KeyCode::Right => return Some(Action::SwitchTabNext),
                _ => {}
            }
        }
        // macOS Option+key composed characters (no ALT modifier set by terminal)
        match key.code {
            KeyCode::Char('ç') => return Some(Action::ShowContextOverlay),    // Option+C
            KeyCode::Char('ƒ') => return Some(Action::ShowFleetOverlay),      // Option+F
            KeyCode::Char('∑') => return Some(Action::ShowWorkOverlay),       // Option+W
            KeyCode::Char('¿') => return Some(Action::ShowHelpOverlay),       // Option+Shift+?
            KeyCode::Char('µ') => return Some(Action::ShowModelPicker),       // Option+M
            KeyCode::Char('π') => return Some(Action::ShowModePicker),        // Option+P
            KeyCode::Char('∂') if ctx.debug_mode => return Some(Action::ShowDebugOverlay), // Option+D
            KeyCode::Char('©') => return Some(Action::ShowSphereNavigator), // Option+G
            _ => {}
        }
    }

    // ── Layer 3: Non-idle globals ──

    // Esc → cancel agent (non-Idle, non-PeekOverlay)
    // During PeekOverlay, Esc should dismiss the overlay instead.
    if key.code == KeyCode::Esc
        && !matches!(mode, InputMode::Editing | InputMode::PeekOverlay | InputMode::SphereNavigator)
    {
        return Some(Action::CancelAgent);
    }

    // Ctrl+V → paste clipboard
    if ctrl && key.code == KeyCode::Char('v') {
        return Some(Action::PasteClipboard);
    }

    // ── Layer 4: Mode-specific dispatch ──

    match mode {
        InputMode::ApprovalModal => classify_approval(key),
        InputMode::SelectorOverlay => Some(Action::SelectorKey(key)),
        InputMode::SphereNavigator => Some(Action::SphereNavKey(key)),
        InputMode::PeekOverlay => classify_peek(key),
        InputMode::AgentRunning => classify_editing_or_running(key, ctx, shift),
        InputMode::Editing => classify_editing_or_running(key, ctx, shift),
    }
}

/// Approval modal: y/n/a/Enter → decision, everything else ignored.
fn classify_approval(key: KeyEvent) -> Option<Action> {
    match key.code {
        KeyCode::Char('y') | KeyCode::Char('Y') => Some(Action::ApprovalDecision {
            decision: "allow",
            label: "[approved] ",
            color: Color::Green,
        }),
        KeyCode::Char('n') | KeyCode::Char('N') => Some(Action::ApprovalDecision {
            decision: "deny",
            label: "[denied] ",
            color: Color::Red,
        }),
        KeyCode::Char('a') | KeyCode::Char('A') => Some(Action::ApprovalDecision {
            decision: "always_allow",
            label: "[always allow] ",
            color: Color::Yellow,
        }),
        KeyCode::Enter => Some(Action::ApprovalDecision {
            decision: "deny",
            label: "[denied] ",
            color: Color::Red,
        }),
        _ => None, // Swallow unhandled keys during approval
    }
}

/// Peek overlay: Esc dismisses, any other non-global key also dismisses.
fn classify_peek(key: KeyEvent) -> Option<Action> {
    // Esc explicitly dismisses
    if key.code == KeyCode::Esc {
        return Some(Action::DismissPeekOverlay);
    }
    // Any other key that wasn't caught by Layer 1-2 dismisses the peek
    Some(Action::DismissPeekOverlay)
}

/// Editing or AgentRunning: scroll keys, then pass to input bar.
fn classify_editing_or_running(
    key: KeyEvent,
    ctx: &ClassifyCtx,
    shift: bool,
) -> Option<Action> {
    // Clear selection on any key
    if ctx.has_selection {
        return Some(Action::ClearSelection);
    }

    // Scroll: Up/Down when input buffer is empty and no picker
    if ctx.input_buffer_empty && !ctx.picker_active {
        match key.code {
            KeyCode::Up => return Some(Action::ScrollUp(1)),
            KeyCode::Down => return Some(Action::ScrollDown(1)),
            _ => {}
        }
    }

    // Scroll: PageUp/PageDown/Shift+Up/Shift+Down/End
    match ctx.focused_panel {
        Panel::Debug if ctx.debug_mode => match (key.code, shift) {
            (KeyCode::PageUp, _) => return Some(Action::ScrollDebugUp(ctx.half_page)),
            (KeyCode::PageDown, _) => return Some(Action::ScrollDebugDown(ctx.half_page)),
            (KeyCode::Up, true) => return Some(Action::ScrollDebugUp(1)),
            (KeyCode::Down, true) => return Some(Action::ScrollDebugDown(1)),
            (KeyCode::End, _) => return Some(Action::ScrollDebugToBottom),
            _ => {}
        },
        _ => match (key.code, shift) {
            (KeyCode::PageUp, _) => return Some(Action::ScrollUp(ctx.half_page)),
            (KeyCode::PageDown, _) => return Some(Action::ScrollDown(ctx.half_page)),
            (KeyCode::Up, true) => return Some(Action::ScrollUp(1)),
            (KeyCode::Down, true) => return Some(Action::ScrollDown(1)),
            (KeyCode::End, _) => return Some(Action::ScrollToBottom),
            _ => {}
        },
    }

    // Everything else → pass to input bar
    Some(Action::InputKey(key))
}

// ─── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyEventState, KeyModifiers};

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent {
            code,
            modifiers: KeyModifiers::NONE,
            kind: KeyEventKind::Press,
            state: KeyEventState::NONE,
        }
    }

    fn ctrl(code: KeyCode) -> KeyEvent {
        KeyEvent {
            code,
            modifiers: KeyModifiers::CONTROL,
            kind: KeyEventKind::Press,
            state: KeyEventState::NONE,
        }
    }

    fn alt(c: char) -> KeyEvent {
        KeyEvent {
            code: KeyCode::Char(c),
            modifiers: KeyModifiers::ALT,
            kind: KeyEventKind::Press,
            state: KeyEventState::NONE,
        }
    }

    fn default_ctx() -> ClassifyCtx {
        ClassifyCtx {
            has_selection: false,
            input_buffer_empty: true,
            picker_active: false,
            debug_mode: false,
            focused_panel: Panel::Chat,
            half_page: 10,
            sphere_nav_active: false,
        }
    }

    // ── compute_input_mode tests ──

    #[test]
    fn mode_idle_no_overlay() {
        let mode = compute_input_mode(&TuiState::Idle, false, false, false);
        assert_eq!(mode, InputMode::Editing);
    }

    #[test]
    fn mode_agent_running() {
        use super::super::components::spinner::SpinnerState;
        let mode = compute_input_mode(
            &TuiState::AgentRunning { spinner: SpinnerState::new() },
            false, false, false,
        );
        assert_eq!(mode, InputMode::AgentRunning);
    }

    #[test]
    fn mode_approval_with_overlay() {
        use super::super::components::spinner::SpinnerState;
        let mode = compute_input_mode(
            &TuiState::AwaitingApproval {
                agent_alias: String::new(),
                tool_name: String::new(),
                tool_input_preview: String::new(),
                daemon_request_id: None,
                spinner: SpinnerState::new(),
            },
            true, false, false,
        );
        assert_eq!(mode, InputMode::ApprovalModal);
    }

    #[test]
    fn mode_approval_beats_sphere_nav() {
        use super::super::components::spinner::SpinnerState;
        let mode = compute_input_mode(
            &TuiState::AwaitingApproval {
                agent_alias: String::new(),
                tool_name: String::new(),
                tool_input_preview: String::new(),
                daemon_request_id: None,
                spinner: SpinnerState::new(),
            },
            true, false, true, // sphere_nav_active = true
        );
        assert_eq!(mode, InputMode::ApprovalModal);
    }

    #[test]
    fn mode_sphere_navigator() {
        let mode = compute_input_mode(&TuiState::Idle, true, false, true);
        assert_eq!(mode, InputMode::SphereNavigator);
    }

    #[test]
    fn mode_selector_overlay() {
        let mode = compute_input_mode(&TuiState::Idle, true, true, false);
        assert_eq!(mode, InputMode::SelectorOverlay);
    }

    #[test]
    fn mode_peek_overlay() {
        let mode = compute_input_mode(&TuiState::Idle, true, false, false);
        assert_eq!(mode, InputMode::PeekOverlay);
    }

    // ── classify_input tests ──

    #[test]
    fn ctrl_c_exits_from_all_modes() {
        let ctx = default_ctx();
        for mode in [InputMode::Editing, InputMode::AgentRunning, InputMode::PeekOverlay,
                     InputMode::ApprovalModal, InputMode::SelectorOverlay] {
            let action = classify_input(ctrl(KeyCode::Char('c')), mode, &ctx);
            assert!(matches!(action, Some(Action::Quit)),
                "Ctrl+C should quit in {mode:?}");
        }
    }

    #[test]
    fn alt_c_works_in_editing() {
        let action = classify_input(alt('c'), InputMode::Editing, &default_ctx());
        assert!(matches!(action, Some(Action::ShowContextOverlay)));
    }

    #[test]
    fn alt_c_works_in_agent_running() {
        let action = classify_input(alt('c'), InputMode::AgentRunning, &default_ctx());
        assert!(matches!(action, Some(Action::ShowContextOverlay)));
    }

    #[test]
    fn alt_c_works_in_peek_overlay() {
        let action = classify_input(alt('c'), InputMode::PeekOverlay, &default_ctx());
        assert!(matches!(action, Some(Action::ShowContextOverlay)),
            "Alt+c should toggle peek overlay, not be swallowed");
    }

    #[test]
    fn alt_c_blocked_in_approval_modal() {
        let action = classify_input(alt('c'), InputMode::ApprovalModal, &default_ctx());
        // Should NOT be ShowContextOverlay — approval blocks overlay toggles
        assert!(!matches!(action, Some(Action::ShowContextOverlay)));
    }

    #[test]
    fn alt_c_blocked_in_selector() {
        let action = classify_input(alt('c'), InputMode::SelectorOverlay, &default_ctx());
        assert!(!matches!(action, Some(Action::ShowContextOverlay)));
    }

    #[test]
    fn esc_cancels_agent_during_running() {
        let action = classify_input(key(KeyCode::Esc), InputMode::AgentRunning, &default_ctx());
        assert!(matches!(action, Some(Action::CancelAgent)));
    }

    #[test]
    fn esc_cancels_agent_during_approval() {
        let action = classify_input(key(KeyCode::Esc), InputMode::ApprovalModal, &default_ctx());
        assert!(matches!(action, Some(Action::CancelAgent)),
            "Esc during approval cancels agent (stronger than deny)");
    }

    #[test]
    fn esc_does_nothing_in_idle() {
        let action = classify_input(key(KeyCode::Esc), InputMode::Editing, &default_ctx());
        // Should pass through to InputKey, not CancelAgent
        assert!(matches!(action, Some(Action::InputKey(_))));
    }

    #[test]
    fn esc_dismisses_peek_overlay() {
        let action = classify_input(key(KeyCode::Esc), InputMode::PeekOverlay, &default_ctx());
        assert!(matches!(action, Some(Action::DismissPeekOverlay)));
    }

    #[test]
    fn y_approves_in_approval_modal() {
        let action = classify_input(key(KeyCode::Char('y')), InputMode::ApprovalModal, &default_ctx());
        assert!(matches!(action, Some(Action::ApprovalDecision { decision: "allow", .. })));
    }

    #[test]
    fn n_denies_in_approval_modal() {
        let action = classify_input(key(KeyCode::Char('n')), InputMode::ApprovalModal, &default_ctx());
        assert!(matches!(action, Some(Action::ApprovalDecision { decision: "deny", .. })));
    }

    #[test]
    fn scroll_up_when_input_empty() {
        let action = classify_input(key(KeyCode::Up), InputMode::Editing, &default_ctx());
        assert!(matches!(action, Some(Action::ScrollUp(1))));
    }

    #[test]
    fn up_key_passes_to_input_when_buffer_has_text() {
        let mut ctx = default_ctx();
        ctx.input_buffer_empty = false;
        let action = classify_input(key(KeyCode::Up), InputMode::Editing, &ctx);
        assert!(matches!(action, Some(Action::InputKey(_))));
    }

    #[test]
    fn ctrl_c_copies_selection_before_exit() {
        let mut ctx = default_ctx();
        ctx.has_selection = true;
        let action = classify_input(ctrl(KeyCode::Char('c')), InputMode::Editing, &ctx);
        assert!(matches!(action, Some(Action::CopySelection)));
    }

    #[test]
    fn any_key_dismisses_peek() {
        let action = classify_input(key(KeyCode::Char('x')), InputMode::PeekOverlay, &default_ctx());
        assert!(matches!(action, Some(Action::DismissPeekOverlay)));
    }

    #[test]
    fn unknown_key_swallowed_in_approval() {
        let action = classify_input(key(KeyCode::Char('x')), InputMode::ApprovalModal, &default_ctx());
        assert!(action.is_none(), "Unknown keys should be swallowed in approval modal");
    }
}
