use std::fmt::Write as FmtWrite;

use crate::kernel::verb_ctx::VerbCtx;
use crate::{
    append_audit, find_project_root, next_needle_id, normalize_spec_path, now_iso,
    read_needles, with_needles_locked,
};
use serde_json::{json, Value};
use std::fs;
use std::io::{self, BufRead, Write};

// ---------------------------------------------------------------------------
// Spec decomposition (existing path)
// ---------------------------------------------------------------------------

/// CLI entry point for spec decomposition (thin wrapper).
pub fn run_spec(path: &str) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_spec_verb(&mut ctx, path)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for spec decomposition (→1157).
pub fn run_spec_verb(ctx: &mut VerbCtx, path: &str) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let spec_ref = normalize_spec_path(&root, path);

    let content = fs::read_to_string(path)
        .map_err(|e| format!("failed to read spec file '{}': {}", path, e))?;

    let checkboxes: Vec<(String, String)> = content
        .lines()
        .filter(|line| line.contains("- [ ] "))
        .map(|line| {
            let full = line.trim().to_string();
            let title = if let Some(idx) = line.find("- [ ] ") {
                line[idx + 6..].trim().to_string()
            } else {
                full.clone()
            };
            (title, full)
        })
        .collect();

    if checkboxes.is_empty() {
        return Err("no checkbox items found in spec file".to_string());
    }

    let new_needles = with_needles_locked(&root, |beads| {
        // DEDUP GUARD under lock — no race between check and insert
        let already_decomposed = beads.iter().any(|b| {
            b.get("spec_ref")
                .and_then(|v| v.as_str())
                .is_some_and(|s| s == spec_ref)
        });
        if already_decomposed {
            return Err("spec already decomposed into needles".to_string());
        }

        let mut created = Vec::new();
        for (title, acceptance) in &checkboxes {
            let id = next_needle_id(&root)?;
            let bead = json!({
                "id": id,
                "title": title,
                "spec_ref": spec_ref,
                "acceptance": acceptance,
                "status": "open",
                "created_at": now_iso(),
                "issue_type": "needle"
            });
            created.push(bead);
        }
        beads.extend(created.clone());
        Ok(created)
    })?;

    let new_ids: Vec<String> = new_needles
        .iter()
        .map(|b| b["id"].as_str().unwrap_or("").to_string())
        .collect();

    append_audit(
        &root,
        &json!({
            "event": "needles.created",
            "spec": spec_ref,
            "needles": new_ids,
            "timestamp": now_iso()
        }),
    )?;

    for bead in &new_needles {
        writeln!(ctx,
            "{} {}",
            bead["id"].as_str().unwrap_or(""),
            bead["title"].as_str().unwrap_or("")
        ).unwrap();
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Needle decomposition (Phase 4)
// ---------------------------------------------------------------------------

/// A proposed sub-needle from LLM or manual decomposition.
#[derive(Debug, Clone)]
pub struct SubNeedleProposal {
    pub title: String,
    pub ac: Option<String>,
    pub priority: String,
}

/// Detect whether an argument is a needle ID (starts with arrow or numeric-only).
pub fn is_needle_id(arg: &str) -> bool {
    let trimmed = arg.trim();
    // →NNN or just NNN (bare number)
    trimmed.starts_with('\u{2192}')
        || trimmed.starts_with("->")
        || trimmed.chars().all(|c| c.is_ascii_digit())
}

/// Normalize a needle ID argument to the canonical →NNN form.
fn normalize_needle_id(arg: &str) -> String {
    let trimmed = arg.trim();
    if trimmed.starts_with('\u{2192}') {
        return trimmed.to_string();
    }
    if let Some(rest) = trimmed.strip_prefix("->") {
        return format!("\u{2192}{rest}");
    }
    // Bare number
    if trimmed.chars().all(|c| c.is_ascii_digit()) {
        return format!("\u{2192}{trimmed}");
    }
    trimmed.to_string()
}

/// Find a needle by ID in the list.
fn find_needle<'a>(needles: &'a [Value], id: &str) -> Option<&'a Value> {
    needles.iter().find(|n| {
        n.get("id").and_then(|v| v.as_str()) == Some(id)
    })
}

