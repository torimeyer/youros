//! Integration tests for the ostk onboarding flow (→844).
//!
//! Covers every permutation of the first-run experience:
//! - `ostk init` with and without git
//! - idempotency, counter preservation
//! - `ostk boot` first-run guidance
//! - bare `ostk` in piped (non-TTY) mode
//!
//! Run with: cargo test --test onboarding_e2e

use std::fs;
use std::path::PathBuf;
use std::process::Command;

// =========================================================================
// Helpers
// =========================================================================

/// Find the ostk binary using multiple strategies (mirrors needle_tests.rs).
fn find_ostk() -> PathBuf {
    // Strategy 1: CARGO_BIN_EXE_ostk (set by `cargo test` for [[bin]] name = "ostk")
    if let Some(p) = option_env!("CARGO_BIN_EXE_ostk") {
        let path = PathBuf::from(p);
        if path.exists() {
            return path;
        }
    }

    // Strategy 2: target/ relative to manifest dir
    if let Some(manifest) = option_env!("CARGO_MANIFEST_DIR") {
        let manifest = PathBuf::from(manifest);
        for profile in &["debug", "release"] {
            let candidate = manifest.join("target").join(profile).join("ostk");
            if candidate.exists() {
                return candidate;
            }
        }
    }

    // Strategy 3: PATH lookup
    if let Ok(output) = Command::new("which").arg("ostk").output() {
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if !path.is_empty() {
            return PathBuf::from(path);
        }
    }

    panic!("ostk binary not found — run `cargo build` first");
}

/// Create a Command for ostk that runs in the given directory with all API
/// keys stripped so tests never accidentally hit a real LLM endpoint.
fn ostk_cmd(dir: &std::path::Path) -> Command {
    let mut cmd = Command::new(find_ostk());
    cmd.current_dir(dir);
    // Strip API keys — tests must not touch real endpoints
    cmd.env_remove("ANTHROPIC_API_KEY");
    cmd.env_remove("OPENROUTER_API_KEY");
    cmd.env_remove("GEMINI_API_KEY");
    cmd.env_remove("OLLAMA_HOST");
    // Suppress any colour / interactive prompts
    cmd.env("NO_COLOR", "1");
    // Prevent the binary from inheriting a parent .ostk/ via cwd walk
    cmd.env("HOME", dir);
    cmd
}

// =========================================================================
// Tests
// =========================================================================

/// 1. `ostk init` in a directory with no .git/ — should exit 0, create .ostk/,
///    and mention "hooks skipped" on stderr.
#[test]
fn test_init_no_git_succeeds() {
    let tmp = tempfile::tempdir().unwrap();

    let output = ostk_cmd(tmp.path())
        .arg("init")
        .output()
        .expect("failed to run ostk init");

    let stderr = String::from_utf8_lossy(&output.stderr);
    let stdout = String::from_utf8_lossy(&output.stdout);

    assert!(
        output.status.success(),
        "ostk init should exit 0 even without git.\nstdout: {stdout}\nstderr: {stderr}"
    );
    assert!(
        tmp.path().join(".ostk").is_dir(),
        ".ostk/ should exist after init"
    );
    assert!(
        stderr.contains("hooks skipped"),
        "stderr should mention hooks skipped.\nstderr: {stderr}"
    );
}

/// 2. `git init` first, then `ostk init`. Verify git hooks path is configured.
#[test]
fn test_init_with_git_installs_hooks() {
    let tmp = tempfile::tempdir().unwrap();

    // git init
    let git_status = Command::new("git")
        .args(["init"])
        .current_dir(tmp.path())
        .output()
        .expect("git init failed");
    assert!(git_status.status.success(), "git init should succeed");

    // ostk init
    let output = ostk_cmd(tmp.path())
        .arg("init")
        .output()
        .expect("failed to run ostk init");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success(),
        "ostk init should succeed in git repo.\nstdout: {stdout}\nstderr: {stderr}"
    );

    // Verify git config core.hooksPath
    let hooks_output = Command::new("git")
        .args(["config", "core.hooksPath"])
        .current_dir(tmp.path())
        .output()
        .expect("git config should work");

    let hooks_path = String::from_utf8_lossy(&hooks_output.stdout)
        .trim()
        .to_string();
    assert_eq!(
        hooks_path, ".ostk/hooks",
        "core.hooksPath should be .ostk/hooks, got: {hooks_path}"
    );
}

