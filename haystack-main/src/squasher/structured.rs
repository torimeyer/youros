//! Structured handler for the squasher pipeline.
//!
//! Commands like git status, docker ps, kubectl get produce structured output
//! that can be parsed into a more useful summary. Unrecognized commands fall
//! back to passthrough behavior.
//!
//! Ported from mish's `handlers/structured.rs` — pipeline-callable formatting only
//! (no process execution, no flag injection).

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Format output from a structured command.
///
/// Parses the command to determine which structured formatter to use:
/// - `git status`: parse into sections (staged, modified, untracked) with counts
/// - `docker ps`: extract container name, status, ports
/// - `kubectl get`: preserve table format, add summary line
/// - Fallback: treat as passthrough (output unchanged + footer)
///
/// # Examples
///
/// ```
/// use ostk::squasher::structured::format_structured;
///
/// let output = "On branch main\nChanges not staged for commit:\n  modified: src/main.rs\n";
/// let result = format_structured("git status", output);
/// assert!(result.contains("git status"));
/// ```
pub fn format_structured(cmd: &str, output: &str) -> String {
    let trimmed = cmd.trim();
    let tokens: Vec<&str> = trimmed.split_whitespace().collect();

    if tokens.is_empty() {
        return output.to_string();
    }

    // Extract command name (handle path-qualified commands)
    let cmd_name = tokens[0].rsplit('/').next().unwrap_or(tokens[0]);
    let subcommand = tokens.get(1).copied().unwrap_or("");

    match (cmd_name, subcommand) {
        ("git", "status") => format_git_status(trimmed, output),
        ("docker", "ps") => format_docker_ps(trimmed, output),
        ("kubectl" | "k", "get") => format_kubectl_get(trimmed, output),
        _ => format_generic_structured(trimmed, output),
    }
}

// ---------------------------------------------------------------------------
// git status formatter
// ---------------------------------------------------------------------------

fn format_git_status(cmd: &str, output: &str) -> String {
    let mut branch = String::new();
    let mut staged: Vec<String> = Vec::new();
    let mut modified: Vec<String> = Vec::new();
    let mut untracked: Vec<String> = Vec::new();
    let mut deleted: Vec<String> = Vec::new();

    // Try porcelain v2 first (starts with # lines)
    let is_porcelain = output.lines().any(|l| l.starts_with("# branch.head"));

    if is_porcelain {
        parse_porcelain_v2(output, &mut branch, &mut staged, &mut modified, &mut untracked, &mut deleted);
    } else {
        parse_human_git_status(output, &mut branch, &mut staged, &mut modified, &mut untracked, &mut deleted);
    }

    // Build summary
    let mut parts: Vec<String> = Vec::new();

    if !branch.is_empty() {
        parts.push(format!("branch: {branch}"));
    }

    if !staged.is_empty() {
        parts.push(format!("staged: {}", staged.len()));
    }
    if !modified.is_empty() {
        parts.push(format!("modified: {}", modified.len()));
    }
    if !deleted.is_empty() {
        parts.push(format!("deleted: {}", deleted.len()));
    }
    if !untracked.is_empty() {
        parts.push(format!("untracked: {}", untracked.len()));
    }

    let total = staged.len() + modified.len() + deleted.len() + untracked.len();

    if total == 0 && !branch.is_empty() {
        return format!("[structured] {cmd}\n  branch: {branch} (clean)");
    }

    if parts.is_empty() {
        // Could not parse, fall back to generic
        return format_generic_structured(cmd, output);
    }

    let summary = parts.join(", ");
    format!("[structured] {cmd}\n  {summary}")
}

fn parse_porcelain_v2(
    output: &str,
    branch: &mut String,
    staged: &mut Vec<String>,
    modified: &mut Vec<String>,
    untracked: &mut Vec<String>,
    deleted: &mut Vec<String>,
) {
    for line in output.lines() {
        if line.starts_with("# branch.head ") {
            *branch = line.strip_prefix("# branch.head ").unwrap_or("").to_string();
        } else if line.starts_with('?') {
            let path = line[2..].trim().to_string();
            untracked.push(path);
        } else if line.starts_with("1 ") || line.starts_with("2 ") {
            let chars: Vec<char> = line.chars().collect();
            if chars.len() >= 4 {
                let x = chars[2]; // index status
                let y = chars[3]; // worktree status

                // Extract path (last field)
                let path = line.split_whitespace().last().unwrap_or("?").to_string();

                match x {
                    'A' => staged.push(path.clone()),
                    'D' => deleted.push(path.clone()),
                    'M' => staged.push(path.clone()),
                    _ => {}
                }

                match y {
                    'M' => {
                        if x == '.' {
                            modified.push(path);
                        }
                    }
                    'D' => {
                        if x == '.' {
                            deleted.push(path);
                        }
                    }
                    _ => {}
                }
            }
        }
    }
}