/// CLI entry point for needle decomposition (thin wrapper).
pub fn run_needle(needle_arg: &str, auto: bool) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_needle_verb(&mut ctx, needle_arg, auto)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for needle decomposition (→1157).
///
/// Reads the parent needle, optionally calls an LLM for proposals,
/// lets the user review (unless --auto), then writes sub-needles
/// and updates the parent.
pub fn run_needle_verb(ctx: &mut VerbCtx, needle_arg: &str, auto: bool) -> Result<(), String> {
    let root = ctx.root.to_path_buf();
    let parent_id = normalize_needle_id(needle_arg);

    // Read the parent needle
    let needles = read_needles(&root)?;
    let parent = find_needle(&needles, &parent_id)
        .ok_or_else(|| format!("needle '{}' not found", parent_id))?;

    let title = parent.get("title").and_then(|v| v.as_str()).unwrap_or("(untitled)");
    let description = parent.get("description").and_then(|v| v.as_str()).unwrap_or("");
    let ac = parent.get("ac").and_then(|v| v.as_str()).unwrap_or("");
    let specs: Vec<&str> = parent.get("specs")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
        .unwrap_or_default();
    let priority = parent.get("priority").and_then(|v| v.as_str()).unwrap_or("P1");

    // Print parent info
    writeln!(ctx, "{}: {}", parent_id, title).unwrap();
    if !ac.is_empty() {
        writeln!(ctx, "  AC: {}", ac).unwrap();
    }
    if !specs.is_empty() {
        writeln!(ctx, "  Specs: {}", specs.join(", ")).unwrap();
    }
    writeln!(ctx).unwrap();

    // Read spec files for context (if any)
    let mut spec_context = String::new();
    for spec_path in &specs {
        if let Ok(content) = fs::read_to_string(spec_path) {
            spec_context.push_str(&format!("--- {} ---\n{}\n\n", spec_path, content));
        } else {
            // Try relative to project root
            let full_path = root.join(spec_path);
            if let Ok(content) = fs::read_to_string(&full_path) {
                spec_context.push_str(&format!("--- {} ---\n{}\n\n", spec_path, content));
            }
        }
    }

    // Try LLM decomposition, fall back to manual guidance
    let proposals = match try_llm_decompose(title, description, ac, &spec_context) {
        Ok(p) => p,
        Err(_) => {
            print_manual_guidance(ctx, &parent_id, title, ac, &specs);
            return Ok(());
        }
    };

    if proposals.is_empty() {
        writeln!(ctx, "LLM returned no sub-needle proposals.").unwrap();
        print_manual_guidance(ctx, &parent_id, title, ac, &specs);
        return Ok(());
    }

    // Present proposals for review
    writeln!(ctx, "Proposed sub-needles:").unwrap();
    writeln!(ctx).unwrap();
    for (i, p) in proposals.iter().enumerate() {
        let ac_display = p.ac.as_deref().unwrap_or("(none)");
        writeln!(ctx, "  {}. {} [{}]", i + 1, p.title, p.priority).unwrap();
        writeln!(ctx, "     AC: {}", ac_display).unwrap();
    }
    writeln!(ctx).unwrap();

    // Review gate (skip with --auto)
    if !auto {
        eprint!("Create these sub-needles? [y/n] ");
        let _ = io::stderr().flush();
        let stdin = io::stdin();
        let mut line = String::new();
        if stdin.lock().read_line(&mut line).is_err() {
            return Err("failed to read input".to_string());
        }
        let answer = line.trim().to_lowercase();
        if answer != "y" && answer != "yes" {
            writeln!(ctx, "Aborted.").unwrap();
            return Ok(());
        }
    }

    // Create sub-needles under lock
    let created_ids = write_sub_needles(&root, &parent_id, priority, &proposals)?;

    // Update parent with blocks[]
    update_parent_blocks(&root, &parent_id, &created_ids)?;

    // Audit
    append_audit(
        &root,
        &json!({
            "event": "needle.decomposed",
            "parent": parent_id,
            "children": created_ids,
            "timestamp": now_iso()
        }),
    )?;

    writeln!(ctx).unwrap();
    writeln!(ctx, "Decomposed {} into {} sub-needles:", parent_id, created_ids.len()).unwrap();
    for id in &created_ids {
        writeln!(ctx, "  {}", id).unwrap();
    }

    Ok(())
}

