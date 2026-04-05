use crate::{find_project_root, read_needles};
use serde_json::Value;

pub fn run_list_filtered(
    status: Option<&str>,
    priority: Option<&str>,
    count_only: bool,
    json_output: bool,
) -> Result<(), String> {
    let root = find_project_root()?;
    let all = read_needles(&root)?;

    let filtered: Vec<&Value> = all
        .iter()
        .filter(|n| {
            if let Some(s) = status
                && n.get("status").and_then(|v| v.as_str()) != Some(s) {
                    return false;
                }
            if let Some(p) = priority
                && n.get("priority").and_then(|v| v.as_str()) != Some(p) {
                    return false;
                }
            true
        })
        .collect();

    if count_only {
        println!("{}", filtered.len());
        return Ok(());
    }

    if json_output {
        let arr: Vec<&Value> = filtered;
        let json_str = serde_json::to_string_pretty(&arr).map_err(|e| e.to_string())?;
        println!("{}", json_str);
        return Ok(());
    }

    // Default: human-readable
    for n in &filtered {
        let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("?");
        let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("?");
        let p = n.get("priority").and_then(|v| v.as_str()).unwrap_or("?");
        let s = n.get("status").and_then(|v| v.as_str()).unwrap_or("?");
        println!("{} [{}] [{}] {}", id, p, s, title);
    }
    println!("{} items", filtered.len());

    Ok(())
}

// Keep old entry point for backward compat
pub fn run_list() -> Result<(), String> {
    run_list_filtered(None, None, false, false)
}
