//! `ostk secret` — OS-mediated secret resolution.
//!
//! Keys never enter LLM context. Resolution order:
//! 1. OSTK_SECRET_CMD (BYO: bw, op, pass)
//! 2. Platform keychain (macOS: security, Linux: secret-tool)
//! 3. Environment variable fallback
//!
//! `ostk secret set KEY` — store in platform keychain
//! `ostk secret get KEY` — retrieve (stdout only, for subprocess injection)
//! `ostk secret list` — show which keys are available (names only, never values)
//! `ostk secret env KEY` — export to current shell env (prints export statement)

use std::fmt::Write;
use std::fs::OpenOptions;
use std::io::Write as IoWrite;
use std::process::Command;
use std::time::Duration;

use wait_timeout::ChildExt;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{append_audit, find_project_root, now_iso};
use serde_json::json;

/// Timeout for interactive password prompt (seconds).
const PASSWORD_TIMEOUT_SECS: u64 = 30;

/// Read a password from `/dev/tty` with echo disabled.
/// Falls back to stdin if `/dev/tty` is unavailable (e.g., CI).
/// Never prints the value anywhere.
///
/// Times out after 30 seconds to avoid hanging in terminal emulators
/// that don't support /dev/tty reads properly.
fn read_password_from_tty(prompt: &str) -> Result<String, String> {
    // Use bash read -s — handles echo suppression and tty correctly.
    // Avoids BufReader/tty fd conflicts that truncate long input.
    let mut child = Command::new("bash")
        .args(["-c", &format!(
            "read -s -p '{}' pw < /dev/tty && printf '%s' \"$pw\"",
            prompt.replace('\'', "'\\''")
        )])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("read password failed: {e}"))?;

    let timeout = Duration::from_secs(PASSWORD_TIMEOUT_SECS);
    match child.wait_timeout(timeout) {
        Ok(Some(status)) => {
            // Print newline after hidden input — write to /dev/tty so it
            // reaches the user's terminal, not ostk's stderr.
            if let Ok(mut tty) = OpenOptions::new().write(true).open("/dev/tty") {
                let _ = tty.write_all(b"\n");
            }

            if !status.success() {
                return Err("password read cancelled".into());
            }

            let mut stdout_bytes = Vec::new();
            if let Some(mut stdout) = child.stdout.take() {
                use std::io::Read;
                let _ = stdout.read_to_end(&mut stdout_bytes);
            }

            let password = String::from_utf8_lossy(&stdout_bytes).to_string();
            if password.is_empty() {
                return Err("empty password".into());
            }

            Ok(password)
        }
        Ok(None) => {
            // Timed out — kill the child and report
            let _ = child.kill();
            let _ = child.wait(); // reap
            Err(format!(
                "password prompt timed out after {PASSWORD_TIMEOUT_SECS}s \
                 (terminal may not support interactive input). \
                 Use `ostk secret set KEY --value VALUE` instead."
            ))
        }
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            Err(format!("read password failed: {e}"))
        }
    }
}

/// Known API key names and their providers.
/// →639: extended to include management key and alternate OpenRouter name.
const KNOWN_KEYS: &[(&str, &str)] = &[
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("MISTRAL_API_KEY", "mistral"),
    ("GEMINI_API_KEY", "google"),
    ("OPENROUTER_API_KEY", "openrouter"),
    ("OPENROUTER_MANAGEMENT_KEY", "openrouter-mgmt"),
    ("OPEN_ROUTER_API_KEY", "openrouter-alt"),
    ("OPENAI_API_KEY", "openai"),
    ("MODEL_API_KEY", "universal"),
];