/// 3. After `ostk init`, verify .ostk/ contains: needles/, audit.jsonl,
///    HUMANFILE, pins/, wip/
#[test]
fn test_init_creates_ostk_structure() {
    let tmp = tempfile::tempdir().unwrap();

    let output = ostk_cmd(tmp.path())
        .arg("init")
        .output()
        .expect("failed to run ostk init");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success(),
        "ostk init should succeed.\nstdout: {stdout}\nstderr: {stderr}"
    );

    let ostk = tmp.path().join(".ostk");
    assert!(ostk.join("needles").is_dir(), "needles/ should exist");
    assert!(ostk.join("audit.jsonl").exists(), "audit.jsonl should exist");
    assert!(ostk.join("HUMANFILE").exists(), "HUMANFILE should exist");
    assert!(ostk.join("pins").is_dir(), "pins/ should exist");
    assert!(ostk.join("wip").is_dir(), "wip/ should exist");
}

/// 4. After `ostk init`, verify docs/ directory does NOT exist.
#[test]
fn test_init_does_not_create_docs() {
    let tmp = tempfile::tempdir().unwrap();

    let output = ostk_cmd(tmp.path())
        .arg("init")
        .output()
        .expect("failed to run ostk init");

    assert!(output.status.success(), "ostk init should succeed");
    assert!(
        !tmp.path().join("docs").exists(),
        "docs/ should NOT be created by ostk init"
    );
}

/// 5. Run `ostk init` twice — both should succeed, counter should not reset.
#[test]
fn test_init_idempotent() {
    let tmp = tempfile::tempdir().unwrap();

    // First init
    let out1 = ostk_cmd(tmp.path())
        .arg("init")
        .output()
        .expect("first ostk init failed");
    assert!(out1.status.success(), "first init should succeed");

    // Read counter after first init
    let counter_path = tmp.path().join(".ostk/needles/counter");
    let counter_before = fs::read_to_string(&counter_path).unwrap_or_default();

    // Second init
    let out2 = ostk_cmd(tmp.path())
        .arg("init")
        .output()
        .expect("second ostk init failed");

    let stdout = String::from_utf8_lossy(&out2.stdout);
    let stderr = String::from_utf8_lossy(&out2.stderr);
    assert!(
        out2.status.success(),
        "second init should succeed.\nstdout: {stdout}\nstderr: {stderr}"
    );

    // Counter should not have been reset
    let counter_after = fs::read_to_string(&counter_path).unwrap_or_default();
    assert_eq!(
        counter_before, counter_after,
        "counter should not reset on re-init (before={counter_before}, after={counter_after})"
    );
}

/// 6. `git init` -> `ostk init` -> `ostk boot` should all succeed.
#[test]
fn test_init_then_boot_succeeds() {
    let tmp = tempfile::tempdir().unwrap();

    // git init
    let git = Command::new("git")
        .args(["init"])
        .current_dir(tmp.path())
        .output()
        .expect("git init failed");
    assert!(git.status.success());

    // ostk init
    let init_out = ostk_cmd(tmp.path())
        .arg("init")
        .output()
        .expect("ostk init failed");
    assert!(
        init_out.status.success(),
        "ostk init should succeed: {}",
        String::from_utf8_lossy(&init_out.stderr)
    );

    // ostk boot
    let boot_out = ostk_cmd(tmp.path())
        .arg("boot")
        .output()
        .expect("ostk boot failed");

    let stdout = String::from_utf8_lossy(&boot_out.stdout);
    let stderr = String::from_utf8_lossy(&boot_out.stderr);
    assert!(
        boot_out.status.success(),
        "ostk boot should succeed after init.\nstdout: {stdout}\nstderr: {stderr}"
    );
}

/// 7. `ostk boot` in empty dir (no .git/, no .ostk/) — should print guidance
///    about git init and exit 0 (first-run welcome is not an error).
#[test]
fn test_boot_fresh_dir_no_git() {
    let tmp = tempfile::tempdir().unwrap();

    let output = ostk_cmd(tmp.path())
        .arg("boot")
        .output()
        .expect("ostk boot failed to run");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    // boot in a fresh dir returns Ok (not an error — it prints guidance)
    assert!(
        output.status.success(),
        "ostk boot in empty dir should exit 0 (first-run welcome).\nstdout: {stdout}\nstderr: {stderr}"
    );
    assert!(
        stdout.contains("git init") || stdout.contains("git") || stderr.contains("git init"),
        "output should mention git init.\nstdout: {stdout}\nstderr: {stderr}"
    );
}

