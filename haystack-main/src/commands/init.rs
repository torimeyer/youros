//! `ostk init` — initialize an ostk project.
//!
//! Downloads the governance bail from os-tack/ostk.ai, verifies its GPG
//! signature, unpacks governance files, and creates the .ostk/ state directory.
//!
//! Flags:
//!   --guided    — interactive 7-step wizard after init
//!   --quiet     — no stdout (for auto-bootstrap from zero-arg path)
//!   --bail URL  — custom bail source (default: os-tack/ostk.ai latest release)
//!   --offline   — skip bail download, use hardcoded defaults

use crate::{append_audit, now_iso};
use crate::kernel::host_identity;
use serde_json::json;
use std::fs;
use std::path::Path;
use std::process::Command;

/// Default bail source — latest governance bail from os-tack/ostk.ai.
const DEFAULT_BAIL_REPO: &str = "os-tack/ostk.ai";
const DEFAULT_BAIL_API: &str = "https://api.github.com/repos/os-tack/ostk.ai/releases/latest";

/// Run `ostk init` at CWD.
pub fn run() -> Result<(), String> {
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
    run_at(&cwd, false)
}

/// Run `ostk init` silently (auto-bootstrap from zero-arg `ostk`).
pub fn run_quiet(root: &Path) -> Result<(), String> {
    run_at(root, true)
}

