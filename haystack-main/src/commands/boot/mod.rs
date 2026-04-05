//! `ostk boot` -- read .ostk/boot.md and report state.
//!
//! Fails with a helpful nudge when:
//! - No `.ostk/` directory found (suggests `ostk install`)
//! - No git repository found (suggests `git init` first)
//!
//! Identity layer (loaded after reap, before boot.md output):
//! - HUMANFILE   -- human preferences + tack dialect
//! - ENTITYFILE  -- instance governance (GPG chain)
//! - Agentfile   -- agent spec (OSTK_AGENTFILE env, optional)

mod context;
mod drivers;
mod entity;
mod humanfile;
mod verify;

use std::fs;
use std::path::Path;

// ── Public re-exports (keep all external callers working) ──────────────────

pub use context::{
    build_register_dump, build_register_dump_with_density, detect_stale_p0, extract_needle_ids,
    generate_continuation_prompt, BootGradient, Density,
};
pub use drivers::extensions_for_driver;

/// ->651: Detect harness type from environment.
/// Returns a static string identifying the execution context.
pub fn detect_harness() -> &'static str {
    if std::env::var("OSTK_HARNESS").as_deref() == Ok("claude-code")
        || std::env::var("TERM_PROGRAM").as_deref() == Ok("claude")
    {
        return "claude-code";
    }
    if std::env::var("OSTK_SERVE").as_deref() == Ok("1") {
        return "ostk-serve";
    }
    if std::env::var("CI").is_ok() || std::env::var("GITHUB_ACTIONS").is_ok() {
        return "ci";
    }
    "terminal"
}

/// Run `ostk boot` using CWD.
pub fn run() -> Result<(), String> {
    let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
    run_at(&cwd)
}

