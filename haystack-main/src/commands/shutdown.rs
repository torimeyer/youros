//! `ostk shutdown` — clean session termination (→450).
//!
//! The shutdown sequence:
//! 1. Regenerate boot.md from current state (force overwrite)
//! 2. Write registers-dump.md with volatile context
//! 3. Verify boot.md is current (boot-failure bench check)
//! 4. Commit with conversation attribution
//! 5. If verification fails → error, don't commit stale state
//!    5b. Compile new tack patterns into HUMANFILE (→579)

use crate::{append_audit, find_project_root, now_iso, read_needles};
use regex::Regex;
use serde_json::json;
use std::collections::HashSet;
use std::fs;
use std::path::Path;
use std::process::Command;

/// Run `ostk shutdown` with optional commit message and agent name.
pub fn run(message: Option<&str>, agent: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let now = now_iso();

    println!("{}", crate::strings::shutdown::HEADER);
    println!();

    // Step 1: Regenerate boot.md from current state
    println!("{}", crate::strings::shutdown::STEP1_REGEN);
    super::boot_md::regenerate(&root)?;
    println!("{}", crate::strings::shutdown::STEP1_DONE);

    // Step 1b: Sign boot.md with GPG (non-fatal)
    let ostk_dir = crate::state_dir(&root);
    sign_boot_md(&ostk_dir);

    // →773: Sign Agentfiles (*.af) with kernel key (non-fatal)
    sign_agentfiles(&ostk_dir);

    // Step 1c: Compile .language from session audit data
    println!("{}", crate::strings::shutdown::STEP1C_COMPILING);
    match compile_language(&root) {
        Ok(n) if n > 0 => println!("   .language: {} verb(s) updated", n),
        Ok(_) => println!("   .language: no changes"),
        Err(e) => eprintln!("   warning: .language compilation failed: {}", e),
    }

    // Step 1d: Write verbs.lock (resident tool set for cold boot)
    let verbs_lock_path = crate::state_dir(&root).join("verbs.lock");
    let verbs_lock_existed = verbs_lock_path.exists();
    maybe_write_verbs_lock(&root, false)?;
    if !verbs_lock_existed && verbs_lock_path.exists() {
        println!("   verbs.lock: written");
    }

    // Step 2: Write registers-dump.md with volatile context
    println!("{}", crate::strings::shutdown::STEP2_WRITING);
    write_registers_dump(&root, agent)?;
    println!("{}", crate::strings::shutdown::STEP2_DONE);

    // Step 3: Verify boot.md is current (inline boot-failure check)
    println!("{}", crate::strings::shutdown::STEP3_VERIFYING);
    verify_boot_md_current(&root)?;
    println!("{}", crate::strings::shutdown::STEP3_DONE);

    // Step 4: Commit with attribution
    let msg = message.unwrap_or(crate::strings::shutdown::DEFAULT_MESSAGE);
    println!("{}", crate::strings::shutdown::STEP4_COMMITTING);

    // Stage the ostk state files (include boot.md.asc if it exists)
    let sd = crate::state_dir(&root);
    let sd_name = sd.strip_prefix(&root).unwrap_or(sd.as_ref());
    let sd_str = sd_name.to_string_lossy();
    let git_add = Command::new("git")
        .args([
            "add".to_string(),
            format!("{sd_str}/boot.md"),
            format!("{sd_str}/boot.md.asc"),
            format!("{sd_str}/registers-dump.md"),
            format!("{sd_str}/audit.jsonl"),
            format!("{sd_str}/.language"),
            format!("{sd_str}/verbs.lock"),
        ])
        .current_dir(&root)
        .output()
        .map_err(|e| format!("git add failed: {e}"))?;

    if !git_add.status.success() {
        let stderr = String::from_utf8_lossy(&git_add.stderr);
        // Non-fatal: some files may not exist yet
        if !stderr.is_empty() {
            eprintln!("   warning: git add: {}", stderr.trim());
        }
    }

    // Also stage index.json if it exists
    let _ = Command::new("git")
        .args(["add", &format!("{sd_str}/index.json")])
        .current_dir(&root)
        .output();

    // Check if there's anything to commit
    let status = Command::new("git")
        .args(["status", "--porcelain"])
        .current_dir(&root)
        .output()
        .map_err(|e| format!("git status failed: {e}"))?;

    let status_out = String::from_utf8_lossy(&status.stdout);
    if status_out.trim().is_empty() {
        println!("{}", crate::strings::shutdown::NOTHING_TO_COMMIT);
    } else {
        let commit_msg = format!("feat: {msg}\n\nagent: {agent}\nshutdown: {now}");
        let commit = Command::new("git")
            .args(["commit", "-m", &commit_msg])
            .current_dir(&root)
            .output()
            .map_err(|e| format!("git commit failed: {e}"))?;

        if !commit.status.success() {
            let stderr = String::from_utf8_lossy(&commit.stderr);
            return Err(format!("git commit failed: {}", stderr.trim()));
        }
        println!("   committed: {msg}");
    }

    // Step 5: Audit
    append_audit(&root, &json!({
        "event": "session.shutdown",
        "agent": agent,
        "boot_regenerated": true,
        "registers_dumped": true,
        "verified": true,
        "timestamp": now,
    }))?;

    // Step 5b: Compile new tack patterns into HUMANFILE (→579)
    println!("{}", crate::strings::shutdown::STEP5B_COMPILING);
    match compile_humanfile(&root, agent) {
        Ok(added) if added > 0 => println!("   HUMANFILE: {} new verb(s) added", added),
        Ok(_) => println!("   HUMANFILE: no new verbs found"),
        Err(e) => eprintln!("   warning: HUMANFILE update failed: {}", e),
    }

    // Step 5a: Compile OS state into a protocol offer (background)
    println!("{}", crate::strings::shutdown::STEP5_OFFER);
    compile_offer_background(&root, agent, &now);

    println!();
    println!("{}", crate::strings::shutdown::COMPLETE);

    Ok(())
}

