use std::collections::HashSet;
use std::fmt::Write;
use std::fs;
use std::process::Command;
use serde_json::Value;
use crate::kernel::verb_ctx::VerbCtx;

/// ostk search — recursive content search routing through the kernel's output compression.
///
/// Basic mode: ripgrep-style recursive search (delegates to `rg` or `grep -r`).
/// --path: limit scope to a subtree.
/// --semantic: uses the index.json search_index + LLM to find relevant drafts and hay.
///
/// Output format: filename:line:content, with compressed (dedup-friendly) headers.
///
/// CLI entry point.
pub fn run(query: &str, path: Option<&str>, semantic: bool) -> Result<(), String> {
    let root = crate::find_project_root()?;
    let input = serde_json::json!({});
    let mut ctx = VerbCtx::new(&root, &input);
    run_verb(&mut ctx, query, path, semantic)?;
    print!("{}", ctx.into_output());
    Ok(())
}

/// Verb implementation — writes to VerbCtx (→1157).
pub fn run_verb(ctx: &mut VerbCtx, query: &str, path: Option<&str>, semantic: bool) -> Result<(), String> {
    if semantic {
        return run_semantic(ctx, query);
    }

    let search_root = path.unwrap_or(".");

    // Prefer rg (ripgrep) — faster, respects .gitignore, agent-friendly output.
    // Fall back to grep -r for environments without rg.
    let result = if which_rg() {
        run_rg(query, search_root)
    } else {
        run_grep(query, search_root)
    };

    match result {
        Ok(output) => {
            if output.is_empty() {
                eprintln!("search: no matches for {:?}", query);
            } else {
                print_compressed(ctx, &output);
            }
            Ok(())
        }
        Err(e) => Err(format!("search failed: {}", e)),
    }
}

/// Semantic search using the pre-built index.json and an LLM call.
fn run_semantic(ctx: &mut VerbCtx, query: &str) -> Result<(), String> {
    let index_path = ctx.ostk_dir().join("index.json");
    if !index_path.exists() {
        return Err("index.json not found. Run 'ostk index' first.".to_string());
    }

    let content = fs::read_to_string(&index_path).map_err(|e| e.to_string())?;
    let index: Value = serde_json::from_str(&content).map_err(|e| e.to_string())?;

    let search_index = index.get("search_index")
        .ok_or_else(|| "search_index not found in index.json. Run 'ostk index'.".to_string())?;

    let hay = search_index.get("hay").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let drafts = search_index.get("drafts").and_then(|v| v.as_array()).cloned().unwrap_or_default();

    writeln!(ctx, "searching semantic index ({} hay, {} drafts)...", hay.len(), drafts.len()).unwrap();

    // Prepare a concise list of items for the LLM
    let mut items = Vec::new();
    for h in hay {
        let straw = h.get("straw").and_then(|v| v.as_str()).unwrap_or("");
        items.push(format!("hay: {}", straw));
    }
    for d in drafts {
        let title = d.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let path = d.get("path").and_then(|v| v.as_str()).unwrap_or("");
        let snippet = d.get("snippet").and_then(|v| v.as_str()).unwrap_or("");
        items.push(format!("draft: {} ({}) - {}", title, path, snippet));
    }

    let items_list = items.join("\n");
    let prompt = format!(
        "Given the search query '{}' and the following list of hay and drafts, \
         return the most relevant items. Be concise. Format as a list with types and paths/text.\n\n{}",
        query, items_list
    );

    // Call LLM via Ask logic
    crate::commands::ask::ask(&prompt, 1024)
}

/// Check whether `rg` (ripgrep) is available on PATH.
pub(crate) fn which_rg() -> bool {
    Command::new("which")
        .arg("rg")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Run ripgrep: filename:line:content format, respect .gitignore, follow symlinks.
pub(crate) fn run_rg(query: &str, root: &str) -> Result<String, String> {
    let output = Command::new("rg")
        .args([
            "--line-number",     // include line numbers
            "--no-heading",      // flat filename:line:content (not grouped)
            "--color=never",     // no ANSI — kernel compression reads clean text
            "--follow",          // follow symlinks
            query,
            root,
        ])
        .output()
        .map_err(|e| format!("rg: {}", e))?;

    // rg exits 1 when no matches found — that's not an error.
    if !output.status.success() && output.status.code() != Some(1) {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("rg exited {:?}: {}", output.status.code(), stderr));
    }

    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Fallback: POSIX grep -rn — available everywhere.
pub(crate) fn run_grep(query: &str, root: &str) -> Result<String, String> {
    let output = Command::new("grep")
        .args([
            "-r",            // recursive
            "-n",            // line numbers
            "--color=never", // no ANSI
            query,
            root,
        ])
        .output()
        .map_err(|e| format!("grep: {}", e))?;

    // grep exits 1 when no matches — not an error.
    if !output.status.success() && output.status.code() != Some(1) {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "grep exited {:?}: {}",
            output.status.code(),
            stderr
        ));
    }

    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Print search results with compressed filename headers.
fn print_compressed(w: &mut VerbCtx, raw: &str) {
    let mut seen_files: HashSet<String> = HashSet::new();
    let mut current_file = String::new();

    for line in raw.lines() {
        let (file, rest) = match split_first_colon(line) {
            Some(pair) => pair,
            None => {
                writeln!(w, "{}", line).unwrap();
                continue;
            }
        };

        let file = file.trim_start_matches("./");

        if file != current_file {
            if !seen_files.contains(file) {
                writeln!(w, "-- {} --", file).unwrap();
                seen_files.insert(file.to_string());
            }
            current_file = file.to_string();
        }

        writeln!(w, "  {}", rest).unwrap();
    }
}

/// Split "path/to/file.rs:42:content" into ("path/to/file.rs", "42:content").
fn split_first_colon(line: &str) -> Option<(&str, &str)> {
    let mut start = 0;
    let bytes = line.as_bytes();

    loop {
        let pos = memchr(b':', bytes, start)?;
        let segment = &line[..pos];
        if segment.len() > 1 || segment.contains('/') || segment.contains('\\') {
            let rest = &line[pos + 1..];
            return Some((segment, rest));
        }
        start = pos + 1;
        if start >= line.len() {
            return None;
        }
    }
}

/// Minimal memchr: find the next occurrence of `needle` in `haystack` starting at `from`.
fn memchr(needle: u8, haystack: &[u8], from: usize) -> Option<usize> {
    haystack[from..].iter().position(|&b| b == needle).map(|p| p + from)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_split_first_colon_normal() {
        let line = "src/main.rs:42:fn main() {";
        let (file, rest) = split_first_colon(line).unwrap();
        assert_eq!(file, "src/main.rs");
        assert_eq!(rest, "42:fn main() {");
    }

    #[test]
    fn test_memchr_found() {
        let data = b"hello:world";
        assert_eq!(memchr(b':', data, 0), Some(5));
    }
}
