use std::fmt::Write;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{
    append_audit, find_project_root, now_iso, parse_frontmatter, parse_needle_ids, parse_spec_refs,
    read_audit_events, read_needles, resolve_remaps,
};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::process::Command;

fn read_audit_committed_hashes(root: &std::path::Path) -> Result<HashSet<String>, String> {
    let mut hashes = HashSet::new();
    for event in read_audit_events(root)? {
        if matches!(
            event.get("event").and_then(|e: &Value| e.as_str()),
            Some("bead.committed") | Some("needle.committed")
        ) && let Some(hash) = event.get("commit").and_then(|h: &Value| h.as_str())
        {
            hashes.insert(hash.to_string());
        }
    }
    Ok(hashes)
}

fn resolve_orphaned(events: &[Value]) -> HashSet<String> {
    let mut orphaned = HashSet::new();
    for event in events {
        if event["event"] == "commit.orphaned"
            && let Some(c) = event["commit"].as_str() {
                orphaned.insert(c.to_string());
            }
    }
    orphaned
}

/// CLI entry point for `ostk audit check`.
pub fn run_check() -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_check_verb(&mut ctx)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for audit check (→1157).
pub fn run_check_verb(ctx: &mut VerbCtx) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let root_str = root.to_str().unwrap_or(".");
    let needles = read_needles(&root)?;
    let mut audit_events = read_audit_events(&root)?;

    let mut remaps = resolve_remaps(&audit_events);
    let mut orphaned = resolve_orphaned(&audit_events);

    // Check if post-rewrite hook is installed
    let hooks_path = Command::new("git")
        .args(["config", "core.hooksPath"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default();

    if hooks_path.is_empty() || !hooks_path.contains(".ostk/hooks") {
        writeln!(ctx, "warning: post-rewrite hook not installed. rebase/amend will create phantom hashes.").unwrap();
        writeln!(ctx, "run `ostk init` to install hooks.").unwrap();
    }

    // Check for phantom hashes (referenced in audit but missing from git, not remapped/orphaned)
    let mut phantoms_found = false;
    for event in &audit_events {
        let is_commit_event = matches!(
            event.get("event").and_then(|e: &Value| e.as_str()),
            Some("bead.committed") | Some("needle.committed")
        );
        if is_commit_event
            && let Some(hash) = event.get("commit").and_then(|v: &Value| v.as_str())
                && !remaps.contains_key(hash)
                    && !orphaned.contains(hash)
                    && !git_object_exists(root_str, hash)
                {
                    phantoms_found = true;
                    break;
                }
    }

    if phantoms_found {
        writeln!(ctx, "notice: phantom hashes detected. triggering automatic backfill recovery...").unwrap();
        run_backfill_verb(ctx, false, true)?;
        // Refresh state
        audit_events = read_audit_events(&root)?;
        remaps = resolve_remaps(&audit_events);
        orphaned = resolve_orphaned(&audit_events);
    }

    let mut committed_needles: HashSet<String> = HashSet::new();
    let mut audit_committed_hashes: HashSet<String> = HashSet::new();

    for event in &audit_events {
        let is_commit_event = matches!(
            event.get("event").and_then(|e: &Value| e.as_str()),
            Some("bead.committed") | Some("needle.committed")
        );
        if is_commit_event {
            if let Some(bead_id) = event.get("bead_id").and_then(|v: &Value| v.as_str()) {
                committed_needles.insert(bead_id.to_string());
            }
            if let Some(hash) = event.get("commit").and_then(|v: &Value| v.as_str()) {
                let final_hash = remaps.get(hash).map(|s| s.as_str()).unwrap_or(hash);
                if !orphaned.contains(hash) {
                    audit_committed_hashes.insert(final_hash.to_string());
                }
            }
        }
    }

    let git_out = Command::new("git")
        .args(["-C", root_str, "log", "--format=%H|||%s"])
        .output()
        .map_err(|e| format!("failed to run git log: {e}"))?;
    let git_log = String::from_utf8_lossy(&git_out.stdout);
    let git_log_str = git_log.as_ref();

    let mut bead_commit_refs: HashMap<String, Vec<String>> = HashMap::new();
    for line in git_log_str.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.splitn(2, "|||").collect();
        if parts.len() < 2 {
            continue;
        }
        let hash = parts[0];
        let subject = parts[1];
        for bead_id in parse_needle_ids(subject) {
            bead_commit_refs
                .entry(bead_id)
                .or_default()
                .push(hash.to_string());
        }
    }

    let mut gaps = 0usize;

    // a. Non-open needles with no needle.committed event
    for needle in &needles {
        let needle_id = needle.get("id").and_then(|v| v.as_str()).unwrap_or("");
        if needle_id.is_empty() {
            continue;
        }
        let status = needle
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("open");
        if status != "open" && !committed_needles.contains(needle_id) {
            writeln!(ctx, "MISSING: {} has no commit", needle_id).unwrap();
            gaps += 1;
        }
    }

    // b. Commits with spec/bead refs but no matching audit event
    for line in git_log_str.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.splitn(2, "|||").collect();
        if parts.len() < 2 {
            continue;
        }
        let hash = parts[0];
        let subject = parts[1];
        let bead_ids = parse_needle_ids(subject);
        let spec_refs = parse_spec_refs(subject);
        if (!bead_ids.is_empty() || !spec_refs.is_empty()) && !audit_committed_hashes.contains(hash)
        {
            if bead_ids.is_empty() {
                writeln!(ctx, "MISSING: commit {} has spec refs but no audit event", hash).unwrap();
                gaps += 1;
            } else {
                for bead_id in &bead_ids {
                    writeln!(
                        ctx,
                        "MISSING: commit {} references {} but no audit event",
                        hash, bead_id
                    ).unwrap();
                    gaps += 1;
                }
            }
        }
    }

    // c. Specs in docs/spec/ with empty/missing implements: frontmatter
    let spec_dir = root.join("docs/spec");
    if spec_dir.is_dir() && let Ok(entries) = fs::read_dir(&spec_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("md") {
                let content = fs::read_to_string(&path).unwrap_or_default();
                let (fm, _body) = parse_frontmatter(&content);
                let has_implements = fm
                    .get("implements")
                    .map(|v| !matches!(v, serde_yaml::Value::Null))
                    .unwrap_or(false);
                if !has_implements {
                    let rel_path = path
                        .strip_prefix(&root)
                        .map(|p| p.display().to_string())
                        .unwrap_or_else(|_| path.display().to_string());
                    writeln!(ctx, "MISSING: {} has no implements: field", rel_path).unwrap();
                    gaps += 1;
                }
            }
        }
    }

    // d. Open needles with commits referencing them
    for needle in &needles {
        let needle_id = needle.get("id").and_then(|v| v.as_str()).unwrap_or("");
        if needle_id.is_empty() {
            continue;
        }
        let status = needle
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("open");
        if status == "open"
            && let Some(commits) = bead_commit_refs.get(needle_id)
            && !commits.is_empty()
        {
            writeln!(ctx, "STALE: {} is open but has commits", needle_id).unwrap();
            gaps += 1;
        }
    }

    if gaps == 0 {
        writeln!(ctx, "audit chain complete").unwrap();
        Ok(())
    } else {
        writeln!(ctx, "{} gaps found", gaps).unwrap();
        Err(format!("{} gaps found", gaps))
    }
}