/// Compile the session delta into a checkpoint and write to
/// .ostk/.boot/checkpoints/TIMESTAMP.md — spawned as background, non-blocking.
fn compile_offer_background(root: &Path, agent: &str, now: &str) {
    let checkpoints_dir = crate::state_dir(root).join(".boot/checkpoints");
    let _ = fs::create_dir_all(&checkpoints_dir);

    // Read audit delta — last N events since last shutdown
    let audit_path = crate::state_dir(root).join("audit.jsonl");
    let audit_delta = fs::read_to_string(&audit_path)
        .map(|s| {
            let lines: Vec<&str> = s.lines().rev().take(20).collect();
            lines.into_iter().rev().collect::<Vec<_>>().join("\n")
        })
        .unwrap_or_default();

    // Read needles filed this session (events with task.added)
    let new_needles: Vec<String> = audit_delta
        .lines()
        .filter(|l| l.contains("task.added") || l.contains("needle.added"))
        .filter_map(|l| {
            serde_json::from_str::<serde_json::Value>(l).ok()
                .and_then(|v| v["title"].as_str().map(|t| format!("- {t}")))
        })
        .collect();

    // Compute a short offer ID from the timestamp
    let offer_id: String = now.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .take(8)
        .collect();

    let checkpoint = format!(
r#"# SESSION CHECKPOINT — @ostk.prime.{offer_id}

**From**: {agent}
**Date**: {now}

---

## What This Session Compiled

### New needles
{needles}

### OS state delta
- registers-dump.md: written ✓
- audit.jsonl: updated ✓

---
"#,
        needles = if new_needles.is_empty() {
            "- (none filed this session)".to_string()
        } else {
            new_needles.join("\n")
        }
    );

    let checkpoint_path = checkpoints_dir.join(format!("{offer_id}.md"));
    if let Err(e) = fs::write(&checkpoint_path, checkpoint) {
        eprintln!("   warning: checkpoint compilation failed: {e}");
    } else {
        println!("   checkpoint: .ostk/.boot/checkpoints/{offer_id}.md");
    }

    // Retention: keep only last 5 checkpoints
    if let Ok(entries) = fs::read_dir(&checkpoints_dir) {
        let mut paths: Vec<_> = entries
            .filter_map(|e| e.ok())
            .filter(|e| e.path().extension().is_some_and(|ext| ext == "md"))
            .map(|e| e.path())
            .collect();
        paths.sort();
        if paths.len() > 5 {
            for old in &paths[..paths.len() - 5] {
                let _ = fs::remove_file(old);
            }
        }
    }
}