/// Create sub-needles under lock, with proper dependency chains.
///
/// Each sub-needle depends on the parent. Siblings form a sequential chain
/// (second depends on first, third on second, etc.).
///
/// Returns the list of created needle IDs.
pub fn write_sub_needles(
    root: &std::path::Path,
    parent_id: &str,
    parent_priority: &str,
    proposals: &[SubNeedleProposal],
) -> Result<Vec<String>, String> {
    with_needles_locked(root, |needles| {
        let mut created_ids: Vec<String> = Vec::new();

        for (i, proposal) in proposals.iter().enumerate() {
            let id = next_needle_id(root)?;

            // Build depends_on: always depends on parent.
            // Additionally, sequential sibling dependency.
            let mut depends_on = vec![json!(parent_id)];
            if i > 0 {
                depends_on.push(json!(&created_ids[i - 1]));
            }

            let priority = if proposal.priority.is_empty() {
                parent_priority
            } else {
                &proposal.priority
            };

            let mut entry = json!({
                "id": id,
                "title": proposal.title,
                "status": "open",
                "priority": priority,
                "issue_type": "task",
                "depends_on": depends_on,
                "created_at": now_iso(),
                "parent": parent_id
            });

            if let Some(ref ac) = proposal.ac {
                entry["ac"] = json!(ac);
            }

            needles.push(entry);
            created_ids.push(id);
        }

        Ok(created_ids)
    })
}

/// Update the parent needle's blocks[] array to include all child IDs.
pub fn update_parent_blocks(
    root: &std::path::Path,
    parent_id: &str,
    child_ids: &[String],
) -> Result<(), String> {
    with_needles_locked(root, |needles| {
        let parent_idx = needles.iter().position(|n| {
            n.get("id").and_then(|v| v.as_str()) == Some(parent_id)
        }).ok_or_else(|| format!("parent needle '{}' not found for blocks update", parent_id))?;

        let mut blocks = needles[parent_idx]
            .get("blocks")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        for child_id in child_ids {
            let val = json!(child_id);
            if !blocks.contains(&val) {
                blocks.push(val);
            }
        }

        needles[parent_idx]["blocks"] = json!(blocks);
        Ok(())
    })
}

// ---------------------------------------------------------------------------
// LLM decomposition
// ---------------------------------------------------------------------------