/// CLI entry point for `ostk audit backfill`.
pub fn run_backfill(dry_run: bool, fix_rewrites: bool) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_backfill_verb(&mut ctx, dry_run, fix_rewrites)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for audit backfill (→1157).
pub fn run_backfill_verb(ctx: &mut VerbCtx, dry_run: bool, fix_rewrites: bool) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let root_str = root.to_str().unwrap_or(".");
    let known_hashes = read_audit_committed_hashes(&root)?;

    let git_out = Command::new("git")
        .args(["-C", root_str, "log", "--format=%H|||%aI|||%s"])
        .output()
        .map_err(|e| format!("failed to run git log: {e}"))?;
    let git_log = String::from_utf8_lossy(&git_out.stdout);

    let mut added = 0usize;
    let mut skipped = 0usize;
    let mut already_present = 0usize;

    for line in git_log.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let parts: Vec<&str> = line.splitn(3, "|||").collect();
        if parts.len() < 3 {
            continue;
        }
        let hash = parts[0];
        let author_date = parts[1];
        let subject = parts[2];

        if known_hashes.contains(hash) {
            writeln!(ctx, "OK: {} already in audit trail", hash).unwrap();
            already_present += 1;
            continue;
        }

        let bead_ids = parse_needle_ids(subject);
        let spec_refs = parse_spec_refs(subject);

        if bead_ids.is_empty() && spec_refs.is_empty() {
            writeln!(
                ctx,
                "SKIP: {} — no spec/bead refs in message. Fix with: ostk commit --amend --spec X --bead Y",
                hash
            ).unwrap();
            skipped += 1;
            continue;
        }

        let refs_desc = {
            let mut parts_desc: Vec<String> = bead_ids.clone();
            if let Some(spec_ref) = spec_refs.first() {
                parts_desc.push(spec_ref.clone());
            }
            parts_desc.join(", ")
        };

        if dry_run {
            writeln!(ctx, "WOULD ADD: bead.committed for {} ({})", hash, refs_desc).unwrap();
            added += 1;
        } else {
            let first_spec = spec_refs.first().cloned().unwrap_or_default();
            if bead_ids.is_empty() {
                let event = json!({
                    "event": "bead.committed",
                    "timestamp": author_date,
                    "commit": hash,
                    "spec_ref": first_spec,
                    "retroactive": true,
                    "retroactive_added": now_iso(),
                });
                append_audit(&root, &event)?;
                added += 1;
            } else {
                for bead_id in &bead_ids {
                    let event = json!({
                        "event": "bead.committed",
                        "timestamp": author_date,
                        "commit": hash,
                        "bead_id": bead_id,
                        "spec_ref": first_spec,
                        "retroactive": true,
                        "retroactive_added": now_iso(),
                    });
                    append_audit(&root, &event)?;
                    added += 1;
                }
            }
        }
    }

    writeln!(
        ctx,
        "{} events added, {} skipped, {} already present",
        added, skipped, already_present
    ).unwrap();

    if fix_rewrites {
        let events = read_audit_events(&root)?;
        let mut remapped = 0usize;
        let mut orphaned = 0usize;

        let mut handled_hashes: HashSet<String> = HashSet::new();
        for event in &events {
            if event["event"] == "commit.remapped" {
                if let Some(old) = event["old_commit"].as_str() {
                    handled_hashes.insert(old.to_string());
                }
            } else if event["event"] == "commit.orphaned"
                && let Some(c) = event["commit"].as_str() {
                    handled_hashes.insert(c.to_string());
                }
        }

        for event in &events {
            let is_commit_event = matches!(
                event["event"].as_str(),
                Some("bead.committed") | Some("needle.committed")
            );
            if is_commit_event
                && let Some(hash) = event["commit"].as_str() {
                    if handled_hashes.contains(hash) {
                        continue;
                    }

                    if !git_object_exists(root_str, hash) {
                        let bead_id = event["bead_id"].as_str();
                        let spec_ref = event["spec_ref"].as_str();

                        if let Some(new_hash) = recover_hash(root_str, bead_id, spec_ref)? {
                            if new_hash != hash {
                                let remap_event = json!({
                                    "event": "commit.remapped",
                                    "old_commit": hash,
                                    "new_commit": new_hash,
                                    "cause": "backfill",
                                    "agent": "orchestrator",
                                    "timestamp": now_iso()
                                });
                                if dry_run {
                                    writeln!(ctx, "WOULD REMAP: {} -> {} (cause: backfill)", hash, new_hash).unwrap();
                                } else {
                                    append_audit(&root, &remap_event)?;
                                    writeln!(ctx, "REMAPPED: {} -> {} (cause: backfill)", hash, new_hash).unwrap();
                                }
                                remapped += 1;
                                handled_hashes.insert(hash.to_string());
                            }
                        } else {
                            let orphan_event = json!({
                                "event": "commit.orphaned",
                                "commit": hash,
                                "original_event": event["event"].clone(),
                                "bead": bead_id,
                                "reason": "no_reachable_replacement",
                                "timestamp": now_iso()
                            });
                            if dry_run {
                                writeln!(ctx, "WOULD ORPHAN: {} (reason: no_reachable_replacement)", hash).unwrap();
                            } else {
                                append_audit(&root, &orphan_event)?;
                                writeln!(ctx, "ORPHANED: {} (reason: no_reachable_replacement)", hash).unwrap();
                            }
                            orphaned += 1;
                            handled_hashes.insert(hash.to_string());
                        }
                    }
                }
        }
        writeln!(ctx, "fix-rewrites: {} remapped, {} orphaned", remapped, orphaned).unwrap();
    }

    Ok(())
}

