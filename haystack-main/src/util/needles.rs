use fs2::FileExt;
use serde_json::Value;
use std::fs;
use std::io::{Read as _, Seek, Write};
use std::path::Path;

use super::paths::state_dir;

/// Read counter, increment, write back under flock. Returns "nd-NNN"
/// Falls back to .ostk/needles/counter if needles/ doesn't exist yet.
pub fn next_needle_id(root: &Path) -> Result<String, String> {
    let needles_dir = state_dir(root).join("needles");
    let dir = needles_dir;
    let counter_path = dir.join("counter");
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&counter_path)
        .map_err(|e| format!("failed to open counter: {e}"))?;
    file.lock_exclusive()
        .map_err(|e| format!("failed to lock counter: {e}"))?;
    let mut contents = String::new();
    file.read_to_string(&mut contents)
        .map_err(|e| format!("failed to read counter: {e}"))?;
    let current = contents.trim().parse::<u64>().unwrap_or(0);
    let next = current + 1;
    file.set_len(0)
        .map_err(|e| format!("failed to truncate counter: {e}"))?;
    file.seek(std::io::SeekFrom::Start(0))
        .map_err(|e| format!("failed to seek counter: {e}"))?;
    file.write_all(next.to_string().as_bytes())
        .map_err(|e| format!("failed to write counter: {e}"))?;
    Ok(format!("\u{2192}{next:03}"))
}

/// Read all needles from .ostk/needles/issues.jsonl (legacy .ostk/beads/ no longer supported)
pub fn read_needles(root: &Path) -> Result<Vec<Value>, String> {
    let path = state_dir(root).join("needles/issues.jsonl");
    if !path.exists() {
        return Ok(vec![]);
    }
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let mut needles = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let v: Value =
            serde_json::from_str(line).map_err(|e| format!("malformed needle line: {e}"))?;
        needles.push(v);
    }
    Ok(needles)
}

/// Write all needles back (falls back to .ostk/beads/ if needles/ doesn't exist)
pub fn write_needles(root: &Path, needles: &[Value]) -> Result<(), String> {
    let dir = state_dir(root).join("needles");
    let path = dir.join("issues.jsonl");
    let mut file =
        fs::File::create(&path).map_err(|e| format!("failed to create issues.jsonl: {e}"))?;
    for needle in needles {
        let mut line = serde_json::to_string(needle).map_err(|e| e.to_string())?;
        line.push('\n');
        file.write_all(line.as_bytes())
            .map_err(|e| format!("failed to write needle: {e}"))?;
    }
    Ok(())
}

/// Atomic read-modify-write on .ostk/needles/issues.jsonl under flock.
pub fn with_needles_locked<F, R>(root: &Path, f: F) -> Result<R, String>
where
    F: FnOnce(&mut Vec<Value>) -> Result<R, String>,
{
    let dir = state_dir(root).join("needles");
    let lock_path = dir.join("issues.lock");
    let lock_file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&lock_path)
        .map_err(|e| format!("failed to open issues.lock: {e}"))?;
    lock_file
        .lock_exclusive()
        .map_err(|e| format!("failed to lock issues: {e}"))?;

    let mut needles = read_needles(root)?;
    let result = f(&mut needles)?;
    write_needles(root, &needles)?;

    Ok(result)
}

/// Recognize nd-, bd-, and \u{2192} (arrow) prefixes for needle IDs in text.
pub fn parse_needle_ids(text: &str) -> Vec<String> {
    let mut ids: Vec<String> = Vec::new();
    for prefix in &["nd-", "bd-", "\u{2192}"] {
        let mut remaining = text;
        while let Some(pos) = remaining.find(prefix) {
            let prefix_len = prefix.len();
            let after = &remaining[pos + prefix_len..];
            let digits: String = after.chars().take_while(|c| c.is_ascii_digit()).collect();
            if !digits.is_empty() {
                let before = &remaining[..pos];
                let prev_alnum = before
                    .chars()
                    .last()
                    .map(|c| c.is_alphanumeric())
                    .unwrap_or(false);
                if !prev_alnum {
                    let candidate = format!("{}{}", prefix, digits);
                    if !ids.contains(&candidate) {
                        ids.push(candidate);
                    }
                }
                remaining = &after[digits.len()..];
            } else {
                remaining = &remaining[pos + prefix_len..];
            }
        }
    }
    ids
}

/// Recognize spec:path references in text.
pub fn parse_spec_refs(text: &str) -> Vec<String> {
    let mut refs = Vec::new();
    let mut remaining = text;
    while let Some(pos) = remaining.find("spec:") {
        let after = &remaining[pos + 5..];
        let ref_str: String = after
            .chars()
            .take_while(|c| !c.is_whitespace() && *c != ',' && *c != ')' && *c != ']')
            .collect();
        if !ref_str.is_empty() {
            let full = format!("spec:{}", ref_str);
            if !refs.contains(&full) {
                refs.push(full);
            }
            remaining = &after[ref_str.len()..];
        } else {
            remaining = &remaining[pos + 5..];
        }
    }
    refs
}