/// Write registers-dump.md with volatile context from current session.
fn write_registers_dump(root: &Path, agent: &str) -> Result<(), String> {
    let dump_path = crate::state_dir(root).join("registers-dump.md");
    let now = now_iso();

    let needles = read_needles(root)?;
    let in_progress: Vec<&serde_json::Value> = needles
        .iter()
        .filter(|n| {
            n.get("status").and_then(|v| v.as_str()) == Some("in_progress")
        })
        .collect();

    let open: Vec<&serde_json::Value> = needles
        .iter()
        .filter(|n| {
            n.get("status").and_then(|v| v.as_str()) == Some("open")
        })
        .collect();

    let mut content = format!("# Registers Dump — {agent}\n\n");
    content.push_str(&format!("Generated: {now}\n\n"));

    // In-progress work
    content.push_str("## Active work\n\n");
    if in_progress.is_empty() {
        content.push_str("No in-progress needles.\n\n");
    } else {
        for n in &in_progress {
            let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("?");
            let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("?");
            content.push_str(&format!("- {id}: {title}\n"));
        }
        content.push('\n');
    }

    // Open work queue
    content.push_str("## Open needles (next up)\n\n");
    let show_count = open.len().min(10);
    for n in open.iter().take(show_count) {
        let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("?");
        let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("?");
        let priority = n.get("priority").and_then(|v| v.as_str()).unwrap_or("?");
        content.push_str(&format!("- {id} [{priority}] {title}\n"));
    }
    if open.len() > show_count {
        content.push_str(&format!("- ... and {} more\n", open.len() - show_count));
    }
    content.push('\n');

    // Summary counts
    let total = needles.len();
    let closed = needles
        .iter()
        .filter(|n| n.get("status").and_then(|v| v.as_str()) == Some("closed"))
        .count();
    content.push_str("## Summary\n\n");
    content.push_str(&format!("- Total needles: {total}\n"));
    content.push_str(&format!("- Open: {}\n", open.len()));
    content.push_str(&format!("- In progress: {}\n", in_progress.len()));
    content.push_str(&format!("- Closed: {closed}\n"));

    fs::write(&dump_path, content)
        .map_err(|e| format!("failed to write registers-dump.md: {e}"))?;

    Ok(())
}

/// Verify boot.md is a valid tack init script.
/// Checks for required boot directives rather than stale needle counts.
fn verify_boot_md_current(root: &Path) -> Result<(), String> {
    let boot_path = crate::state_dir(root).join("boot.md");
    let boot_content = fs::read_to_string(&boot_path)
        .map_err(|e| format!("failed to read boot.md: {e}"))?;

    // Required tack directives in the bootloader
    let required = [":verify", ":post", ":clock", ":reap", ":init", ":load", ":boot"];
    for directive in &required {
        if !boot_content.contains(directive) {
            return Err(format!(
                "boot.md missing required directive '{directive}'. \
                 boot.md must be a tack init script."
            ));
        }
    }

    Ok(())
}


