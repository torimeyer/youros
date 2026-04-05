//! Interactive guided setup — 7-step configuration flow.
//!
//! Called by `ostk init --guided`. Every step is skippable.
//! Uses crossterm-based widgets from `widget.rs`.

use super::widget::{self, MenuSelector, MenuItem, MenuResult, ToggleList, ToggleItem};
use std::fs;
use std::process::Command;

/// Run the 7-step guided setup.
pub fn run() -> Result<(), String> {
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;

    if !std::io::IsTerminal::is_terminal(&std::io::stdin()) {
        return Err("guided setup requires an interactive terminal. Use `ostk init` for headless setup.".into());
    }

    println!();
    println!("  \x1b[1mostk\x1b[0m — interactive setup");
    println!("  \x1b[2mEvery step is skippable. Press s or Esc to skip.\x1b[0m");

    // Step 1: Repository
    let has_git = cwd.join(".git").is_dir();
    if has_git {
        println!();
        println!("  \x1b[32m✓\x1b[0m Repository: git detected");
    } else {
        let mut sel = MenuSelector::new("Step 1/7", "Repository", vec![
            MenuItem {
                label: "Initialize git".into(),
                value: "init".into(),
                description: "run git init in this directory".into(),
            },
            MenuItem {
                label: "Skip".into(),
                value: "skip".into(),
                description: "I'll set up git myself later".into(),
            },
        ], false);

        match sel.run()? {
            MenuResult::Selected(v) if v == "init" => {
                let status = Command::new("git").arg("init").current_dir(&cwd).status()
                    .map_err(|e| format!("git init: {e}"))?;
                if status.success() {
                    // Re-run init to install hooks now that git exists
                    crate::commands::init::run()?;
                    println!("  \x1b[32m✓\x1b[0m Repository: git initialized + hooks installed");
                } else {
                    eprintln!("  \x1b[33m!\x1b[0m git init failed");
                }
            }
            _ => {
                println!("  \x1b[2m– Repository: skipped\x1b[0m");
            }
        }
    }

    // Step 2: Isolation Level
    let mut iso_sel = MenuSelector::new("Step 2/7", "Isolation Level", vec![
        MenuItem {
            label: "Open".into(),
            value: "open".into(),
            description: "full agent access, minimal ceremony (solo dev)".into(),
        },
        MenuItem {
            label: "Governed".into(),
            value: "governed".into(),
            description: "capability pins, destructive ops need approval (teams)".into(),
        },
        MenuItem {
            label: "Sealed".into(),
            value: "sealed".into(),
            description: "strict enforcement, GPG required, VM-ready (production)".into(),
        },
    ], true);

    let isolation = match iso_sel.run()? {
        MenuResult::Selected(v) => v,
        MenuResult::Skipped => "open".to_string(),
    };

    // Write pin.caps based on isolation level
    write_isolation_config(&cwd, &isolation)?;

    // Step 3: Identity & GPG
    let _gpg_key = step_identity_gpg(&cwd, &isolation)?;

    // Step 4: AI Provider
    let provider = step_provider()?;

    // Step 5: Autonomy Grants
    step_autonomy(&cwd, &isolation)?;

    // Step 6: API Key
    step_api_key(&provider)?;

    // Step 7: Summary
    println!();
    println!("  \x1b[1m.ostk/ is ready.\x1b[0m What's next:");
    println!();
    println!("    \x1b[32mostk\x1b[0m              open TUI, start working");
    println!("    \x1b[32mostk boot\x1b[0m          see OS state");
    println!("    \x1b[32mostk hay \"...\"\x1b[0m     capture your first thought");
    println!();
    println!("  The kernel is ready. Run \x1b[32mostk\x1b[0m to boot.");
    println!();

    Ok(())
}

