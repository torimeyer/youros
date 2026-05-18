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

/// Required sections every spec should have, with placeholder descriptions.
/// Order matches the template produced by `ostk doc draft`.
const REQUIRED_SECTIONS: &[(&str, &str)] = &[
    ("Problem", "_What's broken and who's affected._"),
    ("Goals", "_What success looks like._"),
    ("Non-goals", "_What this change will not do._"),
    ("Solution", "_How we'll fix it._"),
    ("Edge cases", "_Edge cases and failure modes to consider._"),
    ("Success criteria", "_Measurable outcomes that confirm success._"),
    ("Acceptance criteria", "_Checkboxes a reviewer uses to sign off._"),
    ("Verification", "_Commands to run to confirm everything works._"),
    ("USER FEEDBACK", "_Unresolved questions for the spec author._"),
    ("DECISION", "_Final disposition: approved, rejected, or deferred._"),
];

/// CLI entry point (thin wrapper).
pub fn run(path: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, path)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for :promote (→1157, →1472).
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

    // →1472: backfill any missing required sections before promoting.
    let backfilled_body = backfill_sections(&body);

    // Warn (not block) if DECISION section is still empty after backfill.
    if is_section_empty(&backfilled_body, "DECISION") {
        eprintln!("warning: DECISION section is empty — spec has not been approved or rejected");
    }

    // →1411: warn if the spec is landing in docs/spec/ instead of ~/.myos/specs/
    eprintln!(
        "warning: spec is landing in docs/spec/ — consider moving to ~/.myos/specs/ (see →1411)"
    );

    let spec_dir = root.join("docs").join("spec");
    fs::create_dir_all(&spec_dir).map_err(|e| format!("Failed to create docs/spec/: {}", e))?;

    let now = now_iso();

    // Lint: warn if discussion: field is missing from frontmatter
    if !fm.contains_key("discussion") {
        eprintln!("warning: spec missing discussion: field");
    }

    fm.insert("status".to_string(), YamlValue::String("spec".to_string()));
    fm.insert("promoted_at".to_string(), YamlValue::String(now.clone()));

    write_with_frontmatter(target_path, &fm, &backfilled_body)?;

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

/// Returns `body` with any missing required sections appended using placeholder text.
/// Sections already present (case-insensitive `## Header` match) are left untouched.
pub fn backfill_sections(body: &str) -> String {
    let mut result = body.to_string();

    for (section, placeholder) in REQUIRED_SECTIONS {
        if !has_section(&result, section) {
            // Ensure a blank line before the new section.
            if !result.ends_with('\n') {
                result.push('\n');
            }
            if !result.ends_with("\n\n") {
                result.push('\n');
            }
            result.push_str(&format!("## {}\n{}\n", section, placeholder));
        }
    }

    result
}

/// Returns true if `body` contains a `## <section>` heading (case-insensitive).
fn has_section(body: &str, section: &str) -> bool {
    let needle = format!("## {}", section.to_lowercase());
    body.lines()
        .any(|line| line.trim().to_lowercase() == needle)
}

/// Returns true if the named section exists but contains only placeholder or blank content.
/// Used to detect an unfilled DECISION block before promoting.
fn is_section_empty(body: &str, section: &str) -> bool {
    let heading = format!("## {}", section.to_lowercase());
    let lines: Vec<&str> = body.lines().collect();

    let start = lines
        .iter()
        .position(|l| l.trim().to_lowercase() == heading);

    let start = match start {
        Some(i) => i + 1,
        None => return true, // section absent → treat as empty
    };

    // Collect lines until the next `##` heading or end of file.
    let content: String = lines[start..]
        .iter()
        .take_while(|l| !l.trim().starts_with("## "))
        .cloned()
        .collect::<Vec<_>>()
        .join("\n");

    let trimmed = content.trim();
    // Empty, or only the placeholder italic line (starts with `_` and ends with `_`)
    trimmed.is_empty()
        || (trimmed.starts_with('_') && trimmed.ends_with('_') && !trimmed.contains('\n'))
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── has_section ──────────────────────────────────────────────────────────

    #[test]
    fn has_section_matches_exact() {
        let body = "## Problem\nSome content.\n";
        assert!(has_section(body, "Problem"));
    }

    #[test]
    fn has_section_case_insensitive() {
        let body = "## problem\nSome content.\n";
        assert!(has_section(body, "Problem"));
    }

    #[test]
    fn has_section_absent() {
        let body = "## Goals\nSome content.\n";
        assert!(!has_section(body, "Problem"));
    }

    // ── is_section_empty ─────────────────────────────────────────────────────

    #[test]
    fn is_section_empty_with_placeholder() {
        let body = "## DECISION\n_Final disposition: approved, rejected, or deferred._\n";
        assert!(is_section_empty(body, "DECISION"));
    }

    #[test]
    fn is_section_empty_with_real_content() {
        let body = "## DECISION\nApproved 2026-05-17 (tori).\n";
        assert!(!is_section_empty(body, "DECISION"));
    }

    #[test]
    fn is_section_empty_absent_section() {
        let body = "## Goals\nSome content.\n";
        assert!(is_section_empty(body, "DECISION"));
    }

    // ── backfill_sections ────────────────────────────────────────────────────

    #[test]
    fn backfill_zero_section_draft_gets_all_ten() {
        // A draft body with only a checkbox and no section headers.
        let body = "Some intro text.\n\n- [ ] AC-001: thing works\n";
        let result = backfill_sections(body);

        for (section, placeholder) in REQUIRED_SECTIONS {
            assert!(
                result.contains(&format!("## {}", section)),
                "missing section: {}",
                section
            );
            assert!(
                result.contains(placeholder),
                "missing placeholder for: {}",
                section
            );
        }
    }

    #[test]
    fn backfill_full_draft_is_noop() {
        // A body that already has all required sections.
        let mut body = String::new();
        for (section, placeholder) in REQUIRED_SECTIONS {
            body.push_str(&format!("## {}\n{}\n\n", section, placeholder));
        }
        body.push_str("- [ ] AC-001: thing works\n");

        let result = backfill_sections(&body);

        // Count occurrences of each section header — should be exactly 1.
        for (section, _) in REQUIRED_SECTIONS {
            let heading = format!("## {}", section);
            let count = result.matches(&heading).count();
            assert_eq!(count, 1, "section {} appears {} times", section, count);
        }
    }

    #[test]
    fn backfill_partial_draft_adds_only_missing() {
        let body = "## Problem\nReal problem description.\n\n## Goals\nReal goals.\n\n- [ ] AC-001\n";
        let result = backfill_sections(body);

        // Problem and Goals must not be duplicated.
        assert_eq!(result.matches("## Problem").count(), 1);
        assert_eq!(result.matches("## Goals").count(), 1);

        // The other 8 sections must have been added.
        let missing = ["Non-goals", "Solution", "Edge cases", "Success criteria",
                       "Acceptance criteria", "Verification", "USER FEEDBACK", "DECISION"];
        for section in &missing {
            assert!(result.contains(&format!("## {}", section)), "missing: {}", section);
        }
    }

    #[test]
    fn backfill_preserves_original_content() {
        let body = "## Problem\nThis is the real problem.\n\n- [ ] AC-001\n";
        let result = backfill_sections(body);
        assert!(result.contains("This is the real problem."));
    }
}