/// Sign boot.md with the kernel key (resolved from .ostk/kernel.key or env).
///
/// Non-fatal: if GPG is absent or the key is unavailable, logs a warning.
/// The signature is stored at `.ostk/boot.md.asc` alongside boot.md.
fn sign_boot_md(ostk_dir: &Path) {
    let boot_path = ostk_dir.join("boot.md");
    let sig_path = ostk_dir.join("boot.md.asc");

    // Remove any stale signature first so gpg doesn't prompt about overwrite
    let _ = fs::remove_file(&sig_path);

    // →773: Resolve kernel key via centralized sign::resolve_kernel_key()
    let kernel_key = crate::commands::sign::resolve_kernel_key(ostk_dir);

    // Sign with kernel key explicitly — not GPG default.
    let output = Command::new("gpg")
        .args([
            "--batch",
            "--yes",
            "--local-user",
            &kernel_key,
            "--detach-sign",
            "--armor",
            "--output",
        ])
        .arg(&sig_path)
        .arg(&boot_path)
        .output();

    match output {
        Ok(o) if o.status.success() => {
            println!("{}", crate::strings::shutdown::BOOT_MD_SIGNED);
        }
        Ok(o) => {
            let msg = String::from_utf8_lossy(&o.stderr);
            let msg = msg.trim();
            eprintln!("{}", crate::strings::shutdown::BOOT_MD_SIGN_FAIL.replacen("{}", msg, 1));
        }
        Err(e) => {
            eprintln!("{}", crate::strings::shutdown::GPG_NOT_AVAILABLE.replacen("{}", &e.to_string(), 1));
        }
    }
}

/// →773: Sign all Agentfiles (*.af) in .ostk/ with the kernel key.
/// Non-fatal — warns on failure, never blocks shutdown.
fn sign_agentfiles(ostk_dir: &Path) {
    let entries = match fs::read_dir(ostk_dir) {
        Ok(e) => e,
        Err(_) => return,
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("af") {
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("?");
            if let Err(e) = crate::commands::sign::sign_agentfile(&path, ostk_dir) {
                eprintln!("  warning: could not sign {}: {}", name, e);
            }
        }
    }
}

