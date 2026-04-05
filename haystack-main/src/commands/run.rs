use std::collections::HashMap;
use std::fs;
use std::io::{self, Write};
use std::path::Path;


use crate::find_project_root;

// Re-export from cpu::providers — these lived here historically.
pub use crate::cpu::providers::{ApiProvider, resolve_provider};

/// Parse the `secrets:` section from `.ostk/HUMANFILE`.
///
/// Finds `secrets:` and collects indented `KEY: <status>` lines underneath.
/// Returns a map of KEY → status string (e.g. "authorized", "denied").
pub fn read_humanfile_secrets(ostk_dir: &Path) -> HashMap<String, String> {
    // →751: Try directive parser first (SECRET <<KEYS heredoc).
    // Falls back to legacy YAML-style parsing for backward compat.
    let load_result = crate::humanfile::load(ostk_dir);
    if !load_result.humanfile.secrets.is_empty() {
        return load_result.humanfile.secrets.iter()
            .map(|key| (key.clone(), "authorized".to_string()))
            .collect();
    }

    // Legacy YAML fallback: parse secrets: section with KEY: value lines.
    let humanfile_path = ostk_dir.join("HUMANFILE");
    let content = match fs::read_to_string(&humanfile_path) {
        Ok(c) => c,
        Err(_) => return HashMap::new(),
    };

    let mut secrets: HashMap<String, String> = HashMap::new();
    let mut in_secrets = false;

    for line in content.lines() {
        if line.trim_start() == "secrets:" {
            in_secrets = true;
            continue;
        }

        if in_secrets {
            // A non-indented non-empty line ends the secrets block
            // (section headers like `##`, bare labels, etc.).
            if !line.is_empty() && !line.starts_with(' ') && !line.starts_with('\t') {
                break;
            }

            // Skip blank lines inside the block.
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }

            // Skip inline comments (lines that start with # after trimming, when indented).
            if trimmed.starts_with('#') {
                continue;
            }

            // Parse `KEY: value`
            if let Some((key, val)) = trimmed.split_once(':') {
                let key = key.trim().to_string();
                let val = val.trim().to_string();
                if !key.is_empty() && !val.is_empty() {
                    secrets.insert(key, val);
                }
            }
        }
    }

    secrets
}

/// Read HUMANFILE drivers via the directive parser.
///
/// Returns a map of name → command string from DRIVER directives.
pub fn read_humanfile_drivers(ostk_dir: &Path) -> HashMap<String, String> {
    let load_result = crate::humanfile::load(ostk_dir);
    load_result.humanfile.drivers
        .into_iter()
        .collect()
}

/// Resolve `FROM auto` to a concrete model (→495, →770).
/// Priority:
///   1. staging/preferred_model (runtime override from :model)
///   2. HUMANFILE llm.model preference
///   3. HUMANFILE llm.fallback preference
///   4. Best available model by API key (env-based scoring)
pub fn resolve_auto_model() -> String {
    // 1. Check staging/preferred_model (runtime override from :model)
    let _sd = crate::find_project_root().ok().map(|r| crate::state_dir(&r));
    if let Some(ref sd) = _sd
        && let Ok(m) = std::fs::read_to_string(sd.join("staging/preferred_model"))
        && !m.trim().is_empty()
    {
        return m.trim().to_string();
    }

    // 2. Check HUMANFILE MODEL directive via humanfile::load.
    //    humanfile::load handles both global (~/.HUMANFILE) and project HUMANFILE,
    //    and correctly parses the `MODEL <name>` directive format (→816).
    let hf_model: Option<String> = if let Some(ref sd) = _sd {
        let load_result = crate::humanfile::load(sd);
        load_result.humanfile.model
    } else {
        None
    };
    let hf_fallback: Option<String> = if let Some(ref sd) = _sd {
        let load_result = crate::humanfile::load(sd);
        load_result.humanfile.fallback
    } else {
        None
    };
    if let Some(model) = hf_model {
        // Verify the preferred model's key is available; if not, try fallback
        let key = crate::commands::secret::key_for_model(&model);
        if crate::commands::secret::resolve_secret(key).is_ok() {
            return model;
        }
        // Preferred model key missing — try fallback
        if let Some(fallback) = hf_fallback {
            let fb_key = crate::commands::secret::key_for_model(&fallback);
            if crate::commands::secret::resolve_secret(fb_key).is_ok() {
                eprintln!("[ostk run] preferred model {model} unavailable (missing {key}), using fallback {fallback}");
                return fallback;
            }
        }
        // Both unavailable — still return preferred (will fail at API call with clear error)
        return model;
    }

    // 3. Fallback to env-based scoring
    resolve_auto_with_lookup(|key| std::env::var(key).ok())
}