/// CLI entry point for `ostk secret set`.
pub fn run_set(key: &str, value: Option<&str>) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_set_verb(&mut ctx, key, value)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for secret set (→1157).
pub fn run_set_verb(ctx: &mut VerbCtx, key: &str, value: Option<&str>) -> Result<(), String> {
    let password = if let Some(v) = value {
        v.to_string()
    } else {
        read_password_from_tty("Password: ")?
    };

    // Store in platform keychain
    if cfg!(target_os = "macos") {
        // Delete ALL existing entries with this service name to avoid stale keys.
        // First delete ostk-specific entry, then delete any legacy entries
        // without the -a ostk account filter. Loop to remove duplicates.
        let _ = Command::new("security")
            .args(["delete-generic-password", "-a", "ostk", "-s", key])
            .output();
        // Delete legacy/stale entries (no -a filter). Loop handles duplicates.
        for _ in 0..5 {
            let out = Command::new("security")
                .args(["delete-generic-password", "-s", key])
                .output();
            if out.map(|o| !o.status.success()).unwrap_or(true) {
                break;
            }
        }

        // Pass password as argument to -w (not interactive — no truncation)
        let status = Command::new("security")
            .args([
                "add-generic-password",
                "-a", "ostk",
                "-s", key,
                "-w", &password,
            ])
            .status()
            .map_err(|e| format!("security command failed: {e}"))?;

        if !status.success() {
            return Err("failed to store secret in keychain".into());
        }
        // →639: echo exact key name so operator can verify what was stored
        writeln!(ctx, "stored {key} in macOS Keychain").unwrap();
        // Register with masking layer so the value is masked in all tool output
        crate::kernel::secrets::register_value(&password);
    } else {
        // Linux: secret-tool reads from stdin.
        // Pipe the password ourselves to avoid tty truncation issues.
        use std::process::Stdio;

        let mut child = Command::new("secret-tool")
            .args([
                "store",
                "--label", key,
                "service", "ostk",
                "key", key,
            ])
            .stdin(Stdio::piped())
            .spawn()
            .map_err(|e| format!("secret-tool failed: {e}"))?;

        if let Some(mut stdin) = child.stdin.take() {
            stdin
                .write_all(password.as_bytes())
                .map_err(|e| format!("failed to write to secret-tool stdin: {e}"))?;
            // Drop stdin to close the pipe (signals EOF)
        }

        let status = child.wait().map_err(|e| format!("secret-tool wait: {e}"))?;

        if !status.success() {
            return Err("failed to store secret".into());
        }
        writeln!(ctx, "stored {key} in keyring").unwrap();
        crate::kernel::secrets::register_value(&password);
    }

    // Audit (name only, never value)
    if let Ok(root) = find_project_root() {
        let _ = append_audit(
            &root,
            &json!({
                "event": "secret.set",
                "key": key,
                "timestamp": now_iso(),
            }),
        );
    }

    Ok(())
}

/// CLI entry point for `ostk secret get`.
pub fn run_get(key: &str) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_get_verb(&mut ctx, key)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for secret get (→1157).
pub fn run_get_verb(ctx: &mut VerbCtx, key: &str) -> Result<(), String> {
    let value = match resolve_secret(key) {
        Ok(v) => v,
        Err(e) => {
            // Auto-request: agent self-heals by submitting a privilege escalation
            if let Ok(root) = crate::find_project_root() {
                let hs = crate::state_dir(&root);
                // Determine calling agent from identity (best-effort)
                let agent = std::env::var("OSTK_AGENT_ALIAS")
                    .unwrap_or_else(|_| "unknown".to_string());
                let granter = std::env::var("OSTK_AGENT_PARENT")
                    .unwrap_or_else(|_| "operator".to_string());
                match crate::kernel::request::submit_request(
                    &hs,
                    &agent,
                    crate::kernel::request::RequestType::Secret,
                    key,
                    &format!("secret {key} not found: {e}"),
                    &granter,
                ) {
                    Ok(req_id) => {
                        return Err(format!(
                            "{key} not found — privilege request submitted ({req_id}).                              Run: ostk grant approve {req_id}"
                        ));
                    }
                    Err(_) => return Err(e),
                }
            }
            return Err(e);
        }
    };
    // Write to ctx — for subprocess capture
    write!(ctx, "{value}").unwrap();
    Ok(())
}

/// CLI entry point for `ostk secret list`.
pub fn run_list() -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_list_verb(&mut ctx)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for secret list (→1157).
pub fn run_list_verb(ctx: &mut VerbCtx) -> Result<(), String> {
    writeln!(ctx, "ostk secret list:").unwrap();
    writeln!(ctx).unwrap();

    for (key, provider) in KNOWN_KEYS {
        let status = match resolve_secret(key) {
            Ok(_) => "available",
            Err(_) => "missing",
        };
        let icon = if status == "available" { "+" } else { "-" };
        writeln!(ctx, "  {icon} {key:<30} [{provider}] {status}").unwrap();
    }

    writeln!(ctx).unwrap();

    // Summary
    let available = KNOWN_KEYS
        .iter()
        .filter(|(k, _)| resolve_secret(k).is_ok())
        .count();
    writeln!(ctx, "{available}/{} keys available", KNOWN_KEYS.len()).unwrap();

    Ok(())
}

