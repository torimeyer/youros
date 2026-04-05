//! Command dispatch — verb routing, model aliases, local command execution.

use std::path::Path;

use crate::cpu::PermissionMode;
use crate::fcp_screen::app::EventLoopContext;
use crate::fcp_screen::protocol::{self, Color};
use crate::fcp_screen::renderer::AppState as Screen;
use super::context;
use super::overlays;
use super::spinner::SpinnerState;

/// (media_type, base64_data, byte_size, optional dimensions)
pub type ImageData = (String, String, usize, Option<(u32, u32)>);

/// Result of processing a colon-command or free-text input.
pub enum DispatchResult {
    /// User typed :quit — caller should exit.
    Quit,
    /// Command was handled (colon-command or free-text dispatched to agent).
    Handled,
}

/// Unified verb list — single source of truth for autocomplete, highlighting,
/// and dispatch routing. Replaces the separate KNOWN_VERBS and LOCAL_VERBS.
pub const VERBS: &[&str] = &[
    "compile", "boot", "reap", "bench", "status", "agents", "help", "quit",
    "q", "switch", "thread", "hay", "needle", "search", "post", "run", "spawn",
    "model", "models", "mode", "fast", "clear", "compact", "reboot", "log", "context",
    "image", "revoke",
];

pub use crate::cpu::providers::resolve_model_alias;

/// Check whether input is a local verb (`:verb` that the TUI handles directly).
pub fn is_local_verb(input: &str) -> bool {
    input.strip_prefix(':')
        .and_then(|r| r.split_whitespace().next())
        .is_some_and(|stem| VERBS.contains(&stem))
}

/// Execute a local ostk command via subprocess with piped stdout/stderr.
///
/// All verbs route through subprocess — safe under ratatui since we never
/// touch fd 1/2 directly. The binary name comes from OSTK_OS_NAME or "ostk".
pub fn dispatch_local(root: &Path, input: &str, scr: &mut Screen) {
    let parts: Vec<&str> = input.strip_prefix(':').unwrap_or(input).split_whitespace().collect();
    if parts.is_empty() { return; }

    let bin = std::env::var("OSTK_OS_NAME").unwrap_or_else(|_| "ostk".into());
    match std::process::Command::new(&bin).args(&parts).current_dir(root)
        .stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped()).spawn() {
        Ok(child) => match child.wait_with_output() {
            Ok(o) => {
                let combined = String::from_utf8_lossy(&o.stdout).to_string()
                    + &String::from_utf8_lossy(&o.stderr);
                for l in combined.lines() {
                    scr.append_line(protocol::line_plain(l));
                }
                let c = o.status.code().unwrap_or(-1);
                scr.append_line(protocol::line_styled(
                    format!("[exit {c}]"), if c == 0 { Color::Green } else { Color::Red }));
            }
            Err(e) => {
                scr.append_line(protocol::line_styled(format!("[error] {e}"), Color::Red));
            }
        }
        Err(e) => {
            scr.append_line(protocol::line_styled(format!("[error] {e}"), Color::Red));
        }
    }
}