/// Core init logic.
fn run_at(root: &Path, quiet: bool) -> Result<(), String> {
    let state = root.join(".ostk");
    let fresh = !state.is_dir();

    // ── 1. Create .ostk/ skeleton ────────────────────────────────────────
    create_skeleton(&state)?;

    // ── 2. Download + verify bail ────────────────────────────────────────
    let bail_dir = state.join("bail");
    let bail_ok = if fresh {
        match download_bail(&bail_dir) {
            Ok(()) => {
                if !quiet { println!("  \x1b[32m✓\x1b[0m bail imported from {}", DEFAULT_BAIL_REPO); }
                true
            }
            Err(e) => {
                if !quiet { eprintln!("  \x1b[33m!\x1b[0m bail download failed: {e} — using defaults"); }
                false
            }
        }
    } else {
        bail_dir.is_dir()
    };

    // ── 3. Import governance from bail (or use hardcoded defaults) ───────
    import_governance(root, &state, bail_ok, quiet)?;

    // ── 4. .primefile ────────────────────────────────────────────────────
    let primefile = state.join(".primefile");
    if !primefile.exists() {
        fs::write(&primefile,
            "---BEGIN OSTK.PRIME KERNEL DECLARATION---\n\n\
             KERNEL: @ostk.prime\n\
             INSTANCE: generic-boot\n\n\
             ---END---"
        ).map_err(|e| format!(".primefile: {e}"))?;
    }

    // ── 5. Git hooks ─────────────────────────────────────────────────────
    install_git_hooks(root, &state, quiet)?;

    // ── 6. Generate boot.md ──────────────────────────────────────────────
    crate::commands::boot_md::generate(root)?;

    // ── 7. Version stamp ─────────────────────────────────────────────────
    let _ = fs::write(state.join("version"), env!("CARGO_PKG_VERSION"));

    // ── 8. Audit ─────────────────────────────────────────────────────────
    let now = now_iso();
    append_audit(root, &json!({
        "event": "project.initialized",
        "bail": if bail_ok { DEFAULT_BAIL_REPO } else { "defaults" },
        "fresh": fresh,
        "path": root.to_string_lossy(),
        "timestamp": now,
    }))?;

    // ── 9. Register in ~/.ostk/registry.jsonl ──────────────────────────
    if let Err(e) = crate::kernel::registry::register_os_instance(root)
        && !quiet { eprintln!("  \x1b[33m!\x1b[0m registry: {e}"); }

    if !quiet {
        println!("{}", crate::strings::init::INITIALIZED.replacen("{}", &root.to_string_lossy(), 1));
    }

    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Skeleton
// ─────────────────────────────────────────────────────────────────────────────

fn create_skeleton(state: &Path) -> Result<(), String> {
    fs::create_dir_all(state).map_err(|e| format!(".ostk/: {e}"))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(state, fs::Permissions::from_mode(0o700));
    }

    let needles = state.join("needles");
    fs::create_dir_all(&needles).map_err(|e| format!("needles/: {e}"))?;
    if !needles.join("counter").exists() {
        let _ = fs::write(needles.join("counter"), "0");
    }
    if !needles.join("issues.jsonl").exists() {
        let _ = fs::File::create(needles.join("issues.jsonl"));
    }

    let audit = state.join("audit.jsonl");
    if !audit.exists() { let _ = fs::File::create(&audit); }

    let id_counter = state.join("identity_counter");
    if !id_counter.exists() { let _ = fs::write(&id_counter, "0"); }

    let _ = fs::create_dir_all(state.join("wip"));
    let _ = fs::create_dir_all(state.join("pins").join("default"));

    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Bail download + verify
// ─────────────────────────────────────────────────────────────────────────────

/// Download the governance bail from the latest os-tack/ostk.ai release.
/// Extracts into .ostk/bail/.
fn download_bail(bail_dir: &Path) -> Result<(), String> {
    // 1. Find the bail asset URL from the latest release
    let bail_url = find_bail_asset_url()?;

    // 2. Download to temp
    let tmp = std::env::temp_dir().join(format!("ostk_bail_{}", std::process::id()));
    fs::create_dir_all(&tmp).map_err(|e| format!("tmpdir: {e}"))?;
    let bail_path = tmp.join("governance.bail");

    let status = Command::new("curl")
        .args(["-fsSL", &bail_url, "-o"])
        .arg(&bail_path)
        .status()
        .map_err(|e| format!("curl: {e}"))?;
    if !status.success() {
        let _ = fs::remove_dir_all(&tmp);
        return Err(format!("download failed: {bail_url}"));
    }

    // 3. Extract
    fs::create_dir_all(bail_dir).map_err(|e| format!("bail dir: {e}"))?;
    let status = Command::new("tar")
        .args(["-xzf"])
        .arg(&bail_path)
        .arg("-C")
        .arg(bail_dir)
        .arg("--strip-components=1")
        .status()
        .map_err(|e| format!("tar: {e}"))?;

    let _ = fs::remove_dir_all(&tmp);

    if !status.success() {
        return Err("bail extraction failed".to_string());
    }

    // 4. Verify GPG signature on manifest
    let manifest = bail_dir.join("manifest.json");
    let sig = bail_dir.join("manifest.json.asc");
    if manifest.exists() && sig.exists() {
        let output = Command::new("gpg")
            .args(["--verify"])
            .arg(&sig)
            .arg(&manifest)
            .output();
        match output {
            Ok(o) if o.status.success() => {
                // Signature verified
            }
            Ok(o) => {
                let stderr = String::from_utf8_lossy(&o.stderr);
                return Err(format!("bail signature verification FAILED — {stderr}"));
            }
            Err(_) => {
                // gpg not installed — warn but allow (T3 path)
                eprintln!("  \x1b[33m!\x1b[0m gpg not found — bail signature not verified");
            }
        }
    }

    Ok(())
}

/// Query GitHub API for the governance bail asset URL.
fn find_bail_asset_url() -> Result<String, String> {
    let output = Command::new("curl")
        .args(["-fsSL", DEFAULT_BAIL_API])
        .output()
        .map_err(|e| format!("curl: {e}"))?;

    if !output.status.success() {
        return Err("could not reach GitHub API".to_string());
    }

    let body = String::from_utf8_lossy(&output.stdout);
    let release: serde_json::Value = serde_json::from_str(&body)
        .map_err(|e| format!("parse release JSON: {e}"))?;

    // Find asset named governance-*.bail
    if let Some(assets) = release["assets"].as_array() {
        for asset in assets {
            if let Some(name) = asset["name"].as_str()
                && name.starts_with("governance-") && name.ends_with(".bail")
                    && let Some(url) = asset["browser_download_url"].as_str() {
                        return Ok(url.to_string());
                    }
        }
    }

    Err("no governance bail found in latest release".to_string())
}

// ─────────────────────────────────────────────────────────────────────────────
// Governance import
// ─────────────────────────────────────────────────────────────────────────────

/// Import governance files from bail, or fall back to hardcoded defaults.
fn import_governance(root: &Path, state: &Path, bail_ok: bool, quiet: bool) -> Result<(), String> {
    let bail_dir = state.join("bail");
    let host = host_identity::discover();

    // HUMANFILE — personalize from bail or generate default
    let hf_path = state.join("HUMANFILE");
    if !hf_path.exists() {
        let content = if bail_ok {
            let bail_hf = fs::read_to_string(bail_dir.join("HUMANFILE")).unwrap_or_default();
            personalize_humanfile(&bail_hf, &host)
        } else {
            default_humanfile(&host)
        };
        fs::write(&hf_path, &content).map_err(|e| format!("HUMANFILE: {e}"))?;
    }

    // pin.caps
    let pin_path = state.join("pins").join("default").join("pin.caps");
    if !pin_path.exists() {
        let content = if bail_ok {
            fs::read_to_string(bail_dir.join("pin.caps"))
                .unwrap_or_else(|_| default_pin_caps().to_string())
        } else {
            default_pin_caps().to_string()
        };
        fs::write(&pin_path, &content).map_err(|e| format!("pin.caps: {e}"))?;
    }

    // Agentfile at project root
    let af_path = root.join("Agentfile");
    if !af_path.exists() {
        let content = if bail_ok {
            fs::read_to_string(bail_dir.join("Agentfile"))
                .unwrap_or_else(|_| default_agentfile().to_string())
        } else {
            default_agentfile().to_string()
        };
        fs::write(&af_path, &content).map_err(|e| format!("Agentfile: {e}"))?;
        if !quiet { println!("  \x1b[32m✓\x1b[0m Agentfile created"); }
    }

    // ostk.toml at project root
    let toml_path = root.join("ostk.toml");
    if !toml_path.exists() {
        let project_name = root.file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| "project".to_string());
        let content = if bail_ok {
            let bail_toml = fs::read_to_string(bail_dir.join("ostk.toml")).unwrap_or_default();
            personalize_toml(&bail_toml, &project_name)
        } else {
            default_toml(&project_name)
        };
        fs::write(&toml_path, &content).map_err(|e| format!("ostk.toml: {e}"))?;
        if !quiet { println!("  \x1b[32m✓\x1b[0m ostk.toml created"); }
    }

    Ok(())
}

