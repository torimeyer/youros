use std::fmt::Write;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{
    append_audit, find_project_root, normalize_spec_path, now_iso, parse_frontmatter,
    post_mutation, write_with_frontmatter, MutationEvent,
};
use serde_json::json;
use serde_yaml::Value as YamlValue;
use std::fs;
use std::path::Path;

/// CLI entry point (thin wrapper).
pub fn run(path: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, path)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for :promote (→1157).
pub fn run_verb(ctx: &mut VerbCtx, path: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    let source_path = Path::new(path);

    let content =
        fs::read_to_string(source_path).map_err(|e| format!("Failed to read {}: {}", path, e))?;

    let (mut fm, body) = parse_frontmatter(&content);

    if !body.contains("- [ ]") {
        return Err(
            "Draft must contain at least one unchecked checkbox (- [ ]) as acceptance criteria"
                .to_string(),
        );
    }

    match fm.get("status") {
        Some(YamlValue::String(s)) if s == "spec" => {
            return Err("File is already a spec (status: spec)".to_string());
        }
        Some(YamlValue::String(s)) if s != "draft" => {
            return Err(format!("Cannot promote: unexpected status '{}'", s));
        }
        _ => {}
    }

    let path_str = source_path.to_string_lossy().into_owned();
    if !path_str.contains("docs/draft/") {
        return Err("Source path must be inside docs/draft/".to_string());
    }

    let target_path_str = path_str.replace("docs/draft/", "docs/spec/");
    let target_path = Path::new(&target_path_str);

    if target_path.exists() {
        return Err(format!("Target already exists: {}", target_path.display()));
    }

    let spec_dir = root.join("docs").join("spec");
    fs::create_dir_all(&spec_dir).map_err(|e| format!("Failed to create docs/spec/: {}", e))?;

    let now = now_iso();

    // Lint: warn if discussion: field is missing from frontmatter
    if !fm.contains_key("discussion") {
        eprintln!("warning: spec missing discussion: field");
    }

    fm.insert("status".to_string(), YamlValue::String("spec".to_string()));
    fm.insert("promoted_at".to_string(), YamlValue::String(now.clone()));

    write_with_frontmatter(target_path, &fm, &body)?;

    fs::remove_file(source_path).map_err(|e| format!("Failed to delete original draft: {}", e))?;

    let event = json!({
        "event": "spec.promoted",
        "from": normalize_spec_path(&root, path),
        "to": normalize_spec_path(&root, &target_path_str),
        "timestamp": now
    });

    append_audit(&root, &event)?;

    writeln!(ctx, "{}", target_path.display()).unwrap();

    // Implicit compile: update index
    let _ = post_mutation(&root, &MutationEvent::SpecPromoted);

    Ok(())
}