fn write_isolation_config(cwd: &std::path::Path, isolation: &str) -> Result<(), String> {
    let state = crate::state_dir(cwd);
    let pin_dir = state.join("pins").join("default");
    fs::create_dir_all(&pin_dir).map_err(|e| format!("pins/: {e}"))?;

    let caps = match isolation {
        "governed" => "read: .ostk/ .language\n\
                       write: .ostk/store/default/ .ostk/needles/ .ostk/hay/\n\
                       execute: shell(readonly)\n\
                       deny: write-kernel modify-governance\n",
        "sealed" => "read: .ostk/ .language\n\
                     write: .ostk/store/default/ .ostk/needles/ .ostk/hay/\n\
                     execute: shell(explicit)\n\
                     deny: write-kernel modify-governance write-src\n",
        _ => "read: .ostk/ .language\n\
              write: .ostk/store/default/ .ostk/needles/ .ostk/hay/\n\
              execute: shell(full)\n\
              deny: write-kernel modify-governance\n",
    };
    fs::write(pin_dir.join("pin.caps"), caps)
        .map_err(|e| format!("write pin.caps: {e}"))?;

    // Write Agentfile at project root (new location) with appropriate permissions
    let perm = match isolation {
        "sealed" => "sealed",
        "governed" => "governed",
        _ => "autonomous",
    };
    let af = format!(
        "# Scheduler — default agent\n\
         FROM auto\n\
         PROMPT \"You are the scheduling intelligence for this project.\"\n\
         TOOL shell\n\
         TOOL file:read\n\
         TOOL file:edit\n\
         LIMIT tokens 200000\n\
         LIMIT permissions {perm}\n\
         BOOT ostk boot\n\
         PIN default\n"
    );
    fs::write(cwd.join("Agentfile"), &af)
        .map_err(|e| format!("write Agentfile: {e}"))?;

    Ok(())
}

fn step_identity_gpg(cwd: &std::path::Path, isolation: &str) -> Result<Option<String>, String> {
    // Show detected identity
    let host = crate::kernel::host_identity::discover();
    println!();
    println!("  \x1b[2mIdentity detected:\x1b[0m");
    println!("    Name:  {}", host.full_name);
    println!("    Email: {}", host.git_email);

    // Try to find GPG keys
    let gpg_keys = discover_gpg_keys();

    if gpg_keys.is_empty() {
        // →911: Offer to generate a GPG key instead of just saying "add one later"
        let mut sel = MenuSelector::new("Step 3/7", "GPG Identity", vec![
            MenuItem {
                label: "Generate key".into(),
                value: "generate".into(),
                description: format!("create a GPG key for {} <{}>", host.full_name, host.git_email),
            },
            MenuItem {
                label: "Skip".into(),
                value: "skip".into(),
                description: "continue without GPG — read-only mode (T3 trust)".into(),
            },
        ], false);

        match sel.run()? {
            MenuResult::Selected(v) if v == "generate" => {
                println!();
                println!("  Generating GPG key for {} <{}>...", host.full_name, host.git_email);
                let status = Command::new("gpg")
                    .args(["--batch", "--quick-generate-key",
                           &format!("{} <{}>", host.full_name, host.git_email),
                           "ed25519", "sign"])
                    .status()
                    .map_err(|e| format!("gpg --quick-generate-key: {e}"))?;
                if status.success() {
                    // Re-discover keys after generation
                    let new_keys = discover_gpg_keys();
                    if let Some((key_id, uid)) = new_keys.first() {
                        println!("  \x1b[32m✓\x1b[0m GPG key created: {} ({})", short_key_id(key_id), uid);
                        // Sign HUMANFILE with the new key
                        let state = crate::state_dir(cwd);
                        match crate::commands::sign::sign_humanfile(&state, key_id) {
                            Ok(()) => println!("  \x1b[32m✓\x1b[0m HUMANFILE signed (T1 trust)"),
                            Err(e) => eprintln!("  \x1b[33m!\x1b[0m Could not sign: {e}"),
                        }
                        create_global_humanfile(&host, Some(key_id));
                        println!();
                        println!("  \x1b[2mTo publish on GitHub:\x1b[0m  gpg --armor --export {} | gh gpg-key add -", key_id);
                        return Ok(Some(key_id.clone()));
                    }
                } else {
                    eprintln!("  \x1b[33m!\x1b[0m GPG key generation failed");
                }
            }
            _ => {}
        }
        let trust = if isolation == "sealed" {
            eprintln!("  \x1b[33m!\x1b[0m Sealed isolation recommends GPG. Operating at T3 trust.");
            "T3"
        } else {
            "T3"
        };
        println!();
        println!("  \x1b[32m✓\x1b[0m Identity: {} <{}> ({})", host.full_name, host.git_email, trust);
        println!("  \x1b[2m  No GPG key. Writes disabled. Generate one: gpg --full-generate-key\x1b[0m");
        create_global_humanfile(&host, None);
        return Ok(None);
    }

    // Build menu items from GPG keys
    let mut items: Vec<MenuItem> = gpg_keys.iter().map(|(id, uid)| MenuItem {
        label: short_key_id(id).to_string(),
        value: id.clone(),
        description: uid.clone(),
    }).collect();
    items.push(MenuItem {
        label: "None".into(),
        value: "none".into(),
        description: "skip GPG — OS operates at T2 trust".into(),
    });

    let mut sel = MenuSelector::new("Step 3/7", "GPG Identity", items, true);
    let key = match sel.run()? {
        MenuResult::Selected(v) if v != "none" => {
            // Sign HUMANFILE
            let state = crate::state_dir(cwd);
            match crate::commands::sign::sign_humanfile(&state, &v) {
                Ok(()) => println!("  \x1b[32m✓\x1b[0m HUMANFILE signed (T0 trust)"),
                Err(e) => eprintln!("  \x1b[33m!\x1b[0m Could not sign: {e}"),
            }

            // Create global ~/.HUMANFILE if needed
            create_global_humanfile(&host, Some(&v));

            Some(v)
        }
        _ => {
            create_global_humanfile(&host, None);
            None
        }
    };

    Ok(key)
}

