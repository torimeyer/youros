use crate::{append_audit, find_project_root, now_iso};
use serde_json::json;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Key files to look for when building the index.
const KEY_FILES: &[(&str, &str)] = &[
    ("readme", "README.md"),
    ("claude_md", "CLAUDE.md"),
];

/// Config files to detect.
const CONFIG_FILES: &[&str] = &[
    "Cargo.toml",
    "package.json",
    "tsconfig.json",
    "pyproject.toml",
    "setup.py",
    "go.mod",
    "Makefile",
    "CMakeLists.txt",
    "build.gradle",
    "pom.xml",
];

/// Source directories to detect.
const SOURCE_DIRS: &[&str] = &["src/", "lib/", "pkg/", "cmd/", "internal/", "app/"];

/// Doc directories to detect.
const DOC_DIRS: &[&str] = &["docs/", "doc/", "documentation/"];

/// File extension → language mapping.
const LANG_EXTENSIONS: &[(&str, &str)] = &[
    ("rs", "rust"),
    ("py", "python"),
    ("ts", "typescript"),
    ("tsx", "typescript"),
    ("js", "javascript"),
    ("jsx", "javascript"),
    ("go", "go"),
    ("java", "java"),
    ("c", "c"),
    ("cpp", "cpp"),
    ("h", "c"),
    ("hpp", "cpp"),
    ("rb", "ruby"),
    ("swift", "swift"),
    ("kt", "kotlin"),
    ("cs", "csharp"),
    ("json", "json"),
    ("toml", "toml"),
    ("yaml", "yaml"),
    ("yml", "yaml"),
    ("md", "markdown"),
    ("sh", "shell"),
    ("bash", "shell"),
    ("zsh", "shell"),
];