fn parse_human_git_status(
    output: &str,
    branch: &mut String,
    staged: &mut Vec<String>,
    modified: &mut Vec<String>,
    untracked: &mut Vec<String>,
    deleted: &mut Vec<String>,
) {
    let mut section = Section::None;

    for line in output.lines() {
        let trimmed = line.trim();

        // Branch detection
        if trimmed.starts_with("On branch ") {
            *branch = trimmed.strip_prefix("On branch ").unwrap_or("").to_string();
            continue;
        }

        // Section headers
        if trimmed.starts_with("Changes to be committed:") {
            section = Section::Staged;
            continue;
        }
        if trimmed.starts_with("Changes not staged for commit:") {
            section = Section::Modified;
            continue;
        }
        if trimmed.starts_with("Untracked files:") {
            section = Section::Untracked;
            continue;
        }

        // Skip hint lines
        if trimmed.starts_with("(use ") || trimmed.is_empty() {
            continue;
        }

        match section {
            Section::Staged => {
                if trimmed.starts_with("modified:") || trimmed.starts_with("new file:")
                    || trimmed.starts_with("deleted:") || trimmed.starts_with("renamed:")
                {
                    let file = trimmed.split(':').nth(1).unwrap_or("").trim().to_string();
                    if trimmed.starts_with("deleted:") {
                        deleted.push(file);
                    } else {
                        staged.push(file);
                    }
                }
            }
            Section::Modified => {
                if trimmed.starts_with("modified:") {
                    let file = trimmed.split(':').nth(1).unwrap_or("").trim().to_string();
                    modified.push(file);
                } else if trimmed.starts_with("deleted:") {
                    let file = trimmed.split(':').nth(1).unwrap_or("").trim().to_string();
                    deleted.push(file);
                }
            }
            Section::Untracked => {
                if !trimmed.is_empty() {
                    untracked.push(trimmed.to_string());
                }
            }
            Section::None => {}
        }
    }
}

#[derive(Debug, Clone, Copy)]
enum Section {
    None,
    Staged,
    Modified,
    Untracked,
}

// ---------------------------------------------------------------------------
// docker ps formatter
// ---------------------------------------------------------------------------

fn format_docker_ps(cmd: &str, output: &str) -> String {
    // Try to parse as JSON lines (from --format json)
    let containers = parse_docker_json(output);
    if !containers.is_empty() {
        let mut lines: Vec<String> = Vec::new();
        lines.push(format!("[structured] {cmd}"));

        for c in &containers {
            lines.push(format!("  {} ({}) [{}] {}", c.name, c.image, c.status, c.ports));
        }

        lines.push(format!("  ({} containers)", containers.len()));
        return lines.join("\n");
    }

    // Try to parse table output (docker ps default)
    let table_lines: Vec<&str> = output.lines().collect();
    if table_lines.len() > 1 {
        // Table has header + data rows
        let data_count = table_lines.len() - 1;
        let mut result = output.to_string();
        if !result.ends_with('\n') {
            result.push('\n');
        }
        result.push_str(&format!("[structured] {cmd} ({data_count} containers)"));
        return result;
    }

    format_generic_structured(cmd, output)
}

struct DockerInfo {
    name: String,
    image: String,
    status: String,
    ports: String,
}

fn parse_docker_json(output: &str) -> Vec<DockerInfo> {
    let mut containers = Vec::new();
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        // Simple JSON key extraction (avoid serde dependency)
        if trimmed.starts_with('{') && trimmed.ends_with('}') {
            let name = extract_json_field(trimmed, "Names")
                .or_else(|| extract_json_field(trimmed, "Name"))
                .unwrap_or_default();
            let image = extract_json_field(trimmed, "Image").unwrap_or_default();
            let status = extract_json_field(trimmed, "Status").unwrap_or_default();
            let ports = extract_json_field(trimmed, "Ports").unwrap_or_default();

            if !name.is_empty() || !image.is_empty() {
                containers.push(DockerInfo { name, image, status, ports });
            }
        }
    }
    containers
}