fn step_provider() -> Result<String, String> {
    // Auto-detect from environment
    let detected = crate::kernel::defaults::detect_api_key();
    let preselect = detected.map(|(_, provider)| provider);

    let mut sel = MenuSelector::new("Step 4/7", "AI Provider", vec![
        MenuItem { label: "Anthropic".into(), value: "anthropic".into(), description: "Claude models".into() },
        MenuItem { label: "Google".into(), value: "google".into(), description: "Gemini models".into() },
        MenuItem { label: "OpenRouter".into(), value: "openrouter".into(), description: "any model via OpenRouter".into() },
        MenuItem { label: "Ollama".into(), value: "ollama".into(), description: "local models (no API key needed)".into() },
        MenuItem { label: "Skip".into(), value: "skip".into(), description: "configure later in HUMANFILE".into() },
    ], true);

    if let Some(p) = preselect {
        sel.preselect(p);
    }

    let provider = match sel.run()? {
        MenuResult::Selected(v) => v,
        MenuResult::Skipped => "skip".to_string(),
    };

    // Write model preference to HUMANFILE if not skip
    if provider != "skip" {
        let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
        let hf_path = crate::state_dir(&cwd).join("HUMANFILE");
        if hf_path.exists() {
            let existing = fs::read_to_string(&hf_path).unwrap_or_default();
            if !existing.contains("\nllm:") && !existing.starts_with("llm:") {
                let model = match provider.as_str() {
                    "anthropic" => "claude-sonnet-4-6",
                    "google" => "gemini-2.5-pro",
                    "openrouter" => "anthropic/claude-sonnet-4-6",
                    "ollama" => "auto",
                    _ => "auto",
                };
                let addition = format!("\n## LLM\n\nllm:\n  model: {model}\n  fallback: auto\n");
                let updated = format!("{existing}{addition}");
                let _ = fs::write(&hf_path, updated);
            }
        }
    }

    Ok(provider)
}

fn step_autonomy(cwd: &std::path::Path, isolation: &str) -> Result<(), String> {
    let defaults = match isolation {
        "sealed" => (false, false, false, false),
        "governed" => (true, true, false, false),
        _ => (true, true, true, true),
    };

    let mut toggle = ToggleList::new("Step 5/7", "Autonomy Grants", vec![
        ToggleItem { label: "File writes".into(), value: defaults.0 },
        ToggleItem { label: "Test runs".into(), value: defaults.1 },
        ToggleItem { label: "Git commits".into(), value: defaults.2 },
        ToggleItem { label: "External APIs".into(), value: defaults.3 },
    ], true);

    let result = toggle.run()?;

    // Write autonomy to HUMANFILE
    if let Some(res) = result {
        let hf_path = crate::state_dir(cwd).join("HUMANFILE");
        if hf_path.exists() {
            let existing = fs::read_to_string(&hf_path).unwrap_or_default();
            if !existing.contains("## Autonomy") {
                let mut section = String::from("\n## Autonomy grants\n");
                for (label, approved) in &res.values {
                    let status = if *approved { "approved without confirmation" } else { "ask first" };
                    section.push_str(&format!("- {label}: {status}\n"));
                }
                let _ = fs::write(&hf_path, format!("{existing}{section}"));
            }
        }
    }

    Ok(())
}