fn personalize_humanfile(bail_content: &str, host: &host_identity::HostIdentity) -> String {
    let gpg_line = host.gpg_key.as_deref().unwrap_or("# no GPG key detected");
    bail_content
        .replace("{OPERATOR}", &format!("{} <{}>", host.full_name, host.git_email))
        .replace("{USERNAME}", &host.username)
        .replace("{GPG_KEY}", gpg_line)
}

fn personalize_toml(bail_content: &str, project_name: &str) -> String {
    // The bail's ostk.toml is the OS config — derive project-local version
    // by replacing identity section with project-specific values
    let mut result = String::new();
    result.push_str(&format!("# ostk.toml — {project_name}\n"));
    result.push_str("# Derived from os-tack/ostk.ai bail\n\n");
    result.push_str(&format!("[project]\nname = \"{project_name}\"\n"));
    result.push_str(&format!("bail = \"{DEFAULT_BAIL_REPO}\"\n\n"));

    // Copy kernel, pins, features sections from bail
    let mut in_identity = false;
    for line in bail_content.lines() {
        if line.starts_with("[identity]") {
            in_identity = true;
            continue;
        }
        if line.starts_with('[') && in_identity {
            in_identity = false;
        }
        if in_identity { continue; }
        // Skip bail's own [project] and [version] sections
        if line.starts_with("[version]") || line.starts_with("[project]") {
            continue;
        }
        if line.starts_with("release =") || line.starts_with("released_at =") {
            continue;
        }
        if line.starts_with("name =") || line.starts_with("bail =") {
            continue;
        }
        result.push_str(line);
        result.push('\n');
    }

    result
}