/// 8. Run `ostk` with piped stdin, no API key, no .ostk/ — should exit
///    non-zero with "no .ostk/" in stderr.
#[test]
fn test_no_args_no_key_piped_exits_error() {
    let tmp = tempfile::tempdir().unwrap();

    let output = ostk_cmd(tmp.path())
        .stdin(std::process::Stdio::piped())
        .output()
        .expect("failed to run bare ostk");

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        !output.status.success(),
        "bare ostk (piped, no key, no .ostk/) should exit non-zero.\nstderr: {stderr}"
    );
    assert!(
        stderr.contains("no .ostk/") || stderr.contains(".ostk"),
        "stderr should mention missing .ostk/ directory.\nstderr: {stderr}"
    );
}

/// 9. `ostk init --help` output should contain "--guided".
#[test]
fn test_init_guided_flag_exists() {
    let tmp = tempfile::tempdir().unwrap();

    let output = ostk_cmd(tmp.path())
        .args(["init", "--help"])
        .output()
        .expect("failed to run ostk init --help");

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("--guided"),
        "ostk init --help should mention --guided.\nstdout: {stdout}"
    );
}

/// 10. Set ANTHROPIC_API_KEY=sk-test, run `ostk init`. Verify .ostk/ created.
///     (We use `ostk init` not bare `ostk` because bare ostk tries to launch TUI.)
#[test]
fn test_auto_bootstrap_with_api_key() {
    let tmp = tempfile::tempdir().unwrap();

    let output = ostk_cmd(tmp.path())
        .env("ANTHROPIC_API_KEY", "sk-test-not-real")
        .arg("init")
        .output()
        .expect("failed to run ostk init with API key");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success(),
        "ostk init with API key should succeed.\nstdout: {stdout}\nstderr: {stderr}"
    );
    assert!(
        tmp.path().join(".ostk").is_dir(),
        ".ostk/ should be created"
    );
}

/// 11. Pre-create .ostk/needles/counter with "42", run `ostk init`,
///     verify counter still says "42".
#[test]
fn test_init_preserves_existing_counter() {
    let tmp = tempfile::tempdir().unwrap();
    let counter_path = tmp.path().join(".ostk/needles/counter");

    // Pre-create the structure with counter = 42
    fs::create_dir_all(tmp.path().join(".ostk/needles")).unwrap();
    fs::write(&counter_path, "42").unwrap();

    let output = ostk_cmd(tmp.path())
        .arg("init")
        .output()
        .expect("ostk init failed");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success(),
        "ostk init should succeed.\nstdout: {stdout}\nstderr: {stderr}"
    );

    let counter_value = fs::read_to_string(&counter_path).unwrap();
    assert_eq!(
        counter_value.trim(),
        "42",
        "counter should be preserved at 42, got: {counter_value}"
    );
}

/// 12. Create .ostk/ structure manually, run `ostk boot` — should succeed
///     and generate boot.md.
#[test]
fn test_boot_without_boot_md() {
    let tmp = tempfile::tempdir().unwrap();

    // Create minimal .ostk/ structure (mirrors what init creates)
    let ostk = tmp.path().join(".ostk");
    fs::create_dir_all(ostk.join("needles")).unwrap();
    fs::write(ostk.join("needles/counter"), "0").unwrap();
    fs::write(ostk.join("needles/issues.jsonl"), "").unwrap();
    fs::write(ostk.join("audit.jsonl"), "").unwrap();

    // Also need a git repo for boot to proceed past first-run welcome
    Command::new("git")
        .args(["init"])
        .current_dir(tmp.path())
        .output()
        .expect("git init failed");

    // Run boot without boot.md
    let output = ostk_cmd(tmp.path())
        .arg("boot")
        .output()
        .expect("ostk boot failed");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success(),
        "ostk boot should succeed and generate boot.md.\nstdout: {stdout}\nstderr: {stderr}"
    );
    assert!(
        ostk.join("boot.md").exists(),
        "boot.md should have been generated by boot"
    );
}