/// Try to decompose a needle using an LLM call.
/// Returns a list of sub-needle proposals, or Err if no LLM is available.
fn try_llm_decompose(
    title: &str,
    description: &str,
    ac: &str,
    spec_context: &str,
) -> Result<Vec<SubNeedleProposal>, String> {
    use crate::cpu::anthropic::{ContentBlock, InferenceRequest, Message, StreamEvent};
    use futures_util::StreamExt;

    let driver = crate::cpu::create_driver("claude-haiku-4-5")
        .map_err(|e| format!("no LLM available: {e}"))?;

    let user_prompt = build_decompose_prompt(title, description, ac, spec_context);

    let params = InferenceRequest {
        model: "claude-haiku-4-5".to_string(),
        max_tokens: 2048,
        system: Some(
            "You decompose software engineering tasks into smaller sub-tasks. \
             Output ONLY a JSON array of objects, each with: \
             \"title\" (verb + target pattern, concise), \
             \"ac\" (shell command to verify completion), \
             \"priority\" (P0/P1/P2). \
             Keep it to 2-5 sub-tasks. No markdown fences, no explanation."
                .to_string(),
        ),
        messages: vec![Message {
            role: "user".into(),
            content: vec![ContentBlock::Text { text: user_prompt }],
            model: None,
        }],
        tools: vec![],
        tool_choice: None,
        stream: true,
        claude: Default::default(),
    };

    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| format!("runtime: {e}"))?;

    let result = rt.block_on(async {
        let mut stream = driver.create_stream(params).await?;
        let mut text = String::new();
        while let Some(event) = stream.next().await {
            match event? {
                StreamEvent::TextDelta(delta) => text.push_str(&delta),
                StreamEvent::MessageStop { .. } => break,
                _ => {}
            }
        }
        Ok::<String, String>(text)
    })?;

    // Parse JSON array from response
    let json_str = extract_json_array(&result);
    let arr: Vec<Value> = serde_json::from_str(json_str)
        .map_err(|e| format!("failed to parse LLM response as JSON array: {e}\nRaw: {result}"))?;

    let proposals: Vec<SubNeedleProposal> = arr.iter().map(|v| {
        SubNeedleProposal {
            title: v.get("title").and_then(|t| t.as_str()).unwrap_or("untitled").to_string(),
            ac: v.get("ac").and_then(|a| a.as_str()).map(|s| s.to_string()),
            priority: v.get("priority").and_then(|p| p.as_str()).unwrap_or("P0").to_string(),
        }
    }).collect();

    Ok(proposals)
}

/// Build the decomposition prompt for the LLM.
fn build_decompose_prompt(title: &str, description: &str, ac: &str, spec_context: &str) -> String {
    let mut prompt = "Decompose this needle into 2-5 smaller sub-needles:\n\n".to_string();
    prompt.push_str(&format!("Title: {}\n", title));
    if !description.is_empty() {
        prompt.push_str(&format!("Description: {}\n", description));
    }
    if !ac.is_empty() {
        prompt.push_str(&format!("Acceptance criteria: {}\n", ac));
    }
    if !spec_context.is_empty() {
        prompt.push_str(&format!("\nSpec context:\n{}\n", spec_context));
    }
    prompt.push_str("\nEach sub-needle should:\n");
    prompt.push_str("- Have a title using verb + target pattern (e.g. \"implement X\", \"write tests for Y\")\n");
    prompt.push_str("- Have an AC that is a shell command to verify completion\n");
    prompt.push_str("- Be P0 priority (immediate work)\n");
    prompt.push_str("\nOutput ONLY a JSON array.");
    prompt
}

/// Extract a JSON array from a response that might contain markdown fences.
fn extract_json_array(s: &str) -> &str {
    let trimmed = s.trim();
    if let Some(start) = trimmed.find('[')
        && let Some(end) = trimmed.rfind(']')
    {
        return &trimmed[start..=end];
    }
    trimmed
}

// ---------------------------------------------------------------------------
// Manual fallback guidance
// ---------------------------------------------------------------------------

/// Print guidance for manual decomposition when no LLM is available.
fn print_manual_guidance(w: &mut VerbCtx, parent_id: &str, title: &str, ac: &str, specs: &[&str]) {
    writeln!(w, "Manual decomposition needed (no LLM available).").unwrap();
    writeln!(w, "Suggested pattern:").unwrap();

    // Generate example sub-needle commands
    let example_title_1 = format!("implement {}", extract_verb_target(title));
    let example_title_2 = format!("write e2e test for {}", extract_verb_target(title));
    let example_ac = if ac.is_empty() {
        "echo 'verify manually'".to_string()
    } else {
        ac.to_string()
    };

    writeln!(w,
        "  ostk needle add \"{}\" --ac \"{}\" --depends-on {} --priority P0",
        example_title_1, example_ac, parent_id
    ).unwrap();
    writeln!(w,
        "  ostk needle add \"{}\" --ac \"cargo test\" --depends-on <prev_id> --priority P0",
        example_title_2
    ).unwrap();

    if !specs.is_empty() {
        writeln!(w).unwrap();
        writeln!(w, "Referenced specs for context:").unwrap();
        for spec in specs {
            writeln!(w, "  {}", spec).unwrap();
        }
    }
}