fn step_api_key(provider: &str) -> Result<(), String> {
    if provider == "ollama" || provider == "skip" {
        if provider == "ollama" {
            println!();
            println!("  \x1b[32m✓\x1b[0m API Key: Ollama — no key needed");
        }
        return Ok(());
    }

    let (key_name, hint) = match provider {
        "anthropic" => ("ANTHROPIC_API_KEY", "https://console.anthropic.com/keys"),
        "google" => ("GEMINI_API_KEY", "https://aistudio.google.com/apikey"),
        "openrouter" => ("OPENROUTER_API_KEY", "https://openrouter.ai/keys"),
        _ => return Ok(()),
    };

    // Check if already in environment
    if std::env::var(key_name).is_ok_and(|v| !v.is_empty()) {
        println!();
        println!("  \x1b[32m✓\x1b[0m API Key: {key_name} detected in environment");
        return Ok(());
    }

    println!();
    println!("  \x1b[1mStep 6/7 — API Key\x1b[0m");
    println!();
    println!("  Agents need {key_name}.");
    println!("  Get one at: \x1b[2m{hint}\x1b[0m");
    println!();

    if widget::confirm("Set API key now?", false) {
        crate::commands::secret::run_set(key_name, None)?;
    } else {
        println!("  \x1b[2m– Skipped. Set later: ostk secret set {key_name}\x1b[0m");
    }

    Ok(())
}

/// Discover available GPG secret keys. Returns vec of (key_id, uid).
fn discover_gpg_keys() -> Vec<(String, String)> {
    let output = match Command::new("gpg")
        .args(["--list-secret-keys", "--keyid-format", "LONG", "--with-colons"])
        .output()
    {
        Ok(o) if o.status.success() => o,
        _ => return vec![],
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut keys = Vec::new();
    let mut current_id = String::new();

    for line in stdout.lines() {
        let fields: Vec<&str> = line.split(':').collect();
        if fields.len() < 5 { continue; }
        if fields[0] == "sec" {
            current_id = fields[4].to_string();
        } else if fields[0] == "uid" && !current_id.is_empty() {
            let uid = fields.get(9).unwrap_or(&"").to_string();
            if !uid.is_empty() {
                keys.push((current_id.clone(), uid));
            }
        }
    }

    keys
}

/// Display-friendly short key ID — last 8 hex chars (what GPG resolves).
/// GPG `--with-colons` emits the full 16-char key ID in fields[4], e.g.
/// `B3746DDBF66103C6`. `gpg -u` resolves short IDs from the *low* 8 chars
/// (`F66103C6`), not the high 8. Truncating from the left is wrong.
fn short_key_id(full_id: &str) -> &str {
    if full_id.len() > 8 { &full_id[full_id.len() - 8..] } else { full_id }
}

/// Create ~/.HUMANFILE if it doesn't exist.
fn create_global_humanfile(host: &crate::kernel::host_identity::HostIdentity, gpg_key: Option<&str>) {
    let home = match std::env::var("HOME").ok() {
        Some(h) => std::path::PathBuf::from(h),
        None => return,
    };
    let path = home.join(".HUMANFILE");
    if path.exists() { return; }

    let sign_line = gpg_key
        .map(|k| format!("SIGN {k}\n"))
        .unwrap_or_else(|| "# SIGN <your-gpg-key-id>  (run `ostk sign` to set up)\n".into());

    let content = format!(
        "# ~/.HUMANFILE — global identity for {} <{}>\n\n\
         IDENTITY {}\n\
         {sign_line}\n\
         MODEL auto\n",
        host.full_name, host.git_email, host.username
    );

    let _ = fs::write(&path, content);
}