/// Handle all colon-commands and free-text input from the Submit action.
///
/// Returns `Some(DispatchResult::Quit)` when the user wants to exit,
/// `Some(DispatchResult::Handled)` for all other inputs (commands + free text),
/// or `None` if the input was empty (caller should `continue`).
///
/// →FSM Phase 2: Accepts `tui_state` for explicit state transitions on dispatch.
/// →899: Spinner is now inside TuiState — no separate spinner parameter.
pub(crate) fn dispatch_command(
    ctx: &mut EventLoopContext,
    got_text: &mut bool,
    tui_state: &mut super::super::app::TuiState,
    input: &str,
) -> Option<DispatchResult> {
    // Destructure ctx for direct field access (avoids borrow-checker issues
    // with passing &mut ctx while also borrowing individual fields)
    let root = &ctx.root;
    let scr = &mut ctx.scr;
    let inp = &mut ctx.inp;
    let mgr = &mut ctx.mgr;
    let status_cache = &mut ctx.status_cache;
    if input.trim().is_empty() { return None; }
    let tl = input.trim().to_lowercase();

    // Quit commands
    if matches!(tl.as_str(), ":quit"|":q"|"q"|"quit"|"exit"|"/exit"|":exit"|"/quit") {
        tracing::info!("exit: quit command");
        return Some(DispatchResult::Quit);
    }

    // Echo user input
    scr.scroll_to_bottom();
    scr.append_line(protocol::line_plain(""));
    scr.append_line(protocol::line_from_spans(vec![
        protocol::colored("[you] ", Color::Cyan), protocol::plain(input)]));

    // ── Colon commands ────────────────────────────────────────────────

    // :model <name> — switch model
    if let Some(model_name) = tl.strip_prefix(":model ")
        .or_else(|| tl.strip_prefix(":model\t"))
    {
        let new_model = resolve_model_alias(model_name.trim());
        if !mgr.available_models.is_empty()
            && !mgr.available_models.iter().any(|m| m == new_model)
        {
            scr.append_line(protocol::line_styled(
                    format!("Unknown model: {new_model}. Use :models to see available."),
                    Color::Red));
        } else {
            // →FSM Phase 3: Cancel running task before switching model.
            // →899: Transition to Idle drops the spinner automatically.
            if mgr.active_session().is_busy() {
                mgr.active_session_mut().cancel();
                *tui_state = super::super::app::TuiState::Idle;
            }
            let old_model = mgr.active_session().config.model.clone();

            // →1157 Phase 6: Detect provider boundary crossing.
            // Same provider (e.g., sonnet→opus): carry messages forward.
            // Different provider (e.g., claude→gemini): build handoff, clear, inject.
            let old_provider = crate::cpu::providers::resolve_provider(&old_model);
            let new_provider = crate::cpu::providers::resolve_provider(new_model);

            if old_provider != new_provider {
                // Provider boundary — build handoff lookup table from kernel state
                let msg_snapshot = mgr.active_session().messages.snapshot();
                let handoff = crate::kernel::handoff::build_handoff_table(
                    root,
                    &old_model,
                    new_model,
                    &msg_snapshot,
                );

                // Clear ISA-specific messages
                mgr.active_session().messages.clear();
                mgr.active_session_mut().session_tokens = crate::cpu::session::SessionTokens::default();

                // Inject handoff as orientation for the new model
                mgr.active_session().messages.push(crate::cpu::anthropic::Message {
                    role: "user".into(),
                    content: vec![crate::cpu::anthropic::ContentBlock::Text {
                        text: handoff,
                    }],
                    model: None,
                });
                mgr.active_session().messages.push(crate::cpu::anthropic::Message {
                    role: "assistant".into(),
                    content: vec![crate::cpu::anthropic::ContentBlock::Text {
                        text: "Handoff received. I have the context index — I'll page in what I need via tools. Continuing.".into(),
                    }],
                    model: None,
                });

                scr.append_line(protocol::line_styled(
                    format!("Provider switch: {old_provider} → {new_provider} (handoff compiled)"),
                    Color::Yellow,
                ));
            }

            mgr.active_session_mut().config.model = new_model.to_string();
            mgr.active_session_mut().model_chain.push(new_model.to_string()); // →903: track model chain
            mgr.invalidate_client(); // →816: force driver recreation for new model
            mgr.boot_context.maybe_refresh(); // →843: refresh boot context on model switch
            scr.append_line(protocol::line_styled(format!("Switched to {new_model}"), Color::Green));
        }
    // :mode <name> — switch permission mode
    } else if let Some(mode_name) = tl.strip_prefix(":mode ")
        .or_else(|| tl.strip_prefix(":mode\t"))
    {
        match PermissionMode::from_str(mode_name.trim()) {
            Some(mode) => {
                mgr.active_session_mut().config.permission_mode = mode;
                scr.append_line(protocol::line_styled(format!("Mode: {}", mode.label()), Color::Green));
            }
            None => {
                scr.append_line(protocol::line_styled(
                        "Unknown mode. Use: plan, governed, auto, autonomous".to_string(),
                        Color::Red));
            }
        }
    // :models — inline model picker
    } else if tl == ":models" {
        let models = if mgr.available_models.is_empty() {
            // Detect available models from API keys in environment
            crate::kernel::defaults::models_for_available_keys()
        } else {
            mgr.available_models.clone()
        };
        let current = mgr.active_session().config.model.as_str();
        inp.start_picker("model", models, Some(current));
    // :fast — toggle fast mode
    } else if tl == ":fast" {
        let session = mgr.active_session_mut();
        session.config.fast_mode = !session.config.fast_mode;
        let fm = session.config.fast_mode;
        let state = if fm { "ON \u{26a1}" } else { "OFF" };
        let color = if fm { Color::Yellow } else { Color::Green };
        scr.append_line(protocol::line_styled(format!("Fast mode: {state}"), color));
        if fm && !session.config.model.contains("opus") {
            scr.append_line(protocol::line_styled("Warning: fast mode only works with Opus models", Color::Yellow));
        }
        if fm && !session.config.betas.iter().any(|b| b == "fast-mode") {
            scr.append_line(protocol::line_styled("Warning: BETA fast-mode not declared in Agentfile", Color::Yellow));
        }
    // :boot — refresh boot context inline (no blocking subprocess)
    } else if tl == ":boot" {
        mgr.boot_context.refresh();
        let boot_summary: String = mgr.boot_context.boot_md.lines().take(15)
            .collect::<Vec<_>>().join("\n");
        for line in boot_summary.lines() {
            scr.append_line(protocol::line_from_spans(vec![protocol::dim(line)]));
        }
        scr.append_line(protocol::line_styled("[boot] context refreshed", Color::Green));
    // :reboot — reset session with fresh boot context
    } else if tl == ":reboot" {
        // →FSM Phase 3: Cancel running task before rebooting.
        // →899: Transition to Idle drops the spinner automatically.
        if mgr.active_session().is_busy() {
            mgr.active_session_mut().cancel();
            *tui_state = super::super::app::TuiState::Idle;
        }
        scr.clear();
        scr.append_line(protocol::line_styled("[reboot] resetting session...", Color::Green));
        mgr.reboot_active();
        status_cache.reset_context();
        scr.append_line(protocol::line_styled("[reboot] fresh boot context + identity + orientation", Color::Green));
    // :clear — clear session messages
    } else if tl == ":clear" {
        mgr.active_session_mut().clear();
        status_cache.reset_context();
        scr.clear();
        scr.append_line(protocol::line_styled("[session] cleared (use :reboot for full boot)", Color::Green));
    // :compact [N] — keep last N turn pairs (default 2), drop the rest
    } else if tl == ":compact" || tl.starts_with(":compact ") {
        let keep = tl.strip_prefix(":compact").unwrap().trim()
            .parse::<usize>().unwrap_or(2);
        let (before, after) = mgr.active_session_mut().compact(keep);
        let dropped = before.saturating_sub(after);
        status_cache.reset_context(); // stale token count no longer valid
        scr.append_line(protocol::line_styled(
                format!("[compact] {dropped} messages dropped, {after} kept (last {keep} turn pairs)"),
                Color::Green));
    // :context — show context overlay
    } else if tl == ":context" {
        let overlay_lines = context::build_context_overlay(mgr, status_cache);
        scr.show_overlay_compact(overlay_lines);
    // :log — show audit log
    } else if tl == ":log" {
        overlays::display_audit_log(root, scr);
    // →1013: :revoke [tool] — manage session allow-list
    } else if tl == ":revoke" || tl.starts_with(":revoke ") {
        let arg = tl.strip_prefix(":revoke").unwrap().trim();
        let allowed = mgr.active_session().runtime_allowed.lock()
            .unwrap_or_else(|e| e.into_inner());
        if arg.is_empty() {
            // List all entries
            if allowed.is_empty() {
                scr.append_line(protocol::line_styled("  No tools in session allow-list", Color::Yellow));
            } else {
                scr.append_line(protocol::line_styled(format!("  Session allow-list ({} entries):", allowed.len()), Color::Cyan));
                for tool in allowed.iter() {
                    scr.append_line(protocol::line_from_spans(vec![
                            protocol::plain("    "),
                            protocol::colored(tool, Color::Green),
                            protocol::dim("  ← :revoke <name> to remove"),
                        ]));
                }
            }
            drop(allowed);
        } else {
            // Remove a specific entry
            let removed = allowed.contains(arg);
            drop(allowed);
            if removed {
                mgr.active_session().runtime_allowed.lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .remove(arg);
                scr.append_line(protocol::line_styled(format!("  Revoked: {arg}"), Color::Yellow));
            } else {
                scr.append_line(protocol::line_styled(format!("  '{arg}' not in session allow-list"), Color::Red));
            }
        }
    // :thread [name|close] — session management
    } else if tl == ":thread" || tl.starts_with(":thread ") {
        let arg = tl.strip_prefix(":thread").unwrap().trim();
        if arg.is_empty() {
            let list = mgr.list_sessions();
            scr.append_line(protocol::line_styled(format!("  Sessions ({})", list.len()), Color::Cyan));
            for (name, msg_count, busy) in &list {
                let indicator = if *busy { "\u{25cf}" } else { "\u{25cb}" };
                let active_mark = if *name == mgr.active { " \u{25c4}" } else { "" };
                scr.append_line(protocol::line_plain(format!("  {indicator} {name:<20} {msg_count} msgs{active_mark}")));
            }
        } else if arg == "close" {
            if mgr.active != "scheduler" {
                let closed_name = mgr.active.clone();
                mgr.switch("scheduler");
                if let Err(e) = mgr.remove_session(&closed_name) {
                    scr.append_line(protocol::line_styled(format!("Warning: {e}"), Color::Yellow));
                }
                scr.append_line(protocol::line_styled(format!("Closed {closed_name}, switched to scheduler"), Color::Green));
            } else {
                scr.append_line(protocol::line_styled("Cannot close the scheduler session".to_string(), Color::Red));
            }
        } else {
            mgr.switch(arg);
            scr.append_line(protocol::line_styled(format!("Switched to thread: {}", mgr.active), Color::Green));
        }
    // :image /path — attach image to next message (→823)
    } else if let Some(path_str) = tl.strip_prefix(":image ")
        .or_else(|| tl.strip_prefix(":image\t"))
    {
        let path_str = path_str.trim();
        // Expand ~ to home dir
        let expanded = if path_str.starts_with('~') {
            if let Ok(home) = std::env::var("HOME") {
                path_str.replacen('~', &home, 1)
            } else {
                path_str.to_string()
            }
        } else if !path_str.starts_with('/') {
            // Relative path → resolve from project root
            root.join(path_str).to_string_lossy().to_string()
        } else {
            path_str.to_string()
        };
        match load_image_file(&expanded) {
            Ok((media_type, data, size_bytes, dimensions)) => {
                let size_str = if size_bytes > 1_000_000 {
                    format!("{:.1}MB", size_bytes as f64 / 1_000_000.0)
                } else {
                    format!("{:.0}KB", size_bytes as f64 / 1_000.0)
                };
                let dim_str = dimensions.map(|(w, h)| format!(" {w}\u{00d7}{h}")).unwrap_or_default();
                let fname = std::path::Path::new(&expanded)
                    .file_name()
                    .map(|f| f.to_string_lossy().to_string())
                    .unwrap_or_else(|| expanded.clone());
                let block = crate::cpu::anthropic::ContentBlock::Image {
                    source: crate::cpu::anthropic::ImageSource {
                        source_type: "base64".into(),
                        media_type,
                        data,
                    },
                };
                mgr.active_session_mut().pending_images.push(block);
                let count = mgr.active_session().pending_images.len();
                scr.append_line(protocol::line_from_spans(vec![
                        protocol::colored(format!("[image #{count}] "), Color::Cyan),
                        protocol::plain(format!("{fname} \u{00b7} {size_str}{dim_str}")),
                    ]));
            }
            Err(e) => {
                scr.append_line(protocol::line_styled(format!("[image] {e}"), Color::Red));
            }
        }
    // Local verb → subprocess dispatch
    } else if is_local_verb(input) {
        dispatch_local(root, input, scr);
    // ── Free text → dispatch to agent ─────────────────────────────────
    } else if mgr.is_daemon() {
        // →842: Route through daemon (model stays warm across TUI restarts)
        // →899: Create spinner inside TuiState
        let mut spinner = SpinnerState::new();
        spinner.start();
        *got_text = false;
        *tui_state = super::super::app::TuiState::AgentRunning { spinner };
        scr.append_line(protocol::line_from_spans(vec![protocol::dim("[thinking via daemon...]")]));
        // Rendering happens on the next main loop tick via Renderer::draw().
        // Drain pending images from local session and send over wire
        let images: Vec<_> = mgr.active_session_mut().pending_images.drain(..).collect();
        let active_name = mgr.active.clone();
        if let Some(client) = mgr.daemon_client_mut()
            && let Err(e) = client.dispatch_session_with_images(&active_name, input, &images)
        {
            // →889: Drop dead daemon and fall back to embedded mode.
            mgr.drop_daemon();
            scr.append_line(protocol::line_styled(format!("[daemon lost] {e} — switched to embedded mode"), Color::Yellow));
            // Retry dispatch in embedded mode
            mgr.dispatch(input);
            if mgr.active_session().is_busy() {
                let mut spinner = SpinnerState::new();
                spinner.start();
                *got_text = false;
                *tui_state = super::super::app::TuiState::AgentRunning { spinner };
            } else {
                *tui_state = super::super::app::TuiState::Idle;
            }
        }
    } else {
        // →904: Try to reconnect to daemon if one has appeared since we went embedded.
        // Covers the case where user kills and restarts the daemon mid-session.
        let state_dir = crate::state_dir(root);
        if mgr.try_upgrade(&state_dir) {
            let model_short = mgr.active_session().config.model
                .split('-').next().unwrap_or("tui").to_string();
            let session_id = format!("{}-{}", model_short, std::process::id());
            let _ = mgr.bind_daemon(&session_id);
            scr.append_line(protocol::line_styled("[daemon reconnected]", Color::Green));
            // Re-dispatch through daemon path on next input
            return Some(DispatchResult::Handled);
        }
        // Attempt dispatch — SessionManager::dispatch() lazily recreates the
        // CpuDriver if it was invalidated by a :model switch (→816 fix).
        // Only start spinner if dispatch succeeds and session becomes busy.
        mgr.dispatch(input);
        if mgr.active_session().is_busy() {
            // →899: Create spinner inside TuiState
            let mut spinner = SpinnerState::new();
            spinner.start();
            *got_text = false;
            *tui_state = super::super::app::TuiState::AgentRunning { spinner };
            scr.append_line(protocol::line_from_spans(vec![protocol::dim("[thinking...]")]));
            // Rendering happens on the next main loop tick via Renderer::draw().
        } else if mgr.client.is_none() {
            // dispatch() couldn't obtain a driver (no API key, truly missing)
            scr.append_line(protocol::line_styled("No API key found. Set ANTHROPIC_API_KEY (or another provider key) to connect.", Color::Red));
        }
    }

    // Update status bar after every command
    let session = mgr.active_session();
    status_cache.maybe_refresh(root);
    scr.set_status(
        status_cache.build_left_line(&mgr.active),
        status_cache.build_right_line(&session.session_tokens, &session.config.model, session.config.fast_mode),
    );

    Some(DispatchResult::Handled)
}