pub fn run(target: &str, alias: Option<&str>) -> Result<(), String> {
    // 1. Parse target: org/repo
    let (org, repo) = parse_target(target)?;

    // 2. Find project root
    let root = find_project_root()?;

    // 3. Create import directory
    let import_dir = crate::state_dir(&root).join("imports").join(&org).join(&repo);
    fs::create_dir_all(&import_dir)
        .map_err(|e| format!("failed to create import dir: {e}"))?;

    let src_dir = import_dir.join("src");

    // 4. Shallow clone (skip if already cloned)
    if src_dir.exists() {
        // Pull latest instead of re-cloning
        println!("updating existing import {org}/{repo}...");
        let status = Command::new("git")
            .args(["-C", &src_dir.to_string_lossy(), "pull", "--depth", "1", "--rebase"])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .status()
            .map_err(|e| format!("git pull failed: {e}"))?;
        if !status.success() {
            return Err(format!("git pull failed for {org}/{repo}"));
        }
    } else {
        println!("cloning {org}/{repo}...");
        let url = format!("https://github.com/{org}/{repo}.git");
        let status = Command::new("git")
            .args([
                "clone",
                "--depth",
                "1",
                &url,
                &src_dir.to_string_lossy(),
            ])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .status()
            .map_err(|e| format!("git clone failed: {e}"))?;
        if !status.success() {
            return Err(format!(
                "git clone failed for {url} — check that the repository exists and is public"
            ));
        }
    }

    // 5. Determine alias
    let alias_str = match alias {
        Some(a) => {
            if a.starts_with('@') {
                a.to_string()
            } else {
                format!("@{a}")
            }
        }
        None => format!("@{repo}"),
    };

    // 6. Write alias file
    let alias_path = import_dir.join("alias");
    fs::write(&alias_path, &alias_str)
        .map_err(|e| format!("failed to write alias file: {e}"))?;

    // 7. Generate index
    let index = generate_index(&src_dir, &org, &repo, &alias_str)?;
    let index_path = import_dir.join("index.json");
    let index_json = serde_json::to_string_pretty(&index)
        .map_err(|e| format!("failed to serialize index: {e}"))?;
    fs::write(&index_path, &index_json)
        .map_err(|e| format!("failed to write index.json: {e}"))?;

    // 8. Audit trail
    let now = now_iso();
    append_audit(
        &root,
        &json!({
            "event": "import.created",
            "org": org,
            "repo": repo,
            "alias": alias_str,
            "timestamp": now
        }),
    )?;

    // 9. Report
    let total = index
        .get("stats")
        .and_then(|s| s.get("total_files"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    println!("imported {org}/{repo} as {alias_str} ({total} files)");
    println!("  index: {}", index_path.display());

    Ok(())
}

/// Import the current directory as a local project.
/// Scans the repo structure, git history, and generates an index.
/// Used by `ostk init --import` when already inside a repo.
pub fn run_local() -> Result<(), String> {
    let cwd = std::env::current_dir().map_err(|e| format!("cwd: {e}"))?;
    let root = find_project_root()?;
    let ostk_dir = crate::state_dir(&root);

    // Derive org/repo from git remote or directory name
    let (org, repo) = detect_org_repo(&cwd);

    // Generate index from current directory (no clone needed)
    let index = generate_index(&cwd, &org, &repo, &format!("@{repo}"))?;

    // Write index to .ostk/
    let index_path = ostk_dir.join("project-index.json");
    let index_json = serde_json::to_string_pretty(&index)
        .map_err(|e| format!("serialize: {e}"))?;
    fs::write(&index_path, &index_json)
        .map_err(|e| format!("write index: {e}"))?;

    // Scan git log for recent activity → file as hay
    let hay_count = import_git_history(&root)?;

    // Audit
    append_audit(&root, &json!({
        "event": "import.local",
        "org": org,
        "repo": repo,
        "timestamp": now_iso(),
    }))?;

    // Report
    let total = index["stats"]["total_files"].as_u64().unwrap_or(0);
    let langs = index["stats"]["languages"].as_object()
        .map(|m| m.keys().cloned().collect::<Vec<_>>().join(", "))
        .unwrap_or_default();
    println!("  \x1b[32m✓\x1b[0m imported {org}/{repo} ({total} files, {langs})");
    if hay_count > 0 {
        println!("  \x1b[32m✓\x1b[0m {hay_count} recent commits → hay pile");
    }

    Ok(())
}

/// Detect org/repo from git remote origin, fallback to directory name.
fn detect_org_repo(dir: &Path) -> (String, String) {
    if let Ok(output) = Command::new("git")
        .args(["-C", &dir.to_string_lossy(), "remote", "get-url", "origin"])
        .output()
    {
        let url = String::from_utf8_lossy(&output.stdout).trim().to_string();
        // Parse github.com:org/repo.git or https://github.com/org/repo.git
        if let Some(parts) = parse_git_url(&url) {
            return parts;
        }
    }
    // Fallback: use directory name
    let name = dir.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "project".to_string());
    ("local".to_string(), name)
}

fn parse_git_url(url: &str) -> Option<(String, String)> {
    // SSH: git@github.com:org/repo.git
    if let Some(rest) = url.strip_prefix("git@github.com:") {
        let rest = rest.trim_end_matches(".git");
        let parts: Vec<&str> = rest.split('/').collect();
        if parts.len() == 2 {
            return Some((parts[0].to_string(), parts[1].to_string()));
        }
    }
    // HTTPS: https://github.com/org/repo.git
    if url.contains("github.com/") {
        let after = url.split("github.com/").nth(1)?;
        let after = after.trim_end_matches(".git");
        let parts: Vec<&str> = after.split('/').collect();
        if parts.len() >= 2 {
            return Some((parts[0].to_string(), parts[1].to_string()));
        }
    }
    None
}

/// Import recent git commits as hay entries.
fn import_git_history(_root: &Path) -> Result<usize, String> {
    let output = Command::new("git")
        .args(["log", "--oneline", "-20", "--no-merges"])
        .output()
        .map_err(|e| format!("git log: {e}"))?;

    if !output.status.success() {
        return Ok(0);
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let mut count = 0;
    for line in stdout.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        // File as hay — raw thinking from git history
        crate::commands::work::run_hay(line, "git-import")?;
        count += 1;
    }

    Ok(count)
}

/// Parse "org/repo" into (org, repo). Rejects malformed targets.
fn parse_target(target: &str) -> Result<(String, String), String> {
    let parts: Vec<&str> = target.split('/').collect();
    if parts.len() != 2 || parts[0].is_empty() || parts[1].is_empty() {
        return Err(format!(
            "invalid target '{target}': expected org/repo (e.g., anthropics/claude-code)"
        ));
    }
    Ok((parts[0].to_string(), parts[1].to_string()))
}

/// Scan the cloned repo and build the index JSON.
fn generate_index(
    src_dir: &Path,
    org: &str,
    repo: &str,
    alias: &str,
) -> Result<serde_json::Value, String> {
    let now = now_iso();

    // Find key files
    let mut files = serde_json::Map::new();

    for &(key, filename) in KEY_FILES {
        let path = src_dir.join(filename);
        if path.exists() {
            files.insert(
                key.to_string(),
                json!(format!("src/{filename}")),
            );
        }
    }

    // Find config files
    let mut configs: Vec<String> = Vec::new();
    for &filename in CONFIG_FILES {
        let path = src_dir.join(filename);
        if path.exists() {
            configs.push(format!("src/{filename}"));
        }
    }
    if !configs.is_empty() {
        files.insert("config".to_string(), json!(configs));
    }

    // Find source directories
    let mut source_dirs: Vec<String> = Vec::new();
    for &dirname in SOURCE_DIRS {
        let path = src_dir.join(dirname);
        if path.is_dir() {
            source_dirs.push(format!("src/{dirname}"));
        }
    }
    if !source_dirs.is_empty() {
        files.insert("source_dirs".to_string(), json!(source_dirs));
    }

    // Find doc directories
    let mut doc_dirs: Vec<String> = Vec::new();
    for &dirname in DOC_DIRS {
        let path = src_dir.join(dirname);
        if path.is_dir() {
            doc_dirs.push(format!("src/{dirname}"));
        }
    }
    if !doc_dirs.is_empty() {
        files.insert("docs".to_string(), json!(doc_dirs));
    }

    // Walk tree for stats
    let (total_files, languages) = walk_stats(src_dir);

    Ok(json!({
        "org": org,
        "repo": repo,
        "alias": alias,
        "imported_at": now,
        "files": files,
        "stats": {
            "total_files": total_files,
            "languages": languages
        }
    }))
}

/// Walk the source tree, counting files and language distribution.
/// Skips .git directory.
fn walk_stats(dir: &Path) -> (u64, HashMap<String, u64>) {
    let mut total: u64 = 0;
    let mut languages: HashMap<String, u64> = HashMap::new();

    let lang_map: HashMap<&str, &str> = LANG_EXTENSIONS.iter().copied().collect();

    fn walk_recursive(
        dir: &Path,
        total: &mut u64,
        languages: &mut HashMap<String, u64>,
        lang_map: &HashMap<&str, &str>,
    ) {
        let entries = match fs::read_dir(dir) {
            Ok(e) => e,
            Err(_) => return,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name();
            let name_str = name.to_string_lossy();

            // Skip .git
            if name_str == ".git" {
                continue;
            }

            if path.is_dir() {
                walk_recursive(&path, total, languages, lang_map);
            } else if path.is_file() {
                *total += 1;
                if let Some(ext) = path.extension().and_then(|e| e.to_str())
                    && let Some(&lang) = lang_map.get(ext) {
                        *languages.entry(lang.to_string()).or_insert(0) += 1;
                    }
            }
        }
    }

    walk_recursive(dir, &mut total, &mut languages, &lang_map);
    (total, languages)
}

/// Resolve an alias (e.g., "@claude-code") to the import directory.
///
/// Scans `.ostk/imports/*/alias` files. Returns the import directory
/// if the alias matches.
pub fn resolve_alias(root: &Path, alias: &str) -> Option<PathBuf> {
    let imports_dir = crate::state_dir(root).join("imports");
    if !imports_dir.is_dir() {
        return None;
    }

    // Walk org dirs
    let org_dirs = fs::read_dir(&imports_dir).ok()?;
    for org_entry in org_dirs.flatten() {
        if !org_entry.path().is_dir() {
            continue;
        }
        let repo_dirs = fs::read_dir(org_entry.path()).ok()?;
        for repo_entry in repo_dirs.flatten() {
            let import_dir = repo_entry.path();
            if !import_dir.is_dir() {
                continue;
            }
            let alias_path = import_dir.join("alias");
            if let Ok(content) = fs::read_to_string(&alias_path)
                && content.trim() == alias {
                    return Some(import_dir);
                }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_target_valid() {
        let (org, repo) = parse_target("anthropics/claude-code").unwrap();
        assert_eq!(org, "anthropics");
        assert_eq!(repo, "claude-code");
    }

    #[test]
    fn test_parse_target_invalid_no_slash() {
        assert!(parse_target("claude-code").is_err());
    }

    #[test]
    fn test_parse_target_invalid_empty_parts() {
        assert!(parse_target("/claude-code").is_err());
        assert!(parse_target("anthropics/").is_err());
    }

    #[test]
    fn test_parse_target_invalid_too_many_slashes() {
        assert!(parse_target("a/b/c").is_err());
    }

    #[test]
    fn test_resolve_alias_missing_imports_dir() {
        let tmp = std::env::temp_dir().join("ostk_test_import_resolve");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();
        assert!(resolve_alias(&tmp, "@foo").is_none());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_resolve_alias_found() {
        let tmp = std::env::temp_dir().join("ostk_test_import_alias");
        let _ = fs::remove_dir_all(&tmp);
        let import_dir = tmp.join(".ostk/imports/org/repo");
        fs::create_dir_all(&import_dir).unwrap();
        fs::write(import_dir.join("alias"), "@repo").unwrap();

        let result = resolve_alias(&tmp, "@repo");
        assert!(result.is_some());
        assert_eq!(result.unwrap(), import_dir);

        // Non-matching alias
        assert!(resolve_alias(&tmp, "@other").is_none());

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_generate_index_empty_dir() {
        let tmp = std::env::temp_dir().join("ostk_test_import_index");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        let index = generate_index(&tmp, "org", "repo", "@repo").unwrap();
        assert_eq!(index["org"], "org");
        assert_eq!(index["repo"], "repo");
        assert_eq!(index["alias"], "@repo");
        assert_eq!(index["stats"]["total_files"], 0);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_generate_index_with_files() {
        let tmp = std::env::temp_dir().join("ostk_test_import_index_files");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        // Create some files
        fs::write(tmp.join("README.md"), "# Test").unwrap();
        fs::write(tmp.join("Cargo.toml"), "[package]").unwrap();
        fs::create_dir_all(tmp.join("src")).unwrap();
        fs::write(tmp.join("src/main.rs"), "fn main() {}").unwrap();
        fs::write(tmp.join("src/lib.rs"), "pub fn hello() {}").unwrap();

        let index = generate_index(&tmp, "org", "repo", "@repo").unwrap();
        assert_eq!(index["files"]["readme"], "src/README.md");
        assert!(index["files"]["config"].as_array().unwrap().contains(&json!("src/Cargo.toml")));
        assert!(index["files"]["source_dirs"].as_array().unwrap().contains(&json!("src/src/")));
        assert_eq!(index["stats"]["total_files"], 4); // README.md, Cargo.toml, main.rs, lib.rs
        assert_eq!(index["stats"]["languages"]["rust"], 2);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_walk_stats_skips_git() {
        let tmp = std::env::temp_dir().join("ostk_test_import_walk");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(tmp.join(".git/objects")).unwrap();
        fs::write(tmp.join(".git/HEAD"), "ref: refs/heads/main").unwrap();
        fs::write(tmp.join("main.rs"), "fn main() {}").unwrap();

        let (total, languages) = walk_stats(&tmp);
        assert_eq!(total, 1); // only main.rs, not .git/HEAD
        assert_eq!(languages.get("rust"), Some(&1));

        let _ = fs::remove_dir_all(&tmp);
    }
}
