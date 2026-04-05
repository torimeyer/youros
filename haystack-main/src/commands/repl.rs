//! fcp-repl — plain stdin/stdout REPL driver.
//!
//! Instant startup. No SessionManager, no tokio at init, no crossterm.
//! CpuDriver created lazily on first message. Uses agent_loop directly.

use std::io::{self, BufRead, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use crate::agentfile;
use crate::cpu::agent_loop::{self, CpuEvent};
use crate::cpu::{config_from_agentfile, CpuDriver, PermissionMode};
use crate::cpu::anthropic::{Message, ContentBlock};
use crate::kernel::approval as approval_bus;

const GREEN: &str = "\x1b[32m";
const RED: &str = "\x1b[31m";
const YELLOW: &str = "\x1b[33m";
const CYAN: &str = "\x1b[36m";
const DIM: &str = "\x1b[2m";
const BOLD: &str = "\x1b[1m";
const RESET: &str = "\x1b[0m";
const PAD: &str = "    "; // 4-char left margin

/// Strip basic markdown formatting for terminal display.
fn strip_markdown(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for line in text.lines() {
        let l = line.trim_start();
        // Headers → bold
        if let Some(rest) = l.strip_prefix("### ") {
            out.push_str(&format!("{BOLD}{rest}{RESET}"));
        } else if let Some(rest) = l.strip_prefix("## ") {
            out.push_str(&format!("{BOLD}{rest}{RESET}"));
        } else if let Some(rest) = l.strip_prefix("# ") {
            out.push_str(&format!("{BOLD}{rest}{RESET}"));
        } else {
            // Strip **bold** → ANSI bold
            let mut s = l.to_string();
            while let Some(start) = s.find("**") {
                let after = start + 2;
                if let Some(end) = s[after..].find("**") {
                    let inner = &s[after..after + end].to_string();
                    s = format!("{}{BOLD}{inner}{RESET}{}", &s[..start], &s[after + end + 2..]);
                } else {
                    break;
                }
            }
            // Strip `code` → plain (terminal doesn't need backtick decoration)
            s = s.replace('`', "");
            out.push_str(&s);
        }
        out.push('\n');
    }
    // Trim trailing newlines
    while out.ends_with('\n') { out.pop(); }
    out
}

fn is_quit(input: &str) -> bool {
    matches!(
        input.trim().to_lowercase().as_str(),
        ":quit" | ":q" | "exit" | "/quit" | "quit" | ":exit" | "/exit"
    )
}

fn parse_command(input: &str) -> Option<(&str, &str)> {
    let trimmed = input.trim();
    if let Some(rest) = trimmed.strip_prefix(':') {
        let mut parts = rest.splitn(2, char::is_whitespace);
        let stem = parts.next()?;
        let args = parts.next().unwrap_or("").trim();
        Some((stem, args))
    } else {
        None
    }
}

fn resolve_model_alias(name: &str) -> &str {
    crate::cpu::providers::resolve_model_alias(name)
}

fn format_tokens(n: u64) -> String {
    if n >= 1000 { format!("{:.1}k", n as f64 / 1000.0) } else { format!("{n}") }
}

fn print_help() {
    println!(
        "{BOLD}REPL commands{RESET}
  {GREEN}:model <name>{RESET}   Switch model (opus, sonnet, local, qwen, deepseek)
  {GREEN}:models{RESET}        List available models
  {GREEN}:mode <name>{RESET}   Switch permission mode
  {GREEN}:image <path>{RESET}  Attach image to next message
  {GREEN}:needles{RESET}       Show needle counts
  {GREEN}:clear{RESET}         Clear session
  {GREEN}:help{RESET}          Show this help
  {GREEN}:quit{RESET}          Exit"
    );
}

/// Render a single CpuEvent to stdout (shared by daemon and standalone paths).
fn render_repl_event(event: &CpuEvent) {
    match event {
        CpuEvent::TextDelta(_) => {}
        CpuEvent::TextComplete(text) => {
            if !text.trim().is_empty() {
                let rendered = strip_markdown(text);
                println!();
                for line in rendered.lines() {
                    println!("{PAD}{CYAN}{line}{RESET}");
                }
            }
        }
        CpuEvent::ToolStart { name, .. } => {
            print!("{PAD}{DIM}\u{2699} {name}{RESET}");
            let _ = io::stdout().flush();
        }
        CpuEvent::ToolResult { name, output, success } => {
            let trunc = crate::util::safe_truncate(output, 120);
            if *success {
                println!("{PAD}{GREEN}\u{2713} {name}{RESET} {DIM}{trunc}{RESET}");
            } else {
                println!("{PAD}{RED}\u{2717} {name}{RESET} {DIM}{trunc}{RESET}");
            }
        }
        CpuEvent::TurnComplete { usage } => {
            println!("{PAD}{DIM}[done] \u{2193}{} \u{2191}{}{RESET}",
                format_tokens(usage.input_tokens),
                format_tokens(usage.output_tokens));
        }
        CpuEvent::Error(msg) => {
            println!("{PAD}{RED}[error] {msg}{RESET}");
        }
        _ => {}
    }
}

fn handle_approval() {
    while let Some(req) = approval_bus::next_pending() {
        println!(
            "\n{YELLOW}[approval]{RESET} {BOLD}{}{RESET}: {} {DIM}\"{}\"{RESET}",
            req.agent_alias, req.tool_name, req.tool_input_preview,
        );
        print!("  {BOLD}[y]es / [n]o / [a]lways?{RESET} ");
        let _ = io::stdout().flush();
        let mut response = String::new();
        let _ = io::stdin().read_line(&mut response);
        let decision = match response.trim().to_lowercase().as_str() {
            "y" | "yes" => approval_bus::ApprovalDecision::Allow,
            "a" | "always" => approval_bus::ApprovalDecision::AlwaysAllow,
            _ => approval_bus::ApprovalDecision::Deny,
        };
        approval_bus::respond(&req.id, decision);
    }
}

pub fn run() -> Result<(), String> {
    let root = crate::find_project_root()?;
    let ostk_dir = crate::state_dir(&root);

    // ── Instant startup: read config, don't create driver ────────────
    // Resolution: ./Agentfile → .ostk/scheduler.af (legacy)
    let scheduler_af_path = crate::agentfile::resolve_default_agentfile(&root)
        .ok_or_else(|| "no Agentfile found (create ./Agentfile or .ostk/scheduler.af)".to_string())?;
    let af_content = std::fs::read_to_string(&scheduler_af_path)
        .map_err(|e| format!("{}: {e}", scheduler_af_path.display()))?;
    let af = agentfile::parse(&af_content)
        .map_err(|e| format!("{} parse: {e}", scheduler_af_path.display()))?;
    let mut cpu_config = config_from_agentfile(&af, &ostk_dir);
    if cpu_config.max_turns.is_none() {
        cpu_config.max_turns = Some(25);
    }

    // Resolve model from env/HUMANFILE
    if cpu_config.model == "auto" {
        cpu_config.model = crate::commands::run::resolve_auto_model();
    }

    // Available models from HUMANFILE (no API call)
    let hf_result = crate::humanfile::load(&ostk_dir);
    let available_models = hf_result.humanfile.available_models;

    // Boot context for system prompt enrichment
    let boot_context = crate::cpu::session::BootContext::new(&root);
    if let Some(ref base_prompt) = cpu_config.system_prompt {
        cpu_config.system_prompt = Some(boot_context.build_system_prompt(base_prompt));
    }

    let config = cpu_config.into_loop_config(Some(root.clone()));

    // Welcome — instant, no blocking
    let version = env!("CARGO_PKG_VERSION");
    println!("{BOLD}ostk repl{RESET} v{version}");
    println!("  model: {GREEN}{}{RESET}  mode: {CYAN}{}{RESET}",
        config.model, config.permission_mode.label());
    println!("  Type {GREEN}:help{RESET} for commands.\n");

    // →842: Auto-start daemon and connect (model stays warm).
    // Falls back to standalone agent_loop if daemon can't start.
    let mut daemon_client = crate::serve::client::auto_connect(&ostk_dir);
    let mut daemon_mode = daemon_client.is_some();
    if daemon_mode {
        println!("  {GREEN}[daemon] connected \u{2014} model stays warm{RESET}");
    }

    // ── Ctrl+C interrupts agent loop, doesn't kill REPL ────────────
    let interrupted = Arc::new(AtomicBool::new(false));
    let int_flag = Arc::clone(&interrupted);
    let _ = ctrlc::set_handler(move || {
        int_flag.store(true, Ordering::SeqCst);
    });

    // ── State ────────────────────────────────────────────────────────
    let mut messages: Vec<Message> = Vec::new();
    let mut model = config.model.clone();
    let mut permission_mode = config.permission_mode;
    let mut driver: Option<Arc<dyn CpuDriver>> = None; // lazy
    let mut pending_images: Vec<ContentBlock> = Vec::new(); // →823

    let stdin = io::stdin();
    let mut line_buf = String::new();

    loop {
        print!("{PAD}{BOLD}> {RESET}");
        let _ = io::stdout().flush();

        line_buf.clear();
        match stdin.lock().read_line(&mut line_buf) {
            Ok(0) => break,
            Err(e) => { eprintln!("{RED}stdin error: {e}{RESET}"); break; }
            Ok(_) => {}
        }

        let input = line_buf.trim();
        if input.is_empty() { continue; }
        if is_quit(input) { break; }

        // ── Meta-commands ────────────────────────────────────────────
        if let Some((stem, args)) = parse_command(input) {
            match stem {
                "help" | "h" => { print_help(); continue; }
                "model" => {
                    if args.is_empty() {
                        println!("  model: {GREEN}{model}{RESET}");
                    } else {
                        model = resolve_model_alias(args).to_string();
                        driver = None; // force recreation
                        println!("  {GREEN}Switched to {model}{RESET}");
                    }
                    continue;
                }
                "models" => {
                    if available_models.is_empty() {
                        println!("  {YELLOW}No models in HUMANFILE llm.available{RESET}");
                    } else {
                        for m in &available_models {
                            let mark = if *m == model { " {CYAN}<-{RESET}" } else { "" };
                            println!("    {m}{mark}");
                        }
                    }
                    continue;
                }
                "mode" => {
                    if args.is_empty() {
                        println!("  mode: {GREEN}{}{RESET}", permission_mode.label());
                    } else if let Some(m) = PermissionMode::from_str(args) {
                        permission_mode = m;
                        println!("  {GREEN}Mode: {}{RESET}", m.label());
                    } else {
                        println!("  {RED}Unknown mode. Use: plan, governed, auto, autonomous{RESET}");
                    }
                    continue;
                }
                "clear" => {
                    messages.clear();
                    println!("  {GREEN}[cleared]{RESET}");
                    continue;
                }
                "needles" => {
                    if let Ok(needles) = crate::read_needles(&root) {
                        let open = needles.iter()
                            .filter(|n| n.get("status").and_then(|s| s.as_str()) == Some("open"))
                            .count();
                        println!("  {open} open / {} total", needles.len());
                    }
                    continue;
                }
                "image" => {
                    if args.is_empty() {
                        println!("  {RED}usage: :image /path/to/file.png{RESET}");
                    } else {
                        // Expand ~ and resolve relative paths
                        let expanded = if args.starts_with('~') {
                            std::env::var("HOME").map(|h| args.replacen('~', &h, 1))
                                .unwrap_or_else(|_| args.to_string())
                        } else if !args.starts_with('/') {
                            root.join(args).to_string_lossy().to_string()
                        } else {
                            args.to_string()
                        };
                        match crate::fcp_screen::components::dispatch::load_image_file(&expanded) {
                            Ok((media_type, data, size, dims)) => {
                                let size_str = if size > 1_000_000 {
                                    format!("{:.1}MB", size as f64 / 1_000_000.0)
                                } else {
                                    format!("{:.0}KB", size as f64 / 1_000.0)
                                };
                                let dim_str = dims.map(|(w, h)| format!(" {w}\u{00d7}{h}")).unwrap_or_default();
                                let fname = std::path::Path::new(&expanded)
                                    .file_name().map(|f| f.to_string_lossy().to_string())
                                    .unwrap_or_else(|| expanded.clone());
                                pending_images.push(ContentBlock::Image {
                                    source: crate::cpu::anthropic::ImageSource {
                                        source_type: "base64".into(),
                                        media_type,
                                        data,
                                    },
                                });
                                println!("  {CYAN}[image #{}]{RESET} {fname} \u{00b7} {size_str}{dim_str}",
                                    pending_images.len());
                            }
                            Err(e) => println!("  {RED}[image] {e}{RESET}"),
                        }
                    }
                    continue;
                }
                _ => {} // fall through to dispatch
            }
        }

        // →842: Route through daemon if available, else standalone agent_loop.
        // →826: On daemon death, fall through to standalone to re-dispatch this turn.
        let mut dispatched = false;
        if daemon_mode {
            let client = daemon_client.as_mut().unwrap();
            match client.dispatch_session("scheduler", input) {
                Err(_) => {
                    // →826: Daemon died at dispatch — switch to standalone, re-dispatch this turn.
                    println!("{PAD}{YELLOW}[daemon lost — switched to standalone mode]{RESET}");
                    daemon_mode = false;
                    daemon_client = None;
                }
                Ok(_) => {
                    dispatched = true;
                    // Poll events until turn completes
                    interrupted.store(false, Ordering::SeqCst);
                    loop {
                        if interrupted.load(Ordering::SeqCst) {
                            println!("\n{PAD}{YELLOW}[interrupted]{RESET}");
                            break;
                        }
                        match client.poll_events("scheduler") {
                            Ok(val) => {
                                if let Some(events) = val.get("events").and_then(|e| e.as_array()) {
                                    let mut turn_done = false;
                                    for ev_val in events {
                                        if let Ok(ev) = serde_json::from_value::<CpuEvent>(ev_val.clone()) {
                                            render_repl_event(&ev);
                                            if matches!(ev, CpuEvent::TurnComplete { .. } | CpuEvent::Error(_)) {
                                                turn_done = true;
                                            }
                                        }
                                    }
                                    if turn_done { break; }
                                    if events.is_empty() {
                                        std::thread::sleep(std::time::Duration::from_millis(80));
                                    }
                                } else {
                                    std::thread::sleep(std::time::Duration::from_millis(80));
                                }
                            }
                            Err(_) => {
                                // →826: Daemon died mid-turn — switch to standalone for next input.
                                println!("{PAD}{YELLOW}[daemon lost — switched to standalone mode]{RESET}");
                                daemon_mode = false;
                                daemon_client = None;
                                break;
                            }
                        }
                        handle_approval();
                    }
                }
            }
        }
        if !dispatched {
            // ── Standalone: lazy driver + agent_loop ─────────────────
            if driver.is_none() {
                print!("{DIM}  connecting to {model}...{RESET}");
                let _ = io::stdout().flush();
                match crate::cpu::create_driver(&model) {
                    Ok(d) => {
                        driver = Some(d);
                        println!(" {GREEN}ok{RESET}");
                    }
                    Err(e) => {
                        println!(" {RED}failed: {e}{RESET}");
                        continue;
                    }
                }
            }

            let client = driver.as_ref().unwrap();
            let (event_tx, mut event_rx) = tokio::sync::mpsc::channel::<CpuEvent>(64);

            let mut turn_config = config.clone();
            turn_config.model = model.clone();
            turn_config.permission_mode = permission_mode;

            // →823: Include any pending image attachments
            let mut content: Vec<ContentBlock> = std::mem::take(&mut pending_images);
            content.push(ContentBlock::Text { text: input.to_string() });
            messages.push(Message {
                role: "user".into(),
                content,
                model: None,
            });

            let api = Arc::clone(client);
            let shared_messages = agent_loop::SharedMessages::new(messages.clone());
            let shared_for_thread = shared_messages.clone();
            let turn_config_clone = turn_config.clone();
            let tx = event_tx.clone();
            drop(event_tx);

            let rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|e| format!("tokio: {e}"))?;

            let handle = std::thread::spawn(move || {
                rt.block_on(async {
                    let _ = agent_loop::run_loop(
                        &*api, &turn_config_clone, &shared_for_thread, tx,
                        Default::default(),
                    ).await;
                });
                // Extract final messages from shared state
                shared_messages.snapshot()
            });

            let drain_rt = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|e| format!("tokio drain: {e}"))?;

            interrupted.store(false, Ordering::SeqCst);
            let int_check = Arc::clone(&interrupted);
            drain_rt.block_on(async {
                while let Some(event) = event_rx.recv().await {
                    if int_check.load(Ordering::SeqCst) {
                        println!("\n{PAD}{YELLOW}[interrupted]{RESET}");
                        break;
                    }
                    render_repl_event(&event);
                    handle_approval();
                }
            });

            if let Ok(updated) = handle.join() {
                messages = updated;
            }
        }
    }

    approval_bus::deny_all();
    println!("{DIM}goodbye{RESET}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_repl_quit_commands() {
        assert!(is_quit(":quit"));
        assert!(is_quit(":q"));
        assert!(is_quit("exit"));
        assert!(is_quit("/quit"));
        assert!(!is_quit("hello"));
        assert!(!is_quit(":model"));
    }

    #[test]
    fn test_repl_parse_command() {
        assert_eq!(parse_command(":model opus"), Some(("model", "opus")));
        assert_eq!(parse_command(":help"), Some(("help", "")));
        assert_eq!(parse_command("hello"), None);
    }

    #[test]
    fn test_repl_empty_input_skipped() {
        assert!("".trim().is_empty());
        assert!("   ".trim().is_empty());
        assert!(!"hello".trim().is_empty());
    }

    #[test]
    fn test_format_tokens() {
        assert_eq!(format_tokens(500), "500");
        assert_eq!(format_tokens(1500), "1.5k");
        assert_eq!(format_tokens(42000), "42.0k");
    }
}