fn default_humanfile(host: &host_identity::HostIdentity) -> String {
    let mut content = format!(
        "# HUMANFILE — @{} ({} <{}>)\n\n\
         # This file defines your identity, secrets, and autonomy grants.\n\n",
        host.username, host.full_name, host.git_email
    );
    if let Some(ref key) = host.gpg_key {
        content.push_str(&format!("IDENTITY {}\nSIGN {key}\n\n", host.username));
    } else {
        content.push_str(&format!("IDENTITY {}\n# SIGN <your-gpg-fingerprint>\n\n", host.username));
    }
    content.push_str("MODEL claude-sonnet-4-5\n\n");
    content.push_str("SECRET <<KEYS\nANTHROPIC_API_KEY\nKEYS\n\n");
    content.push_str("DRIVER fcp-rust\nDRIVER fcp-screen\nDRIVER fcp-web\n");
    content
}

fn default_toml(project_name: &str) -> String {
    format!(
        "# ostk.toml — {project_name}\n\n\
         [project]\n\
         name = \"{project_name}\"\n\n\
         [kernel]\n\
         version = \">={v}\"\n\
         boot_mode = \"embedded-first\"\n\
         laws = [\"invisible-write\", \"ephemeral\", \"filesystem\", \"OCC\", \"invisible-infra\"]\n\n\
         [pins.default]\n\
         read = [\".ostk/\", \".language\"]\n\
         write = [\".ostk/store/default/\"]\n\
         execute = \"shell(readonly)\"\n\
         deny = [\"write-kernel\", \"modify-governance\"]\n\n\
         [features]\n\
         tui = true\n\
         agents = true\n\
         audit = true\n",
        v = env!("CARGO_PKG_VERSION")
    )
}

fn default_agentfile() -> &'static str {
    "# Agentfile — default scheduler agent\n\
     FROM auto\n\
     PROMPT \"You are the scheduling intelligence for this project.\"\n\
     TOOL shell\n\
     TOOL file:read\n\
     TOOL file:edit\n\
     LIMIT tokens 200000\n\
     BOOT ostk boot\n\
     PIN default\n"
}

fn default_pin_caps() -> &'static str {
    "read: .ostk/ .language\n\
     write: .ostk/store/default/ .ostk/needles/ .ostk/hay/\n\
     execute: shell(readonly)\n\
     deny: write-kernel modify-governance\n"
}

// ─────────────────────────────────────────────────────────────────────────────
// Git hooks
// ─────────────────────────────────────────────────────────────────────────────