/// Run `ostk boot` at a specific directory.
pub fn run_at(root: &Path) -> Result<(), String> {
    let ostk_dir = crate::state_dir(root);
    let git_dir = root.join(".git");

    // ->650: first-run experience -- detect fresh install and guide the user
    if !ostk_dir.is_dir() {
        print_first_run_welcome(&git_dir);
        return Ok(());
    }

    // Reap dead agents before reporting state
    let reaped = super::reap::reap_dead_agents(&ostk_dir).unwrap_or(0);
    if reaped > 0 {
        println!("{}", crate::strings::boot::REAPED_AGENTS.replacen("{}", &reaped.to_string(), 1));
    }

    // Load identity files (who layer -- before any work output)
    let hf_result = humanfile::load_humanfile(&ostk_dir)?;
    humanfile::populate_humanfile_available(&ostk_dir);

    // ->826: Report hierarchical trust from LoadResult (T0 global + T1 project)
    humanfile::display_humanfile_trust(&hf_result);

    // Initialize secret masking — resolve HUMANFILE SECRET keys, store values
    // for output masking. Must happen after HUMANFILE load, before any tool output.
    crate::kernel::secrets::init(&hf_result.humanfile.secrets);

    entity::load_entityfile(&ostk_dir)?;

    // →895: Display GPG web-of-trust tier
    // →911: T3 anonymous — print actionable guidance instead of silent failure
    {
        let (tier, gpg_key) = crate::kernel::identity::determine_trust_tier(&ostk_dir);
        let tier_desc = match &tier {
            crate::kernel::identity::TrustTier::T0 => "dual-signed, full governance",
            crate::kernel::identity::TrustTier::T1 => "cross-signed, write access",
            crate::kernel::identity::TrustTier::T2 => "GPG present, read-only",
            crate::kernel::identity::TrustTier::T3 => "anonymous, no GPG key",
        };
        let key_suffix = gpg_key
            .as_deref()
            .map(|k| {
                if k.len() > 8 { &k[k.len()-8..] } else { k }
            })
            .unwrap_or("none");
        println!("trust: {} ({}) (key: {})", tier, tier_desc, key_suffix);

        // →911: T3 gets actionable guidance — don't leave the user stuck
        if matches!(tier, crate::kernel::identity::TrustTier::T3) {
            println!();
            println!("  \x1b[33m⚠\x1b[0m  No GPG key found — operating in read-only mode.");
            println!("     Writes, agent runs, and commits are disabled until you add a key.");
            println!();
            println!("  \x1b[1mTo unlock full access:\x1b[0m");
            println!("    1. Generate a key:  \x1b[32mgpg --full-generate-key\x1b[0m");
            println!("    2. Add to GitHub:   \x1b[32mgpg --armor --export YOUR_KEY | gh gpg-key add -\x1b[0m");
            println!("    3. Re-run:          \x1b[32mostk boot\x1b[0m");
            println!();
            println!("  \x1b[2mGuide: https://ostk.ai/docs#gpg\x1b[0m");
            println!();
        }
    }

    // Agentfile is optional -- only if OSTK_AGENTFILE env var set
    if let Ok(af) = std::env::var("OSTK_AGENTFILE") {
        entity::load_agentfile_context(&af)?;
    }

    // Write boot-done notification to store
    entity::write_identity_boot_done()?;

    // ->826: Spawn drivers from merged Humanfile (replaces legacy read_humanfile_drivers)
    drivers::spawn_humanfile_drivers_from(&ostk_dir, &hf_result.humanfile.drivers);

    // Register kernel services in .language
    for (verb, doc) in [
        ("gen-table", "file generation tracking"),
        ("elision", "read optimization (304)"),
        ("hotpr", "conflict resolution"),
        ("approval", "tool approval gate"),
        ("digest", "process + file annotations"),
        ("heartbeat", "agent health monitoring"),
        ("recovery", "tool call logging"),
    ] {
        let _ = crate::language::register_capability(
            root, verb, "service", "internal", "() -> ()", doc, true,
        );
    }
    // Conditionally register services that may not have consumers
    let has_fleet = ostk_dir.join("fleet").exists();
    let _ = crate::language::register_capability(
        root,
        "dying",
        "service",
        ".ostk/fleet/",
        "() -> (state)",
        "context pressure notification",
        has_fleet,
    );
    let _ = crate::language::register_capability(
        root,
        "nudge",
        "service",
        ".ostk/nudges/",
        "(agent,msg) -> ()",
        "inter-agent messaging",
        true,
    );

    // →963/964/965: Register kernel introspection tools
    for (verb, sig, doc) in [
        ("pitchfork", "(query) -> (matches)", "search all kernel state by keyword"),
        ("context_search", "(query) -> (matches)", "search session transcript"),
        ("context_release", "(before_turn) -> (ack)", "release processed context turns"),
    ] {
        let _ = crate::language::register_capability(
            root, verb, "service", "internal", sig, doc, true,
        );
    }

    // ->876: Register embeddings service (feature-gated)
    #[cfg(feature = "embeddings")]
    let embeddings_available = crate::squasher::embeddings::model_available();
    #[cfg(not(feature = "embeddings"))]
    let embeddings_available = false;

    let _ = crate::language::register_capability(
        root,
        "embeddings",
        "service",
        "internal",
        "(text) -> (vector)",
        "semantic similarity Burn GPU",
        embeddings_available,
    );
    if !embeddings_available {
        println!("  embeddings: not available — run \x1b[32mostk embeddings download\x1b[0m to enable semantic search");
    }

    // →1157 Phase 5: Register MCP primitives in .language so tool_definitions()
    // can be generated from .language instead of hardcoded in dispatch.rs.
    for (verb, layer, sig, doc) in [
        ("shell", "process", "(cmd, cwd?, timeout?, raw?) \u{2192} (output)", "execute command (replaces Bash)"),
        ("spawn", "process", "(alias, cmd, wait_for?, timeout?) \u{2192} (pid)", "background process"),
        ("interact", "process", "(alias, action, input?, lines?, timeout?) \u{2192} (output)", "interact with background process"),
        ("session", "state", "(action, name?) \u{2192} (result)", "session management"),
        ("lock", "state", "(action, name, timeout?) \u{2192} (result)", "coordination locks"),
        ("tack", "resolver", "(input) \u{2192} (resolution)", "resolve tack grammar to ostk commands"),
        ("help", "info", "() \u{2192} (help)", "show available tools and usage"),
        ("web_read", "device", "(url) \u{2192} (text)", "read a web page and extract text content"),
        ("web_links", "device", "(url) \u{2192} (links)", "extract all links from a web page"),
        ("web_status", "device", "(url) \u{2192} (status)", "check HTTP status and headers for a URL"),
    ] {
        let _ = crate::language::register_capability(root, verb, layer, "internal", sig, doc, true);
    }

    // →1157: Register internalized verbs — these resolve to direct function
    // calls in kernel::resolve, not fork+exec. Update .language resolution
    // from "ostk <cmd>" to "internal" so the agent loop uses direct calls.
    for (verb, layer, sig, doc) in crate::kernel::resolve::internalized_verb_metadata() {
        let _ = crate::language::register_capability(root, verb, layer, "internal", sig, doc, true);
    }

    // →1157 Phase 4: Process HUMANFILE VERB and BOOT directives.
    // VERB directives register custom verbs into .language.
    // BOOT directives are stored for execution after INIT's post-language phase.
    for (verb, resolution, sig, doc) in &hf_result.humanfile.verb_defs {
        let _ = crate::language::register_capability(root, verb, "user", resolution, sig, doc, true);
    }
    if !hf_result.humanfile.boot_steps.is_empty() {
        let steps: Vec<&str> = hf_result.humanfile.boot_steps.iter().map(|s| s.as_str()).collect();
        println!("custom boot steps: {}", steps.join(", "));
    }

    // →1157 Phase 3: Validate INIT post-language verbs against .language.
    // After the pivot (all capabilities registered), check that INIT's
    // verb sequence is resolvable. Missing verbs reduce boot confidence.
    let init_adj = crate::kernel::init::validate_at_boot(root);

    // .ostk/ exists -- read boot.md
    let boot_path = ostk_dir.join("boot.md");
    if !boot_path.exists() {
        // .ostk/ exists but no boot.md -- generate it
        println!("{}", crate::strings::boot::BOOT_MD_NOT_FOUND);
        super::boot_md::regenerate(root)?;
        println!();
    }

    // Read boot.md (needed for POST, but we output compressed register dump instead)
    let _content = std::fs::read_to_string(&boot_path)
        .map_err(|e| format!("failed to read boot.md: {e}"))?;

    // Build and print compressed register dump
    let dump = build_register_dump(root, &ostk_dir);
    println!("{dump}");

    // ->596: Report boot confidence gradient (includes INIT validation adjustment)
    let mut gradient = BootGradient::compute(root);
    gradient.confidence = (gradient.confidence + init_adj).clamp(0.0, 1.0);
    gradient.report();

    // ->651: harness detection -- tell the agent how to talk to the kernel
    let harness = detect_harness();
    let tool_hint = match harness {
        "claude-code" => crate::strings::boot::TOOL_HINT_CLAUDE_CODE,
        "ostk-serve" => crate::strings::boot::TOOL_HINT_SERVE,
        "ci" => crate::strings::boot::TOOL_HINT_CI,
        _ => crate::strings::boot::TOOL_HINT_DEFAULT,
    };
    println!("harness: {harness}");
    println!("tool pattern: {tool_hint}");

    // Verify boot.md GPG signature
    // Fatal for governed agents (LIMIT permissions governed) -- refuse unsigned boot.
    // Non-fatal for interactive/terminal sessions.
    let sig_status = verify::verify_boot_md_signature(&ostk_dir);
    let governed = std::env::var("OSTK_PERMISSIONS").as_deref() == Ok("governed");
    match &sig_status {
        verify::BootSignatureStatus::Verified(_key) => println!("{}", crate::strings::boot::BOOT_VERIFIED),
        verify::BootSignatureStatus::ExpiredKey(key) => {
            // ->772: expired-but-not-revoked key -- warn, don't fail (even governed)
            eprintln!("{}", crate::strings::boot::BOOT_EXPIRED_KEY.replacen("{}", key, 1));
        }
        verify::BootSignatureStatus::Unsigned => {
            if governed {
                return Err(crate::strings::boot::BOOT_UNSIGNED_GOVERNED.into());
            }
            println!("{}", crate::strings::boot::BOOT_UNSIGNED);
        }
        verify::BootSignatureStatus::Invalid(msg) => {
            if governed {
                return Err(crate::strings::boot::BOOT_INVALID_GOVERNED.replacen("{}", msg, 1));
            }
            eprintln!("{}", crate::strings::boot::BOOT_INVALID.replacen("{}", msg, 1));
        }
        verify::BootSignatureStatus::GpgAbsent => {
            println!("{}", crate::strings::boot::BOOT_GPG_ABSENT)
        }
    }

    // Check OS version pin
    let version_path = ostk_dir.join("version");
    if version_path.exists()
        && let Ok(pinned) = fs::read_to_string(&version_path) {
            let pinned = pinned.trim();
            let current = env!("CARGO_PKG_VERSION");
            if pinned != current {
                eprintln!("{}", crate::strings::boot::VERSION_MISMATCH
                    .replacen("{}", current, 1)
                    .replacen("{}", pinned, 1));
                eprintln!("{}", crate::strings::boot::VERSION_MISMATCH_HINT);
            }
        }

    // Update registry timestamp
    if let Err(e) = crate::kernel::registry::update_registry_timestamp(root) {
        eprintln!("{}", crate::strings::boot::REGISTRY_TIMESTAMP_FAIL.replacen("{}", &e, 1));
    }

    // ->759: Stale boot.md detection -- warn if P0 references closed needles
    context::check_stale_p0(&ostk_dir);

    // ->813: Dynamic continuation prompt generation
    if std::env::var("OSTK_UPDATE_PROMPT").as_deref() == Ok("1") {
        let prompt = generate_continuation_prompt(&ostk_dir);
        let prompt_dir = ostk_dir.join("prompts");
        let _ = fs::create_dir_all(&prompt_dir);
        let prompt_path = prompt_dir.join("continuation.md");
        match fs::write(&prompt_path, &prompt) {
            Ok(_) => println!("continuation prompt: updated ({} bytes)", prompt.len()),
            Err(e) => eprintln!("warning: failed to write continuation prompt: {e}"),
        }
    }

    // Surface last session checkpoint
    display_last_checkpoint(&ostk_dir);

    // Surface pending nudges
    read_and_display_nudges(&ostk_dir);

    Ok(())
}