/// Step 5b: Compile new tack verbs from the current session log into HUMANFILE.
/// Reads session JSONL, diffs against known verbs, appends new rows.
/// Best-effort — non-fatal if HUMANFILE or session file is absent.
fn compile_humanfile(root: &Path, agent: &str) -> Result<usize, String> {
    let humanfile_path = crate::state_dir(root).join("HUMANFILE");
    if !humanfile_path.exists() {
        return Ok(0);
    }
    let session_path = crate::state_dir(root).join(format!("sessions/{}.jsonl", agent));
    if !session_path.exists() {
        return Ok(0);
    }

    let humanfile_content = fs::read_to_string(&humanfile_path)
        .map_err(|e| format!("failed to read HUMANFILE: {e}"))?;
    let session_content = fs::read_to_string(&session_path)
        .map_err(|e| format!("failed to read session: {e}"))?;

    // Parse known verbs from HUMANFILE table (| :verb | ...)
    let known: HashSet<String> = humanfile_content
        .lines()
        .filter(|l| l.trim_start().starts_with('|'))
        .filter_map(|l| {
            let cols: Vec<&str> = l.split('|').collect();
            cols.get(1).map(|c| c.trim().to_string())
        })
        .filter(|s| s.starts_with(':') && s.len() > 1)
        .map(|s| s[1..].split('/').next().unwrap_or("").trim().to_string())
        .filter(|s| !s.is_empty())
        .collect();

    // Extract verbs used in this session from tack input fields
    let verb_re = Regex::new(r#"(?:^|[\s"'\{,\[]):([a-zA-Z][a-zA-Z0-9_-]*)"#).unwrap();
    let mut new_verbs: Vec<String> = session_content
        .lines()
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .filter_map(|v| {
            v.get("input")
                .or_else(|| v.get("args_summary"))
                .and_then(|i| i.as_str())
                .map(|s| s.to_string())
        })
        .flat_map(|input| {
            verb_re
                .captures_iter(&input)
                .filter_map(|c| c.get(1).map(|m| m.as_str().to_string()))
                .collect::<Vec<_>>()
        })
        .filter(|v| !known.contains(v))
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();

    new_verbs.sort();
    new_verbs.dedup();

    if new_verbs.is_empty() {
        return Ok(0);
    }

    // Append new rows after the last table row in HUMANFILE
    let new_rows: String = new_verbs
        .iter()
        .map(|v| format!("| :{v} | [inferred: {v}] | resolved by LLM |\n"))
        .collect();

    // Find insertion point: after last | line in Quick Start section
    let mut lines: Vec<&str> = humanfile_content.lines().collect();
    let insert_after = lines
        .iter()
        .rposition(|l| l.trim_start().starts_with('|'))
        .unwrap_or(lines.len().saturating_sub(1));

    lines.insert(insert_after + 1, "");
    let mut new_content = lines.join("\n");
    new_content.push('\n');
    new_content.push_str(&new_rows);

    fs::write(&humanfile_path, &new_content)
        .map_err(|e| format!("failed to write HUMANFILE: {e}"))?;

    Ok(new_verbs.len())
}

// ── .language decay (shutdown only) ───────────────────────────────────────
//
// Verb discovery and momentum gain are now handled by `language::record_verb()`
// at tack resolution time. Shutdown only decays unused verbs.

use crate::language::{deduplicate_by_resolution, format_language, parse_language};

/// Minimum momentum for a verb to be written to verbs.lock.
const TOOL_RESIDENT_THRESHOLD: f64 = 0.45;

/// Write `.ostk/verbs.lock` — the minimum resident tool set for cold boot.
///
/// Reads `.language`, deduplicates by resolution, filters to user-layer verbs
/// whose momentum meets `TOOL_RESIDENT_THRESHOLD`, sorts alphabetically, and
/// writes the lock file.  If the lock file already exists and `force` is false,
/// returns `Ok(())` without overwriting (idempotent).
pub fn maybe_write_verbs_lock(root: &Path, force: bool) -> Result<(), String> {
    let lock_path = crate::state_dir(root).join("verbs.lock");

    if lock_path.exists() && !force {
        return Ok(());
    }

    let lang_path = crate::state_dir(root).join(".language");
    let lang_content = fs::read_to_string(&lang_path).unwrap_or_default();
    let entries = parse_language(&lang_content);

    if entries.is_empty() {
        return Ok(());
    }

    let deduped = deduplicate_by_resolution(&entries);

    let mut resident: Vec<&str> = deduped
        .iter()
        .filter(|e| e.layer == "user" && e.momentum >= TOOL_RESIDENT_THRESHOLD)
        .map(|e| e.verb.as_str())
        .collect();
    resident.sort();

    let mut output = String::from(
        "# verbs.lock — minimum resident tools for cold boot\n\
         # Generated by: ostk shutdown\n",
    );
    for verb in &resident {
        output.push_str(verb);
        output.push('\n');
    }

    fs::write(&lock_path, &output)
        .map_err(|e| format!("failed to write verbs.lock: {e}"))?;

    Ok(())
}

/// Decay unused verbs in `.language` at shutdown.
///
/// Verbs used this session have their `last_gen` set to today's epoch day by
/// `record_verb()`. At shutdown we decay any user-layer verb whose `last_gen`
/// is older than today (i.e. not used this session). Kernel and ceremony verbs
/// are never decayed.
fn compile_language(root: &Path) -> Result<usize, String> {
    let lang_path = crate::state_dir(root).join(".language");

    // Read existing .language (or nothing to decay)
    let lang_content = fs::read_to_string(&lang_path).unwrap_or_default();
    let mut entries = parse_language(&lang_content);

    if entries.is_empty() {
        return Ok(0);
    }

    let today = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        / 86400;

    let mut changes = 0usize;

    // Decay unused verbs (skip kernel, ceremony, device, and service layers)
    for entry in entries.iter_mut() {
        if entry.layer != "kernel"
            && entry.layer != "ceremony"
            && entry.layer != "device"
            && entry.layer != "service"
            && entry.last_gen < today
        {
            let old = entry.momentum;
            entry.momentum = (entry.momentum - 0.05).max(0.0);
            if (entry.momentum - old).abs() > f64::EPSILON {
                changes += 1;
            }
        }
    }

    // Write back
    let output = format_language(&entries);
    fs::write(&lang_path, output)
        .map_err(|e| format!("failed to write .language: {e}"))?;

    Ok(changes)
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── sign_boot_md tests ──────────────────────────────────────────────────

    /// sign_boot_md is non-fatal even when GPG is absent or fails.
    /// We can't guarantee GPG presence in CI, so just verify it returns without
    /// panicking and does not leave a corrupted state.
    #[test]
    fn test_sign_boot_md_no_panic() {
        let tmp = tempfile::TempDir::new().unwrap();
        let ostk_dir = tmp.path().join(".ostk");
        std::fs::create_dir_all(&ostk_dir).unwrap();
        std::fs::write(ostk_dir.join("boot.md"), "# test boot\n").unwrap();

        // sign_boot_md is always non-fatal — should not panic or return an error
        sign_boot_md(&ostk_dir);
        // No assertion on the .asc file — GPG may or may not be present in CI.
        // The function must complete without panicking.
    }

    /// If boot.md doesn't exist, sign_boot_md should still not panic.
    #[test]
    fn test_sign_boot_md_missing_boot_md_no_panic() {
        let tmp = tempfile::TempDir::new().unwrap();
        let ostk_dir = tmp.path().join(".ostk");
        std::fs::create_dir_all(&ostk_dir).unwrap();
        // Intentionally NO boot.md

        sign_boot_md(&ostk_dir);
        // GPG will fail; function must not panic.
    }

    #[test]
    fn test_verify_boot_md_valid_tack() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        std::fs::create_dir_all(crate::state_dir(&root)).unwrap();

        let boot = "# boot.md — @ostk.prime\n\n\
                     :verify .primefile\n:post\n:clock\n:reap\n\n\
                     :init @ostk.prime+N\n\
                     :load .language\n\
                     :load fcp-tack\n\n\
                     :boot\n:refine\n:compile\n:work\n";
        std::fs::write(root.join(".ostk/boot.md"), boot).unwrap();

        let result = verify_boot_md_current(root);
        assert!(result.is_ok(), "valid tack boot should pass: {:?}", result);
    }

    #[test]
    fn test_verify_boot_md_missing_directive() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        std::fs::create_dir_all(crate::state_dir(&root)).unwrap();

        // Missing :post, :clock, :reap
        let boot = "# boot.md\n\n:verify .primefile\n:init @ostk.prime+N\n:load .language\n:boot\n";
        std::fs::write(root.join(".ostk/boot.md"), boot).unwrap();

        let result = verify_boot_md_current(root);
        assert!(result.is_err(), "should fail: missing required directives");
    }

    #[test]
    fn test_verify_boot_md_prose_rejected() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        std::fs::create_dir_all(crate::state_dir(&root)).unwrap();

        // Old prose format — no tack directives
        let boot = "# project — boot context\n\n## Recent commits\n\n- Needles: 100 total\n";
        std::fs::write(root.join(".ostk/boot.md"), boot).unwrap();

        let result = verify_boot_md_current(root);
        assert!(result.is_err(), "prose boot.md should be rejected");
    }

    // ── .language decay tests ────────────────────────────────────────────

    #[test]
    fn test_parse_language_entries() {
        let content = r#"# .language — compiled tack dialect
# verb | tier | layer | last_gen | half_life | momentum | resolution
:boot       | 1 | kernel  | 0    | 0     | 1.0  | ostk boot
:compile    | 1 | user    | 0    | 500   | 0.8  | ostk compile
"#;
        let entries = parse_language(content);
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].verb, "boot");
        assert_eq!(entries[0].tier, 1);
        assert_eq!(entries[0].layer, "kernel");
        assert!((entries[0].momentum - 1.0).abs() < f64::EPSILON);
        assert_eq!(entries[1].verb, "compile");
        assert_eq!(entries[1].layer, "user");
        assert!((entries[1].momentum - 0.8).abs() < f64::EPSILON);
    }

    #[test]
    fn test_verb_used_today_no_decay() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        // Compute today's epoch day
        let today = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() / 86400;

        // Write .language with a user verb whose last_gen is today
        let lang = format!(
            "# .language — compiled tack dialect\n\
             # verb | tier | layer | last_gen | half_life | momentum | resolution\n\
             :compile    | 1 | user    | {}    | 500   | 0.8  | ostk compile\n",
            today
        );
        std::fs::write(hs.join(".language"), lang).unwrap();

        let changes = compile_language(root).unwrap();
        assert_eq!(changes, 0, "verb used today should not decay");

        let updated = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&updated);
        let compile_entry = entries.iter().find(|e| e.verb == "compile").unwrap();
        assert!(
            (compile_entry.momentum - 0.8).abs() < f64::EPSILON,
            "momentum should remain 0.8 for today's verb, got {}",
            compile_entry.momentum
        );
    }

    #[test]
    fn test_kernel_layer_verbs_no_decay() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        // Write .language with kernel and ceremony verbs (old last_gen)
        let lang = "# .language — compiled tack dialect\n\
                     # verb | tier | layer | last_gen | half_life | momentum | resolution\n\
                     :boot       | 1 | kernel  | 0    | 0     | 1.0  | ostk boot\n\
                     :negotiate  | 1 | ceremony| 0    | 10000 | 0.9  | ostk negotiate\n";
        std::fs::write(hs.join(".language"), lang).unwrap();

        let _changes = compile_language(root).unwrap();

        let updated = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&updated);

        let boot = entries.iter().find(|e| e.verb == "boot").unwrap();
        assert!(
            (boot.momentum - 1.0).abs() < f64::EPSILON,
            "kernel verb should not decay, got {}",
            boot.momentum
        );

        let negotiate = entries.iter().find(|e| e.verb == "negotiate").unwrap();
        assert!(
            (negotiate.momentum - 0.9).abs() < f64::EPSILON,
            "ceremony verb should not decay, got {}",
            negotiate.momentum
        );
    }

    #[test]
    fn test_user_layer_verbs_decay() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        // Write .language with a user verb at momentum 0.8, last_gen=0 (old)
        let lang = "# .language — compiled tack dialect\n\
                     # verb | tier | layer | last_gen | half_life | momentum | resolution\n\
                     :compile    | 1 | user    | 0    | 500   | 0.8  | ostk compile\n";
        std::fs::write(hs.join(".language"), lang).unwrap();

        let changes = compile_language(root).unwrap();
        assert!(changes > 0, "should have decay changes");

        let updated = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&updated);
        let compile_entry = entries.iter().find(|e| e.verb == "compile").unwrap();
        assert!(
            (compile_entry.momentum - 0.75).abs() < f64::EPSILON,
            "user verb should decay from 0.8 to 0.75, got {}",
            compile_entry.momentum
        );
    }

    #[test]
    fn test_shutdown_decays_unused_verbs() {
        // Integration test: record_verb sets last_gen to today,
        // then compile_language skips that verb but decays others.
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        let today = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() / 86400;

        // Two user verbs: one used today, one stale
        let lang = format!(
            "# .language — compiled tack dialect\n\
             :active     | 1 | user    | {}    | 500   | 0.8  | ostk active |  | \n\
             :stale      | 1 | user    | 0     | 500   | 0.6  | ostk stale |  | \n",
            today
        );
        std::fs::write(hs.join(".language"), lang).unwrap();

        let changes = compile_language(root).unwrap();
        assert_eq!(changes, 1, "only stale verb should decay");

        let updated = std::fs::read_to_string(hs.join(".language")).unwrap();
        let entries = parse_language(&updated);

        let active = entries.iter().find(|e| e.verb == "active").unwrap();
        assert!(
            (active.momentum - 0.8).abs() < f64::EPSILON,
            "active verb should not decay, got {}",
            active.momentum
        );

        let stale = entries.iter().find(|e| e.verb == "stale").unwrap();
        assert!(
            (stale.momentum - 0.55).abs() < f64::EPSILON,
            "stale verb should decay from 0.6 to 0.55, got {}",
            stale.momentum
        );
    }

    // ── verbs.lock tests ──────────────────────────────────────────────────

    #[test]
    fn test_maybe_write_verbs_lock_creates_file() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let hs = crate::state_dir(&root);
        std::fs::create_dir_all(&hs).unwrap();

        // Write .language with user verbs at various momentums
        let lang = "# .language — compiled tack dialect\n\
                     # verb | tier | layer | last_gen | half_life | momentum | resolution\n\
                     :boot       | 1 | kernel  | 0    | 0     | 1.0  | ostk boot\n\
                     :compile    | 1 | user    | 0    | 500   | 0.8  | ostk compile\n\
                     :trace      | 2 | user    | 0    | 500   | 0.5  | ostk trace\n\
                     :dimverb    | 2 | user    | 0    | 500   | 0.3  | ostk dimverb\n\
                     :alpha      | 1 | user    | 0    | 500   | 0.9  | ostk alpha\n";
        std::fs::write(hs.join(".language"), lang).unwrap();

        let lock_path = hs.join("verbs.lock");
        assert!(!lock_path.exists(), "verbs.lock should not exist yet");

        // First call — should create the file
        maybe_write_verbs_lock(root, false).unwrap();
        assert!(lock_path.exists(), "verbs.lock should have been created");

        let content = std::fs::read_to_string(&lock_path).unwrap();

        // Header present
        assert!(content.contains("# verbs.lock"), "should have header");
        assert!(content.contains("Generated by: ostk shutdown"), "should have generator line");

        // Parse non-comment lines (the actual verb entries)
        let verbs: Vec<&str> = content.lines()
            .filter(|l| !l.starts_with('#') && !l.is_empty())
            .collect();

        // Only user verbs with momentum >= 0.45 should appear, sorted alphabetically
        assert!(verbs.contains(&"alpha"), "alpha (0.9) should be in verbs.lock");
        assert!(verbs.contains(&"compile"), "compile (0.8) should be in verbs.lock");
        assert!(verbs.contains(&"trace"), "trace (0.5) should be in verbs.lock");
        assert!(!verbs.contains(&"dimverb"), "dimverb (0.3) should NOT be in verbs.lock");
        assert!(!verbs.contains(&"boot"), "kernel verb boot should NOT be in verbs.lock");

        // Verify alphabetical order
        assert_eq!(verbs, vec!["alpha", "compile", "trace"],
                   "verbs should be sorted alphabetically");

        // Second call with force=false — should NOT overwrite
        // Mutate the file to detect overwrites
        std::fs::write(&lock_path, "SENTINEL\n").unwrap();
        maybe_write_verbs_lock(root, false).unwrap();
        let after = std::fs::read_to_string(&lock_path).unwrap();
        assert_eq!(after, "SENTINEL\n",
                   "verbs.lock should not be overwritten when force=false");
    }
}
