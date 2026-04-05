use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// Parse YAML between --- fences, return (frontmatter, body)
pub fn parse_frontmatter(content: &str) -> (HashMap<String, serde_yaml::Value>, String) {
    let trimmed = content.trim_start();
    if !trimmed.starts_with("---") {
        return (HashMap::new(), content.to_string());
    }
    let after_first = &trimmed[3..];
    let after_first = after_first.strip_prefix('\n').unwrap_or(after_first);
    if let Some(end) = after_first.find("\n---") {
        let yaml_str = &after_first[..end];
        let body = &after_first[end + 4..];
        let body = body.strip_prefix('\n').unwrap_or(body);
        let fm: HashMap<String, serde_yaml::Value> =
            serde_yaml::from_str(yaml_str).unwrap_or_default();
        (fm, body.to_string())
    } else {
        (HashMap::new(), content.to_string())
    }
}

/// Serialize frontmatter + body back to file
pub fn write_with_frontmatter(
    path: &Path,
    fm: &HashMap<String, serde_yaml::Value>,
    body: &str,
) -> Result<(), String> {
    let yaml = serde_yaml::to_string(fm).map_err(|e| e.to_string())?;
    let content = format!("---\n{yaml}---\n{body}");
    fs::write(path, content).map_err(|e| format!("failed to write {}: {e}", path.display()))
}