// ── ->650: first-run welcome ────────────────────────────────────────────────

/// Print the three-state first-run welcome when no .ostk/ is found.
/// State 1: no git repo -- suggest git init first
/// State 2: git repo but no .ostk/ -- full welcome + init options
pub fn print_first_run_welcome(git_dir: &Path) {
    if !git_dir.is_dir() {
        println!("{}", crate::strings::boot::NO_GIT_REPO);
        println!();
        println!("{}", crate::strings::boot::GIT_INIT_HINT);
        return;
    }

    // Git repo exists but no .ostk/ -- this is the primary first-run case
    println!();
    println!("{}", crate::strings::boot::WELCOME);
    println!();
    println!("{}", crate::strings::boot::NO_OSTK_DIR);
    println!();
    println!("{}", crate::strings::boot::OS_INVISIBLE);
    println!();
    println!("{}", crate::strings::boot::FIRST_RUN_COMMANDS);
    println!();
    println!("{}", crate::strings::boot::FREE_TOKENS);
    println!();
}

// ── Nudge display ───────────────────────────────────────────────────────────

/// Read pending nudges from `.ostk/nudges/*.jsonl`, display them, and
/// truncate the files so they aren't repeated on the next boot.
/// Display the most recent session checkpoint from .boot/checkpoints/.
fn display_last_checkpoint(ostk_dir: &Path) {
    let dir = ostk_dir.join(".boot/checkpoints");
    let mut entries: Vec<_> = match fs::read_dir(&dir) {
        Ok(e) => e.filter_map(|e| e.ok())
            .filter(|e| e.path().extension().is_some_and(|ext| ext == "md"))
            .map(|e| e.path())
            .collect(),
        Err(_) => return,
    };
    if entries.is_empty() {
        return;
    }
    entries.sort();
    let latest = entries.last().unwrap();
    let name = latest.file_name().unwrap_or_default().to_string_lossy();
    // Read first line after the title for a summary
    let summary = fs::read_to_string(latest)
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("**Date**:"))
                .map(|l| l.trim_start_matches("**Date**: ").to_string())
        })
        .unwrap_or_default();
    println!("last checkpoint: {} ({})", name.trim_end_matches(".md"), summary);
}