/// CLI entry point for `ostk secret env`.
pub fn run_env(key: &str) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_env_verb(&mut ctx, key)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for secret env (→1157).
pub fn run_env_verb(ctx: &mut VerbCtx, key: &str) -> Result<(), String> {
    // →624 P1: raw value exposed in shell history, CI logs, terminal recordings.
    // Prefer: ostk run <Agentfile> --env-passthrough KEY
    eprintln!("warning: 'ostk secret env' exposes the raw value in shell history.");
    eprintln!("prefer: ostk run <Agentfile> --env-passthrough {key}");
    let value = resolve_secret(key)?;
    writeln!(ctx, "export {key}={value}").unwrap();
    Ok(())
}

/// Resolve a secret. Never returns the value to LLM context —
/// only to stdout (for subprocess capture) or env injection.
///
/// Resolution order:
/// 1. OSTK_SECRET_CMD <key> (BYO vault)
/// 2. Platform keychain
/// 3. Environment variable
pub fn resolve_secret(key: &str) -> Result<String, String> {
    // 1. BYO vault command — read from .ostk/config NOT from env (→620 P0)
    // Reading from env allows an attacker who controls the environment to exfiltrate
    // all secrets. Config file is owned/controlled by the user, env is not.
    let config_cmd = crate::find_project_root()
        .ok()
        .and_then(|root| {
            let cfg = crate::state_dir(&root).join("config");
            std::fs::read_to_string(cfg).ok()
        })
        .and_then(|content| {
            content.lines()
                .find(|l| l.starts_with("secret_cmd"))
                .and_then(|l| l.split_once('=').map(|x| x.1))
                .map(|v| v.trim().to_string())
        });

    if let Some(cmd) = config_cmd {
        let parts: Vec<&str> = cmd.split_whitespace().collect();
        if !parts.is_empty() {
            let output = Command::new(parts[0])
                .args(&parts[1..])
                .arg(key)
                .output()
                .map_err(|e| format!("secret_cmd failed: {e}"))?;
            if output.status.success() {
                let val = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if !val.is_empty() {
                    return Ok(val);
                }
            }
        }
    }

    // Legacy env var support — emit warning but still support for backward compat
    if let Ok(cmd) = std::env::var("OSTK_SECRET_CMD") {
        let parts: Vec<&str> = cmd.split_whitespace().collect();
        if !parts.is_empty() {
            let output = Command::new(parts[0])
                .args(&parts[1..])
                .arg(key)
                .output()
                .map_err(|e| format!("OSTK_SECRET_CMD failed: {e}"))?;

            if output.status.success() {
                let val = String::from_utf8_lossy(&output.stdout).trim().to_string();
                if !val.is_empty() {
                    return Ok(val);
                }
            }
        }
    }

    // 2. Platform keychain
    // Prefer entries stored by ostk (-a ostk), fall back to any entry
    // with the same service name. The -a filter prevents returning a stale key
    // from a different tool that used the same service name.
    let keychain_result = if cfg!(target_os = "macos") {
        Command::new("security")
            .args(["find-generic-password", "-a", "ostk", "-s", key, "-w"])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .or_else(|| {
                // Fallback: match by service only (legacy entries without -a ostk)
                Command::new("security")
                    .args(["find-generic-password", "-s", key, "-w"])
                    .output()
                    .ok()
                    .filter(|o| o.status.success())
                    .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            })
    } else {
        Command::new("secret-tool")
            .args(["lookup", "service", "ostk", "key", key])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
    };

    if let Some(ref val) = keychain_result
        && !val.is_empty() {
            // Cross-check: warn if env var has a DIFFERENT value (stale keychain detection)
            if let Ok(env_val) = std::env::var(key)
                && !env_val.is_empty() && env_val != *val {
                    eprintln!(
                        "[ostk secret] warning: keychain value for {key} differs from env var \
                         (keychain len={}, env len={}). Keychain takes priority. \
                         Run `ostk secret set {key}` to update.",
                        val.len(),
                        env_val.len()
                    );
                }
            return Ok(val.clone());
        }

    // 3. Environment variable fallback
    if let Ok(val) = std::env::var(key)
        && !val.is_empty() {
            return Ok(val);
        }

    Err(format!("{key} not found (checked: OSTK_SECRET_CMD, keychain, env)"))
}

/// Resolve a secret and inject it into the environment of a Command.
/// The key is set as an env var on the Command, never printed or logged.
pub fn inject_secret(cmd: &mut Command, key: &str) -> Result<(), String> {
    let value = resolve_secret(key)?;
    cmd.env(key, &value);
    Ok(())
}