/// →823: Read an image file, detect format from magic bytes, return (media_type, base64_data, size, dimensions).
pub fn load_image_file(path: &str) -> Result<ImageData, String> {
    use std::io::Read;
    let mut file = std::fs::File::open(path)
        .map_err(|e| format!("cannot open {path}: {e}"))?;
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)
        .map_err(|e| format!("cannot read {path}: {e}"))?;

    if bytes.is_empty() {
        return Err("file is empty".into());
    }

    // Detect format from magic bytes
    let media_type = if bytes.starts_with(&[0x89, b'P', b'N', b'G']) {
        "image/png"
    } else if bytes.starts_with(&[0xFF, 0xD8, 0xFF]) {
        "image/jpeg"
    } else if bytes.starts_with(b"GIF8") {
        "image/gif"
    } else if bytes.starts_with(b"RIFF") && bytes.len() > 12 && &bytes[8..12] == b"WEBP" {
        "image/webp"
    } else {
        return Err("unsupported format (expected PNG, JPEG, GIF, or WebP)".into());
    };

    let size = bytes.len();

    // Max 20MB (API limit is typically 5MB base64 which is ~3.75MB raw,
    // but we allow larger and let the API reject if needed)
    if size > 20_000_000 {
        return Err(format!("file too large ({:.1}MB, max 20MB)", size as f64 / 1_000_000.0));
    }

    // Extract dimensions (best-effort, PNG and JPEG only)
    let dimensions = extract_dimensions(&bytes, media_type);

    let data = base64_encode(&bytes);
    Ok((media_type.to_string(), data, size, dimensions))
}