/// Testable core of FROM auto resolution.
/// `lookup` is called with an env var name; returns Some(value) if set and non-empty.
pub fn resolve_auto_with_lookup<F>(lookup: F) -> String
where
    F: Fn(&str) -> Option<String>,
{
    struct Candidate {
        model: &'static str,
        env_key: &'static str,
        score: u32,
    }

    let candidates = [
        Candidate { model: "claude-opus-4-6",   env_key: "ANTHROPIC_API_KEY",  score: 100 },
        Candidate { model: "claude-sonnet-4-6", env_key: "ANTHROPIC_API_KEY",  score: 90  },
        Candidate { model: "gpt-4o",            env_key: "OPENAI_API_KEY",     score: 60  },
        Candidate { model: "gpt-4o",            env_key: "OPENROUTER_API_KEY", score: 58  },
        Candidate { model: "gemini-2.0-flash",  env_key: "GEMINI_API_KEY",     score: 50  },
        Candidate { model: "gemini-2.0-flash",  env_key: "OPENROUTER_API_KEY", score: 48  },
    ];

    candidates
        .iter()
        .filter(|c| lookup(c.env_key).is_some_and(|v| !v.is_empty()))
        .max_by_key(|c| c.score)
        .map(|c| c.model.to_string())
        .unwrap_or_else(|| "claude-sonnet-4-6".to_string())
}

// →751: stream_response_routed/stream_response_openai/stream_response_google/stream_response
// deleted — all LLM calls now route through CpuDriver. ask.rs uses CpuDriver directly.


/// →750/→798: Run an Agentfile through the kernel's AgentSession + CpuDriver.
///
/// Parses the Agentfile, builds a LoopConfig from its directives,
/// creates an AgentSession with boot context enrichment, and dispatches
/// through the kernel agent loop. This is the only run path — all entry
/// points route through CpuDriver.
pub fn run_kernel(agentfile_path: &str) -> Result<(), String> {
    run_kernel_with_context(agentfile_path, None)
}

