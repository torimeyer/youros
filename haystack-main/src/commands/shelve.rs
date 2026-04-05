use std::fmt::Write;
use std::fs;

use crate::{append_audit, now_iso, with_needles_locked};
use crate::kernel::verb_ctx::VerbCtx;
use serde_json::json;

/// CLI entry point for shelve.
pub fn run_shelve(id: &str) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_shelve_verb(&mut ctx, id)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for shelve — writes to VerbCtx (→1157).
pub fn run_shelve_verb(ctx: &mut VerbCtx, id: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    let snapshot_str = with_needles_locked(&root, |beads| {
        let idx = beads
            .iter()
            .position(|b| b.get("id").and_then(|v| v.as_str()) == Some(id))
            .ok_or_else(|| format!("needle '{}' not found", id))?;

        let status = beads[idx]
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if status == "shelved" {
            return Err(format!("needle '{}' is already shelved", id));
        }

        let wip_dir = crate::state_dir(&root).join("wip");
        fs::create_dir_all(&wip_dir)
            .map_err(|e| format!("failed to create wip directory: {}", e))?;

        let timestamp = now_iso();
        let safe_ts = timestamp.replace([':', '.'], "-");
        let snapshot_path = wip_dir.join(format!("{}-{}.md", id, safe_ts));

        let bead_json = serde_json::to_string_pretty(&beads[idx])
            .map_err(|e| format!("failed to serialize bead: {}", e))?;
        fs::write(&snapshot_path, &bead_json)
            .map_err(|e| format!("failed to write snapshot: {}", e))?;

        beads[idx]["status"] = json!("shelved");
        Ok(snapshot_path.to_string_lossy().into_owned())
    })?;

    append_audit(
        &root,
        &json!({
            "event": "needle.shelved",
            "bead": id,
            "snapshot": &snapshot_str,
            "timestamp": now_iso()
        }),
    )?;

    writeln!(ctx, "shelved {} \u{2014} snapshot at {}", id, snapshot_str).unwrap();
    Ok(())
}

/// CLI entry point for unshelve.
pub fn run_unshelve(id: &str) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_unshelve_verb(&mut ctx, id)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for unshelve — writes to VerbCtx (→1157).
pub fn run_unshelve_verb(ctx: &mut VerbCtx, id: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    with_needles_locked(&root, |beads| {
        let idx = beads
            .iter()
            .position(|b| b.get("id").and_then(|v| v.as_str()) == Some(id))
            .ok_or_else(|| format!("needle '{}' not found", id))?;

        let status = beads[idx]
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if status != "shelved" {
            return Err(format!("needle '{}' is not shelved (status: {})", id, status));
        }

        beads[idx]["status"] = json!("open");
        Ok(())
    })?;

    append_audit(
        &root,
        &json!({
            "event": "needle.unshelved",
            "bead": id,
            "timestamp": now_iso()
        }),
    )?;

    writeln!(ctx, "unshelved {} \u{2014} status restored to open", id).unwrap();
    Ok(())
}