/// Simple JSON field extraction — no serde dependency.
fn extract_json_field(json: &str, key: &str) -> Option<String> {
    let pattern = format!("\"{}\":\"", key);
    if let Some(start) = json.find(&pattern) {
        let value_start = start + pattern.len();
        if let Some(end) = json[value_start..].find('"') {
            return Some(json[value_start..value_start + end].to_string());
        }
    }
    // Also try with space after colon: "Key": "value"
    let pattern2 = format!("\"{}\": \"", key);
    if let Some(start) = json.find(&pattern2) {
        let value_start = start + pattern2.len();
        if let Some(end) = json[value_start..].find('"') {
            return Some(json[value_start..value_start + end].to_string());
        }
    }
    None
}

// ---------------------------------------------------------------------------
// kubectl get formatter
// ---------------------------------------------------------------------------

fn format_kubectl_get(cmd: &str, output: &str) -> String {
    let lines: Vec<&str> = output.lines().collect();

    if lines.is_empty() {
        return format!("[structured] {cmd}\n  (no resources)");
    }

    // kubectl get outputs a table; preserve it and add summary
    let data_rows = if lines.len() > 1 { lines.len() - 1 } else { 0 };

    // Try to detect the resource type from the header
    let resource_type = detect_kubectl_resource_type(lines.first().copied().unwrap_or(""));

    let mut result = output.to_string();
    if !result.ends_with('\n') {
        result.push('\n');
    }
    result.push_str(&format!(
        "[structured] {cmd} ({data_rows} {resource_type})"
    ));
    result
}

fn detect_kubectl_resource_type(header: &str) -> &str {
    let h = header.to_uppercase();
    if h.contains("NAME") && h.contains("READY") && h.contains("STATUS") {
        "pods"
    } else if h.contains("NAME") && h.contains("TYPE") && h.contains("CLUSTER-IP") {
        "services"
    } else if h.contains("NAME") && h.contains("READY") && h.contains("UP-TO-DATE") {
        "deployments"
    } else {
        "resources"
    }
}

// ---------------------------------------------------------------------------
// Generic fallback
// ---------------------------------------------------------------------------