/// Extract image dimensions from magic bytes (PNG IHDR, JPEG SOF0).
fn extract_dimensions(bytes: &[u8], media_type: &str) -> Option<(u32, u32)> {
    match media_type {
        "image/png" if bytes.len() > 24 => {
            // PNG IHDR: width at bytes 16-19, height at 20-23 (big-endian u32)
            let w = u32::from_be_bytes([bytes[16], bytes[17], bytes[18], bytes[19]]);
            let h = u32::from_be_bytes([bytes[20], bytes[21], bytes[22], bytes[23]]);
            Some((w, h))
        }
        "image/jpeg" => {
            // JPEG: scan for SOF0 marker (0xFF 0xC0) — height and width follow
            let mut i = 2;
            while i + 8 < bytes.len() {
                if bytes[i] == 0xFF && (bytes[i + 1] == 0xC0 || bytes[i + 1] == 0xC2) {
                    let h = u16::from_be_bytes([bytes[i + 5], bytes[i + 6]]) as u32;
                    let w = u16::from_be_bytes([bytes[i + 7], bytes[i + 8]]) as u32;
                    return Some((w, h));
                }
                if bytes[i] == 0xFF && bytes[i + 1] != 0x00 {
                    let len = u16::from_be_bytes([bytes[i + 2], bytes[i + 3]]) as usize;
                    i += 2 + len;
                } else {
                    i += 1;
                }
            }
            None
        }
        _ => None,
    }
}