/// Run an Agentfile through the kernel, optionally with parent agent context.
///
/// When `parent_context` is `Some`, the context is injected as a preload_context
/// block and the initial dispatch message references the parent context.
pub fn run_kernel_with_context(agentfile_path: &str, parent_context: Option<String>) -> Result<(), String> {
    use crate::cpu::session::AgentSession;

    let root = find_project_root()?;

    // Resolve the Agentfile path: if the given path doesn't exist as-is,
    // treat it as a bare name and search standard locations.
    let path = {
        let direct = Path::new(agentfile_path);
        if direct.exists() {
            direct.to_path_buf()
        } else {
            // Extract bare name (strip directory components and .af extension)
            let bare = Path::new(agentfile_path)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or(agentfile_path);
            crate::agentfile::resolve_agentfile(&root, bare)
                .ok_or_else(|| format!("Agentfile '{}' not found", agentfile_path))?
        }
    };

    // →773: Verify Agentfile GPG signature, respecting HUMANFILE TRUST.
    let ostk_dir = crate::state_dir(&root);
    let trust_unsigned = crate::humanfile::load(&ostk_dir).humanfile.trust
        .as_deref() == Some("unsigned");
    crate::commands::sign::verify_and_log_agentfile(&path, trust_unsigned);

    // →784: Ensure kernel daemon is running — fork `ostk listen` if needed
    let socket_path = crate::serve::socket::socket_path(&ostk_dir);
    if !crate::serve::socket::kernel_alive(&ostk_dir) {
        eprintln!("[kernel] no daemon — forking `ostk listen`");
        let exe = std::env::current_exe()
            .unwrap_or_else(|_| std::path::PathBuf::from("ostk"));
        let _ = std::process::Command::new(&exe)
            .arg("listen")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("failed to fork daemon: {e}"))?;

        // Wait up to 5s for the socket to appear
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while std::time::Instant::now() < deadline {
            if socket_path.exists() && crate::serve::socket::kernel_alive(&ostk_dir) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        if !crate::serve::socket::kernel_alive(&ostk_dir) {
            eprintln!("[kernel] warning: daemon did not start within 5s, continuing without socket");
        }
    }

    // Pass socket path to agent_loop via env var.
    // SAFETY: Still on the main thread before AgentSession is created; no
    // concurrent readers of OSTK_SOCKET exist yet.
    if crate::serve::socket::kernel_alive(&ostk_dir) {
        unsafe { std::env::set_var("OSTK_SOCKET", socket_path.to_string_lossy().as_ref()); }
        eprintln!("[kernel] daemon alive at {}", socket_path.display());
    }

    // ── Canonical config pipeline via SpawnRequest::prepare() ──
    //
    // SpawnRequest handles: parse AF, resolve model (FROM auto → best available),
    // build CpuConfig → LoopConfig, boot context enrichment, parent context
    // injection, and CpuDriver creation. All agent launch paths share this
    // pipeline — duplicating logic here creates drift bugs.
    let prepared = crate::cpu::SpawnRequest {
        root: root.clone(),
        source: crate::cpu::AgentfileSource::FromPath(path.clone()),
        model_override: None,
        parent_context,
    }.prepare()?;

    let model = prepared.model.clone();

    // →775: Activate pin.caps capability enforcement (kernel runtime).
    if let Some(ref pin) = prepared.agentfile.pin {
        // SAFETY: Still on the main thread before AgentSession::new(); no
        // concurrent readers of OSTK_PIN exist yet.
        unsafe { std::env::set_var("OSTK_PIN", pin) };
    }

    // Create session with kernel identity
    let session_name = Path::new(agentfile_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("run-kernel");
    let mut session = AgentSession::new(session_name, prepared.config, root.clone());

    // Build tokio runtime — must be multi-thread so spawned agent loop tasks
    // (which do async tool execution via tokio::process, spawn_blocking, etc.)
    // run on background threads rather than cooperative-only on the main thread.
    // A current_thread runtime can starve tool execution when block_on(rx.recv())
    // monopolises the single thread between event polls (→799).
    let rt = tokio::runtime::Runtime::new()
        .map_err(|e| format!("runtime: {e}"))?;

    eprintln!("[kernel] run --kernel: dispatching via AgentSession (model: {model})");

    // →843: Create boot context for live system state refresh
    let boot_context = crate::cpu::session::BootContext::new(&root);

    // Dispatch initial user message — check preload_context to determine if a
    // parent context was injected (set by SpawnRequest::prepare() when
    // parent_context was Some).
    let tools_suffix = if prepared.agentfile.tools.is_empty() {
        String::new()
    } else {
        format!("Available tools: {}. ", prepared.agentfile.tools.join(", "))
    };
    let user_message = if !session.config.preload_context.is_empty() {
        format!("{}Continue from parent context above.", tools_suffix)
    } else {
        format!("{}Begin.", tools_suffix)
    };
    session.dispatch(&user_message, rt.handle(), &prepared.driver, &boot_context);

    // Stream events to stdout until TurnComplete
    let mut stdout = io::stdout();
    loop {
        let event = {
            if let Some(ref mut rx) = session.event_rx {
                rt.block_on(rx.recv())
            } else {
                break;
            }
        };

        match event {
            Some(ev) => {
                session.process_event(&ev);
                match &ev {
                    crate::cpu::agent_loop::CpuEvent::TextDelta(text) => {
                        let _ = stdout.write_all(text.as_bytes());
                        let _ = stdout.flush();
                    }
                    crate::cpu::agent_loop::CpuEvent::ToolStart { name, .. } => {
                        eprintln!("\n[tool] {name}");
                    }
                    crate::cpu::agent_loop::CpuEvent::ToolResult { name, output, success } => {
                        let symbol = if *success { "+" } else { "!" };
                        let truncated = crate::util::safe_truncate(output, 200);
                        eprintln!("[{symbol}] {name}: {truncated}");
                    }
                    crate::cpu::agent_loop::CpuEvent::TurnComplete { usage } => {
                        eprintln!(
                            "\n[kernel] done — {}in / {}out tokens",
                            usage.input_tokens, usage.output_tokens
                        );
                        break;
                    }
                    crate::cpu::agent_loop::CpuEvent::Error(e) => {
                        eprintln!("\n[error] {e}");
                        break;
                    }
                    _ => {}
                }
            }
            None => break,
        }
    }

    // Save session
    let _ = session.save();

    // Write completion marker
    write_done_marker(agentfile_path)?;

    Ok(())
}


/// Write ~/.ostk/store/run-done.txt with completion metadata.
fn write_done_marker(agentfile_path: &str) -> Result<(), String> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let store_dir = Path::new(&home).join(".ostk").join("store");

    fs::create_dir_all(&store_dir)
        .map_err(|e| format!("failed to create ~/.ostk/store: {e}"))?;

    let done_path = store_dir.join("run-done.txt");

    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_string());

    let marker = format!(
        "agentfile: {}\nstatus: done\ntimestamp: {}\n",
        agentfile_path, timestamp
    );

    fs::write(&done_path, marker)
        .map_err(|e| format!("failed to write run-done.txt: {e}"))?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::sync::Mutex;
    use tempfile::TempDir;

    /// Mutex to serialize tests that mutate shared API key env vars.
    /// Prevents races between tests in this module and across modules that
    /// read the same vars (e.g. defaults.rs tests reading ANTHROPIC_API_KEY).
    static ENV_LOCK: Mutex<()> = Mutex::new(());

    /// Create a temporary HUMANFILE with the given content and return (TempDir, ostk_dir path).
    /// Sets HUMANFILE env var to suppress global ~/.HUMANFILE loading during tests.
    fn make_humanfile(content: &str) -> (TempDir, std::path::PathBuf) {
        let tmp = TempDir::new().unwrap();
        let ostk_dir = tmp.path().join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        let mut f = fs::File::create(ostk_dir.join("HUMANFILE")).unwrap();
        f.write_all(content.as_bytes()).unwrap();
        // Point global HUMANFILE to a nonexistent path so load() only sees the project file
        unsafe { std::env::set_var("HUMANFILE", tmp.path().join("no-global-humanfile")) };
        (tmp, ostk_dir)
    }

    // →631: provider routing tests
    #[test]
    fn test_resolve_provider_claude() {
        assert_eq!(resolve_provider("claude-sonnet-4-6"), ApiProvider::Anthropic);
        assert_eq!(resolve_provider("claude-opus-4-6"), ApiProvider::Anthropic);
    }

    #[test]
    fn test_resolve_provider_gpt_routes_correctly() {
        // GPT models → OpenRouter (if key available) or OpenAi (fallback).
        // resolve_secret checks keychain + env, so outcome depends on host state.
        let result = resolve_provider("gpt-4o");
        assert!(
            result == ApiProvider::OpenRouter || result == ApiProvider::OpenAi,
            "gpt-4o should route to OpenRouter or OpenAi, got {result:?}"
        );
    }

    #[test]
    fn test_resolve_provider_gemini_with_key() {
        let _lock = ENV_LOCK.lock().unwrap();
        let saved = std::env::var("GEMINI_API_KEY").ok();
        // SAFETY: Serialized by ENV_LOCK.
        unsafe { std::env::set_var("GEMINI_API_KEY", "test-key") };
        assert_eq!(resolve_provider("gemini-2.0-flash"), ApiProvider::Google);
        // Restore
        match saved {
            Some(val) => unsafe { std::env::set_var("GEMINI_API_KEY", val) },
            None => unsafe { std::env::remove_var("GEMINI_API_KEY") },
        }
    }

    #[test]
    fn test_resolve_provider_openrouter_prefix() {
        assert_eq!(resolve_provider("openrouter/auto"), ApiProvider::OpenRouter);
        assert_eq!(resolve_provider("meta-llama/llama-3"), ApiProvider::OpenRouter);
    }

    #[test]
    fn test_resolve_provider_mistral_with_key() {
        let _lock = ENV_LOCK.lock().unwrap();
        let saved = std::env::var("MISTRAL_API_KEY").ok();
        // SAFETY: Serialized by ENV_LOCK.
        unsafe { std::env::set_var("MISTRAL_API_KEY", "test-key") };
        assert_eq!(resolve_provider("mistral-large-latest"), ApiProvider::Mistral);
        assert_eq!(resolve_provider("mistral-small-latest"), ApiProvider::Mistral);
        // →761: codestral/ministral/devstral/magistral must route to Mistral
        assert_eq!(resolve_provider("codestral-latest"), ApiProvider::Mistral);
        assert_eq!(resolve_provider("ministral-8b-latest"), ApiProvider::Mistral);
        assert_eq!(resolve_provider("devstral-small"), ApiProvider::Mistral);
        assert_eq!(resolve_provider("magistral-medium-latest"), ApiProvider::Mistral);
        // Restore
        match saved {
            Some(val) => unsafe { std::env::set_var("MISTRAL_API_KEY", val) },
            None => unsafe { std::env::remove_var("MISTRAL_API_KEY") },
        }
    }

    // →495: FROM auto resolution — tests use resolve_auto_with_lookup for env isolation
    #[test]
    fn test_resolve_auto_no_keys_returns_fallback() {
        let model = resolve_auto_with_lookup(|_| None);
        assert_eq!(model, "claude-sonnet-4-6");
    }

    #[test]
    fn test_resolve_auto_anthropic_key_picks_opus() {
        let model = resolve_auto_with_lookup(|key| {
            if key == "ANTHROPIC_API_KEY" { Some("sk-test".into()) } else { None }
        });
        assert_eq!(model, "claude-opus-4-6");
    }

    #[test]
    fn test_resolve_auto_prefers_anthropic_over_openai() {
        let model = resolve_auto_with_lookup(|key| match key {
            "ANTHROPIC_API_KEY" => Some("sk-ant".into()),
            "OPENAI_API_KEY" => Some("sk-oai".into()),
            _ => None,
        });
        assert_eq!(model, "claude-opus-4-6");
    }

    #[test]
    fn test_resolve_auto_gemini_only() {
        let model = resolve_auto_with_lookup(|key| {
            if key == "GEMINI_API_KEY" { Some("gemini-key".into()) } else { None }
        });
        assert_eq!(model, "gemini-2.0-flash");
    }

    // →770: directive parser model tests (legacy YAML tests removed)
    #[test]
    fn test_directive_parser_model_valid() {
        let content = "MODEL gemini-2.5-pro\nFALLBACK claude-sonnet-4-5\n";
        let hf = crate::humanfile::parse(content).unwrap();
        assert_eq!(hf.model, Some("gemini-2.5-pro".to_string()));
    }

    #[test]
    fn test_directive_parser_model_in_full_humanfile() {
        let content = "# HUMANFILE\nIDENTITY scott\nSIGN KEY123\nMODEL claude-opus-4-6\nFALLBACK claude-sonnet-4-5\n";
        let hf = crate::humanfile::parse(content).unwrap();
        assert_eq!(hf.model, Some("claude-opus-4-6".to_string()));
    }

    #[test]
    fn test_humanfile_secrets_parsing() {
        let content = "## Some Section\n\nsecrets:\n  OPENROUTER_API_KEY: authorized\n  GEMINI_API_KEY: authorized\n  BAD_KEY: denied\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let map = read_humanfile_secrets(&ostk_dir);

        assert_eq!(map.get("OPENROUTER_API_KEY").map(|s| s.as_str()), Some("authorized"));
        assert_eq!(map.get("GEMINI_API_KEY").map(|s| s.as_str()), Some("authorized"));
        assert_eq!(map.get("BAD_KEY").map(|s| s.as_str()), Some("denied"));
        assert!(!map.contains_key("MISSING_KEY"));
    }

    #[test]
    fn test_humanfile_secrets_parsing_empty() {
        // No secrets section at all
        let content = "## Just a regular HUMANFILE\n\nNo secrets here.\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let map = read_humanfile_secrets(&ostk_dir);
        assert!(map.is_empty());
    }

    #[test]
    fn test_humanfile_secrets_parsing_section_ends_at_next_header() {
        let content = "secrets:\n  KEY_A: authorized\n\n## Next Section\n  KEY_B: authorized\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let map = read_humanfile_secrets(&ostk_dir);
        // KEY_A should be in secrets; KEY_B is after the section break
        assert_eq!(map.get("KEY_A").map(|s| s.as_str()), Some("authorized"));
        assert!(!map.contains_key("KEY_B"));
    }

    #[test]
    fn test_humanfile_secrets_missing_file() {
        let _lock = ENV_LOCK.lock().unwrap();
        let tmp = TempDir::new().unwrap();
        let ostk_dir = tmp.path().join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        // Suppress global HUMANFILE
        unsafe { std::env::set_var("HUMANFILE", tmp.path().join("no-global")) };
        // No HUMANFILE created — should return empty map, not panic
        let map = read_humanfile_secrets(&ostk_dir);
        assert!(map.is_empty());
        unsafe { std::env::remove_var("HUMANFILE") };
    }

    #[test]
    fn test_env_passthrough_denied_when_not_in_humanfile() {
        // Simulate: key missing from secrets map → authorization check fails.
        // We test read_humanfile_secrets directly; the run() function's auth
        // check is: if status != "authorized" { return Err(...) }.
        let content = "secrets:\n  OTHER_KEY: authorized\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let map = read_humanfile_secrets(&ostk_dir);

        let key = "MISSING_KEY";
        let status = map.get(key).map(|s| s.as_str()).unwrap_or("");
        assert_ne!(status, "authorized", "absent key must not pass authorization");
    }

    #[test]
    fn test_env_passthrough_authorized() {
        // Simulate: key present and authorized → env injection allowed.
        let content = "secrets:\n  OSTK_TEST_PASSTHROUGH: authorized\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let map = read_humanfile_secrets(&ostk_dir);

        let status = map.get("OSTK_TEST_PASSTHROUGH").map(|s| s.as_str()).unwrap_or("");
        assert_eq!(status, "authorized");

        // Verify resolve_secret picks up env var (same chain used by run_kernel())
        // SAFETY: OSTK_TEST_PASSTHROUGH is a test-only var not read by any other
        // test in this binary, so no concurrent readers exist.
        unsafe { std::env::set_var("OSTK_TEST_PASSTHROUGH", "test_passthrough_value") };
        let result = crate::commands::secret::resolve_secret("OSTK_TEST_PASSTHROUGH");
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "test_passthrough_value");
        unsafe { std::env::remove_var("OSTK_TEST_PASSTHROUGH") };
    }

    #[test]
    fn test_humanfile_secrets_tabs_indented() {
        // Tabs as indentation should also parse correctly
        let content = "secrets:\n\tDEEP_KEY: authorized\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let map = read_humanfile_secrets(&ostk_dir);
        assert_eq!(map.get("DEEP_KEY").map(|s| s.as_str()), Some("authorized"));
    }

    // →770: directive parser fallback tests (legacy YAML tests removed)
    #[test]
    fn test_directive_parser_fallback_valid() {
        let content = "MODEL gemini-2.5-pro\nFALLBACK claude-sonnet-4-5\n";
        let hf = crate::humanfile::parse(content).unwrap();
        assert_eq!(hf.fallback, Some("claude-sonnet-4-5".to_string()));
    }

    #[test]
    fn test_directive_parser_fallback_missing() {
        let content = "MODEL claude-sonnet-4-5\n";
        let hf = crate::humanfile::parse(content).unwrap();
        assert_eq!(hf.fallback, None);
    }

    // →770: directive parser available tests (legacy YAML tests removed)
    #[test]
    fn test_directive_parser_available_valid() {
        let content = "AVAILABLE <<MODELS\nclaude-sonnet-4-5\nclaude-opus-4-6\ngemini-2.5-pro\nMODELS\n";
        let hf = crate::humanfile::parse(content).unwrap();
        assert_eq!(hf.available_models, vec![
            "claude-sonnet-4-5".to_string(),
            "claude-opus-4-6".to_string(),
            "gemini-2.5-pro".to_string(),
        ]);
    }

    #[test]
    fn test_directive_parser_available_empty() {
        let content = "MODEL claude-sonnet-4-5\n";
        let hf = crate::humanfile::parse(content).unwrap();
        assert!(hf.available_models.is_empty());
    }

    // →797: read_humanfile_drivers tests (directive format)

    #[test]
    fn test_read_humanfile_drivers_directive_single() {
        let content = "DRIVER rust ostk mcp fcp-rust\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let drivers = read_humanfile_drivers(&ostk_dir);
        assert_eq!(drivers.len(), 1);
        assert_eq!(drivers.get("rust").map(|s| s.as_str()), Some("ostk mcp fcp-rust"));
    }

    #[test]
    fn test_read_humanfile_drivers_directive_multiple() {
        let content = "DRIVER rust ostk mcp fcp-rust\nDRIVER python ostk mcp fcp-python\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let drivers = read_humanfile_drivers(&ostk_dir);
        assert_eq!(drivers.len(), 2);
        assert!(drivers.contains_key("rust"));
        assert!(drivers.contains_key("python"));
    }

    #[test]
    fn test_read_humanfile_drivers_no_drivers() {
        let content = "MODEL claude-sonnet-4-5\n";
        let (_tmp, ostk_dir) = make_humanfile(content);
        let drivers = read_humanfile_drivers(&ostk_dir);
        assert!(drivers.is_empty());
    }

    #[test]
    fn test_read_humanfile_drivers_missing_file() {
        let _lock = ENV_LOCK.lock().unwrap();
        let tmp = TempDir::new().unwrap();
        let ostk_dir = tmp.path().join(".ostk");
        fs::create_dir_all(&ostk_dir).unwrap();
        // Suppress global HUMANFILE
        unsafe { std::env::set_var("HUMANFILE", tmp.path().join("no-global")) };
        // No HUMANFILE — should return empty map, not panic
        let drivers = read_humanfile_drivers(&ostk_dir);
        assert!(drivers.is_empty());
        unsafe { std::env::remove_var("HUMANFILE") };
    }

}