/// Extract a short verb+target from a needle title for example commands.
fn extract_verb_target(title: &str) -> String {
    crate::util::safe_truncate(title, 40)
}

// ---------------------------------------------------------------------------
// Unified entry point — routes spec vs needle
// ---------------------------------------------------------------------------

/// Main entry point for `ostk doc decompose` / `ostk decompose`.
/// Detects whether the argument is a needle ID or spec path.
pub fn run(path: &str, auto: bool) -> Result<(), String> {
    let root = find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, path, auto)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation for :decompose (→1157).
pub fn run_verb(ctx: &mut VerbCtx, path: &str, auto: bool) -> Result<(), String> {
    if is_needle_id(path) {
        run_needle_verb(ctx, path, auto)
    } else {
        run_spec_verb(ctx, path)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_needle_id_arrow() {
        assert!(is_needle_id("\u{2192}843"));
        assert!(is_needle_id("\u{2192}001"));
    }

    #[test]
    fn is_needle_id_ascii_arrow() {
        assert!(is_needle_id("->843"));
        assert!(is_needle_id("->1"));
    }

    #[test]
    fn is_needle_id_bare_number() {
        assert!(is_needle_id("843"));
        assert!(is_needle_id("1"));
    }

    #[test]
    fn is_needle_id_spec_path() {
        assert!(!is_needle_id("docs/spec/foo.md"));
        assert!(!is_needle_id("ostk-compile.md"));
        assert!(!is_needle_id("./some-path"));
    }

    #[test]
    fn normalize_needle_id_arrow() {
        assert_eq!(normalize_needle_id("\u{2192}843"), "\u{2192}843");
    }

    #[test]
    fn normalize_needle_id_ascii_arrow() {
        assert_eq!(normalize_needle_id("->843"), "\u{2192}843");
    }

    #[test]
    fn normalize_needle_id_bare_number() {
        assert_eq!(normalize_needle_id("843"), "\u{2192}843");
    }

    #[test]
    fn extract_json_array_plain() {
        let input = r#"[{"title": "test", "ac": "echo ok", "priority": "P0"}]"#;
        assert_eq!(extract_json_array(input), input);
    }

    #[test]
    fn extract_json_array_with_fences() {
        let input = "```json\n[{\"title\": \"test\"}]\n```";
        assert_eq!(extract_json_array(input), "[{\"title\": \"test\"}]");
    }

    #[test]
    fn extract_json_array_with_leading_text() {
        let input = "Here are the sub-tasks:\n[{\"title\": \"test\"}]";
        assert_eq!(extract_json_array(input), "[{\"title\": \"test\"}]");
    }

    #[test]
    fn build_decompose_prompt_includes_title() {
        let prompt = build_decompose_prompt("implement auth", "", "", "");
        assert!(prompt.contains("implement auth"));
    }

    #[test]
    fn build_decompose_prompt_includes_description() {
        let prompt = build_decompose_prompt("title", "a longer description", "", "");
        assert!(prompt.contains("a longer description"));
    }

    #[test]
    fn build_decompose_prompt_includes_ac() {
        let prompt = build_decompose_prompt("title", "", "cargo test --test auth", "");
        assert!(prompt.contains("cargo test --test auth"));
    }

    #[test]
    fn build_decompose_prompt_includes_spec_context() {
        let prompt = build_decompose_prompt("title", "", "", "spec content here");
        assert!(prompt.contains("spec content here"));
    }
}
