use std::fmt::Write;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{append_audit, find_project_root, normalize_spec_path, now_iso, read_needles};
use serde_json::json;

/// CLI entry point (thin wrapper).
pub fn run(path: &str, severity: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, path, severity)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation (→1157).
pub fn run_verb(ctx: &mut VerbCtx, path: &str, severity: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let spec_normalized = normalize_spec_path(&root, path);

    let valid_severities = ["minor", "breaking", "contradictory"];
    if !valid_severities.contains(&severity) {
        return Err(format!(
            "invalid severity '{}': must be one of minor, breaking, contradictory",
            severity
        ));
    }

    append_audit(
        &root,
        &json!({
            "event": "spec.amended",
            "path": spec_normalized,
            "severity": severity,
            "timestamp": now_iso()
        }),
    )?;

    let beads = read_needles(&root)?;

    let affected: Vec<&serde_json::Value> = beads
        .iter()
        .filter(|b| {
            b.get("spec_ref")
                .and_then(|v| v.as_str())
                .map(|s| s == spec_normalized)
                .unwrap_or(false)
        })
        .collect();

    writeln!(ctx, "severity: {}", severity).unwrap();
    writeln!(ctx, "spec: {}", spec_normalized).unwrap();

    if affected.is_empty() {
        writeln!(ctx, "no needles reference this spec").unwrap();
    } else {
        for bead in affected {
            let id = bead.get("id").and_then(|v| v.as_str()).unwrap_or("unknown");
            let title = bead
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("untitled");
            let status = bead
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            writeln!(ctx, "  {} | {} | {}", id, title, status).unwrap();
        }
    }

    Ok(())
}