fn git_object_exists(root_str: &str, hash: &str) -> bool {
    Command::new("git")
        .args(["-C", root_str, "cat-file", "-e", hash])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn recover_hash(
    root_str: &str,
    bead_id: Option<&str>,
    spec_ref: Option<&str>,
) -> Result<Option<String>, String> {
    let mut query = String::new();
    if let Some(bid) = bead_id {
        query = bid.to_string();
    } else if let Some(sref) = spec_ref {
        query = sref.to_string();
    }

    if query.is_empty() {
        return Ok(None);
    }

    let out = Command::new("git")
        .args([
            "-C",
            root_str,
            "log",
            "--all",
            "--format=%H",
            "-n",
            "1",
            "--grep",
            &query,
        ])
        .output()
        .map_err(|e| e.to_string())?;

    let hash = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if hash.is_empty() {
        Ok(None)
    } else {
        Ok(Some(hash))
    }
}

/// CLI entry point for `ostk audit remap`.
pub fn run_remap(old_hash: &str, new_hash: &str, cause: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_remap_verb(&mut ctx, old_hash, new_hash, cause)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for audit remap (→1157).
pub fn run_remap_verb(ctx: &mut VerbCtx, old_hash: &str, new_hash: &str, cause: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    let event = json!({
        "event": "commit.remapped",
        "old_commit": old_hash,
        "new_commit": new_hash,
        "cause": cause,
        "agent": "orchestrator",
        "timestamp": now_iso()
    });

    append_audit(&root, &event)?;

    writeln!(ctx, "recorded remap: {} -> {} (cause: {})", old_hash, new_hash, cause).unwrap();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_needle_ids() {
        let text = "fix: →001 and →042, but not 123";
        let ids = parse_needle_ids(text);
        assert_eq!(ids, vec!["→001", "→042"]);
    }

    #[test]
    fn test_parse_spec_refs() {
        let text = "implement spec:audit-hash and spec:tack-boot";
        let refs = parse_spec_refs(text);
        assert_eq!(refs, vec!["spec:audit-hash", "spec:tack-boot"]);
    }

    #[test]
    fn test_parse_needle_ids_no_duplicates() {
        let text = "fix →001 and →001";
        let ids = parse_needle_ids(text);
        assert_eq!(ids, vec!["→001"]);
    }
}