fn read_and_display_nudges(ostk_dir: &Path) {
    let nudge_dir = ostk_dir.join("nudges");
    if !nudge_dir.is_dir() {
        return;
    }

    let entries = match fs::read_dir(&nudge_dir) {
        Ok(e) => e,
        Err(_) => return,
    };

    let mut displayed = false;

    for entry in entries.flatten() {
        let path = entry.path();

        // Only process *.jsonl, skip *.consumed.jsonl or non-jsonl files
        let name = match path.file_name().and_then(|n| n.to_str()) {
            Some(n) if n.ends_with(".jsonl") && !n.ends_with(".consumed.jsonl") => {
                n.trim_end_matches(".jsonl").to_string()
            }
            _ => continue,
        };

        let content = match fs::read_to_string(&path) {
            Ok(c) if !c.trim().is_empty() => c,
            _ => continue,
        };

        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            // Parse JSON, extract message. Skip malformed lines.
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(line)
                && let Some(msg) = val.get("message").and_then(|m| m.as_str()) {
                    if !displayed {
                        println!(); // blank line before nudge block
                        displayed = true;
                    }
                    println!("[nudge] from {name}: {msg}");
                }
        }

        // Truncate the file after reading
        let _ = fs::write(&path, "");
    }
}

// ── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU32, Ordering};

    static TEST_COUNTER: AtomicU32 = AtomicU32::new(0);
    fn test_dir(prefix: &str) -> std::path::PathBuf {
        let n = TEST_COUNTER.fetch_add(1, Ordering::SeqCst);
        let pid = std::process::id();
        std::env::temp_dir().join(format!("ostk_boot_test_{prefix}_{pid}_{n}"))
    }

    #[test]
    fn test_boot_no_git_no_ostk() {
        let tmp = test_dir("no_git");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        // ->650: no git = first-run welcome, returns Ok (not an error)
        let result = run_at(&tmp);
        assert!(result.is_ok(), "no-git first-run should return Ok: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_git_but_no_ostk() {
        let tmp = test_dir("git_no_ostk");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(tmp.join(".git")).unwrap();

        // ->650: git but no .ostk/ = first-run welcome, returns Ok
        let result = run_at(&tmp);
        assert!(result.is_ok(), "first-run with git should return Ok: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_with_ostk_and_boot_md() {
        let tmp = test_dir("with_boot");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();
        fs::write(
            ostk_dir.join("boot.md"),
            "# test project -- boot context\n\nAll systems go.\n",
        )
        .unwrap();

        let result = run_at(&tmp);
        assert!(result.is_ok(), "boot should succeed: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_ostk_exists_but_no_boot_md() {
        let tmp = test_dir("no_boot_md");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();

        let result = run_at(&tmp);
        assert!(result.is_ok(), "boot should succeed by generating boot.md: {:?}", result);
        assert!(
            ostk_dir.join("boot.md").exists(),
            "boot.md should have been generated"
        );

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_reads_and_consumes_nudges() {
        let tmp = test_dir("nudges");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        let nudge_dir = ostk_dir.join("nudges");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::create_dir_all(&nudge_dir).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();
        fs::write(
            ostk_dir.join("boot.md"),
            "# test -- nudge boot\n",
        )
        .unwrap();

        // Write nudge files
        fs::write(
            nudge_dir.join("alice.jsonl"),
            "{\"message\":\"check the tests\"}\n{\"message\":\"also fix the docs\"}\n",
        )
        .unwrap();
        fs::write(
            nudge_dir.join("bob.jsonl"),
            "{\"message\":\"deploy when ready\"}\n",
        )
        .unwrap();
        // Malformed line should be skipped
        fs::write(
            nudge_dir.join("charlie.jsonl"),
            "not json\n{\"message\":\"valid line\"}\n",
        )
        .unwrap();
        // Empty file should be skipped
        fs::write(nudge_dir.join("empty.jsonl"), "").unwrap();
        // Consumed file should be skipped
        fs::write(
            nudge_dir.join("old.consumed.jsonl"),
            "{\"message\":\"stale\"}\n",
        )
        .unwrap();

        let result = run_at(&tmp);
        assert!(result.is_ok(), "boot should succeed: {:?}", result);

        // After boot, nudge files should be truncated (empty)
        let alice_content = fs::read_to_string(nudge_dir.join("alice.jsonl")).unwrap();
        assert!(
            alice_content.is_empty(),
            "alice.jsonl should be empty after boot, got: {alice_content}"
        );
        let bob_content = fs::read_to_string(nudge_dir.join("bob.jsonl")).unwrap();
        assert!(
            bob_content.is_empty(),
            "bob.jsonl should be empty after boot, got: {bob_content}"
        );
        let charlie_content = fs::read_to_string(nudge_dir.join("charlie.jsonl")).unwrap();
        assert!(
            charlie_content.is_empty(),
            "charlie.jsonl should be empty after boot, got: {charlie_content}"
        );
        // Consumed file should be untouched
        let old_content = fs::read_to_string(nudge_dir.join("old.consumed.jsonl")).unwrap();
        assert!(
            !old_content.is_empty(),
            "consumed file should not be touched"
        );

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_no_nudge_dir_is_fine() {
        let tmp = test_dir("no_nudges");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("boot.md"), "# test\n").unwrap();

        // No nudges directory at all -- should still succeed
        let result = run_at(&tmp);
        assert!(result.is_ok(), "boot without nudge dir should succeed: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_matching_version_no_error() {
        let tmp = test_dir("version_match");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("boot.md"), "# test\n").unwrap();
        fs::write(ostk_dir.join("version"), env!("CARGO_PKG_VERSION")).unwrap();

        let result = run_at(&tmp);
        assert!(result.is_ok(), "boot should succeed with matching version: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_mismatched_version_still_succeeds() {
        let tmp = test_dir("version_mismatch");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("boot.md"), "# test\n").unwrap();
        fs::write(ostk_dir.join("version"), "0.0.1").unwrap();

        // Boot should still succeed (warning only, not blocking)
        let result = run_at(&tmp);
        assert!(result.is_ok(), "boot should succeed even with version mismatch: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_missing_version_file_succeeds() {
        let tmp = test_dir("version_missing");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("boot.md"), "# test\n").unwrap();
        // Deliberately no version file -- backwards compat

        let result = run_at(&tmp);
        assert!(result.is_ok(), "boot should succeed without version file: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_boot_with_identity_files_succeeds() {
        // Full integration: boot with HUMANFILE + ENTITYFILE present
        let tmp = test_dir("boot_with_identity");
        let _ = fs::remove_dir_all(&tmp);
        let ostk_dir = tmp.join(".ostk");
        fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        fs::write(ostk_dir.join("needles/counter"), "0").unwrap();
        fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("audit.jsonl"), "").unwrap();
        fs::write(ostk_dir.join("boot.md"), "# test -- identity boot\n").unwrap();

        fs::write(
            ostk_dir.join("HUMANFILE"),
            "# HUMANFILE\n| :plan | plan | routes |\n| :compile | triage | routes |\nLast updated: 2026-03-11\n",
        )
        .unwrap();
        fs::write(
            ostk_dir.join("ENTITYFILE"),
            "**Version:** 1.0\nGovernance content.\n",
        )
        .unwrap();

        let result = run_at(&tmp);
        assert!(result.is_ok(), "boot with identity files should succeed: {:?}", result);

        let _ = fs::remove_dir_all(&tmp);
    }
}

#[cfg(test)]
mod first_run_tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn test_first_run_no_git() {
        // No .git, no .ostk/ -- should print git init suggestion
        let tmp = TempDir::new().unwrap();
        let result = run_at(tmp.path());
        // Returns Ok (first-run is not an error)
        assert!(result.is_ok(), "first-run should return Ok: {:?}", result);
    }

    #[test]
    fn test_first_run_with_git_no_ostk() {
        // .git exists but no .ostk/ -- should print welcome
        let tmp = TempDir::new().unwrap();
        fs::create_dir(tmp.path().join(".git")).unwrap();
        let result = run_at(tmp.path());
        assert!(result.is_ok(), "first-run with git should return Ok: {:?}", result);
    }
}

#[cfg(test)]
mod journey_tests {
    use super::*;
    use std::fs;

    fn tmp(name: &str) -> std::path::PathBuf {
        let d = tempfile::tempdir().unwrap();
        let p = d.keep().join(name);
        fs::create_dir_all(&p).unwrap();
        p
    }

    fn init_ostk(root: &std::path::Path) {
        let h = crate::state_dir(&root);
        fs::create_dir_all(h.join("needles")).unwrap();
        fs::write(h.join("needles/counter"), "0").unwrap();
        fs::write(h.join("needles/issues.jsonl"), "").unwrap();
        fs::write(h.join("audit.jsonl"), "").unwrap();
    }

    // ->844: Onboarding journey e2e tests

    #[test]
    fn fresh_dir_boot_returns_ok() {
        let root = tmp("fresh");
        let result = run_at(&root);
        assert!(result.is_ok(), "fresh dir boot should show welcome: {:?}", result);
    }

    #[test]
    fn git_init_then_boot() {
        let root = tmp("git_only");
        fs::create_dir_all(root.join(".git")).unwrap();
        let result = run_at(&root);
        assert!(result.is_ok(), "git-only boot should show welcome: {:?}", result);
    }

    #[test]
    fn init_creates_ostk_structure() {
        let root = tmp("init_struct");
        fs::create_dir_all(root.join(".git")).unwrap();
        init_ostk(&root);
        let h = crate::state_dir(&root);
        assert!(h.join("needles/counter").exists());
        assert!(h.join("needles/issues.jsonl").exists());
        assert!(h.join("audit.jsonl").exists());
    }

    #[test]
    fn init_then_boot_succeeds() {
        let root = tmp("init_boot");
        fs::create_dir_all(root.join(".git")).unwrap();
        init_ostk(&root);
        fs::write(root.join(".ostk/boot.md"), "# test\nReady.\n").unwrap();
        let result = run_at(&root);
        assert!(result.is_ok(), "boot after init should succeed: {:?}", result);
    }

    #[test]
    fn boot_without_boot_md_still_ok() {
        let root = tmp("no_bootmd");
        fs::create_dir_all(root.join(".git")).unwrap();
        init_ostk(&root);
        // No boot.md
        let result = run_at(&root);
        assert!(result.is_ok(), "boot without boot.md should work: {:?}", result);
    }

    #[test]
    fn boot_is_idempotent() {
        let root = tmp("idempotent");
        fs::create_dir_all(root.join(".git")).unwrap();
        init_ostk(&root);
        fs::write(root.join(".ostk/needles/counter"), "5").unwrap();
        fs::write(root.join(".ostk/boot.md"), "# boot\n").unwrap();

        assert!(run_at(&root).is_ok());
        assert!(run_at(&root).is_ok());
        let counter = fs::read_to_string(root.join(".ostk/needles/counter")).unwrap();
        assert_eq!(counter.trim(), "5", "boot should not modify counter");
    }
}