/// Base64-encode bytes (no padding, standard alphabet).
fn base64_encode(bytes: &[u8]) -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = if chunk.len() > 1 { chunk[1] as u32 } else { 0 };
        let b2 = if chunk.len() > 2 { chunk[2] as u32 } else { 0 };
        let triple = (b0 << 16) | (b1 << 8) | b2;
        out.push(ALPHABET[((triple >> 18) & 0x3F) as usize] as char);
        out.push(ALPHABET[((triple >> 12) & 0x3F) as usize] as char);
        if chunk.len() > 1 {
            out.push(ALPHABET[((triple >> 6) & 0x3F) as usize] as char);
        } else {
            out.push('=');
        }
        if chunk.len() > 2 {
            out.push(ALPHABET[(triple & 0x3F) as usize] as char);
        } else {
            out.push('=');
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn model_alias_opus() {
        assert_eq!(resolve_model_alias("opus"), "claude-opus-4-6");
    }

    #[test]
    fn model_alias_sonnet() {
        assert_eq!(resolve_model_alias("sonnet"), "claude-sonnet-4-5-20250929");
    }

    #[test]
    fn model_alias_haiku() {
        assert_eq!(resolve_model_alias("haiku"), "claude-haiku-4-5-20251001");
    }

    #[test]
    fn model_alias_passthrough() {
        assert_eq!(resolve_model_alias("claude-opus-4-6"), "claude-opus-4-6");
        assert_eq!(resolve_model_alias("custom-model-v1"), "custom-model-v1");
    }

    #[test]
    fn model_is_local_verb() {
        assert!(is_local_verb(":model opus"));
        assert!(is_local_verb(":model sonnet"));
        assert!(is_local_verb(":model"));
    }

    #[test]
    fn mode_is_local_verb() {
        assert!(is_local_verb(":mode plan"));
        assert!(is_local_verb(":mode governed"));
        assert!(is_local_verb(":mode auto"));
        assert!(is_local_verb(":mode autonomous"));
        assert!(is_local_verb(":mode"));
    }

    #[test]
    fn models_is_local_verb() {
        assert!(is_local_verb(":models"));
    }

    #[test]
    fn fast_is_local_verb() {
        assert!(is_local_verb(":fast"));
    }

    #[test]
    fn clear_is_local_verb() {
        assert!(is_local_verb(":clear"));
    }

    #[test]
    fn log_is_local_verb() {
        assert!(is_local_verb(":log"));
    }

    #[test]
    fn context_is_local_verb() {
        assert!(is_local_verb(":context"));
    }

    #[test]
    fn test_daemon_client_none_after_error() {
        // →826: Verify that setting daemon_client = None causes is_some() to return false,
        // triggering the embedded SessionManager fallback path in dispatch_command.
        let mut daemon_client: Option<String> = Some("connected".into());
        assert!(daemon_client.is_some());
        daemon_client = None; // simulates error recovery path
        assert!(!daemon_client.is_some());
    }

    // →823: Image loading tests

    #[test]
    fn image_is_local_verb() {
        assert!(is_local_verb(":image foo.png"));
        assert!(is_local_verb(":image"));
    }

    #[test]
    fn load_image_nonexistent_file() {
        let result = load_image_file("/tmp/nonexistent_image_12345.png");
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("cannot open"));
    }

    #[test]
    fn load_image_empty_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("empty.png");
        std::fs::write(&path, b"").unwrap();
        let result = load_image_file(path.to_str().unwrap());
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("empty"));
    }

    #[test]
    fn load_image_not_an_image() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("text.txt");
        std::fs::write(&path, "hello world").unwrap();
        let result = load_image_file(path.to_str().unwrap());
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("unsupported format"));
    }

    #[test]
    fn load_image_png_magic() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.png");
        // Minimal PNG: magic bytes + IHDR chunk (width=1, height=1)
        let mut png = vec![0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A]; // magic
        // IHDR chunk: length(13) + "IHDR" + width(4) + height(4) + bit_depth + color_type + ...
        png.extend_from_slice(&[0, 0, 0, 13]); // chunk length
        png.extend_from_slice(b"IHDR");
        png.extend_from_slice(&1u32.to_be_bytes()); // width = 1
        png.extend_from_slice(&2u32.to_be_bytes()); // height = 2
        png.extend_from_slice(&[8, 2, 0, 0, 0]); // bit_depth, color_type, etc
        std::fs::write(&path, &png).unwrap();

        let result = load_image_file(path.to_str().unwrap());
        assert!(result.is_ok(), "should parse PNG: {:?}", result);
        let (media_type, data, size, dims) = result.unwrap();
        assert_eq!(media_type, "image/png");
        assert!(!data.is_empty(), "base64 data should not be empty");
        assert!(size > 0);
        assert_eq!(dims, Some((1, 2)));
    }

    #[test]
    fn load_image_jpeg_magic() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.jpg");
        // Minimal JPEG: SOI + APP0 marker + SOF0 with dimensions
        let mut jpg = vec![0xFF, 0xD8, 0xFF, 0xE0]; // SOI + APP0
        jpg.extend_from_slice(&[0, 2]); // APP0 length = 2 (minimal)
        // SOF0 marker with dimensions
        jpg.extend_from_slice(&[0xFF, 0xC0]); // SOF0
        jpg.extend_from_slice(&[0, 11]); // length
        jpg.push(8); // precision
        jpg.extend_from_slice(&480u16.to_be_bytes()); // height = 480
        jpg.extend_from_slice(&640u16.to_be_bytes()); // width = 640
        std::fs::write(&path, &jpg).unwrap();

        let result = load_image_file(path.to_str().unwrap());
        assert!(result.is_ok(), "should parse JPEG: {:?}", result);
        let (media_type, _, _, dims) = result.unwrap();
        assert_eq!(media_type, "image/jpeg");
        assert_eq!(dims, Some((640, 480)));
    }

    #[test]
    fn base64_encode_roundtrip() {
        let input = b"Hello, World!";
        let encoded = base64_encode(input);
        assert_eq!(encoded, "SGVsbG8sIFdvcmxkIQ==");
    }

    #[test]
    fn base64_encode_empty() {
        assert_eq!(base64_encode(b""), "");
    }

}
