use serde_json::{Value, json};
use std::fs;
use crate::{find_project_root, now_iso};

/// `ostk index` — build a searchable index of hay and drafts.
pub fn run_reindex() -> Result<(), String> {
    let root = find_project_root()?;
    let hs_dir = crate::state_dir(&root);
    let index_path = hs_dir.join("index.json");

    let mut index: Value = if index_path.exists() {
        let content = fs::read_to_string(&index_path).map_err(|e| e.to_string())?;
        serde_json::from_str(&content).unwrap_or(json!({}))
    } else {
        json!({})
    };

    // 1. Scan Hay from audit.jsonl
    let audit_path = hs_dir.join("audit.jsonl");
    let audit_content = fs::read_to_string(&audit_path).unwrap_or_default();
    let mut hay_items = Vec::new();
    for line in audit_content.lines() {
        if let Ok(v) = serde_json::from_str::<Value>(line)
            && v.get("event").and_then(|e| e.as_str()) == Some("hay.filed") {
                hay_items.push(v);
            }
    }

    // 2. Scan Drafts from docs/draft/
    let draft_dir = root.join("docs/draft");
    let mut drafts = Vec::new();
    if draft_dir.exists() {
        for entry in fs::read_dir(draft_dir).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            if path.is_file() && path.extension().and_then(|s| s.to_str()) == Some("md") {
                let content = fs::read_to_string(&path).unwrap_or_default();
                let title = path.file_stem().and_then(|s| s.to_str()).unwrap_or("untitled").to_string();
                let relative_path = path.strip_prefix(&root).unwrap_or(&path).to_str().unwrap_or("").to_string();
                
                // Extract first 200 chars for a snippet
                let snippet: String = content.chars().take(200).collect();
                
                drafts.push(json!({
                    "title": title,
                    "path": relative_path,
                    "snippet": snippet
                }));
            }
        }
    }

    // 3. Update index
    index["search_index"] = json!({
        "hay": hay_items,
        "drafts": drafts,
        "last_index": now_iso()
    });

    let index_json = serde_json::to_string_pretty(&index).map_err(|e| e.to_string())?;
    fs::write(&index_path, &index_json).map_err(|e| e.to_string())?;

    println!("indexed {} hay items and {} drafts", hay_items.len(), drafts.len());
    Ok(())
}
