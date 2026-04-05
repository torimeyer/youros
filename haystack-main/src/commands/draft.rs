use std::fmt::Write;
use std::collections::HashMap;
use std::fs;

use crate::{append_audit, now_iso, write_with_frontmatter};
use crate::kernel::verb_ctx::VerbCtx;
use serde_json::json;
use serde_yaml::Value as YamlValue;

/// CLI entry point.
pub fn run(title: &str) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, title)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation — writes to VerbCtx (→1157).
pub fn run_verb(ctx: &mut VerbCtx, title: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();

    let slug: String = {
        let lowered = title.to_lowercase();
        let replaced: String = lowered
            .chars()
            .map(|c| if c.is_alphanumeric() { c } else { '-' })
            .collect();
        replaced
            .split('-')
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join("-")
    };

    let draft_dir = root.join("docs").join("draft");
    fs::create_dir_all(&draft_dir).map_err(|e| format!("Failed to create docs/draft/: {}", e))?;

    let file_path = draft_dir.join(format!("{}.md", slug));

    let now = now_iso();

    let mut fm: HashMap<String, YamlValue> = HashMap::new();
    fm.insert("status".to_string(), YamlValue::String("draft".to_string()));
    fm.insert("title".to_string(), YamlValue::String(title.to_string()));
    fm.insert("created_at".to_string(), YamlValue::String(now.clone()));

    write_with_frontmatter(&file_path, &fm, "")?;

    let rel_path = format!("docs/draft/{}.md", slug);

    let event = json!({
        "event": "draft.created",
        "path": rel_path,
        "title": title,
        "timestamp": now
    });

    append_audit(&root, &event)?;

    writeln!(ctx, "{}", file_path.display()).unwrap();

    Ok(())
}
