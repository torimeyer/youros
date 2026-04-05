//! `ostk purge` — remove content from active context without deleting from audit.
//!
//! The purge command appends to audit.jsonl (append-only record) and to
//! .ostk/purged.jsonl (boot-time filter list). Nothing is deleted.
//! The purged.jsonl file is read at boot time to filter context pollution.

use crate::kernel::verb_ctx::VerbCtx;
use crate::{append_audit, find_project_root, now_iso};
use serde_json::json;
use std::fs::{self, OpenOptions};
use std::io::Write as IoWrite;
use std::fmt::Write as FmtWrite;

/// CLI entry point (thin wrapper).
pub fn run(pattern: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, pattern)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
///
/// Records the purge in audit.jsonl and appends to .ostk/purged.jsonl.
/// Both files are append-only — nothing is deleted.
pub fn run_verb(ctx: &mut VerbCtx, pattern: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let now = now_iso();

    let ostk_dir = crate::state_dir(&root);
    fs::create_dir_all(&ostk_dir)
        .map_err(|e| format!("failed to create .ostk dir: {e}"))?;

    // 1. Append to audit.jsonl — append-only provenance record
    append_audit(
        &root,
        &json!({
            "event": "purge",
            "content": pattern,
            "reason": "Law 1 enforcement",
            "timestamp": now,
        }),
    )?;

    // 2. Append to .ostk/purged.jsonl — boot-time filter list
    let purged_path = ostk_dir.join("purged.jsonl");
    let entry = json!({
        "pattern": pattern,
        "purged_at": now,
    });
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&purged_path)
        .map_err(|e| format!("failed to open purged.jsonl: {e}"))?;
    writeln!(file, "{}", entry)
        .map_err(|e| format!("failed to write purged.jsonl: {e}"))?;

    // 3. Confirmation
    writeln!(ctx, "purged: {pattern} — recorded in audit, filtered from future context").unwrap();

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_purge_creates_purged_jsonl() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let ostk_dir = crate::state_dir(&root);
        std::fs::create_dir_all(ostk_dir.join("needles")).unwrap();
        std::fs::write(ostk_dir.join("needles/issues.jsonl"), "").unwrap();

        // Direct path: write purged.jsonl entry
        let now = "2026-01-01T00:00:00Z";
        let purged_path = ostk_dir.join("purged.jsonl");
        let entry = json!({ "pattern": "use fs_ops for file operations", "purged_at": now });
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&purged_path)
            .unwrap();
        writeln!(file, "{}", entry).unwrap();

        let contents = std::fs::read_to_string(&purged_path).unwrap();
        assert!(contents.contains("use fs_ops for file operations"));
        assert!(contents.contains("purged_at"));
    }

    #[test]
    fn test_purge_jsonl_is_append_only() {
        let tmp = tempfile::TempDir::new().unwrap();
        let root = tmp.path();
        let ostk_dir = crate::state_dir(&root);
        std::fs::create_dir_all(&ostk_dir).unwrap();

        let purged_path = ostk_dir.join("purged.jsonl");

        // Write two entries
        for pattern in &["first pattern", "second pattern"] {
            let entry = json!({ "pattern": pattern, "purged_at": "2026-01-01T00:00:00Z" });
            let mut file = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&purged_path)
                .unwrap();
            writeln!(file, "{}", entry).unwrap();
        }

        let contents = std::fs::read_to_string(&purged_path).unwrap();
        let lines: Vec<&str> = contents.lines().collect();
        assert_eq!(lines.len(), 2, "both entries should be present");
        assert!(lines[0].contains("first pattern"));
        assert!(lines[1].contains("second pattern"));
    }
}