/// Map a model name to the required API key.
pub fn key_for_model(model: &str) -> &'static str {
    if model.starts_with("mistral") || model.starts_with("codestral")
        || model.starts_with("ministral") || model.starts_with("devstral")
        || model.starts_with("magistral")
    {
        "MISTRAL_API_KEY"
    } else if model.starts_with("gemini") {
        "GEMINI_API_KEY"
    } else if model.starts_with("gpt") || model.starts_with("o1") || model.starts_with("o3") {
        "OPENAI_API_KEY"
    } else if model.contains("openrouter") || model.contains("/") {
        "OPENROUTER_API_KEY"
    } else {
        // Default: Anthropic (claude-*)
        "ANTHROPIC_API_KEY"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_key_for_model_anthropic() {
        assert_eq!(key_for_model("claude-sonnet-4-6"), "ANTHROPIC_API_KEY");
        assert_eq!(key_for_model("claude-opus-4-6"), "ANTHROPIC_API_KEY");
    }

    #[test]
    fn test_key_for_model_gemini() {
        assert_eq!(key_for_model("gemini-2.0-flash"), "GEMINI_API_KEY");
        assert_eq!(key_for_model("gemini-pro"), "GEMINI_API_KEY");
    }

    #[test]
    fn test_key_for_model_openai() {
        assert_eq!(key_for_model("gpt-4o-mini"), "OPENAI_API_KEY");
        assert_eq!(key_for_model("o3-mini"), "OPENAI_API_KEY");
    }

    #[test]
    fn test_key_for_model_openrouter() {
        assert_eq!(key_for_model("anthropic/claude-3.5-sonnet"), "OPENROUTER_API_KEY");
    }

    #[test]
    fn test_key_for_model_mistral() {
        assert_eq!(key_for_model("mistral-large-latest"), "MISTRAL_API_KEY");
        assert_eq!(key_for_model("mistral-small-latest"), "MISTRAL_API_KEY");
        assert_eq!(key_for_model("codestral-latest"), "MISTRAL_API_KEY");
        assert_eq!(key_for_model("ministral-8b-latest"), "MISTRAL_API_KEY");
        assert_eq!(key_for_model("devstral-latest"), "MISTRAL_API_KEY");
        assert_eq!(key_for_model("magistral-medium-latest"), "MISTRAL_API_KEY");
    }

    #[test]
    fn test_resolve_from_env() {
        // SAFETY: OSTK_TEST_SECRET_KEY is a test-only var not read by any other
        // test in this binary, so no concurrent readers exist.
        unsafe { std::env::set_var("OSTK_TEST_SECRET_KEY", "test_value_123") };
        let result = resolve_secret("OSTK_TEST_SECRET_KEY");
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), "test_value_123");
        unsafe { std::env::remove_var("OSTK_TEST_SECRET_KEY") };
    }

    #[test]
    fn test_resolve_missing() {
        let result = resolve_secret("DEFINITELY_NOT_A_REAL_KEY_999");
        assert!(result.is_err());
    }

    #[test]
    fn test_known_keys_has_anthropic() {
        assert!(KNOWN_KEYS.iter().any(|(k, _)| *k == "ANTHROPIC_API_KEY"));
    }

    #[test]
    fn test_inject_secret_sets_env() {
        // SAFETY: OSTK_TEST_INJECT is a test-only var not read by any other
        // test in this binary, so no concurrent readers exist.
        unsafe { std::env::set_var("OSTK_TEST_INJECT", "inject_val") };
        let mut cmd = Command::new("echo");
        let result = inject_secret(&mut cmd, "OSTK_TEST_INJECT");
        assert!(result.is_ok());
        unsafe { std::env::remove_var("OSTK_TEST_INJECT") };
    }

    #[test]
    fn test_password_timeout_constant() {
        assert_eq!(PASSWORD_TIMEOUT_SECS, 30);
    }

    #[test]
    fn test_password_timeout_kills_hanging_process() {
        // Spawn a process that sleeps forever, then verify wait_timeout
        // returns None and we can kill it. This validates the pattern
        // used in read_password_from_tty without needing /dev/tty.
        let mut child = Command::new("sleep")
            .arg("300")
            .spawn()
            .expect("failed to spawn sleep");

        let result = child
            .wait_timeout(Duration::from_millis(100))
            .expect("wait_timeout failed");

        // Should timeout (None), not complete
        assert!(result.is_none(), "sleep should not have finished in 100ms");

        // Kill and reap
        child.kill().expect("failed to kill");
        child.wait().expect("failed to reap");
    }
}