fn format_generic_structured(cmd: &str, output: &str) -> String {
    let line_count = if output.is_empty() { 0 } else { output.lines().count() };

    if output.is_empty() {
        format!("[structured] {cmd}\n  (no output)")
    } else {
        let mut result = output.to_string();
        if !result.ends_with('\n') {
            result.push('\n');
        }
        result.push_str(&format!("[structured] {cmd} ({line_count} lines)"));
        result
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -----------------------------------------------------------------------
    // git status tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_git_status_porcelain_v2() {
        let output = "\
# branch.oid abc123def456
# branch.head main
1 .M N... 100644 100644 100644 abc123 def456 src/main.rs
1 A. N... 000000 100644 100644 000000 abc123 src/new_file.rs
1 D. N... 100644 000000 000000 abc123 000000 src/old_file.rs
? untracked.txt
? another.rs
";
        let result = format_structured("git status", output);
        assert!(result.contains("[structured] git status"));
        assert!(result.contains("branch: main"));
        assert!(result.contains("modified: 1"));
        assert!(result.contains("staged: 1")); // A. counted as staged
        assert!(result.contains("deleted: 1"));
        assert!(result.contains("untracked: 2"));
    }

    #[test]
    fn test_git_status_clean() {
        let output = "\
# branch.oid abc123
# branch.head main
";
        let result = format_structured("git status", output);
        assert!(result.contains("branch: main"));
        assert!(result.contains("clean"));
    }

    #[test]
    fn test_git_status_human_format() {
        let output = "\
On branch feature/foo
Changes to be committed:
  (use \"git restore --staged <file>...\" to unstage)
	new file:   src/new.rs

Changes not staged for commit:
  (use \"git add <file>...\" to update what will be committed)
	modified:   src/main.rs
	modified:   src/lib.rs

Untracked files:
  (use \"git add <file>...\" to include in what will be committed)
	todo.txt
";
        let result = format_structured("git status", output);
        assert!(result.contains("branch: feature/foo"));
        assert!(result.contains("staged: 1"));
        assert!(result.contains("modified: 2"));
        assert!(result.contains("untracked: 1"));
    }

    // -----------------------------------------------------------------------
    // docker ps tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_docker_ps_json() {
        let output = r#"{"ID":"abc123","Names":"web-app","Status":"Up 2 hours","Image":"nginx:latest","Ports":"0.0.0.0:80->80/tcp"}
{"ID":"def456","Names":"db","Status":"Up 3 hours","Image":"postgres:15","Ports":"5432/tcp"}
"#;
        let result = format_structured("docker ps", output);
        assert!(result.contains("[structured] docker ps"));
        assert!(result.contains("web-app"));
        assert!(result.contains("nginx:latest"));
        assert!(result.contains("2 containers"));
    }

    #[test]
    fn test_docker_ps_table() {
        let output = "\
CONTAINER ID   IMAGE          COMMAND   CREATED   STATUS   PORTS   NAMES
abc123         nginx:latest   ...       1h ago    Up       80/tcp  web
def456         postgres:15    ...       2h ago    Up       5432    db
";
        let result = format_structured("docker ps", output);
        assert!(result.contains("[structured] docker ps"));
        assert!(result.contains("2 containers"));
    }

    // -----------------------------------------------------------------------
    // kubectl get tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_kubectl_get_pods() {
        let output = "\
NAME                    READY   STATUS    RESTARTS   AGE
web-abc123              1/1     Running   0          2d
api-def456              1/1     Running   1          5d
worker-ghi789           0/1     Pending   0          1m
";
        let result = format_structured("kubectl get pods", output);
        assert!(result.contains("[structured] kubectl get pods"));
        assert!(result.contains("3 pods"));
    }

    #[test]
    fn test_kubectl_get_services() {
        let output = "\
NAME         TYPE        CLUSTER-IP       EXTERNAL-IP   PORT(S)   AGE
kubernetes   ClusterIP   10.96.0.1        <none>        443/TCP   30d
web          NodePort    10.96.100.50     <none>        80/TCP    5d
";
        let result = format_structured("kubectl get svc", output);
        assert!(result.contains("2 services"));
    }

    #[test]
    fn test_kubectl_get_empty() {
        let result = format_structured("kubectl get pods", "");
        assert!(result.contains("no resources"));
    }

    // -----------------------------------------------------------------------
    // Generic fallback tests
    // -----------------------------------------------------------------------

    #[test]
    fn test_generic_fallback() {
        let output = "some\ngeneric\noutput\n";
        let result = format_structured("helm list", output);
        assert!(result.contains("[structured] helm list"));
        assert!(result.contains("3 lines"));
        // Output body preserved
        assert!(result.contains("some\ngeneric\noutput\n"));
    }

    #[test]
    fn test_generic_empty_output() {
        let result = format_structured("unknown-cmd", "");
        assert!(result.contains("[structured] unknown-cmd"));
        assert!(result.contains("no output"));
    }

    // -----------------------------------------------------------------------
    // Edge cases
    // -----------------------------------------------------------------------

    #[test]
    fn test_empty_command() {
        let result = format_structured("", "some output");
        assert_eq!(result, "some output");
    }

    #[test]
    fn test_path_qualified_git() {
        let output = "# branch.head main\n";
        let result = format_structured("/usr/bin/git status", output);
        assert!(result.contains("branch: main"));
    }

    // -----------------------------------------------------------------------
    // JSON field extraction
    // -----------------------------------------------------------------------

    #[test]
    fn test_extract_json_field() {
        let json = r#"{"Names":"web-app","Image":"nginx","Status":"Up"}"#;
        assert_eq!(extract_json_field(json, "Names"), Some("web-app".to_string()));
        assert_eq!(extract_json_field(json, "Image"), Some("nginx".to_string()));
        assert_eq!(extract_json_field(json, "Missing"), None);
    }

    #[test]
    fn test_extract_json_field_with_space() {
        let json = r#"{"Names": "web-app", "Image": "nginx"}"#;
        assert_eq!(extract_json_field(json, "Names"), Some("web-app".to_string()));
    }
}
