//! File cache for the Anthropic Files API (→728).
//!
//! Caches path → file_id mappings in `.ostk/file_cache.jsonl` so that
//! files uploaded once can be referenced by ID across API calls, saving
//! context window tokens.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

/// A single cache entry mapping a file path to its uploaded file ID.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileCacheEntry {
    pub path: String,
    pub file_id: String,
    pub uploaded_at: String,
    pub size: u64,
}

/// In-memory file cache backed by `.ostk/file_cache.jsonl`.
#[derive(Debug, Clone)]
pub struct FileCache {
    /// Map from canonical path string to cache entry.
    entries: HashMap<String, FileCacheEntry>,
    /// Path to the JSONL backing file.
    cache_path: PathBuf,
}

impl FileCache {
    /// Load or create the file cache from `.ostk/file_cache.jsonl`.
    pub fn load(ostk_dir: &Path) -> Self {
        let cache_path = ostk_dir.join("file_cache.jsonl");
        let mut entries = HashMap::new();

        if let Ok(content) = fs::read_to_string(&cache_path) {
            for line in content.lines() {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                if let Ok(entry) = serde_json::from_str::<FileCacheEntry>(trimmed) {
                    // Last write wins (append-only log)
                    entries.insert(entry.path.clone(), entry);
                }
            }
        }

        FileCache {
            entries,
            cache_path,
        }
    }

    /// Look up a file_id for the given path.
    pub fn get(&self, path: &str) -> Option<&FileCacheEntry> {
        self.entries.get(path)
    }

    /// Insert or update a cache entry and append to the JSONL file.
    pub fn put(&mut self, entry: FileCacheEntry) -> Result<(), String> {
        // Append to backing file
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.cache_path)
            .map_err(|e| format!("failed to open file_cache.jsonl: {e}"))?;

        let mut line =
            serde_json::to_string(&entry).map_err(|e| format!("failed to serialize cache entry: {e}"))?;
        line.push('\n');
        file.write_all(line.as_bytes())
            .map_err(|e| format!("failed to write file_cache.jsonl: {e}"))?;

        // Update in-memory map
        self.entries.insert(entry.path.clone(), entry);
        Ok(())
    }

    /// Check if a path is cached.
    pub fn contains(&self, path: &str) -> bool {
        self.entries.contains_key(path)
    }

    /// Return the number of cached entries.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Check if the cache is empty.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Get all entries (for display/debugging).
    pub fn entries(&self) -> impl Iterator<Item = &FileCacheEntry> {
        self.entries.values()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn file_cache_entry_serde_roundtrip() {
        let entry = FileCacheEntry {
            path: "src/main.rs".to_string(),
            file_id: "file_abc123".to_string(),
            uploaded_at: "2026-03-16T20:00:00Z".to_string(),
            size: 4096,
        };
        let json = serde_json::to_string(&entry).unwrap();
        let back: FileCacheEntry = serde_json::from_str(&json).unwrap();
        assert_eq!(back.path, "src/main.rs");
        assert_eq!(back.file_id, "file_abc123");
        assert_eq!(back.uploaded_at, "2026-03-16T20:00:00Z");
        assert_eq!(back.size, 4096);
    }

    #[test]
    fn file_cache_load_empty() {
        let tmp = TempDir::new().unwrap();
        let cache = FileCache::load(tmp.path());
        assert!(cache.is_empty());
        assert_eq!(cache.len(), 0);
    }

    #[test]
    fn file_cache_put_and_get() {
        let tmp = TempDir::new().unwrap();
        let mut cache = FileCache::load(tmp.path());

        let entry = FileCacheEntry {
            path: "src/main.rs".to_string(),
            file_id: "file_abc123".to_string(),
            uploaded_at: "2026-03-16T20:00:00Z".to_string(),
            size: 4096,
        };
        cache.put(entry).unwrap();

        assert!(cache.contains("src/main.rs"));
        assert_eq!(cache.len(), 1);

        let got = cache.get("src/main.rs").unwrap();
        assert_eq!(got.file_id, "file_abc123");
        assert_eq!(got.size, 4096);
    }

    #[test]
    fn file_cache_persists_to_disk() {
        let tmp = TempDir::new().unwrap();

        // Write an entry
        {
            let mut cache = FileCache::load(tmp.path());
            cache
                .put(FileCacheEntry {
                    path: "a.rs".to_string(),
                    file_id: "file_001".to_string(),
                    uploaded_at: "2026-03-16T20:00:00Z".to_string(),
                    size: 100,
                })
                .unwrap();
        }

        // Reload from disk
        let cache = FileCache::load(tmp.path());
        assert_eq!(cache.len(), 1);
        assert_eq!(cache.get("a.rs").unwrap().file_id, "file_001");
    }

    #[test]
    fn file_cache_last_write_wins() {
        let tmp = TempDir::new().unwrap();
        let mut cache = FileCache::load(tmp.path());

        cache
            .put(FileCacheEntry {
                path: "x.rs".to_string(),
                file_id: "file_old".to_string(),
                uploaded_at: "2026-03-16T19:00:00Z".to_string(),
                size: 100,
            })
            .unwrap();

        cache
            .put(FileCacheEntry {
                path: "x.rs".to_string(),
                file_id: "file_new".to_string(),
                uploaded_at: "2026-03-16T20:00:00Z".to_string(),
                size: 200,
            })
            .unwrap();

        assert_eq!(cache.len(), 1);
        assert_eq!(cache.get("x.rs").unwrap().file_id, "file_new");

        // Reload from disk — last entry wins
        let cache2 = FileCache::load(tmp.path());
        assert_eq!(cache2.get("x.rs").unwrap().file_id, "file_new");
    }

    #[test]
    fn file_cache_multiple_entries() {
        let tmp = TempDir::new().unwrap();
        let mut cache = FileCache::load(tmp.path());

        for i in 0..5 {
            cache
                .put(FileCacheEntry {
                    path: format!("file_{i}.rs"),
                    file_id: format!("file_id_{i}"),
                    uploaded_at: "2026-03-16T20:00:00Z".to_string(),
                    size: i as u64 * 100,
                })
                .unwrap();
        }

        assert_eq!(cache.len(), 5);
        for i in 0..5 {
            assert!(cache.contains(&format!("file_{i}.rs")));
        }
    }

    #[test]
    fn file_cache_ignores_malformed_lines() {
        let tmp = TempDir::new().unwrap();
        let cache_path = tmp.path().join("file_cache.jsonl");

        // Write valid + invalid lines
        fs::write(
            &cache_path,
            r#"{"path":"a.rs","file_id":"file_001","uploaded_at":"2026-03-16T20:00:00Z","size":100}
not valid json
{"path":"b.rs","file_id":"file_002","uploaded_at":"2026-03-16T20:00:00Z","size":200}
"#,
        )
        .unwrap();

        let cache = FileCache::load(tmp.path());
        assert_eq!(cache.len(), 2);
        assert!(cache.contains("a.rs"));
        assert!(cache.contains("b.rs"));
    }

    #[test]
    fn file_cache_not_found_returns_none() {
        let tmp = TempDir::new().unwrap();
        let cache = FileCache::load(tmp.path());
        assert!(cache.get("nonexistent.rs").is_none());
        assert!(!cache.contains("nonexistent.rs"));
    }
}