fn install_git_hooks(root: &Path, state: &Path, quiet: bool) -> Result<(), String> {
    let git_dir = root.join(".git");
    if !git_dir.is_dir() {
        if !quiet {
            eprintln!("  \x1b[2mnote:\x1b[0m no git repository — hooks skipped");
        }
        return Ok(());
    }

    let hooks_dir = state.join("hooks");
    fs::create_dir_all(&hooks_dir).map_err(|e| format!("hooks/: {e}"))?;

    let post_rewrite = hooks_dir.join("post-rewrite");
    fs::write(&post_rewrite,
        "#!/bin/bash\n\
         # ostk post-rewrite hook\n\
         cause=$1\n\
         while read old_hash new_hash extra; do\n\
         \x20 ostk audit remap --old \"$old_hash\" --new \"$new_hash\" --cause \"$cause\"\n\
         done\n"
    ).map_err(|e| format!("post-rewrite hook: {e}"))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&post_rewrite).unwrap().permissions();
        perms.set_mode(0o755);
        let _ = fs::set_permissions(&post_rewrite, perms);
    }

    match Command::new("git")
        .args(["config", "core.hooksPath", ".ostk/hooks"])
        .current_dir(root)
        .status()
    {
        Ok(s) if s.success() => {}
        _ => {
            if !quiet { eprintln!("  \x1b[33m!\x1b[0m could not set git hooks path"); }
        }
    }

    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn test_dir(name: &str) -> std::path::PathBuf {
        let tmp = std::env::temp_dir().join(format!(
            "ostk_test_init_{name}_{}", std::process::id()
        ));
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        tmp
    }

    #[test]
    fn init_creates_skeleton() {
        let tmp = test_dir("skeleton");
        let state = tmp.join(".ostk");
        create_skeleton(&state).unwrap();

        assert!(state.join("needles/counter").exists());
        assert!(state.join("needles/issues.jsonl").exists());
        assert!(state.join("audit.jsonl").exists());
        assert!(state.join("identity_counter").exists());
        assert!(state.join("wip").is_dir());
        assert!(state.join("pins/default").is_dir());

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn init_creates_defaults_without_bail() {
        let tmp = test_dir("defaults");
        let state = tmp.join(".ostk");
        create_skeleton(&state).unwrap();

        import_governance(&tmp, &state, false, true).unwrap();

        assert!(state.join("HUMANFILE").exists());
        assert!(state.join("pins/default/pin.caps").exists());
        assert!(tmp.join("Agentfile").exists());
        assert!(tmp.join("ostk.toml").exists());

        // Verify HUMANFILE has identity
        let hf = fs::read_to_string(state.join("HUMANFILE")).unwrap();
        assert!(hf.contains("IDENTITY"));
        assert!(hf.contains("MODEL"));

        // Verify ostk.toml has project name
        let toml = fs::read_to_string(tmp.join("ostk.toml")).unwrap();
        assert!(toml.contains("[project]"));

        // Verify Agentfile has FROM
        let af = fs::read_to_string(tmp.join("Agentfile")).unwrap();
        assert!(af.contains("FROM auto"));

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn init_idempotent() {
        let tmp = test_dir("idempotent");
        let state = tmp.join(".ostk");
        create_skeleton(&state).unwrap();
        import_governance(&tmp, &state, false, true).unwrap();

        // Write a custom HUMANFILE
        let hf_path = state.join("HUMANFILE");
        fs::write(&hf_path, "IDENTITY custom\n").unwrap();

        // Re-run — should NOT overwrite
        import_governance(&tmp, &state, false, true).unwrap();
        let content = fs::read_to_string(&hf_path).unwrap();
        assert_eq!(content, "IDENTITY custom\n");

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn default_humanfile_with_gpg() {
        let host = host_identity::HostIdentity {
            username: "testuser".to_string(),
            full_name: "Test User".to_string(),
            git_name: "Test User".to_string(),
            git_email: "test@example.com".to_string(),
            gpg_key: Some("ABCDEF1234567890".to_string()),
        };
        let hf = default_humanfile(&host);
        assert!(hf.contains("SIGN ABCDEF1234567890"));
        assert!(hf.contains("IDENTITY testuser"));
    }

    #[test]
    fn default_humanfile_without_gpg() {
        let host = host_identity::HostIdentity {
            username: "testuser".to_string(),
            full_name: "Test User".to_string(),
            git_name: "Test User".to_string(),
            git_email: "test@example.com".to_string(),
            gpg_key: None,
        };
        let hf = default_humanfile(&host);
        assert!(hf.contains("# SIGN"));
    }

    #[test]
    fn personalize_toml_strips_identity() {
        let bail_toml = "[identity]\nname = \"@ostk.ai\"\nroot_key = \"ABC\"\n\n[kernel]\nversion = \">=2.2.1\"\n";
        let result = personalize_toml(bail_toml, "my-project");
        assert!(result.contains("name = \"my-project\""));
        assert!(!result.contains("root_key"));
        assert!(result.contains("[kernel]"));
    }
}
